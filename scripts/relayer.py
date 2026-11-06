#!/usr/bin/env python3
"""Start the Verdict relayer.

Dry-run (no key, no NEAR AI, no transactions):
  python scripts/relayer.py --once

Offline fixtures:
  python scripts/relayer.py --once --markets-file fixtures/relayer/offline_markets.json

Live env names (values stay in your shell or a gitignored file): VERDICT_RELAYER_MODE,
VERDICT_SIGNER_ACCOUNT, NEAR_CREDENTIALS_FILE, VERDICT_EVIDENCE_PUSH, NEARAI_API_KEY.
See docs/RELAYER.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from relayer.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
