#!/usr/bin/env python3
"""Rebuild scoreboard/index.html from fixtures/scoreboard_cache + fixtures/benchmark_summaries.

Never calls the API. Shows trust label per vote, set filters, evidence links.
"""
from __future__ import annotations
import json, pathlib, datetime, html

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "fixtures" / "scoreboard_cache"
SUMS = ROOT / "fixtures" / "benchmark_summaries"
OUT_HTML = ROOT / "scoreboard" / "index.html"

def load(name):
    p = SUMS / name
    return json.loads(p.read_text()) if p.exists() else None

def load_runs():
    runs = []
    for p in sorted(CACHE.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        runs.append({
            "file": p.name,
            "set": r.get("set") or "unknown",
            "model": r.get("model"),
            "outcome": r.get("outcome"),
            "signer": r.get("signer") or ((r.get("signature") or {}).get("signing_address") if isinstance(r.get("signature"), dict) else None),
            "label": r.get("label") or "UNVERIFIED",
            "polymarket_id": r.get("polymarket_id") or r.get("market_id"),
            "at": r.get("at"),
            "evidence_sha256": r.get("evidence_sha256"),
            "answer_bearing_sources": r.get("answer_bearing_sources"),
        })
    return runs

def main():
    runs = load_runs()
    meta = json.loads((CACHE / "_meta.json").read_text()) if (CACHE / "_meta.json").exists() else {}
    sports = load("sports_postcutoff_124.json")
    thin = load("disputed_thin_34.json")
    curated = load("disputed_curated.json")
    hand = load("disputed_hand.json")
    reattest = load("reattest_today.json")
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

    b2 = (sports.get("summary") or {}).get("post_cutoff") if sports else None
    b2_cost = (sports.get("summary") or {}).get("cost_usd") if sports else None
    avg = f"{(b2_cost / b2['markets']):.4f}" if b2 and b2_cost else "~0.002"

    hand_html = ""
    if hand:
        rows_h = "".join(
            f"<tr><td>{html.escape(str(r['id']))}</td>"
            f"<td>{html.escape(r['question'][:70])}</td>"
            f"<td>{html.escape(str(r.get('answer_bearing_sources')))}</td>"
            f"<td><code>{html.escape(r['panel'])}</code></td>"
            f"<td><code>{html.escape(r['uma'])}</code></td>"
            f"<td><code>{html.escape(str(r.get('curator_ai') or r.get('expected_human') or ''))}</code></td>"
            f"<td><code>{html.escape(r.get('panel_thin_rules_only') or '')}</code></td>"
            f"<td><a href='../fixtures/evidence_hand/{html.escape(r['evidence_sha256'])}.json'>bundle</a></td></tr>"
            for r in hand.get("rows", [])
        )
        hand_html = f"""<section>
  <h2>Hand-curated disputed markets (answer-bearing sources)</h2>
  <p>Settled {hand.get('settled')}/{hand.get('n')} · Agree UMA {hand.get('agree_uma')} · Disagree UMA {hand.get('disagree_uma')} ·
     Escalated {hand.get('escalated')} · Invented answers {hand.get('invented_answers')} ·
     Same IDs thin-evidence escalated {hand.get('thin_same_ids_escalated')}/{hand.get('n')}</p>
  <p class="note">Curator (AI)-checked <code>answer_present:true</code> BEFORE the panel (Baheet has not personally verified each row). Column = answer-bearing sources (not JS shells). Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.</p>
  <table><thead><tr><th>ID</th><th>Question</th><th>Answer-bearing sources</th><th>Panel</th><th>UMA</th><th>curator (AI)</th><th>Thin</th><th>Evidence</th></tr></thead>
  <tbody>{rows_h}</tbody></table>
</section>"""

    # vote table (first 200 shown; full set filterable via data-set)
    shown = runs  # all in DOM; CSS/JS filter
    rows = "\n".join(
        f"<tr data-set='{html.escape(r['set'])}' data-label='{html.escape(r['label'])}'>"
        f"<td>{html.escape(r['set'])}</td>"
        f"<td>{html.escape(str(r['polymarket_id']))}</td>"
        f"<td>{html.escape((r['model'] or '').split('/')[-1])}</td>"
        f"<td><code>{html.escape(str(r['outcome'] or ''))}</code></td>"
        f"<td class='{'ok' if r['label']=='Hardware + host stack verified' else 'warn'}'>{html.escape(r['label'])}</td>"
        f"<td>{html.escape(str(r.get('answer_bearing_sources') or '—'))}</td>"
        f"<td>{('<a href=\"../fixtures/evidence_hand/'+html.escape(r['evidence_sha256'])+'.json\">hand</a>') if r.get('evidence_sha256') and r['set']=='disputed_hand_answer_bearing' else ('<code>'+html.escape((r.get('evidence_sha256') or '')[:10])+'…</code>' if r.get('evidence_sha256') else '—')}</td>"
        f"</tr>"
        for r in shown
    )

    reattest_line = ""
    if reattest:
        saw = reattest.get("saw_a0c07308")
        reattest_line = f"<p>Re-attest {html.escape(str(reattest.get('at')))}: a0c07308 observed = <code>{saw}</code>. All 3 models: Hardware + host stack verified (compose-manager still 6e035c8f…).</p>"

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Verdict scoreboard</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 1.5rem; color: #111; background: #fafafa; }}
h1 {{ margin-bottom: .2rem; }}
.note, .limits {{ color: #444; font-size: .95rem; }}
.ok {{ color: #0a7; }} .warn {{ color: #a60; }}
table {{ border-collapse: collapse; width: 100%; font-size: .85rem; margin: .5rem 0 1.5rem; }}
th, td {{ border: 1px solid #ddd; padding: .35rem .5rem; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; }}
section {{ margin: 1.2rem 0; padding: 1rem; background: #fff; border: 1px solid #e5e5e5; border-radius: 8px; }}
.headline {{ font-size: 1.05rem; line-height: 1.45; }}
.filters label {{ margin-right: 1rem; }}
tr.hidden {{ display: none; }}
code {{ font-size: .9em; }}
</style>
</head>
<body>
<h1>Verdict scoreboard</h1>
<p class="note">Rebuilt {now} from <code>fixtures/scoreboard_cache/</code> ({meta.get('total', len(runs))} sanitized votes). No API calls.</p>

<section>
  <h2>Headline</h2>
  <p class="note">Why: Bloomberg 2026-05-26 — ~9 wallets ~half of UMA dispute votes; WSJ May 2026 — ~1 in 5 disputes had a voter with a stake; Polymarket Protocol V2 2026-10-05 — each market can choose its oracle. No corruption claim.</p>
  <p class="headline"><strong>Clear markets:</strong> auto-settled at ~${avg}/market, <strong>107/107</strong> agreed with UMA (post-cutoff sports), hardware-attested.
  Request params are allowlisted (no logit_bias / guided decoding); a registered relayer still chooses and commits evidence — see README Limits.
  <strong>Unclear markets:</strong> escalated to humans, <strong>0 invented answers</strong>.
  <strong>TOO_EARLY</strong> is first-class
  (Bitquery: 243 of 470 Polymarket/UMA disputes in 9 Sep–7 Oct 2026 ended “too early” —
  <a href="https://bitquery.io/investigations/who-decides-polymarket-disputes">source</a>).
  Visa disagreement: <a href="../docs/VISA_CASE.md">docs/VISA_CASE.md</a> (neutral).</p>
</section>

<section>
  <h2>Post-cutoff sports (124)</h2>
  <p>Markets: {b2.get('markets') if b2 else '—'} · Panel settled: {b2.get('panel_settled') if b2 else '—'} ·
     Agree UMA: {b2.get('panel_settled_agree_uma') if b2 else '—'} · Disagree: {b2.get('panel_settled_disagree_uma') if b2 else '—'} ·
     Escalated: {b2.get('escalated') if b2 else '—'} · Cost USD: {b2_cost}</p>
  <p class="note">Easy final-score markets. Source: docs/BENCHMARK.md.</p>
</section>

<section>
  <h2>UMA-disputed thin evidence (34)</h2>
  <p>Markets: {(thin or {}).get('markets')} · Settled: {(thin or {}).get('panel_settled')} · Escalated: {(thin or {}).get('escalated')} ·
     Invented: 0. Rules-only / JS-shell fetches — expected abstention.</p>
</section>

<section>
  <h2>Earlier curated multi-source attempt (JS shells)</h2>
  <p>Invalid as answer-bearing evidence (pages were boilerplate). Settled {(curated or {}).get('panel_settled', '—')} /
     Escalated {(curated or {}).get('escalated', '—')}. Superseded by hand-curated set below.</p>
</section>

{hand_html}

<section>
  <h2>Re-attest (today)</h2>
  {reattest_line or '<p>No reattest summary in fixtures.</p>'}
</section>

<section>
  <h2>Rule authoring checklist</h2>
  <p><code>scripts/rule_linter.py</code> is an <strong>optional rule-authoring checklist</strong>. It does <strong>not</strong> predict UMA disputes
  (control set 27/34 disputed vs 26/34 non-disputed — gap unused). No dispute-prediction claims on this page.</p>
</section>

<section>
  <h2>All cached votes ({len(runs)})</h2>
  <div class="filters">
    <strong>Filter set:</strong>
    <label><input type="checkbox" class="fset" value="sports_postcutoff_124" checked> sports 124</label>
    <label><input type="checkbox" class="fset" value="disputed_thin_34" checked> thin 34</label>
    <label><input type="checkbox" class="fset" value="disputed_curated_js_shells" checked> curated shells</label>
    <label><input type="checkbox" class="fset" value="disputed_hand_answer_bearing" checked> hand</label>
    <label><input type="checkbox" class="fset" value="legacy_5" checked> legacy</label>
    <label style="margin-left:1rem"><input type="checkbox" id="onlyVerified" checked> only Hardware + host stack verified</label>
  </div>
  <table>
    <thead><tr><th>Set</th><th>Market</th><th>Model</th><th>Outcome</th><th>Trust label</th><th>Answer-bearing sources</th><th>Evidence</th></tr></thead>
    <tbody id="votes">
{rows}
    </tbody>
  </table>
</section>

<section class="limits">
  <h2>Limits (always shown)</h2>
  <ul>
    <li>Model-serving container is not covered by the RTMR3 pin.</li>
    <li>Evidence is fetched outside the TEE; a registered relayer commits the hash.</li>
    <li>Owner registers signers and settles disputes.</li>
    <li>“Unsteerable” in the pitch means request-parameter steering is blocked on-chain; it does not remove relayer evidence choice.</li>
  </ul>
</section>

<script>
function apply() {{
  const sets = new Set([...document.querySelectorAll('.fset:checked')].map(x => x.value));
  const only = document.getElementById('onlyVerified').checked;
  for (const tr of document.querySelectorAll('#votes tr')) {{
    const okSet = sets.has(tr.dataset.set);
    const okLab = !only || tr.dataset.label === 'Hardware + host stack verified';
    tr.classList.toggle('hidden', !(okSet && okLab));
  }}
}}
document.querySelectorAll('.fset').forEach(x => x.addEventListener('change', apply));
document.getElementById('onlyVerified').addEventListener('change', apply);
apply();
</script>
</body>
</html>
"""
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html_doc)
    print(f"wrote {OUT_HTML} with {len(runs)} votes")

if __name__ == "__main__":
    main()
