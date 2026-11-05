#!/usr/bin/env python3
"""Rebuild scoreboard/index.html from cached VERIFIED panel runs only.

Uses fixtures/scoreboard_cache/ (immutable sanitized subset) and optionally
out/bench2/summary.json for aggregate numbers. Never calls the API.
"""
from __future__ import annotations
import json, pathlib, datetime, html

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "fixtures" / "scoreboard_cache"
OUT_HTML = ROOT / "scoreboard" / "index.html"
BENCH2 = ROOT / "out" / "bench2" / "summary.json"
DISPUTED = ROOT / "out" / "bench_disputed" / "summary.json"
CURATED = ROOT / "out" / "bench_disputed_curated" / "summary.json"
LINT_CTRL = ROOT / "out" / "lint_control" / "summary.json"
CURATED_DOC = ROOT / "docs" / "BENCHMARK_DISPUTED_CURATED.md"
DOCS_BENCH = ROOT / "docs" / "BENCHMARK.md"

def load_cache_runs():
    runs = []
    for p in sorted(CACHE.glob("*.json")):
        if p.name.endswith("_meta.json"):
            continue
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        if not r.get("ok"):
            continue
        # Only include Hardware + host stack verified votes
        if r.get("label") != "Hardware + host stack verified":
            continue
        runs.append({
            "file": p.name,
            "model": r.get("model"),
            "outcome": r.get("outcome"),
            "signer": r.get("signer"),
            "label": r.get("label"),
            "polymarket_id": r.get("polymarket_id") or r.get("market_id"),
            "at": r.get("at"),
            "finish_reason": r.get("finish_reason"),
        })
    return runs

def main():
    runs = load_cache_runs()
    b2_raw = json.loads(BENCH2.read_text()) if BENCH2.exists() else None
    b2 = (b2_raw.get("summary") or {}).get("post_cutoff") if b2_raw else None
    b2_cost = (b2_raw.get("summary") or {}).get("cost_usd") if b2_raw else None
    dj = json.loads(DISPUTED.read_text()) if DISPUTED.exists() else None
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    rows = "\n".join(
        f"<tr><td>{html.escape(str(r['polymarket_id']))}</td>"
        f"<td>{html.escape(r['model'] or '')}</td>"
        f"<td><code>{html.escape(r['outcome'] or '')}</code></td>"
        f"<td class='ok'>{html.escape(r['label'])}</td>"
        f"<td><code>{html.escape((r['signer'] or '')[:14])}…</code></td></tr>"
        for r in runs
    )
    b2_html = ""
    if b2:
        b2_html = f"""<section>
  <h2>Post-cutoff sports benchmark (cached summary)</h2>
  <p>Markets: {b2.get('markets')} · Panel settled: {b2.get('panel_settled')} ·
     Agree UMA: {b2.get('panel_settled_agree_uma')} · Disagree: {b2.get('panel_settled_disagree_uma')} ·
     Escalated: {b2.get('escalated')} · Cost USD: {b2_cost}</p>
  <p class="note">Headline = post-cutoff accuracy among panel-settled markets. Easy sports markets; not proof on disputed cases. Source: docs/BENCHMARK.md.</p>
</section>"""
    dj_html = ""
    if dj:
        lint = dj.get("linter") or {}
        dj_html = f"""<section>
  <h2>UMA-disputed post-cutoff benchmark (cached summary)</h2>
  <p>Classification: {html.escape(dj.get('classification',''))}</p>
  <p>Markets: {dj.get('markets')} · Settled: {dj.get('panel_settled')} ·
     Agree UMA: {dj.get('panel_settled_agree_uma')} · Disagree: {dj.get('panel_settled_disagree_uma')} ·
     Escalated: {dj.get('escalated')} · Cost USD: {dj.get('cost_usd')}</p>
  <p>Rule linter flagged 27/34 disputed (matched non-disputed control 26/34 — gap not marketed; see base-rate section).</p>
</section>"""

    curated_html = ""
    if CURATED.exists():
        cj = json.loads(CURATED.read_text())
        curated_html = f"""<section>
  <h2>Curated multi-source disputed subset</h2>
  <p>Markets curated: {cj.get('n_curated')} · Settles with evidence: {cj.get('settles_with_evidence')} ·
     Abstains with evidence: {cj.get('abstains_with_evidence')} · Invented answers: {cj.get('invented_answers')} ·
     Cost USD: {cj.get('cost_usd')}</p>
  <p class="note">Official + 2 reputable reports required; skips if fetch bar not met. Source: docs/BENCHMARK_DISPUTED_CURATED.md.</p>
</section>"""
    ctrl_html = ""
    if LINT_CTRL.exists():
        lj = json.loads(LINT_CTRL.read_text())
        ctrl_html = f"""<section>
  <h2>Rule linter base rate</h2>
  <p>Disputed flagged: {lj.get('disputed_flagged')}/{lj.get('disputed_n')} ·
     Matched non-disputed control: {lj.get('control_flagged')}/{lj.get('control_n')} ·
     Gap: {lj.get('gap'):.3f} · Use as headline: {lj.get('use_in_readme')}</p>
  <p class="note">Selection fixed before lint outcomes (`out/lint_control/selection.json`). Gap &lt; 0.10 → not marketed.</p>
</section>"""
    
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Verdict scoreboard</title>
<style>
body{{font-family:ui-sans-serif,system-ui,sans-serif;margin:2rem;max-width:960px;color:#111}}
h1{{margin-bottom:.25rem}} .meta{{color:#555;margin-bottom:1.5rem}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;font-size:.9rem}}
th{{background:#f4f4f4}} .ok{{color:#0a7}} .note{{color:#666;font-size:.9rem}}
code{{font-size:.85rem}} section{{margin:1.5rem 0;padding:1rem;background:#fafafa;border:1px solid #eee}}
</style>
</head>
<body>
<h1>Verdict scoreboard</h1>
<p class="meta">Rebuilt from <code>fixtures/scoreboard_cache/</code> at {html.escape(now)}. Offline — no API key required. Only votes labeled <strong>Hardware + host stack verified</strong> are listed.</p>
{b2_html}
{dj_html}
{curated_html}
{ctrl_html}
<section>
  <h2>Cached verified votes ({len(runs)})</h2>
  <table>
    <thead><tr><th>Market</th><th>Model</th><th>Outcome</th><th>Label</th><th>Signer</th></tr></thead>
    <tbody>
{rows or '<tr><td colspan="5">No verified cached votes yet. Run <code>python scripts/build_scoreboard_cache.py</code> after a verified live run.</td></tr>'}
    </tbody>
  </table>
</section>
<p class="note">Source: local Verdict repo. Demo video / X post not included in this offline artifact.</p>
</body>
</html>
"""
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(page)
    print(f"wrote {OUT_HTML} with {len(runs)} verified cached votes")

if __name__ == "__main__":
    main()
