# Claude review packet — round 7 (FINAL code stop)

Generated 2026-11-05 13:51 WAT. Local commit only; **no push / deploy / X / form submit**.

## Status vs round-7 checklist

| # | Item | Status |
|---|---|---|
| 1 | Visa 5648379 case (`docs/VISA_CASE.md`) | Done — **under review — a more precise 9.5% figure may support UMA's YES** |
| 2 | Rename human → **curator (AI)** | Done (Baheet has **not** personally verified) |
| 3 | Selection rule + dropped Ballon d'Or×2 + Apple/NVIDIA cap + exact relayer line | Done in `docs/BENCHMARK_DISPUTED_HAND.md` |
| 4 | WHY (Bloomberg / WSJ / Protocol V2) atop README, SUBMISSION, X, video 0:00–0:15 | Done; primary citations verified via Bloomberg date + secondary reports / WSJ coverage / V2 Oct 5 announce |
| 5 | evidence_uri commit-SHA helper; no fabricated SHA; not /master/ | Done: `scripts/evidence_uri.py`; `real_panel` URI = `PENDING_COMMIT_SHA://…` |
| 6 | Reject backslash in object keys + `\u0074emperature` test | Done — **56** contract tests pass |
| 7 | X plain text; remove NEAR/Illia endorsement line | Done |
| 8 | SUBMISSION form field map; nearAddress TODO | Done — never submit |

**Coding stops here.** Remaining work is human-only.

## Visa (5648379)

See full `docs/VISA_CASE.md` (embedded below). Summary: Key Business Drivers **Payments Volume Q4 = 9%** and Sept 30 narrative **9%**; June-30 lag **10%** is a different period; processed transactions **10%** is a different metric. Panel/curator (AI) **NO**; UMA **YES**. OPD more-precise decimal not fetched. **Status**: under review — a more precise 9.5% figure may support UMA's YES.

## Tests

`cargo test` → **56 passed, 0 failed** (includes `escaped_json_object_key_rejected`).
`python scripts/offline_demo.py` → ALL OFFLINE CHECKS PASSED (no API key).

Test names (56):
- `real_signatures_from_all_three_models_recover_to_their_tee_keys`
- `real_three_model_panel_settles_market`
- `real_signer_registered_for_other_model_rejected`
- `real_tampered_response_rejected`
- `real_signature_cannot_be_replayed_on_another_market`
- `wrong_market_rejected`
- `wrong_rules_rejected`
- `wrong_template_rejected`
- `mismatched_evidence_in_round_rejected`
- `evidence_edited_after_hashing_rejected`
- `wrong_seed_rejected`
- `nonzero_temperature_rejected`
- `deepseek_key_signing_as_qwen_rejected`
- `signer_model_stored`
- `unregistered_key_rejected`
- `removed_signer_rejected`
- `same_signature_twice_rejected`
- `second_distinct_vote_by_same_signer_rejected`
- `too_early_votes_cannot_be_replayed_next_round`
- `too_early_vote_itself_cannot_be_replayed`
- `round_one_settles_after_too_early`
- `prose_wrapped_json_rejected_on_chain`
- `parse_outcome_is_structured_not_substring`
- `real_responses_parse_identically_to_python`
- `only_owner_registers`
- `no_verdict_before_deadline`
- `split_panel_escalates`
- `unanimous_panel_finalize_claim`
- `cannot_finalize_during_window`
- `dispute_with_bond_overturned_by_owner`
- `dispute_needs_bond`
- `loser_cannot_claim`
- `vote_before_commit_rejected`
- `vote_with_uncommitted_evidence_rejected`
- `non_relayer_commit_rejected`
- `registered_relayer_can_commit`
- `owner_can_replace_evidence_while_votes_empty`
- `non_owner_relayer_cannot_replace_evidence`
- `owner_cannot_replace_evidence_after_votes`
- `commit_evidence_before_deadline_rejected`
- `too_early_clears_committed_evidence`
- `forbidden_logit_bias_rejected`
- `forbidden_guided_choice_rejected`
- `forbidden_guided_regex_rejected`
- `forbidden_response_format_rejected`
- `forbidden_structured_outputs_rejected`
- `forbidden_tools_rejected`
- `forbidden_tool_choice_rejected`
- `forbidden_stop_rejected`
- `forbidden_n_rejected`
- `forbidden_top_p_rejected`
- `forbidden_message_name_rejected`
- `non_relayer_submit_verdict_rejected`
- `registered_relayer_can_submit_verdict`
- `duplicate_json_key_rejected`
- `escaped_json_object_key_rejected`
## reject_duplicate_json_keys (backslash ban)

```rust
/// Reject duplicate object keys anywhere in `request_raw`.
/// `serde_json::Value` keeps the last duplicate by default, which would let an attacker
/// smuggle a second copy of an allowed key. We count keys via a raw structural walk.
fn reject_duplicate_json_keys(raw: &str) {
    fn skip_ws(b: &[u8], mut i: usize) -> usize {
        while i < b.len() && matches!(b[i], b' ' | b'\n' | b'\r' | b'\t') { i += 1; }
        i
    }
    fn parse_string(b: &[u8], mut i: usize) -> Result<(String, usize), &'static str> {
        if i >= b.len() || b[i] != b'"' { return Err("string"); }
        i += 1;
        let mut out = Vec::new();
        while i < b.len() {
            match b[i] {
                b'\\' => {
                    if i + 1 >= b.len() { return Err("escape"); }
                    out.push(b[i]); out.push(b[i + 1]);
                    // handle \uXXXX roughly by copying 4 hex if present
                    if b[i + 1] == b'u' {
                        if i + 5 >= b.len() { return Err("unicode"); }
                        out.extend_from_slice(&b[i + 2..i + 6]);
                        i += 6;
                    } else {
                        i += 2;
                    }
                }
                b'"' => {
                    i += 1;
                    return Ok((String::from_utf8_lossy(&out).into_owned(), i));
                }
                c => { out.push(c); i += 1; }
            }
        }
        Err("unterminated string")
    }
    fn parse_value(b: &[u8], mut i: usize) -> Result<usize, String> {
        i = skip_ws(b, i);
        if i >= b.len() { return Err("eof".into()); }
        match b[i] {
            b'"' => Ok(parse_string(b, i).map_err(|e| e.to_string())?.1),
            b'{' => parse_object(b, i),
            b'[' => {
                i += 1;
                i = skip_ws(b, i);
                if i < b.len() && b[i] == b']' { return Ok(i + 1); }
                loop {
                    i = parse_value(b, i)?;
                    i = skip_ws(b, i);
                    if i >= b.len() { return Err("unclosed array".into()); }
                    match b[i] {
                        b',' => { i += 1; continue; }
                        b']' => return Ok(i + 1),
                        _ => return Err("array".into()),
                    }
                }
            }
            b't' | b'f' | b'n' => {
                // true/false/null
                while i < b.len() && b[i].is_ascii_alphabetic() { i += 1; }
                Ok(i)
            }
            b'-' | b'0'..=b'9' => {
                i += 1;
                while i < b.len() && (b[i].is_ascii_alphanumeric() || matches!(b[i], b'+' | b'-' | b'.' | b'e' | b'E')) { i += 1; }
                Ok(i)
            }
            _ => Err(format!("unexpected byte {}", b[i])),
        }
    }
    fn parse_object(b: &[u8], mut i: usize) -> Result<usize, String> {
        if i >= b.len() || b[i] != b'{' { return Err("object".into()); }
        i += 1;
        i = skip_ws(b, i);
        if i < b.len() && b[i] == b'}' { return Ok(i + 1); }
        let mut seen = std::collections::BTreeSet::<String>::new();
        loop {
            i = skip_ws(b, i);
            let (key, ni) = parse_string(b, i).map_err(|e| e.to_string())?;
            // Reject any backslash in object keys. Escapes like \u0074emperature decode to
            // "temperature" in serde_json while our raw key-set would treat them as distinct,
            // letting an attacker smuggle a second copy of an allowlisted key.
            if key.as_bytes().contains(&b'\\') {
                return Err("JSON object key contains backslash".into());
            }
            if !seen.insert(key.clone()) {
                return Err(format!("duplicate JSON key: {key}"));
            }
            i = skip_ws(b, ni);
            if i >= b.len() || b[i] != b':' { return Err("colon".into()); }
            i = parse_value(b, i + 1)?;
            i = skip_ws(b, i);
            if i >= b.len() { return Err("unclosed object".into()); }
            match b[i] {
                b',' => { i += 1; continue; }
                b'}' => return Ok(i + 1),
                _ => return Err("object sep".into()),
            }
        }
    }
    let b = raw.as_bytes();
    match parse_value(b, 0) {
        Ok(end) => {
            let end = skip_ws(b, end);
            if end != b.len() {
                env::panic_str("request_raw has trailing data");
            }
        }
        Err(e) => {
            if e.starts_with("duplicate JSON key:") || e == "JSON object key contains backslash" {
                env::panic_str(&e);
            }
            env::panic_str("request_raw is not JSON");
        }
    }
}

/// Validate the raw chat-completions request body against the market. Returns (model, evidence_bundle_hash).
/// Exact key allowlist (round 5): top-level keys MUST be exactly {model, messages, temperature, seed}.
/// Each message MUST be exactly {role, content}. User content MUST be canonical header + "\n" + evidence.
/// Extra keys (logit_bias, tools, response_format, …) are rejected so an attacker cannot steer a
/// committed evidence+header into a forced YES via sampling / guided decoding params.
/// Panics with a specific reason on any mismatch.
pub fn check_request(request_raw: &str, market_id: u32, m: &Market) -> (String, String) {
    reject_duplicate_json_keys(request_raw);
    let v: serde_json::Value = serde_json::from_str(request_raw).unwrap_or_else(|_| env::panic_str("request_raw is not JSON"));
    let obj = v.as_object().unwrap_or_else(|| env::panic_str("request_raw is not a JSON object"));
    const ALLOWED_TOP: &[&str] = &["model", "messages", "temperature", "seed"];
    for k in obj.keys() {
        if !ALLOWED_TOP.contains(&k.as_str()) {
            env::panic_str(&format!("forbidden request key: {k}"));
        }
    }
    for k in ALLOWED_TOP {
        if !obj.contains_key(*k) {
            env::panic_str(&format!("request missing key: {k}"));
        }
    }
    require!(obj.len() == ALLOWED_TOP.len(), "request has unexpected keys");
    let model = obj.get("model").and_then(|x| x.as_str()).unwrap_or_else(|| env::panic_str("request has no model")).to_string();
    require!(obj.get("temperature").and_then(|x| x.as_f64()) == Some(0.0), "request temperature must be 0");
    require!(obj.get("seed").and_then(|x| x.as_u64()) == Some(REQUIRED_SEED), "request seed must be the fixed seed");
    let msgs = obj.get("messages").and_then(|x| x.as_array()).unwrap_or_else(|| env::panic_str("request has no messages"));
    require!(msgs.len() == 2, "request must have exactly [system, user] messages");
    for (i, msg) in msgs.iter().enumerate() {
        let mo = msg.as_object().unwrap_or_else(|| env::panic_str("message must be object"));
        for k in mo.keys() {
            if k != "role" && k != "content" {
                env::panic_str(&format!("forbidden message key: {k}"));
            }
        }
        require!(mo.contains_key("role") && mo.contains_key("content") && mo.len() == 2, "message must be exactly role+content");
        let _ = i;
    }
    require!(msgs[0]["role"] == "system" && msgs[1]["role"] == "user", "request must have exactly [system, user] messages");
    let system = msgs[0]["content"].as_str().unwrap_or_else(|| env::panic_str("system content must be a string"));
    require!(sha_hex(system.as_bytes()) == m.prompt_template_hash, "wrong prompt template");
    let user = msgs[1]["content"].as_str().unwrap_or_else(|| env::panic_str("user content must be a string"));
    let (header, evidence) = user.split_once('\n').unwrap_or_else(|| env::panic_str("missing canonical header"));
    let mut f = std::collections::BTreeMap::new();
    let mut parts = header.split(' ');
    require!(parts.next() == Some(HEADER_PREFIX), "missing canonical header");
    for p in parts {
        let (k, val) = p.split_once('=').unwrap_or_else(|| env::panic_str("malformed header"));
        require!(f.insert(k, val).is_none(), "duplicate header field");
    }
    require!(f.len() == 5, "malformed header");
    let get = |k: &str| *f.get(k).unwrap_or_else(|| env::panic_str(&format!("header missing {k}")));
    require!(get("market_id") == market_id.to_string(), "wrong market_id");
    require!(get("round") == m.round.to_string(), "wrong round");
    require!(get("rules_hash") == m.rules_hash, "wrong rules_hash");
    require!(get("prompt_template_hash") == m.prompt_template_hash, "wrong prompt_template_hash");
    let ev = get("evidence_bundle_hash");
    require!(ev.len() == 64 && hex::decode(ev).is_ok() && ev == ev.to_lowercase(), "bad evidence_bundle_hash");
    require!(sha_hex(evidence.as_bytes()) == ev, "evidence bundle does not hash to header");
    // Byte-exact canonical form (no extra spaces / reordered fields).
    require!(header == canonical_header(market_id, m.round, &m.rules_hash, &m.prompt_template_hash, ev), "header not canonical");
    (model, ev.to_string())
}

/// Read the outcome from the STRUCTURED assistant message, never by substring search:
/// exactly one choice, role=assistant, finish_reason=stop, and the whole content (whitespace trimmed,
/// optionally wrapped in ONE ```json fence) must parse as a JSON object whose "outcome" is exactly
/// one of YES | NO | TOO_EARLY | AMBIGUOUS. Prose around the JSON => None.
pub fn parse_outcome(response_raw: &str) -> Option<Outcome> {
    let v: serde_json::Value = serde_json::from_str(response_raw).ok()?;
    let choices = v["choices"].as_array()?;
    if choices.len() != 1 { return None; }
    let c = &choices[0];
    if c["finish_reason"].as_str()? != "stop" { return None; }
    let msg = &c["message"];
    if msg["role"].as_str()? != "assistant" { return None; }
    let mut content = msg["content"].as_str()?.trim();
    if let Some(rest) = content.strip_prefix("```json") {
        content = rest.strip_suffix("```")?.trim();
    }
    if !content.starts_with('{') { return None; }
    let inner: serde_json::Value = serde_json::from_str(content).ok()?;
    match inner.as_object()?.get("outcome")?.as_str()? {
        "YES" => Some(Outcome::Yes), "NO" => Some(Outcome::No),
        "TOO_EARLY" => Some(Outcome::TooEarly), "AMBIGUOUS" => Some(Outcome::Ambiguous), _ => None,
    }
}

#[near]
impl Verdict {
    #[init]
    pub fn new(owner: AccountId) -> Self {
        Self { owner, signers: IterableMap::new(Key::Signers), attestations: LookupMap::new(Key::Attestations),
               relayers: LookupSet::new(Key::Relayers),
               markets: Vector::new(Key::Markets), stakes: LookupMap::new(Key::Stakes), used_signed: LookupSet::new(Key::UsedSigned) }
    }

    fn only_owner(&self) { require!(env::predecessor_account_id() == self.owner, "owner only"); }

    /// Owner registers a signer address FOR ONE MODEL after verifying its TDX+GPU attestation off-chain
    /// (scripts/hw_verify.py: the attestation entry with this signing_address must have model_name == model).
    pub fn register_signer(&mut self, address: String, model: String, attestation_report_sha256: String) {
        self.only_owner();
        require!(!model.is_empty(), "model required");
        let h = hex32(&attestation_report_sha256, "report hash");
        let a = hex20(&address);
        self.signers.insert(a.clone(), model);
        self.attestations.insert(a, h);
    }
    pub fn get_signer_attestation(&self, address: String) -> Option<String> { self.attestations.get(&hex20(&address)).cloned() }
    pub fn get_signer_model(&self, address: String) -> Option<String> { self.signers.get(&hex20(&address)).cloned() }
    pub fn remove_signer(&mut self, address: String) { self.only_owner(); self.signers.remove(&hex20(&address)); }
    pub fn get_signers(&self) -> Vec<(String, String)> { self.signers.iter().map(|(a, m)| (a.clone(), m.clone())).collect() }

    /// Owner registers an account that may call `commit_evidence` (owner can always commit without registering).
    pub fn register_relayer(&mut self, account: AccountId) {
        self.only_owner();
        self.relayers.insert(account);
    }
    pub fn remove_relayer(&mut self, account: AccountId) {
        self.only_owner();
        self.relayers.remove(&account);
    }
    pub fn is_relayer(&self, account: AccountId) -> bool {
        account == self.owner || self.relayers.contains(&account)
    }
    fn only_relayer(&self) {
        require!(self.is_relayer(env::predecessor_account_id()), "relayer or owner only");
    }

    /// Lock the evidence bundle hash (+ publish URI) for the CURRENT round before any vote.
    /// Permissioned so a random NEAR AI key holder cannot race-submit fabricated evidence.
    /// Requires `block_timestamp >= deadline_ns` (betting closed). Owner may replace the commit while
    /// votes are still empty; once any vote is cast the commit is frozen. The full evidence bundle
    /// MUST be published at `evidence_uri` with the same hash so anyone can recompute.
    pub fn commit_evidence(&mut self, market_id: u32, round: u32, evidence_bundle_hash: String, evidence_uri: String) {
        self.only_relayer();
        require!(!evidence_uri.is_empty(), "evidence_uri required");
        let h = hex32(&evidence_bundle_hash, "evidence_bundle_hash");
        let m = self.markets.get_mut(market_id).expect("no market");
        require!(m.status == Status::Open, "market not open");
        require!(env::block_timestamp() >= m.deadline_ns, "market not ended");
        require!(round == m.round, "wrong round");
        require!(m.votes.is_empty(), "votes already cast this round");
        if m.round_evidence_hash.is_some() {
            // Replacement is owner-only (defense in depth vs a compromised relayer flipping evidence).
            require!(env::predecessor_account_id() == self.owner, "owner only to replace evidence");
        }
        m.round_evidence_hash = Some(h);
        m.round_evidence_uri = Some(evidence_uri);
    }
    pub fn get_committed_evidence(&self, market_id: u32) -> Option<String> {
        self.markets.get(market_id).and_then(|m| m.round_evidence_hash.clone())
    }
    pub fn get_committed_evidence_uri(&self, market_id: u32) -> Option<String> {
        self.markets.get(market_id).and_then(|m| m.round_evidence_uri.clone())
    }

    /// Commits market_id (the returned index), sha256(rules) and the prompt template hash.
    /// Auto-verdict requires ALL resolvers to agree (threshold == resolver count).
    #[allow(clippy::too_many_arguments)]
    pub fn create_market(&mut self, question: String, rules: String, sources: Vec<String>, prompt_template_hash: String,
                         resolvers: Vec<String>, deadline_ns: u64, dispute_window_ns: u64, dispute_bond: NearToken) -> u32 {
        let mut rs: Vec<String> = resolvers.iter().map(|a| hex20(a)).collect();
        rs.sort(); rs.dedup();
        require!(!rs.is_empty(), "bad resolver set");
        for r in &rs { require!(self.signers.contains_key(r), "resolver not registered"); }
        require!(deadline_ns > env::block_timestamp(), "deadline in the past");
        require!(!sources.is_empty(), "lock at least one source");
        let rules_hash = sha_hex(rules.as_bytes());
        let pth = hex32(&prompt_template_hash, "prompt_template_hash");
        let threshold = rs.len() as u32;
        self.markets.push(Market { question, rules, rules_hash, prompt_template_hash: pth, sources, resolvers: rs, threshold,
            deadline_ns, dispute_window_ns, dispute_bond, yes_pool: NearToken::from_yoctonear(0), no_pool: NearToken::from_yoctonear(0),
            round: 0, round_evidence_hash: None, round_evidence_uri: None, votes: vec![], status: Status::Open });
        self.markets.len() - 1
    }

    #[payable]
    pub fn deposit(&mut self, market_id: u32, outcome: Outcome) {
        require!(matches!(outcome, Outcome::Yes | Outcome::No), "bet YES or NO");
        let amt = env::attached_deposit();
        require!(amt.as_yoctonear() > 0, "attach NEAR");
        let m = self.markets.get_mut(market_id).expect("no market");
        require!(env::block_timestamp() < m.deadline_ns && m.status == Status::Open, "betting closed");
        let key = (market_id, env::predecessor_account_id());
        let (y, n) = self.stakes.get(&key).copied().unwrap_or((NearToken::from_yoctonear(0), NearToken::from_yoctonear(0)));
        match outcome {
            Outcome::Yes => { m.yes_pool = m.yes_pool.saturating_add(amt); self.stakes.insert(key, (y.saturating_add(amt), n)); }
            _ => { m.no_pool = m.no_pool.saturating_add(amt); self.stakes.insert(key, (y, n.saturating_add(amt))); }
        }
    }

    /// Relayer/owner submits a signed resolver output. Restricted to registered relayers (owner always
    /// counts) as defense in depth: the same party already chooses evidence via `commit_evidence`, so
    /// this adds no new trust surface, but blocks a random account from racing a steered request that
    /// somehow obtained three attested signatures. Evidence for this round MUST already be committed.
    /// The raw request bytes are hashed ON-CHAIN (not stored) and must contain the canonical header
    /// binding market_id / round / rules_hash / prompt_template_hash / evidence_bundle_hash
    /// (== the committed hash), with an exact request-key allowlist (see `check_request`).
    /// Signed text = "<signer's model>:<sha256(request_raw)>:<sha256(response_raw)>".
    pub fn submit_verdict(&mut self, market_id: u32, request_raw: String, response_raw: String, signature: String) -> Outcome {
        self.only_relayer();
        let m = self.markets.get(market_id).expect("no market");
        require!(env::block_timestamp() >= m.deadline_ns, "market not ended");
        require!(m.status == Status::Open, "not accepting verdicts");
        let (req_model, evidence) = check_request(&request_raw, market_id, m);
        let req_h = sha_hex(request_raw.as_bytes());
        let resp_h = sha_hex(response_raw.as_bytes());
        // Build the text with the model named in the request, recover, then require that the
        // recovered key is registered for exactly that model (signer -> model).
        let text = format!("{req_model}:{req_h}:{resp_h}");
        let signer = recover_signer(&text, &signature).unwrap_or_else(|| env::panic_str("bad signature"));
        let reg_model = self.signers.get(&signer).cloned().unwrap_or_else(|| env::panic_str("signer not attested/registered"));
        require!(reg_model == req_model, "signer is registered for a different model");
        require!(m.resolvers.contains(&signer), "signer not in market resolver set");
        let text_h = env::sha256(text.as_bytes());
        require!(!self.used_signed.contains(&text_h), "signed response already used");
        require!(!m.votes.iter().any(|v| v.signer == signer), "signer already voted");
        let committed = m.round_evidence_hash.as_ref().unwrap_or_else(|| env::panic_str("evidence not committed for this round"));
        require!(*committed == evidence, "evidence bundle differs from committed hash");
        let outcome = parse_outcome(&response_raw).unwrap_or_else(|| env::panic_str("no valid outcome in response"));
        self.used_signed.insert(text_h);
        let m = self.markets.get_mut(market_id).unwrap();
        m.votes.push(Vote { signer, model: req_model, outcome, request_sha256: req_h, response_sha256: resp_h });
        if m.votes.len() as u32 == m.threshold {
            let all_same = m.votes.iter().all(|v| v.outcome == outcome);
            m.status = match (all_same, outcome) {
                (true, Outcome::Yes | Outcome::No) => Status::Proposed { outcome, at_ns: env::block_timestamp() },
                (true, Outcome::TooEarly) => { // re-run panel later in a NEW round; old signatures can't be replayed
                    m.votes.clear(); m.round += 1; m.round_evidence_hash = None; m.round_evidence_uri = None; Status::Open }
```

## evidence_uri helper

```python
#!/usr/bin/env python3
"""Commit-SHA permalink helper for evidence_uri (Claude round 7).

Do NOT use /master/ or /main/ raw URLs — they move. After a human-approved push
that includes the evidence file, pass the resulting commit SHA.

Existing origin/master tip f96954b does NOT contain fixtures/evidence/* —
never point evidence_uri at a SHA that does not contain the file.
"""
from __future__ import annotations
import argparse, os, subprocess, sys
from pathlib import Path

REPO = "Baheet18/verdict"
ROOT = Path(__file__).resolve().parent.parent


def pending_uri(relpath: str) -> str:
    relpath = relpath.lstrip("/")
    return f"PENDING_COMMIT_SHA://{relpath}"


def github_raw_permalink(relpath: str, commit_sha: str | None = None, repo: str = REPO) -> str:
    relpath = relpath.lstrip("/")
    if not commit_sha:
        return pending_uri(relpath)
    if len(commit_sha) < 7 or any(c not in "0123456789abcdef" for c in commit_sha.lower()):
        raise ValueError(f"refuse non-hex commit sha: {commit_sha!r}")
    return f"https://raw.githubusercontent.com/{repo}/{commit_sha}/{relpath}"


def resolve_sha(explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip().lower()
    env = os.environ.get("VERDICT_EVIDENCE_COMMIT_SHA", "").strip()
    if env:
        return env.lower()
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("relpath", help="repo-relative path, e.g. fixtures/evidence/<sha>.json")
    ap.add_argument("--sha", default=None, help="commit SHA after human-approved push that includes the file")
    ap.add_argument("--check-local", action="store_true", help="fail if path missing locally")
    args = ap.parse_args(argv)
    if args.check_local and not (ROOT / args.relpath).is_file():
        print(f"missing local file: {args.relpath}", file=sys.stderr)
        return 2
    sha = resolve_sha(args.sha)
    print(github_raw_permalink(args.relpath, sha))
    if not sha:
        print(
            "# pending: after human-approved push that includes this file, re-run with --sha <commit>",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

```

## Full docs

### docs/VISA_CASE.md

# Visa 5648379 — under review — a more precise 9.5% figure may support UMA's YES

**Market**: Polymarket `5648379` — *Visa (V) Q4 payments volume growth (constant dollars) 9.5% to 10.5%?*  
**Sibling**: `5648378` — same metric, band *8.5% to 9.5%* (UMA resolved **NO**).  
**Panel (hand-curated answer-bearing run)**: unanimous **NO**.  
**UMA**: **YES** on `5648379` and **NO** on `5648378` → UMA’s effective figure is in **9.5–10.5**, not 8.5–9.5.  
**Curator (AI) read of the same bundle**: **NO** (9% is below 9.5–10.5).  
**Baheet has not personally verified this row.**

This note quotes the market’s exact rules and the named release lines. **Status**: under review — a more precise 9.5% figure may support UMA's YES.

## Exact market rules (from Polymarket description / hand bundle)

> This market will resolve according to Visa's payments volume growth (constant dollars) (year-over-year) for the upcoming fourth fiscal quarter, as reported in its official company earnings materials.
>
> The specified metric will be considered as reported in the company's official earnings materials. Subsequent revisions will not be considered.
>
> […]
>
> The resolution source for this market is the specified company's official earnings materials, including press releases, investor presentations, and regulatory filings. If the specified metric is not reported in these materials, recordings or transcripts of the company's earnings webcast may also be used.
>
> Note: This market will resolve based on the most numerically precise version of the specified metric reported in the company's official earnings materials. Only the specified metric will be considered; alternate versions that differ in definition or scope from the specified metric will not be considered.

Bracket mechanics (also in the rules): value exactly between two brackets → higher bracket; reported as a range → midpoint; metric missing / materials not released by Nov 30, 2026 11:59 PM ET → lowest bracket.

## Exact source lines used in the hand bundle

**Primary (SEC Exhibit / earnings release), Oct 27, 2026** —  
https://www.sec.gov/Archives/edgar/data/1403161/000140316126000126/q42026earningsrelease.htm

Quoted lines committed in `fixtures/evidence_hand/7f4d2d3172e91c520650d58e752443de5764f5f690d38c832c672bd5f573dcdc.json`:

1. **Key Business Drivers table (constant dollars), Q4 2026: Payments Volume = 9%**  
   (integer percent; no extra decimal for Payments Volume appears in this Exhibit 99.1 text).
2. **Narrative for the Q4 volume period**:  
   “Payments volume for the three months ended **September 30, 2026** increased **9%** over the prior year on a constant-dollar basis.”
3. **Separate June-30 lag line (service-revenue recognition period, not the Q4 volume period)**:  
   “Payments volume for the three months ended **June 30, 2026**, on which fiscal fourth quarter service revenue is recognized, increased **10%** over the prior year on a constant-dollar basis.”

**Secondary (Investing.com earnings-call transcript summary)** — same 9% for the quarter ended September 30, 2026.

**IR page snapshot in the bundle** also lists **Processed transactions Q4 2026: 10%** — a **different metric** than payments volume growth (constant dollars). Under the rules’ “only the specified metric” clause, processed transactions is out of scope.

**Operational Performance Data (OPD)**: Visa’s IR event page links an OPD PDF for Q4/FY 2026. Automated fetch returned HTTP 403 / captcha during this case write-up, so **no more-precise decimal for Payments Volume from OPD is claimed here**. If a future human fetch of OPD shows a more precise Payments Volume figure for the Q4 (Sept 30) period, that would be the “most numerically precise” candidate under the rules — and should be appended as a new evidence snapshot rather than assumed.

## How the two readings map

| Reading | Figure used | Fits “payments volume … fourth fiscal quarter” (Sept 30 period)? | Band 9.5–10.5 |
|---|---|---|---|
| Panel + curator (AI) | Key Business Drivers **Payments Volume 9%** + Sept 30 narrative **9%** | Yes | **NO** (below band) |
| Implied by UMA YES on 5648379 + NO on 5648378 | Something ≈ **10%** | June-30 lag **10%** is explicitly a different period; processed transactions **10%** is a different metric | **YES** |

## Classification

Panel votes and curator (AI) used the Key Business Drivers **Payments Volume 9%** / Sept 30 narrative **9%** lines → **NO** on 9.5–10.5. UMA resolved **YES**. OPD / a more precise Payments Volume decimal was not successfully fetched for this write-up (HTTP 403 / captcha).

**Status**: under review — a more precise 9.5% figure may support UMA's YES.

Evidence: hand bundle + this note.

## Artifacts

- Hand evidence: `fixtures/evidence_hand/7f4d2d3172e91c520650d58e752443de5764f5f690d38c832c672bd5f573dcdc.json`
- Hand summary: `docs/BENCHMARK_DISPUTED_HAND.md`
- Sibling UMA NO on 8.5–9.5: Polymarket `5648378` (context only; not in the hand-8 settle set)


### docs/BENCHMARK_DISPUTED_HAND.md

# Hand-curated disputed markets (round 6–7) — answer-bearing sources

**Method**: curator-(AI)-extracted official result page or Wikipedia + 2 reputable reports. Each snapshot has `answer_present: true` checked **before** the panel ran. Archive.org links recorded only when the archive check succeeded; otherwise marked unavailable (no fabricated archive URLs).

**Curator column**: **curator (AI)** — Grok Bot’s pre-panel check of the committed text. **Baheet has not personally verified each row.**

**Column name**: `answer-bearing sources`

**Scripts**: `scripts/hand_curate_disputed.py` (bundles) + `scripts/run_hand_disputed.py` (panel).

**Summary**: settled 8/8 · agree UMA 7 · disagree UMA 1 · escalated 0 · invented answers 0 · thin-same-ids escalated 8/8 · cost $0.0195

Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.

## Selection rule (hand-8)

From the 34 UMA-disputed Polymarket markets settled after the panel’s 2026-10-17 cutoff window:

1. Prefer markets where an **official result page or Wikipedia** plus **two reputable reports** could be hand-extracted as plain text (not a JS shell) inside a half-day curation budget.
2. Require `answer_present: true` on every snapshot **before** any panel call.
3. Prefer a single named resolution metric (final score, seat winner, Key Business Drivers line) over intraday rankings or long-horizon award odds.
4. Do **not** retune selection after seeing panel votes; Visa 5648379 was kept in the set.

## Dropped from the earlier JS-shell curated attempt (not in hand-8)

| ID | Question | Why dropped |
|---|---|---|
| 608558 | Will Raphinha win the 2026 Ballon d'Or? | Ceremony / longshot award market; official winners materials were still thin or JS-rendered during the time cap; near-duplicate of other Ballon d'Or longshots — not used to pad agree rate. |
| 608564 | Will Cristiano Ronaldo win the 2026 Ballon d'Or? | Same as above (Ballon d'Or ×2). |
| 5180406 | Will Apple be the second-largest company by market cap on …? | Intraday market-cap **rank** needs a precise as-of timestamp from a named ranking table; available pages were JS shells without a stable extractable close-of-day ranking during the time cap. |
| 5166966 | Will NVIDIA be the third-largest company by market cap on …? | Same market-cap rank problem. |
| 5166968 | Will Apple be the third-largest company by market cap on …? | Same market-cap rank problem (also dropped with the Apple/NVIDIA rank cluster). |

## Results

| ID | Question | Answer-bearing sources | Archives OK | Thin panel | Hand panel | UMA | curator (AI) |
|---|---|---|---|---|---|---|---|
| 1235552 | Will the Cleveland Guardians win the 2026 World Series? | 3 | 2 | ESCALATE | **NO** | NO | NO |
| 5969230 | Fukuoka SoftBank Hawks vs. Yokohama BayStars | 3 | 0 | ESCALATE | **YES** | YES | YES |
| 5285913 | Blues vs. Red Wings: O/U 4.5 | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 4588336 | 3Q Spread: Broncos (-3.5) | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 1281165 | Will the Democratic Party win the CA-34 House seat? | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 1284815 | Will the Republican Party win the TX-02 House seat? | 3 | 3 | ESCALATE | **YES** | YES | YES |
| 5648379 | Visa (V) Q4 payments volume growth (constant dollars) 9.5%–10.5%? | 3 | 1 | ESCALATE | **NO** | YES | NO |
| 5936743 | Spread: FC Andorra (-2.5) | 3 | 1 | ESCALATE | **NO** | NO | NO |

## Disagree note

**5648379 Visa Q4 payments volume growth 9.5%–10.5%**: panel unanimous **NO**; curator (AI) **NO**; UMA **YES**. Full source-line analysis: **`docs/VISA_CASE.md`**. **Status**: under review — a more precise 9.5% figure may support UMA's YES.

## Contrast

Same 8 market IDs with thin rules-only / non-answer-bearing fetches: **8/8 escalated**. With answer-bearing text: **8/8 settled** (7 agree UMA).

Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.


### docs/DEMO_VIDEO.md

# Verdict demo video script (~2.5 minutes)

Record locally. Do **not** show API keys, `.env`, or wallet seed phrases.
Offline mode first (judge-reproducible), then optional live attest.

| Time | Shot | On-screen / voiceover |
|---|---|---|
| 0:00–0:15 | Title: “Verdict” + GitHub URL; subtitles with WHY citations | “Bloomberg May 26 2026: about nine wallets cast roughly half of UMA dispute votes on Polymarket. WSJ May 2026: about one in five disputes had a voter with a stake. Protocol V2 on Oct 5 2026 lets each market choose its oracle. Verdict is a hardware-attested option for that slot — not a claim anyone is corrupt.” |
| 0:15–0:35 | Terminal: clone + `python scripts/offline_demo.py` (no API key) | “A judge with no API key can reproduce this offline: contract tests, cached EIP-191 signatures, and the scoreboard rebuilt from fixtures.” |
| 0:35–0:55 | `cargo test` scrolling PASS (56 tests) | “On-chain checks bind every vote to market, round, rules, prompt template, and a committed evidence hash — reject steered request keys, duplicate JSON keys, and backslash-escaped object keys.” |
| 0:55–1:20 | Open `scoreboard/index.html` | “Clear markets: auto-settled at about $0.002, 107 of 107 agreed with UMA, hardware-attested. Unclear markets escalate — zero invented answers. TOO_EARLY is first-class.” |
| 1:20–1:40 | Hand-curated table on scoreboard | “With curator-(AI)-checked answer-bearing sources on 8 formerly disputed markets: 8 settled, 7 agree UMA, 1 disagree, 0 invented. Same IDs with thin evidence all escalated. Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.” |
| 1:40–2:05 | `python scripts/attest_demo.py Qwen/Qwen3.8-27B` PASS then tamper FAIL | “Hardware + host stack verified: Intel TDX + NVIDIA NRAS, allowlisted digests. Tampering the compose fails closed.” |
| 2:05–2:25 | `deploy/TESTNET.md` + unsigned sports `deploy_prepare.py` (Saints vs Giants) | “Wasm is built. Demo market is a real post-cutoff NFL game. Testnet deploy is signed in the builder’s own wallet — keys never enter the repo. evidence_uri is a commit-SHA permalink after a human-approved push — never /master/.” |
| 2:25–2:40 | Limits slide | “Model container not measured; evidence fetched outside the TEE; owner/relayer still trusted for registration and evidence commit. ‘Unsteerable’ means request-parameter steering is blocked, not that the relayer is trustless.” |
| 2:40–2:50 | End card: repo + “NEAR Hackathon · submit by Nov 14 23:59 UTC” | “Verdict: certified resolutions you can re-hash.” |

## B-roll checklist
- `scoreboard/index.html` (filters + answer-bearing sources column; curator (AI) label)
- `docs/VISA_CASE.md` (under review — a more precise 9.5% figure may support UMA's YES)
- `fixtures/evidence_hand/MANIFEST.json`
- `scripts/expected_measurements.json`
- Contract snippet: `check_request` allowlist + `reject_duplicate_json_keys` + `commit_evidence`
- `scripts/evidence_uri.py` PENDING → commit-SHA flow

## Numbers (must match cache)
- Sports post-cutoff: 124 markets, 107 settled, 107 agree UMA, 0 disagree, ~$0.0022/market (`docs/BENCHMARK.md`)
- Thin disputed: 34 escalate (`docs/BENCHMARK_DISPUTED.md`)
- Hand disputed: 8 settled / 7 agree / 1 disagree / 0 invent (`docs/BENCHMARK_DISPUTED_HAND.md`)
- Bitquery TOO_EARLY: 243/470 (52%) — https://bitquery.io/investigations/who-decides-polymarket-disputes
- Do **not** claim the rule linter predicts disputes.
- Curator column is **curator (AI)** unless Baheet personally verifies.

## Do not film
- `.env`, API key dashboards with key visible, wallet recovery phrases, private RPC tokens.


### docs/X_POST_DRAFT.md

# X post draft (HUMAN posts — do not auto-post)

Plain text only (no markdown bold). Suggested text (edit freely):

---

Why: Bloomberg (May 26, 2026) reported ~9 wallets cast about half of UMA dispute votes on Polymarket; WSJ (May 2026) reported ~1 in 5 disputes had a voter with a stake. Polymarket Protocol V2 (Oct 5, 2026) lets each market choose its oracle. No claim anyone is corrupt — just room for another option.

Built Verdict for the NEAR hackathon:

A 3-model AI panel runs in NEAR AI Cloud TEEs, signs every vote (EIP-191), and only auto-settles when all three agree.

Clear markets: auto-settled at ~$0.002, 107/107 agreed with UMA (post-cutoff sports), hardware-attested. Request params allowlisted on-chain.
Unclear markets: escalated to humans — 0 invented answers. TOO_EARLY is first-class (Bitquery: 243/470 UMA disputes were "too early").

Hand-checked answer-bearing sources on 8 disputed markets (curator: AI; Baheet has not personally verified each row): 8 settled, 7 agree UMA, 1 disagree, 0 invent. Visa 5648379: under review — a more precise 9.5% figure may support UMA's YES. Same IDs with thin evidence: all escalated. Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.

Repo: https://github.com/Baheet18/verdict

Not financial advice. Demo video + testnet txs coming after I sign deploy.

---

Notes for Baheet:
- Attach the demo video when recorded.
- Add explorer links only after you sign testnet txs.
- Do not paste API keys or .env into the post.
- Do not claim the rule linter predicts disputes (optional checklist only).
- "Unsteerable" in conversation = request-parameter steering blocked; relayer still picks evidence.
- Do not restore a NEAR/Illia endorsement line.


### docs/SUBMISSION_DRAFT.md

# NEAR Hackathon submission payload draft (HUMAN submits — never auto-submit)

**Deadline**: Nov 14 23:59 UTC (= Nov 15 00:59 Africa/Lagos). Target submit by **Nov 12** (2-day buffer).

## Form fields (map exactly)

| Field | Value |
|---|---|
| **projectName** | Verdict |
| **leadName** | Baheet Adeniji |
| **email** | TODO — Baheet fills email |
| **size** | TODO — Baheet fills team size as the form requires (solo / N) |
| **tracks** | `["privacy", "defi", "security", "open"]` |
| **nearAddress** | TODO — Baheet fills **his own NEAR mainnet account**. Do **not** invent or guess. |
| **projectUrl** | https://github.com/Baheet18/verdict |
| **demoUrl** | TODO — link after Baheet records the demo (see `docs/DEMO_VIDEO.md`) and/or posts testnet explorer links |
| **idea** | (paste the Idea block below) |

## Why (2 lines — put at top of idea)

Bloomberg (2026-05-26) reported that about nine wallets accounted for roughly half of UMA dispute votes on Polymarket; the Wall Street Journal (May 2026) reported that in about one in five disputes at least one voter had a stake in the outcome. Polymarket Protocol V2 (announced 2026-10-05) lets each market choose its oracle path. Verdict is a hardware-attested multi-model option for that slot — **not** a claim that any whale is corrupt.

## idea (body)

Verdict settles markets with three attested models (Qwen3.8-27B, DeepSeek-V4.1-Flash-1017, GLM-5.3-flash) on NEAR AI Cloud. Votes are EIP-191 signatures over `model:sha256(req):sha256(resp)`. The NEAR contract:

1. Exact request-key allowlist (blocks logit_bias / guided decoding) + rejects duplicate JSON keys and backslash-escaped object keys (`\u0074emperature`).
2. Requires `commit_evidence` (relayer/owner) after the market deadline, with `evidence_uri`, before any `submit_verdict`.
3. Binds header fields: market_id, round, rules_hash, prompt_template_hash, evidence_bundle_hash.
4. Maps each signer → one model; rejects replayed signed texts.

**Headline**: Clear markets auto-settled at ~$0.002, **107/107** agreed with UMA, hardware-attested; request-parameter steering blocked (relayer evidence choice remains — see Limits). Unclear markets escalate to humans, **0 invented answers**. TOO_EARLY is first-class (Bitquery **243/470**). Hand-curated answer-bearing disputed set: **8 settled / 7 agree UMA / 1 disagree / 0 invent**; same IDs thin evidence **8/8 escalate**. Visa 5648379: under review — a more precise 9.5% figure may support UMA's YES (`docs/VISA_CASE.md`). Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.

Offline screener mode (`python scripts/offline_demo.py`) needs no API key.

## Tracks detail

- **privacy**: NEAR AI Cloud TEEs; TDX + NVIDIA attestation; allowlisted host-stack measurements.
- **defi**: prediction-market resolution primitive; bonded disputes; TOO_EARLY as a first-class round advance.
- **security**: on-chain request-key allowlist, duplicate/escaped JSON-key rejection, evidence commit before votes, relayer-only submit, replay protection, per-signer model binding.
- **open**: public repo, offline screener mode, published evidence bundles, scoreboard rebuilt from sanitized cache.

## Numbers (backed by committed scripts + fixtures)

- Post-cutoff sports: `docs/BENCHMARK.md` — 124 markets; 107 settled; 107 agree UMA; 0 disagree; ~$0.0022/market.
- UMA-disputed thin evidence: `docs/BENCHMARK_DISPUTED.md` — 34 markets; 0 settled; 34 escalate; 0 invent.
- Hand-curated answer-bearing disputed: `docs/BENCHMARK_DISPUTED_HAND.md` — 8 settled; 7 agree UMA; 1 disagree; 0 escalate; 0 invent; thin same IDs 8/8 escalate.
- Visa case: `docs/VISA_CASE.md`.
- Bitquery TOO_EARLY: 243 of 470 disputes (9 Sep–7 Oct 2026) — https://bitquery.io/investigations/who-decides-polymarket-disputes
- Rule authoring checklist: optional only; **no** dispute-prediction claim (control 27/34 vs 26/34).

## Limits (must stay in the submission)

- Model-serving container not covered by RTMR3 pin.
- Evidence fetched outside the TEE; registered relayer commits the hash.
- Owner registers signers / settles disputes.
- GPU↔TDX binding is the shared nonce only.
- “Unsteerable” ≠ trustless relayer.
- Testnet deploy requires the builder’s wallet (not agent-held keys).
- Curator column on hand set is **curator (AI)** unless Baheet personally verifies.

## Checklist before submit (human only)

- [ ] Push local round-7 commits to GitHub (explicit OK required)
- [ ] After push, set `evidence_uri` via `python scripts/evidence_uri.py <path> --sha <that_commit>` (never /master/, never invent SHA)
- [ ] Human signs testnet deploy on a **fresh** contract; paste tx links into README
- [ ] Fill **nearAddress** with Baheet’s mainnet account
- [ ] Record demo video from DEMO_VIDEO.md; set **demoUrl**
- [ ] Post X draft manually (plain text)
- [ ] Confirm no secrets in git
- [ ] **Baheet** submits the form — agents never submit


## Remaining human actions (no agent keys)

1. Explicit OK → `git push` local round-7 commit(s) to https://github.com/Baheet18/verdict
2. After push includes evidence files: `python scripts/evidence_uri.py fixtures/evidence/<sha>.json --sha <that_commit>` and update deploy args (never invent SHA; do not use f96954b)
3. Sign NEAR **testnet** deploy from Baheet's own wallet (`deploy/TESTNET.md` + `scripts/deploy_prepare.py`); paste explorer links into README
4. Record demo video (`docs/DEMO_VIDEO.md`)
5. Post X draft manually (plain text)
6. Fill `nearAddress` (Baheet mainnet) + `demoUrl` + `size` in SUBMISSION_DRAFT; **Baheet** submits the form by Nov 12 (buffer before Nov 14 23:59 UTC)
7. Agents: **STOP coding**; never push/deploy/post/submit without fresh explicit approval

## Headline (final)

Clear markets: auto-settled at ~$0.002, 107/107 agreed with UMA, hardware-attested; request-parameter steering blocked on-chain. Unclear markets: escalated to humans, 0 invented answers. TOO_EARLY is first-class (Bitquery 243/470). Hand-curated answer-bearing disputed: 8 settled / 7 agree UMA / 1 disagree (Visa 5648379: under review — a more precise 9.5% figure may support UMA's YES; docs/VISA_CASE.md) / 0 invent; thin same IDs 8/8 escalate. Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.
