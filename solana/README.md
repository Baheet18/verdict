# Verdict on Solana — resolver, step 1

This folder is a Solana port of the resolution rules in `contract/src/lib.rs`. It does not change the NEAR contract or the site.

The program id is `6nbXLUgehsnFqUTvg3EUCtP8mDHodBXFv6NhdJwBt3AW`. Devnet status is at the bottom.

Anchor 1.2.2, Agave CLI 4.1.2, Rust 1.100.0. The on-chain program is `programs/verdict`. Tests run in LiteSVM against the compiled `.so`.

## What the program does

The protocol owner sets three ed25519 public keys (GLM, then Qwen, then DeepSeek), the dispute window in seconds, and the dispute bond in lamports. Only that owner can change them, and a change applies to markets created afterwards.

`create_market` locks a numeric market id, the question (1–200 bytes), `rules_hash`, `evidence_hash`, a future deadline (unix seconds), and an `escalation_owner`. The escalation owner cannot be changed. The market also copies the three signer keys, the window, and the bond from config at that moment.

`submit_verdict` reads the ed25519 instruction placed immediately before it. That instruction must carry exactly three signatures, in GLM / Qwen / DeepSeek order, with every offset pointing at the ed25519 instruction itself (`u16::MAX`). The native ed25519 program checks the signatures. This program checks that the public key and the message bytes are the ones for this market.

Each model signs a 119-byte message:

| offset | length | contents |
| -----: | -----: | --- |
| 0 | 13 | ASCII `VERDICT-SOL/1` |
| 13 | 8 | market id, `u64` little-endian |
| 21 | 32 | evidence hash |
| 53 | 32 | rules hash |
| 85 | 1 | outcome: `0` yes, `1` no, `2` unclear |
| 86 | 1 | confidence, `0..=100` |
| 87 | 32 | hash of the cited rule id or rule text |

A yes or no at confidence 70 or above is clear. 70 is a program constant; the owner cannot lower it. If all three votes are clear and the same outcome, the market becomes Proposed and the dispute window opens. A split, an unclear vote, or any vote below 70 moves the market to Escalated. The three votes are stored either way: signer, outcome, confidence, and cited-rule hash, plus the question and both hashes, so a viewer can see what was voted on.

`dispute` can be called by anyone during the window. They pay the snapshotted bond. The lamports are transferred onto the market account and the market becomes Escalated. This step does not pay the bond back. Do not use a bond you need returned until a later instruction defines that.

`finalize`, after the window and with no dispute, marks the market Final with the proposed outcome. Anyone may call it.

`escalation_resolve` is the only way to settle an Escalated market, and only that market's escalation owner may call it. The outcome may be yes, no, or unclear. The protocol owner has no settle instruction. If the integrating app set the protocol owner as that market's escalation owner, the same key can settle that market, which is the same rule as on NEAR.

Accounts:

| instruction | accounts, in order |
| --- | --- |
| `initialize_config` | config PDA, owner (signer, payer), system program |
| `update_config` | config PDA, owner (signer) |
| `create_market` | market PDA, config PDA, payer (signer), system program, clock sysvar |
| `submit_verdict` | market PDA, config PDA, instructions sysvar, clock sysvar |
| `dispute` | market PDA, disputer (signer), clock sysvar, system program |
| `finalize` | market PDA, clock sysvar |
| `escalation_resolve` | market PDA, escalation owner (signer) |

PDA seeds: config is `[b"config"]`. A market is `[b"market", market_id.to_le_bytes()]`.

Events: `ConfigUpdated`, `MarketCreated`, `VerdictSubmitted`, `MarketDisputed`, `MarketFinalized`, `EscalationResolved`.

## What proves a vote came from a model

On NEAR, a vote is an EIP-191 signature over the text `model:sha256(request_raw):sha256(response_raw)`. `submit_verdict` recovers the secp256k1 address and accepts it only when that address is in the market's resolver list, is still registered, and the model name in the request matches the name snapshotted for that address at `create_market`.

The contract does not check a TEE quote. `register_signer` is owner-only. It stores the address, one model name, and `sha256` of an attestation-report JSON. The owner is expected to register a key only after `scripts/hw_verify.py` has passed. That script, off chain, checks the Intel TDX quote, that `report_data` begins with the signing address and then the caller's nonce, that the signing public key hashes to that address, the RTMR measurements against `scripts/expected_measurements.json`, and an NVIDIA GPU attestation. The relayer does not hold the private key. `relayer/panel.py` asks NEAR AI Cloud for the chat completion and then `GET /v1/signature/{id}?signing_algo=ecdsa`. The signing key is issued inside the TEE by dstack's key service. A signature shows that a key the owner registered produced it. The link from that key to a model and to a machine is the off-chain attestation check, which anyone can repeat against a fresh report. It does not show which replica signed when several replicas share a key, and it does not measure the model container.

The Solana program only checks that three ed25519 signatures verify under the three public keys in the market's snapshot. The owner writes those keys into config. Nothing on chain, and nothing in this step, ties a key to GLM, Qwen, DeepSeek, or to a TEE. NEAR AI's live signatures are secp256k1 over a different message, so they do not verify here. Whoever holds the three ed25519 secret keys can sign any outcome the program will accept. That is a weaker proof than the NEAR deployment. Saying otherwise would be false.

## How to build and test

From this directory, with Agave 4.1.2 on `PATH` and Rust 1.100 (the `rust-toolchain.toml` here selects it):

```bash
cargo-build-sbf --manifest-path programs/verdict/Cargo.toml --sbf-out-dir target/deploy
cargo test --manifest-path programs/verdict/Cargo.toml --lib
cargo test --manifest-path tests/Cargo.toml
```

`cargo-build-sbf` writes `target/deploy/verdict.so`. The lifecycle tests load that file. They cover a unanimous yes through to Final, a dispute that posts the bond and is settled by the escalation owner, a split, an unclear vote, a low-confidence vote, a bad signature rejected by the ed25519 program, a valid signature from the wrong key, a signature replayed onto another market, a stranger failing to resolve, and the protocol owner failing to resolve. One more test checks that `update_config` does not retarget a market that already exists, and that a non-owner cannot update config.

`target/` is gitignored. `cargo-build-sbf` also writes a program keypair there. Do not commit it. Keypair JSON is gitignored. No API key belongs in this folder.

## Devnet

Declared program id: `6nbXLUgehsnFqUTvg3EUCtP8mDHodBXFv6NhdJwBt3AW`.

Deploy, once the `.so` exists and you have a keypair whose public key is that id (the file is not in git):

```bash
solana program deploy target/deploy/verdict.so \
  --program-id target/deploy/verdict-keypair.json \
  --keypair /path/outside/the/repo/deployer.json \
  --url devnet
```

Devnet deploy was not completed. A fresh deployer key (`solana-keygen`, kept outside the repo) had a balance of 0 SOL. `solana airdrop` against `https://api.devnet.solana.com` and `https://solana-devnet.api.onfinality.io/public` returned "airdrop request failed. This can happen when the rate limit is reached." `https://rpc.ankr.com/solana_devnet` refused the request without an API key. `https://devnet.solana.rpcpool.com` did not accept a connection. `https://faucet.solana.com/api/request` answered "Missing wallet address" and did not fund the account. A second airdrop on `api.devnet.solana.com` hit the same rate limit. With no SOL, `solana program deploy` was not sent. The id above is the id compiled into the program, not a devnet account.

## What step 2 needs

The relayer has to build the 119-byte message above and one ed25519 instruction with three signatures, immediately before `submit_verdict`. Order is GLM, Qwen, DeepSeek. Header byte 0 is `3`, byte 1 is `0`, then three 14-byte offset structs (`signature_offset`, `signature_instruction_index`, `public_key_offset`, `public_key_instruction_index`, `message_data_offset`, `message_data_size`, `message_instruction_index`), all three instruction indexes `65535`. The tests pack pubkey, signature, then message for each signer after that header. The program follows the offsets; it does not require that packing, but the offsets must land past the header and inside this instruction.

NEAR signatures cannot be submitted as they are. Step 2 needs ed25519 keys, registered with `update_config` before `create_market`, and a stated holder for the secrets. If the relayer holds them, the vote proof is the weaker one described above. Do not describe it as the NEAR TEE check.

`evidence_hash` and `rules_hash` are fixed at `create_market` by whoever creates the market. There is no later `commit_evidence`. On NEAR the relayer commits evidence after the deadline. Here, the account that creates the market chooses the hashes. The integrating app should create the market, because that is also how `escalation_owner` is set.

There is no `TOO_EARLY` round. An unclear or low-confidence panel escalates and stays there. There is no `escalate_stale`. If the owner rotates signers, markets already created keep the old keys; a market that never receives three valid signatures stays Open. A later step can add a grace-period escalation for the escalation owner, matching NEAR, if that liveness hole matters.

The dispute bond stays on the market account. Step 2 should not collect a real bond until there is an explicit release rule.

`submit_verdict` and `finalize` are permissionless. The signatures are the gate, not a relayer allowlist.
