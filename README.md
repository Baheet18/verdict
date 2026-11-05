# Verdict — TEE-attested prediction-market resolution on NEAR

## Trust labels
- **Verified**: TDX quote verifies (dcap-qvl), report_data = signing_address‖0*12‖our nonce, TD debug bit clear, RTMR0-3 replay (NEAR RTMR3 digest-recompute), NVIDIA NRAS JWT valid with our nonce + `dbgstat=disabled` + `secboot=true`, measurements on `scripts/expected_measurements.json` allowlist, AND response signer matches the verified attestation entry for that model.
- **Signature valid · hardware check pending**: signature ok but hardware/allowlist chain not fully verified.
- **UNVERIFIED**: third-party (Chutes) attestation format, no signing-address binding — no verified label.

TCB status from Intel DCAP is always printed. `UpToDate` preferred; `SWHardeningNeeded` / `ConfigurationAndSWHardeningNeeded` are accepted by policy but never hidden.

## Evidence
Sources are fetched OUTSIDE the TEE, snapshotted (url, fetched_at, sha256, text) and embedded in the request. Bundles in `out/bench/*.json`; anyone can recompute sha256(request_raw) and check the signed text `model:sha256(req):sha256(resp)`.

## Outcomes
YES, NO, TOO_EARLY (votes cleared, market reopens), AMBIGUOUS (→ refunds if final).

## Panel
Qwen3.8-27B + DeepSeek-V4.1-Flash-1017 + GLM-5.3-flash. Auto-propose only if all 3 agree; else Escalated.
Training cutoffs are unpublished; we use the DeepSeek snapshot date 2026-10-17 as split bound.

## Measurement allowlist
`scripts/expected_measurements.json` pins per model:
- `compose_hash` and docker image digests parsed from measured `app_compose` (printed on every verify for human comparison against published NEAR AI / dstack images).
- `os_image_hash` against Phala's published `dstack-nvidia-0.5.12` value.
- `mr_td` + `rt_mr0..2` observed-and-pinned (see Limits — these are not a single published constant per OS version).

Anything off-allowlist fails closed.

## Signer registration
Owner-only `register_signer(address, attestation_report_sha256)`, only after `scripts/hw_verify.py` passes for that node. Owner can `remove_signer`.

## Post-dispute policy
Disputes (bonded, inside window) and escalations are resolved by owner after re-running a larger attested panel off-chain on the same evidence bundle; overturned → bond refunded to disputer, upheld → bond added to winning pool.

## Limits
- **GPU ↔ TDX binding**: the GPU is tied to the session only by the shared client nonce in both the TDX `report_data` and the NVIDIA evidence / NRAS `eat_nonce`. There is no cryptographic binding of the GPU attestation to the specific TDX VM measurement (known limit of NVIDIA Confidential Computing attestation).
- **Evidence fetching**: source snapshots are fetched outside the TEE by the relayer; integrity is the snapshot sha256 embedded inside the signed request, not a TEE-side fetch.
- **Owner trust**: the contract owner registers / removes signers and settles disputes. Hardware verification is a precondition the owner is supposed to run, not an on-chain enforcement of the allowlist.
- **MRTD / RTMR0-2 pins are observed**: dstack publishes `os_image_hash` per image (we pin the Phala `dstack-nvidia-0.5.12` value), but MRTD and RTMR0-2 also encode VM CPU / RAM / device config, so there is no single published tuple for a version. We record what NEAR AI Cloud nodes present, pin those, and fail closed on drift. Independent precomputation would need the exact VmConfig + `dstack-mr`.
- **Measured compose is the host stack**: `app_compose` / `compose_hash` cover the dstack host services (compose-manager, otel, certbot), not the model-serving YAML from `github.com/nearai/cvm-compose-files`. Model image provenance is a separate policy (NEAR image-provenance docs).
- **NRAS + Intel PCCS** need network access; not fully offline-verifiable.
- No deploy yet (build + unit tests only).

## Demo
```bash
source .venv/bin/activate
python scripts/attest_demo.py Qwen/Qwen3.8-27B   # expect PASS then tamper FAIL, label Verified
```
