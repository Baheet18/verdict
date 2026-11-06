# Minimal NEAR testnet deploy (manual wallet signing)

**Live v2 account:** `verdict-v2-baheet.testnet`. Market 0 (Saints vs. Giants) is already Final YES; the hand-run finalize is https://explorer.testnet.near.org/transactions/C4ZQabkbLzSVzDCJFsxUEWEn18gVLmi4h2ANKGBXUsXx. For markets after that, an integrator calls `create_market` and reads `get_market`. The relayer in `docs/RELAYER.md` runs `commit_evidence`, `submit_verdict`, and `finalize`. The steps below remain the manual path.

This repo has **no NEAR private keys or seed phrases**. Deploy requires YOU to sign
with your own testnet wallet. Do not paste a private key into chat or into any
script in this repo.

## Prerequisites
- A NEAR **testnet** account with some testnet NEAR (https://testnet.mynearwallet.com or faucet)
- `cargo-near` (already used to build) and `near-cli-rs` (`cargo install near-cli-rs`)
- Built wasm: `contract/target/near/verdict.wasm` (see below)

## 1. Build wasm (no keys needed)
```bash
cd contract
cargo near build non-reproducible-wasm
# or: cargo build --target wasm32-unknown-unknown --release
ls -la target/near/verdict.wasm   # cargo-near path
```

## 2. Create / fund a contract account (YOU sign)
```bash
# Example: create sub-account under your testnet account
near account create-account sponsor-by-faucet-service verdict.<your>.testnet \
  autogenerate-new-keypair save-to-keychain network-config testnet create
```
Or use an existing empty account you control.

## 3. Deploy
```bash
near contract deploy <contract.account.testnet> use-file contract/target/near/verdict.wasm \
  without-init-call network-config testnet sign-with-keychain send
```

## 4. Init + register signers + one market + settle
Use view/call against the ABI. Signer addresses and models come from a verified
`python scripts/attest_demo.py` run (or `contract/testdata/real_panel.json`):

```bash
# init
near contract call-function as-transaction <contract> new \
  json-args '{"owner":"<your>.testnet"}' prepaid-gas '30.0 Tgas' attached-deposit '0 NEAR' \
  sign-as <your>.testnet network-config testnet sign-with-keychain send

# register_signer for each panel address (address, model, attestation_report_sha256)
# register_relayer <your>.testnet
# create_market ... (must include escalation_owner AccountId)
# commit_evidence ...
# submit_verdict x3 ...
# resolve_dispute ... (ONLY the market's escalation_owner — not protocol owner unless selected)
```

Exact helper args are printed by `python scripts/deploy_prepare.py` (reads
`contract/testdata/real_panel.json` — round 6: Saints vs. Giants sports market,
`dispute_bond` = 1 NEAR, `escalation_owner` required, evidence_uri = GitHub raw after push; does NOT sign).
Deploy to a **fresh** contract so market_id=0 matches the signed headers.
`baheet.testnet` is the old prototype (owner-controlled dispute fallback) and is **not** upgraded in place.

## 5. Put explorer links in README
After each successful tx, paste the NearBlocks / Near Explorer URL into README
under **Testnet deploy**. Example form:
`https://testnet.nearblocks.io/txns/<hash>`

## Blocker (current)
This agent box has `NEARAI_API_KEY` but **no NEAR wallet credentials**. Deploy
stops here until you run the signed steps above on a machine with your keychain.
