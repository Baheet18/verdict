"""In-process chain that enforces the Verdict contract checks the relayer depends on.

This is not a NEAR node. It mirrors the panic strings and the order of checks in
``commit_evidence``, ``submit_verdict``, and ``finalize`` so a mock run cannot skip
evidence locking, signature recovery, model binding, or replay protection.
"""
from __future__ import annotations

import copy

from panel import recover_signer, signed_text
from verdict_request import check_request, parse_outcome, sha

_API_OUTCOME = {"YES": "Yes", "NO": "No", "TOO_EARLY": "TooEarly", "AMBIGUOUS": "Ambiguous"}
CHAIN_TO_API = {v: k for k, v in _API_OUTCOME.items()}


class ChainError(Exception):
    pass


def _hex20(address: str) -> str:
    if not isinstance(address, str):
        raise ChainError("bad address")
    raw = address[2:] if address.startswith("0x") else address
    raw = raw.lower()
    if len(raw) != 40 or any(c not in "0123456789abcdef" for c in raw):
        raise ChainError("bad address")
    return "0x" + raw


def _hex32(value: str, what: str) -> str:
    if not isinstance(value, str):
        raise ChainError(f"bad {what}")
    raw = value.lower()
    if len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
        raise ChainError(f"bad {what}")
    return raw


class MockChain:
    kind = "mock"

    def __init__(self):
        self.owner = "owner.local"
        self.predecessor = self.owner
        self.relayers: set[str] = set()
        self.signers: dict[str, str] = {}
        self.attestations: dict[str, str] = {}
        self.markets: list[dict] = []
        self.used_signed: set[str] = set()
        self.now_ns = 0

    def set_time(self, now_ns: int) -> None:
        self.now_ns = now_ns

    def _only_owner(self) -> None:
        if self.predecessor != self.owner:
            raise ChainError("owner only")

    def _only_relayer(self) -> None:
        if self.predecessor != self.owner and self.predecessor not in self.relayers:
            raise ChainError("relayer or owner only")

    def register_signer(self, address: str, model: str, attestation_report_sha256: str) -> None:
        self._only_owner()
        if not isinstance(model, str) or model == "":
            raise ChainError("model required")
        digest = _hex32(attestation_report_sha256, "report hash")
        addr = _hex20(address)
        self.signers[addr] = model
        self.attestations[addr] = digest

    def register_relayer(self, account: str) -> None:
        self._only_owner()
        self.relayers.add(account)

    def get_signer_model(self, address: str) -> str | None:
        try:
            addr = _hex20(address)
        except ChainError:
            return None
        return self.signers.get(addr)

    def create_market(self, question: str, rules: str, sources: list[str], prompt_template_hash: str,
                      resolvers: list[str], deadline_ns: int, dispute_window_ns: int, dispute_bond: str) -> int:
        addrs = []
        for resolver in resolvers:
            addrs.append(_hex20(resolver))
        addrs = sorted(set(addrs))
        if not addrs:
            raise ChainError("bad resolver set")
        for addr in addrs:
            if addr not in self.signers:
                raise ChainError("resolver not registered")
        if deadline_ns <= self.now_ns:
            raise ChainError("deadline in the past")
        if not sources:
            raise ChainError("lock at least one source")
        rules_hash = sha(rules)
        prompt_hash = _hex32(prompt_template_hash, "prompt_template_hash")
        self.markets.append({
            "question": question,
            "rules": rules,
            "rules_hash": rules_hash,
            "prompt_template_hash": prompt_hash,
            "sources": list(sources),
            "resolvers": addrs,
            "threshold": len(addrs),
            "deadline_ns": deadline_ns,
            "dispute_window_ns": dispute_window_ns,
            "dispute_bond": dispute_bond,
            "yes_pool": "0",
            "no_pool": "0",
            "round": 0,
            "round_evidence_hash": None,
            "round_evidence_uri": None,
            "votes": [],
            "status": "Open",
        })
        return len(self.markets) - 1

    def get_market(self, market_id: int):
        if not isinstance(market_id, int) or isinstance(market_id, bool):
            return None
        if market_id < 0 or market_id >= len(self.markets):
            return None
        return copy.deepcopy(self.markets[market_id])

    def commit_evidence(self, market_id: int, round: int, evidence_bundle_hash: str, evidence_uri: str) -> None:
        self._only_relayer()
        if not isinstance(evidence_uri, str) or evidence_uri == "":
            raise ChainError("evidence_uri required")
        digest = _hex32(evidence_bundle_hash, "evidence_bundle_hash")
        market = self._market(market_id)
        if market["status"] != "Open":
            raise ChainError("market not open")
        if self.now_ns < market["deadline_ns"]:
            raise ChainError("market not ended")
        if round != market["round"]:
            raise ChainError("wrong round")
        if market["votes"]:
            raise ChainError("votes already cast this round")
        if market["round_evidence_hash"] is not None and self.predecessor != self.owner:
            raise ChainError("owner only to replace evidence")
        market["round_evidence_hash"] = digest
        market["round_evidence_uri"] = evidence_uri

    def submit_verdict(self, market_id: int, request_raw: str, response_raw: str, signature: str) -> str:
        self._only_relayer()
        market = self._market(market_id)
        if self.now_ns < market["deadline_ns"]:
            raise ChainError("market not ended")
        if market["status"] != "Open":
            raise ChainError("not accepting verdicts")
        try:
            req_model, evidence = check_request(
                request_raw, market_id, market["round"], market["rules_hash"], market["prompt_template_hash"],
            )
        except ValueError as exc:
            raise ChainError(str(exc)) from None
        text = signed_text(req_model, request_raw, response_raw)
        signer = recover_signer(text, signature)
        if signer is None:
            raise ChainError("bad signature")
        reg_model = self.signers.get(signer)
        if reg_model is None:
            raise ChainError("signer not attested/registered")
        if reg_model != req_model:
            raise ChainError("signer is registered for a different model")
        if signer not in market["resolvers"]:
            raise ChainError("signer not in market resolver set")
        text_h = sha(text)
        if text_h in self.used_signed:
            raise ChainError("signed response already used")
        if any(vote["signer"] == signer for vote in market["votes"]):
            raise ChainError("signer already voted")
        committed = market["round_evidence_hash"]
        if committed is None:
            raise ChainError("evidence not committed for this round")
        if committed != evidence:
            raise ChainError("evidence bundle differs from committed hash")
        outcome = parse_outcome(response_raw)
        if outcome is None:
            raise ChainError("no valid outcome in response")
        chain_outcome = _API_OUTCOME[outcome]
        self.used_signed.add(text_h)
        market["votes"].append({
            "signer": signer,
            "model": req_model,
            "outcome": chain_outcome,
            "request_sha256": sha(request_raw),
            "response_sha256": sha(response_raw),
        })
        if len(market["votes"]) == market["threshold"]:
            all_same = all(vote["outcome"] == chain_outcome for vote in market["votes"])
            if all_same and chain_outcome in ("Yes", "No"):
                market["status"] = {"Proposed": {"outcome": chain_outcome, "at_ns": self.now_ns}}
            elif all_same and chain_outcome == "TooEarly":
                market["votes"] = []
                market["round"] += 1
                market["round_evidence_hash"] = None
                market["round_evidence_uri"] = None
                market["status"] = "Open"
            else:
                market["status"] = "Escalated"
        return outcome

    def finalize(self, market_id: int) -> None:
        market = self._market(market_id)
        status = market["status"]
        if not isinstance(status, dict) or "Proposed" not in status:
            raise ChainError("not proposed")
        proposed = status["Proposed"]
        if self.now_ns < proposed["at_ns"] + market["dispute_window_ns"]:
            raise ChainError("dispute window open")
        market["status"] = {"Final": {"outcome": proposed["outcome"]}}

    def _market(self, market_id: int) -> dict:
        if not isinstance(market_id, int) or isinstance(market_id, bool) or market_id < 0 or market_id >= len(self.markets):
            raise ChainError("no market")
        return self.markets[market_id]
