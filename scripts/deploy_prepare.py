#!/usr/bin/env python3
"""Print JSON-args for a minimal testnet flow from cached fixtures. Does NOT sign or deploy."""
from __future__ import annotations
import json, pathlib, hashlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from verdict_request import PROMPT_TEMPLATE_HASH, sha

panel = json.loads((ROOT / "contract" / "testdata" / "real_panel.json").read_text())
out_lines = []
def P(s=""): out_lines.append(s); print(s)

P("=== Verdict deploy_prepare (NO KEYS — human signs) ===")
P("Wasm (build locally): contract/target/near/verdict.wasm")
P("See deploy/TESTNET.md for near CLI steps.")
P("")
P("=== 1) register_signer (owner) — one per panel model ===")
for v in panel["votes"]:
    P(json.dumps({
        "address": v["signing_address"],
        "model": v["model"],
        "attestation_report_sha256": v.get("attestation_report_sha256") or ("0" * 64),
    }, indent=2))
    P("---")

P("=== 2) register_relayer (owner) — use the account that will commit_evidence + submit_verdict ===")
P('{"account": "<YOUR_TESTNET_ACCOUNT>"}')
P("(Owner can commit/submit without registering; registering a dedicated relayer is optional.)")
P("")

# Derive evidence hash from first vote's request user content
import json as _json
req0 = _json.loads(panel["votes"][0]["request_raw"])
user = req0["messages"][1]["content"]
header, evidence = user.split("\n", 1)
# parse evidence_bundle_hash from header
fields = dict(p.split("=", 1) for p in header.split(" ")[1:])
ev_h = fields["evidence_bundle_hash"]
rules = panel.get("rules") or ""
# Prefer panel question
question = panel.get("question") or "demo market"
P("=== 3) create_market args (owner or anyone) ===")
P(json.dumps({
    "question": question,
    "rules": rules,
    "sources": ["fixture://real_panel"],
    "prompt_template_hash": panel.get("prompt_template_hash") or PROMPT_TEMPLATE_HASH,
    "resolvers": [v["signing_address"] for v in panel["votes"]],
    "deadline_ns": "<now_ns + small delta; then wait>",
    "dispute_window_ns": 3_600_000_000_000,
    "dispute_bond": "100000000000000000000000",
}, indent=2))
P(f"prompt_template_hash (from fixture): {panel.get('prompt_template_hash')}")
P(f"evidence_bundle_hash (from fixture header): {ev_h}")
P("")
P("=== 4) commit_evidence (relayer/owner) AFTER deadline, BEFORE votes ===")
P(json.dumps({
    "market_id": 0,
    "round": 0,
    "evidence_bundle_hash": ev_h,
    "evidence_uri": f"fixture://evidence/{ev_h}",
}, indent=2))
P("")
P("=== 5) submit_verdict x3 (relayer/owner) — byte-exact request_raw/response_raw/signature ===")
for v in panel["votes"]:
    P(f"# model={v['model']} signer={v['signing_address']} outcome={v.get('parsed_outcome')}")
    P(json.dumps({
        "market_id": 0,
        "request_raw": v["request_raw"],
        "response_raw": v["response_raw"],
        "signature": v["signature"],
    })[:300] + " …(truncated; use full fields from contract/testdata/real_panel.json)")
    P("---")

P("")
P("=== NOTE ===")
P("No private keys in this repo. Sign deploy/init/calls with your own testnet keychain.")
P("After txs confirm, paste explorer links under README 'Testnet deploy'.")

(ROOT / "out" / "deploy_prepare").mkdir(parents=True, exist_ok=True)
(ROOT / "out" / "deploy_prepare" / "output.txt").write_text("\n".join(out_lines) + "\n")
print("\nwrote out/deploy_prepare/output.txt")
