"""NEAR view calls over RPC, and change calls through near-cli-rs.

The private key is never placed on the command line. ``file-args`` keeps the
JSON args in a UTF-8 file with no BOM (PowerShell ``Set-Content -Encoding utf8``
writes a BOM; this process uses ``Path.write_bytes``).
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

TX_ID = re.compile(r"Transaction ID:\s*([1-9A-HJ-NP-Za-km-z]+)")
SECRET = re.compile(r"ed25519:[A-Za-z0-9]+")


def redact(text: str) -> str:
    return SECRET.sub("ed25519:[redacted]", text or "")


def urllib_request(url: str, *, data: bytes | None = None, headers: dict | None = None, method: str | None = None, timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(8_000_001)
    except urllib.error.HTTPError as exc:
        body = exc.read(8_000_001) if exc.fp else b""
        return exc.code, body


def decode_view_payload(result_field) -> object:
    if isinstance(result_field, list):
        raw = bytes(result_field)
    elif isinstance(result_field, str):
        raw = base64.b64decode(result_field)
    elif isinstance(result_field, (bytes, bytearray)):
        raw = bytes(result_field)
    else:
        raise RuntimeError("unexpected RPC view payload")
    if raw == b"null":
        return None
    return json.loads(raw.decode("utf-8"))


def block_time_ns(header: dict) -> int:
    if "timestamp_nanosec" in header:
        return int(header["timestamp_nanosec"])
    return int(header["timestamp"])


def dump_args(args: dict) -> bytes:
    raw = json.dumps(args, ensure_ascii=True).encode("utf-8")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError("args file must not start with a BOM")
    return raw


def near_argv(near_bin: str, *, contract: str, method: str, args_file: str, signer: str, network: str, credentials_file: str, gas: str) -> list[str]:
    return [
        near_bin, "contract", "call-function", "as-transaction", contract, method,
        "file-args", args_file,
        "prepaid-gas", gas,
        "attached-deposit", "0 NEAR",
        "sign-as", signer,
        "network-config", network,
        "sign-with-access-key-file", credentials_file,
        "send",
    ]


def parse_tx_id(output: str) -> str | None:
    match = TX_ID.search(output or "")
    return match.group(1) if match else None


def explorer_url(network: str, tx_hash: str | None) -> str | None:
    if not tx_hash:
        return None
    host = "https://explorer.testnet.near.org" if network == "testnet" else "https://explorer.near.org"
    return f"{host}/transactions/{tx_hash}"


GAS = {
    "commit_evidence": "100.0 Tgas",
    "submit_verdict": "300.0 Tgas",
    "finalize": "30.0 Tgas",
}


class RpcClient:
    def __init__(self, rpc_url: str, contract: str, http_post=None):
        self.rpc_url = rpc_url
        self.contract = contract
        self.http_post = http_post or _rpc_post

    def _call(self, payload: dict) -> dict:
        status, body = self.http_post(self.rpc_url, json.dumps(payload).encode("utf-8"))
        if status != 200:
            raise RuntimeError(f"RPC HTTP {status}")
        msg = json.loads(body.decode("utf-8"))
        if "error" in msg:
            raise RuntimeError(redact(json.dumps(msg["error"])[:500]))
        return msg["result"]

    def view(self, method: str, args: dict):
        result = self._call({
            "jsonrpc": "2.0",
            "id": "verdict-relayer",
            "method": "query",
            "params": {
                "request_type": "call_function",
                "finality": "final",
                "account_id": self.contract,
                "method_name": method,
                "args_base64": base64.b64encode(json.dumps(args).encode("utf-8")).decode("ascii"),
            },
        })
        return decode_view_payload(result["result"])

    def now_ns(self) -> int:
        result = self._call({"jsonrpc": "2.0", "id": "verdict-relayer", "method": "block", "params": {"finality": "final"}})
        return block_time_ns(result["header"])

    def get_market(self, market_id: int):
        return self.view("get_market", {"market_id": int(market_id)})

    def is_relayer(self, account: str) -> bool:
        return bool(self.view("is_relayer", {"account": account}))

    def iter_ids(self, explicit: list[int] | None, max_markets: int) -> list[int]:
        if explicit is not None:
            out = []
            for market_id in explicit:
                if self.get_market(market_id) is not None:
                    out.append(int(market_id))
            return out
        out = []
        for market_id in range(max_markets):
            if self.get_market(market_id) is None:
                break
            out.append(market_id)
        return out


def _rpc_post(url: str, data: bytes) -> tuple[int, bytes]:
    return urllib_request(url, data=data, headers={"Content-Type": "application/json"}, method="POST", timeout=30)


class NearCli:
    def __init__(self, *, near_bin: str, contract: str, signer: str, network: str, credentials_file: str):
        self.near_bin = near_bin
        self.contract = contract
        self.signer = signer
        self.network = network
        self.credentials_file = credentials_file

    def transact(self, method: str, args: dict) -> dict:
        gas = GAS.get(method, "100.0 Tgas")
        raw = dump_args(args)
        with tempfile.TemporaryDirectory(prefix="verdict-relayer-args-") as tmp:
            path = Path(tmp) / f"{method}.json"
            path.write_bytes(raw)
            argv = near_argv(
                self.near_bin,
                contract=self.contract,
                method=method,
                args_file=str(path),
                signer=self.signer,
                network=self.network,
                credentials_file=self.credentials_file,
                gas=gas,
            )
            secret_values = [os.environ.get("NEAR_PRIVATE_KEY") or "", os.environ.get("NEARAI_API_KEY") or ""]
            joined = " ".join(argv)
            for secret in secret_values:
                if secret and secret in joined:
                    raise RuntimeError("refusing to put a secret on the near CLI command line")
            env = os.environ.copy()
            for key in ("NEAR_PRIVATE_KEY", "NEARAI_API_KEY", "NEAR_PUBLIC_KEY"):
                env.pop(key, None)
            env["CI"] = "1"
            proc = subprocess.run(argv, capture_output=True, env=env, timeout=180)
        output = redact(proc.stdout.decode("utf-8", "replace") + "\n" + proc.stderr.decode("utf-8", "replace"))
        if proc.returncode != 0:
            raise RuntimeError(f"near {method} failed: {output[-2000:]}")
        tx_hash = parse_tx_id(output)
        return {"sent": True, "tx_hash": tx_hash, "explorer_url": explorer_url(self.network, tx_hash), "output_tail": output[-500:]}
