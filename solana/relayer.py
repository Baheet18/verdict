#!/usr/bin/env python3
"""Send Verdict panel votes to the Solana program.

The NEAR AI key and the Solana keypair path come from the environment only.
This script never prints those secrets.

  NEARAI_API_KEY   bearer token for https://cloud-api.near.ai
  SOLANA_KEYPAIR   path to a Solana keypair JSON file outside this repo
  SOLANA_RPC       cluster URL, default https://api.devnet.solana.com

The keypair pays fees, creates the two markets, and is their escalation owner.
It does not need a second key. If it is also the protocol owner it refreshes
the three model signers when NEAR AI returns different addresses.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verdict_request import SYSTEM_PROMPT, build_request, evidence_bundle, parse_vote, sha  # noqa: E402

from solders.hash import Hash  # noqa: E402
from solders.instruction import AccountMeta, Instruction  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.pubkey import Pubkey  # noqa: E402
from solders.transaction import Transaction  # noqa: E402

PROGRAM_DEFAULT = "6nbXLUgehsnFqUTvg3EUCtP8mDHodBXFv6NhdJwBt3AW"
MODELS = (
    "Qwen/Qwen3.8-27B",
    "deepseek-ai/DeepSeek-V4.1-Flash-1017",
    "z-ai/glm-5.3-flash",
)
WINDOW = 60
BOND = 1_000_000
MAX_PAYLOAD = 9600
CHUNK = 400
HEAP = 256 * 1024
CU = 1_400_000
SYSVAR_CLOCK = Pubkey.from_string("SysvarC1ock11111111111111111111111111111111")
SYSTEM = Pubkey.from_string("11111111111111111111111111111111")
COMPUTE = Pubkey.from_string("ComputeBudget111111111111111111111111111111")


class RelayerError(RuntimeError):
    pass


def disc(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def u8(n: int) -> bytes:
    return bytes([n])


def u32(n: int) -> bytes:
    return int(n).to_bytes(4, "little")


def u64(n: int) -> bytes:
    return int(n).to_bytes(8, "little")


def i64(n: int) -> bytes:
    return int(n).to_bytes(8, "little", signed=True)


def borsh_str(text: str) -> bytes:
    raw = text.encode()
    return u32(len(raw)) + raw


def borsh_vec(raw: bytes) -> bytes:
    return u32(len(raw)) + raw


def chunks(blob: bytes, size: int = CHUNK):
    for offset in range(0, len(blob), size):
        yield offset, blob[offset : offset + size]


def load_keypair(path: str) -> Keypair:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(n, int) for n in raw):
        raise RelayerError("SOLANA_KEYPAIR is not a JSON byte array")
    return Keypair.from_bytes(bytes(raw))


def rpc_url() -> str:
    return os.environ.get("SOLANA_RPC", "https://api.devnet.solana.com")


def program_id() -> Pubkey:
    return Pubkey.from_string(os.environ.get("VERDICT_PROGRAM_ID", PROGRAM_DEFAULT))


def rpc(method: str, params: list):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(rpc_url(), data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RelayerError(f"RPC HTTP {exc.code}") from exc
    if "error" in out:
        raise RelayerError(f"RPC {method} failed: {out['error']}")
    return out["result"]


def pda(seeds: list[bytes]) -> Pubkey:
    return Pubkey.find_program_address(seeds, program_id())[0]


def config_pda() -> Pubkey:
    return pda([b"config"])


def market_pda(market_id: int) -> Pubkey:
    return pda([b"market", market_id.to_bytes(8, "little")])


def payload_pda(market_id: int, slot: int) -> Pubkey:
    return pda([b"payload", market_id.to_bytes(8, "little"), bytes([slot])])


def budget_ixs() -> list[Instruction]:
    heap = bytes([1]) + u32(HEAP)
    limit = bytes([2]) + u32(CU)
    return [Instruction(COMPUTE, heap, []), Instruction(COMPUTE, limit, [])]


def send(ixs: list[Instruction], signers: list[Keypair]) -> str:
    payer = signers[0]
    blockhash = Hash.from_string(rpc("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"])
    tx = Transaction.new_signed_with_payer(budget_ixs() + ixs, payer.pubkey(), signers, blockhash)
    signature = rpc(
        "sendTransaction",
        [base64.b64encode(bytes(tx)).decode(), {"encoding": "base64", "preflightCommitment": "confirmed"}],
    )
    for _ in range(30):
        status = rpc("getSignatureStatuses", [[signature]])["value"][0]
        if status and status.get("confirmationStatus") in ("confirmed", "finalized"):
            if status.get("err"):
                raise RelayerError(f"transaction {signature} failed: {status['err']}")
            return signature
        time.sleep(1)
    raise RelayerError(f"transaction {signature} was not confirmed")


def meta(pubkey: Pubkey, writable: bool, signer: bool = False) -> AccountMeta:
    return AccountMeta(pubkey, signer, writable)


def ix(name: str, data: bytes, accounts: list[AccountMeta]) -> Instruction:
    return Instruction(program_id(), disc(name) + data, accounts)


def signer_data(addresses: list[str]) -> bytes:
    data = b""
    for model, address in zip(MODELS, addresses, strict=True):
        raw = bytes.fromhex(address.removeprefix("0x").removeprefix("0X"))
        if len(raw) != 20:
            raise RelayerError("a model signer address must be 20 bytes")
        data += raw + borsh_str(model)
    return data + i64(WINDOW) + u64(BOND)


def read_config() -> tuple[Pubkey, list[str]] | None:
    info = rpc("getAccountInfo", [str(config_pda()), {"encoding": "base64"}])["value"]
    if not info:
        return None
    raw = base64.b64decode(info["data"][0])
    owner = Pubkey.from_bytes(raw[8:40])
    addresses = []
    cursor = 40
    for _ in MODELS:
        addresses.append("0x" + raw[cursor : cursor + 20].hex())
        cursor += 85
    return owner, addresses


def ensure_config(payer: Keypair, addresses: list[str]) -> str | None:
    """Create config, or replace signers when this key is the protocol owner."""
    data = signer_data(addresses)
    current = read_config()
    if current is None:
        return send(
            [ix("initialize_config", data, [
                meta(config_pda(), True),
                meta(payer.pubkey(), True, True),
                meta(SYSTEM, False),
            ])],
            [payer],
        )
    owner, existing = current
    if [item.lower() for item in existing] == [item.lower() for item in addresses]:
        return None
    if owner != payer.pubkey():
        raise RelayerError("model signer addresses differ from config, and this key is not the protocol owner")
    return send(
        [ix("update_config", data, [
            meta(config_pda(), True),
            meta(payer.pubkey(), True, True),
        ])],
        [payer],
    )


def transfer_owner(owner: Keypair, new_owner: Pubkey) -> str:
    return send(
        [ix("transfer_owner", bytes(new_owner), [
            meta(config_pda(), True),
            meta(owner.pubkey(), True, True),
        ])],
        [owner],
    )


def create_market(owner: Keypair, market_id: int, question: str, rules: str, evidence: str, escalation: Pubkey, deadline: int) -> str:
    rules_hash = bytes.fromhex(sha(rules))
    evidence_hash = bytes.fromhex(sha(evidence))
    prompt_hash = bytes.fromhex(sha(SYSTEM_PROMPT))
    data = (
        u64(market_id)
        + borsh_str(question)
        + rules_hash
        + evidence_hash
        + prompt_hash
        + i64(deadline)
        + bytes(escalation)
    )
    return send(
        [ix("create_market", data, [
            meta(market_pda(market_id), True),
            meta(config_pda(), False),
            meta(owner.pubkey(), True, True),
            meta(SYSTEM, False),
            meta(SYSVAR_CLOCK, False),
        ])],
        [owner],
    )


def submit_signed_vote(payer: Keypair, market_id: int, slot: int, request: bytes, response: bytes, signature: bytes) -> list[str]:
    if len(signature) != 65:
        raise RelayerError("signature must be 65 bytes")
    if len(request) + len(response) > MAX_PAYLOAD:
        raise RelayerError("payload is larger than the program allows")
    sigs = []
    sigs.append(send(
        [ix("init_payload", u64(market_id) + u8(slot) + u32(len(request)) + u32(len(response)), [
            meta(market_pda(market_id), True),
            meta(payload_pda(market_id, slot), True),
            meta(payer.pubkey(), True, True),
            meta(SYSTEM, False),
        ])],
        [payer],
    ))
    blob = request + response
    for offset, chunk in chunks(blob):
        sigs.append(send(
            [ix("write_payload", u64(market_id) + u8(slot) + u32(offset) + borsh_vec(chunk), [
                meta(payload_pda(market_id, slot), True),
            ])],
            [payer],
        ))
    sigs.append(send(
        [ix("submit_vote", u64(market_id) + u8(slot) + signature, [
            meta(market_pda(market_id), True),
            meta(payload_pda(market_id, slot), True),
            meta(payer.pubkey(), True, True),
            meta(SYSVAR_CLOCK, False),
        ])],
        [payer],
    ))
    return sigs


def near_vote(model: str, market_id: int, rules: str, evidence: str) -> dict:
    key = os.environ.get("NEARAI_API_KEY", "")
    if not key:
        raise RelayerError("NEARAI_API_KEY is unset")
    body = build_request(model, market_id, 0, rules, evidence)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    req = urllib.request.Request("https://cloud-api.near.ai/v1/chat/completions", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise RelayerError(f"NEAR AI chat failed with HTTP {exc.code}") from exc
    completion = json.loads(raw.decode())
    completion_id = completion.get("id")
    if not completion_id:
        raise RelayerError("NEAR AI response has no id")
    query = urllib.parse.urlencode({"model": model, "signing_algo": "ecdsa"})
    sig_url = "https://cloud-api.near.ai/v1/signature/" + urllib.parse.quote(str(completion_id), safe="") + "?" + query
    sig_req = urllib.request.Request(sig_url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(sig_req, timeout=120) as resp:
            sig = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RelayerError(f"NEAR AI signature failed with HTTP {exc.code}") from exc
    text = f"{model}:{sha(body)}:{sha(raw)}"
    if sig.get("text") != text:
        raise RelayerError("NEAR AI signed text does not match model:sha256(request):sha256(response)")
    signature = bytes.fromhex(str(sig.get("signature", "")).removeprefix("0x"))
    signing_address = str(sig.get("signing_address") or "").lower()
    if not signing_address.startswith("0x") or len(signing_address) != 42:
        raise RelayerError("NEAR AI signature response has no signing_address")
    outcome, inner = parse_vote(raw.decode())
    return {
        "model": model,
        "request": body,
        "response": raw,
        "signature": signature,
        "signing_address": signing_address,
        "outcome": outcome,
        "confidence": inner.get("confidence") if isinstance(inner, dict) else None,
    }


def account_status(market_id: int) -> int | None:
    info = rpc("getAccountInfo", [str(market_pda(market_id)), {"encoding": "base64"}])["value"]
    if not info:
        return None
    raw = base64.b64decode(info["data"][0])
    # Walk to the status byte. Question is the only variable field before it.
    # disc 8 + market_id 8 + bump 1 + string
    cursor = 8 + 8 + 1
    qlen = int.from_bytes(raw[cursor : cursor + 4], "little")
    cursor += 4 + qlen + 32 + 32 + 32 + 8 + 32 + 8 + 8 + 4 + (20 + 1 + 64) * 3
    return raw[cursor]


def demo() -> dict:
    key_path = os.environ.get("SOLANA_KEYPAIR", "")
    if not key_path:
        raise RelayerError("SOLANA_KEYPAIR is unset")
    if not os.environ.get("NEARAI_API_KEY"):
        raise RelayerError("NEARAI_API_KEY is unset")
    owner = load_keypair(key_path)
    # One key pays, creates both markets, and settles the escalated one.
    escalation = owner
    clear_rules = (
        "YES if both snapshots say Cody Schwab won GENESIS X3 Melee singles. "
        "NO if both say someone else won. If the snapshots disagree or the event is unfinished, AMBIGUOUS."
    )
    question = "GENESIS X3 Melee singles: did Cody Schwab win?"
    clear_evidence = evidence_bundle(
        question,
        clear_rules,
        "2026-02-15T23:59:00Z",
        [
            {
                "url": "https://liquipedia.net/smash/GENESIS/X3/Melee",
                "fetched_at": "2026-11-24T00:00:00Z",
                "sha256": sha("liquipedia-genesis-x3"),
                "text": "GENESIS X3 Melee singles, 13-15 February 2026, San Jose. 1st: Cody Schwab. 2nd: Jmook.",
            },
            {
                "url": "https://www.ssbwiki.com/Smasher:Jmook",
                "fetched_at": "2026-11-24T00:00:00Z",
                "sha256": sha("ssbwiki-jmook-genesis-x3"),
                "text": "GENESIS X3, 13-15 February 2026. Jmook placed 2nd of 981 in Melee singles. Cody Schwab won the event.",
            },
        ],
    )
    split_evidence = evidence_bundle(
        question,
        clear_rules,
        "2026-02-15T23:59:00Z",
        [
            {
                "url": "https://example.invalid/snapshot-a",
                "fetched_at": "2026-11-24T00:00:00Z",
                "sha256": sha("snapshot-a"),
                "text": "GENESIS X3 Melee singles final placement: 1st Cody Schwab.",
            },
            {
                "url": "https://example.invalid/snapshot-b",
                "fetched_at": "2026-11-24T00:00:00Z",
                "sha256": sha("snapshot-b"),
                "text": "GENESIS X3 Melee singles final placement: 1st Jmook.",
            },
        ],
    )
    # Ask the models before creating the market, so config can record the
    # addresses that actually signed. The request already names the market id.
    first_votes = [near_vote(model, 1, clear_rules, clear_evidence) for model in MODELS]
    report = {"owner": str(owner.pubkey()), "escalation_owner": str(escalation.pubkey()), "markets": []}
    report["config_tx"] = ensure_config(owner, [vote["signing_address"] for vote in first_votes])
    planned = (
        (1, question, clear_rules, clear_evidence, first_votes),
        (2, question, clear_rules, split_evidence, None),
    )
    for market_id, q, rules, evidence, ready in planned:
        votes = ready if ready is not None else [near_vote(model, market_id, rules, evidence) for model in MODELS]
        deadline = int(time.time()) + 20
        create_tx = create_market(owner, market_id, q, rules, evidence, escalation.pubkey(), deadline)
        while int(time.time()) < deadline + 2:
            time.sleep(2)
        txs = [create_tx]
        recorded = []
        for slot, vote in enumerate(votes):
            txs.extend(submit_signed_vote(owner, market_id, slot, vote["request"], vote["response"], vote["signature"]))
            recorded.append({
                "model": vote["model"],
                "outcome": vote["outcome"],
                "confidence": vote["confidence"],
                "signing_address": vote["signing_address"],
                "submit": txs[-1],
            })
        status = account_status(market_id)
        final_tx = None
        if market_id == 1 and status == 1:
            time.sleep(WINDOW + 5)
            final_tx = send(
                [ix("finalize", u64(market_id), [meta(market_pda(market_id), True), meta(SYSVAR_CLOCK, False)])],
                [owner],
            )
        elif status == 2:
            # Ambiguous: the escalation owner records that the contradictory bundle does not pick a winner.
            final_tx = send(
                [ix("escalation_resolve", u64(market_id) + u8(3), [
                    meta(market_pda(market_id), True),
                    meta(escalation.pubkey(), True, True),
                ])],
                [escalation],
            )
        report["markets"].append({
            "market_id": market_id,
            "question": q,
            "rules_hash": sha(rules),
            "evidence_hash": sha(evidence),
            "status_before_settle": status,
            "votes": recorded,
            "create": create_tx,
            "settle": final_tx,
            "transactions": txs + ([final_tx] if final_tx else []),
        })
    out = ROOT / "site" / "solana-markets.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"owner": report["owner"], "escalation_owner": report["escalation_owner"], "wrote": str(out.name)}, indent=2))
    return report


def main() -> None:
    if "--demo" in sys.argv:
        demo()
        return
    raise SystemExit("usage: python solana/relayer.py --demo")


if __name__ == "__main__":
    try:
        main()
    except RelayerError as exc:
        print(f"relayer: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
