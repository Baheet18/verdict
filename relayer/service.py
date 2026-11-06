"""One pass over markets: commit evidence, collect votes, finalize when the window is shut.

Markets are handled independently. A failure on one id does not stop the others.
Unanimous TOO_EARLY is reported. The next evidence commit waits ``too_early_retry_s``
(default 6 hours, minimum 1 hour). One pass commits at most ``max_rounds_per_pass``
rounds (default 1). After ``max_rounds`` (default 5) the market is left for
``escalate_stale``. Split or unanimous AMBIGUOUS becomes Escalated on chain; this
process stops and does not call resolve_dispute.
"""
from __future__ import annotations

import json
from pathlib import Path

from evidence_uri import pending_uri
from verdict_request import PROMPT_TEMPLATE_HASH, sha

from relayer import PANEL_MODELS
from relayer.decide import decide, status_name
from relayer.panel import PanelError, check_vote
from relayer.publish import PublishError, _assert_permalink
from relayer.sources import build_bundle, gather, iso_from_ns, wrapper_bytes


class NotReady(RuntimeError):
    pass


def load_markets_file(path: str | Path) -> tuple[int, dict[int, dict]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    markets = {}
    for row in data["markets"]:
        markets[int(row["id"])] = row["market"]
    return int(data["now_ns"]), markets


def _check_bindings(market: dict) -> None:
    rules = market.get("rules") or ""
    rules_hash = str(market.get("rules_hash") or "").lower()
    if sha(rules) != rules_hash:
        raise RuntimeError("rules do not hash to the on-chain rules_hash")
    if str(market.get("prompt_template_hash") or "").lower() != PROMPT_TEMPLATE_HASH:
        raise RuntimeError("prompt_template_hash does not match scripts/verdict_request.SYSTEM_PROMPT; refusing to send a different prompt")


def load_committed_evidence(fetch, market: dict) -> str:
    uri = market.get("round_evidence_uri") or ""
    if not uri or uri.startswith("PENDING_COMMIT_SHA://"):
        raise RuntimeError("committed evidence URI is missing or still pending; refusing to vote a different bundle")
    status, body = fetch(uri)
    if status != 200 or not body:
        raise RuntimeError(f"evidence URI returned HTTP {status}; refusing to rebuild a different bundle")
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("evidence"), str):
        evidence = obj["evidence"]
    else:
        evidence = body.decode("utf-8")
    digest = market.get("round_evidence_hash")
    if sha(evidence) != digest:
        raise RuntimeError("downloaded evidence does not match round_evidence_hash")
    return evidence


def _dry_calls(market_id: int, market: dict, decision) -> list[dict]:
    rnd = int(market.get("round") or 0)
    calls = []
    if decision.action == "commit_and_vote":
        calls.append({
            "method": "commit_evidence",
            "sent": False,
            "tx_hash": None,
            "market_id": market_id,
            "round": rnd,
            "sources": list(market.get("sources") or []),
            "evidence_uri": pending_uri("fixtures/evidence/<bundle-sha256-after-fetch>.json"),
            "note": "dry-run does not fetch sources or invent a commit SHA",
        })
        models = list(market.get("resolver_models") or [])
    else:
        models = list(decision.missing_models)
    for model in models:
        calls.append({
            "method": "submit_verdict",
            "sent": False,
            "tx_hash": None,
            "market_id": market_id,
            "round": rnd,
            "model": model,
            "evidence_bundle_hash": market.get("round_evidence_hash"),
            "note": "dry-run does not invent a signature",
        })
    return calls


def _send(env, method: str, args: dict, **extra) -> dict:
    result = env.send(method, args)
    out = {
        "method": method,
        "sent": bool(result.get("sent")),
        "tx_hash": result.get("tx_hash"),
        "explorer_url": result.get("explorer_url"),
    }
    out.update(extra)
    return out


def _commit(env, market_id: int, market: dict, now_ns: int) -> tuple[list[dict], str]:
    _check_bindings(market)
    fetched_at = iso_from_ns(now_ns)
    gathered = gather(market, env.fetch, fetched_at)
    if not gathered.ready:
        raise NotReady(gathered.reason)
    evidence = build_bundle(market, gathered.bundle_snaps())
    digest = sha(evidence)
    relpath = f"fixtures/evidence/{digest}.json"
    content = wrapper_bytes(market_id, int(market.get("round") or 0), market, evidence, digest)
    if not env.publish:
        raise PublishError("no evidence publisher configured")
    published = env.publish(relpath, content)
    _assert_permalink(published.uri, published.commit_sha)
    env.remember(market_id, digest, evidence)
    call = _send(env, "commit_evidence", {
        "market_id": market_id,
        "round": int(market.get("round") or 0),
        "evidence_bundle_hash": digest,
        "evidence_uri": published.uri,
    }, evidence_bundle_hash=digest, evidence_uri=published.uri, commit_sha=published.commit_sha, round=int(market.get("round") or 0))
    return [call], evidence


def _vote(env, market_id: int, market: dict, evidence: str) -> list[dict]:
    _check_bindings(market)
    digest = market.get("round_evidence_hash")
    if sha(evidence) != digest:
        raise RuntimeError("locked evidence does not match round_evidence_hash")
    rnd = int(market.get("round") or 0)
    voted = {v.get("model") for v in market.get("votes") or []}
    missing = [m for m in (market.get("resolver_models") or []) if m not in voted]
    calls = []
    failures = []
    for model in missing:
        try:
            vote = env.panel.sign(model, market_id, rnd, market.get("rules") or "", evidence, digest)
            if vote is None:
                raise PanelError("no cached vote for this request")
            vote = check_vote(vote, model=model, market_id=market_id, rnd=rnd, evidence=evidence, evidence_hash=digest)
            calls.append(_send(env, "submit_verdict", {
                "market_id": market_id,
                "request_raw": vote["request_raw"],
                "response_raw": vote["response_raw"],
                "signature": vote["signature"],
            }, model=model, outcome=vote.get("outcome"), round=rnd, evidence_bundle_hash=digest))
        except PanelError as exc:
            failures.append(f"{model}: {exc}")
            calls.append({"method": "submit_verdict", "sent": False, "tx_hash": None, "model": model, "error": str(exc)})
    if failures and not any(c.get("sent") for c in calls):
        err = PanelError("; ".join(failures))
        err.calls = calls
        raise err
    return calls


def _skeleton(market_id: int, market: dict | None, env, decision=None) -> dict:
    report = {
        "market_id": market_id,
        "dry_run": env.dry_run,
        "calls": [],
        "notes": [],
        "escalation_owner": (market or {}).get("escalation_owner"),
    }
    if market is None:
        report.update(level="skip", action="skip", detail="no such market", status=None, round=None)
        return report
    report["status"] = status_name(market.get("status"))
    report["round"] = market.get("round")
    if decision is not None:
        report["level"] = decision.level
        report["action"] = decision.action
        report["detail"] = decision.reason
    models = list(market.get("resolver_models") or [])
    if models and set(models) != set(PANEL_MODELS):
        report["model_note"] = "voting this market's resolver_models snapshot"
    return report


def _blocked_new_round(env, market_id: int, market: dict, now_ns: int) -> dict | None:
    """Refuse a fresh evidence commit while a TOO_EARLY wait is open, or once the round cap is hit."""
    rnd = int(market.get("round") or 0)
    if rnd >= env.round_cap:
        return {
            "level": "stop",
            "action": "stop",
            "detail": f"round {rnd} has reached the cap of {env.round_cap}; left for escalate_stale",
        }
    remaining = env.too_early.waiting(market_id, rnd, now_ns, env.too_early_retry_s)
    if remaining is None or market.get("round_evidence_hash"):
        return None
    seconds = max(1, remaining // 1_000_000_000)
    return {
        "level": "wait",
        "action": "wait",
        "detail": f"TOO_EARLY; next evidence commit waits {seconds}s",
    }


def run_market(env, market_id: int, now_ns: int) -> dict:
    market = env.get_market(market_id)
    if market is None:
        return _skeleton(market_id, None, env)
    decision = decide(market, now_ns)
    report = _skeleton(market_id, market, env, decision)
    if decision.action in ("skip", "stop"):
        return report
    if decision.action == "finalize" and env.dry_run:
        report["calls"] = [{"method": "finalize", "sent": False, "tx_hash": None, "market_id": market_id}]
        report["level"] = "ok"
        return report
    if decision.action == "finalize":
        report["calls"] = [_send(env, "finalize", {"market_id": market_id})]
        report["level"] = "ok"
        return report
    if decision.action == "commit_and_vote":
        blocked = _blocked_new_round(env, market_id, market, now_ns)
        if blocked:
            report["level"] = blocked["level"]
            report["action"] = blocked["action"]
            report["detail"] = blocked["detail"]
            return report
    if env.dry_run:
        report["calls"] = _dry_calls(market_id, market, decision)
        report["level"] = "ok"
        return report

    calls: list[dict] = []
    notes: list[str] = []
    level = "ok"
    detail = decision.reason
    status_out = report["status"]
    round_out = report["round"]
    for _ in range(env.max_rounds):
        market = env.get_market(market_id)
        decision = decide(market, now_ns)
        status_out = status_name(market.get("status"))
        round_out = market.get("round")
        if decision.action in ("skip", "stop"):
            level = decision.level
            detail = decision.reason
            break
        if decision.action == "finalize":
            calls.append(_send(env, "finalize", {"market_id": market_id}))
            level = "ok"
            detail = decision.reason
            break
        before_round = int(market.get("round") or 0)
        try:
            evidence = None
            if decision.action == "commit_and_vote":
                committed, evidence = _commit(env, market_id, market, now_ns)
                calls.extend(committed)
                market = env.get_market(market_id)
            if evidence is None:
                evidence = env.recall(market_id, market.get("round_evidence_hash"))
            if evidence is None:
                evidence = load_committed_evidence(env.fetch, market)
                env.remember(market_id, market.get("round_evidence_hash"), evidence)
            vote_calls = _vote(env, market_id, market, evidence)
            calls.extend(vote_calls)
        except NotReady as exc:
            level = "wait"
            detail = str(exc)
            break
        except (PublishError, PanelError, RuntimeError) as exc:
            calls.extend(getattr(exc, "calls", []))
            level = "error"
            detail = str(exc)
            break
        after = env.get_market(market_id)
        status_out = status_name(after.get("status"))
        round_out = after.get("round")
        advanced = status_out == "Open" and int(after.get("round") or 0) > before_round and not after.get("round_evidence_hash")
        if advanced:
            new_round = int(after.get("round") or 0)
            env.too_early.note(market_id, new_round, now_ns)
            notes.append("TOO_EARLY unanimous; next round")
            level = "info"
            detail = (
                "TOO_EARLY unanimous; the contract opened the next round. Not an error. "
                f"Next evidence commit waits {env.too_early_retry_s}s."
            )
            break
        if status_out == "Escalated":
            level = "stop"
            detail = f"Escalated after the panel; left for escalation_owner {after.get('escalation_owner')}. resolve_dispute is not called."
        elif status_out == "Proposed":
            level = "ok"
            detail = "panel agreed; Proposed until the dispute window closes"
        elif status_out == "Final":
            level = "ok"
            detail = "Final"
        else:
            level = "ok"
            detail = f"submitted votes; status is {status_out}"
        break
    report["calls"] = calls
    report["notes"] = notes
    report["level"] = level
    report["detail"] = detail
    report["status"] = status_out
    report["round"] = round_out
    if level == "wait":
        report["action"] = "wait"
    elif level == "error":
        report["action"] = "error"
    return report


def run_pass(env) -> list[dict]:
    now = env.now_ns()
    reports = []
    for market_id in env.market_ids():
        try:
            reports.append(run_market(env, int(market_id), now))
        except Exception as exc:
            reports.append({
                "market_id": int(market_id),
                "level": "error",
                "action": "error",
                "detail": str(exc),
                "dry_run": env.dry_run,
                "calls": [],
                "notes": [],
            })
    return reports


class TooEarlyWaits:
    """Per-market clock for the pause after unanimous TOO_EARLY. Optional JSON file under out/."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self._rows: dict[str, dict] = {}
        if self.path and self.path.is_file():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._rows = loaded

    def note(self, market_id: int, round_n: int, at_ns: int) -> None:
        self._rows[str(int(market_id))] = {"round": int(round_n), "at_ns": int(at_ns)}
        self._save()

    def waiting(self, market_id: int, round_n: int, now_ns: int, retry_s: int) -> int | None:
        row = self._rows.get(str(int(market_id)))
        if not row or int(row.get("round", -1)) != int(round_n):
            return None
        ready_at = int(row["at_ns"]) + int(retry_s) * 1_000_000_000
        if int(now_ns) >= ready_at:
            return None
        return ready_at - int(now_ns)

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._rows, sort_keys=True), encoding="utf-8")


class Memory:
    def __init__(self):
        self._items: dict[int, tuple[str, str]] = {}

    def remember(self, market_id: int, digest: str, evidence: str) -> None:
        self._items[int(market_id)] = (digest, evidence)

    def recall(self, market_id: int, digest: str) -> str | None:
        pair = self._items.get(int(market_id))
        if pair and pair[0] == digest:
            return pair[1]
        return None


class RelayEnv(Memory):
    def __init__(self, *, dry_run: bool, now_ns, get_market, market_ids, send, fetch, publish, panel, max_rounds: int = 1, round_cap: int = 5, too_early_retry_s: int = 6 * 60 * 60, too_early: TooEarlyWaits | None = None):
        super().__init__()
        self.dry_run = dry_run
        self._now_ns = now_ns
        self._get_market = get_market
        self._market_ids = market_ids
        self.send = send
        self.fetch = fetch
        self.publish = publish
        self.panel = panel
        self.max_rounds = max_rounds
        self.round_cap = round_cap
        self.too_early_retry_s = too_early_retry_s
        self.too_early = too_early if too_early is not None else TooEarlyWaits()

    def now_ns(self) -> int:
        value = self._now_ns() if callable(self._now_ns) else self._now_ns
        return int(value)

    def get_market(self, market_id: int):
        return self._get_market(int(market_id))

    def market_ids(self) -> list[int]:
        return [int(i) for i in self._market_ids()]
