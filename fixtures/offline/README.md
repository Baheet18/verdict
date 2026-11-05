# Offline fixtures

- Signature verification uses `../../contract/testdata/real_panel.json` (real EIP-191
  signatures from all 3 panel models; no API key to re-verify locally).
- Scoreboard uses `../scoreboard_cache/` (sanitized verified votes only; raw
  request/response stripped).
- Run `python scripts/offline_demo.py` from the repo root.
