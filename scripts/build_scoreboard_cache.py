#!/usr/bin/env python3
"""Sanitize ALL verified panel runs into fixtures/scoreboard_cache/ + copy summaries.

Strips request_raw/response_raw (large) but keeps signature + outcome + label + signer
so the scoreboard and offline verifier can rebuild without re-hitting the API.
Round 6: ingest 124 sports + 34 thin disputed + curated + hand + legacy.
"""
from __future__ import annotations
import json, pathlib, hashlib, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "fixtures" / "scoreboard_cache"
SUM_DEST = ROOT / "fixtures" / "benchmark_summaries"
DEST.mkdir(parents=True, exist_ok=True)
SUM_DEST.mkdir(parents=True, exist_ok=True)

KEEP = ("ok", "market_id", "polymarket_id", "model", "signature", "signer", "label",
        "outcome", "reasoning", "finish_reason", "usage", "cost_usd", "at",
        "request_sha256", "response_sha256", "evidence_sha256", "answer_bearing_sources",
        "compose_manager_images", "set")

SOURCES = [
    (ROOT / "out" / "bench2" / "runs", "sports_postcutoff_124"),
    (ROOT / "out" / "bench_disputed" / "runs", "disputed_thin_34"),
    (ROOT / "out" / "bench_disputed_curated" / "runs", "disputed_curated_js_shells"),
    (ROOT / "out" / "bench_disputed_hand" / "runs", "disputed_hand_answer_bearing"),
    (ROOT / "out" / "bench", "legacy_5"),  # files live at out/bench/*.json
]

SUMMARIES = [
    (ROOT / "out" / "bench2" / "summary.json", "sports_postcutoff_124.json"),
    (ROOT / "out" / "bench_disputed" / "summary.json", "disputed_thin_34.json"),
    (ROOT / "out" / "bench_disputed_curated" / "summary.json", "disputed_curated.json"),
    (ROOT / "out" / "bench_disputed_hand" / "summary.json", "disputed_hand.json"),
    (ROOT / "out" / "reattest" / "reattest.json", "reattest_today.json"),
]

def sanitize(r: dict, set_name: str) -> dict:
    out = {k: r[k] for k in KEEP if k in r}
    out["set"] = set_name
    if "request_raw" in r and "request_sha256" not in out:
        out["request_sha256"] = hashlib.sha256(r["request_raw"].encode()).hexdigest()
    if "response_raw" in r and "response_sha256" not in out:
        out["response_sha256"] = hashlib.sha256(r["response_raw"].encode()).hexdigest()
    sig = r.get("signature") or {}
    if isinstance(sig, dict):
        out["signature"] = {k: sig[k] for k in ("signature", "signing_address", "text", "signing_algo") if k in sig}
    # truncate long reasoning for size
    if isinstance(out.get("reasoning"), str) and len(out["reasoning"]) > 400:
        out["reasoning"] = out["reasoning"][:400] + "…"
    return out

def from_dir(src: pathlib.Path, set_name: str) -> int:
    n = 0
    if not src.exists():
        return 0
    for p in sorted(src.glob("*.json")):
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        if r.get("ok") is False:
            continue
        if not r.get("label") and not r.get("ok"):
            continue
        if "kimi" in (r.get("model") or "").lower():
            continue
        # Keep all labels so scoreboard can show trust per vote; filter in UI.
        out = sanitize(r, set_name)
        # unique filename across sets
        dest = DEST / f"{set_name}__{p.name}"
        dest.write_text(json.dumps(out, indent=1))
        n += 1
    return n

def main():
    # clear previous vote files (keep dir)
    for p in DEST.glob("*.json"):
        if p.name != "_meta.json":
            p.unlink()
    counts = {}
    total = 0
    for d, name in SOURCES:
        c = from_dir(d, name)
        counts[name] = c
        total += c
    for src, name in SUMMARIES:
        if src.exists():
            shutil.copy2(src, SUM_DEST / name)
    meta = {
        "note": "Sanitized votes from all benchmark runs. No API keys. Raw request/response stripped.",
        "counts": counts,
        "total": total,
        "summaries": [s[1] for s in SUMMARIES],
    }
    (DEST / "_meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))

if __name__ == "__main__":
    main()
