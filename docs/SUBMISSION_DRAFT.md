# NEAR Hackathon submission payload draft (HUMAN submits — never auto-submit)

**Deadline**: Nov 14 23:59 UTC (= Nov 15 00:59 Africa/Lagos). Target submit by **Nov 12** (2-day buffer).

## Form fields (map exactly)

| Field | Value |
|---|---|
| **projectName** | Verdict |
| **leadName** | Baheet Adeniji |
| **email** | adenijibaheet@gmail.com |
| **size** | TODO — Baheet fills team size as the form requires (solo / N) |
| **tracks** | `["privacy", "defi", "security", "open"]` |
| **nearAddress** | TODO — Baheet fills **his own NEAR mainnet account**. Do **not** invent or guess. |
| **projectUrl** | https://github.com/Baheet18/verdict |
| **demoUrl** | TODO — link after Baheet records the demo (see `docs/DEMO_VIDEO.md`) and/or posts testnet explorer links |
| **idea** | (paste the Idea block below) |

## Why (2 lines — put at top of idea)

Bloomberg (2026-05-26) reported that about nine wallets accounted for roughly half of UMA dispute votes on Polymarket; the Wall Street Journal (May 2026) reported that in about one in five disputes at least one voter had a stake in the outcome. Polymarket Protocol V2 (announced 2026-10-05) lets each market choose its oracle path. Verdict is a hardware-attested multi-model option for that slot — **not** a claim that any whale is corrupt.

## idea (body)

Verdict settles markets with three attested models (Qwen3.8-27B, DeepSeek-V4.1-Flash-1017, GLM-5.3-flash) on NEAR AI Cloud. Votes are EIP-191 signatures over `model:sha256(req):sha256(resp)`. The NEAR contract:

1. Exact request-key allowlist (blocks logit_bias / guided decoding) + rejects duplicate JSON keys and backslash-escaped object keys (`\u0074emperature`).
2. Requires `commit_evidence` (relayer/owner) after the market deadline, with `evidence_uri`, before any `submit_verdict`.
3. Binds header fields: market_id, round, rules_hash, prompt_template_hash, evidence_bundle_hash.
4. Maps each signer → one model; rejects replayed signed texts.

**Headline**: Clear markets auto-settled at ~$0.002, **107/107** agreed with UMA, hardware-attested; request-parameter steering blocked (relayer evidence choice remains — see Limits). Unclear markets escalate to humans, **0 invented answers**. TOO_EARLY is first-class (Bitquery **243/470**). Hand-curated answer-bearing disputed set: **8 settled / 7 agree UMA / 1 disagree / 0 invent**; same IDs thin evidence **8/8 escalate**. Visa disagreement documented in `docs/VISA_CASE.md` (neutral; never “UMA wrong”). Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.

Offline screener mode (`python scripts/offline_demo.py`) needs no API key.

## Tracks detail

- **privacy**: NEAR AI Cloud TEEs; TDX + NVIDIA attestation; allowlisted host-stack measurements.
- **defi**: prediction-market resolution primitive; bonded disputes; TOO_EARLY as a first-class round advance.
- **security**: on-chain request-key allowlist, duplicate/escaped JSON-key rejection, evidence commit before votes, relayer-only submit, replay protection, per-signer model binding.
- **open**: public repo, offline screener mode, published evidence bundles, scoreboard rebuilt from sanitized cache.

## Numbers (backed by committed scripts + fixtures)

- Post-cutoff sports: `docs/BENCHMARK.md` — 124 markets; 107 settled; 107 agree UMA; 0 disagree; ~$0.0022/market.
- UMA-disputed thin evidence: `docs/BENCHMARK_DISPUTED.md` — 34 markets; 0 settled; 34 escalate; 0 invent.
- Hand-curated answer-bearing disputed: `docs/BENCHMARK_DISPUTED_HAND.md` — 8 settled; 7 agree UMA; 1 disagree; 0 escalate; 0 invent; thin same IDs 8/8 escalate.
- Visa case: `docs/VISA_CASE.md`.
- Bitquery TOO_EARLY: 243 of 470 disputes (9 Sep–7 Oct 2026) — https://bitquery.io/investigations/who-decides-polymarket-disputes
- Rule authoring checklist: optional only; **no** dispute-prediction claim (control 27/34 vs 26/34).

## Limits (must stay in the submission)

- Model-serving container not covered by RTMR3 pin.
- Evidence fetched outside the TEE; registered relayer commits the hash.
- Owner registers signers / settles disputes.
- GPU↔TDX binding is the shared nonce only.
- “Unsteerable” ≠ trustless relayer.
- Testnet deploy requires the builder’s wallet (not agent-held keys).
- Curator column on hand set is **curator (AI)** unless Baheet personally verifies.

## Checklist before submit (human only)

- [ ] Push local round-7 commits to GitHub (explicit OK required)
- [ ] After push, set `evidence_uri` via `python scripts/evidence_uri.py <path> --sha <that_commit>` (never /master/, never invent SHA)
- [ ] Human signs testnet deploy on a **fresh** contract; paste tx links into README
- [ ] Fill **nearAddress** with Baheet’s mainnet account
- [ ] Record demo video from DEMO_VIDEO.md; set **demoUrl**
- [ ] Post X draft manually (plain text)
- [ ] Confirm no secrets in git
- [ ] **Baheet** submits the form — agents never submit
