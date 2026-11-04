//! Verdict: prediction markets settled by TEE-attested AI resolvers.
//! A verdict is accepted only if its ECDSA signature (EIP-191 over
//! "<model>:<sha256(request)>:<sha256(response)>") recovers to a registered,
//! hardware-attested signer, and a supermajority of distinct signers agree.
use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::serde::{Deserialize, Serialize};
use near_sdk::store::{IterableSet, LookupMap, Vector};
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
#[derive(Clone, Debug)]
pub struct Market {
    pub question: String,
    pub rules: String,
    pub sources: Vec<String>,
    pub model: String,
    pub resolvers: Vec<String>, // lowercase 0x-addresses
    pub threshold: u32,
    pub deadline_ns: u64,
    pub dispute_window_ns: u64,
    pub dispute_bond: NearToken,
    pub yes_pool: NearToken,
    pub no_pool: NearToken,
    pub votes: Vec<(String, Outcome, String)>, // (signer, outcome, signed text)
    pub status: Status,
}

#[derive(BorshStorageKey, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
enum Key { Signers, Markets, Stakes, Attestations }

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Verdict {
    owner: AccountId,
    signers: IterableSet<String>,
    /// signer -> sha256 of the attestation report JSON the owner verified before registering
    attestations: LookupMap<String, String>,
    markets: Vector<Market>,
    stakes: LookupMap<(u32, AccountId), (NearToken, NearToken)>,
}

fn hex20(a: &str) -> String {
    let a = a.trim_start_matches("0x").to_lowercase();
    require!(a.len() == 40 && hex::decode(&a).is_ok(), "bad address");
    format!("0x{a}")
}

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

/// Pull outcome out of an OpenAI-style chat completion body.
pub fn parse_outcome(response_raw: &str) -> Option<Outcome> {
    let v: serde_json::Value = serde_json::from_str(response_raw).ok()?;
    let content = v["choices"][0]["message"]["content"].as_str()?.trim();
    let start = content.find('{')?; let end = content.rfind('}')?;
    let inner: serde_json::Value = serde_json::from_str(&content[start..=end]).ok()?;
    match inner["outcome"].as_str()?.to_uppercase().as_str() {
        "YES" => Some(Outcome::Yes), "NO" => Some(Outcome::No),
        "TOO_EARLY" => Some(Outcome::TooEarly), "AMBIGUOUS" => Some(Outcome::Ambiguous), _ => None,
    }
}

#[near]
impl Verdict {
    #[init]
    pub fn new(owner: AccountId) -> Self {
        Self { owner, signers: IterableSet::new(Key::Signers), attestations: LookupMap::new(Key::Attestations), markets: Vector::new(Key::Markets), stakes: LookupMap::new(Key::Stakes) }
    }

    fn only_owner(&self) { require!(env::predecessor_account_id() == self.owner, "owner only"); }

    /// Owner registers a signer address after verifying its TDX+GPU attestation off-chain.
    /// Only the contract owner can register. Policy: run scripts/attest_demo.py (TDX quote + NVIDIA NRAS +
    /// report_data = address||nonce) first, then register with sha256 of that attestation report JSON.
    pub fn register_signer(&mut self, address: String, attestation_report_sha256: String) {
        self.only_owner();
        require!(attestation_report_sha256.len() == 64 && hex::decode(&attestation_report_sha256).is_ok(), "bad report hash");
        let a = hex20(&address);
        self.signers.insert(a.clone());
        self.attestations.insert(a, attestation_report_sha256.to_lowercase());
    }
    pub fn get_signer_attestation(&self, address: String) -> Option<String> { self.attestations.get(&hex20(&address)).cloned() }
    pub fn remove_signer(&mut self, address: String) { self.only_owner(); self.signers.remove(&hex20(&address)); }
    pub fn get_signers(&self) -> Vec<String> { self.signers.iter().cloned().collect() }

    #[allow(clippy::too_many_arguments)]
    pub fn create_market(&mut self, question: String, rules: String, sources: Vec<String>, model: String,
                         resolvers: Vec<String>, threshold: u32, deadline_ns: u64,
                         dispute_window_ns: u64, dispute_bond: NearToken) -> u32 {
        let mut rs: Vec<String> = resolvers.iter().map(|a| hex20(a)).collect();
        rs.sort(); rs.dedup();
        let n = rs.len() as u32;
        require!(n >= 1 && threshold >= 1 && threshold <= n, "bad resolver set");
        require!(threshold == n, "auto-verdict requires all resolvers to agree (threshold == resolver count)");
        require!(deadline_ns > env::block_timestamp(), "deadline in the past");
        require!(!sources.is_empty(), "lock at least one source");
        self.markets.push(Market { question, rules, sources, model, resolvers: rs, threshold, deadline_ns,
            dispute_window_ns, dispute_bond, yes_pool: NearToken::from_yoctonear(0), no_pool: NearToken::from_yoctonear(0),
            votes: vec![], status: Status::Open });
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

    /// Anyone relays a signed resolver output. Returns the outcome it counted.
    pub fn submit_verdict(&mut self, market_id: u32, request_hash: String, response_raw: String, signature: String) -> Outcome {
        let in_set = |s: &String| self.signers.contains(s);
        let m = self.markets.get(market_id).expect("no market");
        require!(env::block_timestamp() >= m.deadline_ns, "market not ended");
        require!(m.status == Status::Open, "not accepting verdicts");
        require!(request_hash.len() == 64 && hex::decode(&request_hash).is_ok(), "bad request hash");
        let text = format!("{}:{}:{}", m.model, request_hash.to_lowercase(), hex::encode(env::sha256_array(response_raw.as_bytes())));
        let signer = recover_signer(&text, &signature).unwrap_or_else(|| env::panic_str("bad signature"));
        require!(in_set(&signer), "signer not attested/registered");
        require!(m.resolvers.contains(&signer), "signer not in market resolver set");
        require!(!m.votes.iter().any(|(s, _, _)| *s == signer), "signer already voted");
        let outcome = parse_outcome(&response_raw).unwrap_or_else(|| env::panic_str("no valid outcome in response"));
        let m = self.markets.get_mut(market_id).unwrap();
        m.votes.push((signer, outcome, text));
        if m.votes.len() as u32 == m.threshold {
            let all_same = m.votes.iter().all(|(_, o, _)| *o == outcome);
            m.status = match (all_same, outcome) {
                (true, Outcome::Yes | Outcome::No) => Status::Proposed { outcome, at_ns: env::block_timestamp() },
                (true, Outcome::TooEarly) => { m.votes.clear(); Status::Open } // re-run panel later
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
