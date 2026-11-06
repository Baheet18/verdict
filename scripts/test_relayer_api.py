#!/usr/bin/env python3
"""Offline tests for the relayer and HTTP API. No NEAR node and no API key."""
from __future__ import annotations

import json
import pathlib
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from api import LIMITATIONS, MAX_REDIRECTS, http_fetch, serve
from evidence_uri import github_raw_permalink
from mock_chain import ChainError, MockChain
from panel import PANEL_MODELS, LivePanel, MockPanel, PanelError, signed_text
from relayer import MOCK_VOTE_LABEL, ONE_NEAR, RelayerError, build_mock, run_demo
from verdict_request import PROMPT_TEMPLATE_HASH, build_request, check_request, evidence_bundle, sha

DAY = 86_400 * 1_000_000_000
FORBIDDEN = {
    "forbidden_logit_bias": "forbidden request key: logit_bias",
    "forbidden_guided_choice": "forbidden request key: guided_choice",
    "forbidden_guided_regex": "forbidden request key: guided_regex",
    "forbidden_response_format": "forbidden request key: response_format",
    "forbidden_structured_outputs": "forbidden request key: structured_outputs",
    "forbidden_tools": "forbidden request key: tools",
    "forbidden_tool_choice": "forbidden request key: tool_choice",
    "forbidden_stop": "forbidden request key: stop",
    "forbidden_n": "forbidden request key: n",
    "forbidden_top_p": "forbidden request key: top_p",
    "forbidden_message_name": "forbidden message key: name",
    "duplicate_json_key": "duplicate JSON key: temperature",
    "escaped_json_key_temperature": "JSON object key contains backslash",
    "wrong_seed": "request seed must be the fixed seed",
    "nonzero_temperature": "request temperature must be 0",
}


def header_fields(request_raw: str):
    body = json.loads(request_raw)
    header, evidence = body["messages"][1]["content"].split("\n", 1)
    fields = dict(part.split("=", 1) for part in header.split(" ")[1:])
    return body, fields, evidence


def bundle(text_a="alpha", text_b="beta"):
    rules = "Resolve YES only if the snapshots say so."
    snaps = [
        {"url": "https://example.com/a", "fetched_at": "2026-11-06T00:00:00Z", "sha256": sha(text_a), "text": text_a},
        {"url": "https://example.com/b", "fetched_at": "2026-11-06T00:00:00Z", "sha256": sha(text_b), "text": text_b},
    ]
    evidence = evidence_bundle("question", rules, "2026-11-06T00:00:00Z", snaps)
    return rules, evidence


def fresh(now=0, deadline=DAY):
    panel = MockPanel()
    chain = MockChain()
    for row in panel.resolvers():
        chain.register_signer(row["address"], row["model"], "ab" * 32)
    chain.register_relayer("relayer.local")
    chain.set_time(now)
    rules, evidence = bundle()
    market_id = chain.create_market(
        "question", rules, ["https://example.com/a", "https://example.com/b"],
        PROMPT_TEMPLATE_HASH, [row["address"] for row in panel.resolvers()],
        deadline, DAY, ONE_NEAR,
    )
    return panel, chain, market_id, rules, evidence


def commit(chain, market_id, evidence, when=DAY):
    chain.predecessor = "relayer.local"
    chain.set_time(when)
    chain.commit_evidence(market_id, chain.get_market(market_id)["round"], sha(evidence), "PENDING_COMMIT_SHA://evidence/x.json")


class RequestCheckTests(unittest.TestCase):
    def test_build_request_and_vectors(self):
        rules, evidence = bundle()
        raw = build_request(PANEL_MODELS[0], 0, 0, rules, evidence)
        body = json.loads(raw)
        self.assertEqual(set(body), {"model", "messages", "temperature", "seed"})
        self.assertNotIn("max_tokens", body)
        model, ev = check_request(raw.decode(), 0, 0, sha(rules), PROMPT_TEMPLATE_HASH)
        self.assertEqual(model, PANEL_MODELS[0])
        self.assertEqual(ev, sha(evidence))
        steered = dict(body)
        steered["max_tokens"] = 400
        with self.assertRaises(ValueError) as caught:
            check_request(json.dumps(steered), 0, 0, sha(rules), PROMPT_TEMPLATE_HASH)
        self.assertEqual(str(caught.exception), "forbidden request key: max_tokens")
        with self.assertRaises(ValueError) as caught:
            check_request('{"temperature":0,"temperature":1}', 0, 0, sha(rules), PROMPT_TEMPLATE_HASH)
        self.assertEqual(str(caught.exception), "duplicate JSON key: temperature")
        with self.assertRaises(ValueError) as caught:
            check_request('{"\\u0074emperature":0}', 0, 0, sha(rules), PROMPT_TEMPLATE_HASH)
        self.assertEqual(str(caught.exception), "JSON object key contains backslash")

        vectors = json.loads((ROOT / "contract" / "testdata" / "vectors.json").read_text())
        for name, vote in vectors["votes"].items():
            request = vote["request_raw"]
            _body, fields, _evidence = header_fields(request)
            try:
                check_request(request, int(fields["market_id"]), int(fields["round"]), fields["rules_hash"], fields["prompt_template_hash"])
            except ValueError as exc:
                self.assertEqual(str(exc), FORBIDDEN[name], name)
            else:
                self.assertNotIn(name, FORBIDDEN)
        market_reject = {
            "wrong_market": "wrong market_id",
            "wrong_rules": "wrong rules_hash",
            "wrong_template": "wrong prompt template",
        }
        for name, message in market_reject.items():
            with self.assertRaises(ValueError) as caught:
                check_request(vectors["votes"][name]["request_raw"], 0, 0, sha(vectors["rules"]), vectors["prompt_template_hash"])
            self.assertEqual(str(caught.exception), message, name)
        yes = vectors["votes"]["yes_0"]
        _body, fields, _evidence = header_fields(yes["request_raw"])
        self.assertEqual(fields["rules_hash"], sha(vectors["rules"]))
        self.assertEqual(fields["prompt_template_hash"], vectors["prompt_template_hash"])
        self.assertEqual(fields["prompt_template_hash"], PROMPT_TEMPLATE_HASH)

    def test_real_panel_requests_match_the_shipped_prompt(self):
        panel = json.loads((ROOT / "contract" / "testdata" / "real_panel.json").read_text())
        for vote in panel["votes"]:
            body, fields, evidence = header_fields(vote["request_raw"])
            self.assertEqual(set(body), {"model", "messages", "temperature", "seed"})
            self.assertEqual(fields["rules_hash"], sha(panel["rules"]))
            model, ev = check_request(
                vote["request_raw"], int(fields["market_id"]), int(fields["round"]),
                sha(panel["rules"]), panel["prompt_template_hash"],
            )
            self.assertEqual(model, vote["model"])
            self.assertEqual(ev, sha(evidence))
            self.assertEqual(ev, panel["evidence_sha256"])


class MockChainSecurityTests(unittest.TestCase):
    def test_evidence_lock_binding_and_replay(self):
        panel, chain, market_id, rules, evidence = fresh()
        chain.predecessor = "relayer.local"
        chain.set_time(DAY - 1)
        with self.assertRaises(ChainError) as caught:
            chain.commit_evidence(market_id, 0, sha(evidence), "PENDING_COMMIT_SHA://evidence/x.json")
        self.assertEqual(str(caught.exception), "market not ended")
        chain.predecessor = "other.local"
        chain.set_time(DAY)
        with self.assertRaises(ChainError) as caught:
            chain.commit_evidence(market_id, 0, sha(evidence), "PENDING_COMMIT_SHA://evidence/x.json")
        self.assertEqual(str(caught.exception), "relayer or owner only")

        panel.script = {PANEL_MODELS[0]: "YES"}
        raw = build_request(PANEL_MODELS[0], market_id, 0, rules, evidence)
        completion = panel.complete(PANEL_MODELS[0], raw)
        chain.predecessor = "relayer.local"
        with self.assertRaises(ChainError) as caught:
            chain.submit_verdict(market_id, raw.decode(), completion.response_raw, completion.signature)
        self.assertEqual(str(caught.exception), "evidence not committed for this round")

        commit(chain, market_id, evidence)
        self.assertEqual(chain.submit_verdict(market_id, raw.decode(), completion.response_raw, completion.signature), "YES")
        chain.predecessor = chain.owner
        with self.assertRaises(ChainError) as caught:
            chain.commit_evidence(market_id, 0, sha(bundle("later", "text")[1]), "PENDING_COMMIT_SHA://evidence/z.json")
        self.assertEqual(str(caught.exception), "votes already cast this round")
        chain.predecessor = "relayer.local"
        with self.assertRaises(ChainError) as caught:
            chain.submit_verdict(market_id, raw.decode(), completion.response_raw, completion.signature)
        self.assertEqual(str(caught.exception), "signed response already used")

        other_rules, other = bundle("other", "text")
        other_raw = build_request(PANEL_MODELS[1], market_id, 0, rules, other)
        panel.script = {PANEL_MODELS[1]: "YES"}
        other_completion = panel.complete(PANEL_MODELS[1], other_raw)
        with self.assertRaises(ChainError) as caught:
            chain.submit_verdict(market_id, other_raw.decode(), other_completion.response_raw, other_completion.signature)
        self.assertEqual(str(caught.exception), "evidence bundle differs from committed hash")

        swapped = build_request(PANEL_MODELS[1], market_id, 0, rules, evidence)
        response = other_completion.response_raw
        bad_sig = panel.sign(PANEL_MODELS[0], signed_text(PANEL_MODELS[1], swapped, response))
        with self.assertRaises(ChainError) as caught:
            chain.submit_verdict(market_id, swapped.decode(), response, bad_sig)
        self.assertEqual(str(caught.exception), "signer is registered for a different model")

        distinct = build_request(PANEL_MODELS[0], market_id, 0, rules, evidence)
        panel.script = {PANEL_MODELS[0]: "NO"}
        second = panel.complete(PANEL_MODELS[0], distinct)
        with self.assertRaises(ChainError) as caught:
            chain.submit_verdict(market_id, distinct.decode(), second.response_raw, second.signature)
        self.assertEqual(str(caught.exception), "signer already voted")

        steered = json.loads(swapped)
        steered["logit_bias"] = {"0": 100}
        with self.assertRaises(ChainError) as caught:
            chain.submit_verdict(market_id, json.dumps(steered), response, "00" * 65)
        self.assertEqual(str(caught.exception), "forbidden request key: logit_bias")

    def test_owner_replace_is_not_available_to_the_relayer(self):
        _panel, chain, market_id, _rules, evidence = fresh()
        commit(chain, market_id, evidence)
        _rules, other = bundle("other", "text")
        chain.predecessor = "relayer.local"
        with self.assertRaises(ChainError) as caught:
            chain.commit_evidence(market_id, 0, sha(other), "PENDING_COMMIT_SHA://evidence/y.json")
        self.assertEqual(str(caught.exception), "owner only to replace evidence")
        chain.predecessor = chain.owner
        chain.commit_evidence(market_id, 0, sha(other), "PENDING_COMMIT_SHA://evidence/y.json")
        self.assertEqual(chain.get_market(market_id)["round_evidence_hash"], sha(other))

    def test_fixture_signatures_settle_on_the_mock_chain(self):
        self._settle_fixture(ROOT / "contract" / "testdata" / "vectors.json", "address", ["yes_0", "yes_1", "yes_2"])
        self._settle_real_panel()

    def _settle_fixture(self, path, address_field, names):
        payload = json.loads(path.read_text())
        votes = [payload["votes"][name] if isinstance(payload["votes"], dict) else payload["votes"][name] for name in names]
        chain = MockChain()
        for vote in votes:
            chain.register_signer(vote[address_field], vote["model"], "ab" * 32)
        chain.register_relayer("relayer.local")
        chain.set_time(0)
        market_id = chain.create_market(
            payload["question"], payload["rules"], ["https://example.com/source"],
            payload["prompt_template_hash"], [vote[address_field] for vote in votes],
            DAY, DAY, ONE_NEAR,
        )
        self.assertEqual(market_id, 0)
        _body, fields, _evidence = header_fields(votes[0]["request_raw"])
        chain.predecessor = "relayer.local"
        chain.set_time(DAY)
        chain.commit_evidence(0, 0, fields["evidence_bundle_hash"], "PENDING_COMMIT_SHA://evidence/fixture.json")
        tampered = votes[0]["signature"][:-1] + ("0" if votes[0]["signature"][-1] != "0" else "1")
        with self.assertRaises(ChainError) as caught:
            chain.submit_verdict(0, votes[0]["request_raw"], votes[0]["response_raw"], tampered)
        self.assertEqual(str(caught.exception), "bad signature")
        for vote in votes:
            self.assertEqual(
                chain.submit_verdict(0, vote["request_raw"], vote["response_raw"], vote["signature"]),
                vote.get("parsed_outcome", "YES"),
            )
        self.assertEqual(chain.get_market(0)["status"]["Proposed"]["outcome"], "Yes")

    def _settle_real_panel(self):
        payload = json.loads((ROOT / "contract" / "testdata" / "real_panel.json").read_text())
        chain = MockChain()
        votes = payload["votes"]
        for vote in votes:
            chain.register_signer(vote["signing_address"], vote["model"], vote["attestation_report_sha256"])
        chain.set_time(0)
        chain.create_market(
            payload["question"], payload["rules"], ["https://example.com/source"],
            payload["prompt_template_hash"], [vote["signing_address"] for vote in votes],
            DAY, DAY, ONE_NEAR,
        )
        chain.predecessor = chain.owner
        chain.set_time(DAY)
        chain.commit_evidence(0, 0, payload["evidence_sha256"], payload["proposed_evidence_uri_after_push"])
        for vote in votes:
            self.assertEqual(chain.submit_verdict(0, vote["request_raw"], vote["response_raw"], vote["signature"]), "YES")
        self.assertEqual(chain.get_market(0)["status"]["Proposed"]["outcome"], "Yes")
        chain.set_time(2 * DAY)
        chain.finalize(0)
        self.assertEqual(chain.get_market(0)["status"]["Final"]["outcome"], "Yes")

        wrong = MockChain()
        vote = votes[0]
        wrong.register_signer(vote["signing_address"], PANEL_MODELS[1], vote["attestation_report_sha256"])
        wrong.set_time(0)
        wrong.create_market(payload["question"], payload["rules"], ["https://example.com/source"], payload["prompt_template_hash"], [vote["signing_address"]], DAY, DAY, ONE_NEAR)
        wrong.predecessor = wrong.owner
        wrong.set_time(DAY)
        wrong.commit_evidence(0, 0, payload["evidence_sha256"], "PENDING_COMMIT_SHA://evidence/real.json")
        with self.assertRaises(ChainError) as caught:
            wrong.submit_verdict(0, vote["request_raw"], vote["response_raw"], vote["signature"])
        self.assertEqual(str(caught.exception), "signer is registered for a different model")


class Flaky(MockPanel):
    def __init__(self):
        super().__init__()
        self.fail_once = {PANEL_MODELS[0]}

    def complete(self, model, request_raw):
        if model in self.fail_once:
            self.fail_once.remove(model)
            raise PanelError("model call failed")
        return super().complete(model, request_raw)


class RelayerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def tmp(self):
        path = self.directory / (self._testMethodName + "-evidence")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _spec(self, script=None, window=0, deadline=2_000_000):
        body = {
            "question": "Did the example snapshots agree?",
            "rules": "Map a scripted unanimous YES to YES. This example does not ask the models.",
            "sources": ["https://example.com/a", "https://example.com/b"],
            "deadline_ns": deadline,
            "dispute_window_ns": window,
            "market_end": "2026-11-06T00:00:00Z",
            "source_snapshots": [
                {"url": "https://example.com/a", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot A"},
                {"url": "https://example.com/b", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot B"},
            ],
        }
        if script is not None:
            body["mock_script"] = script
        return body

    def test_relayer_locks_one_bundle_and_submits_three_models(self):
        relayer = build_mock(self.tmp(), now_ns=1_000_000, fetch=lambda url: (_ for _ in ()).throw(AssertionError("fetch should not run")))
        created = relayer.create_market(self._spec({model: "YES" for model in PANEL_MODELS}))
        self.assertEqual(created["status"], "Open")
        self.assertIsNone(created["final_outcome"])
        with self.assertRaises(RelayerError) as caught:
            relayer.relay(created["market_id"])
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(str(caught.exception), "market not ended")
        self.assertEqual(list((self.tmp()).glob("*.json")), [])
        relayer.set_clock(2_000_000)
        view = relayer.relay(0)
        self.assertEqual(view["status"], "Final")
        self.assertTrue(view["final"])
        self.assertEqual(view["final_outcome"], "YES")
        self.assertTrue(view["votes_verified"])
        self.assertEqual([row["model"] for row in view["votes"]], PANEL_MODELS)
        self.assertTrue(view["evidence_uri"].startswith("PENDING_COMMIT_SHA://"))
        self.assertNotIn("github.com", view["evidence_uri"])
        self.assertNotIn("/master/", view["evidence_uri"])
        self.assertNotIn("/main/", view["evidence_uri"])
        self.assertNotIn("Hardware + host stack verified", json.dumps(view))
        self.assertNotIn("Signature valid", json.dumps(view))
        self.assertTrue(all(item["label"] == MOCK_VOTE_LABEL and item["tee_signer"] is False for item in view["mock_resolvers"]))
        self.assertNotIn("resolvers", view)
        evidence_parts = []
        request_hashes = []
        for row in view["votes"]:
            self.assertEqual(row["label"], MOCK_VOTE_LABEL)
            self.assertNotIn("signer", row)
            self.assertFalse(row["tee_signer"])
            self.assertTrue(row["mock_signer"].startswith("0x"))
            self.assertEqual(row["script_slot"], row["model"])
            self.assertTrue(row["signature_ok"] and row["model_binding_ok"])
            body, fields, evidence = header_fields(row["request_raw"])
            self.assertEqual(set(body), {"model", "messages", "temperature", "seed"})
            self.assertEqual(body["temperature"], 0)
            self.assertEqual(body["seed"], 1114)
            evidence_parts.append(evidence)
            request_hashes.append(sha(row["request_raw"]))
            self.assertEqual(fields["evidence_bundle_hash"], view["evidence_bundle_hash"])
            self.assertNotIn("mock_script", evidence)
        self.assertEqual(len(set(evidence_parts)), 1)
        self.assertEqual(len(set(request_hashes)), 3)
        published = json.loads((relayer.evidence_dir / f"{view['evidence_bundle_hash']}.json").read_text())
        self.assertEqual(sha(published["evidence"]), view["evidence_bundle_hash"])
        methods = [call["method"] for call in view["chain_calls"]]
        self.assertEqual(methods, ["create_market", "commit_evidence", "submit_verdict", "submit_verdict", "submit_verdict", "finalize"])
        before = len(relayer.calls)
        again = relayer.relay(0)
        self.assertEqual(again["final_outcome"], "YES")
        self.assertEqual(len(relayer.calls), before)
        for account in relayer.panel._accounts:
            key = account[1].key.hex()
            key = key[2:] if key.startswith("0x") else key
            self.assertNotIn(key, json.dumps(view))

    def test_split_and_abstain_do_not_invent_an_outcome(self):
        relayer = build_mock(self.tmp(), now_ns=1_000_000)
        script = {model: "YES" for model in PANEL_MODELS}
        script[PANEL_MODELS[2]] = "NO"
        relayer.create_market(self._spec(script))
        relayer.set_clock(2_000_000)
        view = relayer.relay(0)
        self.assertEqual(view["status"], "Escalated")
        self.assertTrue(view["escalated"])
        self.assertIsNone(view["final_outcome"])
        self.assertIsNone(view["proposed_outcome"])
        self.assertEqual([row["outcome"] for row in view["votes"]], ["YES", "YES", "NO"])
        self.assertTrue(view["votes_verified"])

        other = build_mock(self.tmp(), now_ns=1_000_000)
        other.create_market(self._spec(None))
        other.set_clock(2_000_000)
        abstain = other.relay(0)
        self.assertEqual(abstain["status"], "Escalated")
        self.assertIsNone(abstain["final_outcome"])
        self.assertEqual({row["outcome"] for row in abstain["votes"]}, {"AMBIGUOUS"})

    def test_too_early_advances_the_round_and_blocks_replay(self):
        relayer = build_mock(self.tmp(), now_ns=1_000_000)
        relayer.create_market(self._spec({model: "TOO_EARLY" for model in PANEL_MODELS}))
        relayer.set_clock(2_000_000)
        first = relayer.relay(0)
        self.assertEqual(first["status"], "Open")
        self.assertEqual(first["round"], 1)
        self.assertEqual(first["last_round_outcome"], "TOO_EARLY")
        self.assertIsNone(first["evidence_bundle_hash"])
        self.assertEqual(first["votes"], [])
        old = relayer.records[0]["signed"][0]
        relayer.chain.predecessor = "relayer.local"
        with self.assertRaises(ChainError) as caught:
            relayer.chain.submit_verdict(0, old["request_raw"], old["response_raw"], old["signature"])
        self.assertEqual(str(caught.exception), "wrong round")
        relayer.set_mock_script(0, {model: "YES" for model in PANEL_MODELS})
        second = relayer.relay(0)
        self.assertEqual(second["final_outcome"], "YES")
        self.assertEqual(second["round"], 1)
        self.assertIn("round=1", second["votes"][0]["request_raw"])
        self.assertNotEqual(old["signature"], second["votes"][0]["signature"])

    def test_failed_model_is_retried_without_a_new_bundle(self):
        relayer = build_mock(self.tmp(), now_ns=1_000_000, panel=Flaky())
        relayer.create_market(self._spec({model: "YES" for model in PANEL_MODELS}))
        relayer.set_clock(2_000_000)
        first = relayer.relay(0)
        self.assertEqual(first["status"], "Open")
        self.assertEqual(len(first["votes"]), 2)
        self.assertEqual(first["panel_errors"], [{"model": PANEL_MODELS[0], "error": "model call failed"}])
        locked = first["evidence_bundle_hash"]
        second = relayer.relay(0)
        self.assertEqual(second["final_outcome"], "YES")
        self.assertEqual(second["evidence_bundle_hash"], locked)
        self.assertEqual(second["panel_errors"], [])

    def test_fetch_errors_do_not_keep_the_exception_text(self):
        def fetch(_url):
            raise RuntimeError("canary-fetch-text")
        relayer = build_mock(self.tmp(), now_ns=1_000_000, fetch=fetch)
        relayer.create_market({
            "question": "q",
            "rules": "r",
            "sources": ["https://example.com/a"],
            "deadline_ns": 2_000_000,
        })
        relayer.set_clock(2_000_000)
        with self.assertRaises(RelayerError) as caught:
            relayer.relay(0)
        self.assertNotIn("canary-fetch-text", str(caught.exception))
        self.assertIsNone(relayer.chain.get_market(0)["round_evidence_hash"])

    def test_demo_and_permalink_guard(self):
        view = run_demo(self.tmp())
        self.assertEqual(view["chain"], "mock")
        self.assertEqual(view["panel_execution"], "scripted")
        self.assertEqual(view["final_outcome"], "YES")
        self.assertTrue(view["evidence_uri"].startswith("PENDING_COMMIT_SHA://"))
        with self.assertRaises(ValueError):
            github_raw_permalink("evidence/abc.json", "master")
        with self.assertRaises(ValueError):
            github_raw_permalink("evidence/abc.json", "main")


class LivePanelTests(unittest.TestCase):
    def test_fake_near_ai_signs_three_models_without_leaking_the_key(self):
        token = "test-token-not-a-real-key"
        keys = MockPanel()
        store = {}

        def transport(method, url, headers, body, params):
            self.assertEqual(headers["Authorization"], "Bearer " + token)
            if method == "POST":
                request = json.loads(body)
                self.assertEqual(set(request), {"model", "messages", "temperature", "seed"})
                model = request["model"]
                content = '{"outcome":"YES","quotes":[],"reasoning":"fixture","confidence":1}'
                response = {
                    "id": "live" + sha(body)[:12],
                    "object": "chat.completion",
                    "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                }
                raw = json.dumps(response).encode()
                store[model] = (body, raw)
                return 200, raw
            model = params["model"]
            request_raw, raw = store[model]
            text = signed_text(model, request_raw, raw.decode())
            address = next(row["address"] for row in keys.resolvers() if row["model"] == model)
            payload = {"signature": keys.sign(model, text), "text": text, "signing_address": address}
            return 200, json.dumps(payload).encode()

        panel = LivePanel(transport, lambda: token, resolvers=keys.resolvers())
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        relayer = build_mock(pathlib.Path(directory.name), now_ns=1_000_000, panel=panel)
        relayer.create_market({
            "question": "q",
            "rules": "r",
            "sources": ["https://example.com/a", "https://example.com/b"],
            "deadline_ns": 2_000_000,
            "dispute_window_ns": 0,
            "source_snapshots": [
                {"url": "https://example.com/a", "fetched_at": "t", "text": "one"},
                {"url": "https://example.com/b", "fetched_at": "t", "text": "two"},
            ],
        })
        relayer.set_clock(2_000_000)
        view = relayer.relay(0)
        self.assertEqual(view["panel_execution"], "near-ai")
        self.assertEqual(view["final_outcome"], "YES")
        for vote in view["votes"]:
            self.assertEqual(vote["label"], MOCK_VOTE_LABEL)
            self.assertFalse(vote["tee_signer"])
            self.assertNotIn("signer", vote)
        self.assertEqual(panel.calls, PANEL_MODELS)
        rendered = json.dumps(view)
        self.assertNotIn(token, rendered)
        self.assertNotIn("did not call Qwen", rendered)
        evidence = header_fields(view["votes"][0]["request_raw"])[2]
        for vote in view["votes"][1:]:
            self.assertEqual(header_fields(vote["request_raw"])[2], evidence)

        def leak(_method, _url, _headers, _body, _params):
            return 500, token.encode()

        with self.assertRaises(PanelError) as caught:
            LivePanel(leak, lambda: token).complete(PANEL_MODELS[0], b"{}")
        self.assertNotIn(token, str(caught.exception))
        self.assertEqual(str(caught.exception), "chat completion HTTP 500")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = pathlib.Path(self._tmp.name)
        self.relayer = build_mock(self.directory / "evidence", now_ns=1_000_000_000)
        self.server = serve(self.relayer, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._tmp.cleanup()

    def req(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"http://{self.host}:{self.port}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        last = None
        for _ in range(20):
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status, json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode())
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(0.05)
        raise last

    def test_http_flow_matches_the_integration_example(self):
        status, health = self.req("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["mode"], "mock")
        self.assertEqual(health["panel"], PANEL_MODELS)
        self.assertEqual(health["limitations"], LIMITATIONS)
        joined = " ".join(health["limitations"])
        self.assertIn("does not call Qwen, DeepSeek, or GLM", joined)
        self.assertIn("PENDING_COMMIT_SHA", joined)
        self.assertIn("does not predict disputes", joined)
        self.assertIn("not authoritative", joined)
        self.assertIn(MOCK_VOTE_LABEL, joined)
        self.assertIn("rebinding", joined)
        integration = (ROOT / "docs" / "INTEGRATION.md").read_text()
        self.assertIn("DNS-rebinding gap", integration)
        self.assertIn("pinned to the IP that was checked", integration)
        self.assertIn("acceptable only while the server is bound to `127.0.0.1`", integration)
        listed_resolvers = self.req("GET", "/v1/resolvers")[1]
        self.assertNotIn("resolvers", listed_resolvers)
        for item in listed_resolvers["mock_resolvers"]:
            self.assertEqual(item["label"], MOCK_VOTE_LABEL)
            self.assertFalse(item["tee_signer"])
            self.assertNotIn("address", item)
            self.assertNotIn("model", item)
        self.assertEqual(self.req("POST", "/v1/testing/clock", {"now_ns": 1_000_000_000})[0], 200)
        spec = {
            "question": "Did the example snapshots agree?",
            "rules": "Map a scripted unanimous YES to YES. This example does not ask the models.",
            "sources": ["https://example.com/a", "https://example.com/b"],
            "deadline_ns": 2_000_000_000,
            "dispute_window_ns": 0,
            "market_end": "2026-11-06T00:00:00Z",
            "source_snapshots": [
                {"url": "https://example.com/a", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot A"},
                {"url": "https://example.com/b", "fetched_at": "2026-11-06T00:00:00Z", "text": "example snapshot B"},
            ],
            "mock_script": {model: "YES" for model in PANEL_MODELS},
        }
        self.assertEqual(self.req("POST", "/v1/markets", dict(spec, logit_bias={"0": 1}))[0], 400)
        self.assertEqual(self.req("POST", "/v1/markets", dict(spec, sources=["file:///etc/passwd"]))[0], 400)
        self.assertEqual(self.req("POST", "/v1/markets", dict(spec, sources=["https://user:pw@example.com/a"]))[0], 400)
        self.assertEqual(self.req("POST", "/v1/markets", dict(spec, resolvers=["0x" + "11" * 20]))[0], 400)
        self.assertEqual(self.req("POST", "/v1/markets", dict(spec, prompt_template_hash="ab" * 32))[0], 400)
        bad_hash = dict(spec)
        bad_hash["source_snapshots"] = [dict(spec["source_snapshots"][0], sha256="00" * 32), spec["source_snapshots"][1]]
        self.assertEqual(self.req("POST", "/v1/markets", bad_hash)[0], 400)
        self.assertEqual(self.req("POST", "/v1/markets", dict(spec, deadline_ns=1))[0], 400)

        status, created = self.req("POST", "/v1/markets", spec)
        self.assertEqual(status, 200)
        self.assertEqual(created["market_id"], 0)
        self.assertEqual(self.relayer.chain.get_market(0)["dispute_bond"], ONE_NEAR)
        code, early = self.req("POST", "/v1/markets/0/relay")
        self.assertEqual(code, 409)
        self.assertEqual(early["error"], "market not ended")
        self.req("POST", "/v1/testing/clock", {"now_ns": 2_000_000_000})
        status, view = self.req("POST", "/v1/markets/0/relay")
        self.assertEqual(status, 200)
        self.assertEqual(view["final_outcome"], "YES")
        self.assertTrue(view["votes_verified"])
        for vote in view["votes"]:
            self.assertEqual(vote["label"], MOCK_VOTE_LABEL)
            self.assertNotIn("signer", vote)
            self.assertFalse(vote["tee_signer"])
            self.assertNotIn('"signer"', json.dumps(vote))
        self.assertEqual(self.req("GET", "/v1/markets/0")[1]["final_outcome"], "YES")
        evidence = self.req("GET", view["evidence_api_path"])
        self.assertEqual(evidence[0], 200)
        self.assertEqual(sha(evidence[1]["evidence"]), view["evidence_bundle_hash"])
        listed = self.req("GET", "/v1/markets")[1]["markets"][0]
        self.assertNotIn("request_raw", json.dumps(listed["votes"]))
        self.assertNotIn("rules", listed)
        for account in self.relayer.panel._accounts:
            key = account[1].key.hex()
            key = key[2:] if key.startswith("0x") else key
            self.assertNotIn(key, json.dumps(view))
        calls = len(self.relayer.calls)
        self.req("POST", "/v1/markets/0/relay")
        self.assertEqual(len(self.relayer.calls), calls)

        # A second market escalates on a split, then tick leaves the finished market alone.
        self.req("POST", "/v1/testing/clock", {"now_ns": 1_000_000_000})
        split = dict(spec)
        split["mock_script"] = {model: "YES" for model in PANEL_MODELS}
        split["mock_script"][PANEL_MODELS[1]] = "NO"
        split["deadline_ns"] = 2_000_000_000
        self.assertEqual(self.req("POST", "/v1/markets", split)[0], 200)
        self.req("POST", "/v1/testing/clock", {"now_ns": 2_000_000_000})
        tick = self.req("POST", "/v1/relay/tick", {})[1]
        by_id = {row["market_id"]: row for row in tick["results"]}
        self.assertTrue(by_id[0]["ok"])
        self.assertEqual(by_id[0]["market"]["final_outcome"], "YES")
        self.assertEqual(by_id[1]["market"]["status"], "Escalated")
        self.assertIsNone(by_id[1]["market"]["final_outcome"])

    def test_proposed_stays_unfinal_until_the_window_elapses(self):
        spec = {
            "question": "q",
            "rules": "r",
            "sources": ["https://example.com/a", "https://example.com/b"],
            "deadline_ns": 2_000_000_000,
            "source_snapshots": [
                {"url": "https://example.com/a", "fetched_at": "t", "text": "one"},
                {"url": "https://example.com/b", "fetched_at": "t", "text": "two"},
            ],
            "mock_script": {model: "YES" for model in PANEL_MODELS},
        }
        self.req("POST", "/v1/testing/clock", {"now_ns": 1_000_000_000})
        self.req("POST", "/v1/markets", spec)
        self.req("POST", "/v1/testing/clock", {"now_ns": 2_000_000_000})
        view = self.req("POST", "/v1/markets/0/relay")[1]
        self.assertEqual(view["status"], "Proposed")
        self.assertEqual(view["proposed_outcome"], "YES")
        self.assertIsNone(view["final_outcome"])
        self.assertFalse(view["final"])
        at_ns = view["status_raw"]["Proposed"]["at_ns"]
        self.req("POST", "/v1/testing/clock", {"now_ns": at_ns + view["dispute_window_ns"] - 1})
        self.assertEqual(self.req("POST", "/v1/markets/0/relay")[1]["status"], "Proposed")
        self.req("POST", "/v1/testing/clock", {"now_ns": at_ns + view["dispute_window_ns"]})
        final = self.req("POST", "/v1/markets/0/relay")[1]
        self.assertEqual(final["status"], "Final")
        self.assertEqual(final["final_outcome"], "YES")


class HygieneTests(unittest.TestCase):
    def test_new_modules_do_not_touch_env_files_or_the_process_environment(self):
        for name in ("api.py", "relayer.py", "panel.py", "mock_chain.py", "verdict_request.py"):
            text = (ROOT / "scripts" / name).read_text()
            self.assertNotIn("load_dotenv", text)
            self.assertNotIn("dotenv", text)
            self.assertNotIn(".env", text)
            self.assertNotIn("NEARAI_API_KEY", text)
            self.assertNotIn("os.environ", text)

    def test_http_fetch_refuses_local_and_credentialed_urls(self):
        with self.assertRaises(ValueError):
            http_fetch("file:///etc/passwd", get=self._fail_get)
        with self.assertRaises(ValueError):
            http_fetch("https://user:pw@example.com/a", get=self._fail_get)

    def test_http_fetch_rejects_private_loopback_link_local_and_metadata(self):
        blocked = [
            "http://127.0.0.1/",
            "http://127.0.0.1:80/latest",
            "http://10.1.2.3/x",
            "http://192.168.1.1/",
            "http://172.16.0.1/",
            "http://169.254.169.254/latest/meta-data",
            "http://169.254.170.2/",
            "http://169.254.1.1/",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://2130706433/",
            "http://0x7f000001/",
            "http://localhost/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as caught:
                    http_fetch(url, get=self._fail_get)
                self.assertIn("refusing", str(caught.exception).lower())

        def metadata_resolver(host, _port):
            self.assertEqual(host, "metadata.example")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]

        with self.assertRaises(ValueError) as caught:
            http_fetch("http://metadata.example/latest", resolver=metadata_resolver, get=self._fail_get)
        self.assertIn("metadata", str(caught.exception))

        def mixed_resolver(_host, _port):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 80)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80)),
            ]

        with self.assertRaises(ValueError):
            http_fetch("http://mixed.example/", resolver=mixed_resolver, get=self._fail_get)

    def test_http_fetch_limits_redirects_and_rechecks_each_target(self):
        calls = []

        def getter(url, **kwargs):
            self.assertIs(kwargs["allow_redirects"], False)
            calls.append(url)
            class Response:
                def __init__(self, status, location=None, text="ok"):
                    self.status_code = status
                    self.headers = {"Location": location} if location else {}
                    self.text = text
                def raise_for_status(self):
                    if self.status_code >= 400:
                        raise RuntimeError("status")
            if url == "http://1.1.1.1/start":
                return Response(302, "http://127.0.0.1/secret")
            if url.startswith("http://1.0.0.1/"):
                hop = url.rsplit("/", 1)[-1]
                nxt = str(int(hop) + 1)
                return Response(302, f"http://8.8.8.8/{nxt}")
            if url.startswith("http://8.8.8.8/"):
                hop = url.rsplit("/", 1)[-1]
                return Response(302, f"http://1.0.0.1/{hop}")
            return Response(200, text="public-body")

        with self.assertRaises(ValueError):
            http_fetch("http://1.1.1.1/start", get=getter)
        self.assertEqual(calls, ["http://1.1.1.1/start"])

        calls.clear()
        with self.assertRaises(ValueError) as caught:
            http_fetch("http://1.0.0.1/0", get=getter)
        self.assertIn("too many redirects", str(caught.exception))
        self.assertEqual(len(calls), MAX_REDIRECTS + 1)
        self.assertNotIn("http://8.8.8.8/2", calls)

        calls.clear()
        body = http_fetch("http://1.1.1.1/ok", get=getter)
        self.assertEqual(body, "public-body")
        self.assertEqual(calls, ["http://1.1.1.1/ok"])

    @staticmethod
    def _fail_get(*_args, **_kwargs):
        raise AssertionError("fetch was attempted")


if __name__ == "__main__":
    unittest.main()
