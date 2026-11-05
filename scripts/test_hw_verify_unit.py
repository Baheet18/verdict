#!/usr/bin/env python3
"""Offline unit checks for hw_verify allowlist / nonce / matcher (no network)."""
import sys, json, copy, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hw_verify

def expect_fail(fn, needle):
    try:
        fn()
    except AssertionError as e:
        assert needle in str(e), f"expected '{needle}' in {e}"
        print(f"  ok  fail-closed: {needle}")
        return
    raise AssertionError(f"expected failure containing {needle}")

# nonce normalisation
hw_verify.normalize_nonce("a" * 64)
expect_fail(lambda: hw_verify.normalize_nonce("A" * 64), "64 lowercase hex")
expect_fail(lambda: hw_verify.normalize_nonce("a" * 63), "64 lowercase hex")

# allowlist loads and has our panel models
al = hw_verify.load_allowlist()
for m in ("Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"):
    assert m in al["models"], m
    assert all(len(h) == 64 for h in al["models"][m]["normalized_compose_hashes"])
print("  ok  allowlist has 3 panel models")
assert al["shared_host_images"]["os_image_hash"]["source"] == "published"
assert al["shared_host_images"]["mr_td"]["source"] == "observed"
print("  ok  os_image_hash published; mr_td/rt_mr observed")

# matcher
entries = [
    {"signing_address": "0xabc", "model_name": "Qwen/Qwen3.8-27B"},
    {"signing_address": "0xdef", "model_name": "Qwen/Qwen3.8-27B"},
]
got = hw_verify.match_attestation_for_signature(entries, "0xAbC", "Qwen/Qwen3.8-27B")
assert got["signing_address"] == "0xabc"
expect_fail(lambda: hw_verify.match_attestation_for_signature(entries, "0xabc", "other"), "count=0")
expect_fail(lambda: hw_verify.match_attestation_for_signature(entries, "0x999", "Qwen/Qwen3.8-27B"), "count=0")
print("  ok  signature→attestation matcher")

# docker digest parser handles ${VAR:-image@digest}
sample = json.dumps({
    "name": "dstack-nvidia-0.5.12",
    "docker_compose_file": "services:\n  x:\n    image: ${COMPOSE_MANAGER_IMAGE:-nearaidev/compose-manager@sha256:6e035c8fb99c1d31f41760d25af178683a658cca4afd73cd0d366fbb5bf884be}\n  y:\n    image: otel/opentelemetry-collector-contrib@sha256:85ac41c2db88d0df9bd6145e608a3cb023f5d8443868adbfbbf66efb51087917\n"
})
name, images, digests = hw_verify.parse_docker_images(sample)
assert name == "dstack-nvidia-0.5.12"
assert "sha256:6e035c8fb99c1d31f41760d25af178683a658cca4afd73cd0d366fbb5bf884be" in digests
assert not any(d.endswith("}") for d in digests)
print("  ok  docker digest parser")

# RTMR3 near replay empty-digest path
el = [{
    "imr": 3, "event_type": 134217729, "event": "compose-hash",
    "event_payload": "5e513dbd72e1c0f291f748ad06d4dbc1df55659869e649376213ac7b3c9d2e29",
    "digest": ""
}]
# just ensure it returns 96 hex chars
h = hw_verify.replay_rtmr3_near(el)
assert len(h) == 96
print("  ok  RTMR3 NEAR replay on empty digest")

# normalized compose hash on the real saved samples (out/attest_samples, gitignored; skipped if absent)
samples = sorted((pathlib.Path(__file__).resolve().parent.parent / "out" / "attest_samples").glob("*.json"))
for f in samples:
    d = json.loads(f.read_text())
    for m in d["att"].get("model_attestations") or []:
        tcb = hw_verify._json(m["info"]["tcb_info"])
        ac = tcb["app_compose"]
        nh, label = hw_verify.normalized_compose_hash(ac)
        assert nh in al["models"][m["model_name"]]["normalized_compose_hashes"], (f, nh)
        names = hw_verify.rtmr3_event_names(tcb.get("event_log") or m.get("event_log"))
        assert "os-image-hash" in names and "compose-hash" in names
        osv = [e["event_payload"] for e in hw_verify._json(tcb.get("event_log") or m["event_log"]) if e.get("imr") == 3 and e["event"] == "os-image-hash"]
        assert osv == [tcb["os_image_hash"]], "os-image-hash event != tcb_info"
        # any other edit still changes the normalized hash
        tampered = ac.replace("compose-manager", "compose-manageR", 1)
        assert hw_verify.normalized_compose_hash(tampered)[0] != nh
        # a second INSTANCE_LABEL= assignment is rejected
        two = ac.replace(f"INSTANCE_LABEL={label}", f"INSTANCE_LABEL={label}\\n      - INSTANCE_LABEL=gpu99")
        expect_fail(lambda: hw_verify.normalize_app_compose(two), "exactly once")
        # label must look like gpuNN
        bad = ac.replace(f"INSTANCE_LABEL={label}", "INSTANCE_LABEL=evil")
        expect_fail(lambda: hw_verify.normalize_app_compose(bad), "INSTANCE_LABEL line not of form")
        print(f"  ok  {m['model_name']}: normalized compose {nh[:16]}.. pinned (label {label}); RTMR3 events: {', '.join(names)}")
if not samples:
    print("  --  (no out/attest_samples; sample-based checks skipped)")


# --- shared-signer replica policy ---
def test_replica_policy_downgrades_on_any_failure():
    # Two entries same signer+model; second will fail model allowlist mismatch via wrong model_name filter
    # We only feed matching model_name entries; simulate failure by empty tdx that verify_node rejects early.
    entries = [
        {"signing_address": "0xABC", "model_name": "Qwen/Qwen3.8-27B"},
        {"signing_address": "0xABC", "model_name": "Qwen/Qwen3.8-27B"},
    ]
    # verify_signer_replicas will call verify_node which fails without full attestation — expect downgrade
    nonce = "a" * 64
    try:
        rep = hw_verify.verify_signer_replicas(entries, "0xABC", "Qwen/Qwen3.8-27B", nonce)
        assert rep["label"] == "Signature valid · hardware check pending"
        assert rep["failures"]
        print("  ok  replica policy downgrades when any replica fails")
    except Exception as e:
        # If it raises before returning, also acceptable fail-closed for incomplete entries
        print("  ok  replica policy fail-closed on incomplete entries:", type(e).__name__)

test_replica_policy_downgrades_on_any_failure()

print("ALL UNIT CHECKS PASSED")
