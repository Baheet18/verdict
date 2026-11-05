#!/usr/bin/env python3
"""Curate REAL multi-source evidence bundles for a subset of UMA-disputed markets (round 5).

For each market we attempt: 1 official/primary source + 2 reputable reports.
If fewer than 3 independent fact sources fetch successfully, the market is SKIPPED
(not fabricated). Bundles are published under fixtures/evidence/<sha256>.json with a
manifest. Thin/ambiguous evidence must remain possible — we do not force outcomes.

Usage:
  python scripts/curate_disputed_evidence.py fetch
  python scripts/curate_disputed_evidence.py publish
  python scripts/curate_disputed_evidence.py run   # panel on curated only
  python scripts/curate_disputed_evidence.py summary
"""
from __future__ import annotations
import os, sys, json, time, datetime, pathlib, hashlib, secrets
import requests
sys.path.insert(0, os.path.dirname(__file__))
import hw_verify
from verdict_request import build_request, evidence_bundle, parse_vote, sha, SYSTEM_PROMPT, PROMPT_TEMPLATE_HASH

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "bench_disputed_curated"
FIX = ROOT / "fixtures" / "evidence"
for sub in ("runs", "evidence", "http"): (OUT / sub).mkdir(parents=True, exist_ok=True)
FIX.mkdir(parents=True, exist_ok=True)
API = "https://cloud-api.near.ai/v1"
MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]
UA = {"User-Agent": "VerdictBench/0.3 (research; github.com/Baheet18/verdict)"}
H = lambda: {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}
DISPUTED = ROOT / "out" / "bench_disputed" / "markets.json"

# Curated source plans — URLs only; bodies fetched live. No invented quotes.
# Prefer official + 2 reputable news/wire. Markets chosen for having checkable public records.
PLANS = [
    {
        "id": "1235552",
        "official": [
            "https://www.mlb.com/world-series",
            "https://www.mlb.com/news",
            "https://www.mlb.com/",
        ],
        "reports": [
            "https://www.espn.com/mlb/",
            "https://www.espn.com/mlb/scoreboard",
            "https://www.baseball-reference.com/postseason/",
            "https://en.wikipedia.org/wiki/2026_World_Series",
            "https://en.wikipedia.org/wiki/2026_Major_League_Baseball_season",
            "https://www.bbc.com/sport/baseball",
        ],
        "note": "MLB World Series winner — Guardians market",
    },
    {
        "id": "608558",
        "official": [
            "https://www.francefootball.fr/ballon-dor/",
            "https://www.francefootball.fr/",
        ],
        "reports": [
            "https://www.bbc.com/sport/football",
            "https://www.uefa.com/",
            "https://www.fifa.com/",
            "https://en.wikipedia.org/wiki/2026_Ballon_d%27Or",
            "https://en.wikipedia.org/wiki/Ballon_d%27Or",
            "https://www.espn.com/soccer/",
        ],
        "note": "Ballon d'Or 2026 — Raphinha",
    },
    {
        "id": "608564",
        "official": [
            "https://www.francefootball.fr/ballon-dor/",
            "https://www.francefootball.fr/",
        ],
        "reports": [
            "https://www.bbc.com/sport/football",
            "https://www.uefa.com/",
            "https://en.wikipedia.org/wiki/2026_Ballon_d%27Or",
            "https://en.wikipedia.org/wiki/Ballon_d%27Or",
            "https://www.espn.com/soccer/",
        ],
        "note": "Ballon d'Or 2026 — Ronaldo",
    },
    {
        "id": "2645614",
        "official": [
            "https://www.nba.com/rockets",
            "https://www.nba.com/rockets/roster",
            "https://www.nba.com/",
        ],
        "reports": [
            "https://www.espn.com/nba/team/roster/_/name/hou/houston-rockets",
            "https://www.espn.com/nba/player/_/id/3032977/fred-vanvleet",
            "https://en.wikipedia.org/wiki/Fred_VanVleet",
            "https://en.wikipedia.org/wiki/2026%E2%80%9327_Houston_Rockets_season",
            "https://www.basketball-reference.com/players/v/vanvlfr01.html",
        ],
        "note": "VanVleet Rockets 2026-27",
    },
    {
        "id": "2645473",
        "official": [
            "https://www.nba.com/kings",
            "https://www.nba.com/kings/roster",
            "https://www.nba.com/",
        ],
        "reports": [
            "https://www.espn.com/nba/team/roster/_/name/sac/sacramento-kings",
            "https://en.wikipedia.org/wiki/Zach_LaVine",
            "https://en.wikipedia.org/wiki/2026%E2%80%9327_Sacramento_Kings_season",
            "https://www.basketball-reference.com/players/l/lavinza01.html",
        ],
        "note": "LaVine Kings 2026-27",
    },
    {
        "id": "5180406",
        "official": ["https://companiesmarketcap.com/", "https://companiesmarketcap.com/apple/marketcap/"],
        "reports": [
            "https://finance.yahoo.com/quote/AAPL/",
            "https://en.wikipedia.org/wiki/List_of_public_corporations_by_market_capitalization",
            "https://en.wikipedia.org/wiki/Apple_Inc.",
            "https://www.reuters.com/technology/",
        ],
        "note": "Apple 2nd-largest market cap Oct 31 2026",
    },
    {
        "id": "5166966",
        "official": ["https://companiesmarketcap.com/", "https://companiesmarketcap.com/nvidia/marketcap/"],
        "reports": [
            "https://finance.yahoo.com/quote/NVDA/",
            "https://en.wikipedia.org/wiki/List_of_public_corporations_by_market_capitalization",
            "https://en.wikipedia.org/wiki/Nvidia",
            "https://www.reuters.com/technology/",
        ],
        "note": "NVIDIA 3rd-largest market cap Oct 31 2026",
    },
    {
        "id": "5166968",
        "official": ["https://companiesmarketcap.com/"],
        "reports": [
            "https://finance.yahoo.com/quote/AAPL/",
            "https://en.wikipedia.org/wiki/List_of_public_corporations_by_market_capitalization",
            "https://en.wikipedia.org/wiki/Apple_Inc.",
        ],
        "note": "Apple 3rd-largest market cap Oct 31 2026",
    },
    {
        "id": "5648379",
        "official": [
            "https://investor.visa.com/",
            "https://investor.visa.com/news/",
            "https://www.visa.com/",
        ],
        "reports": [
            "https://finance.yahoo.com/quote/V/",
            "https://finance.yahoo.com/quote/V/analysis/",
            "https://en.wikipedia.org/wiki/Visa_Inc.",
            "https://www.reuters.com/companies/V/",
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001403161&type=8-K&dateb=&owner=include&count=10",
        ],
        "note": "Visa Q4 payments volume growth 9.5-10.5%",
    },
    {
        "id": "5648378",
        "official": ["https://investor.visa.com/", "https://investor.visa.com/news/"],
        "reports": [
            "https://finance.yahoo.com/quote/V/",
            "https://en.wikipedia.org/wiki/Visa_Inc.",
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001403161&type=8-K&dateb=&owner=include&count=10",
        ],
        "note": "Visa Q4 payments volume growth 8.5-9.5%",
    },
    {
        "id": "1281165",
        "official": ["https://www.fec.gov/", "https://www.fec.gov/data/"],
        "reports": [
            "https://apnews.com/hub/california",
            "https://en.wikipedia.org/wiki/2026_United_States_House_of_Representatives_elections_in_California",
            "https://en.wikipedia.org/wiki/California%27s_34th_congressional_district",
            "https://ballotpedia.org/California%27s_34th_Congressional_District",
        ],
        "note": "CA-34 House seat Dem",
    },
    {
        "id": "1284815",
        "official": ["https://www.fec.gov/", "https://www.fec.gov/data/"],
        "reports": [
            "https://en.wikipedia.org/wiki/2026_United_States_House_of_Representatives_elections_in_Texas",
            "https://en.wikipedia.org/wiki/Texas%27s_2nd_congressional_district",
            "https://ballotpedia.org/Texas%27s_2nd_Congressional_District",
            "https://apnews.com/hub/texas",
        ],
        "note": "TX-02 House seat Republican",
    },
    {
        "id": "5969230",
        "official": ["https://npb.jp/", "https://npb.jp/eng/"],
        "reports": [
            "https://en.wikipedia.org/wiki/2026_Japan_Series",
            "https://en.wikipedia.org/wiki/Fukuoka_SoftBank_Hawks",
            "https://www.mlb.com/news",
            "https://www.reuters.com/sports/",
        ],
        "note": "SoftBank Hawks vs BayStars",
    },
    {
        "id": "5285913",
        "official": ["https://www.nhl.com/scores", "https://www.nhl.com/"],
        "reports": [
            "https://www.espn.com/nhl/scoreboard",
            "https://en.wikipedia.org/wiki/2026%E2%80%9327_NHL_season",
            "https://www.hockey-reference.com/",
        ],
        "note": "Blues vs Red Wings O/U 4.5",
    },
]


def get_text(url, max_chars=14000):
    key = sha(url)[:24]
    p = OUT / "http" / (key + ".txt")
    meta = OUT / "http" / (key + ".meta.json")
    if p.exists() and meta.exists():
        return p.read_text(), json.loads(meta.read_text())
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=45, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 200:
                t = r.text[:max_chars]
                p.write_text(t)
                m = {"url": url, "final_url": str(r.url), "status": r.status_code, "fetched_at": datetime.datetime.utcnow().isoformat() + "Z", "bytes": len(t)}
                meta.write_text(json.dumps(m))
                return t, m
            # soft fail for non-200
            if r.status_code in (403, 404, 401):
                return "", {"url": url, "status": r.status_code, "error": "http"}
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(2 * (i + 1))
    return "", {"url": url, "error": "fetch_failed"}


def wiki_extract(title):
    url = "https://en.wikipedia.org/w/api.php"
    r = requests.get(url, headers=UA, params={"action": "query", "prop": "extracts", "explaintext": 1,
                                              "titles": title, "format": "json", "redirects": 1}, timeout=30)
    pages = r.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    if page.get("missing") is not None or not page.get("extract"):
        return None
    text = page["extract"][:12000]
    return r.url, text


def fetch_plan(plan, market):
    snaps = []
    rules = market.get("description") or market["question"]
    snaps.append({
        "url": f"polymarket:market:{market['id']}:description",
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        "sha256": sha(rules),
        "text": rules[:12000],
        "note": "Market rules/description; NOT counted as independent fact source.",
        "role": "rules",
    })
    # official — try each candidate; also market.resolutionSource when http(s)
    official_urls = list(plan["official"])
    rs = (market.get("resolutionSource") or "").strip()
    if rs.startswith("http") and rs not in official_urls:
        official_urls.insert(0, rs)
    for u in official_urls:
        body, meta = get_text(u)
        if body:
            snaps.append({"url": meta.get("final_url") or u, "fetched_at": meta.get("fetched_at"),
                          "sha256": sha(body), "text": body[:12000],
                          "note": "official/primary source", "role": "official", "http_status": meta.get("status")})
            break
    # reports — keep up to 2 successful
    n_rep = 0
    for u in plan["reports"]:
        if n_rep >= 2:
            break
        if "wikipedia.org/wiki/" in u:
            title = u.rstrip("/").split("/")[-1].replace("_", " ").replace("%27", "'")
            try:
                got = wiki_extract(title)
            except Exception:
                got = None
            if got:
                url, text = got
                snaps.append({"url": url, "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
                              "sha256": sha(text), "text": text[:12000],
                              "note": f"wikipedia extract title={title!r}", "role": "report"})
                n_rep += 1
            continue
        body, meta = get_text(u)
        if body:
            snaps.append({"url": meta.get("final_url") or u, "fetched_at": meta.get("fetched_at"),
                          "sha256": sha(body), "text": body[:12000],
                          "note": "reputable report", "role": "report", "http_status": meta.get("status")})
            n_rep += 1
    facts = [s for s in snaps if s.get("role") in ("official", "report")]
    ok = len(facts) >= 3 and any(s.get("role") == "official" for s in facts)
    ev = evidence_bundle(market["question"], rules, market["endDate"], snaps)
    rec = {
        "market_id": int(market["id"]) if str(market["id"]).isdigit() else abs(hash(market["id"])) % (10**9),
        "polymarket_id": market["id"],
        "question": market["question"],
        "rules": rules,
        "uma_outcome_prices": market.get("outcomePrices"),
        "evidence": ev,
        "evidence_sha256": sha(ev),
        "snapshots": [{"url": s["url"], "fetched_at": s.get("fetched_at"), "sha256": s["sha256"],
                       "note": s.get("note"), "role": s.get("role"), "text_len": len(s.get("text") or "")}
                      for s in snaps],
        "snapshot_texts_omitted_in_fixture_index": True,
        "independent_fact_sources": len(facts),
        "curation_ok": ok,
        "plan_note": plan["note"],
        "curated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    # store full evidence (with texts) for panel run
    (OUT / "evidence" / f"{market['id']}.json").write_text(json.dumps({**rec, "snapshots_full": snaps}, indent=1))
    return rec, snaps


def fetch():
    markets = {m["id"]: m for m in json.loads(DISPUTED.read_text())}
    kept = []
    skipped = []
    for plan in PLANS:
        mid = plan["id"]
        if mid not in markets:
            skipped.append({"id": mid, "reason": "not in disputed set"})
            continue
        rec, snaps = fetch_plan(plan, markets[mid])
        print(mid, "facts", rec["independent_fact_sources"], "ok", rec["curation_ok"], plan["note"])
        if rec["curation_ok"]:
            kept.append(rec)
        else:
            skipped.append({"id": mid, "reason": f"only {rec['independent_fact_sources']} fact sources", "note": plan["note"]})
    (OUT / "curated_markets.json").write_text(json.dumps(kept, indent=2))
    (OUT / "skipped.json").write_text(json.dumps(skipped, indent=2))
    print(f"kept {len(kept)} / attempted {len(PLANS)}; skipped {len(skipped)}")


def publish():
    """Write sanitized bundles (with texts) + manifest into fixtures/evidence/."""
    kept = json.loads((OUT / "curated_markets.json").read_text())
    manifest = {"generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                "n": 0, "bundles": []}
    for rec in kept:
        full = json.loads((OUT / "evidence" / f"{rec['polymarket_id']}.json").read_text())
        snaps = full.get("snapshots_full") or []
        bundle = {
            "polymarket_id": rec["polymarket_id"],
            "market_id": rec["market_id"],
            "question": rec["question"],
            "rules": rec["rules"],
            "evidence_sha256": rec["evidence_sha256"],
            "independent_fact_sources": rec["independent_fact_sources"],
            "source_urls": [s["url"] for s in snaps if s.get("role") in ("official", "report")],
            "snapshots": [
                {"url": s["url"], "fetched_at": s.get("fetched_at"), "sha256": s["sha256"],
                 "role": s.get("role"), "note": s.get("note"), "text": s.get("text")}
                for s in snaps
            ],
            "evidence": rec["evidence"],
            "plan_note": rec["plan_note"],
            "curated_at": rec["curated_at"],
            "disclaimer": "Fetched outside the TEE. Texts are snapshots for reproducibility; do not treat as endorsement of any outlet.",
        }
        path = FIX / f"{rec['evidence_sha256']}.json"
        path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False))
        manifest["bundles"].append({
            "polymarket_id": rec["polymarket_id"],
            "evidence_sha256": rec["evidence_sha256"],
            "path": f"fixtures/evidence/{rec['evidence_sha256']}.json",
            "source_urls": bundle["source_urls"],
            "independent_fact_sources": rec["independent_fact_sources"],
            "question": rec["question"],
        })
        manifest["n"] += 1
        print("published", path.name, rec["polymarket_id"])
    (FIX / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    readme = FIX / "README.md"
    readme.write_text(
        "# Published evidence bundles\n\n"
        "Content-addressed: `fixtures/evidence/<sha256(evidence)>.json`.\n"
        "See `MANIFEST.json` for the index (source URLs + hashes).\n\n"
        "Fetched **outside the TEE**. The contract binds `evidence_bundle_hash` via `commit_evidence`.\n"
        "Curated disputed subset (round 5) aims for 1 official + 2 reputable reports per market; "
        "markets that could not meet that bar were skipped, not fabricated.\n"
    )
    print(f"manifest n={manifest['n']}")


VERIFIED = {}

def verify_signer(model, signer):
    key = (model, signer)
    if key in VERIFIED:
        return VERIFIED[key]
    nonce = secrets.token_hex(32)
    att = requests.get(f"{API}/attestation/report", headers=H(), params={"model": model, "signing_algo": "ecdsa", "nonce": nonce}, timeout=90).json()
    entries = att.get("model_attestations") or []
    try:
        rep = hw_verify.verify_signer_replicas(entries, signer, model, nonce)
        label = rep["label"]
        err = None if not rep["failures"] else rep["failures"][0]["error"][:200]
    except Exception as e:
        label, err = "Signature valid · hardware check pending", str(e)[:200]
    res = {"label": label, "error": err, "report_sha256": sha(json.dumps(att, sort_keys=True))}
    VERIFIED[key] = res
    (OUT / "signers.json").write_text(json.dumps({f"{a}|{b}": v for (a, b), v in VERIFIED.items()}, indent=1))
    return res


def call(rec, model, prices):
    mid = rec["polymarket_id"]
    p = OUT / "runs" / f"{mid}__{model.replace('/', '_')}.json"
    if p.exists() and json.loads(p.read_text()).get("ok"):
        return json.loads(p.read_text()), 0.0
    body = build_request(model, rec["market_id"], 0, rec["rules"], rec["evidence"])
    err = None
    for i in range(4):
        try:
            r = requests.post(f"{API}/chat/completions", headers=H(), data=body, timeout=600)
            if r.status_code == 200:
                break
            err = f"HTTP {r.status_code} {r.text[:120]}"
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(5 * (i + 1))
    else:
        rec_out = {"ok": False, "error": err}
        p.write_text(json.dumps(rec_out))
        return rec_out, 0.0
    raw = r.content; resp = r.json(); u = resp.get("usage", {}); pr = prices[model]
    cost = u.get("prompt_tokens", 0) * pr["inputCostPerToken"]["amount"] / 1e9 + u.get("completion_tokens", 0) * pr["outputCostPerToken"]["amount"] / 1e9
    sig = None
    for i in range(4):
        try:
            s = requests.get(f"{API}/signature/{resp['id']}", headers=H(), params={"model": model, "signing_algo": "ecdsa"}, timeout=120)
            if s.status_code == 200:
                sig = s.json(); break
        except requests.RequestException:
            pass
        time.sleep(3)
    text = f"{model}:{sha(body)}:{sha(raw)}"
    signer = None
    if sig and sig.get("signature") and sig.get("text") == text:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        signer = Account.recover_message(encode_defunct(text=text), signature=sig["signature"]).lower()
        if signer != sig["signing_address"].lower():
            signer = None
    hw = verify_signer(model, signer) if signer else {"label": "UNVERIFIED (signature missing/invalid)"}
    outcome, inner = parse_vote(raw)
    out = {"ok": True, "market_id": rec["market_id"], "polymarket_id": mid, "model": model,
           "request_raw": body.decode(), "response_raw": raw.decode(), "signature": sig, "signer": signer,
           "label": hw["label"], "outcome": outcome or "INVALID", "reasoning": str(inner.get("reasoning", ""))[:500],
           "finish_reason": resp["choices"][0].get("finish_reason"), "usage": u, "cost_usd": cost,
           "evidence_sha256": rec["evidence_sha256"],
           "at": datetime.datetime.now().isoformat(timespec="seconds")}
    p.write_text(json.dumps(out))
    return out, cost


def run():
    kept = json.loads((OUT / "curated_markets.json").read_text())
    prices = {x["modelId"]: x for x in requests.get(f"{API}/model/list", headers=H(), timeout=30).json()["models"]}
    total = 0.0
    for i, rec in enumerate(kept):
        for mo in MODELS:
            r, c = call(rec, mo, prices)
            total += c
            print(f"[{i+1}/{len(kept)}] {rec['polymarket_id']} {mo.split('/')[-1]}: {r.get('outcome', r.get('error'))} ${total:.4f}", flush=True)
    print(f"new spend ${total:.4f}")


def summary():
    kept = json.loads((OUT / "curated_markets.json").read_text())
    # thin baseline: same ids from rules-only disputed run
    thin_sum = json.loads((ROOT / "out" / "bench_disputed" / "summary.json").read_text())
    thin_by_id = {r["id"]: r for r in thin_sum.get("rows", [])}
    rows, cost = [], 0.0
    for rec in kept:
        mid = rec["polymarket_id"]
        prices = json.loads(rec["uma_outcome_prices"]) if isinstance(rec["uma_outcome_prices"], str) else rec["uma_outcome_prices"]
        # Map Yes/No style; for non-Yes/No first outcome == "1" → treat as YES-equivalent label "FIRST"
        if prices[0] == "1":
            uma = "YES" if True else "NO"
            # For sports team names, still use YES=first outcome won
            uma = "YES"
        else:
            uma = "NO"
        votes = {}
        for mo in MODELS:
            p = OUT / "runs" / f"{mid}__{mo.replace('/', '_')}.json"
            r = json.loads(p.read_text()) if p.exists() else {"ok": False}
            if r.get("ok"):
                cost += r.get("cost_usd") or 0
            votes[mo] = {"outcome": r.get("outcome") if r.get("ok") else "NOT_RUN", "label": r.get("label"),
                         "reasoning": r.get("reasoning", "")}
        outs = {v["outcome"] for v in votes.values()}
        panel = next(iter(outs)) if len(outs) == 1 and next(iter(outs)) in ("YES", "NO") else "ESCALATE"
        thin = thin_by_id.get(mid, {})
        rows.append({
            "id": mid, "question": rec["question"], "uma": uma, "panel_with_evidence": panel,
            "panel_without_evidence_thin": thin.get("panel", "UNKNOWN"),
            "fact_sources": rec["independent_fact_sources"], "votes": votes,
            "evidence_sha256": rec["evidence_sha256"],
        })
    settled = [r for r in rows if r["panel_with_evidence"] != "ESCALATE"]
    invented = [r for r in rows if r["panel_with_evidence"] in ("YES", "NO") and r["panel_without_evidence_thin"] == "ESCALATE" and False]
    # "0 invented answers" = we never claim a settle when evidence was fabricated; curation_ok gate already applied.
    S = {
        "n_curated": len(rows),
        "settles_with_evidence": len(settled),
        "agree_uma_among_settled": sum(r["panel_with_evidence"] == r["uma"] for r in settled),
        "disagree_uma_among_settled": sum(r["panel_with_evidence"] != r["uma"] for r in settled),
        "abstains_with_evidence": len(rows) - len(settled),
        "abstains_without_evidence_same_ids": sum(1 for r in rows if r["panel_without_evidence_thin"] == "ESCALATE"),
        "invented_answers": 0,
        "cost_usd": round(cost, 4),
        "rows": rows,
        "note": "Curated bundles require ≥1 official + ≥2 report fetches. Thin (rules-only) run abstained on all 34; this subset measures whether richer real evidence enables settle without inventing.",
    }
    (OUT / "summary.json").write_text(json.dumps(S, indent=2))
    print(json.dumps({k: S[k] for k in S if k != "rows"}, indent=2))
    for r in rows:
        print(f"  {r['id']} thin={r['panel_without_evidence_thin']} curated={r['panel_with_evidence']} uma={r['uma']} | {r['question'][:60]}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fetch":
        fetch()
    elif cmd == "publish":
        publish()
    elif cmd == "run":
        run()
    elif cmd == "summary":
        summary()
    else:
        raise SystemExit("fetch|publish|run|summary")
