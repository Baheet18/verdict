#!/usr/bin/env python3
"""Rule linter for prediction-market questions + resolution rules.

Transparent heuristics only — no model calls. A "flag" means the rule text
matches a known ambiguity pattern that historically correlates with disputes;
it is NOT a prediction that UMA will dispute the market.
"""
from __future__ import annotations
import argparse, json, re, sys

# Each rule: (id, severity, description, compiled regex OR callable(text)->bool)
HEURISTICS = []

def H(id_, sev, desc, pat=None, fn=None):
    HEURISTICS.append({"id": id_, "severity": sev, "description": desc,
                       "re": re.compile(pat, re.I | re.S) if pat else None, "fn": fn})

H("missing_source", "high", "No explicit resolution source / URL / 'according to' / 'official' named source",
  fn=lambda t: not re.search(r"(https?://|according to|resolves? (based )?on|official (site|source|announcement)|wikipedia|espn|reuters|ap news|bloomberg)", t, re.I))
H("subjective_qualifier", "high", "Subjective/undefined qualifier (significant, material, substantial, reasonable, widely, generally, major)",
  pat=r"\b(significant(ly)?|material(ly)?|substantial(ly)?|reasonable|widely (considered|regarded|seen)|generally (considered|regarded)|major(?!\s+league))\b")
H("missing_timezone", "med", "'by <date>' / deadline without timezone (UTC/ET/WAT/GMT)",
  fn=lambda t: bool(re.search(r"\bby\s+(january|february|march|april|may|june|july|august|september|october|november|december|\d{4}-\d\d-\d\d|[A-Z][a-z]+ \d{1,2})", t, re.I))
              and not re.search(r"\b(UTC|GMT|ET|EST|EDT|PT|PST|PDT|WAT|CET|CEST|timezone|time zone)\b", t, re.I))
H("ambiguous_pronoun", "med", "Pronoun-heavy resolution without clear antecedent nearby (it/they/this/that as subject of resolves)",
  pat=r"\b(resolves? (to )?(yes|no) if (it|they|this|that)\b)")
H("or_without_precedence", "med", "Multiple OR/AND conditions without explicit precedence / 'whichever earlier'",
  fn=lambda t: len(re.findall(r"\b(or|and)\b", t, re.I)) >= 4 and not re.search(r"(whichever|precedence|in that order|priority)", t, re.I))
H("unofficial_ok", "med", "Allows unofficial / social / 'credible reports' without naming a primary source",
  pat=r"\b(unofficial|credible reports?|social media|tweets?|rumou?rs?)\b")
H("missing_threshold", "med", "Comparative claim (more/less/higher/lower/win) without a numeric threshold or exact definition",
  fn=lambda t: bool(re.search(r"\b(more than|less than|higher|lower|at least|over|under)\b", t, re.I))
              and not re.search(r"\b\d+(\.\d+)?\s*%|\b\d[\d,]*\b", t))
H("self_referential", "high", "Circular resolution via community vote / trader consensus / this market's price (not the boilerplate 'This market will resolve…')",
  pat=r"\b(polymarket (users|traders)|community (vote|consensus)|price of this market|resolves? based on (the )?market price|crowd.?sourc)\b")
H("vague_time_window", "med", "Vague time window (soon, shortly, around, approximately, early/late <month> without day)",
  pat=r"\b(soon|shortly|around|approximately|roughly|early (january|february|march|april|may|june|july|august|september|october|november|december)|late (january|february|march|april|may|june|july|august|september|october|november|december))\b")
H("empty_or_short", "high", "Rules text empty or shorter than 40 characters",
  fn=lambda t: len(t.strip()) < 40)

def lint(question: str, rules: str) -> dict:
    text = f"{question.strip()}\n{rules.strip()}"
    flags = []
    for h in HEURISTICS:
        hit = False
        if h["fn"]:
            hit = bool(h["fn"](text))
        elif h["re"]:
            hit = bool(h["re"].search(text))
        if hit:
            flags.append({"id": h["id"], "severity": h["severity"], "description": h["description"]})
    return {
        "question": question,
        "rules_len": len(rules or ""),
        "flagged": bool(flags),
        "n_flags": len(flags),
        "flags": flags,
        "heuristics_version": "1.0",
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--question", default="")
    ap.add_argument("--rules", default="")
    ap.add_argument("--file", help="JSON list of {id,question,rules/description}")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.file:
        rows = json.load(open(args.file))
        results = []
        for r in rows:
            rules = r.get("rules") or r.get("description") or ""
            out = lint(r.get("question") or "", rules)
            out["id"] = r.get("id")
            results.append(out)
        flagged = sum(1 for x in results if x["flagged"])
        summary = {"markets": len(results), "flagged": flagged, "rate": flagged / max(1, len(results)),
                   "by_id": {f["id"]: [x["id"] for x in f["flags"]] for f in results},
                   "results": results}
        text = json.dumps(summary, indent=2)
        if args.out:
            open(args.out, "w").write(text)
        print(f"linter flagged {flagged}/{len(results)} markets (heuristics v1.0)")
        for f in results:
            if f["flagged"]:
                print(f"  {f.get('id')}: {[x['id'] for x in f['flags']]} | {(f['question'] or '')[:70]}")
        return
    r = lint(args.question, args.rules)
    print(json.dumps(r, indent=2))
    sys.exit(0 if not r["flagged"] else 2)

if __name__ == "__main__":
    main()
