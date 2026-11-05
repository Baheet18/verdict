# NEAR Hackathon submission payload draft (HUMAN submits)

**Deadline**: Nov 14 23:59 UTC (= Nov 15 00:59 WAT).

## Project
- **Name**: Verdict
- **One-liner**: TEE-attested multi-model resolution panel for prediction markets on NEAR.
- **Repo**: https://github.com/Baheet18/verdict (push latest local commits before submit — needs explicit OK)
- **Demo video**: _(link after recording — see docs/DEMO_VIDEO.md)_
- **X/thread**: _(link after human posts — see docs/X_POST_DRAFT.md)_
- **Testnet**: _(explorer links after human wallet deploy — see deploy/TESTNET.md)_

## Tracks (privacy, defi, security, open)
- **Privacy / confidential compute**: NEAR AI Cloud TEEs; TDX + NVIDIA attestation; allowlisted host-stack measurements.
- **DeFi**: prediction-market resolution primitive; bonded disputes; TOO_EARLY as a first-class round advance.
- **Security**: on-chain request-key allowlist, duplicate-JSON-key rejection, evidence commit before votes, relayer-only submit, replay protection, per-signer model binding.
- **Open**: public repo, offline screener mode, published evidence bundles, scoreboard rebuilt from sanitized cache.

## What to paste into “Description”
Verdict settles markets with three attested models (Qwen3.8-27B, DeepSeek-V4.1-Flash-1017, GLM-5.3-flash) on NEAR AI Cloud. Votes are EIP-191 signatures over `model:sha256(req):sha256(resp)`. The NEAR contract:
1. Requires an exact request-key allowlist (blocks logit_bias / guided decoding steering) and rejects duplicate JSON keys.
2. Requires `commit_evidence` (relayer/owner) after the market deadline, with `evidence_uri`, before any `submit_verdict`.
3. Binds header fields: market_id, round, rules_hash, prompt_template_hash, evidence_bundle_hash.
4. Maps each signer → one model; rejects replayed signed texts.

**Headline**: Clear markets auto-settled at ~$0.002, 107/107 agreed with UMA, hardware-attested; request-parameter steering blocked (relayer evidence choice remains — see Limits). Unclear markets escalate to humans, 0 invented answers. TOO_EARLY is first-class (Bitquery 243/470).

Offline screener mode (`python scripts/offline_demo.py`) needs no API key.

## Numbers (backed by committed scripts + fixtures)
- Post-cutoff sports: `docs/BENCHMARK.md` — 124 markets; 107 settled; 107 agree UMA; 0 disagree; ~$0.0022/market.
- UMA-disputed thin evidence: `docs/BENCHMARK_DISPUTED.md` — 34 markets; 0 settled; 34 escalate; 0 invent.
- Hand-curated answer-bearing disputed: `docs/BENCHMARK_DISPUTED_HAND.md` — 8 settled; 7 agree UMA; 1 disagree; 0 escalate; 0 invent; thin same IDs 8/8 escalate.
- Bitquery TOO_EARLY: 243 of 470 disputes (9 Sep–7 Oct 2026) — https://bitquery.io/investigations/who-decides-polymarket-disputes
- Rule authoring checklist: optional only; **no** dispute-prediction claim (control 27/34 vs 26/34).

## Limits (must stay in the submission)
- Model-serving container not covered by RTMR3 pin.
- Evidence fetched outside the TEE; registered relayer commits the hash.
- Owner registers signers / settles disputes.
- GPU↔TDX binding is the shared nonce only.
- “Unsteerable” ≠ trustless relayer.
- Testnet deploy requires the builder’s wallet (not agent-held keys).

## Checklist before submit
- [ ] Push local round-6 commits to GitHub (explicit OK required)
- [ ] Human signs testnet deploy on a **fresh** contract; paste tx links into README
- [ ] Evidence raw URL resolves (after push)
- [ ] Record demo video from DEMO_VIDEO.md
- [ ] Post X draft manually
- [ ] Confirm no secrets in git (`git grep -i api_key` clean on tracked files)
