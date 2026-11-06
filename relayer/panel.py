"""Panel calls: the canonical request from scripts/verdict_request.py, or a cached fixture.

Dry-run and a missing NEARAI_API_KEY never synthesize a signature. A cached vote
is used only when its request bytes, response bytes, and signed text all match.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote, urlencode

from verdict_request import REQUIRED_SEED, build_request, parse_outcome, sha

ALLOWLIST_TOP = {"model", "messages", "temperature", "seed"}
ALLOWLIST_MSG = {"role", "content"}


class PanelError(RuntimeError):
    pass


def assert_allowlisted(request_raw: str, *, market_id: int, rnd: int, evidence: str, evidence_hash: str) -> dict:
    try:
        body = json.loads(request_raw)
    except json.JSONDecodeError as exc:
        raise PanelError("request is not JSON") from exc
    if not isinstance(body, dict) or set(body) != ALLOWLIST_TOP:
        raise PanelError(f"request keys must be {sorted(ALLOWLIST_TOP)}")
    if body.get("temperature") != 0 or body.get("seed") != REQUIRED_SEED:
        raise PanelError("temperature must be 0 and seed must be 1114")
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise PanelError("request needs exactly two messages")
    for msg in messages:
        if not isinstance(msg, dict) or set(msg) != ALLOWLIST_MSG:
            raise PanelError("message keys must be role and content")
    if messages[0].get("role") != "system" or messages[1].get("role") != "user":
        raise PanelError("messages must be system then user")
    user = messages[1].get("content")
    if not isinstance(user, str) or not user.startswith("VERDICT/1 "):
        raise PanelError("user message is missing the VERDICT/1 header")
    header, ev = user.split("\n", 1)
    if ev != evidence:
        raise PanelError("request evidence bytes differ from the locked bundle")
    if sha(ev) != evidence_hash or f"evidence_bundle_hash={evidence_hash}" not in header:
        raise PanelError("request evidence hash does not match the committed bundle")
    if f"market_id={market_id} " not in header and not header.startswith(f"VERDICT/1 market_id={market_id} "):
        raise PanelError("request market_id does not match")
    if f"round={rnd} " not in header:
        raise PanelError("request round does not match")
    return body


def _signature_hex(signature: str) -> str:
    sig = signature.strip().removeprefix("0x").removeprefix("0X")
    if len(sig) != 130 or any(c not in "0123456789abcdefABCDEF" for c in sig):
        raise PanelError("signature must be 65 bytes of hex")
    return sig.lower()


def check_vote(vote: dict, *, model: str, market_id: int, rnd: int, evidence: str, evidence_hash: str) -> dict:
    request_raw = vote.get("request_raw")
    response_raw = vote.get("response_raw")
    if not isinstance(request_raw, str) or not isinstance(response_raw, str):
        raise PanelError("vote is missing request_raw or response_raw")
    body = assert_allowlisted(request_raw, market_id=market_id, rnd=rnd, evidence=evidence, evidence_hash=evidence_hash)
    if body.get("model") != model:
        raise PanelError("request model does not match the resolver snapshot")
    if parse_outcome(response_raw) is None:
        raise PanelError("response has no strict outcome; not submitting")
    text = vote.get("text")
    expected = f"{model}:{sha(request_raw)}:{sha(response_raw)}"
    if not isinstance(text, str) or text != expected:
        raise PanelError("signed text does not match model:sha256(request):sha256(response)")
    return {
        "model": model,
        "request_raw": request_raw,
        "response_raw": response_raw,
        "signature": _signature_hex(vote["signature"]),
        "text": text,
        "outcome": parse_outcome(response_raw),
    }


def _records_from(obj) -> list[dict]:
    found = []
    if isinstance(obj, dict) and {"request_raw", "response_raw", "signature"} <= set(obj):
        found.append(obj)
    if isinstance(obj, dict):
        votes = obj.get("votes")
        if isinstance(votes, list):
            for item in votes:
                if isinstance(item, dict) and {"request_raw", "response_raw", "signature"} <= set(item):
                    found.append(item)
    return found


class PanelClient:
    def __init__(self, *, live: bool = False, api_key: str | None = None, fixture_paths: list[Path] | None = None, http_post=None, http_get=None):
        self.live = live
        self.api_key = api_key if api_key is not None else os.environ.get("NEARAI_API_KEY", "")
        self.http_post = http_post
        self.http_get = http_get
        self.cache: dict[str, dict] = {}
        for path in fixture_paths or []:
            self._load(Path(path))

    def _load(self, path: Path) -> None:
        files = [path] if path.is_file() else list(path.glob("*.json")) if path.is_dir() else []
        for file in files:
            try:
                obj = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for rec in _records_from(obj):
                request_raw = rec.get("request_raw")
                response_raw = rec.get("response_raw")
                text = rec.get("text")
                if not isinstance(request_raw, str) or not isinstance(response_raw, str):
                    continue
                expected = f"{rec.get('model')}:{sha(request_raw)}:{sha(response_raw)}"
                if text != expected:
                    continue
                self.cache[sha(request_raw)] = rec

    def sign(self, model: str, market_id: int, rnd: int, rules: str, evidence: str, evidence_hash: str) -> dict | None:
        body = build_request(model, market_id, rnd, rules, evidence)
        request_raw = body.decode("utf-8")
        assert_allowlisted(request_raw, market_id=market_id, rnd=rnd, evidence=evidence, evidence_hash=evidence_hash)
        cached = self.cache.get(sha(request_raw))
        if cached and cached.get("model") == model:
            return check_vote(cached, model=model, market_id=market_id, rnd=rnd, evidence=evidence, evidence_hash=evidence_hash)
        if not self.live:
            return None
        if not self.api_key:
            raise PanelError("NEARAI_API_KEY is unset and no cached vote matches this request")
        return self._call(model, body, request_raw, market_id, rnd, evidence, evidence_hash)

    def _call(self, model: str, body: bytes, request_raw: str, market_id: int, rnd: int, evidence: str, evidence_hash: str) -> dict:
        if self.http_post is None or self.http_get is None:
            from relayer.chain import urllib_request
            post = lambda url, data, headers: urllib_request(url, data=data, headers=headers, method="POST")
            get = lambda url, headers: urllib_request(url, headers=headers, method="GET")
        else:
            post, get = self.http_post, self.http_get
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        status, raw = post("https://cloud-api.near.ai/v1/chat/completions", body, headers)
        if status != 200:
            raise PanelError(f"NEAR AI chat/completions HTTP {status}")
        try:
            resp = json.loads(raw.decode("utf-8"))
            response_raw = raw.decode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PanelError("NEAR AI response was not utf-8 JSON") from exc
        completion_id = resp.get("id")
        if not completion_id:
            raise PanelError("NEAR AI response has no id")
        sig_url = (
            "https://cloud-api.near.ai/v1/signature/"
            + quote(str(completion_id), safe="")
            + "?"
            + urlencode({"model": model, "signing_algo": "ecdsa"})
        )
        sig_status, sig_raw = get(sig_url, headers)
        if sig_status != 200:
            raise PanelError(f"NEAR AI signature HTTP {sig_status}")
        try:
            sig = json.loads(sig_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PanelError("signature response was not JSON") from exc
        vote = {
            "model": model,
            "request_raw": request_raw,
            "response_raw": response_raw,
            "signature": sig.get("signature") or "",
            "text": sig.get("text") or "",
        }
        return check_vote(vote, model=model, market_id=market_id, rnd=rnd, evidence=evidence, evidence_hash=evidence_hash)
