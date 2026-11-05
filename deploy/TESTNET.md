# Minimal NEAR testnet deploy (manual wallet signing)

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
# create_market ...
# commit_evidence ...
# submit_verdict x3 ...
```

Exact helper args are printed by `python scripts/deploy_prepare.py` (reads
`contract/testdata/real_panel.json` + a published evidence hash; does NOT sign).

## 5. Put explorer links in README
After each successful tx, paste the NearBlocks / Near Explorer URL into README
under **Testnet deploy**. Example form:
`https://testnet.nearblocks.io/txns/<hash>`

## Blocker (current)
This agent box has `NEARAI_API_KEY` but **no NEAR wallet credentials**. Deploy
stops here until you run the signed steps above on a machine with your keychain.
