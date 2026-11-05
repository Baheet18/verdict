#!/usr/bin/env python3
"""Python mirror of the contract's parse_outcome / request header must agree with the Rust tests' expectations."""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verdict_request import parse_outcome, build_request, sha, PROMPT_TEMPLATE_HASH, header
root = pathlib.Path(__file__).resolve().parent.parent / "contract" / "testdata"
V = json.loads((root / "vectors.json").read_text())["votes"]
exp = {"substring_trap_0": "NO", "prose_wrapped_0": None, "truncated_0": None, "lowercase_0": None, "fenced_yes_2": "YES", "yes_0": "YES", "too_early_r0_1": "TOO_EARLY"}
for k, e in exp.items():
    assert parse_outcome(V[k]["response_raw"]) == e, (k, parse_outcome(V[k]["response_raw"]))
w = lambda c: json.dumps({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": c}}]})
assert parse_outcome(w('\n\n{"outcome":"TOO_EARLY"}')) == "TOO_EARLY"
assert parse_outcome(w('[{"outcome":"YES"}]')) is None
assert parse_outcome(w('{"outcome":"YES"} {"outcome":"NO"}')) is None
assert parse_outcome(w('```json\n{"outcome":"YES"}')) is None
for x in json.loads((root / "real_panel.json").read_text())["votes"]:
    assert parse_outcome(x["response_raw"]) == x["parsed_outcome"]
    body = json.loads(x["request_raw"]); assert body["temperature"] == 0 and body["seed"] == 1114
    hdr, ev = body["messages"][1]["content"].split("\n", 1)
    assert hdr.endswith("evidence_bundle_hash=" + sha(ev)) and sha(body["messages"][0]["content"]) == PROMPT_TEMPLATE_HASH
print("ALL verdict_request CHECKS PASSED")
