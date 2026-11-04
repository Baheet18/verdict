# Verdict — TEE-attested prediction-market resolution on NEAR

## Trust labels
- **Verified**: TDX quote verifies (dcap-qvl), report_data = signing_address‖0*12‖our nonce, RTMR0-3 replay, NVIDIA NRAS JWT valid with our nonce, AND response signer ∈ attested addresses.
- **Signature valid · hardware check pending**: signature ok but hardware chain not fully verified.
- **UNVERIFIED**: third-party (Chutes) attestation format, no signing_address binding — no verified label.

## Evidence
Sources are fetched OUTSIDE the TEE, snapshotted (url, fetched_at, sha256, text) and embedded in the request. Bundles in `out/bench/*.json`; anyone can recompute sha256(request_raw) and check the signed text `model:sha256(req):sha256(resp)`.

## Outcomes
YES, NO, TOO_EARLY (votes cleared, market reopens), AMBIGUOUS (→ refunds if final).

## Panel
Qwen3.8-27B + DeepSeek-V4.1-Flash-1017 + Kimi-K2.6. Auto-propose only if all 3 agree; else Escalated.
Training cutoffs are unpublished; we use the DeepSeek snapshot date 2026-10-17 as split bound.

## Signer registration
Owner-only `register_signer(address, attestation_report_sha256)`, only after `scripts/hw_verify.py` passes for that node. Owner can `remove_signer`.

## Post-dispute policy
Disputes (bonded, inside window) and escalations are resolved by owner after re-running a larger attested panel off-chain on the same evidence bundle; overturned → bond refunded to disputer, upheld → bond added to winning pool.

## Known gaps
- compose_hash / MRTD not checked against a known-good allowlist.
- NRAS verification and PCCS collateral require network access.
- Owner is a trusted party for registration and dispute resolution.
- No deploy (build + unit tests only).
