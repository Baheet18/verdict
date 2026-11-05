# NEAR Hackathon submission payload draft (HUMAN submits)

**Deadline**: Nov 14 23:59 UTC (= Nov 15 00:59 WAT).

## Project
- **Name**: Verdict
- **One-liner**: TEE-attested multi-model resolution panel for prediction markets on NEAR.
- **Repo**: https://github.com/Baheet18/verdict (push latest local commits before submit)
- **Demo video**: _(link after recording — see docs/DEMO_VIDEO.md)_
- **X/thread**: _(link after human posts — see docs/X_POST_DRAFT.md)_
- **Testnet**: _(explorer links after human wallet deploy — see deploy/TESTNET.md)_

## Tracks / fit (edit to match the form)
- AI / NEAR AI Cloud / confidential compute
- DeFi / prediction-market infrastructure
- Developer tooling (rule linter, offline verify, evidence publish)

## What to paste into “Description”
Verdict settles markets with three attested models (Qwen3.8-27B, DeepSeek-V4.1-Flash-1017, GLM-5.3-flash) on NEAR AI Cloud. Votes are EIP-191 signatures over `model:sha256(req):sha256(resp)`. The NEAR contract:
1. Requires an exact request-key allowlist (blocks logit_bias / guided decoding steering).
2. Requires `commit_evidence` (relayer/owner) after the market deadline, with `evidence_uri`, before any `submit_verdict`.
3. Binds header fields: market_id, round, rules_hash, prompt_template_hash, evidence_bundle_hash.
4. Maps each signer → one model; rejects replayed signed texts.

Offline screener mode (`python scripts/offline_demo.py`) needs no API key.

## Numbers (only those backed by committed scripts + fixtures)
- Post-cutoff sports: see `docs/BENCHMARK.md` (124 markets; 107 settled; 107 agree UMA; 0 disagree).
- UMA-disputed thin evidence: see `docs/BENCHMARK_DISPUTED.md` (34 markets; 0 settled; 34 escalate).
- Curated disputed multi-source subset: see `docs/BENCHMARK_DISPUTED_CURATED.md` (after run).
- Rule linter: flagged 27/34 disputed; matched non-disputed control 26/34 — gap not used as a marketing claim (`out/lint_control/summary.json`).

## Limits (must stay in the submission)
- Model-serving container not covered by RTMR3 pin.
- Evidence fetched outside the TEE.
- Owner registers signers / settles disputes; relayer commits evidence.
- GPU↔TDX binding is the shared nonce only.
- Testnet deploy requires the builder’s wallet (not agent-held keys).

## Checklist before submit
- [ ] Push local round-5 commits to GitHub (explicit OK required)
- [ ] Human signs testnet deploy; paste tx links into README
- [ ] Record demo video from DEMO_VIDEO.md
- [ ] Post X draft manually
- [ ] Confirm no secrets in git (`git grep -i api_key` clean on tracked files)
