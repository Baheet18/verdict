//! Lifecycle tests, including one real NEAR AI Cloud panel signature.

use std::result::Result as StdResult;

use anchor_lang::prelude::{AccountDeserialize, AccountSerialize, Pubkey};
use anchor_lang::Discriminator;
use anchor_lang::InstructionData;
use base64::Engine;
use k256::ecdsa::{signature::hazmat::PrehashSigner, SigningKey};
use litesvm::LiteSVM;
use serde_json::json;
use sha2::{Digest, Sha256};
use sha3::{Digest as _, Keccak256};
use solana_address::Address;
use solana_clock::Clock;
use solana_instruction::{AccountMeta, Instruction};
use solana_keypair::Keypair;
use solana_message::{Message, VersionedMessage};
use solana_signer::Signer as _;
use solana_transaction::versioned::VersionedTransaction;
use verdict::{
    MarketCreated, MarketDisputed, MarketFinalized, Outcome, VoteAccepted, VerdictSubmitted,
    CONFIDENCE_ABSENT, MIN_CLEAR_CONFIDENCE_BPS, PAYLOAD_DATA,
};

const WINDOW: i64 = 3_600;
const BOND: u64 = 1_000_000;
const T0: i64 = 1_700_000_000;
const PROGRAM_BYTES: &[u8] = include_bytes!("../../target/deploy/verdict.so");

struct World {
    svm: LiteSVM,
    owner: Keypair,
    escalation: Keypair,
    stranger: Keypair,
    keys: [SigningKey; 3],
    models: [&'static str; 3],
}

fn addr(pk: &Pubkey) -> Address {
    Address::new_from_array(pk.to_bytes())
}

fn pubkey_str(value: &str) -> Pubkey {
    value.parse().unwrap()
}

fn program() -> Address {
    addr(&verdict::ID)
}

fn sysvar_clock() -> Address {
    addr(&pubkey_str("SysvarC1ock11111111111111111111111111111111"))
}

fn system_program() -> Address {
    addr(&pubkey_str("11111111111111111111111111111111"))
}

fn config_pda() -> Pubkey {
    Pubkey::find_program_address(&[b"config"], &verdict::ID).0
}

fn market_pda(market_id: u64) -> Pubkey {
    Pubkey::find_program_address(&[b"market", &market_id.to_le_bytes()], &verdict::ID).0
}

fn payload_pda(market_id: u64, slot: u8) -> (Pubkey, u8) {
    Pubkey::find_program_address(&[b"payload", &market_id.to_le_bytes(), &[slot]], &verdict::ID)
}

fn set_time(svm: &mut LiteSVM, ts: i64) {
    let mut clock = svm.get_sysvar::<Clock>();
    clock.unix_timestamp = ts;
    svm.set_sysvar::<Clock>(&clock);
}

fn budget(tag: u8, value: u32) -> Instruction {
    let mut data = vec![tag];
    data.extend_from_slice(&value.to_le_bytes());
    Instruction {
        program_id: addr(&pubkey_str("ComputeBudget111111111111111111111111111111")),
        accounts: vec![],
        data,
    }
}

fn send(svm: &mut LiteSVM, ixs: &[Instruction], payer: &Keypair) -> StdResult<Vec<String>, String> {
    svm.expire_blockhash();
    let blockhash = svm.latest_blockhash();
    let mut with_heap = Vec::with_capacity(ixs.len() + 2);
    with_heap.push(budget(1, 256 * 1024));
    with_heap.push(budget(2, 1_400_000));
    with_heap.extend_from_slice(ixs);
    let msg = Message::new_with_blockhash(&with_heap, Some(&payer.pubkey()), &blockhash);
    let tx = VersionedTransaction::try_new(VersionedMessage::Legacy(msg), &[payer]).map_err(|e| e.to_string())?;
    match svm.send_transaction(tx) {
        Ok(meta) => Ok(meta.logs),
        Err(err) => Err(format!("{err:?}")),
    }
}

fn expect_ok(result: StdResult<Vec<String>, String>) -> Vec<String> {
    result.unwrap_or_else(|err| panic!("transaction failed: {err}"))
}

fn expect_err_real(result: StdResult<Vec<String>, String>, code: &str) {
    match result {
        Err(err) => assert!(err.contains(code), "expected {code} in {err}"),
        Ok(logs) => panic!("transaction succeeded: {logs:?}"),
    }
}

fn has_event<T: Discriminator>(logs: &[String]) -> bool {
    let disc = T::DISCRIMINATOR;
    for line in logs {
        let Some(encoded) = line.strip_prefix("Program data: ") else { continue };
        let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(encoded.trim()) else { continue };
        if bytes.len() >= 8 && &bytes[..8] == disc {
            return true;
        }
    }
    false
}

fn sha256(data: &[u8]) -> [u8; 32] {
    Sha256::digest(data).into()
}

fn keccak(data: &[u8]) -> [u8; 32] {
    Keccak256::digest(data).into()
}

fn hex32(bytes: &[u8; 32]) -> String {
    hex::encode(bytes)
}

fn eth_address(key: &SigningKey) -> [u8; 20] {
    let point = key.verifying_key().to_encoded_point(false);
    let hash = keccak(&point.as_bytes()[1..]);
    hash[12..].try_into().unwrap()
}

fn sign(model: &str, request: &[u8], response: &[u8], key: &SigningKey) -> [u8; 65] {
    let text = format!("{model}:{}:{}", hex32(&sha256(request)), hex32(&sha256(response)));
    let mut preimage = format!("\x19Ethereum Signed Message:\n{}", text.len()).into_bytes();
    preimage.extend_from_slice(text.as_bytes());
    let (sig, recid) = key.sign_prehash_recoverable(&keccak(&preimage)).unwrap();
    let mut out = [0u8; 65];
    out[..64].copy_from_slice(&sig.to_bytes());
    out[64] = recid.to_byte();
    out
}

fn request_bytes(model: &str, market_id: u64, round: u32, rules: &[u8; 32], prompt: &[u8; 32], evidence: &str) -> (Vec<u8>, [u8; 32]) {
    let evidence_hash = sha256(evidence.as_bytes());
    let user = format!(
        "VERDICT/1 market_id={market_id} round={round} rules_hash={} prompt_template_hash={} evidence_bundle_hash={}\n{evidence}",
        hex32(rules),
        hex32(prompt),
        hex32(&evidence_hash),
    );
    let body = json!({
        "model": model,
        "temperature": 0,
        "seed": 1114,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": user}
        ]
    });
    (serde_json::to_vec(&body).unwrap(), evidence_hash)
}

fn response_bytes(outcome: &str, confidence: &str) -> Vec<u8> {
    let content = format!(r#"{{"outcome":"{outcome}","confidence":{confidence}}}"#);
    serde_json::to_vec(&json!({
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": content}
        }]
    }))
    .unwrap()
}

impl World {
    fn new() -> Self {
        let mut svm = LiteSVM::new();
        svm.add_program(program(), PROGRAM_BYTES).unwrap();
        let owner = Keypair::new();
        let escalation = Keypair::new();
        let stranger = Keypair::new();
        for key in [&owner, &escalation, &stranger] {
            svm.airdrop(&key.pubkey(), 10_000_000_000).unwrap();
        }
        set_time(&mut svm, T0);
        let keys = [
            SigningKey::from_slice(&[1u8; 32]).unwrap(),
            SigningKey::from_slice(&[2u8; 32]).unwrap(),
            SigningKey::from_slice(&[3u8; 32]).unwrap(),
        ];
        let models = ["model-a", "model-b", "model-c"];
        let mut world = Self { svm, owner, escalation, stranger, keys, models };
        world.init_config(&world.addresses(), models);
        world
    }

    fn addresses(&self) -> [[u8; 20]; 3] {
        self.keys.each_ref().map(eth_address)
    }

    fn init_config(&mut self, addresses: &[[u8; 20]; 3], models: [&str; 3]) {
        let ix = Instruction {
            program_id: program(),
            accounts: vec![
                AccountMeta::new(addr(&config_pda()), false),
                AccountMeta::new(self.owner.pubkey(), true),
                AccountMeta::new_readonly(system_program(), false),
            ],
            data: verdict::instruction::InitializeConfig {
                signers: [
                    verdict::SignerArg { eth_address: addresses[0], model: models[0].into() },
                    verdict::SignerArg { eth_address: addresses[1], model: models[1].into() },
                    verdict::SignerArg { eth_address: addresses[2], model: models[2].into() },
                ],
                dispute_window_seconds: WINDOW,
                dispute_bond_lamports: BOND,
            }
            .data(),
        };
        expect_ok(send(&mut self.svm, &[ix], &self.owner));
    }

    fn create(&mut self, market_id: u64, question: &str, rules: [u8; 32], evidence: [u8; 32], prompt: [u8; 32]) {
        let escalation = Pubkey::new_from_array(self.escalation.pubkey().to_bytes());
        let ix = Instruction {
            program_id: program(),
            accounts: vec![
                AccountMeta::new(addr(&market_pda(market_id)), false),
                AccountMeta::new_readonly(addr(&config_pda()), false),
                AccountMeta::new(self.owner.pubkey(), true),
                AccountMeta::new_readonly(system_program(), false),
                AccountMeta::new_readonly(sysvar_clock(), false),
            ],
            data: verdict::instruction::CreateMarket {
                market_id,
                question: question.into(),
                rules_hash: rules,
                evidence_hash: evidence,
                prompt_template_hash: prompt,
                deadline: T0 + 100,
                escalation_owner: escalation,
            }
            .data(),
        };
        let logs = expect_ok(send(&mut self.svm, &[ix], &self.owner));
        assert!(has_event::<MarketCreated>(&logs), "{logs:?}");
    }

    fn upload_and_submit(&mut self, market_id: u64, slot: u8, request: &[u8], response: &[u8], signature: [u8; 65]) -> StdResult<Vec<String>, String> {
        let init = Instruction {
            program_id: program(),
            accounts: vec![
                AccountMeta::new(addr(&market_pda(market_id)), false),
                AccountMeta::new(addr(&payload_pda(market_id, slot).0), false),
                AccountMeta::new(self.stranger.pubkey(), true),
                AccountMeta::new_readonly(system_program(), false),
            ],
            data: verdict::instruction::InitPayload {
                market_id,
                slot,
                request_len: request.len() as u32,
                response_len: response.len() as u32,
            }
            .data(),
        };
        expect_ok(send(&mut self.svm, &[init], &self.stranger));
        let mut body = Vec::with_capacity(request.len() + response.len());
        body.extend_from_slice(request);
        body.extend_from_slice(response);
        let mut offset = 0u32;
        for chunk in body.chunks(400) {
            let write = Instruction {
                program_id: program(),
                accounts: vec![AccountMeta::new(addr(&payload_pda(market_id, slot).0), false)],
                data: verdict::instruction::WritePayload {
                    market_id,
                    slot,
                    offset,
                    data: chunk.to_vec(),
                }
                .data(),
            };
            expect_ok(send(&mut self.svm, &[write], &self.stranger));
            offset += chunk.len() as u32;
        }
        let submit = Instruction {
            program_id: program(),
            accounts: vec![
                AccountMeta::new(addr(&market_pda(market_id)), false),
                AccountMeta::new(addr(&payload_pda(market_id, slot).0), false),
                AccountMeta::new(self.stranger.pubkey(), true),
                AccountMeta::new_readonly(sysvar_clock(), false),
            ],
            data: verdict::instruction::SubmitVote { market_id, slot, signature }.data(),
        };
        send(&mut self.svm, &[submit], &self.stranger)
    }

    fn vote(&mut self, market_id: u64, slot: usize, outcome: &str, confidence: &str, rules: [u8; 32], prompt: [u8; 32], evidence: &str) -> StdResult<Vec<String>, String> {
        let (request, evidence_hash) = request_bytes(self.models[slot], market_id, 0, &rules, &prompt, evidence);
        let _ = evidence_hash;
        let response = response_bytes(outcome, confidence);
        let signature = sign(self.models[slot], &request, &response, &self.keys[slot]);
        self.upload_and_submit(market_id, slot as u8, &request, &response, signature)
    }

    fn market(&self, market_id: u64) -> verdict::Market {
        let account = self.svm.get_account(&addr(&market_pda(market_id))).unwrap();
        let mut data: &[u8] = &account.data;
        verdict::Market::try_deserialize(&mut data).unwrap()
    }
}

fn rules_and_prompt() -> ([u8; 32], [u8; 32], &'static str) {
    let evidence = r#"{"note":"clear"}"#;
    let prompt = sha256(b"sys");
    // rules hash is independent of the evidence text; the header carries this exact hash.
    let rules = sha256(b"rules");
    (rules, prompt, evidence)
}

#[test]
fn payload_header_matches_the_constant() {
    let payload = verdict::Payload {
        market_id: 1,
        slot: 0,
        bump: 1,
        request_len: 2,
        response_len: 3,
        filled: 0,
    };
    let mut data = Vec::new();
    payload.try_serialize(&mut data).unwrap();
    assert_eq!(data.len(), PAYLOAD_DATA);
}

#[test]
fn unanimous_clear_yes_finalises_after_the_window() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    let evidence_hash = sha256(evidence.as_bytes());
    world.create(1, "Head to head", rules, evidence_hash, prompt);
    set_time(&mut world.svm, T0 + 100);
    for slot in 0..3 {
        let logs = expect_ok(world.vote(1, slot, "YES", "0.91", rules, prompt, evidence));
        assert!(has_event::<VoteAccepted>(&logs), "{logs:?}");
    }
    let market = world.market(1);
    assert_eq!(market.status, verdict::Status::Proposed);
    assert_eq!(market.proposed_outcome, Outcome::Yes);
    assert_eq!(market.votes[2].confidence_bps, 9100);
    assert!(market.votes.iter().all(|vote| vote.set));

    let early_final = send(&mut world.svm, &[finalize_ix(1)], &world.stranger);
    expect_err_real(early_final, "DisputeWindowOpen");
    set_time(&mut world.svm, T0 + 100 + WINDOW);
    let logs = expect_ok(send(&mut world.svm, &[finalize_ix(1)], &world.stranger));
    assert!(has_event::<MarketFinalized>(&logs), "{logs:?}");
    let market = world.market(1);
    assert_eq!(market.status, verdict::Status::Final);
    assert_eq!(market.final_outcome, Outcome::Yes);
}

#[test]
fn dispute_posts_the_bond_and_the_escalation_owner_settles() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    world.create(2, "Disputed head to head", rules, sha256(evidence.as_bytes()), prompt);
    set_time(&mut world.svm, T0 + 100);
    for slot in 0..3 {
        expect_ok(world.vote(2, slot, "NO", "0.80", rules, prompt, evidence));
    }
    let before = world.svm.get_account(&addr(&market_pda(2))).unwrap().lamports;
    let ix = Instruction {
        program_id: program(),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(2)), false),
            AccountMeta::new(world.stranger.pubkey(), true),
            AccountMeta::new_readonly(sysvar_clock(), false),
            AccountMeta::new_readonly(system_program(), false),
        ],
        data: verdict::instruction::Dispute { market_id: 2 }.data(),
    };
    let logs = expect_ok(send(&mut world.svm, &[ix], &world.stranger));
    assert!(has_event::<MarketDisputed>(&logs), "{logs:?}");
    assert_eq!(world.market(2).status, verdict::Status::Escalated);
    assert_eq!(world.market(2).bond_lamports, BOND);
    assert_eq!(world.svm.get_account(&addr(&market_pda(2))).unwrap().lamports, before + BOND);
    let resolve = Instruction {
        program_id: program(),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(2)), false),
            AccountMeta::new_readonly(world.escalation.pubkey(), true),
        ],
        data: verdict::instruction::EscalationResolve { market_id: 2, outcome: Outcome::Yes }.data(),
    };
    let logs = expect_ok(send(&mut world.svm, &[resolve], &world.escalation));
    assert!(logs.iter().any(|line| line.contains("Program data:")));
    assert_eq!(world.market(2).status, verdict::Status::Final);
    assert_eq!(world.market(2).final_outcome, Outcome::Yes);
}

#[test]
fn split_vote_escalates() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    world.create(3, "Split", rules, sha256(evidence.as_bytes()), prompt);
    set_time(&mut world.svm, T0 + 100);
    expect_ok(world.vote(3, 0, "YES", "0.9", rules, prompt, evidence));
    expect_ok(world.vote(3, 1, "NO", "0.9", rules, prompt, evidence));
    let logs = expect_ok(world.vote(3, 2, "YES", "0.9", rules, prompt, evidence));
    assert!(has_event::<VerdictSubmitted>(&logs), "{logs:?}");
    let market = world.market(3);
    assert_eq!(market.status, verdict::Status::Escalated);
    assert_eq!(market.votes[1].outcome, Outcome::No);
}

#[test]
fn ambiguous_vote_escalates() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    world.create(4, "Unclear", rules, sha256(evidence.as_bytes()), prompt);
    set_time(&mut world.svm, T0 + 100);
    for slot in 0..3 {
        expect_ok(world.vote(4, slot, "AMBIGUOUS", "0.99", rules, prompt, evidence));
    }
    assert_eq!(world.market(4).status, verdict::Status::Escalated);
    assert_eq!(world.market(4).votes[0].outcome, Outcome::Ambiguous);
}

#[test]
fn low_confidence_does_not_propose() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    world.create(5, "Low confidence", rules, sha256(evidence.as_bytes()), prompt);
    set_time(&mut world.svm, T0 + 100);
    for slot in 0..3 {
        expect_ok(world.vote(5, slot, "YES", "0.50", rules, prompt, evidence));
    }
    let market = world.market(5);
    assert_eq!(market.status, verdict::Status::Escalated);
    assert!(market.votes[0].confidence_bps < MIN_CLEAR_CONFIDENCE_BPS);
    assert_ne!(market.votes[0].confidence_bps, CONFIDENCE_ABSENT);
}

#[test]
fn bad_signature_is_rejected() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    let evidence_hash = sha256(evidence.as_bytes());
    world.create(6, "Bad signature", rules, evidence_hash, prompt);
    set_time(&mut world.svm, T0 + 100);
    let (request, _) = request_bytes(world.models[0], 6, 0, &rules, &prompt, evidence);
    let response = response_bytes("YES", "0.9");
    let mut signature = sign(world.models[0], &request, &response, &world.keys[0]);
    signature[64] = 5;
    expect_err_real(
        world.upload_and_submit(6, 0, &request, &response, signature),
        "BadSignature",
    );
}

#[test]
fn wrong_signer_is_rejected() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    world.create(7, "Wrong signer", rules, sha256(evidence.as_bytes()), prompt);
    set_time(&mut world.svm, T0 + 100);
    let (request, _) = request_bytes(world.models[0], 7, 0, &rules, &prompt, evidence);
    let response = response_bytes("YES", "0.9");
    let other = SigningKey::from_slice(&[9u8; 32]).unwrap();
    let signature = sign(world.models[0], &request, &response, &other);
    expect_err_real(
        world.upload_and_submit(7, 0, &request, &response, signature),
        "WrongSigner",
    );
}

#[test]
fn replay_onto_another_market_is_rejected() {
    let mut world = World::new();
    let (rules, prompt, evidence) = rules_and_prompt();
    let evidence_hash = sha256(evidence.as_bytes());
    world.create(8, "Market eight", rules, evidence_hash, prompt);
    world.create(9, "Market nine", rules, evidence_hash, prompt);
    set_time(&mut world.svm, T0 + 100);
    let (request, _) = request_bytes(world.models[0], 8, 0, &rules, &prompt, evidence);
    let response = response_bytes("YES", "0.9");
    let signature = sign(world.models[0], &request, &response, &world.keys[0]);
    expect_err_real(
        world.upload_and_submit(9, 0, &request, &response, signature),
        "HeaderMismatch",
    );
    assert_eq!(world.market(9).status, verdict::Status::Open);
    assert!(!world.market(9).votes[0].set);
}

#[test]
fn non_escalation_owner_cannot_resolve() {
    let mut world = World::new();
    open_escalated(&mut world, 10);
    let ix = resolve_ix(10, &world.stranger);
    expect_err_real(send(&mut world.svm, &[ix], &world.stranger), "NotEscalationOwner");
    assert_eq!(world.market(10).status, verdict::Status::Escalated);
}

#[test]
fn protocol_owner_cannot_resolve_a_market() {
    let mut world = World::new();
    open_escalated(&mut world, 11);
    let ix = resolve_ix(11, &world.owner);
    expect_err_real(send(&mut world.svm, &[ix], &world.owner), "NotEscalationOwner");
    assert_eq!(world.market(11).status, verdict::Status::Escalated);
    let owner = Pubkey::new_from_array(world.owner.pubkey().to_bytes());
    assert_ne!(world.market(11).escalation_owner, owner);
}

#[test]
fn real_near_signatures_propose_yes() {
    let file: serde_json::Value = serde_json::from_str(include_str!(
        "../../../contract/testdata/real_panel.json"
    ))
    .unwrap();
    let mut svm = LiteSVM::new();
    svm.add_program(program(), PROGRAM_BYTES).unwrap();
    let owner = Keypair::new();
    let escalation = Keypair::new();
    let stranger = Keypair::new();
    for key in [&owner, &escalation, &stranger] {
        svm.airdrop(&key.pubkey(), 10_000_000_000).unwrap();
    }
    set_time(&mut svm, T0);
    let votes = file["votes"].as_array().unwrap();
    let mut addresses = [[0u8; 20]; 3];
    let mut models = Vec::new();
    for (i, vote) in votes.iter().enumerate() {
        let hex_addr = vote["signing_address"].as_str().unwrap().trim_start_matches("0x");
        addresses[i] = hex::decode(hex_addr).unwrap().try_into().unwrap();
        models.push(vote["model"].as_str().unwrap().to_string());
    }
    let mut world = World {
        svm,
        owner,
        escalation,
        stranger,
        keys: [
            SigningKey::from_slice(&[1u8; 32]).unwrap(),
            SigningKey::from_slice(&[2u8; 32]).unwrap(),
            SigningKey::from_slice(&[3u8; 32]).unwrap(),
        ],
        models: ["model-a", "model-b", "model-c"],
    };
    world.init_config(&addresses, [&models[0], &models[1], &models[2]]);
    let rules = hex::decode("0a4e65422937656bae4ae33d26e8c8f2e710faeeaa3572dac0aeb0d5ce5897da").unwrap().try_into().unwrap();
    let evidence = hex::decode("ccde677158f3e2ca4962f09e365c2ff7be958df4bdd50b7a23ac259f5fc598b7").unwrap().try_into().unwrap();
    let prompt = hex::decode("fe85745ffa51fc2ac8a80b61fff3e13c3e680f4c2cf71f6142f10b5bd97f7d8f").unwrap().try_into().unwrap();
    world.create(0, "Saints vs. Giants", rules, evidence, prompt);
    set_time(&mut world.svm, T0 + 100);
    for (slot, vote) in votes.iter().enumerate() {
        let request = vote["request_raw"].as_str().unwrap().as_bytes();
        let response = vote["response_raw"].as_str().unwrap().as_bytes();
        let signature: [u8; 65] = hex::decode(vote["signature"].as_str().unwrap()).unwrap().try_into().unwrap();
        let logs = expect_ok(world.upload_and_submit(0, slot as u8, request, response, signature));
        assert!(has_event::<VoteAccepted>(&logs), "{logs:?}");
    }
    let market = world.market(0);
    assert_eq!(market.status, verdict::Status::Proposed);
    assert_eq!(market.proposed_outcome, Outcome::Yes);
    assert!(market.votes.iter().all(|vote| vote.confidence_bps == 9900));
}

fn open_escalated(world: &mut World, market_id: u64) {
    let (rules, prompt, evidence) = rules_and_prompt();
    world.create(market_id, "Escalated", rules, sha256(evidence.as_bytes()), prompt);
    set_time(&mut world.svm, T0 + 100);
    expect_ok(world.vote(market_id, 0, "YES", "0.9", rules, prompt, evidence));
    expect_ok(world.vote(market_id, 1, "NO", "0.9", rules, prompt, evidence));
    expect_ok(world.vote(market_id, 2, "NO", "0.9", rules, prompt, evidence));
}

fn finalize_ix(market_id: u64) -> Instruction {
    Instruction {
        program_id: program(),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(market_id)), false),
            AccountMeta::new_readonly(sysvar_clock(), false),
        ],
        data: verdict::instruction::Finalize { market_id }.data(),
    }
}

fn resolve_ix(market_id: u64, signer: &Keypair) -> Instruction {
    Instruction {
        program_id: program(),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(market_id)), false),
            AccountMeta::new_readonly(signer.pubkey(), true),
        ],
        data: verdict::instruction::EscalationResolve { market_id, outcome: Outcome::Yes }.data(),
    }
}
