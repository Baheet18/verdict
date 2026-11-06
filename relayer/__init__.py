"""Automatic relayer for Verdict markets on the NEAR testnet contract.

The integrating app calls ``create_market`` and reads ``get_market``. This package
does ``commit_evidence``, ``submit_verdict``, and ``finalize``. It does not call
``resolve_dispute``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# The panel registered on verdict-v2-baheet.testnet. A market's own
# resolver_models snapshot is what we actually call; these are the default set.
PANEL_MODELS = (
    "Qwen/Qwen3.8-27B",
    "deepseek-ai/DeepSeek-V4.1-Flash-1017",
    "z-ai/glm-5.3-flash",
)

CONTRACT_DEFAULT = "verdict-v2-baheet.testnet"
RPC_DEFAULT = "https://rpc.testnet.near.org"
GITHUB_REPO_DEFAULT = "Baheet18/verdict"
