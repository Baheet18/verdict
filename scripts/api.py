#!/usr/bin/env python3
"""Local HTTP API in front of the Verdict relayer.

Default mode is mock: an in-process chain and a scripted panel. It does not read
credentials, does not deploy a contract, and does not call NEAR or NEAR AI.

Integration endpoints:
  GET  /v1/health
  GET  /v1/resolvers
  POST /v1/markets
  GET  /v1/markets
  GET  /v1/markets/{id}
  POST /v1/markets/{id}/relay
  POST /v1/relay/tick
  GET  /v1/evidence/{sha256}

Mock-only endpoints:
  POST /v1/testing/clock
  POST /v1/testing/markets/{id}/mock-script
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from mock_chain import ChainError
from panel import PANEL_MODELS
from relayer import RelayerError, build_mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_BODY = 1_000_000

LIMITATIONS = [
    "This process is a mock chain. It does not submit transactions to NEAR and it does not deploy a contract.",
    "The panel is scripted. It does not call Qwen, DeepSeek, or GLM.",
    "EIP-191 signatures and the signer-to-model binding are checked. Hardware attestation is not re-checked.",
    "evidence_uri stays PENDING_COMMIT_SHA. This API does not invent a commit SHA.",
    "The relayer chooses the evidence snapshots. Rejecting request-parameter steering does not make that choice trustless.",
    "A split panel or a unanimous AMBIGUOUS escalates. This API does not invent an outcome and it does not predict disputes.",
    "Restarting the process drops markets, signers, and the in-memory clock. Files under the data dir are not a NEAR commitment.",
]

_MARKET = re.compile(r"^/v1/markets/(\d+)$")
_RELAY = re.compile(r"^/v1/markets/(\d+)/relay$")
_EVIDENCE = re.compile(r"^/v1/evidence/([0-9a-f]{64})$")
_SCRIPT = re.compile(r"^/v1/testing/markets/(\d+)/mock-script$")


def chain_http_status(message: str) -> int:
    if message == "no market":
        return 404
    if message == "market not ended":
        return 409
    return 400


def http_fetch(url: str) -> str:
    if not isinstance(url, str) or not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("refusing non-http source fetch")
    host = url.split("://", 1)[1].split("/", 1)[0]
    if not host or "@" in host:
        raise ValueError("refusing source URL with userinfo")
    import requests
    response = requests.get(url, timeout=30, headers={"User-Agent": "VerdictRelayer/0.1"})
    response.raise_for_status()
    return response.text[:200_000]


def _market_id(text: str) -> int:
    value = int(text)
    if value > 2**32 - 1:
        raise RelayerError("no market", 404)
    return value


def serve(relayer, host: str, port: int) -> ThreadingHTTPServer:
    evidence_dir = relayer.evidence_dir

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method: str):
            try:
                split = urlsplit(self.path)
                if split.query:
                    self._send(400, {"error": "query strings are not accepted"})
                    return
                self._route(method, split.path)
            except RelayerError as exc:
                self._send(exc.status, {"error": str(exc)})
            except ChainError as exc:
                self._send(chain_http_status(str(exc)), {"error": str(exc)})
            except Exception as exc:
                self._send(500, {"error": "internal error"})

        def _route(self, method: str, path: str):
            if method == "GET" and path == "/v1/health":
                self._send(200, health(relayer))
                return
            if method == "GET" and path == "/v1/resolvers":
                self._send(200, {
                    "resolvers": relayer.panel.resolvers(),
                    "note": "Mock mode generates these signers in memory for this process. They are not NEAR AI TEE keys.",
                })
                return
            if method == "GET" and path == "/v1/markets":
                self._send(200, {"markets": relayer.list_markets()})
                return
            if method == "POST" and path == "/v1/markets":
                self._send(200, relayer.create_market(self._json()))
                return
            if method == "POST" and path == "/v1/relay/tick":
                if self._length():
                    self._json()
                self._send(200, relayer.tick())
                return
            if method == "POST" and path == "/v1/testing/clock":
                if relayer.panel.execution != "scripted" or relayer.chain.kind != "mock":
                    raise RelayerError("not found", 404)
                body = self._json()
                if set(body) != {"now_ns"}:
                    raise RelayerError("clock body must be {now_ns}")
                self._send(200, relayer.set_clock(body["now_ns"]))
                return
            match = _MARKET.fullmatch(path)
            if match and method == "GET":
                self._send(200, relayer.view(_market_id(match.group(1))))
                return
            match = _RELAY.fullmatch(path)
            if match and method == "POST":
                if self._length():
                    self._json()
                self._send(200, relayer.relay(_market_id(match.group(1))))
                return
            match = _SCRIPT.fullmatch(path)
            if match and method == "POST":
                body = self._json()
                if set(body) != {"mock_script"}:
                    raise RelayerError("body must be {mock_script}")
                self._send(200, relayer.set_mock_script(_market_id(match.group(1)), body["mock_script"]))
                return
            match = _EVIDENCE.fullmatch(path)
            if match and method == "GET":
                file_path = evidence_dir / f"{match.group(1)}.json"
                if not file_path.is_file():
                    raise RelayerError("no evidence", 404)
                self._send(200, json.loads(file_path.read_text()))
                return
            self._send(404, {"error": "not found"})

        def _length(self) -> int:
            raw = self.headers.get("Content-Length")
            if raw is None:
                return 0
            try:
                length = int(raw)
            except ValueError:
                raise RelayerError("bad Content-Length") from None
            if length < 0 or length > MAX_BODY:
                raise RelayerError("body too large", 413 if length > MAX_BODY else 400)
            return length

        def _json(self) -> dict:
            length = self._length()
            if length == 0:
                raise RelayerError("body must be JSON")
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                raise RelayerError("body must be JSON") from None
            if not isinstance(data, dict):
                raise RelayerError("body must be a JSON object")
            return data

        def _send(self, code: int, obj) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def health(relayer) -> dict:
    return {
        "ok": True,
        "mode": "mock",
        "chain": relayer.chain.kind,
        "panel_execution": relayer.panel.execution,
        "panel": list(PANEL_MODELS),
        "now_ns": relayer.chain.now_ns,
        "fetch_sources": relayer.fetch is not None,
        "limitations": list(LIMITATIONS),
    }


def _refuse_fixtures(path: pathlib.Path) -> None:
    resolved = path.resolve()
    fixtures = (ROOT / "fixtures").resolve()
    if resolved == fixtures or fixtures in resolved.parents:
        raise SystemExit("refusing to write evidence under fixtures/")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verdict mock HTTP API. Does not use NEAR or NEAR AI credentials.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", default="out/relayer-api")
    parser.add_argument("--fetch", action="store_true", help="GET locked http(s) sources after the deadline when no snapshot was supplied")
    args = parser.parse_args(argv)
    data = pathlib.Path(args.data_dir)
    if not data.is_absolute():
        data = ROOT / data
    _refuse_fixtures(data)
    data.mkdir(parents=True, exist_ok=True)
    relayer = build_mock(data / "evidence", fetch=http_fetch if args.fetch else None)
    server = serve(relayer, args.host, args.port)
    host, port = server.server_address[:2]
    print(f"Verdict API mock mode on http://{host}:{port}")
    print("Panel execution: scripted. No NEAR and no NEAR AI calls.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
