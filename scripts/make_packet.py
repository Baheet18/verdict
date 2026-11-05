#!/usr/bin/env python3
"""Regenerate out/claude_review_packet.md (Claude review round 3)."""
import pathlib, re, json
R = pathlib.Path(__file__).resolve().parent.parent
rd = lambda p: (R / p).read_text() if (R / p).exists() else f"(missing {p})"
lib = rd("contract/src/lib.rs")

def rust_fn(name):
    i = lib.index(f"fn {name}"); s = lib.rfind("\n", 0, i)
    while True:  # include preceding doc comments / attributes
        p = lib.rfind("\n", 0, s)
        if lib[p + 1:s].strip().startswith(("///", "#[")): s = p
        else: break
    j = lib.index("{", i); d = 0
    for k in range(j, len(lib)):
        d += lib[k] == "{"; d -= lib[k] == "}"
        if d == 0: return lib[s + 1:k + 1]

def py_fn(path, name):
    src = rd(path); m = re.search(rf"^def {name}\(.*?(?=^\S)", src, re.S | re.M); return m.group(0).rstrip() if m else "(missing)"

tests = rd("contract/src/tests.rs")
test_names = re.findall(r"#\[test\]\s*(?:#\[should_panic[^\]]*\]\s*)?fn (\w+)", tests)
bench = rd("docs/BENCHMARK.md")
out = f"""# Verdict — review packet for Claude (round 3)

Local commit only; nothing pushed or deployed. All times WAT (UTC+1).

## 1. submit_verdict + request binding (contract/src/lib.rs)
```rust
{rust_fn("canonical_header")}

{rust_fn("check_request")}

{rust_fn("submit_verdict")}
```

## 2. parse_outcome — strict, structured (Rust + Python mirror)
```rust
{rust_fn("parse_outcome")}
```
```python
{py_fn("scripts/verdict_request.py", "parse_outcome")}
```

## 3. Tests
`cargo test`: {len(test_names)} tests.
```
{rd("out/tests/cargo_test.log")}
```
Test names: {", ".join(test_names)}

Real-signature fixture `contract/testdata/real_panel.json`: live NEAR AI responses from all 3 models (Qwen3.8-27B, DeepSeek-V4.1-Flash-1017, GLM-5.3-flash), each signer registered to its own model; tests cover recovery + settle, signer-registered-for-different-model, tampered response, replay on another market. Synthetic vectors (`contract/testdata/vectors.json`, test-only keys) cover header/seed/temperature/round/replay cases.

Python: `scripts/test_verdict_request.py` → {rd("out/tests/test_verdict_request.log").strip().splitlines()[-1]}; `scripts/test_hw_verify_unit.py` → {rd("out/tests/test_hw_verify_unit.log").strip().splitlines()[-1]}. `cargo check --target wasm32-unknown-unknown` OK.

## 4. RTMR3 event list (live, all 3 models)
`system-preparing, app-id, compose-hash, instance-id, boot-mr-done, mr-kms, os-image-hash, key-provider, storage-fs, system-ready`; GLM additionally `nearai-replica-report-key-v1` ×2.
- `os_image_hash` is read from the replayed `os-image-hash` event (must equal TCB info + pin).
- Normalized compose hash (single `- INSTANCE_LABEL=gpuN` line removed): Qwen `e3a78905…5322`; DeepSeek and GLM `cac94e21…abff`. Raw hash still bound to MRCONFIGID and the RTMR3 `compose-hash` event.
- Label: **Hardware + host stack verified**. The model-serving container is NOT covered (README "What RTMR3 covers").
- New finding: Qwen key `0x835ab53d…aa26` served by two VM shapes (64 vCPU/8 GPU and 12 vCPU/1 GPU), different RTMR0; both pinned via `also_observed`.

### scripts/hw_verify.py (normalization)
```python
{py_fn("scripts/hw_verify.py", "normalize_app_compose")}

{py_fn("scripts/hw_verify.py", "normalized_compose_hash")}
```

## 5. Scaled benchmark
{bench}

## 6. README (full)
```markdown
{rd("README.md")}```

## 7. Cutoffs
{rd("docs/CUTOFFS.md")}
"""
(R / "out/claude_review_packet.md").write_text(out); print("wrote out/claude_review_packet.md", len(out))
