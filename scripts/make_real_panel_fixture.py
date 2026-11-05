#!/usr/bin/env python3
"""Live: one canonical request (market_id=0, round=0) to each of the 3 panel models on NEAR AI Cloud, save the exact
request/response bytes + NEAR's ECDSA signature into contract/testdata/real_panel.json, and hardware-verify the
attestation entry whose signing_address is the recovered signer (model_name must equal the model)."""
import os, sys, json, secrets, requests
sys.path.insert(0, os.path.dirname(__file__))
import hw_verify
from verdict_request import build_request, evidence_bundle, sha, parse_outcome, PROMPT_TEMPLATE_HASH
from eth_account import Account
from eth_account.messages import encode_defunct

API = "https://cloud-api.near.ai/v1"; H = {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}
MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]
QUESTION = "Did it rain (>=1mm) at Ikeja on 2026-11-03?"
RULES = "Resolves YES if the source snapshot reports >=1mm rainfall at Ikeja for 2026-11-03, NO if it reports <1mm. Otherwise AMBIGUOUS."
SNAP_TEXT = "Daily weather summary, Ikeja (Lagos), 2026-11-03: total precipitation 4.2 mm. Source A."
SNAP2 = "Ikeja station 2026-11-03 rainfall: 4.2mm (thunderstorms in the afternoon). Source B."
EV = evidence_bundle(QUESTION, RULES, "2026-11-03T23:59:59Z", [
    {"url": "fixture://source-a", "fetched_at": "2026-11-05T10:00:00Z", "sha256": sha(SNAP_TEXT), "text": SNAP_TEXT},
    {"url": "fixture://source-b", "fetched_at": "2026-11-05T10:00:00Z", "sha256": sha(SNAP2), "text": SNAP2}])

out = {"question": QUESTION, "rules": RULES, "prompt_template_hash": PROMPT_TEMPLATE_HASH, "votes": []}
for model in MODELS:
    body = build_request(model, 0, 0, RULES, EV)
    r = requests.post(f"{API}/chat/completions", headers=H, data=body, timeout=600); r.raise_for_status()
    raw = r.content; resp = r.json()
    sig = requests.get(f"{API}/signature/{resp['id']}", headers=H, params={"model": model, "signing_algo": "ecdsa"}, timeout=120).json()
    text = f"{model}:{sha(body)}:{sha(raw)}"
    assert sig["text"] == text, (sig["text"], text)
    signer = Account.recover_message(encode_defunct(text=text), signature=sig["signature"]).lower()
    assert signer == sig["signing_address"].lower()
    nonce = secrets.token_hex(32)
    att = requests.get(f"{API}/attestation/report", headers=H, params={"model": model, "signing_algo": "ecdsa", "nonce": nonce}, timeout=120).json()
    entry = hw_verify.match_attestation_for_signature(att.get("model_attestations"), signer, model)
    try:
        hw = hw_verify.verify_node(entry, nonce, model=model)
    except AssertionError as e:
        raise SystemExit(f"HW CHECK FAILED for {model} {signer}: {e}")
    o = parse_outcome(raw)
    print(f"== {model}: signer={signer} outcome={o} hw={hw['tdx']['label']} seed_echo={resp.get('system_fingerprint')}")
    out["votes"].append({"model": model, "signing_address": signer, "request_raw": body.decode(), "response_raw": raw.decode(),
                         "signature": sig["signature"].removeprefix("0x"), "text": text, "parsed_outcome": o,
                         "attestation_report_sha256": sha(json.dumps(att, sort_keys=True)), "hw_label": hw["tdx"]["label"],
                         "rtmr3_events": hw["tdx"]["rtmr3_events"], "normalized_compose_hash": hw["tdx"]["normalized_compose_hash"]})
p = os.path.join(os.path.dirname(__file__), "..", "contract", "testdata", "real_panel.json")
open(p, "w").write(json.dumps(out, indent=1)); print("wrote", p)
