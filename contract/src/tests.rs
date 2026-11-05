use super::*;
use near_sdk::test_utils::{accounts, VMContextBuilder};
use near_sdk::testing_env;
use serde_json::Value;

const DAY: u64 = 86_400 * 1_000_000_000;
fn vec_file() -> Value { serde_json::from_str(include_str!("../testdata/vectors.json")).unwrap() }
fn real() -> Value { serde_json::from_str(include_str!("../testdata/real_panel.json")).unwrap() }
fn v(name: &str) -> Value { vec_file()["votes"][name].clone() }
fn near(n: u128) -> NearToken { NearToken::from_near(n) }
fn s(x: &Value, k: &str) -> String { x[k].as_str().unwrap().to_string() }

fn ctx(who: usize, t: u64, dep: NearToken) {
    testing_env!(VMContextBuilder::new().predecessor_account_id(accounts(who)).block_timestamp(t).attached_deposit(dep).build());
}
/// Synthetic panel: test keys 0,1,2 registered as Qwen / DeepSeek / GLM. Key 3 is never registered.
fn setup() -> (Verdict, u32) {
    let f = vec_file();
    ctx(0, 0, near(0));
    let mut c = Verdict::new(accounts(0));
    let sg = f["signers"].as_array().unwrap();
    for x in &sg[..3] { c.register_signer(s(x, "address"), s(x, "model"), "ab".repeat(32)); }
    let rs = sg[..3].iter().map(|x| s(x, "address")).collect();
    let id = c.create_market(s(&f, "question"), s(&f, "rules"), vec!["NiMet bulletin".into()], s(&f, "prompt_template_hash"), rs, DAY, DAY, near(1));
    assert_eq!(id, 0);
    (c, id)
}
fn vote(c: &mut Verdict, id: u32, x: &Value) -> Outcome {
    c.submit_verdict(id, s(x, "request_raw"), s(x, "response_raw"), s(x, "signature"))
}
fn unanimous_yes(c: &mut Verdict, id: u32) { for i in 0..3 { vote(c, id, &v(&format!("yes_{i}"))); } }

// ---------- REAL signatures from all three NEAR AI panel models ----------
fn real_setup(model_override: Option<(usize, &str)>) -> (Verdict, u32, Value) {
    let r = real();
    ctx(0, 0, near(0));
    let mut c = Verdict::new(accounts(0));
    let votes = r["votes"].as_array().unwrap();
    for (i, x) in votes.iter().enumerate() {
        let model = match model_override { Some((j, m)) if j == i => m.to_string(), _ => s(x, "model") };
        c.register_signer(s(x, "signing_address"), model, s(x, "attestation_report_sha256"));
    }
    let rs = votes.iter().map(|x| s(x, "signing_address")).collect();
    let id = c.create_market(s(&r, "question"), s(&r, "rules"), vec!["fixture".into()], s(&r, "prompt_template_hash"), rs, DAY, DAY, near(1));
    ctx(0, DAY, near(0));
    (c, id, r)
}

#[test]
fn real_signatures_from_all_three_models_recover_to_their_tee_keys() {
    ctx(0, 0, near(0));
    let r = real();
    let models: Vec<String> = r["votes"].as_array().unwrap().iter().map(|x| s(x, "model")).collect();
    assert_eq!(models, vec!["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]);
    for x in r["votes"].as_array().unwrap() {
        let req_h = hex::encode(env::sha256_array(s(x, "request_raw").as_bytes()));
        let resp_h = hex::encode(env::sha256_array(s(x, "response_raw").as_bytes()));
        let text = format!("{}:{}:{}", s(x, "model"), req_h, resp_h);
        assert_eq!(text, s(x, "text"));
        assert_eq!(recover_signer(&text, &s(x, "signature")).unwrap(), s(x, "signing_address").to_lowercase());
    }
}

#[test]
fn real_three_model_panel_settles_market() {
    let (mut c, id, r) = real_setup(None);
    for x in r["votes"].as_array().unwrap() { assert_eq!(vote(&mut c, id, x), Outcome::Yes); }
    let m = c.get_market(id).unwrap();
    assert!(matches!(m.status, Status::Proposed { outcome: Outcome::Yes, .. }));
    assert_eq!(m.votes.iter().map(|v| v.model.clone()).collect::<Vec<_>>(),
               vec!["Qwen/Qwen3.8-27B", "deepseek-ai/DeepSeek-V4.1-Flash-1017", "z-ai/glm-5.3-flash"]);
}

#[test]
#[should_panic(expected = "signer is registered for a different model")]
fn real_signer_registered_for_other_model_rejected() {
    // GLM's TEE key registered as if it served Qwen: its genuine GLM signature must not count.
    let (mut c, id, r) = real_setup(Some((2, "Qwen/Qwen3.8-27B")));
    vote(&mut c, id, &r["votes"][2]);
}

#[test]
#[should_panic(expected = "signer not attested/registered")]
fn real_tampered_response_rejected() {
    let (mut c, id, r) = real_setup(None);
    let x = &r["votes"][0];
    let raw = s(x, "response_raw").replacen("YES", "NO ", 1);
    c.submit_verdict(id, s(x, "request_raw"), raw, s(x, "signature"));
}

#[test]
#[should_panic(expected = "wrong market_id")]
fn real_signature_cannot_be_replayed_on_another_market() {
    let (mut c, _id, r) = real_setup(None);
    ctx(0, 0, near(0));
    let rs = r["votes"].as_array().unwrap().iter().map(|x| s(x, "signing_address")).collect();
    let id2 = c.create_market(s(&r, "question"), s(&r, "rules"), vec!["fixture".into()], s(&r, "prompt_template_hash"), rs, DAY, DAY, near(1));
    ctx(0, DAY, near(0));
    vote(&mut c, id2, &r["votes"][0]);
}

// ---------- request binding (item 1) ----------
#[test]
#[should_panic(expected = "wrong market_id")]
fn wrong_market_rejected() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("wrong_market")); }

#[test]
#[should_panic(expected = "wrong rules_hash")]
fn wrong_rules_rejected() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("wrong_rules")); }

#[test]
#[should_panic(expected = "wrong prompt template")]
fn wrong_template_rejected() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("wrong_template")); }

#[test]
#[should_panic(expected = "evidence bundle differs from this round's")]
fn mismatched_evidence_in_round_rejected() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v("yes_0"));
    vote(&mut c, id, &v("other_evidence_1"));
}

#[test]
#[should_panic(expected = "evidence bundle does not hash to header")]
fn evidence_edited_after_hashing_rejected() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    let x = v("yes_0");
    let req = s(&x, "request_raw").replacen("Ikeja 4.2mm", "Ikeja 0.0mm", 1);
    c.submit_verdict(id, req, s(&x, "response_raw"), s(&x, "signature"));
}

#[test]
#[should_panic(expected = "request seed must be the fixed seed")]
fn wrong_seed_rejected() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("wrong_seed")); }

#[test]
#[should_panic(expected = "request temperature must be 0")]
fn nonzero_temperature_rejected() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("nonzero_temperature")); }

// ---------- signer -> model (item 2) ----------
#[test]
#[should_panic(expected = "signer is registered for a different model")]
fn deepseek_key_signing_as_qwen_rejected() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("model_swap_1")); }

#[test]
fn signer_model_stored() {
    let (c, _) = setup();
    let f = vec_file();
    assert_eq!(c.get_signer_model(s(&f["signers"][1], "address")), Some("deepseek-ai/DeepSeek-V4.1-Flash-1017".into()));
    assert_eq!(c.get_signer_attestation(s(&f["signers"][0], "address")), Some("ab".repeat(32)));
}

#[test]
#[should_panic(expected = "signer not attested/registered")]
fn unregistered_key_rejected() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("yes_3")); }

#[test]
#[should_panic(expected = "signer not attested/registered")]
fn removed_signer_rejected() {
    let (mut c, id) = setup();
    ctx(0, 0, near(0)); c.remove_signer(s(&vec_file()["signers"][0], "address"));
    ctx(0, DAY, near(0)); vote(&mut c, id, &v("yes_0"));
}

// ---------- replay (item 3) ----------
#[test]
#[should_panic(expected = "signed response already used")]
fn same_signature_twice_rejected() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v("yes_0")); vote(&mut c, id, &v("yes_0"));
}

#[test]
#[should_panic(expected = "signer already voted")]
fn second_distinct_vote_by_same_signer_rejected() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v("yes_0")); vote(&mut c, id, &v("no_0"));
}

fn too_early_round(c: &mut Verdict, id: u32) {
    for i in 0..3 { vote(c, id, &v(&format!("too_early_r0_{i}"))); }
    let m = c.get_market(id).unwrap();
    assert_eq!((m.round, m.votes.len(), m.status.clone(), m.round_evidence_hash.clone()), (1, 0, Status::Open, None));
}

#[test]
#[should_panic(expected = "wrong round")]
fn too_early_votes_cannot_be_replayed_next_round() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    too_early_round(&mut c, id);
    vote(&mut c, id, &v("yes_0")); // round-0 signed YES: rejected in round 1
}

#[test]
#[should_panic(expected = "wrong round")]
fn too_early_vote_itself_cannot_be_replayed() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    too_early_round(&mut c, id);
    vote(&mut c, id, &v("too_early_r0_0"));
}

#[test]
fn round_one_settles_after_too_early() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    too_early_round(&mut c, id);
    for i in 0..3 { vote(&mut c, id, &v(&format!("yes_r1_{i}"))); }
    assert!(matches!(c.get_market(id).unwrap().status, Status::Proposed { outcome: Outcome::Yes, .. }));
}

// ---------- strict outcome parsing (item 4) ----------
#[test]
#[should_panic(expected = "no valid outcome in response")]
fn prose_wrapped_json_rejected_on_chain() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); vote(&mut c, id, &v("prose_wrapped_0")); }

#[test]
fn parse_outcome_is_structured_not_substring() {
    ctx(0, 0, near(0));
    let o = |n: &str| parse_outcome(&s(&v(n), "response_raw"));
    assert_eq!(o("substring_trap_0"), Some(Outcome::No)); // the inner "YES" text is ignored
    assert_eq!(o("prose_wrapped_0"), None);
    assert_eq!(o("truncated_0"), None);         // finish_reason=length
    assert_eq!(o("lowercase_0"), None);         // enum is exact
    assert_eq!(o("fenced_yes_2"), Some(Outcome::Yes)); // one whole ```json fence allowed
    let w = |c: &str| format!(r#"{{"choices":[{{"finish_reason":"stop","message":{{"role":"assistant","content":{}}}}}]}}"#, serde_json::to_string(c).unwrap());
    assert_eq!(parse_outcome(&w("\n\n{\"outcome\":\"TOO_EARLY\"}")), Some(Outcome::TooEarly));
    assert_eq!(parse_outcome(&w("{\"outcome\":\"AMBIGUOUS\"}")), Some(Outcome::Ambiguous));
    assert_eq!(parse_outcome(&w("{\"outcome\":\"MAYBE\"}")), None);
    assert_eq!(parse_outcome(&w("[{\"outcome\":\"YES\"}]")), None);
    assert_eq!(parse_outcome(&w("{\"outcome\":\"YES\"} {\"outcome\":\"NO\"}")), None);
    assert_eq!(parse_outcome(&w("```json\n{\"outcome\":\"YES\"}")), None); // unterminated fence
    let two = r#"{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"{\"outcome\":\"YES\"}"}},{"finish_reason":"stop","message":{"role":"assistant","content":"{\"outcome\":\"NO\"}"}}]}"#;
    assert_eq!(parse_outcome(two), None);
}

#[test]
fn real_responses_parse_identically_to_python() {
    ctx(0, 0, near(0));
    for x in real()["votes"].as_array().unwrap() {
        let py = x["parsed_outcome"].as_str().unwrap();
        let rs = match parse_outcome(&s(x, "response_raw")).unwrap() { Outcome::Yes => "YES", Outcome::No => "NO", Outcome::TooEarly => "TOO_EARLY", Outcome::Ambiguous => "AMBIGUOUS" };
        assert_eq!(rs, py);
    }
}

// ---------- market lifecycle ----------
#[test]
#[should_panic(expected = "owner only")]
fn only_owner_registers() { let (mut c, _) = setup(); ctx(1, 0, near(0)); c.register_signer(s(&vec_file()["signers"][3], "address"), "m".into(), "ab".repeat(32)); }

#[test]
#[should_panic(expected = "market not ended")]
fn no_verdict_before_deadline() { let (mut c, id) = setup(); ctx(0, 1, near(0)); vote(&mut c, id, &v("yes_0")); }

#[test]
fn split_panel_escalates() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0));
    vote(&mut c, id, &v("yes_0")); vote(&mut c, id, &v("yes_1"));
    assert_eq!(vote(&mut c, id, &v("no_2")), Outcome::No);
    assert_eq!(c.get_market(id).unwrap().status, Status::Escalated);
}

#[test]
fn unanimous_panel_finalize_claim() {
    let (mut c, id) = setup();
    ctx(1, 1, near(3)); c.deposit(id, Outcome::Yes);
    ctx(2, 1, near(1)); c.deposit(id, Outcome::Yes);
    ctx(3, 1, near(4)); c.deposit(id, Outcome::No);
    ctx(0, DAY, near(0)); unanimous_yes(&mut c, id);
    assert!(matches!(c.get_market(id).unwrap().status, Status::Proposed { outcome: Outcome::Yes, .. }));
    ctx(0, 2 * DAY, near(0)); c.finalize(id);
    ctx(1, 2 * DAY, near(0)); let _ = c.claim(id);
    assert_eq!(c.get_stake(id, accounts(1)), (near(0), near(0)));
}

#[test]
#[should_panic(expected = "dispute window open")]
fn cannot_finalize_during_window() { let (mut c, id) = setup(); ctx(0, DAY, near(0)); unanimous_yes(&mut c, id); c.finalize(id); }

#[test]
fn dispute_with_bond_overturned_by_owner() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0)); unanimous_yes(&mut c, id);
    ctx(4, DAY + 10, near(1)); c.dispute(id);
    assert!(matches!(c.get_market(id).unwrap().status, Status::Disputed { .. }));
    ctx(0, DAY + 20, near(0)); assert!(c.resolve_dispute(id, Outcome::No).is_some());
    assert_eq!(c.get_market(id).unwrap().status, Status::Final { outcome: Outcome::No });
}

#[test]
#[should_panic(expected = "bond too small")]
fn dispute_needs_bond() {
    let (mut c, id) = setup(); ctx(0, DAY, near(0)); unanimous_yes(&mut c, id);
    ctx(4, DAY + 10, NearToken::from_millinear(1)); c.dispute(id);
}

#[test]
#[should_panic(expected = "nothing to claim")]
fn loser_cannot_claim() {
    let (mut c, id) = setup();
    ctx(3, 1, near(4)); c.deposit(id, Outcome::No);
    ctx(1, 1, near(1)); c.deposit(id, Outcome::Yes);
    ctx(0, DAY, near(0)); unanimous_yes(&mut c, id);
    ctx(0, 2 * DAY, near(0)); c.finalize(id);
    ctx(3, 2 * DAY, near(0)); let _ = c.claim(id);
}
