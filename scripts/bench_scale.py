#!/usr/bin/env python3
"""Scaled Verdict benchmark: 100+ real, UMA-resolved Polymarket markets, weighted to events AFTER every panel model's
training-cutoff upper bound (2026-10-17, the latest panel release date; see docs/CUTOFFS.md).

  python scripts/bench_scale.py select    # pick markets from out/bench2/pm_closed.json (scripts/pm_fetch.py)
  python scripts/bench_scale.py evidence  # fetch >=2 independent sources per market (cached; resumable)
  python scripts/bench_scale.py run       # 3 models x N markets on NEAR AI (cached per market x model; resumable)
  python scripts/bench_scale.py summary   # out/bench2/summary.json + summary.md

Every request is the canonical Verdict request (scripts/verdict_request.py): temperature 0, fixed seed, header binding
market_id/round/rules_hash/prompt_template_hash/evidence_bundle_hash. Sources are fetched OUTSIDE the TEE by this relayer.
UMA's resolution is the reference label, not ground truth by definition.
"""
import os, sys, json, re, csv, io, time, random, hashlib, secrets, datetime, pathlib, unicodedata, threading, collections
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, os.path.dirname(__file__))
import hw_verify
from verdict_request import build_request, evidence_bundle, parse_vote, sha, PROMPT_TEMPLATE_HASH
from eth_account import Account
from eth_account.messages import encode_defunct

OUT = pathlib.Path("out/bench2"); (OUT / "http").mkdir(parents=True, exist_ok=True)
(OUT / "evidence").mkdir(exist_ok=True); (OUT / "runs").mkdir(exist_ok=True)
API = "https://cloud-api.near.ai/v1"
MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]
CUTOFF_UPPER_BOUND = "2026-10-17"   # latest panel release date (DeepSeek-V4.1-Flash-1017); event day must be AFTER this
ET = datetime.timezone(datetime.timedelta(hours=-4))  # EDT (US DST ends 2026-11-01; all NFL/NHL picks we keep are checked +-1 day)
UA = {"User-Agent": "VerdictBench/0.2 (research; contact via github.com/Baheet18/verdict)"}
ESPN = {"nhl-2026": "hockey/nhl", "nfl-2026": "football/nfl", "dfb-pokal": "soccer/ger.dfb_pokal",
        "bundesliga-2025": "soccer/ger.1", "bundesliga-2": "soccer/ger.2"}
OLDB = {"dfb-pokal": "dfb", "bundesliga-2025": "bl1", "bundesliga-2": "bl2"}
ser = lambda m: (m.get("events") or [{}])[0].get("seriesSlug") or ""
evt = lambda m: (m.get("events") or [{}])[0]

# ---------------- cached HTTP ----------------
def get(url, as_text=False):
    p = OUT / "http" / (sha(url)[:24] + ".txt")
    if p.exists(): t = p.read_text()
    else:
        for i in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=60)
                if r.status_code == 200: break
            except requests.RequestException: pass
            time.sleep(2 * (i + 1))
        else: raise RuntimeError(f"fetch failed {url}")
        t = r.text; p.write_text(t)
    return t if as_text else json.loads(t)

# ---------------- selection ----------------
def kind(m):
    q = m["question"]
    if any(x in q for x in ("Exact Score", "halftime", "half", "1H ", "1Q ", "2H ", "Q Spread", "H Spread", "Team Total", "second")): return None
    if re.search(r"\b\dH\b|\b\dQ\b|NQ|NH", q): return None
    if "draw" in q: return "draw"
    if re.match(r"Will .* win on \d{4}-\d\d-\d\d\?", q): return "win"
    if "O/U" in q: return "ou"
    if q.startswith("Spread:"): return "spread"
    if " vs. " in q and ":" not in q: return "moneyline"
    return None

def event_day_et(m):
    t = datetime.datetime.fromisoformat((evt(m).get("startTime") or m.get("gameStartTime") or m["endDate"]).replace("Z", "+00:00").replace(" ", "T").split("+")[0] + "+00:00")
    return t.astimezone(ET).date()

def select():
    rows = json.load(open(OUT / "pm_closed.json"))
    ok = [m for m in rows if m.get("umaResolutionStatus") == "resolved" and m.get("outcomePrices") in ('["1", "0"]', '["0", "1"]') and ser(m) in ESPN]
    random.seed(1114)
    by = collections.defaultdict(list)
    for m in ok:
        k = kind(m)
        if not k or " vs. " not in evt(m).get("title", ""): continue
        if str(event_day_et(m)) <= CUTOFF_UPPER_BOUND: continue
        by[(ser(m), k)].append(m)
    quota = {("nhl-2026", "moneyline"): 45, ("nfl-2026", "moneyline"): 25, ("nfl-2026", "spread"): 12, ("nfl-2026", "ou"): 12}
    pick, games_used = [], collections.Counter()
    for (s, k), n in quota.items():
        c = sorted(by[(s, k)], key=lambda m: m["id"]); random.shuffle(c)
        for m in c:
            g = evt(m).get("slug")
            if games_used[(s, k, g)]: continue
            games_used[(s, k, g)] += 1; pick.append(m)
            if sum(1 for x in pick if (ser(x), kind(x)) == (s, k)) >= n: break
    soccer = [m for (s, k), ms in by.items() if s in OLDB and k in ("win", "draw", "ou", "moneyline") for m in ms]
    soccer.sort(key=lambda m: m["id"]); random.shuffle(soccer)
    per_game = collections.Counter()
    for m in soccer:
        g = evt(m).get("slug")
        if per_game[g] >= 2: continue
        per_game[g] += 1; pick.append(m)
        if sum(1 for x in pick if ser(x) in OLDB) >= 30: break
    keep = ["id", "question", "description", "outcomes", "outcomePrices", "endDate", "umaResolutionStatus", "volumeNum", "closedTime"]
    sel = [{**{k: m.get(k) for k in keep}, "series": ser(m), "kind": kind(m), "event_title": evt(m).get("title"),
            "event_slug": evt(m).get("slug"), "event_day_et": str(event_day_et(m))} for m in pick]
    (OUT / "markets.json").write_text(json.dumps(sel, indent=1))
    print(len(sel), collections.Counter((x["series"], x["kind"]) for x in sel))

# ---------------- sources ----------------
STOP = {"fc", "sv", "vfl", "vfb", "sc", "tsg", "sg", "fsv", "1", "1.", "bsc", "cf", "ac", "afc", "ssv", "tsv", "spvgg", "the", "de", "e.v.", "ev", "05", "04", "09", "1899", "1910", "98", "96", "07", "06"}
def toks(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", s) if t and t not in STOP}
def same_team(a, b):
    ta, tb = toks(a), toks(b)
    return bool(ta & tb) or (a.lower() in b.lower()) or (b.lower() in a.lower())
def teams_of(m): return [t.strip() for t in m["event_title"].split(" vs. ", 1)]

def match_pair(teams, names):
    return all(any(same_team(t, n) for n in names) for t in teams)

def espn(m, day):
    path = ESPN[m["series"]]; teams = teams_of(m)
    for d in (day, day - datetime.timedelta(days=1), day + datetime.timedelta(days=1)):
        url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={d:%Y%m%d}"
        for e in get(url).get("events", []):
            comp = e["competitions"][0]
            names = [x["team"].get(k, "") for x in comp["competitors"] for k in ("displayName", "shortDisplayName", "name")]
            if match_pair(teams, names):
                slim = {"name": e.get("name"), "date": e.get("date"), "status": comp["status"]["type"].get("description"),
                        "completed": comp["status"]["type"].get("completed"),
                        "competitors": [{"team": x["team"].get("displayName"), "abbreviation": x["team"].get("abbreviation"), "homeAway": x.get("homeAway"),
                                         "score": x.get("score"), "winner": x.get("winner")} for x in comp["competitors"]]}
                return url, "ESPN scoreboard (extracted game object): " + json.dumps(slim, ensure_ascii=False), slim
    return url, f"ESPN scoreboard: no game matching {teams} on {day} +-1 day.", None

def nhl_api(m, day):
    teams = teams_of(m)
    for d in (day, day - datetime.timedelta(days=1), day + datetime.timedelta(days=1)):
        url = f"https://api-web.nhle.com/v1/score/{d}"
        for g in get(url).get("games", []):
            names = [g[s]["name"]["default"] for s in ("awayTeam", "homeTeam")] + [g[s].get("abbrev", "") for s in ("awayTeam", "homeTeam")]
            if match_pair(teams, names):
                slim = {"gameDate": g.get("gameDate"), "gameState": g.get("gameState"), "gameOutcome": g.get("gameOutcome"),
                        "awayTeam": {"name": g["awayTeam"]["name"]["default"], "score": g["awayTeam"].get("score")},
                        "homeTeam": {"name": g["homeTeam"]["name"]["default"], "score": g["homeTeam"].get("score")}}
                return url, "NHL official API score feed (extracted game object; gameState OFF/FINAL = final): " + json.dumps(slim), slim
    return url, f"NHL API: no game matching {teams} on {day} +-1 day.", None

NFLV = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
ESPN2NFLV = {"WSH": "WAS", "LAR": "LA"}
def nflverse(m, day, espn_slim):
    txt = get(NFLV, as_text=True); rd = list(csv.DictReader(io.StringIO(txt)))
    if not espn_slim: return NFLV, "nflverse games.csv: could not identify teams (no ESPN match).", None
    ab = {ESPN2NFLV.get(x["abbreviation"], x["abbreviation"]) for x in espn_slim["competitors"]}
    for r in rd:
        if {r["away_team"], r["home_team"]} == ab and abs((datetime.date.fromisoformat(r["gameday"]) - day).days) <= 1:
            slim = {k: r[k] for k in ("game_id", "gameday", "away_team", "away_score", "home_team", "home_score", "result", "total", "overtime")}
            return NFLV, "nflverse/nfldata games.csv row (independent community dataset; empty score = not yet final): " + json.dumps(slim), slim
    return NFLV, f"nflverse games.csv: no row for {sorted(ab)} on {day} +-1 day.", None

def openliga(m, day):
    teams = teams_of(m); url = f"https://api.openligadb.de/getmatchdata/{OLDB[m['series']]}/2026"
    for g in get(url):
        d = datetime.date.fromisoformat(g["matchDateTime"][:10])
        if abs((d - day).days) <= 1 and match_pair(teams, [g["team1"]["teamName"], g["team2"]["teamName"], g["team1"].get("shortName", ""), g["team2"].get("shortName", "")]):
            slim = {"matchDateTime": g["matchDateTime"], "team1": g["team1"]["teamName"], "team2": g["team2"]["teamName"], "matchIsFinished": g["matchIsFinished"],
                    "matchResults": [{"name": r["resultName"], "team1": r["pointsTeam1"], "team2": r["pointsTeam2"]} for r in g.get("matchResults", [])]}
            return url, "OpenLigaDB (independent community-maintained German football results; extracted match): " + json.dumps(slim, ensure_ascii=False), slim
    return url, f"OpenLigaDB: no match for {teams} on {day} +-1 day.", None

def rules_text(m):
    o = json.loads(m["outcomes"])
    return m["description"].strip() + f"\n\nVERDICT MAPPING: answer YES if this market resolves to \"{o[0]}\"; answer NO if it resolves to \"{o[1]}\"."

def build_evidence(m):
    p = OUT / "evidence" / f"{m['id']}.json"
    if p.exists(): return json.loads(p.read_text())
    day = datetime.date.fromisoformat(m["event_day_et"]); now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    srcs = [espn(m, day)]
    if m["series"] == "nhl-2026": srcs.append(nhl_api(m, day))
    elif m["series"] == "nfl-2026": srcs.append(nflverse(m, day, srcs[0][2]))
    else: srcs.append(openliga(m, day))
    snaps = [{"url": u, "fetched_at": now, "sha256": sha(t), "text": t} for u, t, _ in srcs]
    ev = evidence_bundle(m["question"], rules_text(m), m["endDate"], snaps)
    rec = {"market_id": int(m["id"]), "rules": rules_text(m), "evidence": ev, "sources_found": sum(1 for *_, s in srcs if s)}
    p.write_text(json.dumps(rec)); return rec

def evidence():
    ms = json.load(open(OUT / "markets.json")); c = collections.Counter()
    for m in ms:
        try: c[build_evidence(m)["sources_found"]] += 1
        except Exception as e: print("evidence fail", m["id"], e); c["fail"] += 1
    print("sources found per market:", dict(c))

# ---------------- run ----------------
H = lambda: {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}
_att_lock = threading.Lock(); VERIFIED = {}  # (model, signer) -> {"label", "report_sha256", ...}

def verify_signer(model, signer):
    with _att_lock:
        if (model, signer) in VERIFIED: return VERIFIED[(model, signer)]
        res = {"label": "Signature valid · hardware check pending", "error": None}
        for attempt in range(3):
            nonce = secrets.token_hex(32)
            att = requests.get(f"{API}/attestation/report", headers=H(), params={"model": model, "signing_algo": "ecdsa", "nonce": nonce}, timeout=120).json()
            try:
                entry = hw_verify.match_attestation_for_signature(att.get("model_attestations"), signer, model)
                hw = hw_verify.verify_node(entry, nonce, model=model)
                res = {"label": hw["tdx"]["label"], "report_sha256": sha(json.dumps(att, sort_keys=True)), "verified_at": datetime.datetime.now().isoformat(timespec="seconds"),
                       "rt_mr0": None, "normalized_compose_hash": hw["tdx"]["normalized_compose_hash"], "rtmr3_events": hw["tdx"]["rtmr3_events"], "error": None}
                break
            except AssertionError as e: res["error"] = str(e)[:200]
        VERIFIED[(model, signer)] = res
        (OUT / "signers.json").write_text(json.dumps({f"{k[0]}|{k[1]}": v for k, v in VERIFIED.items()}, indent=1))
        return res

def call(m, model, prices):
    p = OUT / "runs" / f"{m['id']}__{model.replace('/', '_')}.json"
    if p.exists():
        r = json.loads(p.read_text())
        if r.get("ok"): return r, 0.0
    ev = build_evidence(m)
    body = build_request(model, ev["market_id"], 0, ev["rules"], ev["evidence"])
    err = None
    for i in range(4):
        try:
            r = requests.post(f"{API}/chat/completions", headers=H(), data=body, timeout=600)
            if r.status_code == 200: break
            err = f"HTTP {r.status_code} {r.text[:120]}"
        except requests.RequestException as e: err = type(e).__name__
        time.sleep(5 * (i + 1))
    else:
        rec = {"ok": False, "error": err}; p.write_text(json.dumps(rec)); return rec, 0.0
    raw = r.content; resp = r.json(); u = resp.get("usage", {}); pr = prices[model]
    cost = u.get("prompt_tokens", 0) * pr["inputCostPerToken"]["amount"] / 1e9 + u.get("completion_tokens", 0) * pr["outputCostPerToken"]["amount"] / 1e9
    sig = None
    for i in range(4):
        try:
            s = requests.get(f"{API}/signature/{resp['id']}", headers=H(), params={"model": model, "signing_algo": "ecdsa"}, timeout=120)
            if s.status_code == 200: sig = s.json(); break
        except requests.RequestException: pass
        time.sleep(3)
    text = f"{model}:{sha(body)}:{sha(raw)}"
    signer = None
    if sig and sig.get("signature") and sig.get("text") == text:
        signer = Account.recover_message(encode_defunct(text=text), signature=sig["signature"]).lower()
        if signer != sig["signing_address"].lower(): signer = None
    hw = verify_signer(model, signer) if signer else {"label": "UNVERIFIED (signature missing/invalid)"}
    outcome, inner = parse_vote(raw)
    rec = {"ok": True, "market_id": ev["market_id"], "model": model, "request_raw": body.decode(), "response_raw": raw.decode(),
           "signature": sig, "signer": signer, "label": hw["label"], "outcome": outcome or "INVALID", "reasoning": str(inner.get("reasoning", ""))[:400],
           "finish_reason": resp["choices"][0].get("finish_reason"), "usage": u, "cost_usd": cost, "at": datetime.datetime.now().isoformat(timespec="seconds")}
    p.write_text(json.dumps(rec)); return rec, cost

def run(limit=None, workers=6):
    ms = json.load(open(OUT / "markets.json"))[: int(limit) if limit else None]
    prices = {x["modelId"]: x for x in requests.get(f"{API}/model/list", headers=H(), timeout=30).json()["models"]}
    jobs = [(m, mo) for m in ms for mo in MODELS]
    todo = [(m, mo) for m, mo in jobs if not (lambda p: p.exists() and json.loads(p.read_text()).get("ok"))(OUT / "runs" / f"{m['id']}__{mo.replace('/', '_')}.json")]
    print(f"{len(jobs)} jobs, {len(jobs) - len(todo)} cached, {len(todo)} to run", flush=True)
    for m in ms: build_evidence(m)
    total, done = 0.0, 0
    with ThreadPoolExecutor(workers) as ex:
        fs = {ex.submit(call, m, mo, prices): (m, mo) for m, mo in todo}
        for f in as_completed(fs):
            m, mo = fs[f]; done += 1
            try: rec, c = f.result(); total += c
            except Exception as e: print("ERR", m["id"], mo, type(e).__name__, str(e)[:150], flush=True); continue
            print(f"[{done}/{len(todo)}] {m['id']} {mo.split('/')[-1]}: {rec.get('outcome', rec.get('error'))} ${total:.4f}", flush=True)
    print(f"new spend this run: ${total:.4f}")

# ---------------- summary ----------------
def summary():
    ms = json.load(open(OUT / "markets.json")); rows = []; cost = 0.0
    for m in ms:
        uma = "YES" if json.loads(m["outcomePrices"])[0] == "1" else "NO"
        ev = json.loads((OUT / "evidence" / f"{m['id']}.json").read_text())
        votes = {}
        for mo in MODELS:
            p = OUT / "runs" / f"{m['id']}__{mo.replace('/', '_')}.json"
            r = json.loads(p.read_text()) if p.exists() else {"ok": False}
            if r.get("ok"): cost += r["cost_usd"]
            votes[mo] = {"outcome": r.get("outcome") if r.get("ok") else "NOT_RUN", "label": r.get("label"), "signer": r.get("signer"), "reasoning": r.get("reasoning", "")}
        outs = {v["outcome"] for v in votes.values()}
        panel = next(iter(outs)) if len(outs) == 1 and next(iter(outs)) in ("YES", "NO") else "ESCALATE"
        rows.append({"id": m["id"], "question": m["question"], "series": m["series"], "kind": m["kind"], "event_day_et": m["event_day_et"],
                     "post_cutoff": m["event_day_et"] > CUTOFF_UPPER_BOUND, "sources_found": ev["sources_found"], "uma": uma, "panel": panel, "votes": votes})
    def stats(rs):
        n = len(rs); settled = [r for r in rs if r["panel"] != "ESCALATE"]
        return {"markets": n, "panel_settled": len(settled), "panel_settled_agree_uma": sum(r["panel"] == r["uma"] for r in settled),
                "panel_settled_disagree_uma": sum(r["panel"] != r["uma"] for r in settled), "escalated": n - len(settled),
                "per_model": {mo: {"agree": sum(r["votes"][mo]["outcome"] == r["uma"] for r in rs),
                                   "disagree": sum(r["votes"][mo]["outcome"] in ("YES", "NO") and r["votes"][mo]["outcome"] != r["uma"] for r in rs),
                                   "abstain_or_invalid": sum(r["votes"][mo]["outcome"] not in ("YES", "NO") for r in rs)} for mo in MODELS}}
    post = [r for r in rows if r["post_cutoff"]]
    labels = collections.Counter(v["label"] for r in rows for v in r["votes"].values())
    S = {"cutoff_upper_bound": CUTOFF_UPPER_BOUND, "post_cutoff": stats(post), "all": stats(rows),
         "by_series_kind": {f"{s}/{k}": stats([r for r in rows if (r["series"], r["kind"]) == (s, k)]) for s, k in sorted({(r["series"], r["kind"]) for r in rows})},
         "two_sources_found": sum(r["sources_found"] >= 2 for r in rows), "labels": dict(labels), "cost_usd": round(cost, 4),
         "disagreements": [r for r in rows if r["panel"] not in ("ESCALATE", r["uma"])],
         "escalations": [{k: r[k] for k in ("id", "question", "uma", "sources_found")} | {"votes": {mo: v["outcome"] for mo, v in r["votes"].items()}} for r in rows if r["panel"] == "ESCALATE"]}
    (OUT / "summary.json").write_text(json.dumps({"summary": S, "rows": rows}, indent=1))
    print(json.dumps({k: v for k, v in S.items() if k not in ("disagreements", "escalations", "by_series_kind")}, indent=1))
    print("by series/kind:", {k: (v["markets"], v["panel_settled"], v["panel_settled_agree_uma"]) for k, v in S["by_series_kind"].items()})
    print("disagreements:", [(r["id"], r["question"], r["uma"], r["panel"]) for r in S["disagreements"]])
    print("escalations:", len(S["escalations"]))

if __name__ == "__main__":
    {"select": select, "evidence": evidence, "run": run, "summary": summary}[sys.argv[1]](*sys.argv[2:])
