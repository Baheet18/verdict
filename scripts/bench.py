#!/usr/bin/env python3
"""Verdict 5-market benchmark vs UMA. Sources are fetched OUTSIDE the TEE, snapshotted, hashed and embedded
in the request; the request bytes are saved so anyone can recompute sha256(request) and check the TEE signature."""
import os, sys, json, hashlib, secrets, time, datetime, pathlib, requests
sys.path.insert(0, os.path.dirname(__file__)); import hw_verify
from eth_account import Account; from eth_account.messages import encode_defunct

API = "https://cloud-api.near.ai/v1"; H = {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}
UA = {"User-Agent": "VerdictBench/0.1 (hackathon research)"}
sha = lambda b: hashlib.sha256(b).hexdigest()
OUT = pathlib.Path("out/bench"); OUT.mkdir(parents=True, exist_ok=True)

# Training cutoffs: none of these vendors publish an exact cutoff; the release/snapshot date is an upper bound.
PANEL = {
  "Qwen/Qwen3.8-27B":                    {"family": "Qwen",     "cutoff": "unpublished; <= 2026 release"},
  "deepseek-ai/DeepSeek-V4.1-Flash-1017":{"family": "DeepSeek", "cutoff": "unpublished; snapshot 2026-10-17 is upper bound"},
  "z-ai/glm-5.3-flash":                {"family": "GLM",     "cutoff": "unpublished; <= 2026 release"},
}
CUTOFF_SPLIT = "2026-10-17"  # markets resolving after this are post-cutoff for every panel model

def wiki(title):
    r = requests.get("https://en.wikipedia.org/w/api.php", headers=UA, params={"action": "query", "prop": "extracts",
        "explaintext": 1, "titles": title, "format": "json", "redirects": 1}, timeout=30)
    return r.url, next(iter(r.json()["query"]["pages"].values()))["extract"][:12000]
def binance_lows(sym, start, end):
    r = requests.get("https://api.binance.com/api/v3/klines", params={"symbol": sym, "interval": "1d",
        "startTime": start, "endTime": end, "limit": 1000}, timeout=30)
    rows = [f"{datetime.datetime.utcfromtimestamp(k[0]/1000):%Y-%m-%d} low={k[3]} high={k[2]}" for k in r.json()]
    return r.url, "Binance BTCUSDT daily candles (UTC)\n" + "\n".join(rows)
def espn_player(event, name):
    r = requests.get("https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/summary", params={"event": event}, timeout=30)
    d = r.json(); lines = [f"Final: {[(c['team']['displayName'], c.get('score')) for c in d['header']['competitions'][0]['competitors']]}"]
    for ro in d.get("rosters", []):
        for p in ro.get("roster", []):
            if name in p["athlete"]["displayName"]:
                lines.append(f"{ro['team']['displayName']} {p['athlete']['displayName']}: " + ", ".join(f"{s['name']}={s.get('value')}" for s in p.get("stats", [])))
    return r.url, "ESPN match summary (unofficial; not UEFA official stats)\n" + "\n".join(lines)

MARKETS = [  # (polymarket id, disputed?, sources)
  ("515406", True,  [lambda: wiki("Benjamin Netanyahu")]),
  ("515427", True,  [lambda: wiki("Syria–United States relations")]),
  ("6692797", True, [lambda: espn_player("401915303", "Vanaken")]),
  ("515554", False, [lambda: binance_lows("BTCUSDT", 1735689600000, 1743465599000)]),
  ("514267", False, [lambda: wiki("Fall of the Assad regime")]),
]
SYS = ("You resolve prediction markets strictly from the SOURCE SNAPSHOTS provided, applying the market RULES literally. "
       "Do not use outside knowledge. Reply ONLY with JSON: {\"outcome\":\"YES|NO|TOO_EARLY|AMBIGUOUS\","
       "\"quotes\":[exact quotes from snapshots],\"reasoning\":\"<=2 sentences\",\"confidence\":0-1}. "
       "TOO_EARLY = event window not over per snapshots; AMBIGUOUS = snapshots insufficient or rules unclear.")

def retry(fn, n=4):
    import time
    for i in range(n):
        try:
            r = fn()
            if r.status_code < 500 and r.status_code != 429: return r
            print(f"  [retry] HTTP {r.status_code}")
        except requests.exceptions.RequestException as e: print(f"  [retry] {type(e).__name__}")
        if i == n - 1: raise RuntimeError("retries exhausted")
        time.sleep(10 * 2 ** i)

def attest(model):
    nonce = secrets.token_hex(32)
    att = requests.get(f"{API}/attestation/report", headers=H, params={"model": model, "signing_algo": "ecdsa", "nonce": nonce}, timeout=90).json()
    verified, all_addr = set(), set()
    for m in att.get("model_attestations") or []:
        if not m.get("signing_address"): print(f"  [hw] {model}: 3rd-party attestation format (keys={list(m)[:8]}) - hardware check pending"); continue
        all_addr.add(m["signing_address"].lower())
        try: hw_verify.verify_node(m, nonce); verified.add(m["signing_address"].lower())
        except Exception as e: print(f"  [hw] {model} node {m.get('signing_address')}: hardware check pending ({type(e).__name__}: {str(e)[:80]})")
    rep_hash = sha(json.dumps(att, sort_keys=True).encode())
    return {"verified": verified, "attested": all_addr, "report_sha256": rep_hash}

def run():
    att = {m: attest(m) for m in PANEL}
    for m, a in att.items(): print(f"[attest] {m}: hw-verified nodes={len(a['verified'])}/{len(a['attested'])} report_sha256={a['report_sha256'][:16]}")
    rows, cost = [], 0.0
    prices = {x["modelId"]: x for x in requests.get(f"{API}/model/list", headers=H, timeout=30).json()["models"]}
    for mid, disputed, srcs in MARKETS:
        pm = requests.get(f"https://gamma-api.polymarket.com/markets/{mid}", timeout=30).json()
        uma = "YES" if json.loads(pm["outcomePrices"])[0] == "1" else "NO"
        snaps = []
        for f in srcs:
            url, txt = f(); snaps.append({"url": url, "fetched_at": datetime.datetime.utcnow().isoformat() + "Z", "sha256": sha(txt.encode()), "text": txt})
        user = json.dumps({"question": pm["question"], "rules": pm["description"], "market_end": pm["endDate"],
                           "source_snapshots": [{k: s[k] for k in ("url", "fetched_at", "sha256", "text")} for s in snaps]})
        votes = []
        for model, meta in PANEL.items():
            body = json.dumps({"model": model, "temperature": 0, "max_tokens": 12000,
                               "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}]}).encode()
            r = retry(lambda: requests.post(f"{API}/chat/completions", headers=H, data=body, timeout=900)); raw = r.content; resp = r.json()
            content = (resp["choices"][0]["message"].get("content") or "").strip()
            try: v = json.loads(content[content.index("{"): content.rindex("}") + 1])
            except Exception: v = {"outcome": "AMBIGUOUS", "reasoning": "unparseable output"}
            u = resp.get("usage", {}); p = prices[model]
            c = u.get("prompt_tokens", 0) * p["inputCostPerToken"]["amount"] / 1e9 + u.get("completion_tokens", 0) * p["outputCostPerToken"]["amount"] / 1e9
            cost += c
            sig = retry(lambda: requests.get(f"{API}/signature/{resp['id']}", headers=H, params={"model": model, "signing_algo": "ecdsa"}, timeout=120)).json()
            text = f"{model}:{sha(body)}:{sha(raw)}"
            signer = Account.recover_message(encode_defunct(text=sig["text"]), signature=sig["signature"]).lower() if sig.get("signature") else None
            sig_ok = signer is not None and sig["text"] == text and signer == sig["signing_address"].lower() and signer in att[model]["attested"]
            label = "Verified" if sig_ok and signer in att[model]["verified"] else ("Signature valid · hardware check pending" if sig_ok else "UNVERIFIED")
            votes.append({"model": model, "outcome": str(v.get("outcome", "AMBIGUOUS")).upper(), "quotes": v.get("quotes", [])[:2],
                          "reasoning": v.get("reasoning", ""), "label": label, "signer": signer, "cost_usd": round(c, 5)})
            bundle = {"market_id": mid, "model": model, "request_raw": body.decode(), "request_sha256": sha(body),
                      "response_raw": raw.decode(), "response_sha256": sha(raw), "signature": sig, "attestation_report_sha256": att[model]["report_sha256"],
                      "label": label, "note": "Sources were fetched outside the TEE by the relayer; the snapshots and their hashes are inside the signed request."}
            (OUT / f"{mid}__{model.replace('/', '_')}.json").write_text(json.dumps(bundle, indent=1))
        outs = {x["outcome"] for x in votes}
        panel = votes[0]["outcome"] if len(outs) == 1 and votes[0]["outcome"] in ("YES", "NO") else "ESCALATE"
        rows.append({"id": mid, "question": pm["question"], "end": pm["endDate"][:10], "disputed": disputed,
                     "cutoff": "post" if pm["endDate"][:10] > CUTOFF_SPLIT else "pre", "uma": uma, "panel": panel, "votes": votes,
                     "snapshots": [{"url": s["url"], "sha256": s["sha256"]} for s in snaps]})
        print(f"\n#{mid} [{'DISPUTED' if disputed else 'clean'} · {rows[-1]['cutoff']}-cutoff] {pm['question']}\n  UMA={uma}  Verdict panel={panel}  {'AGREE' if panel == uma else 'DISAGREE'}")
        for x in votes: print(f"   - {x['model']}: {x['outcome']}  [{x['label']}]  ${x['cost_usd']}  {x['reasoning'][:140]}")
    (OUT / "results.json").write_text(json.dumps({"panel": PANEL, "cutoff_split": CUTOFF_SPLIT, "rows": rows, "cost_usd": cost}, indent=1))
    agree = [r for r in rows if r["panel"] == r["uma"]]
    print(f"\n== Agreed with UMA {len(agree)}/{len(rows)} ==")
    for k in ("pre", "post"):
        rs = [r for r in rows if r["cutoff"] == k]; print(f"   {k}-cutoff: {sum(r['panel']==r['uma'] for r in rs)}/{len(rs)}")
    print(f"   disputed: {sum(r['panel']==r['uma'] for r in rows if r['disputed'])}/{sum(r['disputed'] for r in rows)}")
    print("== Disagreements (evidence) ==")
    for r in rows:
        if r["panel"] != r["uma"]:
            print(f" #{r['id']} {r['question']}: UMA={r['uma']} panel={r['panel']}; sources={[s['url'][:80] for s in r['snapshots']]}")
            for x in r["votes"]: print(f"    {x['model']}: {x['outcome']} quotes={[q[:120] for q in x['quotes']]}")
    print(f"\nTotal cost ${cost:.4f} for {len(rows)} markets x {len(PANEL)} models = ${cost/len(rows):.4f}/market ({cost/len(rows)/10*100:.2f}% of $10 credit per market; ~{int(10/(cost/len(rows)))} markets per $10)")

if __name__ == "__main__": run()
