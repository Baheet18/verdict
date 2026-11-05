# Verdict — review packet for Claude (round 5)

Local commits only; GitHub remote not updated this round. Times WAT (UTC+1).
Self-review applied: permissionless influence (request keys + evidence + submit), replica labels, offline reproducibility, committed numbers only.

## 1. CRITICAL — request-parameter steering (exact key allowlist)
```rust
/// Validate the raw chat-completions request body against the market. Returns (model, evidence_bundle_hash).
/// Exact key allowlist (round 5): top-level keys MUST be exactly {model, messages, temperature, seed}.
/// Each message MUST be exactly {role, content}. User content MUST be canonical header + "\n" + evidence.
/// Extra keys (logit_bias, tools, response_format, …) are rejected so an attacker cannot steer a
/// committed evidence+header into a forced YES via sampling / guided decoding params.
/// Panics with a specific reason on any mismatch.
pub fn check_request(request_raw: &str, market_id: u32, m: &Market) -> (String, String) {
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
```
Top-level keys MUST be exactly `{model, messages, temperature, seed}` (max_tokens omitted by policy).
Each message MUST be exactly `{role, content}`. User content = canonical header + `\n` + evidence.
Forbidden-key rejection tests (one each): logit_bias, guided_choice, guided_regex, response_format, structured_outputs, tools, tool_choice, stop, n, top_p (+ forbidden message key name).
Defense in depth: `submit_verdict` is relayer/owner only (same trust as `commit_evidence`).
```rust
    fn only_relayer(&self) {
        require!(self.is_relayer(env::predecessor_account_id()), "relayer or owner only");
    }
```

## 2. commit_evidence — deadline + owner replace + evidence_uri
```rust
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
```
```rust
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
```

## 3. parse_outcome (structured) + verify_signer_replicas (was missing in packet)
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
def verify_signer_replicas(model_attestations, signing_address: str, requested_model: str, nonce: str):
    """Shared-key policy (round 4): a signing key is "Hardware + host stack verified" only if
    EVERY observed attestation entry for that key (+ model) passes verify_node.

    A signature cannot prove which replica signed. If any replica fails (e.g. unknown
    compose-manager digest), ALL votes from this key are downgraded.
    Returns {label, entries_checked, failures, results}.
    """
    want = signing_address.lower()
    entries = [
        e for e in (model_attestations or [])
        if (e.get("signing_address") or "").lower() == want and e.get("model_name") == requested_model
    ]
    check(len(entries) >= 1, f"at least one attestation entry for signer {want} model {requested_model}")
    results, failures = [], []
    for i, e in enumerate(entries):
        print(f"[hw] replica {i+1}/{len(entries)} for {want}")
        try:
            results.append(verify_node(e, nonce, model=requested_model))
        except Exception as ex:
            failures.append({"index": i, "error": str(ex)})
            print(f"  !!  replica {i+1} FAILED: {ex}")
    if failures:
        label = "Signature valid · hardware check pending"
    else:
        label = "Hardware + host stack verified"
    print(f"[label] {label} (replicas_ok={len(results)}/{len(entries)})")
    return {
        "label": label,
        "entries_checked": len(entries),
        "failures": failures,
        "results": results,
    }
```

## 4. Tests
`cargo test`: 54 tests.
```

running 54 tests
......................................................
test result: ok. 54 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.37s


running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s


```

## 5. Linter control set (matched non-disputed)
```json
{
  "disputed_flagged": 27,
  "disputed_n": 34,
  "control_flagged": 26,
  "control_n": 34,
  "gap": 0.02941176470588236,
  "selection_path": "/workspace/verdict/out/lint_control/selection.json",
  "use_in_readme": false
}
```
Selection written to `out/lint_control/selection.json` BEFORE lint outcomes. Gap < 0.10 → **do not** use “flagged X/34 disputed vs Y/34 non-disputed” as a headline claim.

## 6. Disputed benchmarks
### Thin evidence (rules-only) — prior
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


## Matched non-disputed control (round 5)
Selection method (written to `out/lint_control/selection.json` **before** lint outcomes): same endDate window (>2026-10-17), resolved binary, history without `"disputed"`, 1:1 match to the 34 disputed markets by keyword-bucket + closest log10(volume).

| Set | Flagged |
|---|---|
| Disputed | **27 / 34** |
| Non-disputed control | **26 / 34** |
| Gap | ≈ 0.029 |

**Not used as a headline.** The gap is below the 0.10 threshold in `scripts/lint_control_set.py`; the linter flags ambiguity patterns that appear in both disputed and clean markets (especially sports boilerplate). Script: `python scripts/lint_control_set.py`.


### Curated multi-source subset
# Curated multi-source disputed subset (round 5)

Generated from `scripts/curate_disputed_evidence.py` + panel run.

## Method

From the 34 UMA-disputed post-cutoff markets (`docs/BENCHMARK_DISPUTED.md`), attempt real HTTP fetches of **≥1 official/primary URL + ≥2 reputable reports** per market.
Markets that cannot meet the fetch bar are **skipped** (not fabricated).
Published bundles: `fixtures/evidence/<sha256>.json` indexed by `fixtures/evidence/MANIFEST.json` (source URLs + hashes).
Evidence is fetched **outside the TEE**; the contract binds `evidence_bundle_hash` via `commit_evidence`.

## Headline numbers

| Metric | Value |
|---|---|
| Curated markets kept | **12** |
| Settles with curated evidence (unanimous YES/NO) | **0** |
| Abstains with curated evidence | **12** |
| Same IDs abstain on thin (rules-only) evidence | **12** |
| Invented answers | **0** |
| Agree UMA among settled | 0 |
| Disagree UMA among settled | 0 |
| Cost USD (panel) | $0.1055 |

**Reading**: richer real fetches still did not yield unanimous settles — many official/report pages returned JS shells or boilerplate without the resolution fact. The panel correctly abstained rather than inventing. One market (Hawks vs BayStars) had 2×YES + 1×AMBIGUOUS → escalate (not auto-settle).

## Per-market

| id | UMA | thin | curated | facts | question |
|---|---|---|---|---|---|
| 1235552 | NO | ESCALATE | ESCALATE | 3 | Will the Cleveland Guardians win the 2026 World Series? |
| 608558 | NO | ESCALATE | ESCALATE | 3 | Will Raphinha win the 2026 Ballon d'Or? |
| 608564 | NO | ESCALATE | ESCALATE | 3 | Will Cristiano Ronaldo win the 2026 Ballon d'Or? |
| 5180406 | NO | ESCALATE | ESCALATE | 3 | Will Apple be the second-largest company in the world by market cap on |
| 5166966 | NO | ESCALATE | ESCALATE | 3 | Will NVIDIA be the third-largest company in the world by market cap on |
| 5166968 | NO | ESCALATE | ESCALATE | 3 | Will Apple be the third-largest company in the world by market cap on  |
| 5648379 | YES | ESCALATE | ESCALATE | 3 | Visa (V) Q4 payments volume growth (constant dollars) 9.5% to 10.5%? |
| 5648378 | NO | ESCALATE | ESCALATE | 3 | Visa (V) Q4 payments volume growth (constant dollars) 8.5% to 9.5%? |
| 1281165 | YES | ESCALATE | ESCALATE | 3 | Will the Democratic Party win the CA-34 House seat? |
| 1284815 | YES | ESCALATE | ESCALATE | 3 | Will the Republican Party win the TX-02 House seat? |
| 5969230 | YES | ESCALATE | ESCALATE | 3 | Fukuoka SoftBank Hawks vs. Yokohama BayStars |
| 5285913 | YES | ESCALATE | ESCALATE | 3 | Blues vs. Red Wings: O/U 4.5 |

## Reproduce
```bash
python scripts/curate_disputed_evidence.py summary
# live re-run (needs NEARAI_API_KEY): python scripts/curate_disputed_evidence.py run
```


## 7. check_new_digest (DIFF only; no auto-allowlist)
```json
{
  "pinned_digests": [
    "sha256:079a14b41deb11e0e27f9b69648e8b3d89bd6fb9ccf6071643b8f447fa415085",
    "sha256:6e035c8fb99c1d31f41760d25af178683a658cca4afd73cd0d366fbb5bf884be",
    "sha256:742dbd2e61c8709b930712c38958386c3cb3928e09eeb1f1e490600c127e2edb",
    "sha256:85ac41c2db88d0df9bd6145e608a3cb023f5d8443868adbfbbf66efb51087917",
    "sha256:a320bb4882341c8e19ef1944436f6591ed73303732009ed5db9f6db6c9da756a"
  ],
  "watched": [
    {
      "repo": "nearaidev/compose-manager",
      "tags": {
        "latest": {
          "found": true,
          "last_updated": "2026-11-04T04:11:50.268866Z",
          "digests": [
            "sha256:a0c073085684e12e19f31bb279fc8da663a374199bfecc47b04167ae79eaa766"
          ],
          "on_allowlist": [],
          "not_on_allowlist": [
            "sha256:a0c073085684e12e19f31bb279fc8da663a374199bfecc47b04167ae79eaa766"
          ]
        },
        "prod": {
          "found": true,
          "last_updated": "2026-11-04T04:11:45.711141Z",
          "digests": [
            "sha256:a0c073085684e12e19f31bb279fc8da663a374199bfecc47b04167ae79eaa766"
          ],
          "on_allowlist": [],
          "not_on_allowlist": [
            "sha256:a0c073085684e12e19f31bb279fc8da663a374199bfecc47b04167ae79eaa766"
          ]
        },
        "prod-20261029-be649f8": {
          "found": true,
          "last_updated": "2026-10-29T06:03:10.121914Z",
          "digests": [
            "sha256:a320bb4882341c8e19ef1944436f6591ed73303732009ed5db9f6db6c9da756a"
          ],
          "on_allowlist": [
            "sha256:a320bb4882341c8e19ef1944436f6591ed73303732009ed5db9f6db6c9da756a"
          ],
          "not_on_allowlist": []
        }
      }
    },
    {
      "repo": "nearaidev/compose-manager-launcher",
      "tags": {
        "latest": {
          "found": true,
          "last_updated": "2026-09-25T21:34:00.770271Z",
          "digests": [
            "sha256:079a14b41deb11e0e27f9b69648e8b3d89bd6fb9ccf6071643b8f447fa415085"
          ],
          "on_allowlist": [
            "sha256:079a14b41deb11e0e27f9b69648e8b3d89bd6fb9ccf6071643b8f447fa415085"
          ],
          "not_on_allowlist": []
        },
        "prod": {
          "found": true,
          "last_updated": "2026-09-25T21:33:58.395363Z",
          "digests": [
            "sha256:079a14b41deb11e0e27f9b69648e8b3d89bd6fb9ccf6071643b8f447fa415085"
          ],
          "on_allowlist": [
            "sha256:079a14b41deb11e0e27f9b69648e8b3d89bd6fb9ccf6071643b8f447fa415085"
          ],
          "not_on_allowlist": []
        }
      }
    }
  ],
  "new_not_pinned": [
    "nearaidev/compose-manager-launcher:prod-20260612-06a5f44@sha256:3bd958ffb4bc401d3f17f575b9cf2adc29839d4fccbf6fe8c0b4b80bd158db1a",
    "nearaidev/compose-manager-launcher:prod-20260617-3e4a080@sha256:37165f3a8e18ad78b665c019668a1abb8c3d4ba7f82ba845703923fcaf9d31bd",
    "nearaidev/compose-manager-launcher:prod-20260617-3e4a080@sha256:524b2f62be34e7ac20da4b8f9eaddf1dc2170da2dd37ed34bd0e1db40401d0c0",
    "nearaidev/compose-manager-launcher:prod-20260617-3e4a080@sha256:e9c4b70bac422414ac022e1ecc15ac8e87ce8dce056f10778a2004ec71bad166",
    "nearaidev/compose-manager-launcher:prod-20260617-b4f3932@sha256:4bfce0c38fc927144ae58c9195913b03980ff5fd2dc8a5d3937407dab0c160b9",
    "nearaidev/compose-manager-launcher:prod-20260617-b4f3932@sha256:560fc82fe98b741c1e88ce227d95a6bff0753c54c2ee43250bcdc808e9481244",
    "nearaidev/compose-manager-launcher:prod-20260617-b4f3932@sha256:c5267a534e04950e799b7c148b17a15bb71b147963eea5402fe6971dd011dac4",
    "nearaidev/compose-manager-launcher:prod-20260617-c8df49c@sha256:7c68252238e61352456b1aef48550e556268724a10a4ef0688931b0b1d2fb5e5",
    "nearaidev/compose-manager-launcher:prod-20260619-9a035b2@sha256:27b8b73e85df2e8306a87df1025f24e6b5791a5cf3b7975233761d5fcdaefaed",
    "nearaidev/compose-manager-launcher:prod-20260619-9a035b2@sha256:78afb82330137c2fd90a9c8c6e452f8f178c15d863f87c079a11c13e92216e00",
    "nearaidev/compose-
```

## 8. Offline clean-clone demo
```
wrote /tmp/verdict_clean_clone/scoreboard/index.html with 36 verified cached votes
=== Verdict offline demo (no API key) ===

running 54 tests
......................................................
test result: ok. 54 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.38s


running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s



cargo test: OK
--- cached signatures ---
  PASS signature Qwen/Qwen3.8-27B → 0x835ab53d20…
  PASS signature deepseek-ai/DeepSeek-V4.1-Flash-1017 → 0xfe00718955…
  PASS signature z-ai/glm-5.3-flash → 0x3b753b4406…
real_panel: 3/3 signatures OK
--- scoreboard ---
scoreboard: OK /tmp/verdict_clean_clone/scoreboard/index.html
ALL OFFLINE CHECKS PASSED

```

## 9. Deploy prepare (no keys; human signs)
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

```
=== Verdict deploy_prepare (NO KEYS — human signs) ===
Wasm (build locally): contract/target/near/verdict.wasm
See deploy/TESTNET.md for near CLI steps.

=== 1) register_signer (owner) — one per panel model ===
{
  "address": "0x835ab53d20294ef795906dff21ae4c045336aa26",
  "model": "Qwen/Qwen3.8-27B",
  "attestation_report_sha256": "5a4bf929b5075e5c5fff05e0df4485497477eead03da7b8133d5a70c1a34bffd"
}
---
{
  "address": "0xfe0071895500a9755c18044948ab4f4e1a7f337e",
  "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
  "attestation_report_sha256": "de901b505c741c12fde485af41a3e772eb10f1bf885923dd5637ef18eb3e9006"
}
---
{
  "address": "0x3b753b4406613c5e70ae640508632b600424fa0c",
  "model": "z-ai/glm-5.3-flash",
  "attestation_report_sha256": "94e77677879e5da8f0cf1f3d96664334042797431edd40481b88c31d0b94f87d"
}
---
=== 2) register_relayer (owner) — use the account that will commit_evidence + submit_verdict ===
{"account": "<YOUR_TESTNET_ACCOUNT>"}
(Owner can commit/submit without registering; registering a dedicated relayer is optional.)

=== 3) create_market args (owner or anyone) ===
{
  "question": "Did it rain (>=1mm) at Ikeja on 2026-11-03?",
  "rules": "Resolves YES if the source snapshot reports >=1mm rainfall at Ikeja for 2026-11-03, NO if it reports <1mm. Otherwise AMBIGUOUS.",
  "sources": [
    "fixture://real_panel"
  ],
  "prompt_template_hash": "fe85745ffa51fc2ac8a80b61fff3e13c3e680f4c2cf71f6142f10b5bd97f7d8f",
  "resolvers": [
    "0x835ab53d20294ef795906dff21ae4c045336aa26",
    "0xfe0071895500a9755c18044948ab4f4e1a7f337e",
    "0x3b753b4406613c5e70ae640508632b600424fa0c"
  ],
  "deadline_ns": "<now_ns + small delta; then wait>",
  "dispute_window_ns": 3600000000000,
  "dispute_bond": "100000000000000000000000"
}
prompt_template_hash (from fixture): fe85745ffa51fc2ac8a80b61fff3e13c3e680f4c2cf71f6142f10b5bd97f7d8f
evidence_bundle_hash (from fixture header): 9e9bc4f0715b3fc6220790a8bf107538b1de99e26d7795a34d6a499aca925555

=== 4) commit_evidence (relayer/owner) AFTER deadline, BEFORE votes ===
{
  "market_id": 0,
  "round": 0,
  "evidence_bundle_hash": "9e9bc4f0715b3fc6220790a8bf107538b1de99e26d7795a34d6a499aca925555",
  "evidence_uri": "fixture://evidence/9e9bc4f0715b3fc6220790a8bf107538b1de99e26d7795a34d6a499aca925555"
}

=== 5) submit_verdict x3 (relayer/owner) — byte-exact request_raw/response_raw/signature ===
# model=Qwen/Qwen3.8-27B signer=0x835ab53d20294ef795906dff21ae4c045336aa26 outcome=YES
{"market_id": 0, "request_raw": "{\"model\": \"Qwen/Qwen3.8-27B\", \"temperature\": 0, \"seed\": 1114, \"messages\": [{\"role\": \"system\", \"content\": \"You resolve prediction markets strictly from the SOURCE SNAPSHOTS in the evidence bundle, applying the market RULES literally. Do not use outsid …(truncated; use full fields from contract/testdata/real_panel.json)
---
# model=deepseek-ai/DeepSeek-V4.1-Flash-1017 signer=0xfe0071895500a9755c18044948ab4f4e1a7f337e outcome=YES
{"market_id": 0, "request_raw": "{\"model\": \"deepseek-ai/DeepSeek-V4.1-Flash-1017\", \"temperature\": 0, \"seed\": 1114, \"messages\": [{\"role\": \"system\", \"content\": \"You resolve prediction markets strictly from the SOURCE SNAPSHOTS in the evidence bundle, applying the market RULES literall …(truncated; use full fields from contract/testdata/real_panel.json)
---
# model=z-ai/glm-5.3-flash signer=0x3b753b4406613c5e70ae640508632b600424fa0c outcome=YES
{"market_id": 0, "request_raw": "{\"model\": \"z-ai/glm-5.3-flash\", \"temperature\": 0, \"seed\": 1114, \"messages\": [{\"role\": \"system\", \"content\": \"You resolve prediction markets strictly from the SOURCE SNAPSHOTS in the evidence bundle, applying the market RULES literally. Do not use outs …(truncated; use full fields from contract/testdata/real_panel.json)
---

=== NOTE ===
No private keys in this repo. Sign deploy/init/calls with your own testnet keychain.
After txs confirm, paste explorer links under README 'Testnet deploy'.

wrote out/deploy_prepare/output.txt

```

## 10. Status deliverables
- Scoreboard: `scoreboard/index.html` (from `fixtures/scoreboard_cache/`)
- Demo video script: `docs/DEMO_VIDEO.md`
- X post draft: `docs/X_POST_DRAFT.md` (human posts)
- Submission draft: `docs/SUBMISSION_DRAFT.md` (human submits)
- Post-cutoff sports: see BENCHMARK.md below
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

