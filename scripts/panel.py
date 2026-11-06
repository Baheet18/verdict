"""Panel clients for the three independently bound Verdict models.

MockPanel signs scripted outcomes with process-local keys. Those keys stay in memory.
LivePanel speaks the same NEAR AI chat and signature endpoints as scripts/bench_scale.py.
The HTTP API constructs MockPanel only. LivePanel is used when a host passes a transport
and a key provider; this module does not read the environment.
"""
from __future__ import annotations

from dataclasses import dataclass

from eth_account import Account
from eth_account.messages import encode_defunct

from verdict_request import OUTCOMES, sha

# Same three models as scripts/bench_scale.py. Each request names one of them.
PANEL_MODELS = [
    "Qwen/Qwen3.8-27B",
    "deepseek-ai/DeepSeek-V4.1-Flash-1017",
    "z-ai/glm-5.3-flash",
]

NEAR_AI_API = "https://cloud-api.near.ai/v1"


class PanelError(Exception):
    pass


def sign_text(text: str, private_key) -> str:
    signed = Account.sign_message(encode_defunct(text=text), private_key=private_key)
    hex_sig = signed.signature.hex()
    if hex_sig.startswith(("0x", "0X")):
        hex_sig = hex_sig[2:]
    return hex_sig.lower()


def recover_signer(text: str, signature: str) -> str | None:
    if not isinstance(signature, str) or signature.strip() == "":
        return None
    try:
        return Account.recover_message(encode_defunct(text=text), signature=signature).lower()
    except Exception:
        return None


def signed_text(model: str, request_raw: bytes | str, response_raw: bytes | str) -> str:
    return f"{model}:{sha(request_raw)}:{sha(response_raw)}"


@dataclass
class Completion:
    response_raw: str
    signature: str


class MockPanel:
    """Scripted stand-in. Does not call Qwen, DeepSeek, or GLM."""

    execution = "scripted"

    def __init__(self):
        self._accounts = [(model, Account.create()) for model in PANEL_MODELS]
        self.script: dict[str, str] = {}
        self.calls: list[str] = []

    def resolvers(self) -> list[dict]:
        return [{"address": acct.address.lower(), "model": model} for model, acct in self._accounts]

    def _account(self, model: str):
        for name, acct in self._accounts:
            if name == model:
                return acct
        raise PanelError("model is not on the panel")

    def sign(self, model: str, text: str) -> str:
        return sign_text(text, self._account(model).key)

    def complete(self, model: str, request_raw: bytes) -> Completion:
        if model not in PANEL_MODELS:
            raise PanelError("model is not on the panel")
        self.calls.append(model)
        outcome = self.script.get(model, "AMBIGUOUS")
        if outcome not in OUTCOMES:
            raise PanelError("scripted outcome is not allowed")
        content = (
            '{"outcome":"%s","quotes":[],"reasoning":"Scripted mock outcome. Not a model judgment.","confidence":0}'
            % outcome
        )
        response = {
            "id": "mock-" + sha(request_raw)[:16],
            "object": "chat.completion",
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        }
        import json
        raw = json.dumps(response, ensure_ascii=False, separators=(", ", ": "))
        text = signed_text(model, request_raw, raw)
        return Completion(raw, self.sign(model, text))


class LivePanel:
    """NEAR AI client. The host supplies transport and the key. Nothing is read from disk."""

    execution = "near-ai"

    def __init__(self, transport, api_key_provider, resolvers=None):
        self.transport = transport
        self.api_key_provider = api_key_provider
        self._resolvers = list(resolvers or [])
        self.calls: list[str] = []

    def resolvers(self) -> list[dict]:
        return list(self._resolvers)

    def complete(self, model: str, request_raw: bytes) -> Completion:
        if model not in PANEL_MODELS:
            raise PanelError("model is not on the panel")
        key = self.api_key_provider()
        if not isinstance(key, str) or key.strip() == "":
            raise PanelError("API key is empty")
        self.calls.append(model)
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        status, raw = self.transport("POST", NEAR_AI_API + "/chat/completions", headers, request_raw, None)
        if status != 200:
            raise PanelError(f"chat completion HTTP {status}")
        import json
        try:
            resp = json.loads(raw)
            completion_id = resp["id"]
        except Exception:
            raise PanelError("chat completion was not a completion object") from None
        if not isinstance(completion_id, str) or not completion_id or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-" for c in completion_id):
            raise PanelError("unexpected completion id")
        status, sig_raw = self.transport(
            "GET",
            f"{NEAR_AI_API}/signature/{completion_id}",
            headers,
            None,
            {"model": model, "signing_algo": "ecdsa"},
        )
        if status != 200:
            raise PanelError(f"signature HTTP {status}")
        try:
            sig = json.loads(sig_raw)
            response_text = raw.decode("utf-8")
        except Exception:
            raise PanelError("signature response was not usable") from None
        if response_text.encode("utf-8") != raw:
            raise PanelError("completion body is not utf-8")
        text = signed_text(model, request_raw, response_text)
        signature = sig.get("signature") if isinstance(sig, dict) else None
        got = recover_signer(text, signature or "")
        announced = (sig.get("signing_address") or "") if isinstance(sig, dict) else ""
        if got is None or sig.get("text") != text or announced.lower() != got:
            raise PanelError("signature does not match request and response")
        hex_sig = signature[2:] if signature.startswith(("0x", "0X")) else signature
        return Completion(response_text, hex_sig.lower())
