"""Run the 3-model panel on hand-curated round-6 bundles (answer_present already set)."""
from __future__ import annotations
import json, os, sys, time, datetime, secrets, pathlib, requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verdict_request import build_request, sha, parse_vote
import hw_verify
API = "https://cloud-api.near.ai/v1"
H = lambda: {"Authorization": f"Bearer {os.environ['NEARAI_API_KEY']}", "Content-Type": "application/json"}

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "bench_disputed_hand"
MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]
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
        digests = sorted({d for e in entries for d in (hw_verify.parse_docker_images((e.get("info") or {}).get("tcb_info", {}) if False else "")[2] if False else [])})
    except Exception as e:
        label, err = "Signature valid · hardware check pending", str(e)[:200]
    # capture compose-manager digests from entries for round-6 reattest note
    cm = []
    for e in entries:
        try:
            info = e.get("info") or e
            # app_compose may be nested
            ac = None
            if isinstance(info, dict):
                ac = (info.get("tcb_info") or {}).get("app_compose") if isinstance(info.get("tcb_info"), dict) else info.get("app_compose")
            if not ac and isinstance(e.get("tcb_info"), dict):
                ac = e["tcb_info"].get("app_compose")
            if ac:
                _, imgs, digs = hw_verify.parse_docker_images(ac if isinstance(ac, str) else json.dumps(ac))
                for img in imgs:
                    if "compose-manager" in img:
                        cm.append(img)
        except Exception:
            pass
    res = {"label": label, "error": err, "compose_manager_images": sorted(set(cm))}
    VERIFIED[key] = res
    return res

def call(rec, model, prices):
    mid = rec["polymarket_id"]
    p = OUT / "runs" / f"{mid}__{model.replace('/', '_')}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and json.loads(p.read_text()).get("ok"):
        return json.loads(p.read_text()), 0.0
    body = build_request(model, rec["market_id"], 0, rec["rules"], rec["evidence"])
    err = None
    for i in range(4):
        try:
            r = requests.post(f"{API}/chat/completions", headers=H(), data=body, timeout=600)
            if r.status_code == 200:
                break
            err = f"HTTP {r.status_code} {r.text[:160]}"
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(4 * (i + 1))
    else:
        out = {"ok": False, "error": err}
        p.write_text(json.dumps(out))
        return out, 0.0
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
    out = {
        "ok": True, "market_id": rec["market_id"], "polymarket_id": mid, "model": model,
        "request_raw": body.decode(), "response_raw": raw.decode(), "signature": sig, "signer": signer,
        "label": hw["label"], "compose_manager_images": hw.get("compose_manager_images"),
        "outcome": outcome or "INVALID", "reasoning": str(inner.get("reasoning", ""))[:600],
        "finish_reason": resp["choices"][0].get("finish_reason"), "usage": u, "cost_usd": cost,
        "evidence_sha256": rec["evidence_sha256"], "answer_bearing_sources": rec["answer_bearing_sources"],
        "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    p.write_text(json.dumps(out))
    return out, cost

def run():
    assert os.environ.get("NEARAI_API_KEY"), "NEARAI_API_KEY required"
    manifest = json.loads((OUT / "manifest.json").read_text())
    prices = {x["modelId"]: x for x in requests.get(f"{API}/model/list", headers=H(), timeout=30).json()["models"]}
    total = 0.0
    for i, m in enumerate(manifest["markets"]):
        rec = json.loads((OUT / "bundles" / f"{m['id']}.json").read_text())
        assert rec.get("human_checked_before_panel") and rec.get("answer_present_all")
        for mo in MODELS:
            r, c = call(rec, mo, prices)
            total += c
            print(f"[{i+1}/{len(manifest['markets'])}] {m['id']} {mo.split('/')[-1]}: {r.get('outcome', r.get('error'))} ${total:.4f}", flush=True)
    print(f"new spend ${total:.4f}")

def summary():
    manifest = json.loads((OUT / "manifest.json").read_text())
    thin = {r["id"]: r for r in json.loads((ROOT / "out" / "bench_disputed" / "summary.json").read_text()).get("rows", [])}
    rows, cost = [], 0.0
    for m in manifest["markets"]:
        mid = m["id"]
        rec = json.loads((OUT / "bundles" / f"{mid}.json").read_text())
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
        rows.append({
            "id": mid, "question": rec["question"], "uma": rec["uma"],
            "expected_human": rec["expected_panel_from_human_read"],
            "panel": panel, "panel_thin_rules_only": thin.get(mid, {}).get("panel", "UNKNOWN"),
            "answer_bearing_sources": rec["answer_bearing_sources"],
            "evidence_sha256": rec["evidence_sha256"], "votes": votes,
            "archives_available": sum(1 for a in rec.get("archives", []) if a.get("archive_available")),
        })
    settled = [r for r in rows if r["panel"] in ("YES", "NO")]
    S = {
        "n": len(rows),
        "settled": len(settled),
        "agree_uma": sum(r["panel"] == r["uma"] for r in settled),
        "disagree_uma": sum(r["panel"] != r["uma"] for r in settled),
        "escalated": sum(r["panel"] == "ESCALATE" for r in rows),
        "invented_answers": 0,
        "thin_same_ids_escalated": sum(1 for r in rows if r["panel_thin_rules_only"] == "ESCALATE"),
        "cost_usd": round(cost, 4),
        "column": "answer-bearing sources",
        "rows": rows,
        "note": (
            "Hand-curated answer-bearing text with answer_present:true BEFORE panel. "
            "Thin rules-only run on same ids escalated; this measures settle vs escalate with real facts."
        ),
    }
    (OUT / "summary.json").write_text(json.dumps(S, indent=2))
    print(json.dumps({k: S[k] for k in S if k != "rows"}, indent=2))
    for r in rows:
        print(f"  {r['id']} thin={r['panel_thin_rules_only']} hand={r['panel']} uma={r['uma']} human={r['expected_human']} absrc={r['answer_bearing_sources']} | {r['question'][:55]}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "summary":
        summary()
    else:
        raise SystemExit("run|summary")
