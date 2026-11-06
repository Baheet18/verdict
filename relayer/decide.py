"""Pure status machine for one market. No network, no clock of its own."""
from __future__ import annotations

from dataclasses import dataclass, field


def as_u64(value, what: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"bad {what}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f"bad {what}")


def status_name(status) -> str:
    if isinstance(status, str):
        return status
    if isinstance(status, dict) and len(status) == 1:
        return next(iter(status))
    raise ValueError(f"unrecognized status: {status!r}")


def status_fields(status) -> dict:
    if isinstance(status, dict) and len(status) == 1:
        inner = next(iter(status.values()))
        return inner if isinstance(inner, dict) else {}
    return {}


@dataclass
class Decision:
    action: str  # skip, stop, finalize, commit_and_vote, vote
    level: str  # skip, stop, ok, error
    reason: str
    missing_models: list[str] = field(default_factory=list)


def _voted_models(market: dict) -> set[str]:
    out = set()
    for vote in market.get("votes") or []:
        model = vote.get("model")
        if isinstance(model, str) and model:
            out.add(model)
    return out


def decide(market: dict, now_ns: int) -> Decision:
    """What to do with one market at chain time ``now_ns``.

    TOO_EARLY is not decided here: the contract keeps the market Open, clears
    votes, and increments ``round``. The next pass takes the commit path again.
    """
    now = as_u64(now_ns, "now_ns")
    name = status_name(market.get("status"))
    owner = market.get("escalation_owner") or ""

    if name == "Final":
        return Decision("skip", "skip", "already Final")
    if name == "Disputed":
        return Decision(
            "stop",
            "stop",
            f"Disputed; left for escalation_owner {owner} (resolve_dispute is not called by the relayer)",
        )
    if name == "Escalated":
        return Decision(
            "stop",
            "stop",
            f"Escalated; left for escalation_owner {owner} (resolve_dispute is not called by the relayer)",
        )
    if name == "Proposed":
        fields = status_fields(market.get("status"))
        if "at_ns" not in fields:
            return Decision("stop", "error", "Proposed status is missing at_ns")
        at_ns = as_u64(fields["at_ns"], "at_ns")
        window = as_u64(market.get("dispute_window_ns"), "dispute_window_ns")
        if now >= at_ns + window:
            return Decision("finalize", "ok", "dispute window closed with no dispute")
        return Decision("skip", "skip", "Proposed; dispute window still open")
    if name != "Open":
        return Decision("stop", "error", f"unrecognized status {name}")

    deadline = as_u64(market.get("deadline_ns"), "deadline_ns")
    if now < deadline:
        return Decision("skip", "skip", "before deadline")

    evidence = market.get("round_evidence_hash")
    if not evidence:
        return Decision("commit_and_vote", "ok", "deadline passed; evidence not committed for this round")

    missing = [m for m in (market.get("resolver_models") or []) if m not in _voted_models(market)]
    if missing:
        return Decision("vote", "ok", "evidence committed; submitting missing panel votes", missing)
    return Decision("skip", "skip", "Open with evidence committed and no missing votes")
