#!/usr/bin/env python3
"""Print JSON-args for a minimal testnet flow from cached fixtures. Does NOT sign or deploy."""
from __future__ import annotations
import json, pathlib, hashlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
panel = json.loads((ROOT / "contract" / "testdata" / "real_panel.json").read_text())

print("=== register_signer calls (one per vote) ===")
for v in panel["votes"]:
    print(json.dumps({
        "address": v["signing_address"],
        "model": v["model"],
        "attestation_report_sha256": v.get("attestation_report_sha256") or ("0" * 64),
    }, indent=2))
    print("---")

print("prompt_template_hash:", panel.get("prompt_template_hash"))
print("\nresolvers:", [v["signing_address"] for v in panel["votes"]])
print("\n=== NOTE ===")
print("No private keys in this repo. Sign deploy/init/calls with your own testnet keychain.")
print("See deploy/TESTNET.md. For a live settle: create_market, publish evidence,")
print("commit_evidence, submit_verdict x3 with matching request_raw/response_raw/signature.")
