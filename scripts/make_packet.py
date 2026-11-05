#!/usr/bin/env python3
"""Regenerate out/claude_review_packet.md (Claude review round 4)."""
import pathlib, re
R = pathlib.Path(__file__).resolve().parent.parent
rd = lambda p: (R / p).read_text() if (R / p).exists() else f"(missing {p})"
lib = rd("contract/src/lib.rs")

def rust_fn(name):
    """Extract a Rust fn by name; brace-match skipping string/char/line comments."""
    m = re.search(rf"(?m)^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:async[ \t]+)?fn[ \t]+{re.escape(name)}\b", lib)
    if not m:
        return f"(missing fn {name})"
    i = m.start()
    s = i
    while True:
        p = lib.rfind("\n", 0, s)
        if p < 0:
            break
        line = lib[p + 1:s].strip()
        if line.startswith(("///", "#[", "//!")) or line == "":
            s = p
            continue
        break
    # find opening brace of body
    j = lib.index("{", i)
    d = 0
    k = j
    in_str = None  # ', ", or r#"..."#
    while k < len(lib):
        ch = lib[k]
        if in_str:
            if in_str == "'" and ch == "\\":
                k += 2
                continue
            if in_str == '"' and ch == "\\":
                k += 2
                continue
            if ch == in_str:
                in_str = None
            k += 1
            continue
        # line comment
        if ch == "/" and k + 1 < len(lib) and lib[k + 1] == "/":
            nl = lib.find("\n", k)
            k = len(lib) if nl < 0 else nl + 1
            continue
        if ch == "/" and k + 1 < len(lib) and lib[k + 1] == "*":
            end = lib.find("*/", k + 2)
            k = len(lib) if end < 0 else end + 2
            continue
        if ch in ("'", '"'):
            in_str = ch
            k += 1
            continue
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
            if d == 0:
                return lib[s:k + 1].lstrip("\n")
        k += 1
    return "(unclosed)"

def py_fn(path, name):
    """Extract a top-level def. Lookahead accepts next top-level OR end-of-file
    (EOF used to print '(missing)' when the function was the last def in the file)."""
    src = rd(path)
    m = re.search(rf"^def {name}\(.*?(?=^\S|\Z)", src, re.S | re.M)
    return m.group(0).rstrip() if m else "(missing)"

tests = rd("contract/src/tests.rs")
test_names = re.findall(r"#\[test\]\s*(?:#\[should_panic[^\]]*\]\s*)?fn (\w+)", tests)
bench = rd("docs/BENCHMARK.md")
disputed = rd("docs/BENCHMARK_DISPUTED.md")
deploy_note = rd("deploy/TESTNET.md")
cargo_log = rd("out/tests/cargo_test.log")

po = rust_fn("parse_outcome")
ce = rust_fn("commit_evidence")
sv = rust_fn("submit_verdict")
rr = rust_fn("register_relayer")
assert "pub fn parse_outcome" in po, po[:200]
assert "pub fn commit_evidence" in ce
assert "pub fn submit_verdict" in sv

out = f"""# Verdict — review packet for Claude (round 4)

Local commits only; GitHub remote not updated this round. Times WAT (UTC+1).

## 1. Evidence lock — commit_evidence + submit_verdict
```rust
{rr}

{ce}

{sv}
```

## 2. parse_outcome — strict, structured (Rust; was previously printed as None)
```rust
{po}
```
```python
{py_fn("scripts/verdict_request.py", "parse_outcome")}
```

## 3. Tests
`cargo test`: {len(test_names)} tests.
Names: {", ".join(test_names)}
```
{cargo_log}
```
Evidence-lock tests: `vote_before_commit_rejected`, `vote_with_uncommitted_evidence_rejected`, `non_relayer_commit_rejected`, `registered_relayer_can_commit`, `double_commit_rejected`, plus prior binding/replay/per-signer-model regressions.

## 4. Shared-signer / replica policy
```python
{py_fn("scripts/hw_verify.py", "verify_signer_replicas")}
```
Label **Hardware + host stack verified** only if EVERY observed attestation entry for that signing_address+model passes. Otherwise all votes from that key are downgraded to `Signature valid · hardware check pending`.
mr-kms + key-provider RTMR3 events are pinned in `scripts/expected_measurements.json`.
compose-manager digests pinned: `6e035c8f…` and `a320bb48…756a` (Docker Hub nearaidev/compose-manager; tags include prod-20261029-be649f8; confirmed 2026-11-05 WAT). Docker Hub `latest` a0c07308… is NOT pinned (not observed on panel nodes).

## 5. Benchmarks
### Post-cutoff sports (prior)
{bench}

### UMA-disputed post-cutoff
{disputed}

## 6. Deploy / offline
{deploy_note}

Offline: `docs/SETUP.md` + `python scripts/offline_demo.py` (cargo test + EIP-191 verify of `contract/testdata/real_panel.json` + rebuild `scoreboard/index.html` from `fixtures/scoreboard_cache/`).

## 7. README Limits
See full README. New this round: evidence must be committed by relayer/owner before votes; KMS/key-provider trust; model container still not measured; testnet deploy blocked pending user wallet signature.
"""
(R / "out/claude_review_packet.md").write_text(out)
print("wrote out/claude_review_packet.md", len(out))
print("parse_outcome lines", po.count(chr(10))+1)
