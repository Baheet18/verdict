"""Re-attest all 3 panel models today. If compose-manager a0c07308 appears, record and STOP before pin."""
from __future__ import annotations
import os, json, secrets, datetime, pathlib, requests, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hw_verify
API = "https://cloud-api.near.ai/v1"
H = lambda: {"Authorization": f"Bearer {__import__("os").environ["NEARAI_API_KEY"]}", "Content-Type": "application/json"}

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "reattest"
OUT.mkdir(parents=True, exist_ok=True)
MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]
TARGET = "a0c07308"
NOW = datetime.datetime.now().astimezone().isoformat(timespec="seconds")

allow = hw_verify.load_allowlist()
results = {"at": NOW, "models": {}, "saw_a0c07308": False, "pin_blocked_pending_human_approval": False}

for model in MODELS:
    nonce = secrets.token_hex(32)
    att = requests.get(f"{API}/attestation/report", headers=H(),
                       params={"model": model, "signing_algo": "ecdsa", "nonce": nonce}, timeout=90).json()
    entries = att.get("model_attestations") or []
    model_res = {"nonce": nonce, "n_entries": len(entries), "entries": [], "compose_manager_images": [], "labels_ok": True}
    for e in entries:
        addr = (e.get("signing_address") or "").lower()
        row = {"signing_address": addr, "model_name": e.get("model_name"), "ok": False, "error": None, "images": [], "digests": []}
        try:
            # Find app_compose
            info = e.get("info") or {}
            tcb = info.get("tcb_info") if isinstance(info, dict) else None
            if isinstance(tcb, str):
                tcb = json.loads(tcb)
            ac = None
            if isinstance(tcb, dict):
                ac = tcb.get("app_compose")
            if not ac and isinstance(info, dict):
                ac = info.get("app_compose")
            if ac:
                name, imgs, digs = hw_verify.parse_docker_images(ac if isinstance(ac, str) else json.dumps(ac))
                row["images"] = imgs
                row["digests"] = digs
                row["compose_name"] = name
                for img in imgs:
                    if "compose-manager" in img:
                        model_res["compose_manager_images"].append(img)
                        if TARGET in img:
                            results["saw_a0c07308"] = True
            # Full verify against allowlist for this entry if address present
            if addr and e.get("model_name") == model:
                # verify_node path via replicas helper needs all entries for the key — do per-entry carefully
                pass
            row["ok"] = True
        except Exception as ex:
            row["error"] = str(ex)[:300]
            model_res["labels_ok"] = False
        model_res["entries"].append(row)
    # Replica-wide verify for each unique signing address
    addrs = sorted({(e.get("signing_address") or "").lower() for e in entries if e.get("signing_address")})
    model_res["signer_checks"] = []
    for addr in addrs:
        try:
            rep = hw_verify.verify_signer_replicas(entries, addr, model, nonce)
            model_res["signer_checks"].append({"signer": addr, "label": rep["label"], "failures": rep.get("failures")})
            if "verified" not in rep["label"].lower() and "Hardware" not in rep["label"]:
                model_res["labels_ok"] = False
        except Exception as ex:
            model_res["signer_checks"].append({"signer": addr, "error": str(ex)[:300]})
            model_res["labels_ok"] = False
    model_res["compose_manager_images"] = sorted(set(model_res["compose_manager_images"]))
    results["models"][model] = model_res
    print(model, "entries", len(entries), "cm", model_res["compose_manager_images"], "checks", model_res["signer_checks"])

if results["saw_a0c07308"]:
    results["pin_blocked_pending_human_approval"] = True
    # Still run provenance check for the human, but DO NOT pin.
    try:
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_new_digest.py")], capture_output=True, text=True, timeout=120)
        results["check_new_digest_stdout"] = (r.stdout or "")[-4000:]
        results["check_new_digest_stderr"] = (r.stderr or "")[-1000:]
        results["check_new_digest_code"] = r.returncode
    except Exception as e:
        results["check_new_digest_error"] = str(e)
    print("STOP: saw compose-manager a0c07308 — not pinning without human approval")

(OUT / "reattest.json").write_text(json.dumps(results, indent=2))
print(json.dumps({k: results[k] for k in results if k != "models"}, indent=2))
for m, v in results["models"].items():
    print(m, "cm=", v["compose_manager_images"], "okish=", v["labels_ok"])
