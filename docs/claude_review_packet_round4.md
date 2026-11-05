# Verdict — review packet for Claude (round 4)

Local commits only; GitHub remote not updated this round. Times WAT (UTC+1).

## 1. Evidence lock — commit_evidence + submit_verdict
```rust
    /// Owner registers an account that may call `commit_evidence` (owner can always commit without registering).
    pub fn register_relayer(&mut self, account: AccountId) {
        self.only_owner();
        self.relayers.insert(account);
    }

    /// Lock the evidence bundle hash for the CURRENT round before any vote. Permissioned so a random
    /// NEAR AI key holder cannot race-submit fabricated evidence and lock out honest votes.
    /// The full evidence bundle MUST be published off-chain (same hash) so anyone can recompute.
    pub fn commit_evidence(&mut self, market_id: u32, round: u32, evidence_bundle_hash: String) {
        self.only_relayer();
        let h = hex32(&evidence_bundle_hash, "evidence_bundle_hash");
        let m = self.markets.get_mut(market_id).expect("no market");
        require!(m.status == Status::Open, "market not open");
        require!(round == m.round, "wrong round");
        require!(m.votes.is_empty(), "votes already cast this round");
        require!(m.round_evidence_hash.is_none(), "evidence already committed this round");
        m.round_evidence_hash = Some(h);
    }

    /// Anyone relays a signed resolver output. Evidence for this round MUST already be committed via
    /// `commit_evidence` (relayer/owner). The raw request bytes are hashed ON-CHAIN (not stored) and must
    /// contain the canonical header binding market_id / round / rules_hash / prompt_template_hash /
    /// evidence_bundle_hash (== the committed hash). Signed text = "<signer's model>:<sha256(request_raw)>:<sha256(response_raw)>".
    pub fn submit_verdict(&mut self, market_id: u32, request_raw: String, response_raw: String, signature: String) -> Outcome {
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
                    m.votes.clear(); m.round += 1; m.round_evidence_hash = None; Status::Open }
                _ => Status::Escalated, // split panel or unanimous AMBIGUOUS
            };
        }
        outcome
    }
```

## 2. parse_outcome — strict, structured (Rust; was previously printed as None)
```rust
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
```
```python
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
```

## 3. Tests
`cargo test`: 38 tests.
Names: real_signatures_from_all_three_models_recover_to_their_tee_keys, real_three_model_panel_settles_market, real_signer_registered_for_other_model_rejected, real_tampered_response_rejected, real_signature_cannot_be_replayed_on_another_market, wrong_market_rejected, wrong_rules_rejected, wrong_template_rejected, mismatched_evidence_in_round_rejected, evidence_edited_after_hashing_rejected, wrong_seed_rejected, nonzero_temperature_rejected, deepseek_key_signing_as_qwen_rejected, signer_model_stored, unregistered_key_rejected, removed_signer_rejected, same_signature_twice_rejected, second_distinct_vote_by_same_signer_rejected, too_early_votes_cannot_be_replayed_next_round, too_early_vote_itself_cannot_be_replayed, round_one_settles_after_too_early, prose_wrapped_json_rejected_on_chain, parse_outcome_is_structured_not_substring, real_responses_parse_identically_to_python, only_owner_registers, no_verdict_before_deadline, split_panel_escalates, unanimous_panel_finalize_claim, cannot_finalize_during_window, dispute_with_bond_overturned_by_owner, dispute_needs_bond, loser_cannot_claim, vote_before_commit_rejected, vote_with_uncommitted_evidence_rejected, non_relayer_commit_rejected, registered_relayer_can_commit, double_commit_rejected, too_early_clears_committed_evidence
```
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.09s
     Running unittests src/lib.rs (contract/target/debug/deps/verdict-84737343388f1d4f)

running 38 tests
test tests::evidence_edited_after_hashing_rejected - should panic ... ok
test tests::mismatched_evidence_in_round_rejected - should panic ... ok
test tests::cannot_finalize_during_window - should panic ... ok
test tests::deepseek_key_signing_as_qwen_rejected - should panic ... ok
test tests::dispute_needs_bond - should panic ... ok
test tests::double_commit_rejected - should panic ... ok
test tests::dispute_with_bond_overturned_by_owner ... ok
test tests::no_verdict_before_deadline - should panic ... ok
test tests::nonzero_temperature_rejected - should panic ... ok
test tests::non_relayer_commit_rejected - should panic ... ok
test tests::parse_outcome_is_structured_not_substring ... ok
test tests::real_responses_parse_identically_to_python ... ok
test tests::loser_cannot_claim - should panic ... ok
test tests::only_owner_registers - should panic ... ok
test tests::real_signatures_from_all_three_models_recover_to_their_tee_keys ... ok
test tests::real_signer_registered_for_other_model_rejected - should panic ... ok
test tests::prose_wrapped_json_rejected_on_chain - should panic ... ok
test tests::real_tampered_response_rejected - should panic ... ok
test tests::real_three_model_panel_settles_market ... ok
test tests::real_signature_cannot_be_replayed_on_another_market - should panic ... ok
test tests::round_one_settles_after_too_early ... ok
test tests::removed_signer_rejected - should panic ... ok
test tests::signer_model_stored ... ok
test tests::registered_relayer_can_commit ... ok
test tests::same_signature_twice_rejected - should panic ... ok
test tests::second_distinct_vote_by_same_signer_rejected - should panic ... ok
test tests::split_panel_escalates ... ok
test tests::too_early_clears_committed_evidence ... ok
test tests::too_early_votes_cannot_be_replayed_next_round - should panic ... ok
test tests::too_early_vote_itself_cannot_be_replayed - should panic ... ok
test tests::unregistered_key_rejected - should panic ... ok
test tests::vote_before_commit_rejected - should panic ... ok
test tests::vote_with_uncommitted_evidence_rejected - should panic ... ok
test tests::wrong_market_rejected - should panic ... ok
test tests::wrong_rules_rejected - should panic ... ok
test tests::wrong_template_rejected - should panic ... ok
test tests::wrong_seed_rejected - should panic ... ok
test tests::unanimous_panel_finalize_claim ... ok

test result: ok. 38 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.26s

   Doc-tests verdict

running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s


```
Evidence-lock tests: `vote_before_commit_rejected`, `vote_with_uncommitted_evidence_rejected`, `non_relayer_commit_rejected`, `registered_relayer_can_commit`, `double_commit_rejected`, plus prior binding/replay/per-signer-model regressions.

## 4. Shared-signer / replica policy
```python
(missing)
```
Label **Hardware + host stack verified** only if EVERY observed attestation entry for that signing_address+model passes. Otherwise all votes from that key are downgraded to `Signature valid · hardware check pending`.
mr-kms + key-provider RTMR3 events are pinned in `scripts/expected_measurements.json`.
compose-manager digests pinned: `6e035c8f…` and `a320bb48…756a` (Docker Hub nearaidev/compose-manager; tags include prod-20261029-be649f8; confirmed 2026-11-05 WAT). Docker Hub `latest` a0c07308… is NOT pinned (not observed on panel nodes).

## 5. Benchmarks
### Post-cutoff sports (prior)
### Scaled benchmark (round 3) — `scripts/bench_scale.py`, run 2026-11-05 WAT

**Set**: 124 UMA-resolved binary Polymarket sports markets (fetched from Gamma API, closed 2026-10-18 … 11-04), seeded random pick (seed 1114), one market per game for NHL/NFL, ≤2 per soccer game. **All 124 have their game day (US Eastern) after 2026-10-17**, i.e. post-cutoff for every panel model by the upper-bound method in `docs/CUTOFFS.md`. No pre-cutoff markets are in this set; the 5 legacy markets below are the pre-cutoff reference.

| Series / type | Markets | Panel settled (unanimous) | Settled = UMA |
|---|---|---|---|
| NHL moneyline | 45 | 44 | 44 |
| NFL moneyline | 25 | 25 | 25 |
| NFL full-game spread | 12 | 12 | 12 |
| NFL full-game O/U | 12 | 12 | 12 |
| German soccer (DFB-Pokal, Bundesliga, 2. Bundesliga) | 30 | 14 | 14 |
| **Total (post-cutoff)** | **124** | **107 (86.3%)** | **107 / 107 (100%)** |

- **Headline (post-cutoff)**: when the panel settles, it matched UMA 107/107; 17/124 escalated, 0 panel–UMA disagreements.
- Per model vs UMA (124): Qwen 108 agree / 0 disagree / 16 abstain-or-invalid; DeepSeek 110 / 0 / 14; GLM 114 / 0 / 10.
- **Sources**: 2 independent sources per market, all 124 found both. NHL = ESPN scoreboard + NHL official API (api-web.nhle.com); NFL = ESPN + nflverse/nfldata games.csv; soccer = ESPN + OpenLigaDB. Fetched by the relayer outside the TEE, stored as extracted game objects with sha256 inside the signed request.
- **Escalations (17)**: 2 were Qwen outputs rejected by the strict parser (malformed JSON: unescaped quotes in `quotes`); the other 15 are soccer sub-markets (first-half O/U, total corners, first scorer) that slipped past the type filter. Our sources have no corner data, and half-time scores come from OpenLigaDB only, so under the 2-independent-sources rule AMBIGUOUS/escalation is the expected result, not a model error.
- **Caveat**: these are easy, unambiguous final-score markets with clean structured sources. 100% on settled markets says the pipeline is correct on easy cases. It is not evidence about hard or disputed markets.
- **Trust labels**: all 372 votes were `Hardware + host stack verified`. Each signer was attested once at the start of the run, not per call.
- **Cost**: $0.274 for 372 calls (124 × 3), about $0.0022/market. Responses are cached per (market, model) in `out/bench2/runs/`; reruns skip cached calls.

### Legacy 5 markets (pre-cutoff reference + Vanaken rerun), `scripts/bench.py`
| Market | UMA | Panel |
|---|---|---|
| Netanyahu out before April? (disputed) | NO | NO |
| U.S. recognize Syrian gov by Mar 31? (disputed) | NO | NO |
| Hans Vanaken 1+ shots on target (disputed, post-cutoff) | YES | ESCALATE (all 3 AMBIGUOUS: single unofficial ESPN source) |
| Bitcoin dip to $80k by Mar 31? | YES | YES |
| Assad out by Mar 31, 2025? | YES | YES |

Vanaken: under the new rule (sports stats need 2+ independent sources; an all-zero single-source line is missing data) the panel abstains instead of answering NO. We do not claim UMA was wrong. Legacy cost this rerun: $0.0114 (3 markets' responses reused from cache).

Finding (updated round 4): Qwen signer `0x835ab53d…aa26` was also observed with compose-manager digest `a320bb48…756a`. Provenance confirmed on Docker Hub `nearaidev/compose-manager` (tags `prod-20261029-be649f8`, `staging-20261029-be649f8`, `staging-20261021-754d65c`; fetched 2026-11-05 WAT). Both `6e035c8f…` and `a320bb48…` are now allowlisted. Docker Hub `latest`/`prod` digest `a0c07308…` (2026-11-04) is **not** pinned because it has not been observed on panel nodes. Shared-signer policy: a key is fully verified only if EVERY observed replica for that key passes.


### UMA-disputed post-cutoff
# UMA-disputed post-cutoff benchmark (round 4)

**Run**: `scripts/bench_disputed.py`, 2026-11-05 WAT. Cost ≈ $0.1008 (102 calls).

## Classification (transparent)
Markets whose Gamma `umaResolutionStatuses` **history string contains `"disputed"`**, final `umaResolutionStatus == "resolved"`, binary `outcomePrices` in `{["1","0"],["0","1"]}`, and `endDate` **after 2026-10-17** (panel cutoff upper bound). Sorted by volume, capped at 40 → **34 markets**.

This is **not** a claim that UMA's final resolution was wrong. "Disputed" means the UMA process had a dispute stage before resolving.

## Evidence (honest thinness)
Fetched **outside the TEE**:
1. Always: market rules/description snapshot (does **not** count as an independent fact source).
2. `resolutionSource` URL body when present and fetchable.
3. Wikipedia extract when a simple title can be derived from the question.

Resulting independent fact sources: most geopolitical markets had **0** external snapshots (rules only); a minority of sports/election titles got a Wikipedia page. Under Verdict's evidence rules the panel is expected to abstain when facts are insufficient.

## Panel result
| Metric | Value |
|---|---|
| Markets | 34 |
| Panel settled (unanimous YES/NO) | **0** |
| Agree / disagree with UMA (among settled) | n/a (0 settled) |
| Escalated | **34 / 34** |
| Panel–UMA disagreements | **0** (no settled votes to disagree) |

Vote-level outcomes were overwhelmingly `AMBIGUOUS` (insufficient evidence), with occasional `TOO_EARLY` / split labels → market-level **ESCALATE**. The panel did **not** invent resolutions for hard disputed geopolitics from rules text alone.

Caveat: this measures **abstention quality under thin evidence**, not accuracy on disputed markets with rich sources. A follow-up with curated multi-source bundles for a subset is future work.

## Rule linter on the same 34
`scripts/rule_linter.py` heuristics v1.0 (no model calls):

**Linter flagged 27 / 34 disputed markets before launch.**

Common hits: `missing_source`, `or_without_precedence`, `missing_timezone`, `subjective_qualifier`, `unofficial_ok`. The Polymarket boilerplate phrase "This market will resolve…" is **not** treated as circular self-reference; only community-vote / price-circular language is.

A flag means the rule text matched a known ambiguity pattern. It is **not** a prediction that UMA will dispute, and the 27/34 rate is not manufactured to look good — several clean sports markets in the set were not flagged.

## Trust labels
Shared-signer replica policy applied (`verify_signer_replicas`). See run artifacts under `out/bench_disputed/` (gitignored).

## Vote-level breakdown (102 calls)
- AMBIGUOUS: 93
- TOO_EARLY: 6
- INVALID: 2
- NO: 1
- YES: 0

Only 1 market had any YES/NO vote at all; never unanimous. Independent fact sources: 23 markets with 0, 11 with 1 (Wikipedia or resolutionSource). All 102 votes labelled `Hardware + host stack verified` under the shared-signer replica policy.


## 6. Deploy / offline
# Minimal NEAR testnet deploy (manual wallet signing)

This repo has **no NEAR private keys or seed phrases**. Deploy requires YOU to sign
with your own testnet wallet. Do not paste a private key into chat or into any
script in this repo.

## Prerequisites
- A NEAR **testnet** account with some testnet NEAR (https://testnet.mynearwallet.com or faucet)
- `cargo-near` (already used to build) and `near-cli-rs` (`cargo install near-cli-rs`)
- Built wasm: `contract/target/near/verdict.wasm` (see below)

## 1. Build wasm (no keys needed)
```bash
cd contract
cargo near build non-reproducible-wasm
# or: cargo build --target wasm32-unknown-unknown --release
ls -la target/near/verdict.wasm   # cargo-near path
```

## 2. Create / fund a contract account (YOU sign)
```bash
# Example: create sub-account under your testnet account
near account create-account sponsor-by-faucet-service verdict.<your>.testnet \
  autogenerate-new-keypair save-to-keychain network-config testnet create
```
Or use an existing empty account you control.

## 3. Deploy
```bash
near contract deploy <contract.account.testnet> use-file contract/target/near/verdict.wasm \
  without-init-call network-config testnet sign-with-keychain send
```

## 4. Init + register signers + one market + settle
Use view/call against the ABI. Signer addresses and models come from a verified
`python scripts/attest_demo.py` run (or `contract/testdata/real_panel.json`):

```bash
# init
near contract call-function as-transaction <contract> new \
  json-args '{"owner":"<your>.testnet"}' prepaid-gas '30.0 Tgas' attached-deposit '0 NEAR' \
  sign-as <your>.testnet network-config testnet sign-with-keychain send

# register_signer for each panel address (address, model, attestation_report_sha256)
# register_relayer <your>.testnet
# create_market ...
# commit_evidence ...
# submit_verdict x3 ...
```

Exact helper args are printed by `python scripts/deploy_prepare.py` (reads
`contract/testdata/real_panel.json` + a published evidence hash; does NOT sign).

## 5. Put explorer links in README
After each successful tx, paste the NearBlocks / Near Explorer URL into README
under **Testnet deploy**. Example form:
`https://testnet.nearblocks.io/txns/<hash>`

## Blocker (current)
This agent box has `NEARAI_API_KEY` but **no NEAR wallet credentials**. Deploy
stops here until you run the signed steps above on a machine with your keychain.


Offline: `docs/SETUP.md` + `python scripts/offline_demo.py` (cargo test + EIP-191 verify of `contract/testdata/real_panel.json` + rebuild `scoreboard/index.html` from `fixtures/scoreboard_cache/`).

## 7. README Limits
See full README. New this round: evidence must be committed by relayer/owner before votes; KMS/key-provider trust; model container still not measured; testnet deploy blocked pending user wallet signature.
