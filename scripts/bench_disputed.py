#!/usr/bin/env python3
"""UMA-DISPUTED market benchmark (round 4).

Selects markets whose Gamma `umaResolutionStatuses` history contains "disputed",
that are now `umaResolutionStatus=resolved` with binary prices, and whose endDate
is AFTER the panel cutoff upper bound (2026-10-17). Does NOT invent disputes.

  python scripts/bench_disputed.py select
  python scripts/bench_disputed.py evidence
  python scripts/bench_disputed.py lint
  python scripts/bench_disputed.py run [limit]
  python scripts/bench_disputed.py summary

Evidence is fetched OUTSIDE the TEE. For non-sports disputed markets we snapshot:
  1) the market's own resolution rules/description
  2) resolutionSource URL body if present
  3) a Wikipedia extract when a simple title can be derived
Many will honestly escalate under AMBIGUOUS / insufficient evidence.
"""
from __future__ import annotations
import os, sys, json, re, time, datetime, pathlib, secrets, hashlib, collections
import requests
sys.path.insert(0, os.path.dirname(__file__))
import hw_verify, rule_linter
from verdict_request import build_request, evidence_bundle, parse_vote, sha
from eth_account import Account
from eth_account.messages import encode_defunct

OUT = pathlib.Path("out/bench_disputed")
for sub in ("runs", "evidence", "http"): (OUT / sub).mkdir(parents=True, exist_ok=True)
API = "https://cloud-api.near.ai/v1"
MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]
CUTOFF = "2026-10-17"
UA = {"User-Agent": "VerdictBench/0.3 (research; github.com/Baheet18/verdict)"}
H = lambda: {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}

def get_text(url, max_chars=12000):
    key = sha(url)[:24]
    p = OUT / "http" / (key + ".txt")
    if p.exists():
        return p.read_text()
    for i in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=45)
            if r.status_code == 200:
                t = r.text[:max_chars]
                p.write_text(t)
                return t
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return ""

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

def select(limit=40):
    src = pathlib.Path("out/bench2/pm_closed.json")
    rows = json.load(open(src))
    picks = []
    for m in rows:
        hist = str(m.get("umaResolutionStatuses") or "")
        if "disputed" not in hist:
            continue
        if m.get("umaResolutionStatus") != "resolved":
            continue
        if m.get("outcomePrices") not in ('["1", "0"]', '["0", "1"]'):
            continue
        ed = m.get("endDate") or ""
        try:
            d = datetime.datetime.fromisoformat(ed.replace("Z", "+00:00").replace(" ", "T").split("+")[0] + "+00:00").date()
        except Exception:
            continue
        if str(d) <= CUTOFF:
            continue
        picks.append({
            "id": m["id"], "question": m["question"], "description": m.get("description") or "",
            "outcomes": m.get("outcomes"), "outcomePrices": m["outcomePrices"],
            "endDate": m["endDate"], "closedTime": m.get("closedTime"),
            "umaResolutionStatus": m["umaResolutionStatus"],
            "umaResolutionStatuses": m.get("umaResolutionStatuses"),
            "resolutionSource": m.get("resolutionSource") or "",
            "volumeNum": m.get("volumeNum") or 0,
            "event_day": str(d),
            "classification": "uma_disputed_history",
            "classification_note": "Gamma umaResolutionStatuses contains 'disputed'; final status resolved; endDate after 2026-10-17.",
        })
    picks.sort(key=lambda x: -x["volumeNum"])
    picks = picks[: int(limit)]
    (OUT / "markets.json").write_text(json.dumps(picks, indent=2))
    print(f"selected {len(picks)} UMA-disputed resolved binary markets endDate>{CUTOFF}")
    for p in picks:
        print(f"  {p['id']} vol={round(p['volumeNum'])} {p['question'][:80]}")

def guess_wiki_title(question):
    q = re.sub(r"[?\"]", "", question)
    # Strip leading Will/Did
    q = re.sub(r"^(Will|Did|Does|Has|Have|Is|Are)\s+", "", q, flags=re.I)
    # Take left of ' by ' / ' on ' / ' in 20'
    q = re.split(r"\s+by\s+|\s+on\s+\d|\s+in\s+20", q, maxsplit=1)[0].strip()
    if 3 <= len(q) <= 80:
        return q
    return None

def build_evidence_for(m):
    snaps = []
    # Always snapshot the rules text itself as source 0 (integrity of what panel saw)
    rules = m["description"] or m["question"]
    snaps.append({
        "url": f"polymarket:market:{m['id']}:description",
        "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
        "sha256": sha(rules),
        "text": rules[:12000],
        "note": "Market rules/description from Gamma; not an external fact source.",
    })
    src = (m.get("resolutionSource") or "").strip()
    if src.startswith("http"):
        body = get_text(src)
        if body:
            snaps.append({"url": src, "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
                          "sha256": sha(body), "text": body[:12000], "note": "resolutionSource URL body"})
    title = guess_wiki_title(m["question"])
    if title:
        try:
            got = wiki_extract(title)
            if got:
                url, text = got
                snaps.append({"url": url, "fetched_at": datetime.datetime.utcnow().isoformat() + "Z",
                              "sha256": sha(text), "text": text, "note": f"wikipedia extract title={title!r}"})
        except Exception as e:
            print("wiki fail", m["id"], e)
    # Mark independence: rules snapshot does NOT count as an independent fact source
    fact_sources = [s for s in snaps if not s["url"].startswith("polymarket:")]
    ev = evidence_bundle(m["question"], rules, m["endDate"], snaps)
    rec = {
        "market_id": int(m["id"]) if str(m["id"]).isdigit() else abs(hash(m["id"])) % (10**9),
        "polymarket_id": m["id"],
        "rules": rules,
        "evidence": ev,
        "evidence_sha256": sha(ev),
        "snapshots": snaps,
        "independent_fact_sources": len(fact_sources),
        "sources_found": len(fact_sources),
        "note": "Rules snapshot always included; independent_fact_sources excludes it. Sports-style 2-source rule applies only when facts are statistical.",
    }
    (OUT / "evidence" / f"{m['id']}.json").write_text(json.dumps(rec, indent=1))
    return rec

def evidence():
    ms = json.load(open(OUT / "markets.json"))
    for m in ms:
        e = build_evidence_for(m)
        print(m["id"], "fact_sources", e["independent_fact_sources"], "snaps", len(e["snapshots"]))

def lint():
    ms = json.load(open(OUT / "markets.json"))
    rows = [{"id": m["id"], "question": m["question"], "rules": m.get("description") or ""} for m in ms]
    tmp = OUT / "lint_input.json"
    tmp.write_text(json.dumps(rows))
    # reuse CLI
    import subprocess
    subprocess.check_call([sys.executable, "scripts/rule_linter.py", "--file", str(tmp), "--out", str(OUT / "lint.json")])

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

def call(m, model, prices):
    p = OUT / "runs" / f"{m['id']}__{model.replace('/', '_')}.json"
    if p.exists() and json.loads(p.read_text()).get("ok"):
        return json.loads(p.read_text()), 0.0
    ev = json.loads((OUT / "evidence" / f"{m['id']}.json").read_text())
    body = build_request(model, ev["market_id"], 0, ev["rules"], ev["evidence"], max_tokens=6000)
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
        rec = {"ok": False, "error": err}
        p.write_text(json.dumps(rec))
        return rec, 0.0
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
        signer = Account.recover_message(encode_defunct(text=text), signature=sig["signature"]).lower()
        if signer != sig["signing_address"].lower():
            signer = None
    hw = verify_signer(model, signer) if signer else {"label": "UNVERIFIED (signature missing/invalid)"}
    outcome, inner = parse_vote(raw)
    rec = {"ok": True, "market_id": ev["market_id"], "polymarket_id": m["id"], "model": model,
           "request_raw": body.decode(), "response_raw": raw.decode(), "signature": sig, "signer": signer,
           "label": hw["label"], "outcome": outcome or "INVALID", "reasoning": str(inner.get("reasoning", ""))[:500],
           "finish_reason": resp["choices"][0].get("finish_reason"), "usage": u, "cost_usd": cost,
           "at": datetime.datetime.now().isoformat(timespec="seconds")}
    p.write_text(json.dumps(rec))
    return rec, cost

def run(limit=None):
    ms = json.load(open(OUT / "markets.json"))
    if limit:
        ms = ms[: int(limit)]
    prices = {x["modelId"]: x for x in requests.get(f"{API}/model/list", headers=H(), timeout=30).json()["models"]}
    total = 0.0
    for i, m in enumerate(ms):
        for mo in MODELS:
            rec, c = call(m, mo, prices)
            total += c
            print(f"[{i+1}/{len(ms)}] {m['id']} {mo.split('/')[-1]}: {rec.get('outcome', rec.get('error'))} ${total:.4f}", flush=True)
    print(f"new spend ${total:.4f}")

def summary():
    ms = json.load(open(OUT / "markets.json"))
    lint = json.loads((OUT / "lint.json").read_text()) if (OUT / "lint.json").exists() else None
    rows, cost = [], 0.0
    for m in ms:
        uma = "YES" if json.loads(m["outcomePrices"])[0] == "1" else "NO"
        evp = OUT / "evidence" / f"{m['id']}.json"
        ev = json.loads(evp.read_text()) if evp.exists() else {"independent_fact_sources": 0}
        votes = {}
        for mo in MODELS:
            p = OUT / "runs" / f"{m['id']}__{mo.replace('/', '_')}.json"
            r = json.loads(p.read_text()) if p.exists() else {"ok": False}
            if r.get("ok"):
                cost += r.get("cost_usd") or 0
            votes[mo] = {"outcome": r.get("outcome") if r.get("ok") else "NOT_RUN", "label": r.get("label"),
                         "reasoning": r.get("reasoning", "")}
        outs = {v["outcome"] for v in votes.values()}
        panel = next(iter(outs)) if len(outs) == 1 and next(iter(outs)) in ("YES", "NO") else "ESCALATE"
        rows.append({"id": m["id"], "question": m["question"], "event_day": m["event_day"], "uma": uma, "panel": panel,
                     "fact_sources": ev.get("independent_fact_sources", 0), "votes": votes,
                     "umaResolutionStatuses": m.get("umaResolutionStatuses")})
    settled = [r for r in rows if r["panel"] != "ESCALATE"]
    S = {
        "classification": "Gamma umaResolutionStatuses contains 'disputed'; final status resolved; endDate > 2026-10-17",
        "markets": len(rows),
        "panel_settled": len(settled),
        "panel_settled_agree_uma": sum(r["panel"] == r["uma"] for r in settled),
        "panel_settled_disagree_uma": sum(r["panel"] != r["uma"] for r in settled),
        "escalated": len(rows) - len(settled),
        "cost_usd": round(cost, 4),
        "linter": {"flagged": lint["flagged"], "markets": lint["markets"]} if lint else None,
        "rows": rows,
    }
    (OUT / "summary.json").write_text(json.dumps(S, indent=2))
    print(json.dumps({k: S[k] for k in S if k != "rows"}, indent=2))
    print("disagreements:", [(r["id"], r["uma"], r["panel"], r["question"][:60]) for r in rows if r["panel"] not in ("ESCALATE", r["uma"])])
    print("escalations:", S["escalated"])

if __name__ == "__main__":
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "select":
        select(int(args[0]) if args else 40)
    elif cmd == "evidence":
        evidence()
    elif cmd == "lint":
        lint()
    elif cmd == "run":
        run(args[0] if args else None)
    elif cmd == "summary":
        summary()
    else:
        raise SystemExit("select|evidence|lint|run|summary")
