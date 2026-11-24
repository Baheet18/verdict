# Verdict on Solana

This folder is the Solana port of the resolver in `contract/src/lib.rs`. It does not change how the NEAR contract or the existing site pages behave. The devnet page is [`site/solana.html`](../site/solana.html).

Program id compiled into the binary: `6nbXLUgehsnFqUTvg3EUCtP8mDHodBXFv6NhdJwBt3AW`.

Anchor 1.2.2, Agave CLI 4.1.2, Rust 1.100.0.

## What a vote proves

NEAR AI Cloud returns an EIP-191 secp256k1 signature, the same one the NEAR contract checks. The signed text is exactly

```text
<model>:<sha256(request_raw)>:<sha256(response_raw)>
```

with both hashes as lowercase hex. The program rebuilds that text from the uploaded request and response bytes, hashes it as

```text
keccak256("\x19Ethereum Signed Message:\n" || decimal_length || text)
```

and calls the `secp256k1_recover` syscall. The Ethereum address is the last 20 bytes of `keccak256` of the recovered 64-byte public key. It must equal the signer the owner registered for that slot, and the model name in the request must equal the registered model name.

The request is the chat-completions JSON. After JSON string unescaping, the user message is

```text
VERDICT/1 market_id=<id> round=<round> rules_hash=<hex> prompt_template_hash=<hex> evidence_bundle_hash=<hex>
<evidence bytes>
```

The program checks that this header is canonical, that `sha256(evidence bytes)` equals `evidence_bundle_hash`, and that the market id, round, rules hash, prompt-template hash and evidence hash match the market account. That is the same chain the NEAR contract verifies in `recover_signer` and `check_request`. The full request-key allowlist from the NEAR contract (rejecting `logit_bias` and similar) is not reimplemented here; the signature still covers the exact request bytes, so a different request is a different hash and will not verify.

Ed25519 was removed. NEAR AI Cloud does not sign with those keys, so an ed25519 path could not check these votes. The syscall is used instead of the secp256k1 precompile because the program has to hash the request and response itself and then recover against that digest.

What this does **not** prove: the program does not check a TEE quote. On NEAR, `register_signer` stores an address only after the owner has run `scripts/hw_verify.py` off chain (TDX quote, `report_data` bound to the signing address, measurement allowlist, GPU attestation). The relayer never holds that private key; it fetches the signature from `GET /v1/signature/{id}?signing_algo=ecdsa`. The Solana owner registers the same kind of address. A signature shows that the registered key signed that request and response. The link from the key to a model binary and to a machine is still the off-chain attestation, not a check inside this program.

## Accounts and instructions

Config, set only by the protocol owner: three `(ethereum address, model name)` slots, dispute window in seconds, dispute bond in lamports. A change applies to markets created afterwards. Each market copies the signers, window and bond at creation.

`create_market` locks the question (1–200 bytes), rules hash, evidence hash, prompt-template hash, a future deadline, and an `escalation_owner`. The escalation owner cannot be changed. The protocol owner has no instruction that settles a market. If a market was created with the protocol owner as its escalation owner, that same key can settle that market, which is the same rule as on NEAR.

`init_payload` / `write_payload` upload the raw request and response for one signer. The two bodies together must be at most 9600 bytes. `submit_vote` checks the signature and header. When all three slots have voted:

- unanimous yes or no, each with confidence at least 0.70 in the signed response, becomes Proposed and opens the dispute window
- unanimous `TOO_EARLY` clears the votes and increments `round` (the round is inside the signed header, so the old signatures do not replay)
- a split, `AMBIGUOUS`, or a yes/no below 0.70 becomes Escalated

Confidence is a Solana gate on top of the NEAR signature check. The NEAR contract records the outcome and does not read the confidence field. Missing confidence does not count as clear.

`dispute` can be called by anyone during the window. They pay the snapshotted bond onto the market account. This program does not pay the bond back. `finalize`, after the window, marks the proposed outcome Final. `escalation_resolve` is only for an Escalated market, and only that market's escalation owner may call it.

Every transaction that calls the program must request a 256 KB heap frame and enough compute units (the relayer asks for 1.4 million). The default 32 KB heap is too small for a real panel body.

Events: `ConfigUpdated`, `MarketCreated`, `VoteAccepted`, `VerdictSubmitted`, `RoundAdvanced`, `MarketDisputed`, `MarketFinalized`, `EscalationResolved`.

PDA seeds: config `[b"config"]`, market `[b"market", market_id le]`, payload `[b"payload", market_id le, slot]`.

## Tests

```bash
cargo-build-sbf --manifest-path programs/verdict/Cargo.toml --sbf-out-dir target/deploy
cargo test --manifest-path programs/verdict/Cargo.toml --lib
cargo test --manifest-path tests/Cargo.toml
python3 -m unittest solana/test_relayer.py
```

Run the Python test from the repository root. The LiteSVM file includes the cached Saints-vs-Giants panel in `contract/testdata/real_panel.json`: three real NEAR AI signatures, unanimous yes, confidence 0.99, and the program moves that market to Proposed. The other tests cover a finalised yes, a dispute, a split, an ambiguous panel, low confidence, a bad recovery id, a wrong signer, a signature replayed onto another market, and both a stranger and the protocol owner failing to resolve.

`target/` is gitignored. Do not commit keypair JSON. The program keypair `cargo-build-sbf` writes under `target/deploy/` is not a secret to publish; it is still a keypair, so it stays out of git.

## Relayer

From the repository root, with the keypair file **outside** the repo:

```bash
export NEARAI_API_KEY=...          # do not write this into a file in the repo
export SOLANA_KEYPAIR=/path/outside/the/repo/deployer.json
export SOLANA_RPC=https://api.devnet.solana.com
python3 solana/relayer.py --demo
```

The demo asks the three models the same way `relayer/panel.py` does (`POST /v1/chat/completions`, then `GET /v1/signature/{id}?signing_algo=ecdsa`), registers the `signing_address` values those responses carry, and submits the raw bytes. Market 1 is GENESIS X3 Melee singles (Cody Schwab won, 13–15 February 2026) with two agreeing snapshots. Market 2 uses the same question with contradictory snapshots, which the prompt tells the models to treat as ambiguous, and then the escalation owner records Ambiguous. A 60 second dispute window applies. The script writes `site/solana-markets.json` with transaction signatures and public keys only.

## Devnet

Not deployed in this change. A deployer key was generated outside the repo. Its public key is `Y3tvBjJMS9X4wVCdXyi6jQpbqpVSjrGRKQ7C3Brxcs1`. Its balance stayed 0 SOL. `solana airdrop` on `https://api.devnet.solana.com` returned "airdrop request failed. This can happen when the rate limit is reached" on both attempts in this step. `solana program deploy` was not sent.

Deploy needs about **5 SOL** on that address: the program binary is about 240 KB, and the deploy holds a second copy as a buffer until it is closed. Two markets and their payload accounts are small beside that, and payload rent is returned when a vote is accepted.

```bash
solana program deploy solana/target/deploy/verdict.so \
  --program-id /path/outside/the/repo/program-keypair.json \
  --keypair /path/outside/the/repo/deployer.json \
  --url devnet
```

The program keypair's public key must be `6nbXLUgehsnFqUTvg3EUCtP8mDHodBXFv6NhdJwBt3AW`.

## For a filmed demo

Honest claims:

- The Solana program verifies NEAR AI Cloud's EIP-191 secp256k1 signatures and the header binding described above. A cached real panel (Saints vs Giants, three hardware-attested votes) is accepted in the LiteSVM test.
- The owner chooses the signer addresses. The TEE quote is still checked off chain, as on NEAR, and is not checked inside this program.
- Clear unanimous yes/no votes with confidence at least 0.70 open a dispute window. Splits, ambiguous votes, low confidence and disputes go to that market's escalation owner. The protocol owner cannot settle a market unless it was created with them as that owner.
- The dispute bond stays on the market account. Do not describe a refund that this program does not perform.
- Do not describe the result as hardware-checked on Solana. Say the signature matches a registered NEAR AI signing address, and point at `scripts/hw_verify.py` for the off-chain quote check.

`NEARAI_API_KEY` was unset in this environment, so the live model calls were not run.
