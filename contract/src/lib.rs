//! Verdict: prediction markets settled by TEE-attested AI resolvers.
//! A verdict is accepted only if its ECDSA signature (EIP-191 over
//! "<model>:<sha256(request_raw)>:<sha256(response_raw)>") recovers to a registered,
//! hardware-attested signer registered for that model, and ALL resolvers agree.
use near_sdk::borsh::BorshSerialize;
use near_sdk::store::{IterableMap, LookupMap, LookupSet, Vector};
use near_sdk::{env, near, require, AccountId, BorshStorageKey, NearToken, PanicOnDefault, Promise};

#[near(serializers=[borsh, json])]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Outcome { Yes, No, TooEarly, Ambiguous }

#[near(serializers=[borsh, json])]
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum Status {
    Open,
    Proposed { outcome: Outcome, at_ns: u64 },
    /// Panel split, or unanimous AMBIGUOUS: needs human / bigger-panel review.
    Escalated,
    Disputed { proposed: Outcome, disputer: AccountId, bond: NearToken },
    Final { outcome: Outcome },
}

#[near(serializers=[borsh, json])]
#[derive(Clone, Debug, PartialEq)]
pub struct Vote {
    pub signer: String,
    pub model: String,
    pub outcome: Outcome,
    pub request_sha256: String,
    pub response_sha256: String,
}

#[near(serializers=[borsh, json])]
#[derive(Clone, Debug)]
pub struct Market {
    pub question: String,
    pub rules: String,
    /// sha256(rules), computed on-chain at creation. Every signed request must carry it in its header.
    pub rules_hash: String,
    /// sha256 of the exact system prompt (resolver instructions), committed at creation.
    pub prompt_template_hash: String,
    pub sources: Vec<String>,
    pub resolvers: Vec<String>, // lowercase 0x-addresses; each maps to exactly one model in `signers`
    pub threshold: u32,
    pub deadline_ns: u64,
    pub dispute_window_ns: u64,
    pub dispute_bond: NearToken,
    pub yes_pool: NearToken,
    pub no_pool: NearToken,
    /// Incremented when a unanimous TOO_EARLY clears the votes; requests must name the current round.
    pub round: u32,
    /// evidence_bundle_hash committed by a registered relayer (or owner) for this round BEFORE any vote.
    /// Votes whose header evidence hash differs are rejected. Cleared when TooEarly advances the round.
    pub round_evidence_hash: Option<String>,
    /// Off-chain URI where the full evidence bundle is published (same hash). Stored for auditors.
    pub round_evidence_uri: Option<String>,
    pub votes: Vec<Vote>,
    pub status: Status,
}

#[derive(BorshStorageKey, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
enum Key { Signers, Markets, Stakes, Attestations, UsedSigned, Relayers }

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Verdict {
    owner: AccountId,
    /// signer address -> the ONE model name that TEE key serves (from its attestation entry)
    signers: IterableMap<String, String>,
    /// signer -> sha256 of the attestation report JSON the owner verified before registering
    attestations: LookupMap<String, String>,
    /// accounts allowed to commit_evidence for a round (owner is always allowed even if not listed)
    relayers: LookupSet<AccountId>,
    markets: Vector<Market>,
    stakes: LookupMap<(u32, AccountId), (NearToken, NearToken)>,
    /// sha256(signed text) of every accepted vote, forever: a signed response can be counted once.
    used_signed: LookupSet<Vec<u8>>,
}

/// Fixed sampling seed every resolver request must carry (with temperature 0).
pub const REQUIRED_SEED: u64 = 1_114;
pub const HEADER_PREFIX: &str = "VERDICT/1";

fn hex20(a: &str) -> String {
    let a = a.trim_start_matches("0x").to_lowercase();
    require!(a.len() == 40 && hex::decode(&a).is_ok(), "bad address");
    format!("0x{a}")
}
fn hex32(h: &str, what: &str) -> String {
    let h = h.to_lowercase();
    if !(h.len() == 64 && hex::decode(&h).is_ok()) { env::panic_str(&format!("bad {what}")); }
    h
}
fn sha_hex(b: &[u8]) -> String { hex::encode(env::sha256_array(b)) }

/// EIP-191 personal_sign recovery -> lowercase 0x address, or None.
pub fn recover_signer(text: &str, sig_hex: &str) -> Option<String> {
    let sig = hex::decode(sig_hex.trim_start_matches("0x")).ok()?;
    if sig.len() != 65 { return None; }
    let mut msg = format!("\x19Ethereum Signed Message:\n{}", text.len()).into_bytes();
    msg.extend_from_slice(text.as_bytes());
    let hash = env::keccak256_array(&msg);
    let v = match sig[64] { 27 | 28 => sig[64] - 27, 0 | 1 => sig[64], _ => return None };
    let pk = env::ecrecover(&hash, &sig[..64], v, true)?;
    Some(format!("0x{}", hex::encode(&env::keccak256_array(&pk)[12..])))
}

/// The canonical first line of the user message. Everything after the first '\n' is the evidence
/// bundle, and evidence_bundle_hash = sha256(those bytes).
pub fn canonical_header(market_id: u32, round: u32, rules_hash: &str, prompt_template_hash: &str, evidence_bundle_hash: &str) -> String {
    format!("{HEADER_PREFIX} market_id={market_id} round={round} rules_hash={rules_hash} prompt_template_hash={prompt_template_hash} evidence_bundle_hash={evidence_bundle_hash}")
}



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
            if e.starts_with("duplicate JSON key:") {
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
                _ => Status::Escalated, // split panel or unanimous AMBIGUOUS
            };
        }
        outcome
    }

    #[payable]
    pub fn dispute(&mut self, market_id: u32) {
        let m = self.markets.get_mut(market_id).expect("no market");
        let Status::Proposed { outcome, at_ns } = m.status.clone() else { env::panic_str("nothing to dispute") };
        require!(env::block_timestamp() < at_ns + m.dispute_window_ns, "dispute window closed");
        require!(env::attached_deposit() >= m.dispute_bond, "bond too small");
        m.status = Status::Disputed { proposed: outcome, disputer: env::predecessor_account_id(), bond: env::attached_deposit() };
    }

    /// Post-dispute / escalation policy: owner re-runs a bigger attested panel off-chain (e.g. 5 models,
    /// evidence bundle published), then posts the result here. AMBIGUOUS => everyone is refunded.
    /// Human fallback. Disputer gets bond back if right; else bond goes to the winning pool.
    pub fn resolve_dispute(&mut self, market_id: u32, outcome: Outcome) -> Option<Promise> {
        self.only_owner();
        let m = self.markets.get_mut(market_id).expect("no market");
        if m.status == Status::Escalated { m.status = Status::Final { outcome }; return None; }
        let Status::Disputed { proposed, disputer, bond } = m.status.clone() else { env::panic_str("not disputed/escalated") };
        m.status = Status::Final { outcome };
        if outcome != proposed { return Some(Promise::new(disputer).transfer(bond)); }
        match outcome { Outcome::Yes => m.yes_pool = m.yes_pool.saturating_add(bond), _ => m.no_pool = m.no_pool.saturating_add(bond) }
        None
    }

    pub fn finalize(&mut self, market_id: u32) {
        let m = self.markets.get_mut(market_id).expect("no market");
        let Status::Proposed { outcome, at_ns } = m.status.clone() else { env::panic_str("not proposed") };
        require!(env::block_timestamp() >= at_ns + m.dispute_window_ns, "dispute window open");
        m.status = Status::Final { outcome };
    }

    pub fn claim(&mut self, market_id: u32) -> Promise {
        let m = self.markets.get(market_id).expect("no market");
        let Status::Final { outcome } = m.status else { env::panic_str("not final") };
        let who = env::predecessor_account_id();
        let (y, n) = self.stakes.remove(&(market_id, who.clone())).unwrap_or_else(|| env::panic_str("nothing to claim"));
        let (total, win_pool, my) = (m.yes_pool.as_yoctonear() + m.no_pool.as_yoctonear(),
            match outcome { Outcome::Yes => m.yes_pool, Outcome::No => m.no_pool, _ => NearToken::from_yoctonear(0) }.as_yoctonear(),
            match outcome { Outcome::Yes => y, Outcome::No => n, _ => NearToken::from_yoctonear(0) }.as_yoctonear());
        let pay = if win_pool == 0 { y.as_yoctonear() + n.as_yoctonear() } // AMBIGUOUS or nobody won: refund
                  else { (primitive_types::U256::from(my) * primitive_types::U256::from(total) / primitive_types::U256::from(win_pool)).as_u128() };
        require!(pay > 0, "nothing to claim");
        Promise::new(who).transfer(NearToken::from_yoctonear(pay))
    }

    pub fn get_market(&self, market_id: u32) -> Option<Market> { self.markets.get(market_id).cloned() }
    pub fn get_stake(&self, market_id: u32, account: AccountId) -> (NearToken, NearToken) {
        self.stakes.get(&(market_id, account)).copied().unwrap_or((NearToken::from_yoctonear(0), NearToken::from_yoctonear(0)))
    }
}

#[cfg(test)]
mod tests;
