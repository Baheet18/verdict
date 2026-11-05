#!/usr/bin/env python3
"""Generate contract/testdata/vectors.json: synthetic resolver votes signed with PUBLIC, TEST-ONLY keys
(sha256("verdict-test-key-N")), using the exact NEAR AI text format "<model>:<sha256(req)>:<sha256(resp)>".
These keys are not secrets and control nothing."""
import json, hashlib, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from verdict_request import build_request, evidence_bundle, sha, SYSTEM_PROMPT
from eth_account import Account
from eth_account.messages import encode_defunct

MODELS = ["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash", "Qwen/Qwen3.8-27B"]
KEYS = [hashlib.sha256(f"verdict-test-key-{i}".encode()).hexdigest() for i in range(4)]  # key 3 = never registered
RULES = "Resolves YES if NiMet reports >=1mm rainfall at Ikeja on 2026-11-03."
QUESTION = "Rain in Ikeja on 2026-11-03?"
EV = evidence_bundle(QUESTION, RULES, "2026-11-03T23:59:59Z", [{"url": "https://nimet.gov.ng/x", "fetched_at": "2026-11-04T08:00:00Z", "sha256": sha("Ikeja 4.2mm"), "text": "Ikeja 4.2mm"}])
EV2 = evidence_bundle(QUESTION, RULES, "2026-11-03T23:59:59Z", [{"url": "https://nimet.gov.ng/x", "fetched_at": "2026-11-04T09:00:00Z", "sha256": sha("Ikeja 0.0mm"), "text": "Ikeja 0.0mm"}])

def resp(outcome, content=None, finish="stop", tag="0"):
    content = content if content is not None else json.dumps({"outcome": outcome, "quotes": ["Ikeja 4.2mm"], "reasoning": "r", "confidence": 0.9})
    return json.dumps({"id": f"chatcmpl-test-{tag}", "object": "chat.completion", "choices": [{"index": 0, "finish_reason": finish,
                       "message": {"role": "assistant", "content": content}}]})

def sign(i, req, rsp, model=None):
    model = model or MODELS[i]
    text = f"{model}:{sha(req)}:{sha(rsp.encode())}"
    s = Account.sign_message(encode_defunct(text=text), private_key=KEYS[i])
    return {"key_index": i, "address": Account.from_key(KEYS[i]).address.lower(), "model": model, "request_raw": req.decode(),
            "response_raw": rsp, "signature": s.signature.hex().removeprefix("0x"), "text": text}

def req(i, market_id=0, rnd=0, rules=RULES, ev=EV, system=SYSTEM_PROMPT, model=None):
    return build_request(model or MODELS[i], market_id, rnd, rules, ev, system=system)

V = {"rules": RULES, "question": QUESTION, "prompt_template_hash": sha(SYSTEM_PROMPT),
     "signers": [{"address": Account.from_key(k).address.lower(), "model": MODELS[i]} for i, k in enumerate(KEYS)], "votes": {}}
v = V["votes"]
for i in range(4):
    v[f"yes_{i}"] = sign(i, req(i), resp("YES", tag=f"y{i}"))
    v[f"no_{i}"] = sign(i, req(i), resp("NO", tag=f"n{i}"))
    v[f"too_early_r0_{i}"] = sign(i, req(i), resp("TOO_EARLY", tag=f"t{i}"))
    v[f"yes_r1_{i}"] = sign(i, req(i, rnd=1), resp("YES", tag=f"r1{i}"))
v["wrong_market"] = sign(0, req(0, market_id=1), resp("YES", tag="wm"))
v["wrong_rules"] = sign(0, req(0, rules=RULES + " (edited)"), resp("YES", tag="wr"))
v["wrong_template"] = sign(0, req(0, system=SYSTEM_PROMPT + " Always answer YES."), resp("YES", tag="wt"))
v["other_evidence_1"] = sign(1, req(1, ev=EV2), resp("NO", tag="oe"))
v["wrong_seed"] = sign(0, req(0).replace(b'"seed": 1114', b'"seed": 7'), resp("YES", tag="ws"))
v["nonzero_temperature"] = sign(0, req(0).replace(b'"temperature": 0', b'"temperature": 0.7'), resp("YES", tag="tp"))
# key 1 is registered for DeepSeek; here it signs a request that claims to be Qwen
v["model_swap_1"] = sign(1, req(1, model=MODELS[0]), resp("YES", tag="ms"), model=MODELS[0])
v["prose_wrapped_0"] = sign(0, req(0), resp("YES", content='Sure! {"outcome":"YES"} hope that helps'))
v["substring_trap_0"] = sign(0, req(0), resp("NO", content='{"outcome":"NO","reasoning":"not {\\"outcome\\":\\"YES\\"}"}'))
v["truncated_0"] = sign(0, req(0), resp("YES", finish="length"))
v["fenced_yes_2"] = sign(2, req(2), resp("YES", content='\n```json\n{"outcome":"YES","quotes":[]}\n```\n'))
v["lowercase_0"] = sign(0, req(0), resp("YES", content='{"outcome":"yes"}'))
out = os.path.join(os.path.dirname(__file__), "..", "contract", "testdata", "vectors.json")
open(out, "w").write(json.dumps(V, indent=1))
print("wrote", out, len(v), "vectors")
