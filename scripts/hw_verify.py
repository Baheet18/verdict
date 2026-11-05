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
    check(tcb["os_image_hash"].lower() == host["os_image_hash"]["value"].lower(),
          f"os_image_hash pinned to published {host['os_image_hash']['published_as']}")

    # Pin MRTD + RTMR0-2 (observed pins — see allowlist _meta)
    for key, td_key in (("mr_td", "mr_td"), ("rt_mr0", "rt_mr0"), ("rt_mr1", "rt_mr1"), ("rt_mr2", "rt_mr2")):
        exp = host[key]["value"].lower()
        got = td[td_key].lower()
        check(got == exp, f"{key} matches allowlist ({host[key]['source']}) {exp[:16]}..")

    compose_name, images, digests = parse_docker_images(app_compose)
    print("  --  docker image digests (compare to published NEAR AI / dstack images):")
    for img in images:
        print(f"      {img}")
    exp_digests = set(host["docker_image_digests"])
    check(set(digests) == exp_digests, f"docker image digests match allowlist ({len(digests)} digests)")

    model_key = model or m.get("model_name")
    check(model_key in allow["models"], f"model {model_key} is on the measurement allowlist")
    exp_model = allow["models"][model_key]
    check(
        compose_hash == exp_model["compose_hash"],
        f"compose_hash pinned for {model_key} ({compose_hash[:16]}..)",
    )
    check(
        compose_name == exp_model["compose_name"],
        f"compose name {compose_name} matches allowlist",
    )

    return {
        "tcb_status": tcb_status,
        "compose_hash": ch,
        "compose_name": compose_name,
        "os_image_hash": tcb["os_image_hash"],
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
