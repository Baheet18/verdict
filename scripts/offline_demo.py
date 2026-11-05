#!/usr/bin/env python3
"""Offline screener mode — no NEARAI_API_KEY required.

Verifies:
  1) cargo test (contract) — subprocess
  2) cached real_panel signatures recover to registered signers (EIP-191)
  3) scoreboard rebuilds from fixtures/scoreboard_cache

Does NOT call cloud-api.near.ai.
"""
from __future__ import annotations
import json, hashlib, pathlib, subprocess, sys
from eth_account import Account
from eth_account.messages import encode_defunct

ROOT = pathlib.Path(__file__).resolve().parent.parent

def sha(b: bytes | str) -> str:
    if isinstance(b, str):
        b = b.encode()
    return hashlib.sha256(b).hexdigest()

def verify_real_panel():
    p = ROOT / "contract" / "testdata" / "real_panel.json"
    data = json.loads(p.read_text())
    ok = 0
    for v in data["votes"]:
        text = v["text"]
        sig = v["signature"]
        want = (v.get("signing_address") or v.get("signer") or "").lower()
        got = Account.recover_message(encode_defunct(text=text), signature=sig).lower()
        assert got == want, f"signer mismatch {got} != {want}"
        parts = text.split(":")
        assert len(parts) == 3 and parts[0] == v["model"], f"bad signed text {text[:60]}"
        ok += 1
        print(f"  PASS signature {v['model']} → {want[:12]}…")
    print(f"real_panel: {ok}/{len(data['votes'])} signatures OK")
    return ok

def cargo_test():
    r = subprocess.run(
        ["cargo", "test", "--manifest-path", str(ROOT / "contract" / "Cargo.toml"), "--quiet"],
        cwd=ROOT, capture_output=True, text=True,
    )
    print(r.stdout)
    print(r.stderr[-2000:] if r.stderr else "")
    if r.returncode != 0:
        raise SystemExit(f"cargo test failed: {r.returncode}")
    print("cargo test: OK")

def scoreboard():
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "build_scoreboard.py")], cwd=ROOT)
    html = (ROOT / "scoreboard" / "index.html").read_text()
    assert "Verdict scoreboard" in html
    print("scoreboard: OK", ROOT / "scoreboard" / "index.html")

def main():
    print("=== Verdict offline demo (no API key) ===")
    cargo_test()
    print("--- cached signatures ---")
    verify_real_panel()
    print("--- scoreboard ---")
    scoreboard()
    print("ALL OFFLINE CHECKS PASSED")

if __name__ == "__main__":
    main()
