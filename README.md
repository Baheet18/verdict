# Verdict — TEE-attested prediction-market resolution on NEAR

## Why this exists
Bloomberg (2026-05-26) reported that about nine wallets accounted for roughly half of UMA dispute votes on Polymarket; the Wall Street Journal (May 2026) reported that in about one in five disputes at least one voter had a stake in the outcome. Polymarket Protocol V2 (announced 2026-10-05) lets each market choose its oracle path. Verdict is a hardware-attested, multi-model option for that slot — not a claim that any whale is corrupt.

## Pitch headline
**Clear markets:** auto-settled at ~$0.002/market, **107/107** agreed with UMA (post-cutoff sports), hardware-attested. Request parameters are allowlisted on-chain (no `logit_bias` / guided decoding); a registered relayer still chooses and commits evidence — that remaining trust is listed under Limits, so “unsteerable” here means *request-parameter* steering is blocked, not that the relayer is trustless.
**Unclear markets:** escalated to humans, **0 invented answers**.
**TOO_EARLY** is first-class (Bitquery: **243 of 470** Polymarket/UMA disputes in 9 Sep–7 Oct 2026 ended “too early” — https://bitquery.io/investigations/who-decides-polymarket-disputes).
**Hand-curated disputed (answer-bearing sources):** 8 markets · settled 8 · agree UMA 7 · disagree UMA 1 (Visa 5648379: under review — a more precise 9.5% figure may support UMA's YES; see docs/VISA_CASE.md) · escalated 0 · invented 0. Same IDs with thin rules-only evidence: 8/8 escalated. See `docs/BENCHMARK_DISPUTED_HAND.md`.


## Trust labels
- **Hardware + host stack verified**: TDX quote verifies (dcap-qvl), report_data = signing_address‖0*12‖our nonce, TD debug bit clear, RTMR0-3 replay (NEAR RTMR3 digest-recompute), NVIDIA NRAS JWT valid with our nonce + `dbgstat=disabled` + `secboot=true`, measurements on `scripts/expected_measurements.json` allowlist, AND the response signer matches the verified attestation entry for that model. **This label does NOT cover the model-serving container** (see "What RTMR3 covers").
- **Signature valid · hardware check pending**: signature ok but the hardware/allowlist chain did not fully verify.
- **UNVERIFIED**: signature missing/invalid, or third-party attestation format with no signing-address binding.

TCB status from Intel DCAP is always printed. `UpToDate` preferred; `SWHardeningNeeded` / `ConfigurationAndSWHardeningNeeded` are accepted by policy but never hidden.

## What RTMR3 covers
Replayed RTMR3 events (identical names on all 3 panel models, observed 2026-11-05 WAT): `system-preparing, app-id, compose-hash, instance-id, boot-mr-done, mr-kms, os-image-hash, key-provider, storage-fs, system-ready` (GLM additionally logs 2× `nearai-replica-report-key-v1`).
- `os_image_hash` is taken from the **replayed `os-image-hash` RTMR3 event** and must equal the pin and the TCB info value.
- `compose-hash` covers the dstack **host stack** compose (compose-manager, otel, certbot). The raw hash must equal MRCONFIGID and the RTMR3 event. Because each replica's compose differs only in `INSTANCE_LABEL=gpuN`, we pin a **normalized** hash (exactly one `- INSTANCE_LABEL=gpu\d+` line, removed) per model.
- **Not covered**: the model-serving container (vLLM image + weights) is started by compose-manager at runtime from `github.com/nearai/cvm-compose-files`; compose-manager's action log is in the event log and checked to replay, but many compose files run concurrently on a node so it cannot be pinned to one model image. Model/weights provenance therefore rests on NEAR AI's image-provenance policy, not on our measurement check.
- **Shared signing key across replicas**: Qwen's signing key `0x835ab53d…aa26` was observed on two VM shapes (64 vCPU / 8 GPU and 12 vCPU / 1 GPU) with different RTMR0; both RTMR0 values are pinned (`also_observed`), MRTD/RTMR1/RTMR2 are identical. A signature therefore proves "one of the attested replicas holding this key", not a specific VM. Label **Hardware + host stack verified** only if EVERY observed attestation entry for that key+model passes; otherwise all its votes are downgraded.

## Request format (bound on-chain)
Two messages only: system = the versioned prompt (`sha256` = `prompt_template_hash` stored in the market) and user = `VERDICT/1 market_id=.. round=.. rules_hash=.. prompt_template_hash=.. evidence_bundle_hash=..\n<evidence>`. The contract re-derives the header byte-for-byte, checks `sha256(evidence)`, `temperature == 0` and `seed == 1114`, and parses the outcome strictly (whole content must be the JSON object, or one fenced JSON block; no substring search). See `scripts/verdict_request.py` and `contract/src/lib.rs`.

**Re-roll limit**: temperature 0 + a fixed seed make sampling deterministic *per request*, but the relayer that builds requests can still re-roll by changing evidence bytes (e.g. fetch time, source choice) before the first vote of a round, or by withholding votes it dislikes. The contract fixes the evidence hash per round at the first vote and rejects reused signatures, but it cannot stop a relayer that never submits. Backend batching (vLLM data/expert parallel) can also make "deterministic" outputs differ across calls.

## Replay protection
Each signed text `model:sha256(req):sha256(resp)` is stored in `used_signed`; a signature is accepted once, one vote per signer per round. A unanimous TOO_EARLY clears votes and increments `round`; the round number is in the signed header, so old-round responses cannot be replayed.

## Evidence
Sources are fetched OUTSIDE the TEE, snapshotted (url, fetched_at, sha256, text) and embedded in the request; integrity is the snapshot hash inside the signed request, not a TEE-side fetch. Sports statistics need 2+ independent sources; a single-source stat line (e.g. an all-zero ESPN line) is treated as missing data → AMBIGUOUS.

## Evidence commit
`commit_evidence(market_id, round, evidence_bundle_hash, evidence_uri)` is callable only by a registered relayer or the owner, and only after `block_timestamp >= deadline_ns`. Owner may replace the commit while votes are empty; once any vote is cast the commit is frozen. `submit_verdict` is also relayer/owner-only (defense in depth — same trust surface as evidence choice) and requires the request's evidence hash to equal the committed hash. Bundles are published content-addressed under `fixtures/evidence/<sha256>.json` (see `MANIFEST.json`). **`evidence_uri` must be a commit-SHA permalink** (`python scripts/evidence_uri.py <path> --sha <commit>` after a human-approved push that includes the file) — never `/master/` / `/main/`, and never a fabricated SHA. Until that push, URIs stay `PENDING_COMMIT_SHA://…` (origin/master tip `f96954b` lacks these evidence files).

## Request-key allowlist (round 5)
`check_request` accepts **exactly** top-level keys `{model, messages, temperature, seed}` and each message `{role, content}`. Extra keys (`logit_bias`, `guided_choice`, `guided_regex`, `response_format`, `structured_outputs`, `tools`, `tool_choice`, `stop`, `n`, `top_p`, …) are rejected so a committed evidence+header cannot be steered into a forced YES via sampling / guided-decoding params. `max_tokens` is intentionally omitted. Duplicate JSON object keys are rejected; **any backslash inside an object key** is also rejected (blocks `\u0074emperature`-style escapes that serde would normalize onto an allowlisted key).

## Disputed-market evidence
- **Thin / JS-shell fetches** (34 markets, and an earlier “curated” attempt): panel abstained — expected when snapshots lack the resolution fact. See `docs/BENCHMARK_DISPUTED.md` / `docs/BENCHMARK_DISPUTED_CURATED.md`.
- **Hand-curated answer-bearing set (round 6):** 8 markets with curator-(AI)-extracted official/Wikipedia + 2 report texts, `answer_present:true` checked by curator (AI) **before** the panel (Baheet has not personally verified each row), archive.org noted only when reachable. Panel: **8 settled / 7 agree UMA / 1 disagree UMA / 0 escalate / 0 invented**. Same IDs on thin evidence: 8/8 escalate. Column name: **answer-bearing sources**. Details: `docs/BENCHMARK_DISPUTED_HAND.md` + `fixtures/evidence_hand/`. Visa 5648379: under review — a more precise 9.5% figure may support UMA's YES (`docs/VISA_CASE.md`). Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.

## Rule authoring checklist (optional)
`scripts/rule_linter.py` is an **optional rule-authoring checklist** (regex/heuristics, no model). It surfaces vague wording, missing sources, and deadline gaps while drafting rules.
It does **not** predict UMA disputes and is **not** marketed as a dispute screener. A matched control set (27/34 disputed vs 26/34 non-disputed) showed no useful gap; that comparison is kept only as a negative result in `docs/BENCHMARK_DISPUTED.md`. Do not retune the heuristics on that same set.
Digest drift watch: `python scripts/check_new_digest.py` prints Docker Hub allowlist DIFF only (never auto-pins).

## Offline / screener mode
See `docs/SETUP.md`. `python scripts/offline_demo.py` runs `cargo test`, verifies cached real signatures, rebuilds `scoreboard/index.html` from `fixtures/scoreboard_cache/` — no API key.

## Outcomes
YES, NO, TOO_EARLY (votes cleared, next round), AMBIGUOUS (→ refunds if final).

## Panel
Qwen3.8-27B + DeepSeek-V4.1-Flash-1017 + GLM-5.3-flash. Auto-propose only if all 3 agree; else Escalated. Training cutoffs are unpublished; method and sources in `docs/CUTOFFS.md` (upper bound: events after 2026-10-17).

## Benchmark
`scripts/bench_scale.py` (124 UMA-resolved Polymarket sports markets, all events after 2026-10-17, two independent sources each, cached/resumable). Results in `docs/BENCHMARK.md`. UMA's resolution is the reference label; a disagreement is reported as a disagreement, not as UMA being wrong.

## Re-attest (2026-11-05 WAT)
`python scripts/reattest_today.py` → `out/reattest/reattest.json` (also copied to `fixtures/benchmark_summaries/reattest_today.json`).
All 3 panel models: **Hardware + host stack verified**. Observed compose-manager digests still `6e035c8f…` + launcher `079a14b4…`. Docker Hub `a0c07308…` was **not** observed on nodes; it remains **unpinned** pending a future observation + explicit human approval to pin (never auto-pin).

## Measurement allowlist
`scripts/expected_measurements.json` pins per model: normalized compose hash (raw hash also recorded), docker image digests of the host stack, `os_image_hash` (Phala `dstack-nvidia-0.5.12`), `mr_td` + `rt_mr0..2` observed-and-pinned. Anything off-allowlist fails closed.

## Signer registration
Owner-only `register_signer(address, model, attestation_report_sha256)`, only after `scripts/hw_verify.py` passes for that node. The contract requires the signer's registered model to equal the request's `model`. Owner can `remove_signer`.

## Post-dispute policy
Each market sets an immutable `escalation_owner` (`AccountId`) at `create_market` — the integrating app’s team, multisig, DAO, or jury contract. Only that account may call `resolve_dispute` for Escalated / Disputed status on **that** market. The protocol `owner` keeps signer/relayer administration but **cannot decide outcomes unless explicitly selected** as that market’s `escalation_owner`. One market’s escalation owner has no authority over another. Typical flow: escalation owner re-runs a larger attested panel off-chain on the same evidence bundle, then posts the result; overturned → bond refunded to disputer, upheld → bond added to winning pool.

## Motivation
Illia Polosukhin (NEAR) listed "AI benchmarking with private test sets. Submit harness (or new model in hosted privately in @near_ai) and evaluate on private tests sets and see results that are certified for given model and given hash of test set, without revealing any data." among build ideas on X, 2026-10-03 (https://x.com/ilblackdragon/status/2106269343881519444; full text read via the X API on 2026-11-05). Verdict applies the same pattern (certified result for a given model + given hash of inputs) to market resolution; it is not an endorsement by Illia or NEAR.

## Limits
- **GPU ↔ TDX binding** is only the shared client nonce; no cryptographic binding of GPU attestation to the TDX VM measurement.
- **Owner / relayer / escalation trust**: the protocol owner registers/removes signers and relayers; a registered relayer (or owner) must `commit_evidence` before votes; each market’s `escalation_owner` (chosen at creation) settles that market’s disputes/escalations. Hardware verification is an off-chain precondition for registration.
- **Key-management service (dstack KMS)**: RTMR3 `mr-kms` and `key-provider` events are pinned, but signing keys are issued by this KMS and may be shared across replicas. A signature proves "one of the attested replicas holding this key", not a specific VM. See shared-signer policy in `scripts/hw_verify.py::verify_signer_replicas`.
- **MRTD / RTMR0-2 pins are observed**, not a published constant (they encode VM shape).
- **Model container not measured** (see above).
- **NRAS + Intel PCCS** need network access.
- **Testnet deploy**: `baheet.testnet` is the **old prototype** (universal protocol-owner dispute fallback). The per-market `escalation_owner` API is a breaking change — deploy to a **fresh** contract account after review; do not expect in-place upgrade of the prototype. No keys in this repo. See `deploy/TESTNET.md`.

## Demo
```bash
source .venv/bin/activate
python scripts/attest_demo.py Qwen/Qwen3.8-27B   # expect PASS then tamper FAIL
```


## Testnet deploy
Wasm builds locally (`cargo near build non-reproducible-wasm` → `contract/target/near/verdict.wasm`, ~246 KB).
**Demo market (round 6):** post-cutoff NFL *Saints vs. Giants* (Polymarket 4329887) with live panel votes in `contract/testdata/real_panel.json` (unanimous YES, hardware-verified). `dispute_bond` = **1 NEAR**. Evidence file prepared at `fixtures/evidence/<sha>.json`; `evidence_uri` is the GitHub raw URL and **404s until a human-approved push**.
**Old prototype (do not treat as current trust model):** `baheet.testnet`, initialized with owner `baheet.testnet` on 2026-11-05 (universal owner dispute fallback).
[Deploy + init transaction](https://explorer.testnet.near.org/transactions/4koasWRtwm9F3ZTh9dN86qSu2oTgA8iDHg5rs7uVRLFT)

**Next deploy:** use a **fresh** contract account for the per-market `escalation_owner` API (storage/ABI break vs the prototype). Pass `escalation_owner` in `create_market`. No redeploy until after review.
Next signed steps: register signers → create market (with `escalation_owner`) → commit_evidence → three submit_verdict → optional dispute resolved by that market’s escalation owner. Steps: `deploy/TESTNET.md`. Args: `python scripts/deploy_prepare.py`.
