use super::*;
use near_sdk::test_utils::{accounts, VMContextBuilder};
use near_sdk::testing_env;
use serde_json::Value;

const MODEL: &str = "Qwen/Qwen3.8-27B";
const DAY: u64 = 86_400 * 1_000_000_000;
fn vectors() -> Vec<Value> { serde_json::from_str(include_str!("../testdata/vectors.json")).unwrap() }
fn real() -> Value { serde_json::from_str(include_str!("../testdata/real_qwen_verdict.json")).unwrap() }
fn near(n: u128) -> NearToken { NearToken::from_near(n) }

fn ctx(who: usize, t: u64, dep: NearToken) {
    testing_env!(VMContextBuilder::new().predecessor_account_id(accounts(who)).block_timestamp(t).attached_deposit(dep).build());
}
fn addrs() -> Vec<String> {
    let v = vectors(); let mut a: Vec<String> = v.iter().map(|x| x["address"].as_str().unwrap().to_string()).collect();
    a.dedup(); a
}
/// owner=accounts(0); 3 resolvers, threshold 2 (>2/3 of 3 => 3? no: 2*3=6 > 6 false) -> use threshold 3 of 3 or 3-of-4
fn setup(threshold: u32) -> (Verdict, u32) {
    ctx(0, 0, near(0));
    let mut c = Verdict::new(accounts(0));
    for a in addrs() { c.register_signer(a, "ab".repeat(32)); }
    let id = c.create_market("Rain in Ikeja on 2026-11-03?".into(), ">=1mm per NiMet".into(), vec!["NiMet bulletin".into()],
        MODEL.into(), addrs(), threshold, DAY, DAY, near(1));
    (c, id)
}
fn vote(c: &mut Verdict, id: u32, v: &Value) -> Outcome {
    c.submit_verdict(id, v["request_hash"].as_str().unwrap().into(), v["response_raw"].as_str().unwrap().into(), v["signature"].as_str().unwrap().into())
}

#[test]
fn real_near_ai_signature_recovers_to_attested_tee_key() {
    let r = real();
    ctx(0, 0, near(0));
    let signer = recover_signer(r["text"].as_str().unwrap(), r["signature"].as_str().unwrap()).unwrap();
    assert_eq!(signer, r["signing_address"].as_str().unwrap().to_lowercase());
    // and the response hash in the signed text matches the stored response
    let h = hex::encode(env::sha256_array(r["response_raw"].as_str().unwrap().as_bytes()));
    assert!(r["text"].as_str().unwrap().ends_with(&h));
}

#[test]
fn real_near_ai_verdict_settles_single_resolver_market() {
    let r = real();
    ctx(0, 0, near(0));
    let mut c = Verdict::new(accounts(0));
    let a = r["signing_address"].as_str().unwrap().to_string();
    c.register_signer(a.clone(), "ab".repeat(32));
    let id = c.create_market("q".into(), "r".into(), vec!["s".into()], MODEL.into(), vec![a], 1, DAY, DAY, near(1));
    ctx(0, DAY, near(0));
    let o = c.submit_verdict(id, r["request_hash"].as_str().unwrap().into(), r["response_raw"].as_str().unwrap().into(), r["signature"].as_str().unwrap().into());
    assert_eq!(o, Outcome::Yes);
    assert!(matches!(c.get_market(id).unwrap().status, Status::Proposed { outcome: Outcome::Yes, .. }));
}

#[test]
#[should_panic(expected = "signer not attested/registered")]
fn tampered_byte_in_real_response_is_rejected() {
    let r = real();
    ctx(0, 0, near(0));
    let mut c = Verdict::new(accounts(0));
    let a = r["signing_address"].as_str().unwrap().to_string();
    c.register_signer(a.clone(), "ab".repeat(32));
    let id = c.create_market("q".into(), "r".into(), vec!["s".into()], MODEL.into(), vec![a], 1, DAY, DAY, near(1));
    let mut raw = r["response_raw"].as_str().unwrap().as_bytes().to_vec();
    let i = raw.windows(3).position(|w| w == b"YES").unwrap(); raw[i] = b'N'; raw[i + 1] = b'O'; raw[i + 2] = b' ';
    ctx(0, DAY, near(0));
    c.submit_verdict(id, r["request_hash"].as_str().unwrap().into(), String::from_utf8(raw).unwrap(), r["signature"].as_str().unwrap().into());
}

#[test]
#[should_panic(expected = "signer not attested/registered")]
fn single_flipped_byte_rejected() {
    let (mut c, id) = setup(3);
    let v = &vectors()[0];
    let mut raw = v["response_raw"].as_str().unwrap().as_bytes().to_vec(); raw[5] ^= 1;
    ctx(0, DAY, near(0));
    c.submit_verdict(id, v["request_hash"].as_str().unwrap().into(), String::from_utf8(raw).unwrap(), v["signature"].as_str().unwrap().into());
}

#[test]
#[should_panic(expected = "signer not attested/registered")]
fn unregistered_signer_rejected() {
    let (mut c, id) = setup(3);
    ctx(0, 0, near(0)); c.remove_signer(addrs()[0].clone());
    ctx(0, DAY, near(0)); vote(&mut c, id, &vectors()[0]);
}

#[test]
#[should_panic(expected = "owner only")]
fn only_owner_registers() { let (mut c, _) = setup(3); ctx(1, 0, near(0)); c.register_signer(addrs()[0].clone(), "ab".repeat(32)); }

#[test]
#[should_panic(expected = "all resolvers to agree")]
fn threshold_must_be_supermajority() { setup(2); }

#[test]
#[should_panic(expected = "already voted")]
fn duplicate_signer_rejected() {
    let (mut c, id) = setup(3); ctx(0, DAY, near(0));
    vote(&mut c, id, &vectors()[0]); vote(&mut c, id, &vectors()[0]);
}

#[test]
#[should_panic(expected = "market not ended")]
fn no_verdict_before_deadline() { let (mut c, id) = setup(3); ctx(0, 1, near(0)); vote(&mut c, id, &vectors()[0]); }

#[test]
fn split_panel_does_not_settle_then_full_flow_pays_out() {
    let (mut c, id) = setup(3);
    ctx(1, 1, near(3)); c.deposit(id, Outcome::Yes);
    ctx(2, 1, near(1)); c.deposit(id, Outcome::Yes);
    ctx(3, 1, near(4)); c.deposit(id, Outcome::No);
    let v = vectors();
    ctx(0, DAY, near(0));
    vote(&mut c, id, &v[0]); vote(&mut c, id, &v[1]);
    assert_eq!(vote(&mut c, id, &v[2]), Outcome::No); // key 3 says NO -> 2/3 YES -> escalate
    assert_eq!(c.get_market(id).unwrap().status, Status::Escalated);
}

#[test]
fn unanimous_panel_finalize_claim() {
    let (mut c, id) = setup(3);
    ctx(1, 1, near(3)); c.deposit(id, Outcome::Yes);
    ctx(2, 1, near(1)); c.deposit(id, Outcome::Yes);
    ctx(3, 1, near(4)); c.deposit(id, Outcome::No);
    let v = vectors();
    ctx(0, DAY, near(0));
    vote(&mut c, id, &v[0]); vote(&mut c, id, &v[1]); vote(&mut c, id, &v[3]);
    assert!(matches!(c.get_market(id).unwrap().status, Status::Proposed { outcome: Outcome::Yes, .. }));
    ctx(0, 2 * DAY, near(0)); c.finalize(id);
    ctx(1, 2 * DAY, near(0)); let _ = c.claim(id); // 3/4 of 8 = 6 NEAR
    assert_eq!(c.get_stake(id, accounts(1)), (near(0), near(0)));
}

#[test]
#[should_panic(expected = "dispute window open")]
fn cannot_finalize_during_window() {
    let (mut c, id) = setup(3); let v = vectors(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v[0]); vote(&mut c, id, &v[1]); vote(&mut c, id, &v[3]); c.finalize(id);
}

#[test]
fn dispute_with_bond_overturned_by_owner() {
    let (mut c, id) = setup(3); let v = vectors(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v[0]); vote(&mut c, id, &v[1]); vote(&mut c, id, &v[3]);
    ctx(4, DAY + 10, near(1)); c.dispute(id);
    assert!(matches!(c.get_market(id).unwrap().status, Status::Disputed { .. }));
    ctx(0, DAY + 20, near(0)); assert!(c.resolve_dispute(id, Outcome::No).is_some()); // bond refunded
    assert_eq!(c.get_market(id).unwrap().status, Status::Final { outcome: Outcome::No });
}

#[test]
#[should_panic(expected = "bond too small")]
fn dispute_needs_bond() {
    let (mut c, id) = setup(3); let v = vectors(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v[0]); vote(&mut c, id, &v[1]); vote(&mut c, id, &v[3]);
    ctx(4, DAY + 10, NearToken::from_millinear(1)); c.dispute(id);
}

#[test]
#[should_panic(expected = "nothing to claim")]
fn loser_cannot_claim() {
    let (mut c, id) = setup(3);
    ctx(3, 1, near(4)); c.deposit(id, Outcome::No);
    ctx(1, 1, near(1)); c.deposit(id, Outcome::Yes);
    let v = vectors(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v[0]); vote(&mut c, id, &v[1]); vote(&mut c, id, &v[3]);
    ctx(0, 2 * DAY, near(0)); c.finalize(id);
    ctx(3, 2 * DAY, near(0)); let _ = c.claim(id);
}

#[test]
fn attestation_hash_stored_with_signer() {
    let (c, _) = setup(3);
    assert_eq!(c.get_signer_attestation(addrs()[0].clone()), Some("ab".repeat(32)));
}

#[test]
fn parses_all_four_outcomes() {
    let w = |o: &str| format!(r#"{{"choices":[{{"message":{{"content":"{{\"outcome\":\"{o}\"}}"}}}}]}}"#);
    assert_eq!(parse_outcome(&w("TOO_EARLY")), Some(Outcome::TooEarly));
    assert_eq!(parse_outcome(&w("AMBIGUOUS")), Some(Outcome::Ambiguous));
    assert_eq!(parse_outcome(&w("NO")), Some(Outcome::No));
    assert_eq!(parse_outcome(&w("MAYBE")), None);
}
