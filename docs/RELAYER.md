# Automatic relayer

An integrating app creates a market and reads it. This process does the resolution steps that used to be typed into near-cli: `commit_evidence`, three `submit_verdict` calls, and `finalize` after the dispute window. It scans every market id, not only market 0.

The app still chooses the question, the rules, the source URLs, the deadline, the dispute window, and the `escalation_owner`. The relayer does not call `resolve_dispute`. That method belongs to the market's `escalation_owner`.

## Two paths

**Integrator.** Call `create_market` on `verdict-v2-baheet.testnet`, then poll `get_market`.

`create_market` arguments:

- `question`, `rules`
- `sources` — URLs fetched outside the TEE. A sports market needs two URLs on different hosts (for example the ESPN scoreboard for that date, and `https://github.com/nflverse/nfldata`). The relayer reads the nflverse repo the same way `scripts/bench_scale.py` does: `games.csv`. It does not invent a second source when a fetch fails.
- `prompt_template_hash` — `PROMPT_TEMPLATE_HASH` from `scripts/verdict_request.py`. The relayer refuses a market whose hash is anything else, because it will only send that prompt.
- `resolvers` — the three registered signer addresses. On the live contract those models are `Qwen/Qwen3.8-27B`, `deepseek-ai/DeepSeek-V4.1-Flash-1017`, and `z-ai/glm-5.3-flash`. The relayer calls the models stored on the market at creation (`resolver_models`), whichever order they are in.
- `deadline_ns` — unix time in nanoseconds, in the future. Betting closes then. The relayer waits until `block_timestamp >= deadline_ns`.
- `dispute_window_ns` — per market. The hand-run Saints vs. Giants market used one hour (`3600000000000`).
- `dispute_bond` — the hand-run market used 1 NEAR.
- `escalation_owner` — the account that may call `resolve_dispute` for this market only.

Then read `get_market`. Status moves through `Open`, `Proposed`, and `Final`, or stops at `Escalated` or `Disputed`. Pools, votes, and the evidence URI are on that view. Deposits and claims stay in the app. A user who disagrees during the window calls `dispute` with the bond attached. The relayer does not dispute markets.

**Relayer.** A registered account (or the protocol owner) runs `python scripts/relayer.py`. For each market, independently:

| On-chain state | What the relayer does |
| --- | --- |
| `Open`, before `deadline_ns` | Skip |
| `Open`, deadline passed, no evidence for this round | Fetch `sources`, publish the bundle, `commit_evidence`, then `submit_verdict` for each missing model |
| `Open`, evidence already committed | Reuse that exact bundle (the URI must hash to `round_evidence_hash`). Submit only the models that have not voted |
| `Proposed`, dispute window still open | Skip |
| `Proposed`, window closed, still not disputed | `finalize` |
| `Final` | Skip |
| `Disputed` or `Escalated` | Stop and report `escalation_owner`. Do not call `resolve_dispute` |
| Unanimous `TOO_EARLY` | The contract clears the votes, increments `round`, and stays `Open`. That is the next round, not a failed job. The relayer commits again, up to `VERDICT_MAX_ROUNDS_PER_PASS` (default 2) per pass |
| Split vote, or unanimous `AMBIGUOUS` | The contract sets `Escalated`. The relayer stops |

Sports statistics are committed only after two successful snapshots from different hosts. A single ESPN line is not enough. The pass reports `wait` and tries again later.

## Register the relayer once

The protocol owner is `baheet.testnet`. Owner calls are accepted without registration. A dedicated relayer account needs one owner transaction so the service is not owner-only:

```bash
python scripts/relayer.py print-register --account YOUR_RELAYER.testnet --out register_relayer.json
near contract call-function as-transaction verdict-v2-baheet.testnet register_relayer \
  file-args register_relayer.json prepaid-gas '30.0 Tgas' attached-deposit '0 NEAR' \
  sign-as baheet.testnet network-config testnet sign-with-keychain send
```

`register_relayer.json` is `{"account":"YOUR_RELAYER.testnet"}`. Delete it when you are done; it is not a key.

PowerShell's `Set-Content -Encoding utf8` writes a BOM, and near-cli then rejects the JSON. Write the file without a BOM:

```powershell
[System.IO.File]::WriteAllText((Join-Path (Get-Location) 'register_relayer.json'), '{"account":"YOUR_RELAYER.testnet"}')
```

The Python service writes its own near-cli arg files with `Path.write_bytes` (UTF-8, no BOM) and passes `file-args`. The private key is read from `NEAR_CREDENTIALS_FILE` or from `NEAR_PUBLIC_KEY` and `NEAR_PRIVATE_KEY`, then handed to near-cli as `sign-with-access-key-file`. It is not placed on the command line.

## Start locally

Dry-run is the default. It reads `get_market` from the public testnet RPC, prints the action for every market, and does not send a transaction, fetch sources, or call NEAR AI. No env vars are required.

```bash
python scripts/relayer.py --once
```

Offline, with the synthetic markets in `fixtures/relayer/offline_markets.json` (no RPC):

```bash
python scripts/relayer.py --once --markets-file fixtures/relayer/offline_markets.json
```

```bash
python -m unittest relayer.test_relayer
```

Live. Set the names below in the shell or in a gitignored file. Do not commit the values. `relayer/example.config.json` holds the non-secret fields only; a config file that contains `private_key` or `nearai_api_key` is refused.

| Name | Required for live sends | Role |
| --- | --- | --- |
| `VERDICT_RELAYER_MODE` | yes | `live` (default is `dry-run`) |
| `VERDICT_SIGNER_ACCOUNT` | yes | the registered relayer account |
| `NEAR_CREDENTIALS_FILE` | yes, unless the key pair env vars are set | path to a JSON file outside the repo: `{"public_key":"ed25519:...","private_key":"ed25519:..."}` |
| `NEAR_PUBLIC_KEY`, `NEAR_PRIVATE_KEY` | alternative to the file | read into a mode-0600 temp file and deleted on exit |
| `VERDICT_EVIDENCE_PUSH` | yes when a market still needs `commit_evidence` | `1` commits the bundle and pushes it |
| `NEARAI_API_KEY` | yes when the exact request is not already in the cache | NEAR AI Cloud key, from the environment only |
| `VERDICT_CONTRACT` | no | default `verdict-v2-baheet.testnet` |
| `VERDICT_NETWORK` | no | default `testnet` |
| `VERDICT_RPC_URL` | no | default `https://rpc.testnet.near.org` |
| `VERDICT_EVIDENCE_REPO` | no | default `Baheet18/verdict` |
| `VERDICT_EVIDENCE_REMOTE` | no | default `origin` |
| `VERDICT_EVIDENCE_WORKDIR` | no | git clone used for evidence commits; default is this repo. A separate clone keeps evidence refs away from day-to-day branches |
| `VERDICT_MARKET_IDS` | no | comma-separated ids. Empty means scan from 0 until `get_market` returns null |
| `VERDICT_POLL_INTERVAL_S` | no | default 60, minimum 5 |
| `VERDICT_FIXTURE_DIR` | no | default `fixtures/relayer_cache` |
| `VERDICT_NEAR_BIN` | no | default `near` (near-cli-rs) |
| `VERDICT_GIT_USER_NAME`, `VERDICT_GIT_USER_EMAIL` | no | author on evidence commits; default `verdict-relayer` / `verdict-relayer@localhost` |

```bash
export VERDICT_RELAYER_MODE=live
export VERDICT_SIGNER_ACCOUNT=YOUR_RELAYER.testnet
export NEAR_CREDENTIALS_FILE=/path/outside/the/repo/access-key.json
export VERDICT_EVIDENCE_PUSH=1
# NEARAI_API_KEY — set in the shell from cloud.near.ai; do not write it into a file in this repo
python scripts/relayer.py --loop
```

`python scripts/relayer.py --once` is the one-shot form. The API key stays in the shell.

near-cli-rs has to be on `PATH` for live transactions (`cargo install near-cli-rs`). View calls use the public RPC and do not need it. This environment check refuses to start live mode when `is_relayer` is false for `VERDICT_SIGNER_ACCOUNT`.

A cached vote in `fixtures/relayer_cache/` or `contract/testdata/real_panel.json` is submitted only when its `request_raw` is byte-for-byte the request the relayer just built, and `text` equals `model:sha256(request):sha256(response)`. Otherwise live mode calls `https://cloud-api.near.ai/v1/chat/completions` with the body from `build_request` (`data=`, not a re-serialized JSON object). Allowed keys are `model`, `messages`, `temperature` 0, `seed` 1114. The user message starts with the `VERDICT/1` header.

## Evidence permalinks

The bundle string is the one `scripts/verdict_request.evidence_bundle` builds. Its sha256 is `evidence_bundle_hash`. The file committed under `fixtures/evidence/<hash>.json` is a wrapper around that string.

The relayer asks git for the commit SHA (`git rev-parse` of the commit whose tree contains those bytes), pushes `refs/heads/verdict-evidence/<commit-sha>`, and checks `git ls-remote` before it will call `commit_evidence`. The URI is `https://raw.githubusercontent.com/Baheet18/verdict/<sha>/fixtures/evidence/<hash>.json`. In live mode it also GETs that URL and requires the body to match the committed file.

`/master/` and `/main/` are refused. `VERDICT_EVIDENCE_COMMIT_SHA` is ignored. A SHA that git did not return is not used. Until the push exists, dry-run prints `PENDING_COMMIT_SHA://...` and sends nothing. With `VERDICT_EVIDENCE_PUSH=1`, live mode GETs the permalink and requires the body to match the file before `commit_evidence`. If the push flag is off and the commit is not already on the remote, that market errors and the other markets continue.

Do not delete the `verdict-evidence/<sha>` refs. GitHub serves the raw URL only while the commit is still reachable.

The upstream nflverse snapshot URL may contain `/raw/master/` because that is the file `scripts/bench_scale.py` fetches. That URL is inside the bundle. The evidence permalink is a separate commit SHA.

## What is already on testnet

Market 0 on `verdict-v2-baheet.testnet` is Saints vs. Giants, `Final` / `Yes`. It was completed by hand (create, commit, three votes, Proposed, then finalize). The finalize transaction is [C4ZQabkbLzSVzDCJFsxUEWEn18gVLmi4h2ANKGBXUsXx](https://explorer.testnet.near.org/transactions/C4ZQabkbLzSVzDCJFsxUEWEn18gVLmi4h2ANKGBXUsXx). Its evidence URI is pinned to commit `7d4149cfff493fdc3cbe932ac1d4c7406c605f40`. `escalation_owner` is `baheet.testnet`. A dry-run skips this market. The relayer is for the markets created after it.

## Limits

This is testnet. One registered relayer can delay a market by withholding a transaction; after three days past the deadline the market's `escalation_owner` may call `escalate_stale`. The relayer cannot edit a vote the contract has already accepted: the contract checks the TEE signature and, once any vote is in, freezes the evidence hash. Before that first vote the relayer still chooses which snapshot to commit. That choice is the trust described in the README Limits section. This service is not a trustless oracle, and it does not claim to be one.

Sources are fetched outside the TEE. The integrity check is the sha256 of the snapshot inside the signed request.

The relayer does not deploy keys, and it does not call NEAR AI unless `VERDICT_RELAYER_MODE=live` and `NEARAI_API_KEY` is set (or a cache entry matches). Dry-run never invents a signature, a commit SHA, or an explorer transaction.
