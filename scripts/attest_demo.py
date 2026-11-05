#!/usr/bin/env python3
"""Verdict attestation demo: resolve a market in a TEE model, match signature→attestation
entry, verify hardware+allowlist, then tamper one byte."""
import os, sys, json, pathlib, hashlib, secrets, requests
sys.path.insert(0, os.path.dirname(__file__))
import hw_verify
from eth_account import Account
from eth_account.messages import encode_defunct

API = "https://cloud-api.near.ai/v1"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "Qwen/Qwen3.8-27B"
H = {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}
sha = lambda b: hashlib.sha256(b).hexdigest()

# 1. Fresh attestation bound to our own nonce (normalized: 64 lowercase hex)
nonce = secrets.token_hex(32)
hw_verify.normalize_nonce(nonce)
att = requests.get(
    f"{API}/attestation/report",
    headers=H,
    params={"model": MODEL, "signing_algo": "ecdsa", "nonce": nonce},
    timeout=60,
).json()
ma = att.get("model_attestations") or []
if not ma:
    sys.exit(f"[attest] {MODEL}: no model_attestations -> not TEE-attested")
for m in ma:
    assert m["request_nonce"] == nonce, "nonce mismatch in model attestation"
att_addrs = {m["signing_address"].lower() for m in ma}
print(f"[attest] model={MODEL} nodes={len(ma)} nonce echoed OK, signers={sorted(att_addrs)}")
g = att["gateway_attestation"]
rd = g["report_data"]
assert rd[:40].lower() == g["signing_address"][2:].lower() and rd[64:128] == nonce, "gateway report_data binding failed"
print("[attest] gateway report_data = signing_address(padded) || nonce  OK")

# 2. Resolution-style completion
market = {
    "question": "Will it rain in Lagos (Ikeja) on 2026-11-03?",
    "rules": "Resolves YES if the locked source reports >=1.0 mm precipitation for Ikeja on that date (WAT). Otherwise NO.",
    "sources": [
        "[NiMet daily bulletin, 2026-11-04] Ikeja recorded 3.2 mm of rainfall on 3 November 2026, mostly in the evening."
    ],
}
req = {
    "model": MODEL,
    "max_tokens": 400,
    "temperature": 0,
    "messages": [
        {
            "role": "system",
            "content": 'You resolve prediction markets strictly from the given sources. Reply ONLY with JSON: {"outcome":"YES|NO|UNRESOLVABLE","quotes":[...],"confidence":0-1}.',
        },
        {"role": "user", "content": json.dumps(market)},
    ],
}
body = json.dumps(req).encode()
r = requests.post(f"{API}/chat/completions", headers=H, data=body, timeout=120)
raw = r.content
resp = r.json()
content = resp["choices"][0]["message"]["content"].strip()
print("[resolve]", content[:400])

# 3. Signature for this completion → recover signer → match THAT attestation entry → verify_node
sig = requests.get(
    f"{API}/signature/{resp['id']}",
    headers=H,
    params={"model": MODEL, "signing_algo": "ecdsa"},
    timeout=60,
).json()

def verify_sig(raw_resp):
    text = f"{MODEL}:{sha(body)}:{sha(raw_resp)}"
    if text != sig["text"]:
        return False, None, "model:sha256(request):sha256(response) != signed text"
    rec = Account.recover_message(encode_defunct(text=sig["text"]), signature=sig["signature"])
    if rec.lower() != sig["signing_address"].lower():
        return False, None, "signature does not recover to signing_address"
    return True, rec, f"signer {rec}"

ok, recovered, why = verify_sig(raw)
print(f"[verify] signed text={sig.get('text','')[:40]}...  -> {'PASS' if ok else 'FAIL'} ({why})")
if not ok:
    sys.exit(1)

# Critical: verify_node runs only on the attestation entry whose signing_address equals
# the recovered signature address AND whose model_name equals the requested model.
entry = hw_verify.match_attestation_for_signature(ma, recovered, MODEL)
print(f"[match] signature→attestation entry {entry['signing_address']} model={entry.get('model_name')}")
hw = hw_verify.verify_node(entry, nonce, model=MODEL)
print(f"[hw] PASS tcb={hw['tdx']['tcb_status']} compose={hw['tdx']['compose_hash'][:16]}.. gpus={hw['gpu']['gpus']}")
print(f"[label] Verified")

pathlib.Path("out").mkdir(exist_ok=True)
json.dump(
    {
        "model": MODEL,
        "request_hash": sha(body),
        "response_raw": raw.decode(),
        "text": sig["text"],
        "signature": sig["signature"],
        "signing_address": sig["signing_address"],
        "content": content,
        "tcb_status": hw["tdx"]["tcb_status"],
        "compose_hash": hw["tdx"]["compose_hash"],
        "label": "Verified",
    },
    open("out/last_verdict.json", "w"),
    indent=1,
)

# 4. Tamper one byte of the response
t = bytearray(raw)
i = raw.find(b'"content"') + 12
t[i] ^= 1
ok2, _, why2 = verify_sig(bytes(t))
print(f"[tamper] flipped byte {i} -> {'PASS' if ok2 else 'FAIL'} ({why2})")
sys.exit(0 if ok and not ok2 else 1)
