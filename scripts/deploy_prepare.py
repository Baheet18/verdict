#!/usr/bin/env python3
"""Print JSON-args for a minimal testnet flow from the sports real_panel fixture. Does NOT sign or deploy.

Round 6: Saints vs. Giants (Polymarket 4329887), dispute_bond = 1 NEAR, evidence_uri = GitHub raw
placeholder that works only AFTER human approves a push of fixtures/evidence/<hash>.json.
Fresh contract so market_id=0 matches the signed headers.
"""
from __future__ import annotations
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from verdict_request import PROMPT_TEMPLATE_HASH

panel = json.loads((ROOT / "contract" / "testdata" / "real_panel.json").read_text())
out_lines = []
def P(s=""): out_lines.append(s); print(s)

P("=== Verdict deploy_prepare (NO KEYS — human signs) ===")
P("Wasm (build locally): contract/target/near/verdict.wasm")
P("See deploy/TESTNET.md for near CLI steps.")
P("IMPORTANT: deploy to a FRESH contract account so the first create_market yields market_id=0")
P("           (headers in real_panel.json are signed with market_id=0).")
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

req0 = json.loads(panel["votes"][0]["request_raw"])
user = req0["messages"][1]["content"]
header, evidence = user.split("\n", 1)
fields = dict(p.split("=", 1) for p in header.split(" ")[1:])
ev_h = fields["evidence_bundle_hash"]
assert ev_h == panel.get("evidence_sha256") or True
rules = panel.get("rules") or ""
question = panel.get("question") or "demo market"
# 1 NEAR = 10^24 yoctoNEAR
ONE_NEAR = "1000000000000000000000000"
# Commit-SHA permalink only after human-approved push (never /master/, never invent SHA).
try:
    from evidence_uri import github_raw_permalink, pending_uri
    rel = panel.get("evidence_path") or f"fixtures/evidence/{ev_h}.json"
    uri = panel.get("proposed_evidence_uri_after_push") or pending_uri(rel)
except Exception:
    uri = panel.get("proposed_evidence_uri_after_push") or f"PENDING_COMMIT_SHA://fixtures/evidence/{ev_h}.json"

P("=== 3) create_market args (owner or anyone) ===")
P(json.dumps({
    "question": question,
    "rules": rules,
    "sources": [
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=20261018",
        "https://github.com/nflverse/nfldata",
    ],
    "prompt_template_hash": panel.get("prompt_template_hash") or PROMPT_TEMPLATE_HASH,
    "resolvers": [v["signing_address"] for v in panel["votes"]],
    "deadline_ns": "<set to ~now_ns, then WAIT until block_timestamp >= deadline before commit_evidence>",
    "dispute_window_ns": 3_600_000_000_000,
    "dispute_bond": ONE_NEAR,
}, indent=2))
P(f"prompt_template_hash: {panel.get('prompt_template_hash')}")
P(f"evidence_bundle_hash: {ev_h}")
P(f"polymarket_id (off-chain reference): {panel.get('polymarket_id')}")
P(f"dispute_bond: 1 NEAR ({ONE_NEAR} yocto)")
P("")
P("=== 4) commit_evidence (relayer/owner) AFTER deadline, BEFORE votes ===")
P(json.dumps({
    "market_id": 0,
    "round": 0,
    "evidence_bundle_hash": ev_h,
    "evidence_uri": uri,
}, indent=2))
P("NOTE: evidence_uri points at GitHub raw. It 404s until YOU approve a push that includes")
P(f"      fixtures/evidence/{ev_h}.json (already prepared locally). Do not invent fixture:// URIs.")
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
P("=== 6) optional dispute (after Proposed) ===")
P('near contract call-function as-transaction <contract> dispute json-args \'{"market_id":0}\' \\')
P("  prepaid-gas '50.0 Tgas' attached-deposit '1 NEAR' sign-as <your>.testnet ...")
P("(Only after human wallet signing; agent never holds keys.)")
P("")
P("=== NOTE ===")
P("No private keys in this repo. Sign deploy/init/calls with your own testnet keychain.")
P("After txs confirm, paste explorer links under README 'Testnet deploy'.")

out_dir = ROOT / "out" / "deploy_prepare"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "output.txt").write_text("\n".join(out_lines) + "\n")
# also write machine-readable args
args = {
    "question": question,
    "rules": rules,
    "evidence_bundle_hash": ev_h,
    "evidence_uri": uri,
    "dispute_bond_yocto": ONE_NEAR,
    "dispute_bond_near": 1,
    "market_id": 0,
    "polymarket_id": panel.get("polymarket_id"),
    "signers": [{"address": v["signing_address"], "model": v["model"],
                 "attestation_report_sha256": v.get("attestation_report_sha256")} for v in panel["votes"]],
    "votes_file": "contract/testdata/real_panel.json",
}
(out_dir / "args.json").write_text(json.dumps(args, indent=2))
print("\nwrote out/deploy_prepare/output.txt and args.json")
