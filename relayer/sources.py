"""Fetch market sources outside the TEE and build the evidence bundle.

The bundle bytes come from ``scripts/verdict_request.evidence_bundle``. Sports
scoreboards are reduced to the same extracted objects ``scripts/bench_scale.py``
stores (one game, not the whole feed). A sports market is not committed until
two snapshots from different hosts succeeded. Nothing is invented to fill a gap.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlparse

from verdict_request import evidence_bundle, sha

# Same team-token rules as scripts/bench_scale.py (kept local so importing the
# benchmark module does not create out/bench2 or require its HTTP stack).
_STOP = {
    "fc", "sv", "vfl", "vfb", "sc", "tsg", "sg", "fsv", "1", "1.", "bsc", "cf", "ac", "afc",
    "ssv", "tsv", "spvgg", "the", "de", "e.v.", "ev", "05", "04", "09", "1899", "1910", "98",
    "96", "07", "06",
}
ESPN2NFLV = {"WSH": "WAS", "LAR": "LA"}
NFLVERSE_CSV = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
STAT_KINDS = ("espn", "nflverse", "nhl", "openliga")
TEXT_CAP = 12_000


def toks(value: str) -> set[str]:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", value) if t and t not in _STOP}


def same_team(a: str, b: str) -> bool:
    ta, tb = toks(a), toks(b)
    return bool(ta & tb) or (a.lower() in b.lower()) or (b.lower() in a.lower())


def match_pair(teams: list[str], names: list[str]) -> bool:
    return all(any(same_team(t, n) for n in names) for t in teams)


def teams_from_question(question: str) -> list[str]:
    if " vs. " in question:
        left, right = question.split(" vs. ", 1)
    elif " vs " in question:
        left, right = question.split(" vs ", 1)
    else:
        return []
    right = re.split(r"[:?]", right, maxsplit=1)[0]
    return [left.strip(), right.strip()]


def iso_from_ns(ns: int) -> str:
    sec = int(ns) // 1_000_000_000
    return datetime.datetime.fromtimestamp(sec, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_from_ns(ns: int) -> datetime.date:
    sec = int(ns) // 1_000_000_000
    return datetime.datetime.fromtimestamp(sec, datetime.timezone.utc).date()


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_sports_market(market: dict) -> bool:
    question = market.get("question") or ""
    if " vs. " in question or " vs " in question or question.startswith("Spread:") or "O/U" in question:
        return True
    for url in market.get("sources") or []:
        u = url.lower()
        if any(s in u for s in ("site.api.espn.com", "nflverse/nfldata", "api-web.nhle.com", "api.openligadb.de")):
            return True
    return False


def resolve_fetch_url(source_url: str) -> str:
    """Map the nflverse repo URL stored on-chain to the games.csv the benchmark reads.

    The snapshot records the URL that was actually fetched. The repo HTML is not a stat line.
    """
    bare = source_url.rstrip("/")
    if bare in ("https://github.com/nflverse/nfldata", "http://github.com/nflverse/nfldata"):
        return NFLVERSE_CSV
    return source_url


def _dated_urls(url: str) -> list[str]:
    match = re.search(r"dates=(\d{8})", url)
    if match:
        day = datetime.datetime.strptime(match.group(1), "%Y%m%d").date()
        out = []
        for delta in (0, -1, 1):
            d = day + datetime.timedelta(days=delta)
            out.append(re.sub(r"dates=\d{8}", f"dates={d:%Y%m%d}", url, count=1))
        return out
    match = re.search(r"/score/(\d{4}-\d{2}-\d{2})", url)
    if match:
        day = datetime.date.fromisoformat(match.group(1))
        out = []
        for delta in (0, -1, 1):
            d = (day + datetime.timedelta(days=delta)).isoformat()
            out.append(url[: match.start(1)] + d + url[match.end(1) :])
        return out
    return [url]


def _day_from_url(url: str) -> datetime.date | None:
    match = re.search(r"dates=(\d{8})", url)
    if match:
        return datetime.datetime.strptime(match.group(1), "%Y%m%d").date()
    match = re.search(r"/score/(\d{4}-\d{2}-\d{2})", url)
    if match:
        return datetime.date.fromisoformat(match.group(1))
    return None


@dataclass
class Snap:
    url: str
    fetched_at: str
    sha256: str
    text: str
    kind: str
    host: str
    espn_slim: dict | None = None
    day: datetime.date | None = None


@dataclass
class GatherResult:
    snaps: list[Snap] = field(default_factory=list)
    ready: bool = False
    reason: str = ""

    def bundle_snaps(self) -> list[dict]:
        return [
            {"url": s.url, "fetched_at": s.fetched_at, "sha256": s.sha256, "text": s.text}
            for s in self.snaps
        ]


def _snap(url: str, fetched_at: str, text: str, kind: str, espn_slim=None, day=None) -> Snap:
    digest = sha(text)
    return Snap(url=url, fetched_at=fetched_at, sha256=digest, text=text, kind=kind, host=host_of(url), espn_slim=espn_slim, day=day)


def extract_espn(body: bytes, question: str, page_url: str, fetched_at: str) -> Snap | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    teams = teams_from_question(question)
    if len(teams) != 2:
        return None
    for event in data.get("events") or []:
        comps = event.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        names = []
        for side in comp.get("competitors") or []:
            team = side.get("team") or {}
            for key in ("displayName", "shortDisplayName", "name", "abbreviation"):
                if team.get(key):
                    names.append(team[key])
        if not match_pair(teams, names):
            continue
        status = ((comp.get("status") or {}).get("type") or {})
        slim = {
            "name": event.get("name"),
            "date": event.get("date"),
            "status": status.get("description"),
            "completed": status.get("completed"),
            "competitors": [
                {
                    "team": (x.get("team") or {}).get("displayName"),
                    "abbreviation": (x.get("team") or {}).get("abbreviation"),
                    "homeAway": x.get("homeAway"),
                    "score": x.get("score"),
                    "winner": x.get("winner"),
                }
                for x in comp.get("competitors") or []
            ],
        }
        text = "ESPN scoreboard (extracted game object): " + json.dumps(slim, ensure_ascii=False)
        day = None
        if isinstance(slim.get("date"), str) and len(slim["date"]) >= 10:
            try:
                day = datetime.date.fromisoformat(slim["date"][:10])
            except ValueError:
                day = None
        return _snap(page_url, fetched_at, text, "espn", slim, day)
    return None


def extract_nflverse(body: bytes, fetched_at: str, espn_slim: dict | None, day: datetime.date | None) -> Snap | None:
    if not espn_slim or day is None:
        return None
    try:
        text_csv = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    abbr = {ESPN2NFLV.get(x.get("abbreviation"), x.get("abbreviation")) for x in espn_slim.get("competitors") or []}
    abbr.discard(None)
    if len(abbr) != 2:
        return None
    for row in csv.DictReader(io.StringIO(text_csv)):
        try:
            gday = datetime.date.fromisoformat(row["gameday"])
        except (KeyError, ValueError):
            continue
        if {row.get("away_team"), row.get("home_team")} != abbr:
            continue
        if abs((gday - day).days) > 1:
            continue
        slim = {k: row.get(k) for k in ("game_id", "gameday", "away_team", "away_score", "home_team", "home_score", "result", "total", "overtime")}
        text = "nflverse/nfldata games.csv row (independent community dataset; empty score = not yet final): " + json.dumps(slim)
        return _snap(NFLVERSE_CSV, fetched_at, text, "nflverse", day=gday)
    return None


def extract_nhl(body: bytes, question: str, page_url: str, fetched_at: str) -> Snap | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    teams = teams_from_question(question)
    if len(teams) != 2:
        return None
    for game in data.get("games") or []:
        names = []
        for side in ("awayTeam", "homeTeam"):
            club = game.get(side) or {}
            names.append(((club.get("name") or {}).get("default")) or "")
            if club.get("abbrev"):
                names.append(club["abbrev"])
        if not match_pair(teams, [n for n in names if n]):
            continue
        slim = {
            "gameDate": game.get("gameDate"),
            "gameState": game.get("gameState"),
            "gameOutcome": game.get("gameOutcome"),
            "awayTeam": {"name": ((game.get("awayTeam") or {}).get("name") or {}).get("default"), "score": (game.get("awayTeam") or {}).get("score")},
            "homeTeam": {"name": ((game.get("homeTeam") or {}).get("name") or {}).get("default"), "score": (game.get("homeTeam") or {}).get("score")},
        }
        text = "NHL official API score feed (extracted game object; gameState OFF/FINAL = final): " + json.dumps(slim)
        day = None
        if isinstance(slim.get("gameDate"), str):
            try:
                day = datetime.date.fromisoformat(slim["gameDate"][:10])
            except ValueError:
                day = None
        return _snap(page_url, fetched_at, text, "nhl", day=day)
    return None


def extract_openliga(body: bytes, question: str, page_url: str, fetched_at: str, day: datetime.date | None) -> Snap | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    teams = teams_from_question(question)
    if len(teams) != 2 or day is None:
        return None
    for game in data:
        try:
            gday = datetime.date.fromisoformat(str(game.get("matchDateTime", ""))[:10])
        except ValueError:
            continue
        names = [
            (game.get("team1") or {}).get("teamName", ""),
            (game.get("team2") or {}).get("teamName", ""),
            (game.get("team1") or {}).get("shortName", ""),
            (game.get("team2") or {}).get("shortName", ""),
        ]
        if abs((gday - day).days) > 1 or not match_pair(teams, [n for n in names if n]):
            continue
        slim = {
            "matchDateTime": game.get("matchDateTime"),
            "team1": (game.get("team1") or {}).get("teamName"),
            "team2": (game.get("team2") or {}).get("teamName"),
            "matchIsFinished": game.get("matchIsFinished"),
            "matchResults": [
                {"name": r.get("resultName"), "team1": r.get("pointsTeam1"), "team2": r.get("pointsTeam2")}
                for r in game.get("matchResults") or []
            ],
        }
        text = "OpenLigaDB (independent community-maintained German football results; extracted match): " + json.dumps(slim, ensure_ascii=False)
        return _snap(page_url, fetched_at, text, "openliga", day=gday)
    return None


def extract_generic(body: bytes, page_url: str, fetched_at: str) -> Snap | None:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > TEXT_CAP:
        text = text[:TEXT_CAP] + "\n[truncated by relayer at 12000 chars]"
    return _snap(page_url, fetched_at, text, "generic")


def _source_rank(url: str) -> int:
    u = url.lower()
    if "espn.com" in u:
        return 0
    if "nhle.com" in u:
        return 1
    if "openligadb" in u:
        return 2
    if "nflverse" in u:
        return 3
    return 4


def gather(market: dict, fetch, fetched_at: str) -> GatherResult:
    """``fetch(url) -> (status_code, body_bytes)``. Failures are omitted, not filled in."""
    espn_slim = None
    day = _day_from_url(next((u for u in market.get("sources") or [] if "dates=" in u), "")) 
    if day is None and market.get("deadline_ns") is not None:
        try:
            day = date_from_ns(market["deadline_ns"])
        except (TypeError, ValueError):
            day = None
    snaps: list[Snap] = []
    for source in sorted(market.get("sources") or [], key=_source_rank):
        fetch_url = resolve_fetch_url(source)
        kind_hint = _source_rank(source)
        urls = _dated_urls(fetch_url) if kind_hint in (0, 1) else [fetch_url]
        got = None
        for url in urls:
            try:
                status, body = fetch(url)
            except Exception:
                continue
            if status != 200 or not body:
                continue
            if "site.api.espn.com" in url:
                got = extract_espn(body, market.get("question") or "", url, fetched_at)
            elif "nflverse/nfldata" in url and url.endswith("games.csv"):
                got = extract_nflverse(body, fetched_at, espn_slim, day)
            elif "api-web.nhle.com" in url:
                got = extract_nhl(body, market.get("question") or "", url, fetched_at)
            elif "api.openligadb.de" in url:
                got = extract_openliga(body, market.get("question") or "", url, fetched_at, day)
            else:
                got = extract_generic(body, url, fetched_at)
            if got:
                break
        if not got:
            continue
        if any(existing.url == got.url and existing.sha256 == got.sha256 for existing in snaps):
            continue
        if got.kind == "espn" and got.espn_slim:
            espn_slim = got.espn_slim
            if got.day:
                day = got.day
        snaps.append(got)
    ready, reason = enough(market, snaps)
    return GatherResult(snaps=snaps, ready=ready, reason=reason)


def enough(market: dict, snaps: list[Snap]) -> tuple[bool, str]:
    if not snaps:
        return False, "no source snapshots yet; not committing"
    if is_sports_market(market):
        stats = [s for s in snaps if s.kind in STAT_KINDS]
        use = stats if stats else snaps
        hosts = {s.host for s in use}
        if len(use) >= 2 and len(hosts) >= 2:
            return True, "two independent sources"
        return False, "sports market needs 2 independent sources; not committing yet"
    return True, "source snapshot ready"


def build_bundle(market: dict, snaps: list[dict]) -> str:
    end = iso_from_ns(as_deadline(market))
    return evidence_bundle(market.get("question") or "", market.get("rules") or "", end, snaps)


def as_deadline(market: dict) -> int:
    from relayer.decide import as_u64
    return as_u64(market.get("deadline_ns"), "deadline_ns")


def wrapper_bytes(market_id: int, rnd: int, market: dict, evidence: str, evidence_hash: str) -> bytes:
    """Content-addressed file. The on-chain hash is sha256 of ``evidence``, not of this wrapper."""
    payload = {
        "market_id": market_id,
        "round": rnd,
        "question": market.get("question"),
        "rules": market.get("rules"),
        "market_end": iso_from_ns(as_deadline(market)),
        "evidence": evidence,
        "evidence_bundle_hash": evidence_hash,
        "note": "Fetched outside the TEE by the Verdict relayer. Integrity is sha256 of the evidence string.",
    }
    return json.dumps(payload, indent=1, ensure_ascii=False).encode("utf-8")
