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
    assert len(al["models"][m]["compose_hash"]) == 64
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

print("ALL UNIT CHECKS PASSED")
