"""Relayer decisions, evidence permalinks, and a dry-run that does not invent signatures or SHAs."""
from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from verdict_request import PROMPT_TEMPLATE_HASH, build_request, evidence_bundle, parse_outcome, sha

from relayer import PANEL_MODELS, ROOT
from relayer.chain import block_time_ns, decode_view_payload, dump_args, explorer_url, near_argv, parse_tx_id
from relayer.config import ConfigError, load_config
from relayer.decide import decide
from relayer.panel import PanelClient, PanelError, assert_allowlisted
from relayer.publish import GitPublisher, PublishError
from relayer.service import RelayEnv, load_markets_file, run_pass
from relayer.sources import gather, resolve_fetch_url

RULES = "Resolve YES if Saints win and NO if Giants win."
NOW = 10_000
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=20261018"
NFL_REPO = "https://github.com/nflverse/nfldata"
ESPN_BODY = json.dumps({
    "events": [{
        "name": "New Orleans Saints at New York Giants",
        "date": "2026-10-18T17:00Z",
        "competitions": [{
            "status": {"type": {"description": "Final", "completed": True}},
            "competitors": [
                {"team": {"displayName": "New York Giants", "abbreviation": "NYG"}, "homeAway": "home", "score": "27", "winner": False},
                {"team": {"displayName": "New Orleans Saints", "abbreviation": "NO"}, "homeAway": "away", "score": "34", "winner": True},
            ],
        }],
    }]
}).encode()
CSV_BODY = (
    "game_id,gameday,away_team,away_score,home_team,home_score,result,total,overtime\n"
    "2026_06_NO_NYG,2026-10-18,NO,34,NYG,27,-7,61,0\n"
).encode()


def base_market(**kw):
    market = {
        "question": "Saints vs. Giants",
        "rules": RULES,
        "rules_hash": sha(RULES),
        "prompt_template_hash": PROMPT_TEMPLATE_HASH,
        "sources": [ESPN, NFL_REPO],
        "resolver_models": list(PANEL_MODELS),
        "threshold": 3,
        "deadline_ns": 5_000,
        "dispute_window_ns": 1_000,
        "escalation_owner": "app.testnet",
        "round": 0,
        "round_evidence_hash": None,
        "round_evidence_uri": None,
        "votes": [],
        "status": "Open",
    }
    market.update(kw)
    return market


def response_for(outcome: str) -> str:
    return json.dumps({
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps({"outcome": outcome})},
        }]
    })


class ScriptedPanel:
    def __init__(self, outcomes):
        self.outcomes = outcomes

    def sign(self, model, market_id, rnd, rules, evidence, evidence_hash):
        spec = self.outcomes[rnd]
        outcome = spec[model] if isinstance(spec, dict) else spec
        request_raw = build_request(model, market_id, rnd, rules, evidence).decode("utf-8")
        response_raw = response_for(outcome)
        text = f"{model}:{sha(request_raw)}:{sha(response_raw)}"
        return {
            "model": model,
            "request_raw": request_raw,
            "response_raw": response_raw,
            "signature": "ab" * 65,
            "text": text,
            "outcome": outcome,
        }


class ApplyingChain:
    """Minimal stand-in for the contract transitions the relayer has to notice."""

    def __init__(self, markets, now=NOW):
        self.markets = markets
        self.now = now
        self.sent = []

    def get(self, market_id):
        market = self.markets.get(market_id)
        return copy.deepcopy(market) if market is not None else None

    def send(self, method, args):
        if method == "resolve_dispute":
            raise AssertionError("relayer must not call resolve_dispute")
        self.sent.append((method, copy.deepcopy(args)))
        self.apply(method, args)
        return {"sent": True, "tx_hash": None, "explorer_url": None}

    def apply(self, method, args):
        market = self.markets[args["market_id"]]
        if method == "commit_evidence":
            market["round_evidence_hash"] = args["evidence_bundle_hash"]
            market["round_evidence_uri"] = args["evidence_uri"]
            return
        if method == "finalize":
            proposed = market["status"]["Proposed"]["outcome"]
            market["status"] = {"Final": {"outcome": proposed}}
            return
        if method != "submit_verdict":
            raise AssertionError(method)
        outcome = parse_outcome(args["response_raw"])
        model = json.loads(args["request_raw"])["model"]
        market["votes"].append({"model": model, "outcome": outcome})
        if len(market["votes"]) < market["threshold"]:
            return
        outs = [v["outcome"] for v in market["votes"]]
        if len(set(outs)) == 1 and outs[0] == "TOO_EARLY":
            market["votes"] = []
            market["round"] = int(market["round"]) + 1
            market["round_evidence_hash"] = None
            market["round_evidence_uri"] = None
            market["status"] = "Open"
            return
        if len(set(outs)) == 1 and outs[0] in ("YES", "NO"):
            market["status"] = {"Proposed": {"outcome": "Yes" if outs[0] == "YES" else "No", "at_ns": self.now}}
            return
        market["status"] = "Escalated"


def sports_fetch(url: str):
    if url == ESPN:
        return 200, ESPN_BODY
    if "dates=20261017" in url or "dates=20261019" in url:
        return 404, b""
    if url == resolve_fetch_url(NFL_REPO):
        return 200, CSV_BODY
    return 404, b""


def git(repo: Path, *args, input_bytes: bytes | None = None):
    env = {
        "GIT_AUTHOR_NAME": "verdict-relayer",
        "GIT_AUTHOR_EMAIL": "verdict-relayer@localhost",
        "GIT_COMMITTER_NAME": "verdict-relayer",
        "GIT_COMMITTER_EMAIL": "verdict-relayer@localhost",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    proc = subprocess.run(["git", *args], cwd=repo, input=input_bytes, capture_output=True, env=env, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.decode())
    return proc


def init_repo():
    work = Path(tempfile.mkdtemp(prefix="verdict-evidence-"))
    bare = Path(tempfile.mkdtemp(prefix="verdict-evidence-bare-"))
    git(bare, "init", "--bare", "-b", "master")
    git(work, "init", "-b", "master")
    (work / "README").write_text("evidence repo\n", encoding="utf-8")
    git(work, "add", "README")
    git(work, "commit", "-m", "init")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-u", "origin", "master")
    return work


def env_for(chain: ApplyingChain, publish, panel, fetch, dry_run=False) -> RelayEnv:
    return RelayEnv(
        dry_run=dry_run,
        now_ns=lambda: chain.now,
        get_market=chain.get,
        market_ids=lambda: sorted(chain.markets),
        send=chain.send,
        fetch=fetch,
        publish=None if publish is None else publish.publish,
        panel=panel,
        max_rounds=2,
    )


class DecideTests(unittest.TestCase):
    def test_boundaries_and_terminal_statuses(self):
        self.assertEqual(decide(base_market(deadline_ns=NOW + 1), NOW).action, "skip")
        self.assertEqual(decide(base_market(deadline_ns=NOW + 1), NOW).reason, "before deadline")
        self.assertEqual(decide(base_market(deadline_ns=NOW), NOW).action, "commit_and_vote")
        proposed_open = base_market(status={"Proposed": {"outcome": "Yes", "at_ns": NOW - 10}}, dispute_window_ns=100)
        self.assertEqual(decide(proposed_open, NOW).action, "skip")
        proposed_closed = base_market(status={"Proposed": {"outcome": "Yes", "at_ns": 1_000}}, dispute_window_ns=1_000)
        self.assertEqual(decide(proposed_closed, 2_000).action, "finalize")
        self.assertEqual(decide(base_market(status={"Final": {"outcome": "Yes"}}), NOW).action, "skip")
        disputed = decide(base_market(status={"Disputed": {"proposed": "Yes", "disputer": "alice.testnet", "bond": "1"}}), NOW)
        escalated = decide(base_market(status="Escalated"), NOW)
        self.assertEqual(disputed.action, "stop")
        self.assertEqual(escalated.action, "stop")
        self.assertIn("app.testnet", disputed.reason)
        self.assertIn("resolve_dispute", disputed.reason)
        self.assertIn("resolve_dispute", escalated.reason)
        partial = base_market(round_evidence_hash="ab" * 32, votes=[{"model": PANEL_MODELS[0], "outcome": "Yes"}])
        vote = decide(partial, NOW)
        self.assertEqual(vote.action, "vote")
        self.assertEqual(vote.missing_models, list(PANEL_MODELS[1:]))

    def test_view_parsing_does_not_drop_nanoseconds(self):
        raw = b'{"status": {"Final": {"outcome": "Yes"}}}'
        self.assertEqual(decode_view_payload(list(raw))["status"]["Final"]["outcome"], "Yes")
        self.assertIsNone(decode_view_payload(list(b"null")))
        self.assertEqual(block_time_ns({"timestamp": 1, "timestamp_nanosec": "1793961558056375475"}), 1793961558056375475)

    def test_cli_args_keep_secrets_off_the_command_line(self):
        argv = near_argv(
            "near",
            contract="verdict-v2-baheet.testnet",
            method="finalize",
            args_file="finalize.json",
            signer="relayer.testnet",
            network="testnet",
            credentials_file="access-key.json",
            gas="30.0 Tgas",
        )
        blob = " ".join(argv)
        self.assertIn("file-args", argv)
        self.assertIn("sign-with-access-key-file", argv)
        self.assertNotIn("sign-with-plaintext-private-key", argv)
        self.assertNotIn("json-args", argv)
        self.assertNotIn("ed25519:", blob)
        self.assertFalse(dump_args({"market_id": 0}).startswith(b"\xef\xbb\xbf"))
        self.assertIsNone(parse_tx_id("https://explorer.testnet.near.org/transactions/C4ZQabkbLzSVzDCJFsxUEWEn18gVLmi4h2ANKGBXUsXx"))
        self.assertEqual(parse_tx_id("Transaction ID: Abcdefghijkmnopqrstuvwxyz123456789\n"), "Abcdefghijkmnopqrstuvwxyz123456789")
        self.assertIsNone(explorer_url("testnet", None))

    def test_config_file_rejects_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "relayer.local.json"
            path.write_text('{"private_key": "ed25519:nope", "mode": "dry-run"}', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(str(path))
            path.write_bytes(b"\xef\xbb\xbf" + b'{"mode": "dry-run", "contract": "verdict-v2-baheet.testnet"}')
            cfg = load_config(str(path))
            self.assertEqual(cfg.mode, "dry-run")
            self.assertEqual(cfg.contract, "verdict-v2-baheet.testnet")


class EvidenceTests(unittest.TestCase):
    def test_two_sports_sources_and_a_single_source_waits(self):
        ready = gather(base_market(), sports_fetch, "2026-11-06T10:00:00Z")
        self.assertTrue(ready.ready, ready.reason)
        self.assertEqual({s.kind for s in ready.snaps}, {"espn", "nflverse"})
        self.assertEqual(len({s.host for s in ready.snaps}), 2)
        self.assertEqual(len(ready.bundle_snaps()), 2)
        self.assertTrue(all("34" in snap["text"] for snap in ready.bundle_snaps()))
        thin = gather(base_market(sources=[ESPN]), sports_fetch, "2026-11-06T10:00:00Z")
        self.assertFalse(thin.ready)
        self.assertIn("2 independent", thin.reason)

    def test_permalink_is_the_commit_git_returns(self):
        work = init_repo()
        publisher = GitPublisher(work, remote="origin", github_repo="Baheet18/verdict", push=True, verify_http=False)
        content = b'{"evidence": "locked", "evidence_bundle_hash": "abc"}\n'
        relpath = "fixtures/evidence/" + ("ab" * 32) + ".json"
        published = publisher.publish(relpath, content)
        ref = f"refs/heads/verdict-evidence/{published.commit_sha}"
        sha_ref = git(work, "rev-parse", ref).stdout.decode().strip()
        self.assertEqual(published.commit_sha, sha_ref)
        self.assertEqual(git(work, "cat-file", "blob", f"{sha_ref}:{relpath}").stdout, content)
        self.assertIn(sha_ref, published.uri)
        self.assertNotIn("/master/", published.uri)
        self.assertNotIn("/main/", published.uri)
        self.assertTrue(published.uri.startswith("https://raw.githubusercontent.com/Baheet18/verdict/"))
        before = git(work, "rev-list", "--all", "--count").stdout.decode().strip()
        again = publisher.publish(relpath, content)
        after = git(work, "rev-list", "--all", "--count").stdout.decode().strip()
        self.assertEqual(again.commit_sha, published.commit_sha)
        self.assertEqual(before, after)
        with self.assertRaises(PublishError):
            GitPublisher(work, push=False, verify_http=False).publish(
                "fixtures/evidence/" + ("cd" * 32) + ".json",
                b'{"evidence": "not pushed"}\n',
            )

    def test_allowlist_and_cached_real_panel_only_for_the_same_bytes(self):
        body = build_request(PANEL_MODELS[0], 0, 0, RULES, "{}")
        parsed = json.loads(body)
        self.assertEqual(set(parsed), {"model", "messages", "temperature", "seed"})
        self.assertEqual(parsed["temperature"], 0)
        self.assertEqual(parsed["seed"], 1114)
        self.assertTrue(parsed["messages"][1]["content"].startswith("VERDICT/1 "))
        bad = json.dumps({**parsed, "logit_bias": {"1": 100}})
        with self.assertRaises(PanelError):
            assert_allowlisted(bad, market_id=0, rnd=0, evidence="{}", evidence_hash=sha("{}"))
        panel = PanelClient(live=False, fixture_paths=[ROOT / "contract" / "testdata" / "real_panel.json"])
        stored = json.loads((ROOT / "contract" / "testdata" / "real_panel.json").read_text())
        vote = stored["votes"][0]
        evidence = json.loads(vote["request_raw"])["messages"][1]["content"].split("\n", 1)[1]
        got = panel.sign(vote["model"], 0, 0, stored["rules"], evidence, sha(evidence))
        self.assertIsNotNone(got)
        self.assertEqual(got["signature"], vote["signature"])
        self.assertEqual(got["text"], vote["text"])
        self.assertIsNone(panel.sign(vote["model"], 0, 0, stored["rules"], evidence + "x", sha(evidence + "x")))

    def test_live_panel_posts_the_exact_request_bytes(self):
        seen = {}

        def post(url, data, headers):
            self.assertTrue(url.endswith("/chat/completions"))
            self.assertEqual(headers["Authorization"], "Bearer test-key")
            self.assertNotIn(b"logit_bias", data)
            seen["data"] = data
            raw = json.dumps({
                "id": "completion-1",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({"outcome": "YES"})}}],
            }).encode()
            seen["raw"] = raw
            return 200, raw

        def get(url, headers):
            self.assertIn("signing_algo=ecdsa", url)
            self.assertIn("model=Qwen", url)
            text = f"{PANEL_MODELS[0]}:{sha(seen['data'])}:{sha(seen['raw'])}"
            return 200, json.dumps({"signature": "cd" * 65, "text": text}).encode()

        panel = PanelClient(live=True, api_key="test-key", http_post=post, http_get=get)
        evidence = "{}"
        got = panel.sign(PANEL_MODELS[0], 0, 0, RULES, evidence, sha(evidence))
        self.assertEqual(seen["data"], build_request(PANEL_MODELS[0], 0, 0, RULES, evidence))
        self.assertEqual(got["outcome"], "YES")
        self.assertEqual(got["signature"], "cd" * 65)


class PassTests(unittest.TestCase):
    def test_many_markets_act_independently(self):
        work = init_repo()
        publisher = GitPublisher(work, remote="origin", push=True, verify_http=False)
        chain = ApplyingChain({
            0: base_market(deadline_ns=NOW + 5),
            1: base_market(),
            2: base_market(status={"Proposed": {"outcome": "Yes", "at_ns": NOW - 10}}, dispute_window_ns=100),
            3: base_market(status={"Proposed": {"outcome": "No", "at_ns": 1_000}}, dispute_window_ns=1_000),
            4: base_market(status={"Final": {"outcome": "Yes"}}),
            5: base_market(status={"Disputed": {"proposed": "Yes", "disputer": "alice.testnet", "bond": "1"}}),
            6: base_market(status="Escalated"),
        })
        reports = run_pass(env_for(chain, publisher, ScriptedPanel({0: "YES"}), sports_fetch))
        by_id = {r["market_id"]: r for r in reports}
        self.assertEqual(by_id[0]["action"], "skip")
        self.assertEqual(by_id[0]["detail"], "before deadline")
        self.assertEqual([c["method"] for c in by_id[1]["calls"]], ["commit_evidence", "submit_verdict", "submit_verdict", "submit_verdict"])
        commit = by_id[1]["calls"][0]
        self.assertNotIn("/master/", commit["evidence_uri"])
        self.assertNotIn("/main/", commit["evidence_uri"])
        self.assertEqual(commit["commit_sha"], git(work, "rev-parse", commit["commit_sha"]).stdout.decode().strip())
        blob = git(work, "cat-file", "blob", f"{commit['commit_sha']}:{ 'fixtures/evidence/' + commit['evidence_bundle_hash'] + '.json' }").stdout
        self.assertEqual(sha(json.loads(blob)["evidence"]), commit["evidence_bundle_hash"])
        for call in by_id[1]["calls"][1:]:
            args = next(args for method, args in chain.sent if method == "submit_verdict" and json.loads(args["request_raw"])["model"] == call["model"])
            body = json.loads(args["request_raw"])
            self.assertEqual(set(body), {"model", "messages", "temperature", "seed"})
            self.assertEqual(body["temperature"], 0)
            self.assertEqual(body["seed"], 1114)
            self.assertTrue(body["messages"][1]["content"].startswith("VERDICT/1 "))
            self.assertIn(commit["evidence_bundle_hash"], body["messages"][1]["content"])
        self.assertEqual(chain.get(1)["status"]["Proposed"]["outcome"], "Yes")
        self.assertEqual(by_id[2]["action"], "skip")
        self.assertEqual([c["method"] for c in by_id[3]["calls"]], ["finalize"])
        self.assertEqual(chain.get(3)["status"], {"Final": {"outcome": "No"}})
        self.assertEqual(by_id[4]["action"], "skip")
        self.assertEqual(by_id[4]["detail"], "already Final")
        self.assertEqual(by_id[5]["calls"], [])
        self.assertEqual(by_id[6]["calls"], [])
        self.assertIn("app.testnet", by_id[5]["detail"])
        self.assertIn("app.testnet", by_id[6]["detail"])
        self.assertNotIn("resolve_dispute", [method for method, _ in chain.sent])

    def test_too_early_is_not_an_error_and_split_stops(self):
        work = init_repo()
        publisher = GitPublisher(work, remote="origin", push=True, verify_http=False)
        early = ApplyingChain({0: base_market()})
        early_reports = run_pass(env_for(early, publisher, ScriptedPanel({0: "TOO_EARLY", 1: "YES"}), sports_fetch))
        self.assertEqual(early_reports[0]["level"], "ok")
        self.assertNotEqual(early_reports[0]["level"], "error")
        self.assertTrue(any("TOO_EARLY" in note for note in early_reports[0]["notes"]))
        self.assertEqual(early.get(0)["status"]["Proposed"]["outcome"], "Yes")
        self.assertEqual(early.get(0)["round"], 1)
        self.assertGreaterEqual(sum(1 for method, _ in early.sent if method == "commit_evidence"), 2)

        split = ApplyingChain({0: base_market()})
        panel = ScriptedPanel({0: {
            PANEL_MODELS[0]: "YES",
            PANEL_MODELS[1]: "NO",
            PANEL_MODELS[2]: "AMBIGUOUS",
        }})
        split_reports = run_pass(env_for(split, publisher, panel, sports_fetch))
        self.assertEqual(split_reports[0]["level"], "stop")
        self.assertEqual(split.get(0)["status"], "Escalated")
        self.assertNotIn("resolve_dispute", [method for method, _ in split.sent])

        ambiguous = ApplyingChain({0: base_market()})
        amb_reports = run_pass(env_for(ambiguous, publisher, ScriptedPanel({0: "AMBIGUOUS"}), sports_fetch))
        self.assertEqual(amb_reports[0]["level"], "stop")
        self.assertEqual(ambiguous.get(0)["status"], "Escalated")
        self.assertNotIn("resolve_dispute", [method for method, _ in ambiguous.sent])

    def test_a_failed_market_does_not_block_the_next(self):
        class Boom:
            def publish(self, *_args, **_kwargs):
                raise PublishError("remote rejected the push")

        chain = ApplyingChain({
            0: base_market(),
            1: base_market(status={"Proposed": {"outcome": "Yes", "at_ns": 1}}, dispute_window_ns=1),
        })
        reports = run_pass(env_for(chain, Boom(), ScriptedPanel({0: "YES"}), sports_fetch))
        self.assertEqual(reports[0]["level"], "error")
        self.assertIn("remote rejected", reports[0]["detail"])
        self.assertEqual(reports[1]["calls"][0]["method"], "finalize")
        self.assertEqual(chain.get(1)["status"], {"Final": {"outcome": "Yes"}})

    def test_thin_sports_evidence_does_not_commit_and_a_bad_prompt_does_not_vote(self):
        work = init_repo()
        publisher = GitPublisher(work, remote="origin", push=True, verify_http=False)
        chain = ApplyingChain({0: base_market(sources=[ESPN])})
        report = run_pass(env_for(chain, publisher, ScriptedPanel({0: "YES"}), sports_fetch))[0]
        self.assertEqual(report["level"], "wait")
        self.assertEqual(chain.sent, [])

        broken = ApplyingChain({0: base_market(prompt_template_hash="11" * 32)})
        broken_report = run_pass(env_for(broken, publisher, ScriptedPanel({0: "YES"}), sports_fetch))[0]
        self.assertEqual(broken_report["level"], "error")
        self.assertEqual(broken.sent, [])

    def test_existing_evidence_is_reused_and_final_or_proposed_markets_are_left_alone(self):
        evidence = evidence_bundle("Saints vs. Giants", RULES, "2026-11-06T00:00:05Z", [{
            "url": "https://example.com/a",
            "fetched_at": "2026-11-06T00:00:00Z",
            "sha256": sha("alpha"),
            "text": "alpha",
        }, {
            "url": "https://example.net/b",
            "fetched_at": "2026-11-06T00:00:00Z",
            "sha256": sha("beta"),
            "text": "beta",
        }])
        digest = sha(evidence)
        wrapper = json.dumps({"evidence": evidence, "evidence_bundle_hash": digest}).encode()

        def fetch(url):
            if url == "https://example.test/evidence.json":
                return 200, wrapper
            return 404, b""

        chain = ApplyingChain({
            7: base_market(
                question="Saints vs. Giants",
                sources=["https://example.com/a", "https://example.net/b"],
                round_evidence_hash=digest,
                round_evidence_uri="https://example.test/evidence.json",
                votes=[{"model": PANEL_MODELS[0], "outcome": "Yes"}],
            ),
        })
        # Deadline is in the past and this is not a sports stat feed, but the bundle is already committed.
        chain.markets[7]["question"] = "Will the notice be posted?"
        report = run_pass(env_for(chain, None, ScriptedPanel({0: "YES"}), fetch))[0]
        methods = [c["method"] for c in report["calls"]]
        self.assertEqual(methods, ["submit_verdict", "submit_verdict"])
        self.assertNotIn("commit_evidence", methods)
        self.assertEqual({c["model"] for c in report["calls"]}, set(PANEL_MODELS[1:]))

    def test_dry_run_fixture_invents_nothing(self):
        now, markets = load_markets_file(ROOT / "fixtures" / "relayer" / "offline_markets.json")
        chain_markets = {i: m for i, m in markets.items()}

        def get_market(market_id):
            market = chain_markets.get(market_id)
            return copy.deepcopy(market) if market is not None else None

        sent = []
        env = RelayEnv(
            dry_run=True,
            now_ns=now,
            get_market=get_market,
            market_ids=lambda: sorted(chain_markets),
            send=lambda method, args: sent.append((method, args)),
            fetch=lambda url: (_ for _ in ()).throw(AssertionError(url)),
            publish=lambda *a, **k: (_ for _ in ()).throw(AssertionError("publish")),
            panel=ScriptedPanel({0: "YES"}),
            max_rounds=2,
        )
        reports = {r["market_id"]: r for r in run_pass(env)}
        self.assertEqual(sent, [])
        self.assertEqual(reports[0]["detail"], "before deadline")
        self.assertEqual(reports[1]["action"], "commit_and_vote")
        self.assertTrue(reports[1]["calls"][0]["evidence_uri"].startswith("PENDING_COMMIT_SHA://"))
        self.assertNotIn("/master/", reports[1]["calls"][0]["evidence_uri"])
        self.assertNotIn("/main/", reports[1]["calls"][0]["evidence_uri"])
        self.assertTrue(all(c["sent"] is False and "signature" not in c and c["tx_hash"] is None for c in reports[1]["calls"]))
        self.assertEqual(reports[2]["action"], "skip")
        self.assertEqual([c["method"] for c in reports[3]["calls"]], ["finalize"])
        self.assertFalse(reports[3]["calls"][0]["sent"])
        self.assertEqual(reports[4]["detail"], "already Final")
        self.assertEqual(reports[5]["calls"], [])
        self.assertEqual(reports[6]["calls"], [])
        self.assertIn("app.testnet", reports[5]["detail"])
        self.assertEqual([c["method"] for c in reports[7]["calls"]], ["submit_verdict", "submit_verdict"])
        self.assertNotIn("commit_evidence", [c["method"] for c in reports[7]["calls"]])
        self.assertEqual(reports[8]["action"], "skip")


if __name__ == "__main__":
    unittest.main()
