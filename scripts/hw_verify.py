"""Hardware verification for a NEAR AI Cloud model node attestation (Intel TDX + NVIDIA GPU)."""
import json, hashlib, asyncio, requests, jwt, dcap_qvl
from eth_utils import keccak

NRAS = "https://nras.attestation.nvidia.com/v3/attest/gpu"
NRAS_JWKS = "https://nras.attestation.nvidia.com/.well-known/jwks.json"

def check(cond, msg):
    if not cond: raise AssertionError(msg)
    print(f"  ok  {msg}")

def verify_tdx(m, nonce):
    """Intel DCAP quote verification (collateral from Phala PCCS, rooted in Intel SGX Root CA)."""
    rep = json.loads(asyncio.run(dcap_qvl.get_collateral_and_verify(bytes.fromhex(m["intel_quote"]))).to_json())
    check(rep["status"] in ("UpToDate", "SWHardeningNeeded", "ConfigurationAndSWHardeningNeeded"),
          f"TDX quote signature chain valid to Intel root, TCB status={rep['status']}")
    td = list(rep["report"].values())[0]
    rd = td["report_data"].lower()
    addr = m["signing_address"].lower().removeprefix("0x")
    check(rd[:40] == addr and rd[40:64] == "0" * 24, "report_data[0:32] = signing_address (padded)")
    check(rd[64:128] == nonce, "report_data[32:64] = our nonce")
    pk = bytes.fromhex(m["signing_public_key"])[-64:]
    check(keccak(pk)[-20:].hex() == addr, "signing_public_key hashes to signing_address")
    # Replay event log -> RTMR0-3 must match quote; ties compose (docker images) to hardware
    # Any candidate log is acceptable iff it replays to the quote's RTMRs (the quote is the root of trust).
    # NEAR-native Qwen/DeepSeek: tcb_info.event_log replays; GLM: top-level event_log (has extra RTMR3 events).
    tcb = m["info"]["tcb_info"]
    def _rep(el, i):
        r = b"\0" * 48
        for e in el:
            if e["imr"] == i: r = hashlib.sha384(r + bytes.fromhex(e["digest"])).digest()
        return r.hex()
    cands = [x for x in (tcb.get("event_log"), m.get("event_log")) if x]
    cands = [json.loads(x) if isinstance(x, str) else x for x in cands]
    el = next((c for c in cands if all(_rep(c, i) == td[f"rt_mr{i}"].lower() for i in range(4))), None)
    check(el is not None, "event log replays to RTMR0-3")
    ch = next(e["event_payload"] for e in el if e["imr"] == 3 and e["event"] == "compose-hash")
    check(ch == tcb["compose_hash"] == hashlib.sha256(tcb["app_compose"].encode()).hexdigest(),
          f"compose_hash {ch[:16]}.. = sha256(app_compose) and is measured in RTMR3")
    check(td["mr_td"].lower() == tcb["mrtd"].lower(), "MRTD matches tcb_info")
    return {"tcb_status": rep["status"], "compose_hash": ch, "mr_td": td["mr_td"]}

def verify_gpu(m, nonce):
    """NVIDIA Remote Attestation Service (NRAS) — online by design; JWTs verified against NVIDIA JWKS."""
    payload = json.loads(m["nvidia_payload"])
    check(payload["nonce"] == nonce, "GPU evidence carries our nonce")
    res = requests.post(NRAS, json=payload, timeout=60).json()
    jwks = jwt.PyJWKClient(NRAS_JWKS)
    overall = res[0][1]
    claims = jwt.decode(overall, jwks.get_signing_key_from_jwt(overall).key, algorithms=["ES384"],
                        options={"verify_aud": False})
    check(claims.get("x-nvidia-overall-att-result") is True, "NRAS overall GPU attestation result = true (JWT sig verified)")
    check(claims.get("eat_nonce") == nonce, "NRAS token eat_nonce = our nonce")
    for gpu, tok in res[1].items():
        c = jwt.decode(tok, jwks.get_signing_key_from_jwt(tok).key, algorithms=["ES384"], options={"verify_aud": False})
        check(c.get("measres") == "success", f"{gpu}: measurements match NVIDIA RIM ({c.get('hwmodel','')})")
    return {"gpus": len(res[1])}

def verify_node(m, nonce):
    print(f"[hw] node {m['signing_address']}")
    return {"tdx": verify_tdx(m, nonce), "gpu": verify_gpu(m, nonce)}
