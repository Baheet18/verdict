#!/usr/bin/env python3
"""Copy a small sanitized subset of VERIFIED live runs into fixtures/scoreboard_cache/.

Strips request_raw/response_raw (large) but keeps signature + outcome + label + signer
so the scoreboard and offline verifier can rebuild without re-hitting the API.
Also copies the immutable real_panel fixture pointer.
"""
from __future__ import annotations
import json, pathlib, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "fixtures" / "scoreboard_cache"
DEST.mkdir(parents=True, exist_ok=True)

KEEP = ("ok", "market_id", "polymarket_id", "model", "signature", "signer", "label",
        "outcome", "reasoning", "finish_reason", "usage", "cost_usd", "at",
        "request_sha256", "response_sha256")

def sanitize(r: dict) -> dict:
    out = {k: r[k] for k in KEEP if k in r}
    # Derive hashes if raw present
    import hashlib
    if "request_raw" in r and "request_sha256" not in out:
        out["request_sha256"] = hashlib.sha256(r["request_raw"].encode()).hexdigest()
    if "response_raw" in r and "response_sha256" not in out:
        out["response_sha256"] = hashlib.sha256(r["response_raw"].encode()).hexdigest()
    # Keep compact signature fields only
    sig = r.get("signature") or {}
    if isinstance(sig, dict):
        out["signature"] = {k: sig[k] for k in ("signature", "signing_address", "text", "signing_algo") if k in sig}
    return out

def from_dir(src: pathlib.Path, limit=30):
    n = 0
    for p in sorted(src.glob("*.json")):
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        if not r.get("ok"):
            continue
        if r.get("label") != "Hardware + host stack verified":
            continue
        out = sanitize(r)
        dest = DEST / p.name
        dest.write_text(json.dumps(out, indent=1))
        n += 1
        if n >= limit:
            break
    return n

def main():
    n = 0
    for d in [ROOT / "out" / "bench2" / "runs", ROOT / "out" / "bench" / "runs", ROOT / "out" / "bench_disputed" / "runs"]:
        if d.exists():
            n += from_dir(d, limit=40)
    # Keep attest sample meta if present
    meta = {"note": "Sanitized verified votes only. No API keys. Raw request/response stripped.",
            "label_required": "Hardware + host stack verified", "count": n}
    (DEST / "_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"cached {n} verified votes into {DEST}")

if __name__ == "__main__":
    main()
