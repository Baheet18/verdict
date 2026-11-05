#!/usr/bin/env python3
"""Regenerate docs/claude_review_packet_round5.md."""
import pathlib, re, json
R = pathlib.Path(__file__).resolve().parent.parent
rd = lambda p: (R / p).read_text() if (R / p).exists() else f"(missing {p})"
lib = rd("contract/src/lib.rs")

def rust_fn(name):
    m = re.search(rf"(?m)^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?fn[ \t]+{re.escape(name)}\b", lib)
    if not m:
        return f"(missing fn {name})"
    i = m.start(); s = i
    while True:
        p = lib.rfind("\n", 0, s)
        if p < 0: break
        line = lib[p + 1:s].strip()
        if line.startswith(("///", "#[", "//!")) or line == "":
            s = p; continue
        break
    j = lib.index("{", i); d = 0; k = j; in_str = None
    while k < len(lib):
        ch = lib[k]
        if in_str:
            if in_str in ("'", '"') and ch == "\\":
                k += 2; continue
            if ch == in_str: in_str = None
            k += 1; continue
        if ch == "/" and k + 1 < len(lib) and lib[k + 1] == "/":
            nl = lib.find("\n", k); k = len(lib) if nl < 0 else nl + 1; continue
        if ch == "/" and k + 1 < len(lib) and lib[k + 1] == "*":
            end = lib.find("*/", k + 2); k = len(lib) if end < 0 else end + 2; continue
        if ch in ("'", '"'):
            in_str = ch; k += 1; continue
        if ch == "{": d += 1
        elif ch == "}":
            d -= 1
            if d == 0: return lib[s:k + 1].lstrip("\n")
        k += 1
    return "(unclosed)"

def py_fn(path, name):
    """Extract top-level def; lookahead accepts next top-level OR EOF."""
    src = rd(path)
    m = re.search(rf"^def {name}\(.*?(?=^\S|\Z)", src, re.S | re.M)
    return m.group(0).rstrip() if m else "(missing)"

tests = rd("contract/src/tests.rs")
test_names = re.findall(r"#\[test\]\s*(?:#\[should_panic[^\]]*\]\s*)?fn (\w+)", tests)
cargo_log = rd("out/tests/cargo_test.log")
offline_log = rd("out/offline_clean_clone/offline_demo.log")
lint_ctrl = rd("out/lint_control/summary.json")
curated = rd("docs/BENCHMARK_DISPUTED_CURATED.md")
disputed = rd("docs/BENCHMARK_DISPUTED.md")
bench = rd("docs/BENCHMARK.md")
deploy_note = rd("deploy/TESTNET.md")
deploy_prep = rd("out/deploy_prepare/output.txt")
digest_diff = rd("out/check_new_digest/diff.json")

cr = rust_fn("check_request")
ce = rust_fn("commit_evidence")
sv = rust_fn("submit_verdict")
olr = rust_fn("only_relayer")
po = rust_fn("parse_outcome")
vsr = py_fn("scripts/hw_verify.py", "verify_signer_replicas")
assert "forbidden request key" in cr
assert "evidence_uri" in ce
assert "only_relayer" in sv
assert vsr != "(missing)" and "def verify_signer_replicas" in vsr

out = f"""# Verdict — review packet for Claude (round 5)

Local commits only; GitHub remote not updated this round. Times WAT (UTC+1).
Self-review applied: permissionless influence (request keys + evidence + submit), replica labels, offline reproducibility, committed numbers only.

## 1. CRITICAL — request-parameter steering (exact key allowlist)
```rust
{cr}
```
Top-level keys MUST be exactly `{{model, messages, temperature, seed}}` (max_tokens omitted by policy).
Each message MUST be exactly `{{role, content}}`. User content = canonical header + `\\n` + evidence.
Forbidden-key rejection tests (one each): logit_bias, guided_choice, guided_regex, response_format, structured_outputs, tools, tool_choice, stop, n, top_p (+ forbidden message key name).
Defense in depth: `submit_verdict` is relayer/owner only (same trust as `commit_evidence`).
```rust
{olr}
```

## 2. commit_evidence — deadline + owner replace + evidence_uri
```rust
{ce}
```
```rust
{sv}
```

## 3. parse_outcome (structured) + verify_signer_replicas (was missing in packet)
```rust
{po}
```
```python
{vsr}
```

## 4. Tests
`cargo test`: {len(test_names)} tests.
```
{cargo_log}
```

## 5. Linter control set (matched non-disputed)
```json
{lint_ctrl}
```
Selection written to `out/lint_control/selection.json` BEFORE lint outcomes. Gap < 0.10 → **do not** use “flagged X/34 disputed vs Y/34 non-disputed” as a headline claim.

## 6. Disputed benchmarks
### Thin evidence (rules-only) — prior
{disputed}

### Curated multi-source subset
{curated}

## 7. check_new_digest (DIFF only; no auto-allowlist)
```json
{digest_diff[:4000]}
```

## 8. Offline clean-clone demo
```
{offline_log}
```

## 9. Deploy prepare (no keys; human signs)
{deploy_note}
```
{deploy_prep}
```

## 10. Status deliverables
- Scoreboard: `scoreboard/index.html` (from `fixtures/scoreboard_cache/`)
- Demo video script: `docs/DEMO_VIDEO.md`
- X post draft: `docs/X_POST_DRAFT.md` (human posts)
- Submission draft: `docs/SUBMISSION_DRAFT.md` (human submits)
- Post-cutoff sports: see BENCHMARK.md below
{bench}
"""
path = R / "docs" / "claude_review_packet_round5.md"
path.write_text(out)
print("wrote", path, "bytes", len(out))
print("verify_signer_replicas lines", vsr.count(chr(10))+1, "starts", vsr.splitlines()[0][:60])
