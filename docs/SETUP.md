# Setup (Windows + Mac/Linux)

## Offline mode (no API key) — recommended for screeners
```bash
# 1. Python 3.11+
python3 -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt   # pinned exact versions

# 2. Rust (for contract tests)
# https://rustup.rs — then:
rustup target add wasm32-unknown-unknown

# 3. Offline checks
python scripts/offline_demo.py
# Runs: cargo test (38+), EIP-191 verify of contract/testdata/real_panel.json,
#       rebuilds scoreboard/index.html from fixtures/scoreboard_cache/
```

Open `scoreboard/index.html` in a browser. No `NEARAI_API_KEY` needed.

## Relayer API (mock, no NEAR and no API key)
```bash
python scripts/test_relayer_api.py
python scripts/api.py --data-dir out/relayer-api --port 8787
```
The HTTP process is a mock chain and a scripted panel. It does not deploy a contract and it does not call NEAR AI. See `docs/INTEGRATION.md`.

## Live mode (optional)
```bash
export NEARAI_API_KEY=...   # from cloud.near.ai; never commit
source .venv/bin/activate
python scripts/attest_demo.py Qwen/Qwen3.8-27B   # PASS then tamper FAIL
# Rule linter (no API):
python scripts/rule_linter.py --question "..." --rules "..."
```

## Contract
```bash
cd contract && cargo test
cargo near build non-reproducible-wasm   # → target/near/verdict.wasm
```

## What is tracked vs ignored
- Tracked fixtures: `contract/testdata/`, `fixtures/scoreboard_cache/` (sanitized), `fixtures/evidence/` (content-addressed bundles), `scoreboard/index.html`
- Ignored: `.env`, `*.jsonl`, `out/` (live logs, large raw responses)
