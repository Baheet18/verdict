#!/usr/bin/env python3
"""Relayer for one Verdict market.

After the deadline it locks one evidence bundle, publishes the bytes, commits the hash,
asks each of the three panel models for a signature over that same bundle, and submits
the votes. A unanimous YES or NO can then be finalized once the dispute window has elapsed.

Mock mode runs this against scripts/mock_chain.py and a scripted panel. It does not
contact NEAR or NEAR AI and it does not read credentials.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import threading
import time

from evidence_uri import pending_uri
from mock_chain import ChainError, MockChain
from panel import PANEL_MODELS, Completion, MockPanel, PanelError, recover_signer, signed_text
from publish_evidence import publish_evidence_body
from verdict_request import (
    OUTCOMES,
    PROMPT_TEMPLATE_HASH,
    build_request,
    check_request,
    evidence_bundle,
    sha,
)

ONE_NEAR = "1000000000000000000000000"
DEFAULT_DISPUTE_WINDOW_NS = 3_600_000_000_000
# On every mock vote object. Not a README trust label and not a TEE result.
MOCK_VOTE_LABEL = "MOCK: not a real model signature"
ROOT = pathlib.Path(__file__).resolve().parent.parent

_CREATE_FIELDS = {
    "question",
    "rules",
    "sources",
    "deadline_ns",
    "resolvers",
    "prompt_template_hash",
    "dispute_window_ns",
    "dispute_bond_yocto",
    "source_snapshots",
    "market_end",
    "mock_script",
}


class RelayerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def status_name(status) -> str:
    if isinstance(status, str):
        return status
    if isinstance(status, dict) and len(status) == 1:
        return next(iter(status))
    return "Unknown"


def _u64(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**64 - 1:
        raise RelayerError(f"{name} must be an integer")
    return value


def _http_url(url: str) -> str:
    if not isinstance(url, str) or any(ch in url for ch in " \n\r\t"):
        raise RelayerError("source must be an http(s) URL")
    if not (url.startswith("https://") or url.startswith("http://")):
        raise RelayerError("source must be an http(s) URL")
    rest = url.split("://", 1)[1]
    host = rest.split("/", 1)[0]
    if not host or "@" in host:
        raise RelayerError("source must be an http(s) URL without userinfo")
    return url


def _yocto(value: str) -> str:
    if not isinstance(value, str) or not value.isdigit() or (len(value) > 1 and value.startswith("0")):
        raise RelayerError("dispute_bond_yocto must be a decimal string")
    if int(value) > 2**128 - 1:
        raise RelayerError("dispute_bond_yocto out of range")
    return value


class Relayer:
    def __init__(self, chain, panel, evidence_dir: pathlib.Path, fetch=None):
        self.chain = chain
        self.panel = panel
        self.evidence_dir = pathlib.Path(evidence_dir)
        self.fetch = fetch
        self.account = "relayer.local"
        self.records: dict[int, dict] = {}
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    def create_market(self, spec: dict) -> dict:
        with self.lock:
            return self._create(spec)

    def relay(self, market_id: int) -> dict:
        with self.lock:
            self.chain.predecessor = self.account
            return self._relay(market_id)

    def tick(self) -> dict:
        results = []
        with self.lock:
            ids = list(range(len(self.chain.markets)))
        for market_id in ids:
            try:
                results.append({"market_id": market_id, "ok": True, "market": self.relay(market_id)})
            except (RelayerError, ChainError) as exc:
                results.append({"market_id": market_id, "ok": False, "error": str(exc)})
        return {"results": results}

    def view(self, market_id: int, *, include_raw: bool = True) -> dict:
        with self.lock:
            return self._view(market_id, include_raw=include_raw, include_rules=True)

    def list_markets(self) -> list[dict]:
        with self.lock:
            return [
                self._view(i, include_raw=False, include_rules=False)
                for i in range(len(self.chain.markets))
            ]

    def set_clock(self, now_ns: int) -> dict:
        now_ns = _u64(now_ns, "now_ns")
        with self.lock:
            self.chain.set_time(now_ns)
            return {"now_ns": self.chain.now_ns}

    def set_mock_script(self, market_id: int, script) -> dict:
        if self.panel.execution != "scripted":
            raise RelayerError("not found", 404)
        with self.lock:
            market = self.chain.get_market(market_id)
            if market is None or market_id not in self.records:
                raise RelayerError("no market", 404)
            if status_name(market["status"]) != "Open" or market["votes"]:
                raise RelayerError("mock script can only be set while the round has no votes", 409)
            self.records[market_id]["mock_script"] = self._parse_script(script)
            return self._view(market_id, include_raw=True, include_rules=True)

    def _create(self, spec: dict) -> dict:
        if not isinstance(spec, dict):
            raise RelayerError("body must be a JSON object")
        unknown = sorted(set(spec) - _CREATE_FIELDS)
        if unknown:
            raise RelayerError("unknown field: " + ", ".join(unknown))
        if self.panel.execution != "scripted" and "mock_script" in spec:
            raise RelayerError("mock_script is only available in mock mode")
        question = spec.get("question")
        rules = spec.get("rules")
        if not isinstance(question, str) or not question.strip() or len(question) > 4_000:
            raise RelayerError("question is required")
        if not isinstance(rules, str) or not rules.strip() or len(rules) > 20_000:
            raise RelayerError("rules are required")
        sources = spec.get("sources")
        if not isinstance(sources, list) or not sources or len(sources) > 20:
            raise RelayerError("sources must be a non-empty list of http(s) URLs")
        sources = [_http_url(url) for url in sources]
        if len(set(sources)) != len(sources):
            raise RelayerError("sources must be unique")
        deadline_ns = _u64(spec.get("deadline_ns"), "deadline_ns")
        if deadline_ns <= self.chain.now_ns:
            raise RelayerError("deadline in the past")
        prompt_hash = spec.get("prompt_template_hash", PROMPT_TEMPLATE_HASH)
        if prompt_hash != PROMPT_TEMPLATE_HASH:
            raise RelayerError("only the shipped prompt template is supported")
        window = spec.get("dispute_window_ns", DEFAULT_DISPUTE_WINDOW_NS)
        window = _u64(window, "dispute_window_ns")
        bond = _yocto(spec.get("dispute_bond_yocto", ONE_NEAR))
        market_end = spec.get("market_end", "")
        if not isinstance(market_end, str) or len(market_end) > 80 or any(ch in market_end for ch in "\n\r"):
            raise RelayerError("market_end must be a short single-line string")
        resolvers = self._resolvers(spec.get("resolvers"))
        snapshots = self._provided_snapshots(sources, spec.get("source_snapshots"))
        script = self._parse_script(spec["mock_script"]) if "mock_script" in spec else {}
        args = {
            "question": question,
            "rules": rules,
            "sources": sources,
            "prompt_template_hash": prompt_hash,
            "resolvers": resolvers,
            "deadline_ns": deadline_ns,
            "dispute_window_ns": window,
            "dispute_bond": bond,
        }
        market_id = self.chain.create_market(**args)
        self.calls.append({"method": "create_market", "market_id": market_id, "args": args})
        self.records[market_id] = {
            "market_end": market_end,
            "provided_snapshots": snapshots,
            "mock_script": script,
            "signed": [],
            "panel_errors": {},
            "last_round_outcome": None,
        }
        return self._view(market_id, include_raw=True, include_rules=True)

    def _resolvers(self, supplied) -> list[str]:
        if not hasattr(self.panel, "resolvers"):
            raise RelayerError("this panel does not publish a resolver set")
        want = [row["address"] for row in self.panel.resolvers()]
        if supplied is None:
            return want
        if not isinstance(supplied, list) or not supplied:
            raise RelayerError("resolvers must be the three mock_signer values from GET /v1/resolvers")
        try:
            got = []
            for addr in supplied:
                raw = addr[2:] if isinstance(addr, str) and addr.startswith("0x") else addr
                if not isinstance(raw, str):
                    raise RelayerError("resolvers must be the three mock_signer values from GET /v1/resolvers")
                raw = raw.lower()
                if len(raw) != 40 or any(c not in "0123456789abcdef" for c in raw):
                    raise RelayerError("resolvers must be the three mock_signer values from GET /v1/resolvers")
                got.append("0x" + raw)
        except RelayerError:
            raise
        if sorted(got) != sorted(want):
            raise RelayerError("resolvers must be exactly the three mock_signer values from GET /v1/resolvers")
        return want

    def _parse_script(self, script) -> dict:
        if script is None:
            return {}
        if not isinstance(script, dict):
            raise RelayerError("mock_script must be an object of model to outcome")
        out = {}
        for model, outcome in script.items():
            if model not in PANEL_MODELS:
                raise RelayerError("mock_script has a model that is not on the panel")
            if outcome not in OUTCOMES:
                raise RelayerError("mock_script outcome must be YES, NO, TOO_EARLY, or AMBIGUOUS")
            out[model] = outcome
        return out

    def _provided_snapshots(self, sources: list[str], provided) -> dict:
        if provided is None:
            return {}
        if not isinstance(provided, list):
            raise RelayerError("source_snapshots must be a list")
        by_url = {}
        allowed = set(sources)
        for snap in provided:
            if not isinstance(snap, dict):
                raise RelayerError("source snapshot must be an object")
            url = snap.get("url")
            text = snap.get("text")
            if url not in allowed:
                raise RelayerError("snapshot url is not in the locked source list")
            if url in by_url:
                raise RelayerError("duplicate snapshot url")
            if not isinstance(text, str) or text == "":
                raise RelayerError("snapshot text required")
            if len(text) > 200_000:
                raise RelayerError("snapshot text is too large")
            digest = sha(text)
            if "sha256" in snap and str(snap["sha256"]).lower() != digest:
                raise RelayerError("snapshot sha256 does not match text")
            fetched_at = snap.get("fetched_at")
            if fetched_at is not None and (not isinstance(fetched_at, str) or not fetched_at or len(fetched_at) > 80 or any(ch in fetched_at for ch in "\n\r")):
                raise RelayerError("snapshot fetched_at must be a short single-line string")
            by_url[url] = {"url": url, "text": text, "sha256": digest, "fetched_at": fetched_at}
        return by_url

    def _relay(self, market_id: int) -> dict:
        market = self.chain.get_market(market_id)
        if market is None or market_id not in self.records:
            raise RelayerError("no market", 404)
        name = status_name(market["status"])
        if name in ("Final", "Escalated", "Disputed"):
            return self._view(market_id, include_raw=True, include_rules=True)
        if name == "Proposed":
            self._maybe_finalize(market_id, market)
            return self._view(market_id, include_raw=True, include_rules=True)
        if name != "Open":
            return self._view(market_id, include_raw=True, include_rules=True)
        if self.chain.now_ns < market["deadline_ns"]:
            raise RelayerError("market not ended", 409)
        self._lock_and_commit(market_id)
        self._vote(market_id)
        market = self.chain.get_market(market_id)
        if status_name(market["status"]) == "Proposed":
            self._maybe_finalize(market_id, market)
        return self._view(market_id, include_raw=True, include_rules=True)

    def _maybe_finalize(self, market_id: int, market: dict) -> None:
        proposed = market["status"]["Proposed"]
        if self.chain.now_ns >= proposed["at_ns"] + market["dispute_window_ns"]:
            self.chain.finalize(market_id)
            self.calls.append({"method": "finalize", "market_id": market_id, "args": {"market_id": market_id}})

    def _lock_and_commit(self, market_id: int) -> None:
        market = self.chain.get_market(market_id)
        record = self.records[market_id]
        if market["round_evidence_hash"]:
            stored = record.get("evidence")
            if not isinstance(stored, str) or sha(stored) != market["round_evidence_hash"]:
                raise RelayerError("evidence is committed but the bytes are not in this process; refusing to gather a replacement", 409)
            return
        snapshots = self._lock_snapshots(record, market["sources"])
        evidence = evidence_bundle(market["question"], market["rules"], record["market_end"], snapshots)
        digest = sha(evidence)
        uri = pending_uri(f"evidence/{digest}.json")
        if "github.com" in uri or "/master/" in uri or "/main/" in uri or uri.startswith("https://"):
            raise RelayerError("refusing evidence_uri that is not a pending commit-SHA placeholder")
        _digest, path = publish_evidence_body(
            evidence.encode("utf-8"),
            self.evidence_dir,
            {
                "evidence": evidence,
                "question": market["question"],
                "market_id": market_id,
                "round": market["round"],
                "sources": market["sources"],
            },
            ensure_ascii=False,
        )
        loaded = json.loads(path.read_text())
        if loaded.get("evidence") != evidence or loaded.get("evidence_bundle_hash") != digest or sha(loaded["evidence"]) != digest:
            raise RelayerError("published evidence does not round-trip")
        args = {
            "market_id": market_id,
            "round": market["round"],
            "evidence_bundle_hash": digest,
            "evidence_uri": uri,
        }
        self.chain.commit_evidence(**args)
        record["evidence"] = evidence
        record["evidence_bundle_hash"] = digest
        record["evidence_uri"] = uri
        self.calls.append({"method": "commit_evidence", "market_id": market_id, "args": args})

    def _lock_snapshots(self, record: dict, sources: list[str]) -> list[dict]:
        if "locked_snapshots" in record:
            return record["locked_snapshots"]
        provided = record["provided_snapshots"]
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        locked = []
        for url in sources:
            if url in provided:
                row = dict(provided[url])
                if not row.get("fetched_at"):
                    row["fetched_at"] = stamp
                locked.append({
                    "url": row["url"],
                    "fetched_at": row["fetched_at"],
                    "sha256": row["sha256"],
                    "text": row["text"],
                })
                continue
            if self.fetch is None:
                raise RelayerError(
                    f"missing source snapshot for {url}. Pass source_snapshots for every source, or start with --fetch",
                    409,
                )
            try:
                text = self.fetch(url)
            except Exception as exc:
                raise RelayerError(f"source fetch failed for {url}: {type(exc).__name__}") from None
            if not isinstance(text, str) or text == "":
                raise RelayerError(f"source fetch failed for {url}: empty")
            if len(text) > 200_000:
                text = text[:200_000]
            locked.append({"url": url, "fetched_at": stamp, "sha256": sha(text), "text": text})
        record["locked_snapshots"] = locked
        return locked

    def _vote(self, market_id: int) -> None:
        market = self.chain.get_market(market_id)
        if status_name(market["status"]) != "Open":
            return
        record = self.records[market_id]
        evidence = record.get("evidence")
        if not market["round_evidence_hash"] or not isinstance(evidence, str) or sha(evidence) != market["round_evidence_hash"]:
            raise RelayerError("evidence not committed for this round", 409)
        rnd = market["round"]
        if self.panel.execution == "scripted":
            script = {model: "AMBIGUOUS" for model in PANEL_MODELS}
            script.update(record.get("mock_script") or {})
            self.panel.script = script
        for model in PANEL_MODELS:
            market = self.chain.get_market(market_id)
            if market["round"] != rnd or status_name(market["status"]) != "Open":
                return
            if any(vote["model"] == model for vote in market["votes"]):
                continue
            request_raw = build_request(model, market_id, rnd, market["rules"], evidence)
            request_text = request_raw.decode("utf-8")
            if request_text.encode("utf-8") != request_raw:
                raise RelayerError("request is not utf-8")
            try:
                completion = self.panel.complete(model, request_raw)
            except PanelError as exc:
                record["panel_errors"][model] = str(exc)
                continue
            record["panel_errors"].pop(model, None)
            if not isinstance(completion, Completion):
                record["panel_errors"][model] = "model call failed"
                continue
            text = signed_text(model, request_raw, completion.response_raw)
            signer = recover_signer(text, completion.signature)
            expected = self._address_for(model)
            if signer is None or signer != expected or self.chain.signers.get(signer) != model:
                record["panel_errors"][model] = "signature for this model did not recover to the registered signer"
                continue
            before_round = market["round"]
            outcome = self.chain.submit_verdict(market_id, request_text, completion.response_raw, completion.signature)
            self.calls.append({
                "method": "submit_verdict",
                "market_id": market_id,
                "args": {
                    "market_id": market_id,
                    "request_raw": request_text,
                    "response_raw": completion.response_raw,
                    "signature": completion.signature,
                },
            })
            record["signed"].append({
                "round": rnd,
                "model": model,
                "signer": signer,
                "request_raw": request_text,
                "response_raw": completion.response_raw,
                "signature": completion.signature,
                "outcome": outcome,
            })
            after = self.chain.get_market(market_id)
            if after["round"] != before_round:
                record["last_round_outcome"] = "TOO_EARLY"
                record.pop("evidence", None)
                record.pop("locked_snapshots", None)
                return

    def _address_for(self, model: str) -> str:
        found = [addr for addr, name in self.chain.signers.items() if name == model]
        if len(found) != 1:
            raise RelayerError(f"expected one registered signer for {model}")
        return found[0]

    def _view(self, market_id: int, *, include_raw: bool, include_rules: bool) -> dict:
        market = self.chain.get_market(market_id)
        if market is None or market_id not in self.records:
            raise RelayerError("no market", 404)
        record = self.records[market_id]
        name = status_name(market["status"])
        votes_out = []
        for vote in market["votes"]:
            stored = self._stored_for(record, market["round"], vote)
            checked = self._recheck(market_id, market, vote, stored) if stored else None
            signature_ok = bool(checked and checked["signature_ok"])
            model_ok = bool(checked and checked["model_binding_ok"])
            row = {
                # ``model`` is the script slot id written into the canonical request.
                # It is not a claim that a TEE for that model produced the signature.
                "model": vote["model"],
                "script_slot": vote["model"],
                "mock_signer": vote["signer"],
                "tee_signer": False,
                "outcome": CHAIN_TO_API_OUTCOME[vote["outcome"]],
                "request_sha256": vote["request_sha256"],
                "response_sha256": vote["response_sha256"],
                "evidence_bundle_hash": checked["evidence_bundle_hash"] if checked else None,
                "signature_ok": signature_ok,
                "model_binding_ok": model_ok,
                "label": MOCK_VOTE_LABEL,
            }
            if include_raw and stored:
                row["request_raw"] = stored["request_raw"]
                row["response_raw"] = stored["response_raw"]
                row["signature"] = stored["signature"]
            votes_out.append(row)
        votes_verified = (
            len(votes_out) == market["threshold"]
            and all(row["signature_ok"] and row["model_binding_ok"] for row in votes_out)
            and all(row["evidence_bundle_hash"] == market["round_evidence_hash"] for row in votes_out)
        )
        proposed = None
        final = None
        if name == "Proposed":
            proposed = CHAIN_TO_API_OUTCOME[market["status"]["Proposed"]["outcome"]]
        elif name == "Final":
            final = CHAIN_TO_API_OUTCOME[market["status"]["Final"]["outcome"]]
        if not votes_verified:
            proposed_out = None
            final_out = None
        else:
            proposed_out = proposed
            final_out = final
        notes = [
            "The mock chain is not authoritative. contract/src/lib.rs is the source of truth.",
            MOCK_VOTE_LABEL + " Each mock_signer is a process-local key, not a Qwen, DeepSeek, or GLM TEE key.",
            "The relayer chooses and commits the evidence. Request-parameter steering is rejected; that does not make evidence selection trustless.",
        ]
        if self.panel.execution == "scripted":
            notes.append("Mock mode scripted these outcomes. It did not call Qwen, DeepSeek, or GLM.")
        if name == "Escalated":
            notes.append("The panel did not unanimously return YES or NO. No outcome was invented.")
        if record.get("last_round_outcome") == "TOO_EARLY":
            notes.append("A unanimous TOO_EARLY cleared a previous round. Old signatures cannot be reused.")
        evidence_hash = market["round_evidence_hash"]
        body = {
            "market_id": market_id,
            "chain": self.chain.kind,
            "panel_execution": self.panel.execution,
            "question": market["question"],
            "rules_hash": market["rules_hash"],
            "prompt_template_hash": market["prompt_template_hash"],
            "sources": market["sources"],
            "mock_resolvers": [self._public_resolver(addr) for addr in market["resolvers"]],
            "deadline_ns": market["deadline_ns"],
            "dispute_window_ns": market["dispute_window_ns"],
            "dispute_bond_yocto": market["dispute_bond"],
            "round": market["round"],
            "status": name,
            "status_raw": market["status"],
            "final": name == "Final" and votes_verified,
            "escalated": name == "Escalated",
            "proposed_outcome": proposed_out,
            "final_outcome": final_out,
            "last_round_outcome": record.get("last_round_outcome"),
            "votes": votes_out,
            "votes_verified": votes_verified,
            "threshold": market["threshold"],
            "evidence_bundle_hash": evidence_hash,
            "evidence_uri": market["round_evidence_uri"],
            "evidence_api_path": f"/v1/evidence/{evidence_hash}" if evidence_hash else None,
            "chain_calls": [public_call(call) for call in self.calls if call["market_id"] == market_id],
            "panel_errors": [{"model": model, "error": err} for model, err in sorted(record.get("panel_errors", {}).items())],
            "verification_notes": notes,
            "now_ns": self.chain.now_ns,
        }
        if include_rules:
            body["rules"] = market["rules"]
        return body

    def _public_resolver(self, address: str) -> dict:
        # This process has no TEE attestation to attach. The address is a
        # process-local mock key even when a test injects LivePanel.
        return {
            "mock_signer": address,
            "script_slot": self.chain.signers.get(address),
            "tee_signer": False,
            "label": MOCK_VOTE_LABEL,
        }

    def _stored_for(self, record: dict, rnd: int, vote: dict):
        for stored in record.get("signed", []):
            if stored["round"] != rnd or stored["signer"] != vote["signer"]:
                continue
            if sha(stored["request_raw"]) == vote["request_sha256"] and sha(stored["response_raw"]) == vote["response_sha256"]:
                return stored
        return None

    def _recheck(self, market_id: int, market: dict, vote: dict, stored: dict):
        text = signed_text(stored["model"], stored["request_raw"], stored["response_raw"])
        signer = recover_signer(text, stored["signature"])
        try:
            model, evidence = check_request(
                stored["request_raw"], market_id, market["round"], market["rules_hash"], market["prompt_template_hash"],
            )
        except ValueError:
            return {"signature_ok": False, "model_binding_ok": False, "evidence_bundle_hash": None}
        signature_ok = signer is not None and signer == stored["signer"] == vote["signer"]
        model_ok = signature_ok and self.chain.signers.get(signer) == model == vote["model"] == stored["model"]
        return {"signature_ok": signature_ok, "model_binding_ok": model_ok, "evidence_bundle_hash": evidence}


CHAIN_TO_API_OUTCOME = {
    "Yes": "YES",
    "No": "NO",
    "TooEarly": "TOO_EARLY",
    "Ambiguous": "AMBIGUOUS",
}


def public_call(call: dict) -> dict:
    method = call["method"]
    args = call["args"]
    if method == "create_market":
        return {"method": method, "market_id": call["market_id"]}
    if method == "commit_evidence":
        return {
            "method": method,
            "market_id": args["market_id"],
            "round": args["round"],
            "evidence_bundle_hash": args["evidence_bundle_hash"],
            "evidence_uri": args["evidence_uri"],
        }
    if method == "submit_verdict":
        return {
            "method": method,
            "market_id": args["market_id"],
            "request_sha256": sha(args["request_raw"]),
            "response_sha256": sha(args["response_raw"]),
        }
    if method == "finalize":
        return {"method": method, "market_id": args["market_id"]}
    return {"method": method, "market_id": call["market_id"]}


def build_mock(evidence_dir, *, now_ns: int | None = None, fetch=None, panel=None) -> Relayer:
    panel = panel or MockPanel()
    chain = MockChain()
    for row in panel.resolvers():
        chain.register_signer(row["address"], row["model"], "ab" * 32)
    chain.register_relayer("relayer.local")
    chain.set_time(time.time_ns() if now_ns is None else now_ns)
    return Relayer(chain, panel, evidence_dir, fetch=fetch)


def run_demo(evidence_dir) -> dict:
    relayer = build_mock(evidence_dir, now_ns=1_000_000_000)
    created = relayer.create_market({
        "question": "Did the example snapshots agree?",
        "rules": "YES only if a later scripted panel says YES. This demo does not ask the models.",
        "sources": ["https://example.com/a", "https://example.com/b"],
        "deadline_ns": 2_000_000_000,
        "dispute_window_ns": 0,
        "market_end": "2026-11-06T00:00:00Z",
        "source_snapshots": [
            {"url": "https://example.com/a", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot A"},
            {"url": "https://example.com/b", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot B"},
        ],
        "mock_script": {model: "YES" for model in PANEL_MODELS},
    })
    relayer.set_clock(2_000_000_000)
    return relayer.relay(created["market_id"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run one mock Verdict market through the relayer. No NEAR and no API key.")
    parser.add_argument("command", choices=["demo"])
    parser.add_argument("--data-dir", default="out/relayer-demo")
    args = parser.parse_args(argv)
    data = pathlib.Path(args.data_dir)
    if not data.is_absolute():
        data = ROOT / data
    _refuse_fixtures(data)
    data.mkdir(parents=True, exist_ok=True)
    view = run_demo(data / "evidence")
    print(json.dumps({
        "market_id": view["market_id"],
        "status": view["status"],
        "final_outcome": view["final_outcome"],
        "votes_verified": view["votes_verified"],
        "evidence_bundle_hash": view["evidence_bundle_hash"],
        "evidence_uri": view["evidence_uri"],
        "panel_execution": view["panel_execution"],
        "chain": view["chain"],
    }, indent=2))
    return 0


def _refuse_fixtures(path: pathlib.Path) -> None:
    resolved = path.resolve()
    fixtures = (ROOT / "fixtures").resolve()
    if resolved == fixtures or fixtures in resolved.parents:
        raise SystemExit("refusing to write evidence under fixtures/")


if __name__ == "__main__":
    raise SystemExit(main())
