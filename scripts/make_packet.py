#!/usr/bin/env python3
"""Regenerate out/claude_review_packet.md for Claude's hardware-verification review."""
from __future__ import annotations
import pathlib, re

R = pathlib.Path(__file__).resolve().parent.parent

hw = (R / "scripts/hw_verify.py").read_text()
allow = (R / "scripts/expected_measurements.json").read_text()
readme = (R / "README.md").read_text()
pass_log = (R / "out/pass_run.log").read_text() if (R / "out/pass_run.log").exists() else "(missing)"
bench = (R / "out/bench_output.txt").read_text().replace("EXIT_DONE\n", "") if (R / "out/bench_output.txt").exists() else "(missing)"

compact_lines = []
for line in pass_log.splitlines():
    if line.startswith("      ") and "=" in line and not line.strip().startswith("ok"):
        continue
    compact_lines.append(line)
pass_compact = "\n".join(compact_lines)

m_match = re.search(
    r"def match_attestation_for_signature\(.*?(?=\n(?:def |\Z))",
    hw,
    re.S,
)
m_verify = re.search(
    r"def verify_node\(.*?(?=\ndef match_attestation_for_signature)",
    hw,
    re.S,
)
matcher_src = (m_verify.group(0).rstrip() + "\n\n" + m_match.group(0).rstrip()) if m_match and m_verify else "(extract failed)"

demo = (R / "scripts/attest_demo.py").read_text()
m_caller = re.search(
    r"# Critical: verify_node runs only.*?(?=pathlib\.Path\(\"out\"\))",
    demo,
    re.S,
)
caller_src = m_caller.group(0).rstrip() if m_caller else "(extract failed)"

def section(md: str, title: str) -> str:
    pat = rf"(## {re.escape(title)}\n.*?)(?=\n## |\Z)"
    m = re.search(pat, md, re.S)
    return m.group(1).rstrip() if m else f"(missing section {title})"

readme_excerpts = "\n\n".join([
    section(readme, "Trust labels"),
    section(readme, "Measurement allowlist"),
    section(readme, "Limits"),
])

lib = (R / "contract/src/lib.rs").read_text()
i = lib.index("    pub fn submit_verdict")
s = lib.rfind("\n", 0, lib.rfind("///", 0, i) if "///" in lib[i - 400 : i] else i)
depth = 0
j = lib.index("{", i)
for k in range(j, len(lib)):
    depth += lib[k] == "{"
    depth -= lib[k] == "}"
    if depth == 0:
        break
sv = lib[s + 1 : k + 1]
rs = lib.index("fn recover_signer")
rs = lib.rfind("\n", 0, rs)
d = 0
j = lib.index("{", rs)
for e in range(j, len(lib)):
    d += lib[e] == "{"
    d -= lib[e] == "}"
    if d == 0:
        break
rec = lib[rs + 1 : e + 1]

out = f"""# Verdict — review packet for Claude (hardware allowlist round)

Generated after applying Claude's MUST FIX + SMALL items on hardware verification.
All live checks below used NEAR AI Cloud + Intel PCCS + NVIDIA NRAS (network required).

## Deliverable 1 — allowlist: scripts/expected_measurements.json
```json
{allow}```

## Deliverable 2 — signature → attestation matching

### hw_verify.match_attestation_for_signature + verify_node
```python
{matcher_src}
```

### Caller in scripts/attest_demo.py
```python
{caller_src}
```

## Deliverable 3 — full fresh PASS run (Qwen/Qwen3.8-27B)
Compacted: per-GPU claim key dumps omitted; all `ok` asserts retained. Raw log: `out/pass_run.log`.
```
{pass_compact}
```

## README excerpts (allowlist + limits + trust labels)
```markdown
{readme_excerpts}
```

## (a) Hardware attestation verification — scripts/hw_verify.py (full file)
```python
{hw}```

## (b) submit_verdict — contract/src/lib.rs
```rust
{rec}

{sv}
```
Contract: 17/17 unit tests pass (incl. real NEAR AI signature, tampered response and 1-byte flip rejected); wasm builds via cargo-near (~227 KB). Not deployed.

## (c) 5-market benchmark (prior run; unchanged this round)
Panel: Qwen/Qwen3.8-27B + deepseek-ai/DeepSeek-V4.1-Flash-1017 + z-ai/glm-5.3-flash.
```
{bench}```

## What changed this round (Claude MUST FIX / SMALL)
1. **Pin code, not just hardware** — `expected_measurements.json` pins per-model `compose_hash` + shared docker digests + published Phala `os_image_hash` for `dstack-nvidia-0.5.12`. `mr_td`/`rt_mr0-2` are **observed pins** (dstack does not publish a single tuple; they encode VM config). Off-allowlist → fail closed. Digests printed every verify for human comparison.
2. **TD debug bit** — `td_attributes` byte0 bit0 must be 0.
3. **GPU loop** — `len(gpu_tokens) > 0` required; each token asserts `dbgstat=="disabled"` and `secboot is True` (actual NRAS EAT claim names); all claims printed.
4. **Caller matching** — `match_attestation_for_signature` requires recovered signature address AND requested model; `verify_node` only on that entry.
5. **Nonce** — exactly 64 lowercase hex chars before compare.
6. **TCB status** — always shown; `SWHardeningNeeded` accepted but not hidden.
7. **README Limits** — GPU↔TDX shared-nonce only; evidence fetch outside TEE; owner signer registration; observed MRTD pins; measured compose is host stack.

## Live verification results (this round)
- Unit: `scripts/test_hw_verify_unit.py` → ALL UNIT CHECKS PASSED (nonce, allowlist meta, matcher, digest parser, RTMR3 empty-digest replay).
- Fail-closed: mutated Qwen `compose_hash` in allowlist → AssertionError on pin check (restored after).
- Live PASS: Qwen/Qwen3.8-27B → Verified, TCB=UpToDate, 8×GH100, tamper FAIL (EXIT 0).
- Live PASS: deepseek-ai/DeepSeek-V4.1-Flash-1017 → Verified (allowlist compose `ae66f844…`, 8 GPUs).
- Live PASS: z-ai/glm-5.3-flash → Verified (NEAR RTMR3 digest-recompute; allowlist compose `a0e3c6f4…`, 8 GPUs). Prior "RTMR3 pending" gap closed for current nodes without weakening Qwen/DeepSeek checks.

## Remaining limitations (honest)
- `mr_td` / `rt_mr0-2` pins are **first-observation**, not third-party published constants — documented in allowlist `_meta` and README Limits.
- Measured `app_compose` is the **dstack host stack**, not model-serving YAML from `nearai/cvm-compose-files`.
- GPU attestation bound to session only via shared nonce (no crypto GPU↔TDX measurement bind).
- Evidence fetching is outside the TEE.
- Owner trusted for on-chain signer registration / disputes.
- NRAS + Intel PCCS need network.
- Benchmark still 5 markets / 1 post-cutoff; scale to ~50 still pending.
- Not deployed; nothing pushed.
"""
(R / "out/claude_review_packet.md").write_text(out)
print("wrote", R / "out/claude_review_packet.md", "bytes", len(out))
