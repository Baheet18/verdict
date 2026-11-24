//! Lifecycle tests against the compiled program. Requires
//! `solana/target/deploy/verdict.so` from `cargo-build-sbf`.

use std::result::Result as StdResult;

use anchor_lang::prelude::{AccountDeserialize, Pubkey};
use anchor_lang::InstructionData;
use anchor_lang::Discriminator;
use base64::Engine;
use ed25519_dalek::{Signer, SigningKey};
use litesvm::LiteSVM;
use solana_address::Address;
use solana_clock::Clock;
use solana_instruction::{AccountMeta, Instruction};
use solana_keypair::Keypair;
use solana_message::{Message, VersionedMessage};
use solana_signer::Signer as _;
use solana_transaction::versioned::VersionedTransaction;
use verdict::{
    canonical_message, ConfigUpdated, EscalationResolved, MarketCreated, MarketDisputed,
    MarketFinalized, Outcome, VerdictSubmitted, VoteInput, MIN_CLEAR_CONFIDENCE,
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
}

fn addr(pk: &Pubkey) -> Address {
    Address::new_from_array(pk.to_bytes())
}

fn pk_of(bytes: &[u8; 32]) -> Pubkey {
    Pubkey::new_from_array(*bytes)
}

fn ed25519_id() -> Address {
    addr(&pubkey_str("Ed25519SigVerify111111111111111111111111111"))
}

fn sysvar_instructions() -> Address {
    addr(&pubkey_str("Sysvar1nstructions1111111111111111111111111"))
}

fn sysvar_clock() -> Address {
    addr(&pubkey_str("SysvarC1ock11111111111111111111111111111111"))
}

fn system_program() -> Address {
    addr(&pubkey_str("11111111111111111111111111111111"))
}

fn pubkey_str(value: &str) -> Pubkey {
    value.parse().unwrap()
}

fn config_pda() -> Pubkey {
    Pubkey::find_program_address(&[b"config"], &verdict::ID).0
}

fn market_pda(market_id: u64) -> Pubkey {
    Pubkey::find_program_address(&[b"market", &market_id.to_le_bytes()], &verdict::ID).0
}

fn set_time(svm: &mut LiteSVM, ts: i64) {
    let mut clock = svm.get_sysvar::<Clock>();
    clock.unix_timestamp = ts;
    svm.set_sysvar::<Clock>(&clock);
}

fn send(svm: &mut LiteSVM, ixs: &[Instruction], payer: &Keypair) -> StdResult<Vec<String>, String> {
    // A repeated instruction with the same blockhash is the same signature.
    svm.expire_blockhash();
    let blockhash = svm.latest_blockhash();
    let msg = Message::new_with_blockhash(ixs, Some(&payer.pubkey()), &blockhash);
    let tx = VersionedTransaction::try_new(VersionedMessage::Legacy(msg), &[payer])
        .map_err(|err| err.to_string())?;
    match svm.send_transaction(tx) {
        Ok(meta) => Ok(meta.logs),
        Err(err) => Err(format!("{err:?}")),
    }
}

fn expect_ok(result: StdResult<Vec<String>, String>) -> Vec<String> {
    match result {
        Ok(logs) => logs,
        Err(err) => panic!("transaction failed: {err}"),
    }
}

fn expect_err(result: StdResult<Vec<String>, String>, code: &str) {
    let err = match result {
        Err(err) => err,
        Ok(logs) => panic!("transaction succeeded, logs: {logs:?}"),
    };
    assert!(
        err.contains(code),
        "expected {code} in error, got {err}"
    );
}

fn has_event<T: Discriminator>(logs: &[String]) -> bool {
    let disc = T::DISCRIMINATOR;
    for line in logs {
        let Some(encoded) = line.strip_prefix("Program data: ") else {
            continue;
        };
        let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(encoded.trim()) else {
            continue;
        };
        if bytes.len() >= 8 && &bytes[..8] == disc {
            return true;
        }
    }
    false
}

fn vote(outcome: Outcome, confidence: u8, cited: u8) -> VoteInput {
    VoteInput {
        outcome,
        confidence,
        cited_rule: [cited; 32],
    }
}

/// One ed25519 instruction, three signatures, offsets pointing at this instruction.
fn ed25519_ix(keys: &[SigningKey; 3], messages: [&[u8]; 3]) -> Instruction {
    let count = 3usize;
    let header = 2 + count * 14;
    let mut cursor = header;
    let mut parts = Vec::with_capacity(count);
    for (key, message) in keys.iter().zip(messages) {
        let public = key.verifying_key().to_bytes();
        let signature = key.sign(message).to_bytes();
        let public_offset = cursor;
        cursor += 32;
        let signature_offset = cursor;
        cursor += 64;
        let message_offset = cursor;
        cursor += message.len();
        parts.push((public_offset, signature_offset, message_offset, public, signature, message));
    }
    let mut data = vec![0u8; cursor];
    data[0] = count as u8;
    for (index, (public_offset, signature_offset, message_offset, public, signature, message)) in
        parts.iter().enumerate()
    {
        let start = 2 + index * 14;
        data[start..start + 2].copy_from_slice(&(*signature_offset as u16).to_le_bytes());
        data[start + 2..start + 4].copy_from_slice(&u16::MAX.to_le_bytes());
        data[start + 4..start + 6].copy_from_slice(&(*public_offset as u16).to_le_bytes());
        data[start + 6..start + 8].copy_from_slice(&u16::MAX.to_le_bytes());
        data[start + 8..start + 10].copy_from_slice(&(*message_offset as u16).to_le_bytes());
        data[start + 10..start + 12].copy_from_slice(&(message.len() as u16).to_le_bytes());
        data[start + 12..start + 14].copy_from_slice(&u16::MAX.to_le_bytes());
        data[*public_offset..*public_offset + 32].copy_from_slice(public);
        data[*signature_offset..*signature_offset + 64].copy_from_slice(signature);
        data[*message_offset..*message_offset + message.len()].copy_from_slice(message);
    }
    Instruction {
        program_id: ed25519_id(),
        accounts: vec![],
        data,
    }
}

fn signed_messages(
    market_id: u64,
    evidence: &[u8; 32],
    rules: &[u8; 32],
    votes: &[VoteInput; 3],
) -> [[u8; verdict::MESSAGE_LEN]; 3] {
    [0, 1, 2].map(|index| {
        canonical_message(
            market_id,
            evidence,
            rules,
            votes[index].outcome,
            votes[index].confidence,
            &votes[index].cited_rule,
        )
    })
}

fn panel_ix(keys: &[SigningKey; 3], market_id: u64, evidence: &[u8; 32], rules: &[u8; 32], votes: &[VoteInput; 3]) -> Instruction {
    let messages = signed_messages(market_id, evidence, rules, votes);
    ed25519_ix(keys, [&messages[0], &messages[1], &messages[2]])
}

impl World {
    fn new() -> Self {
        let mut svm = LiteSVM::new().with_precompiles();
        svm.add_program(addr(&verdict::ID), PROGRAM_BYTES).unwrap();
        let owner = Keypair::new();
        let escalation = Keypair::new();
        let stranger = Keypair::new();
        for key in [&owner, &escalation, &stranger] {
            svm.airdrop(&key.pubkey(), 10_000_000_000).unwrap();
        }
        set_time(&mut svm, T0);
        let keys = [
            SigningKey::from_bytes(&[1u8; 32]),
            SigningKey::from_bytes(&[2u8; 32]),
            SigningKey::from_bytes(&[3u8; 32]),
        ];
        let mut world = Self {
            svm,
            owner,
            escalation,
            stranger,
            keys,
        };
        world.init_config(&world.signer_pubkeys(), WINDOW, BOND);
        world
    }

    fn signer_pubkeys(&self) -> [Pubkey; 3] {
        self.keys
            .iter()
            .map(|key| pk_of(&key.verifying_key().to_bytes()))
            .collect::<Vec<_>>()
            .try_into()
            .unwrap()
    }

    fn init_config(&mut self, signers: &[Pubkey; 3], window: i64, bond: u64) {
        let ix = Instruction {
            program_id: addr(&verdict::ID),
            accounts: vec![
                AccountMeta::new(addr(&config_pda()), false),
                AccountMeta::new(self.owner.pubkey(), true),
                AccountMeta::new_readonly(system_program(), false),
            ],
            data: verdict::instruction::InitializeConfig {
                glm_signer: signers[0],
                qwen_signer: signers[1],
                deepseek_signer: signers[2],
                dispute_window_seconds: window,
                dispute_bond_lamports: bond,
            }
            .data(),
        };
        let logs = expect_ok(send(&mut self.svm, &[ix], &self.owner));
        assert!(has_event::<ConfigUpdated>(&logs), "logs: {logs:?}");
    }

    fn create(&mut self, market_id: u64, question: &str, deadline: i64) -> ([u8; 32], [u8; 32]) {
        let evidence = [market_id as u8; 32];
        let rules = [0xAB; 32];
        let escalation = pk_of(&self.escalation.pubkey().to_bytes());
        let ix = Instruction {
            program_id: addr(&verdict::ID),
            accounts: vec![
                AccountMeta::new(addr(&market_pda(market_id)), false),
                AccountMeta::new_readonly(addr(&config_pda()), false),
                AccountMeta::new(self.owner.pubkey(), true),
                AccountMeta::new_readonly(system_program(), false),
                AccountMeta::new_readonly(sysvar_clock(), false),
            ],
            data: verdict::instruction::CreateMarket {
                market_id,
                question: question.to_string(),
                rules_hash: rules,
                evidence_hash: evidence,
                deadline,
                escalation_owner: escalation,
            }
            .data(),
        };
        let logs = expect_ok(send(&mut self.svm, &[ix], &self.owner));
        assert!(has_event::<MarketCreated>(&logs), "logs: {logs:?}");
        (evidence, rules)
    }

    fn submit(
        &mut self,
        market_id: u64,
        evidence: &[u8; 32],
        rules: &[u8; 32],
        votes: [VoteInput; 3],
        keys: &[SigningKey; 3],
    ) -> StdResult<Vec<String>, String> {
        let ed = panel_ix(keys, market_id, evidence, rules, &votes);
        let ix = Instruction {
            program_id: addr(&verdict::ID),
            accounts: vec![
                AccountMeta::new(addr(&market_pda(market_id)), false),
                AccountMeta::new_readonly(addr(&config_pda()), false),
                AccountMeta::new_readonly(sysvar_instructions(), false),
                AccountMeta::new_readonly(sysvar_clock(), false),
            ],
            data: verdict::instruction::SubmitVerdict { market_id, votes }.data(),
        };
        send(&mut self.svm, &[ed, ix], &self.stranger)
    }

    fn market(&self, market_id: u64) -> verdict::Market {
        let account = self
            .svm
            .get_account(&addr(&market_pda(market_id)))
            .expect("market account");
        let mut data: &[u8] = &account.data;
        verdict::Market::try_deserialize(&mut data).expect("market data")
    }

    fn lamports(&self, market_id: u64) -> u64 {
        self.svm
            .get_account(&addr(&market_pda(market_id)))
            .expect("market account")
            .lamports
    }
}

#[test]
fn unanimous_clear_yes_finalises_after_the_window() {
    let mut world = World::new();
    let (evidence, rules) = world.create(1, "Saints beat the Giants?", T0 + 100);
    let votes = [
        vote(Outcome::Yes, 90, 1),
        vote(Outcome::Yes, MIN_CLEAR_CONFIDENCE, 1),
        vote(Outcome::Yes, 100, 2),
    ];
    expect_err(
        world.submit(1, &evidence, &rules, votes, &world.keys.clone()),
        "DeadlineNotReached",
    );
    set_time(&mut world.svm, T0 + 100);
    let logs = expect_ok(world.submit(1, &evidence, &rules, votes, &world.keys.clone()));
    assert!(has_event::<VerdictSubmitted>(&logs), "logs: {logs:?}");
    let market = world.market(1);
    assert_eq!(market.status, verdict::Status::Proposed);
    assert_eq!(market.proposed_outcome, Outcome::Yes);
    assert!(market.votes_set);
    assert_eq!(market.votes[0].outcome, Outcome::Yes);
    assert_eq!(market.votes[1].confidence, MIN_CLEAR_CONFIDENCE);
    assert_eq!(market.votes[2].cited_rule, [2u8; 32]);
    assert_eq!(market.evidence_hash, evidence);
    assert_eq!(market.rules_hash, rules);
    assert_eq!(market.question, "Saints beat the Giants?");

    expect_err(
        send(
            &mut world.svm,
            &[finalize_ix(1)],
            &world.stranger,
        ),
        "DisputeWindowOpen",
    );

    set_time(&mut world.svm, T0 + 100 + WINDOW);
    let logs = expect_ok(send(&mut world.svm, &[finalize_ix(1)], &world.stranger));
    assert!(has_event::<MarketFinalized>(&logs), "logs: {logs:?}");
    let market = world.market(1);
    assert_eq!(market.status, verdict::Status::Final);
    assert_eq!(market.final_outcome, Outcome::Yes);
}

#[test]
fn dispute_posts_the_bond_and_the_escalation_owner_settles() {
    let mut world = World::new();
    let (evidence, rules) = world.create(2, "Will it rain?", T0 + 10);
    set_time(&mut world.svm, T0 + 10);
    let votes = [vote(Outcome::No, 88, 4); 3];
    let logs = expect_ok(world.submit(2, &evidence, &rules, votes, &world.keys.clone()));
    assert!(has_event::<VerdictSubmitted>(&logs));
    let before = world.lamports(2);

    let ix = Instruction {
        program_id: addr(&verdict::ID),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(2)), false),
            AccountMeta::new(world.stranger.pubkey(), true),
            AccountMeta::new_readonly(sysvar_clock(), false),
            AccountMeta::new_readonly(system_program(), false),
        ],
        data: verdict::instruction::Dispute { market_id: 2 }.data(),
    };
    let logs = expect_ok(send(&mut world.svm, &[ix], &world.stranger));
    assert!(has_event::<MarketDisputed>(&logs), "logs: {logs:?}");
    let market = world.market(2);
    assert_eq!(market.status, verdict::Status::Escalated);
    assert_eq!(market.bond_lamports, BOND);
    assert_eq!(market.proposed_outcome, Outcome::No);
    assert_eq!(
        market.disputer,
        pk_of(&world.stranger.pubkey().to_bytes())
    );
    assert_eq!(world.lamports(2), before + BOND);

    let logs = expect_ok(send(
        &mut world.svm,
        &[resolve_ix(2, Outcome::Yes, &world.escalation)],
        &world.escalation,
    ));
    assert!(has_event::<EscalationResolved>(&logs), "logs: {logs:?}");
    let market = world.market(2);
    assert_eq!(market.status, verdict::Status::Final);
    assert_eq!(market.final_outcome, Outcome::Yes);
    assert_eq!(market.bond_lamports, BOND);
}

#[test]
fn split_vote_escalates() {
    let mut world = World::new();
    let (evidence, rules) = world.create(3, "Split?", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [
        vote(Outcome::Yes, 91, 1),
        vote(Outcome::No, 91, 1),
        vote(Outcome::Yes, 91, 1),
    ];
    let logs = expect_ok(world.submit(3, &evidence, &rules, votes, &world.keys.clone()));
    assert!(has_event::<VerdictSubmitted>(&logs));
    let market = world.market(3);
    assert_eq!(market.status, verdict::Status::Escalated);
    assert_eq!(market.votes[1].outcome, Outcome::No);
}

#[test]
fn unclear_vote_escalates() {
    let mut world = World::new();
    let (evidence, rules) = world.create(4, "Unclear?", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [
        vote(Outcome::Yes, 95, 1),
        vote(Outcome::Yes, 95, 1),
        vote(Outcome::Unclear, 99, 9),
    ];
    expect_ok(world.submit(4, &evidence, &rules, votes, &world.keys.clone()));
    let market = world.market(4);
    assert_eq!(market.status, verdict::Status::Escalated);
    assert_eq!(market.votes[2].outcome, Outcome::Unclear);
    assert_eq!(market.votes[2].cited_rule, [9u8; 32]);
}

#[test]
fn low_confidence_escalates() {
    let mut world = World::new();
    let (evidence, rules) = world.create(5, "Low confidence?", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [
        vote(Outcome::Yes, 90, 1),
        vote(Outcome::Yes, 90, 1),
        vote(Outcome::Yes, MIN_CLEAR_CONFIDENCE - 1, 1),
    ];
    expect_ok(world.submit(5, &evidence, &rules, votes, &world.keys.clone()));
    assert_eq!(world.market(5).status, verdict::Status::Escalated);
}

#[test]
fn bad_signature_is_rejected() {
    let mut world = World::new();
    let (evidence, rules) = world.create(6, "Bad sig?", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [vote(Outcome::Yes, 90, 1); 3];
    let mut ed = panel_ix(&world.keys, 6, &evidence, &rules, &votes);
    let header = 2 + 3 * 14;
    // First signature byte. The ed25519 program is instruction 0 and rejects it
    // before this program runs. Custom(2) is that program's invalid-signature error.
    ed.data[header + 32] ^= 0xff;
    let ix = submit_ix(6, votes);
    expect_err(
        send(&mut world.svm, &[ed, ix], &world.stranger),
        "InstructionError(0, Custom(2))",
    );
}

#[test]
fn wrong_signer_is_rejected() {
    let mut world = World::new();
    let (evidence, rules) = world.create(7, "Wrong signer?", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [vote(Outcome::Yes, 90, 1); 3];
    let mut keys = world.keys.clone();
    keys[1] = SigningKey::from_bytes(&[9u8; 32]);
    expect_err(
        world.submit(7, &evidence, &rules, votes, &keys),
        "WrongSigner",
    );
}

#[test]
fn replay_onto_another_market_is_rejected() {
    let mut world = World::new();
    let (evidence, rules) = world.create(8, "Market eight", T0 + 5);
    world.create(9, "Market nine", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [vote(Outcome::Yes, 90, 1); 3];
    let ed = panel_ix(&world.keys, 8, &evidence, &rules, &votes);
    // Same signature bytes, claimed against market 9. The signed message
    // carries market 8, so the program rejects it.
    let ix = submit_ix(9, votes);
    expect_err(
        send(&mut world.svm, &[ed, ix], &world.stranger),
        "MessageMismatch",
    );
    assert_eq!(world.market(9).status, verdict::Status::Open);
    assert!(!world.market(9).votes_set);
}

#[test]
fn non_escalation_owner_cannot_resolve() {
    let mut world = World::new();
    let (evidence, rules) = world.create(10, "Resolve?", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [
        vote(Outcome::Yes, 90, 1),
        vote(Outcome::No, 90, 1),
        vote(Outcome::No, 90, 1),
    ];
    expect_ok(world.submit(10, &evidence, &rules, votes, &world.keys.clone()));
    expect_err(
        send(
            &mut world.svm,
            &[resolve_ix(10, Outcome::Yes, &world.stranger)],
            &world.stranger,
        ),
        "NotEscalationOwner",
    );
    assert_eq!(world.market(10).status, verdict::Status::Escalated);
}

#[test]
fn protocol_owner_cannot_resolve_a_market() {
    let mut world = World::new();
    let (evidence, rules) = world.create(11, "Owner?", T0 + 5);
    set_time(&mut world.svm, T0 + 5);
    let votes = [
        vote(Outcome::Yes, 80, 1),
        vote(Outcome::Unclear, 80, 1),
        vote(Outcome::Yes, 80, 1),
    ];
    expect_ok(world.submit(11, &evidence, &rules, votes, &world.keys.clone()));
    expect_err(
        send(
            &mut world.svm,
            &[resolve_ix(11, Outcome::Yes, &world.owner)],
            &world.owner,
        ),
        "NotEscalationOwner",
    );
    assert_eq!(world.market(11).status, verdict::Status::Escalated);
    assert_ne!(
        world.market(11).escalation_owner,
        pk_of(&world.owner.pubkey().to_bytes())
    );
}

#[test]
fn updating_signers_does_not_retarget_an_existing_market() {
    let mut world = World::new();
    let (evidence, rules) = world.create(12, "Snapshot?", T0 + 50);
    let replacement = [
        SigningKey::from_bytes(&[4u8; 32]),
        SigningKey::from_bytes(&[5u8; 32]),
        SigningKey::from_bytes(&[6u8; 32]),
    ];
    let signers: [Pubkey; 3] = replacement
        .iter()
        .map(|key| pk_of(&key.verifying_key().to_bytes()))
        .collect::<Vec<_>>()
        .try_into()
        .unwrap();
    let ix = Instruction {
        program_id: addr(&verdict::ID),
        accounts: vec![
            AccountMeta::new(addr(&config_pda()), false),
            AccountMeta::new_readonly(world.owner.pubkey(), true),
        ],
        data: verdict::instruction::UpdateConfig {
            glm_signer: signers[0],
            qwen_signer: signers[1],
            deepseek_signer: signers[2],
            dispute_window_seconds: WINDOW,
            dispute_bond_lamports: BOND,
        }
        .data(),
    };
    let logs = expect_ok(send(&mut world.svm, &[ix], &world.owner));
    assert!(has_event::<ConfigUpdated>(&logs));

    let stranger_update = ix_update_stranger(&world);
    expect_err(
        send(&mut world.svm, &[stranger_update], &world.stranger),
        "ConstraintHasOne",
    );

    set_time(&mut world.svm, T0 + 50);
    let votes = [vote(Outcome::Yes, 90, 1); 3];
    expect_err(
        world.submit(12, &evidence, &rules, votes, &replacement),
        "WrongSigner",
    );
    let logs = expect_ok(world.submit(12, &evidence, &rules, votes, &world.keys.clone()));
    assert!(has_event::<VerdictSubmitted>(&logs));
    assert_eq!(world.market(12).status, verdict::Status::Proposed);
}

fn finalize_ix(market_id: u64) -> Instruction {
    Instruction {
        program_id: addr(&verdict::ID),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(market_id)), false),
            AccountMeta::new_readonly(sysvar_clock(), false),
        ],
        data: verdict::instruction::Finalize { market_id }.data(),
    }
}

fn submit_ix(market_id: u64, votes: [VoteInput; 3]) -> Instruction {
    Instruction {
        program_id: addr(&verdict::ID),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(market_id)), false),
            AccountMeta::new_readonly(addr(&config_pda()), false),
            AccountMeta::new_readonly(sysvar_instructions(), false),
            AccountMeta::new_readonly(sysvar_clock(), false),
        ],
        data: verdict::instruction::SubmitVerdict { market_id, votes }.data(),
    }
}

fn resolve_ix(market_id: u64, outcome: Outcome, signer: &Keypair) -> Instruction {
    Instruction {
        program_id: addr(&verdict::ID),
        accounts: vec![
            AccountMeta::new(addr(&market_pda(market_id)), false),
            AccountMeta::new_readonly(signer.pubkey(), true),
        ],
        data: verdict::instruction::EscalationResolve { market_id, outcome }.data(),
    }
}

fn ix_update_stranger(world: &World) -> Instruction {
    let signers = world.signer_pubkeys();
    Instruction {
        program_id: addr(&verdict::ID),
        accounts: vec![
            AccountMeta::new(addr(&config_pda()), false),
            AccountMeta::new_readonly(world.stranger.pubkey(), true),
        ],
        data: verdict::instruction::UpdateConfig {
            glm_signer: signers[0],
            qwen_signer: signers[1],
            deepseek_signer: signers[2],
            dispute_window_seconds: WINDOW,
            dispute_bond_lamports: BOND,
        }
        .data(),
    }
}
