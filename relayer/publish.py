"""Publish an evidence file at a commit SHA git itself returns.

The permalink is ``https://raw.githubusercontent.com/<repo>/<sha>/<path>``.
The SHA is read from ``git rev-parse`` after the blob is in that commit.
``/master/`` and ``/main/`` are refused. A caller-supplied SHA is ignored.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evidence_uri import github_raw_permalink


class PublishError(RuntimeError):
    pass


@dataclass
class PublishResult:
    uri: str
    commit_sha: str
    relpath: str


def _redact(text: str) -> str:
    import re
    return re.sub(r"ed25519:[A-Za-z0-9]+", "ed25519:[redacted]", text or "")


def _git(workdir: Path, args: list[str], *, input_bytes: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in ("NEAR_PRIVATE_KEY", "NEARAI_API_KEY", "NEAR_PUBLIC_KEY"):
        env.pop(key, None)
    # Evidence commits must not depend on a developer identity being configured.
    env.setdefault("GIT_AUTHOR_NAME", os.environ.get("VERDICT_GIT_USER_NAME", "verdict-relayer"))
    env.setdefault("GIT_AUTHOR_EMAIL", os.environ.get("VERDICT_GIT_USER_EMAIL", "verdict-relayer@localhost"))
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    proc = subprocess.run(
        ["git", *args],
        cwd=workdir,
        input=input_bytes,
        capture_output=True,
        env=env,
    )
    if check and proc.returncode != 0:
        err = _redact(proc.stderr.decode("utf-8", "replace")[:2000])
        raise PublishError(f"git {' '.join(args[:4])} failed: {err}")
    return proc


def _blob_at(workdir: Path, sha: str, relpath: str) -> bytes | None:
    proc = _git(workdir, ["cat-file", "blob", f"{sha}:{relpath}"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def _object_type(workdir: Path, sha: str) -> str | None:
    proc = _git(workdir, ["cat-file", "-t", sha], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode().strip()


def _is_ancestor(workdir: Path, older: str, newer: str) -> bool:
    proc = _git(workdir, ["merge-base", "--is-ancestor", older, newer], check=False)
    return proc.returncode == 0


def find_commit_with_bytes(workdir: Path, relpath: str, content: bytes) -> str | None:
    shas: list[str] = []
    head = _git(workdir, ["rev-parse", "--verify", "HEAD"], check=False)
    if head.returncode == 0:
        shas.append(head.stdout.decode().strip())
    log = _git(workdir, ["log", "--all", "-n", "40", "--format=%H", "--", relpath], check=False)
    if log.returncode == 0:
        shas.extend(line.strip() for line in log.stdout.decode().splitlines() if line.strip())
    seen = set()
    for sha in shas:
        if sha in seen:
            continue
        seen.add(sha)
        if _blob_at(workdir, sha, relpath) == content:
            return sha
    return None


def _remote_has_commit(workdir: Path, remote: str, sha: str) -> bool:
    proc = _git(workdir, ["ls-remote", remote, "refs/heads/*"], check=False)
    if proc.returncode != 0:
        return False
    for line in proc.stdout.decode().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        remote_sha = parts[0]
        if remote_sha == sha:
            return True
        if _object_type(workdir, remote_sha) == "commit" and _is_ancestor(workdir, sha, remote_sha):
            return True
    return False


def _commit_bytes(workdir: Path, relpath: str, content: bytes) -> str:
    """Create a commit that contains ``content`` at ``relpath``. Does not move HEAD."""
    blob = _git(workdir, ["hash-object", "-w", "--stdin"], input_bytes=content).stdout.decode().strip()
    if _git(workdir, ["cat-file", "-t", blob], check=False).stdout.decode().strip() != "blob":
        raise PublishError("git hash-object did not return a blob")
    index_path = workdir / ".git" / f"verdict-evidence-index-{blob[:12]}"
    def g(args: list[str], *, input_bytes: bytes | None = None, check: bool = True):
        env = os.environ.copy()
        for key in ("NEAR_PRIVATE_KEY", "NEARAI_API_KEY", "NEAR_PUBLIC_KEY"):
            env.pop(key, None)
        env["GIT_INDEX_FILE"] = str(index_path)
        env.setdefault("GIT_AUTHOR_NAME", os.environ.get("VERDICT_GIT_USER_NAME", "verdict-relayer"))
        env.setdefault("GIT_AUTHOR_EMAIL", os.environ.get("VERDICT_GIT_USER_EMAIL", "verdict-relayer@localhost"))
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
        proc = subprocess.run(["git", *args], cwd=workdir, input=input_bytes, capture_output=True, env=env)
        if check and proc.returncode != 0:
            raise PublishError(_redact(proc.stderr.decode("utf-8", "replace")[:2000]))
        return proc

    try:
        has_head = _git(workdir, ["rev-parse", "--verify", "HEAD"], check=False).returncode == 0
        if has_head:
            g(["read-tree", "HEAD"])
        g(["update-index", "--add", "--cacheinfo", f"100644,{blob},{relpath}"])
        tree = g(["write-tree"]).stdout.decode().strip()
        commit_args = ["commit-tree", tree, "-m", f"relayer: publish {relpath}"]
        if has_head:
            parent = _git(workdir, ["rev-parse", "HEAD"]).stdout.decode().strip()
            commit_args.extend(["-p", parent])
        sha = g(commit_args).stdout.decode().strip()
    finally:
        index_path.unlink(missing_ok=True)
    if _object_type(workdir, sha) != "commit":
        raise PublishError("commit-tree did not return a commit")
    if _blob_at(workdir, sha, relpath) != content:
        raise PublishError("new commit does not contain the evidence bytes")
    return sha


def _assert_permalink(uri: str, commit_sha: str) -> None:
    lowered = uri.lower()
    if "/master/" in lowered or "/main/" in lowered or lowered.endswith("/master") or lowered.endswith("/main"):
        raise PublishError(f"refusing branch permalink: {uri}")
    if commit_sha.lower() not in lowered:
        raise PublishError("permalink is missing the git commit sha")
    if uri.startswith("PENDING_COMMIT_SHA://"):
        raise PublishError("refusing to treat a pending URI as published")
    # The path segment that is the ref must be the hex sha, which github_raw_permalink enforces.
    if any(part in ("master", "main") for part in uri.split("/")):
        raise PublishError(f"refusing branch name in permalink: {uri}")


class GitPublisher:
    def __init__(self, workdir: Path, *, remote: str = "origin", github_repo: str = "Baheet18/verdict", push: bool = False, verify_http: bool = False, http_get=None):
        self.workdir = Path(workdir)
        self.remote = remote
        self.github_repo = github_repo
        self.push = push
        self.verify_http = verify_http
        self.http_get = http_get

    def publish(self, relpath: str, content: bytes) -> PublishResult:
        relpath = relpath.lstrip("/")
        parts = Path(relpath).parts
        if not relpath or ".." in parts or Path(relpath).is_absolute():
            raise PublishError(f"refusing evidence path {relpath!r}")
        if not content:
            raise PublishError("refusing empty evidence file")
        if not (self.workdir / ".git").exists():
            raise PublishError(f"{self.workdir} is not a git checkout")

        sha = find_commit_with_bytes(self.workdir, relpath, content)
        if sha is None:
            sha = _commit_bytes(self.workdir, relpath, content)
        sha = _git(self.workdir, ["rev-parse", sha]).stdout.decode().strip()
        if _blob_at(self.workdir, sha, relpath) != content:
            raise PublishError("refusing permalink; commit does not contain these bytes")

        # One ref per commit so a later round (new wrapper bytes, same evidence hash)
        # does not force-push the previous permalink off the remote.
        ref = f"refs/heads/verdict-evidence/{sha}"
        if sha.lower() in ("master", "main") or ref in ("refs/heads/master", "refs/heads/main"):
            raise PublishError("refusing to publish onto master/main")

        on_remote = _remote_has_commit(self.workdir, self.remote, sha)
        if self.push and not on_remote:
            _git(self.workdir, ["update-ref", ref, sha])
            _git(self.workdir, ["push", self.remote, f"{sha}:{ref}"])
            listed = _git(self.workdir, ["ls-remote", self.remote, ref]).stdout.decode().strip()
            if not listed:
                raise PublishError(f"push did not create {ref}")
            remote_sha = listed.split()[0]
            full_remote = _git(self.workdir, ["rev-parse", remote_sha]).stdout.decode().strip()
            if full_remote != sha:
                raise PublishError("remote ref does not point at the commit that contains the evidence")
            on_remote = True
        if not on_remote:
            raise PublishError(
                "evidence commit is not on the git remote; set VERDICT_EVIDENCE_PUSH=1. "
                "Refusing to invent a public commit SHA."
            )

        uri = github_raw_permalink(relpath, sha, repo=self.github_repo)
        _assert_permalink(uri, sha)
        if self.verify_http:
            self._confirm_http(uri, content)
        return PublishResult(uri=uri, commit_sha=sha, relpath=relpath)

    def _confirm_http(self, uri: str, content: bytes) -> None:
        getter = self.http_get
        if getter is None:
            getter = _urllib_get
        import time
        last = "no response"
        for delay in (0, 2, 4, 8):
            if delay:
                time.sleep(delay)
            status, body = getter(uri)
            last = f"HTTP {status} ({len(body)} bytes)"
            if status == 200 and body == content:
                return
        raise PublishError(f"permalink did not serve the committed bytes ({last}); not calling commit_evidence")


def _urllib_get(url: str) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "VerdictRelayer/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return exc.code, body
