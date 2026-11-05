import re, pathlib
R = pathlib.Path(__file__).resolve().parent.parent
hw = (R/"scripts/hw_verify.py").read_text()
lib = (R/"contract/src/lib.rs").read_text()
i = lib.index("    pub fn submit_verdict"); s = lib.rfind("\n", 0, lib.rfind("///", 0, i) if "///" in lib[i-400:i] else i)
depth = 0; j = lib.index("{", i)
for k in range(j, len(lib)):
    depth += lib[k] == "{"; depth -= lib[k] == "}"
    if depth == 0: break
sv = lib[s+1:k+1]
rs = lib.index("fn recover_signer"); rs = lib.rfind("\n", 0, rs)
d=0; j=lib.index("{", rs)
for e in range(j, len(lib)):
    d += lib[e]=="{"; d -= lib[e]=="}"
    if d==0: break
rec = lib[rs+1:e+1]
bench = (R/"out/bench_output.txt").read_text().replace("EXIT_DONE\n","")
out = f"""# Verdict — review packet for Claude

## (a) Hardware attestation verification — scripts/hw_verify.py (full file)
```python
{hw}```

## (b) submit_verdict — contract/src/lib.rs
```rust
{rec}

{sv}
```
Contract: 17/17 unit tests pass (incl. real NEAR AI signature, tampered response and 1-byte flip rejected); wasm builds via cargo-near (~227 KB). Not deployed.

## (c) 5-market benchmark (final run)
Panel: Qwen/Qwen3.8-27B + deepseek-ai/DeepSeek-V4.1-Flash-1017 + z-ai/glm-5.3-flash (3 families, all NEAR-native TEE; Kimi dropped — Chutes 3p attestation has no signing-address binding).
max_tokens=12000, read timeout 900s, 4 retries w/ exponential backoff.
```
{bench}```

## Gaps
- GLM-5.3-flash: TDX quote, report_data(address+nonce), pubkey→address, RTMR0-2, and NVIDIA NRAS (8×GH100, our nonce) all PASS, but RTMR3 does not replay from either published event log → labelled "Signature valid · hardware check pending", never "Verified". Panel is therefore 2/3 Verified.
- compose_hash / MRTD not pinned to a known-good allowlist (we check consistency, not identity of the image).
- NRAS + Intel PCCS collateral need network; not verifiable fully offline.
- Training cutoffs unpublished; split uses DeepSeek snapshot date 2026-10-17 as upper bound. Only 1 post-cutoff market so far — need ~50 markets with many post-cutoff.
- Sources fetched outside the TEE; integrity = snapshot sha256 embedded in signed request (bundles in out/bench/).
- Owner is trusted for signer registration and dispute resolution (documented).
- Illia "private test sets" quote: source link not yet found — do not quote.
"""
(R/"out/claude_review_packet.md").write_text(out); print("ok", len(out))
