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


def build_request(model, market_id, rnd, rules, evidence, system=SYSTEM_PROMPT):
    """Exact top-level keys: model, messages, temperature, seed. max_tokens intentionally OMITTED
    (round 5): the contract rejects any extra key so sampling/steering params cannot be smuggled in.
    """
    ev_h = sha(evidence)
    user = header(market_id, rnd, sha(rules), sha(system), ev_h) + "\n" + evidence
    body = {"model": model, "temperature": 0, "seed": REQUIRED_SEED,
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


class _JsonWalkError(Exception):
    pass


def _reject_duplicate_json_keys(raw: str) -> None:
    """Mirror contract ``reject_duplicate_json_keys``.

    Duplicate object keys are rejected. Any backslash in an object key is rejected so
    ``\\u0074emperature`` cannot decode onto an allowlisted key.
    """
    b = raw
    n = len(b)

    def skip_ws(i: int) -> int:
        while i < n and b[i] in " \n\r\t":
            i += 1
        return i

    def parse_string(i: int) -> tuple[str, int]:
        if i >= n or b[i] != '"':
            raise _JsonWalkError("string")
        i += 1
        out: list[str] = []
        while i < n:
            if b[i] == "\\":
                if i + 1 >= n:
                    raise _JsonWalkError("escape")
                out.append("\\")
                out.append(b[i + 1])
                if b[i + 1] == "u":
                    if i + 5 >= n:
                        raise _JsonWalkError("unicode")
                    out.extend(b[i + 2:i + 6])
                    i += 6
                else:
                    i += 2
            elif b[i] == '"':
                return "".join(out), i + 1
            else:
                out.append(b[i])
                i += 1
        raise _JsonWalkError("unterminated string")

    def parse_value(i: int) -> int:
        i = skip_ws(i)
        if i >= n:
            raise _JsonWalkError("eof")
        c = b[i]
        if c == '"':
            return parse_string(i)[1]
        if c == "{":
            return parse_object(i)
        if c == "[":
            i += 1
            i = skip_ws(i)
            if i < n and b[i] == "]":
                return i + 1
            while True:
                i = parse_value(i)
                i = skip_ws(i)
                if i >= n:
                    raise _JsonWalkError("unclosed array")
                if b[i] == ",":
                    i += 1
                    continue
                if b[i] == "]":
                    return i + 1
                raise _JsonWalkError("array")
        if c in "tfn":
            while i < n and b[i].isascii() and b[i].isalpha():
                i += 1
            return i
        if c == "-" or (c.isascii() and c.isdigit()):
            i += 1
            while i < n and ((b[i].isascii() and b[i].isalnum()) or b[i] in "+-.eE"):
                i += 1
            return i
        raise _JsonWalkError("unexpected")

    def parse_object(i: int) -> int:
        if i >= n or b[i] != "{":
            raise _JsonWalkError("object")
        i += 1
        i = skip_ws(i)
        if i < n and b[i] == "}":
            return i + 1
        seen: set[str] = set()
        while True:
            i = skip_ws(i)
            key, i = parse_string(i)
            if "\\" in key:
                raise _JsonWalkError("JSON object key contains backslash")
            if key in seen:
                raise _JsonWalkError(f"duplicate JSON key: {key}")
            seen.add(key)
            i = skip_ws(i)
            if i >= n or b[i] != ":":
                raise _JsonWalkError("colon")
            i = parse_value(i + 1)
            i = skip_ws(i)
            if i >= n:
                raise _JsonWalkError("unclosed object")
            if b[i] == ",":
                i += 1
                continue
            if b[i] == "}":
                return i + 1
            raise _JsonWalkError("object sep")

    try:
        end = parse_value(0)
    except _JsonWalkError as exc:
        msg = str(exc)
        if msg.startswith("duplicate JSON key:") or msg == "JSON object key contains backslash":
            raise ValueError(msg) from None
        raise ValueError("request_raw is not JSON") from None
    if skip_ws(end) != n:
        raise ValueError("request_raw has trailing data")


_ALLOWED_TOP = ("model", "messages", "temperature", "seed")


def _is_zero_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == 0


def check_request(request_raw: str, market_id: int, rnd: int, rules_hash: str, prompt_template_hash: str) -> tuple[str, str]:
    """Mirror contract ``check_request``. Returns ``(model, evidence_bundle_hash)``.

    Error strings match the contract panics. The raw bytes are not rewritten.
    """
    if not isinstance(request_raw, str):
        raise ValueError("request_raw is not JSON")
    _reject_duplicate_json_keys(request_raw)
    try:
        value = json.loads(request_raw)
    except json.JSONDecodeError:
        raise ValueError("request_raw is not JSON") from None
    if not isinstance(value, dict):
        raise ValueError("request_raw is not a JSON object")
    for key in value:
        if key not in _ALLOWED_TOP:
            raise ValueError(f"forbidden request key: {key}")
    for key in _ALLOWED_TOP:
        if key not in value:
            raise ValueError(f"request missing key: {key}")
    if len(value) != len(_ALLOWED_TOP):
        raise ValueError("request has unexpected keys")
    model = value.get("model")
    if not isinstance(model, str):
        raise ValueError("request has no model")
    if not _is_zero_number(value.get("temperature")):
        raise ValueError("request temperature must be 0")
    seed = value.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed != REQUIRED_SEED:
        raise ValueError("request seed must be the fixed seed")
    msgs = value.get("messages")
    if not isinstance(msgs, list):
        raise ValueError("request has no messages")
    if len(msgs) != 2:
        raise ValueError("request must have exactly [system, user] messages")
    for msg in msgs:
        if not isinstance(msg, dict):
            raise ValueError("message must be object")
        for key in msg:
            if key not in ("role", "content"):
                raise ValueError(f"forbidden message key: {key}")
        if set(msg) != {"role", "content"}:
            raise ValueError("message must be exactly role+content")
    if msgs[0].get("role") != "system" or msgs[1].get("role") != "user":
        raise ValueError("request must have exactly [system, user] messages")
    system = msgs[0].get("content")
    user = msgs[1].get("content")
    if not isinstance(system, str):
        raise ValueError("system content must be a string")
    if sha(system) != prompt_template_hash:
        raise ValueError("wrong prompt template")
    if not isinstance(user, str):
        raise ValueError("user content must be a string")
    if "\n" not in user:
        raise ValueError("missing canonical header")
    header_line, evidence = user.split("\n", 1)
    parts = header_line.split(" ")
    if not parts or parts[0] != HEADER_PREFIX:
        raise ValueError("missing canonical header")
    fields: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            raise ValueError("malformed header")
        key, val = part.split("=", 1)
        if key in fields:
            raise ValueError("duplicate header field")
        fields[key] = val
    if len(fields) != 5:
        raise ValueError("malformed header")

    def get(key: str) -> str:
        if key not in fields:
            raise ValueError(f"header missing {key}")
        return fields[key]

    if get("market_id") != str(market_id):
        raise ValueError("wrong market_id")
    if get("round") != str(rnd):
        raise ValueError("wrong round")
    if get("rules_hash") != rules_hash:
        raise ValueError("wrong rules_hash")
    if get("prompt_template_hash") != prompt_template_hash:
        raise ValueError("wrong prompt_template_hash")
    ev = get("evidence_bundle_hash")
    if len(ev) != 64 or ev != ev.lower() or any(c not in "0123456789abcdef" for c in ev):
        raise ValueError("bad evidence_bundle_hash")
    if sha(evidence) != ev:
        raise ValueError("evidence bundle does not hash to header")
    if header_line != header(market_id, rnd, rules_hash, prompt_template_hash, ev):
        raise ValueError("header not canonical")
    return model, ev
