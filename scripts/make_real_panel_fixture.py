#!/usr/bin/env python3
"""Live: one canonical request (market_id=0, round=0) to each of the 3 panel models on NEAR AI Cloud.

Round 6: uses a REAL post-cutoff sports market from the 124-market benchmark
(Polymarket 4329887 — Saints vs. Giants, UMA YES / panel YES), not the Ikeja fixture.
Saves exact request/response bytes + ECDSA signatures into contract/testdata/real_panel.json,
publishes the evidence bundle under fixtures/evidence/<sha256>.json, and hardware-verifies
the attestation entry whose signing_address is the recovered signer.
"""
from __future__ import annotations
import os, sys, json, secrets, requests, pathlib
sys.path.insert(0, os.path.dirname(__file__))
import hw_verify
from verdict_request import build_request, sha, parse_outcome, PROMPT_TEMPLATE_HASH
from eth_account import Account
from eth_account.messages import encode_defunct

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = "https://cloud-api.near.ai/v1"
H = {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}
MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]
POLY_ID = "4329887"
BENCH_EV = ROOT / "out" / "bench2" / "evidence" / f"{POLY_ID}.json"

assert os.environ.get("NEARAI_API_KEY"), "NEARAI_API_KEY required"
wrap = json.loads(BENCH_EV.read_text())
# Prefer the already-canonical evidence string from the benchmark (sort_keys etc.)
if isinstance(wrap["evidence"], str):
    EV = wrap["evidence"]
    inner = json.loads(EV)
else:
    inner = wrap["evidence"]
    EV = json.dumps(inner, ensure_ascii=False, sort_keys=True)
RULES = wrap["rules"]
QUESTION = inner["question"]
assert QUESTION == "Saints vs. Giants"
EV_H = sha(EV)

# Publish evidence content-addressed (committable). GitHub raw URL is ONLY valid after human push approval.
ev_dir = ROOT / "fixtures" / "evidence"
ev_dir.mkdir(parents=True, exist_ok=True)
ev_path = ev_dir / f"{EV_H}.json"
payload = {
    "polymarket_id": POLY_ID,
    "question": QUESTION,
    "rules": RULES,
    "market_end": inner.get("market_end"),
    "evidence": EV,
    "evidence_sha256": EV_H,
    "source_snapshots": inner.get("source_snapshots"),
    "note": "Post-cutoff NFL moneyline from bench2. evidence_uri for testnet = GitHub raw AFTER human approves push.",
    "proposed_evidence_uri_after_push": f"https://raw.githubusercontent.com/Baheet18/verdict/master/fixtures/evidence/{EV_H}.json",
}
ev_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
print(f"wrote {ev_path} sha={EV_H}")

out = {
    "question": QUESTION,
    "rules": RULES,
    "prompt_template_hash": PROMPT_TEMPLATE_HASH,
    "polymarket_id": POLY_ID,
    "evidence_sha256": EV_H,
    "evidence_path": f"fixtures/evidence/{EV_H}.json",
    "proposed_evidence_uri_after_push": payload["proposed_evidence_uri_after_push"],
    "votes": [],
}
for model in MODELS:
    body = build_request(model, 0, 0, RULES, EV)
    r = requests.post(f"{API}/chat/completions", headers=H, data=body, timeout=600)
    r.raise_for_status()
    raw = r.content
    resp = r.json()
    sig = None
    for _ in range(5):
        s = requests.get(f"{API}/signature/{resp['id']}", headers=H, params={"model": model, "signing_algo": "ecdsa"}, timeout=120)
        if s.status_code == 200:
            sig = s.json()
            break
    assert sig, f"no signature for {model}"
    text = f"{model}:{sha(body)}:{sha(raw)}"
    assert sig["text"] == text, (sig["text"], text)
    signer = Account.recover_message(encode_defunct(text=text), signature=sig["signature"]).lower()
    assert signer == sig["signing_address"].lower()
    nonce = secrets.token_hex(32)
    att = requests.get(f"{API}/attestation/report", headers=H, params={"model": model, "signing_algo": "ecdsa", "nonce": nonce}, timeout=120).json()
    # shared-signer policy
    rep = hw_verify.verify_signer_replicas(att.get("model_attestations"), signer, model, nonce)
    assert rep["label"] == "Hardware + host stack verified", rep
    o = parse_outcome(raw)
    print(f"== {model}: signer={signer} outcome={o} label={rep['label']}")
    out["votes"].append({
        "model": model,
        "signing_address": signer,
        "request_raw": body.decode(),
        "response_raw": raw.decode(),
        "signature": sig["signature"].removeprefix("0x"),
        "text": text,
        "parsed_outcome": o,
        "attestation_report_sha256": sha(json.dumps(att, sort_keys=True)),
        "hw_label": rep["label"],
        "rtmr3_events": (rep["results"][0]["tdx"]["rtmr3_events"] if rep.get("results") else []),
        "normalized_compose_hash": (rep["results"][0]["tdx"].get("normalized_compose_hash") if rep.get("results") else None),
    })

dest = ROOT / "contract" / "testdata" / "real_panel.json"
dest.write_text(json.dumps(out, indent=1))
print("wrote", dest)
outcomes = {v["parsed_outcome"] for v in out["votes"]}
print("panel outcomes:", outcomes)
