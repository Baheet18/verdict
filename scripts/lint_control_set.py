#!/usr/bin/env python3
"""Matched NON-disputed control set for the rule linter base rate (round 5).

Selection is documented BEFORE looking at linter outcomes:
  - Same Gamma dump as disputed set (out/bench2/pm_closed.json)
  - umaResolutionStatus == resolved, binary prices
  - endDate AFTER 2026-10-17 (same window)
  - umaResolutionStatuses history does NOT contain "disputed"
  - Matched 1:1 to the 34 disputed markets by (bucket, volume rank)
    where bucket ∈ {sports, geopolitics_conflict, politics_election, corporate_markets, other}
    inferred from question keywords (transparent, not Gamma tags — Gamma has no category field here).
  - Within each bucket, greedily pick the non-disputed market whose log10(volume+1) is closest
    to the disputed market's, without reuse.
  - Cap = len(disputed) (34). No cherry-picking by linter result: selection writes
    out/lint_control/selection.json first; then rule_linter runs on that file.

Does NOT invent disputes or drop markets after seeing flags.
"""
from __future__ import annotations
import json, math, datetime, pathlib, subprocess, sys, re

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "lint_control"
OUT.mkdir(parents=True, exist_ok=True)
CUTOFF = "2026-10-17"
DISPUTED = ROOT / "out" / "bench_disputed" / "markets.json"
PM = ROOT / "out" / "bench2" / "pm_closed.json"

SPORTS = re.compile(r"\b(world series|ballon|nba|nfl|nhl|mlb|laliga|premier|vs\.|spread|o/u|over/under|rockets|kings|guardians|hawks|baystars|broncos|blues|red wings|ballon d.or|raphinha|ronaldo|vanvleet|lavine)\b", re.I)
GEO = re.compile(r"\b(nato|russia|ukraine|iran|iraq|peace|military|clash|target|cartel|moscow|merz|von der leyen)\b", re.I)
ELEC = re.compile(r"\b(house seat|presidential|election|democratic|republican|israeli|bulgarian|seats)\b", re.I)
CORP = re.compile(r"\b(apple|nvidia|visa|market cap|album|drake|trump say)\b", re.I)

def bucket(q: str) -> str:
    if SPORTS.search(q or ""): return "sports"
    if GEO.search(q or ""): return "geopolitics_conflict"
    if ELEC.search(q or ""): return "politics_election"
    if CORP.search(q or ""): return "corporate_markets"
    return "other"

def parse_day(ed: str):
    try:
        return datetime.datetime.fromisoformat(ed.replace("Z", "+00:00").replace(" ", "T").split("+")[0] + "+00:00").date()
    except Exception:
        return None

def main():
    disputed = json.loads(DISPUTED.read_text())
    rows = json.loads(PM.read_text())
    # pool of non-disputed
    pool = []
    for m in rows:
        hist = str(m.get("umaResolutionStatuses") or "")
        if "disputed" in hist:
            continue
        if m.get("umaResolutionStatus") != "resolved":
            continue
        if m.get("outcomePrices") not in ('["1", "0"]', '["0", "1"]'):
            continue
        d = parse_day(m.get("endDate") or "")
        if not d or str(d) <= CUTOFF:
            continue
        pool.append({
            "id": m["id"], "question": m["question"], "description": m.get("description") or "",
            "endDate": m["endDate"], "volumeNum": m.get("volumeNum") or 0,
            "event_day": str(d), "bucket": bucket(m["question"] or ""),
            "umaResolutionStatus": m["umaResolutionStatus"],
            "classification": "non_disputed_control",
        })
    # index pool by bucket
    by_b = {}
    for p in pool:
        by_b.setdefault(p["bucket"], []).append(p)
    for b in by_b:
        by_b[b].sort(key=lambda x: -x["volumeNum"])

    used = set()
    selected = []
    pairing = []
    for d in disputed:
        b = bucket(d["question"] or "")
        target = math.log10((d.get("volumeNum") or 0) + 1)
        cands = [p for p in by_b.get(b, []) if p["id"] not in used]
        if not cands:
            # fall back to any unused
            cands = [p for p in pool if p["id"] not in used]
        if not cands:
            break
        best = min(cands, key=lambda p: abs(math.log10(p["volumeNum"] + 1) - target))
        used.add(best["id"])
        selected.append(best)
        pairing.append({
            "disputed_id": d["id"], "disputed_vol": d.get("volumeNum"), "disputed_q": d["question"][:80],
            "control_id": best["id"], "control_vol": best["volumeNum"], "control_q": best["question"][:80],
            "bucket": b, "control_bucket": best["bucket"],
            "log10_vol_delta": abs(math.log10(best["volumeNum"] + 1) - target),
        })

    selection = {
        "method": "1:1 match disputed→non-disputed by keyword-bucket + closest log10(volume); same endDate window >2026-10-17; resolved binary; no 'disputed' in umaResolutionStatuses. Selection written before linter run.",
        "cutoff": CUTOFF,
        "n_disputed": len(disputed),
        "n_control": len(selected),
        "n_pool_non_disputed": len(pool),
        "bucket_counts_disputed": {b: sum(1 for d in disputed if bucket(d["question"]) == b) for b in sorted({bucket(d["question"]) for d in disputed})},
        "bucket_counts_control": {b: sum(1 for s in selected if s["bucket"] == b) for b in sorted({s["bucket"] for s in selected})},
        "pairing": pairing,
        "markets": selected,
    }
    (OUT / "selection.json").write_text(json.dumps(selection, indent=2))
    lint_in = [{"id": m["id"], "question": m["question"], "rules": m.get("description") or ""} for m in selected]
    (OUT / "lint_input.json").write_text(json.dumps(lint_in))
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "rule_linter.py"),
                           "--file", str(OUT / "lint_input.json"), "--out", str(OUT / "lint.json")])
    lint = json.loads((OUT / "lint.json").read_text())
    disp_lint = json.loads((ROOT / "out" / "bench_disputed" / "lint.json").read_text())
    summary = {
        "disputed_flagged": disp_lint["flagged"],
        "disputed_n": disp_lint["markets"],
        "control_flagged": lint["flagged"],
        "control_n": lint["markets"],
        "gap": disp_lint["flagged"] / max(1, disp_lint["markets"]) - lint["flagged"] / max(1, lint["markets"]),
        "selection_path": str(OUT / "selection.json"),
        "use_in_readme": None,  # filled below
    }
    # Only recommend the headline if the gap is real (disputed rate meaningfully higher)
    summary["use_in_readme"] = bool(summary["gap"] >= 0.10 and summary["control_n"] >= 30)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"linter flagged {disp_lint['flagged']}/{disp_lint['markets']} disputed vs {lint['flagged']}/{lint['markets']} non-disputed")
    print("use_in_readme:", summary["use_in_readme"], "(requires gap>=0.10)")

if __name__ == "__main__":
    main()
