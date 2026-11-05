#!/usr/bin/env python3
"""Commit-SHA permalink helper for evidence_uri (Claude round 7).

Do NOT use /master/ or /main/ raw URLs — they move. After a human-approved push
that includes the evidence file, pass the resulting commit SHA.

Existing origin/master tip f96954b does NOT contain fixtures/evidence/* —
never point evidence_uri at a SHA that does not contain the file.
"""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path

REPO = "Baheet18/verdict"
ROOT = Path(__file__).resolve().parent.parent


def pending_uri(relpath: str) -> str:
    relpath = relpath.lstrip("/")
    return f"PENDING_COMMIT_SHA://{relpath}"


def github_raw_permalink(relpath: str, commit_sha: str | None = None, repo: str = REPO) -> str:
    relpath = relpath.lstrip("/")
    if not commit_sha:
        return pending_uri(relpath)
    if len(commit_sha) < 7 or any(c not in "0123456789abcdef" for c in commit_sha.lower()):
        raise ValueError(f"refuse non-hex commit sha: {commit_sha!r}")
    return f"https://raw.githubusercontent.com/{repo}/{commit_sha}/{relpath}"


def resolve_sha(explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip().lower()
    env = os.environ.get("VERDICT_EVIDENCE_COMMIT_SHA", "").strip()
    if env:
        return env.lower()
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("relpath", help="repo-relative path, e.g. fixtures/evidence/<sha>.json")
    ap.add_argument("--sha", default=None, help="commit SHA after human-approved push that includes the file")
    ap.add_argument("--check-local", action="store_true", help="fail if path missing locally")
    args = ap.parse_args(argv)
    if args.check_local and not (ROOT / args.relpath).is_file():
        print(f"missing local file: {args.relpath}", file=sys.stderr)
        return 2
    sha = resolve_sha(args.sha)
    print(github_raw_permalink(args.relpath, sha))
    if not sha:
        print(
            "# pending: after human-approved push that includes this file, re-run with --sha <commit>",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
