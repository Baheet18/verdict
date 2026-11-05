"""Hardware verification for a NEAR AI Cloud model node attestation (Intel TDX + NVIDIA GPU).

Fail-closed against scripts/expected_measurements.json so a genuine TEE running a
malicious/unexpected app compose does not pass.
"""
from __future__ import annotations
import json, hashlib, asyncio, re, struct, pathlib, requests, jwt, dcap_qvl
from eth_utils import keccak

NRAS = "https://nras.attestation.nvidia.com/v3/attest/gpu"
NRAS_JWKS = "https://nras.attestation.nvidia.com/.well-known/jwks.json"
ALLOWLIST_PATH = pathlib.Path(__file__).resolve().parent / "expected_measurements.json"
ACCEPTED_TCB = ("UpToDate", "SWHardeningNeeded", "ConfigurationAndSWHardeningNeeded")

def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok  {msg}")

def load_allowlist():
    return json.loads(ALLOWLIST_PATH.read_text())

def normalize_nonce(nonce: str) -> str:
    """Require exactly 64 lowercase hex chars (32 bytes) before any compare."""
    if not isinstance(nonce, str):
        raise AssertionError("nonce must be a string")
    if len(nonce) != 64 or any(c not in "0123456789abcdef" for c in nonce):
        raise AssertionError(f"nonce must be exactly 64 lowercase hex chars, got len={len(nonce)} value={nonce[:16]!r}...")
    return nonce

def _json(value):
    return json.loads(value) if isinstance(value, str) else value

def parse_docker_images(app_compose: str):
    """Extract immutable image refs and digests from the measured app_compose string."""
    obj = json.loads(app_compose)
    yml = obj.get("docker_compose_file") or ""
    images = []
    for m in re.finditer(r'image:\s*["\']?(.+?)["\']?\s*$', yml, re.M):
        raw = m.group(1).strip()
        m2 = re.search(r"\$\{[^:]+:-([^}]+)\}", raw)
        if m2:
            raw = m2.group(1)
        images.append(raw)
    digests = sorted({img.split("@", 1)[1] for img in images if "@sha256:" in img})
    return obj.get("name"), sorted(set(images)), digests

def replay_rtmr_digest_field(event_log, imr: int) -> str:
    """Replay using the digest field (TCG-style). Used for RTMR0-2. Raises ValueError on bad log."""
    r = b"\0" * 48
    for e in event_log:
        if e.get("imr") == imr:
            dig = e.get("digest") or ""
            if not dig:
                raise ValueError(f"RTMR{imr} event missing digest field")
            r = hashlib.sha384(r + bytes.fromhex(dig)).digest()
    return r.hex()

def replay_rtmr3_near(event_log) -> str:
    """NEAR AI official RTMR3 replay: recompute each digest from event_type/event/payload.

    See https://docs.near.ai/cloud/verification/reference/quote-nonce-signer
    Raises ValueError if a non-empty digest field disagrees with the recomputation.
    """
    r = b"\0" * 48
    for e in _json(event_log):
        if e.get("imr") != 3:
            continue
        digest = hashlib.sha384(
            struct.pack("<I", int(e["event_type"]))
            + b":"
            + e["event"].encode("utf-8")
            + b":"
            + bytes.fromhex(e.get("event_payload") or "")
        ).digest()
        if e.get("digest") and bytes.fromhex(e["digest"]) != digest:
            raise ValueError(f"RTMR3 digest mismatch for event {e['event']}")
        r = hashlib.sha384(r + digest).digest()
    return r.hex()

INSTANCE_LABEL_RE = re.compile(r"^(\s*- INSTANCE_LABEL=)(gpu\d+)$", re.M)

def normalize_app_compose(app_compose: str):
    """Replace the single per-node INSTANCE_LABEL value with a placeholder so one pin covers every node.

    Fail closed unless the docker_compose_file contains EXACTLY one `- INSTANCE_LABEL=gpuNN` line and the
    raw app_compose has no other `INSTANCE_LABEL=` assignment (a comment mentions the name). Returns (normalized_string, label).
    """
    obj = json.loads(app_compose)
    yml = obj.get("docker_compose_file") or ""
    if app_compose.count("INSTANCE_LABEL=") != 1:
        raise AssertionError(f"INSTANCE_LABEL= must appear exactly once in app_compose (got {app_compose.count('INSTANCE_LABEL=')})")
    ms = list(INSTANCE_LABEL_RE.finditer(yml))
    if len(ms) != 1:
        raise AssertionError("INSTANCE_LABEL line not of form '- INSTANCE_LABEL=gpu<digits>'")
    label = ms[0].group(2)
    needle = f"INSTANCE_LABEL={label}"
    norm = app_compose.replace(needle, "INSTANCE_LABEL=<INSTANCE_LABEL>")
    return norm, label

def normalized_compose_hash(app_compose: str):
    norm, label = normalize_app_compose(app_compose)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest(), label

def rtmr3_event_names(event_log):
    return [e["event"] for e in _json(event_log) if e.get("imr") == 3]

def find_replaying_event_log(m, td):
    """Pick an event log that replays to the quote's RTMR0-3 (quote is root of trust).

    Prefer tcb_info.event_log; fall back to top-level event_log (NEAR sometimes returns
    empty digests there — RTMR3 uses NEAR's digest-recompute so empty digests still work).
    """
    tcb = _json(m["info"]["tcb_info"])
    cands = []
    for x in (tcb.get("event_log"), m.get("event_log")):
        if not x:
            continue
        cands.append(_json(x))
    for el in cands:
        try:
            ok01 = all(replay_rtmr_digest_field(el, i) == td[f"rt_mr{i}"].lower() for i in range(3))
            ok3 = replay_rtmr3_near(el) == td["rt_mr3"].lower()
            if ok01 and ok3:
                return el
        except (AssertionError, ValueError):
            continue
    return None

def verify_tdx(m, nonce, model: str | None = None):
    """Intel DCAP quote verification + measurement allowlist checks."""
    nonce = normalize_nonce(nonce)
    allow = load_allowlist()
    host = allow["shared_host_images"]
    rep = json.loads(asyncio.run(dcap_qvl.get_collateral_and_verify(bytes.fromhex(m["intel_quote"]))).to_json())
    tcb_status = rep["status"]
    check(
        tcb_status in ACCEPTED_TCB,
        f"TDX quote signature chain valid to Intel root, TCB status={tcb_status}",
    )
    if tcb_status != "UpToDate":
        print(f"  !!  TCB status is {tcb_status} (accepted by policy; not hidden)")
    td = list(rep["report"].values())[0]

    # Debug TD lets the host read guest memory — bit 0 of TDATTRIBUTES must be 0.
    # NEAR docs / dcap-qvl expose td_attributes as hex of the raw 8 bytes; bit0 of byte0 = DEBUG.
    attrs = bytes.fromhex(td["td_attributes"])
    check((attrs[0] & 1) == 0, f"TDX debug bit clear (td_attributes={td['td_attributes']})")

    rd = td["report_data"].lower()
    addr = m["signing_address"].lower().removeprefix("0x")
    check(rd[:40] == addr and rd[40:64] == "0" * 24, "report_data[0:32] = signing_address (padded)")
    check(rd[64:128] == nonce, "report_data[32:64] = our nonce")
    pk = bytes.fromhex(m["signing_public_key"])[-64:]
    check(keccak(pk)[-20:].hex() == addr, "signing_public_key hashes to signing_address")

    tcb = _json(m["info"]["tcb_info"])
    app_compose = tcb["app_compose"]
    compose_hash = hashlib.sha256(app_compose.encode("utf-8")).hexdigest()
    check(
        bytes.fromhex(td["mr_config_id"]) == b"\x01" + bytes.fromhex(compose_hash) + bytes(15),
        f"MRCONFIGID = 01||sha256(app_compose)||0*15 ({compose_hash[:16]}..)",
    )

    el = find_replaying_event_log(m, td)
    check(el is not None, "event log replays to RTMR0-3 (NEAR RTMR3 digest-recompute)")
    ch = next(e["event_payload"] for e in el if e["imr"] == 3 and e["event"] == "compose-hash")
    check(
        ch == tcb["compose_hash"] == compose_hash,
        f"compose_hash {ch[:16]}.. = sha256(app_compose) and is measured in RTMR3",
    )
    check(td["mr_td"].lower() == tcb["mrtd"].lower(), "MRTD matches tcb_info")
    names = rtmr3_event_names(el)
    print(f"  --  RTMR3 events (replayed): {', '.join(names)}")
    os_evs = [e["event_payload"].lower() for e in el if e["imr"] == 3 and e["event"] == "os-image-hash"]
    check(len(os_evs) == 1, "exactly one os-image-hash event measured in RTMR3")
    check(os_evs[0] == tcb["os_image_hash"].lower(), "tcb_info.os_image_hash equals the RTMR3-measured os-image-hash event")
    check(os_evs[0] == host["os_image_hash"]["value"].lower(),
          f"RTMR3 os-image-hash pinned to {host['os_image_hash']['source']} {host['os_image_hash']['published_as']}")

    # Pin KMS / key-provider RTMR3 events (signing keys are issued by this service).
    mr_kms = [e["event_payload"].lower() for e in el if e.get("imr") == 3 and e.get("event") == "mr-kms"]
    check(len(mr_kms) == 1, "exactly one mr-kms event in RTMR3")
    check(mr_kms[0] == host["mr_kms"]["value"].lower(), f"mr-kms pinned ({host['mr_kms']['source']})")
    kp = [e["event_payload"].lower() for e in el if e.get("imr") == 3 and e.get("event") == "key-provider"]
    check(len(kp) == 1, "exactly one key-provider event in RTMR3")
    check(kp[0] == host["key_provider"]["value"].lower(), f"key-provider pinned ({host['key_provider']['source']})")

    # Pin MRTD + RTMR0-2 (observed pins — see allowlist _meta)
    for key, td_key in (("mr_td", "mr_td"), ("rt_mr0", "rt_mr0"), ("rt_mr1", "rt_mr1"), ("rt_mr2", "rt_mr2")):
        exps = [v.lower() for v in ([host[key]["value"]] + host[key].get("also_observed", []))]
        got = td[td_key].lower()
        check(got in exps, f"{key} {got[:16]}.. matches allowlist ({host[key]['source']}, {len(exps)} value(s))")

    compose_name, images, digests = parse_docker_images(app_compose)
    print("  --  docker image digests (compare to published NEAR AI / dstack images):")
    for img in images:
        print(f"      {img}")
    exp_digests = set(host["docker_image_digests"])
    unknown = set(digests) - exp_digests
    check(not unknown, f"all docker digests on allowlist (unknown={sorted(unknown)})")
    for pref in host.get("required_image_name_prefixes", []):
        check(any(img.startswith(pref) for img in images), f"required image present: {pref.split('@')[0]}")

    model_key = model or m.get("model_name")
    check(model_key in allow["models"], f"model {model_key} is on the measurement allowlist")
    exp_model = allow["models"][model_key]
    norm_hash, label = normalized_compose_hash(app_compose)
    check(
        norm_hash in exp_model["normalized_compose_hashes"],
        f"normalized compose hash (INSTANCE_LABEL={label} removed) pinned for {model_key} ({norm_hash[:16]}..); raw hash still bound to MRCONFIGID above",
    )
    check(
        compose_name == exp_model["compose_name"],
        f"compose name {compose_name} matches allowlist",
    )

    return {
        "tcb_status": tcb_status,
        "compose_hash": ch,
        "normalized_compose_hash": norm_hash,
        "instance_label": label,
        "rtmr3_events": names,
        "label": "Hardware + host stack verified",
        "compose_name": compose_name,
        "os_image_hash": os_evs[0],
        "mr_td": td["mr_td"],
        "docker_image_digests": digests,
        "docker_images": images,
    }

def verify_gpu(m, nonce):
    """NVIDIA Remote Attestation Service (NRAS) — online by design; JWTs verified against NVIDIA JWKS."""
    nonce = normalize_nonce(nonce)
    payload = json.loads(m["nvidia_payload"])
    check(payload["nonce"] == nonce, "GPU evidence carries our nonce")
    res = requests.post(NRAS, json=payload, timeout=60).json()
    jwks = jwt.PyJWKClient(NRAS_JWKS)
    overall = res[0][1]
    claims = jwt.decode(
        overall, jwks.get_signing_key_from_jwt(overall).key, algorithms=["ES384"], options={"verify_aud": False}
    )
    check(claims.get("x-nvidia-overall-att-result") is True, "NRAS overall GPU attestation result = true (JWT sig verified)")
    check(claims.get("eat_nonce") == nonce, "NRAS token eat_nonce = our nonce")
    gpu_tokens = res[1]
    check(isinstance(gpu_tokens, dict) and len(gpu_tokens) > 0, f"NRAS returned >0 GPU tokens (got {len(gpu_tokens) if isinstance(gpu_tokens, dict) else type(gpu_tokens)})")
    for gpu, tok in gpu_tokens.items():
        c = jwt.decode(tok, jwks.get_signing_key_from_jwt(tok).key, algorithms=["ES384"], options={"verify_aud": False})
        print(f"  --  {gpu} claims:")
        for k in sorted(c.keys()):
            print(f"      {k}={c[k]}")
        # Actual NRAS EAT claim names (observed 2026-11-05): dbgstat, secboot
        check(c.get("dbgstat") == "disabled", f"{gpu}: dbgstat=disabled (GPU debug disabled)")
        check(c.get("secboot") is True, f"{gpu}: secboot=true (secure boot on)")
        check(c.get("measres") == "success", f"{gpu}: measurements match NVIDIA RIM ({c.get('hwmodel', '')})")
    return {"gpus": len(gpu_tokens)}

def verify_node(m, nonce, model: str | None = None):
    """Verify one model_attestations[] entry. Caller must pass the entry whose
    signing_address equals the address recovered from that chat response's signature,
    and whose model matches the requested model (see match_attestation_for_signature).
    """
    model_key = model or m.get("model_name")
    print(f"[hw] node {m.get('signing_address')} model={model_key}")
    if model_key and m.get("model_name"):
        check(m["model_name"] == model_key, f"attestation entry model_name={m['model_name']} equals requested {model_key}")
    return {"tdx": verify_tdx(m, nonce, model=model_key), "gpu": verify_gpu(m, nonce)}

def match_attestation_for_signature(model_attestations, recovered_address: str, requested_model: str):
    """Return the single attestation entry for this chat response.

    MUST: signing_address equals the address recovered from the response signature,
    AND entry.model_name equals the requested model. Fail closed otherwise.
    """
    want = recovered_address.lower()
    matches = [
        e
        for e in (model_attestations or [])
        if (e.get("signing_address") or "").lower() == want and e.get("model_name") == requested_model
    ]
    check(len(matches) == 1, f"matched attestation entry for signer {want} model {requested_model} (count={len(matches)})")
    return matches[0]

def verify_signer_replicas(model_attestations, signing_address: str, requested_model: str, nonce: str):
    """Shared-key policy (round 4): a signing key is "Hardware + host stack verified" only if
    EVERY observed attestation entry for that key (+ model) passes verify_node.

    A signature cannot prove which replica signed. If any replica fails (e.g. unknown
    compose-manager digest), ALL votes from this key are downgraded.
    Returns {label, entries_checked, failures, results}.
    """
    want = signing_address.lower()
    entries = [
        e for e in (model_attestations or [])
        if (e.get("signing_address") or "").lower() == want and e.get("model_name") == requested_model
    ]
    check(len(entries) >= 1, f"at least one attestation entry for signer {want} model {requested_model}")
    results, failures = [], []
    for i, e in enumerate(entries):
        print(f"[hw] replica {i+1}/{len(entries)} for {want}")
        try:
            results.append(verify_node(e, nonce, model=requested_model))
        except Exception as ex:
            failures.append({"index": i, "error": str(ex)})
            print(f"  !!  replica {i+1} FAILED: {ex}")
    if failures:
        label = "Signature valid · hardware check pending"
    else:
        label = "Hardware + host stack verified"
    print(f"[label] {label} (replicas_ok={len(results)}/{len(entries)})")
    return {
        "label": label,
        "entries_checked": len(entries),
        "failures": failures,
        "results": results,
    }

