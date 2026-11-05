"""Canonical Verdict resolver request + strict outcome parser (mirrors contract/src/lib.rs exactly).

request_raw = the exact chat-completions JSON body sent to NEAR AI. The contract re-hashes these bytes, so
they must be sent byte-for-byte (use `data=`, never `json=`).
  messages[0] = system: SYSTEM_PROMPT            sha256(SYSTEM_PROMPT) == market.prompt_template_hash
  messages[1] = user:   "<canonical header>\\n<evidence bundle>"   sha256(evidence bundle) == evidence_bundle_hash
"""
import hashlib, json

REQUIRED_SEED = 1114           # must equal REQUIRED_SEED in the contract
HEADER_PREFIX = "VERDICT/1"
OUTCOMES = ("YES", "NO", "TOO_EARLY", "AMBIGUOUS")
sha = lambda b: hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()

SYSTEM_PROMPT = (
    "You resolve prediction markets strictly from the SOURCE SNAPSHOTS in the evidence bundle, applying the market RULES literally. "
    "Do not use outside knowledge. Reply ONLY with one JSON object, no prose and no code fence: "
    "{\"outcome\":\"YES|NO|TOO_EARLY|AMBIGUOUS\",\"quotes\":[exact quotes from snapshots],\"reasoning\":\"<=2 sentences\",\"confidence\":0-1}. "
    "TOO_EARLY = event window not over per snapshots. AMBIGUOUS = snapshots insufficient, contradictory, or rules unclear. "
    "Data-quality rules: (1) a stat line that is all zeros from a single source is MISSING DATA, not a real zero. "
    "(2) For sports statistics (player/team stats, scores) you need at least two independent sources that agree; "
    "if fewer than two independent sources report the needed number, or they disagree, answer AMBIGUOUS."
)
PROMPT_TEMPLATE_HASH = sha(SYSTEM_PROMPT)


def header(market_id, rnd, rules_hash, prompt_template_hash, evidence_bundle_hash):
    return (f"{HEADER_PREFIX} market_id={market_id} round={rnd} rules_hash={rules_hash} "
            f"prompt_template_hash={prompt_template_hash} evidence_bundle_hash={evidence_bundle_hash}")


def evidence_bundle(question, rules, market_end, snapshots):
    """snapshots: [{url, fetched_at, sha256, text}] fetched OUTSIDE the TEE by the relayer."""
    return json.dumps({"question": question, "rules": rules, "market_end": market_end, "source_snapshots": snapshots},
                      ensure_ascii=False, sort_keys=True)


def build_request(model, market_id, rnd, rules, evidence, max_tokens=12000, system=SYSTEM_PROMPT):
    ev_h = sha(evidence)
    user = header(market_id, rnd, sha(rules), sha(system), ev_h) + "\n" + evidence
    body = {"model": model, "temperature": 0, "seed": REQUIRED_SEED, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    return json.dumps(body, ensure_ascii=False).encode()


def parse_outcome(response_raw):
    """Same rule as the contract: 1 choice, assistant, finish_reason=stop, whole content (trimmed, optionally inside
    ONE ```json fence) is a JSON object with an exact-enum "outcome". Anything else -> None."""
    try: v = json.loads(response_raw)
    except Exception: return None
    ch = v.get("choices") if isinstance(v, dict) else None
    if not isinstance(ch, list) or len(ch) != 1: return None
    c = ch[0]
    if c.get("finish_reason") != "stop": return None
    msg = c.get("message") or {}
    if msg.get("role") != "assistant" or not isinstance(msg.get("content"), str): return None
    s = msg["content"].strip()
    if s.startswith("```json"):
        if not s.endswith("```") or len(s) < 10: return None
        s = s[len("```json"):-3].strip()
    if not s.startswith("{"): return None
    try: inner = json.loads(s)
    except Exception: return None
    if not isinstance(inner, dict): return None
    o = inner.get("outcome")
    return o if o in OUTCOMES else None


def parse_vote(response_raw):
    """(outcome or None, decoded inner object or {})"""
    o = parse_outcome(response_raw)
    if o is None: return None, {}
    s = json.loads(response_raw)["choices"][0]["message"]["content"].strip()
    if s.startswith("```json"): s = s[7:-3].strip()
    return o, json.loads(s)
