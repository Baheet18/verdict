# Verdict — TEE-attested prediction-market resolution on NEAR

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
- **Shared signing key across replicas**: Qwen's signing key `0x835ab53d…aa26` was observed on two VM shapes (64 vCPU / 8 GPU and 12 vCPU / 1 GPU) with different RTMR0; both RTMR0 values are pinned (`also_observed`), MRTD/RTMR1/RTMR2 are identical. A signature therefore proves "one of the attested replicas holding this key", not a specific VM.

## Request format (bound on-chain)
Two messages only: system = the versioned prompt (`sha256` = `prompt_template_hash` stored in the market) and user = `VERDICT/1 market_id=.. round=.. rules_hash=.. prompt_template_hash=.. evidence_bundle_hash=..\n<evidence>`. The contract re-derives the header byte-for-byte, checks `sha256(evidence)`, `temperature == 0` and `seed == 1114`, and parses the outcome strictly (whole content must be the JSON object, or one fenced JSON block; no substring search). See `scripts/verdict_request.py` and `contract/src/lib.rs`.

**Re-roll limit**: temperature 0 + a fixed seed make sampling deterministic *per request*, but the relayer that builds requests can still re-roll by changing evidence bytes (e.g. fetch time, source choice) before the first vote of a round, or by withholding votes it dislikes. The contract fixes the evidence hash per round at the first vote and rejects reused signatures, but it cannot stop a relayer that never submits. Backend batching (vLLM data/expert parallel) can also make "deterministic" outputs differ across calls.

## Replay protection
Each signed text `model:sha256(req):sha256(resp)` is stored in `used_signed`; a signature is accepted once, one vote per signer per round. A unanimous TOO_EARLY clears votes and increments `round`; the round number is in the signed header, so old-round responses cannot be replayed.

## Evidence
Sources are fetched OUTSIDE the TEE, snapshotted (url, fetched_at, sha256, text) and embedded in the request; integrity is the snapshot hash inside the signed request, not a TEE-side fetch. Sports statistics need 2+ independent sources; a single-source stat line (e.g. an all-zero ESPN line) is treated as missing data → AMBIGUOUS.

## Outcomes
YES, NO, TOO_EARLY (votes cleared, next round), AMBIGUOUS (→ refunds if final).

## Panel
Qwen3.8-27B + DeepSeek-V4.1-Flash-1017 + GLM-5.3-flash. Auto-propose only if all 3 agree; else Escalated. Training cutoffs are unpublished; method and sources in `docs/CUTOFFS.md` (upper bound: events after 2026-10-17).

## Benchmark
`scripts/bench_scale.py` (124 UMA-resolved Polymarket sports markets, all events after 2026-10-17, two independent sources each, cached/resumable). Results in `docs/BENCHMARK.md`. UMA's resolution is the reference label; a disagreement is reported as a disagreement, not as UMA being wrong.

## Measurement allowlist
`scripts/expected_measurements.json` pins per model: normalized compose hash (raw hash also recorded), docker image digests of the host stack, `os_image_hash` (Phala `dstack-nvidia-0.5.12`), `mr_td` + `rt_mr0..2` observed-and-pinned. Anything off-allowlist fails closed.

## Signer registration
Owner-only `register_signer(address, model, attestation_report_sha256)`, only after `scripts/hw_verify.py` passes for that node. The contract requires the signer's registered model to equal the request's `model`. Owner can `remove_signer`.

## Post-dispute policy
Disputes (bonded, inside window) and escalations are resolved by owner after re-running a larger attested panel off-chain on the same evidence bundle; overturned → bond refunded to disputer, upheld → bond added to winning pool.

## Motivation
Illia Polosukhin (NEAR) listed "AI benchmarking with private test sets. Submit harness (or new model in hosted privately in @near_ai) and evaluate on private tests sets and see results that are certified for given model and given hash of test set, without revealing any data." among build ideas on X, 2026-10-03 (https://x.com/ilblackdragon/status/2106269343881519444; full text read via the X API on 2026-11-05). Verdict applies the same pattern (certified result for a given model + given hash of inputs) to market resolution; it is not an endorsement by Illia or NEAR.

## Limits
- **GPU ↔ TDX binding** is only the shared client nonce; no cryptographic binding of GPU attestation to the TDX VM measurement.
- **Owner trust**: the owner registers/removes signers and settles disputes; hardware verification is an off-chain precondition.
- **MRTD / RTMR0-2 pins are observed**, not a published constant (they encode VM shape).
- **Model container not measured** (see above).
- **NRAS + Intel PCCS** need network access.
- No deploy yet (build + unit tests only).

## Demo
```bash
source .venv/bin/activate
python scripts/attest_demo.py Qwen/Qwen3.8-27B   # expect PASS then tamper FAIL
```
