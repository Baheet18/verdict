#!/usr/bin/env python3
"""Verdict attestation demo: resolve a market in a TEE model, verify signature + attestation, then tamper one byte."""
import os, sys, json, hashlib, secrets, requests
from eth_account import Account
from eth_account.messages import encode_defunct

API = "https://cloud-api.near.ai/v1"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "Qwen/Qwen3.8-27B"
H = {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}
sha = lambda b: hashlib.sha256(b).hexdigest()

# 1. Fresh attestation bound to our own nonce
nonce = secrets.token_hex(32)
att = requests.get(f"{API}/attestation/report", headers=H,
                   params={"model": MODEL, "signing_algo": "ecdsa", "nonce": nonce}, timeout=60).json()
ma = att.get("model_attestations") or []
if not ma:
    sys.exit(f"[attest] {MODEL}: no model_attestations -> not TEE-attested")
for m in ma:
    assert m["request_nonce"] == nonce, "nonce mismatch in model attestation"
att_addrs = {m["signing_address"].lower() for m in ma}
print(f"[attest] model={MODEL} nodes={len(ma)} nonce echoed OK, signers={sorted(att_addrs)}")
g = att["gateway_attestation"]; rd = g["report_data"]
assert rd[:40].lower() == g["signing_address"][2:].lower() and rd[64:128] == nonce, "gateway report_data binding failed"
print("[attest] gateway report_data = signing_address(padded) || nonce  OK")

# 2. Resolution-style completion
market = {"question": "Will it rain in Lagos (Ikeja) on 2026-11-03?",
          "rules": "Resolves YES if the locked source reports >=1.0 mm precipitation for Ikeja on that date (WAT). Otherwise NO.",
          "sources": ["[NiMet daily bulletin, 2026-11-04] Ikeja recorded 3.2 mm of rainfall on 3 November 2026, mostly in the evening."]}
req = {"model": MODEL, "max_tokens": 400, "temperature": 0,
       "messages": [{"role": "system", "content": "You resolve prediction markets strictly from the given sources. Reply ONLY with JSON: {\"outcome\":\"YES|NO|UNRESOLVABLE\",\"quotes\":[...],\"confidence\":0-1}."},
                    {"role": "user", "content": json.dumps(market)}]}
body = json.dumps(req).encode()
r = requests.post(f"{API}/chat/completions", headers=H, data=body, timeout=120)
raw = r.content
resp = r.json()
print("[resolve]", resp["choices"][0]["message"]["content"].strip()[:400])

# 3. Signature for this completion
sig = requests.get(f"{API}/signature/{resp['id']}", headers=H,
                   params={"model": MODEL, "signing_algo": "ecdsa"}, timeout=60).json()
def verify(raw_resp):
    text = f"{MODEL}:{sha(body)}:{sha(raw_resp)}"
    if text != sig["text"]: return False, "model:sha256(request):sha256(response) != signed text"
    rec = Account.recover_message(encode_defunct(text=sig["text"]), signature=sig["signature"])
    if rec.lower() != sig["signing_address"].lower(): return False, "signature does not recover to signing_address"
    if rec.lower() not in att_addrs: return False, "signer not among attested model nodes"
    return True, f"signer {rec} is attested"
ok, why = verify(raw)
print(f"[verify] signed text={sig.get('text','')[:40]}...  -> {'PASS' if ok else 'FAIL'} ({why})")

# 4. Tamper one byte of the response
t = bytearray(raw); i = raw.find(b'"content"') + 12; t[i] ^= 1
ok2, why2 = verify(bytes(t))
print(f"[tamper] flipped byte {i} -> {'PASS' if ok2 else 'FAIL'} ({why2})")
sys.exit(0 if ok and not ok2 else 1)
