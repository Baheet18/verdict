# Claude review packet — round 6

Generated 2026-11-05 WAT. Local commits only; **no push / deploy / X / form submit** without a fresh explicit approval.

## Status vs round-6 checklist

| # | Item | Status |
|---|---|---|
| 1 | Linter → optional rule-authoring checklist; no predictive claims | Done (README, scoreboard, DEMO_VIDEO, X, SUBMISSION) |
| 2 | Hand-curate 6–8 disputed with answer-bearing text + archives | Done: **8 markets**, `answer_present:true` before panel |
| 3 | Headline rewrite + Bitquery exact source | Done: 243/470 cited |
| 4 | Scoreboard from ALL caches | Done: **549** sanitized votes + summaries in fixtures |
| 5 | Re-attest all 3 today; STOP before pinning a0c07308 | Done: **a0c07308 not observed**; still unpinned |
| 6 | Testnet demo prep (sports, 1 NEAR, GitHub raw URI) | Prepared; **human wallet signing still required** |
| 7 | Reject duplicate JSON keys + test | Done: 55 contract tests pass |
| 8 | Full DEMO_VIDEO / X_POST / SUBMISSION in this packet | Below |

## Headline (use everywhere)

Clear markets: auto-settled at ~$0.002, **107/107** agreed with UMA, hardware-attested; request-parameter steering blocked on-chain. Unclear markets: escalated to humans, **0 invented answers**. TOO_EARLY is first-class (Bitquery: **243 of 470** disputes 9 Sep–7 Oct 2026 ended “too early” — https://bitquery.io/investigations/who-decides-polymarket-disputes).

**Threat-model qualification:** “unsteerable” in the pitch means the contract rejects extra request keys / duplicate keys / unbound evidence. A registered **relayer still chooses** which answer-bearing sources to commit. README Limits state this explicitly.

## Hand-curated disputed result (answer-bearing sources)

- Settled **8/8**
- Agree UMA **7**
- Disagree UMA **1** (Visa 5648379: panel+human NO vs UMA YES — reported as disagreement)
- Escalated **0**
- Invented answers **0**
- Same IDs thin evidence escalated **8/8**
- Cost $0.0195
- Docs: `docs/BENCHMARK_DISPUTED_HAND.md` · fixtures: `fixtures/evidence_hand/`

Rows:
- `1235552` thin=ESCALATE hand=**NO** uma=NO human=NO absrc=3 — Will the Cleveland Guardians win the 2026 World Series?
- `5969230` thin=ESCALATE hand=**YES** uma=YES human=YES absrc=3 — Fukuoka SoftBank Hawks vs. Yokohama BayStars
- `5285913` thin=ESCALATE hand=**YES** uma=YES human=YES absrc=3 — Blues vs. Red Wings: O/U 4.5
- `4588336` thin=ESCALATE hand=**YES** uma=YES human=YES absrc=3 — 3Q Spread: Broncos (-3.5)
- `1281165` thin=ESCALATE hand=**YES** uma=YES human=YES absrc=3 — Will the Democratic Party win the CA-34 House seat?
- `1284815` thin=ESCALATE hand=**YES** uma=YES human=YES absrc=3 — Will the Republican Party win the TX-02 House seat?
- `5648379` thin=ESCALATE hand=**NO** uma=YES human=NO absrc=3 — Visa (V) Q4 payments volume growth (constant dollars) 9.5% to 10.5%?
- `5936743` thin=ESCALATE hand=**NO** uma=NO human=NO absrc=3 — Spread: FC Andorra (-2.5)

## Re-attest (2026-11-05 WAT)

```json
{
  "at": "2026-11-05T12:57:02+01:00",
  "saw_a0c07308": false,
  "pin_blocked_pending_human_approval": false
}
```

Per-model labels (all Hardware + host stack verified); compose-manager still `6e035c8f…` / launcher `079a14b4…`. **`saw_a0c07308: false`** → no pin request; Docker Hub `a0c07308` remains unpinned until observed **and** human explicitly approves a pin.

## Testnet demo prep (unsigned)

- Market: **Saints vs. Giants** (Polymarket `4329887`), post-cutoff NFL from the 124-bench; live panel unanimous **YES**.
- Fixture: `contract/testdata/real_panel.json` (market_id=0 headers).
- Evidence: `fixtures/evidence/ccde677158f3e2ca4962f09e365c2ff7be958df4bdd50b7a23ac259f5fc598b7.json`
- `evidence_uri` (after human push): `https://raw.githubusercontent.com/Baheet18/verdict/master/fixtures/evidence/ccde677158f3e2ca4962f09e365c2ff7be958df4bdd50b7a23ac259f5fc598b7.json`
- `dispute_bond`: **1 NEAR**
- Helper: `python scripts/deploy_prepare.py` → `out/deploy_prepare/{output.txt,args.json}`
- **Blocker:** human signs with own testnet wallet on a **fresh** contract. Never ask for keys.

## Offline demo (clean run this turn)

```
wrote /workspace/verdict/scoreboard/index.html with 549 votes
=== Verdict offline demo (no API key) ===

running 55 tests
.......................................................
test result: ok. 55 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.36s


running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s



cargo test: OK
--- cached signatures ---
  PASS signature Qwen/Qwen3.8-27B → 0x835ab53d20…
  PASS signature deepseek-ai/DeepSeek-V4.1-Flash-1017 → 0xfe00718955…
  PASS signature z-ai/glm-5.3-flash → 0x3b753b4406…
real_panel: 3/3 signatures OK
--- scoreboard ---
scoreboard: OK /workspace/verdict/scoreboard/index.html
ALL OFFLINE CHECKS PASSED
```

## `verify_signer_replicas` (full)

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

## `reject_duplicate_json_keys` (full)

```rust
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
```

## `parse_outcome` (Rust)

```rust
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
```

## DEMO_VIDEO.md (full)

# Verdict demo video script (~2.5 minutes)

Record locally. Do **not** show API keys, `.env`, or wallet seed phrases.
Offline mode first (judge-reproducible), then optional live attest.

| Time | Shot | On-screen / voiceover |
|---|---|---|
| 0:00–0:15 | Title card: “Verdict — TEE-attested market resolution on NEAR” + GitHub URL | “Verdict is a three-model AI resolution panel that runs in NEAR AI Cloud sealed hardware and signs its verdicts.” |
| 0:15–0:35 | Terminal: clone + `python scripts/offline_demo.py` (no API key) | “A judge with no API key can reproduce this offline: contract tests, cached EIP-191 signatures, and the scoreboard rebuilt from fixtures.” |
| 0:35–0:55 | `cargo test` scrolling PASS (55 tests) | “On-chain checks bind every vote to market, round, rules, prompt template, and a committed evidence hash — reject steered request keys and duplicate JSON keys.” |
| 0:55–1:20 | Open `scoreboard/index.html` | “Clear markets: auto-settled at about $0.002, 107 of 107 agreed with UMA, hardware-attested. Unclear markets escalate — zero invented answers. TOO_EARLY is first-class.” |
| 1:20–1:40 | Hand-curated table on scoreboard | “With human-checked answer-bearing sources on 8 formerly disputed markets: 8 settled, 7 agree UMA, 1 disagree, 0 invented. Same IDs with thin evidence all escalated.” |
| 1:40–2:05 | `python scripts/attest_demo.py Qwen/Qwen3.8-27B` PASS then tamper FAIL | “Hardware + host stack verified: Intel TDX + NVIDIA NRAS, allowlisted digests. Tampering the compose fails closed.” |
| 2:05–2:25 | `deploy/TESTNET.md` + unsigned sports `deploy_prepare.py` (Saints vs Giants) | “Wasm is built. Demo market is a real post-cutoff NFL game. Testnet deploy is signed in the builder’s own wallet — keys never enter the repo. Evidence URI is GitHub raw after a human-approved push.” |
| 2:25–2:40 | Limits slide | “Model container not measured; evidence fetched outside the TEE; owner/relayer still trusted for registration and evidence commit. ‘Unsteerable’ means request-parameter steering is blocked, not that the relayer is trustless.” |
| 2:40–2:50 | End card: repo + “NEAR Hackathon · submit by Nov 14 23:59 UTC” | “Verdict: certified resolutions you can re-hash.” |

## B-roll checklist
- `scoreboard/index.html` (filters + answer-bearing sources column)
- `fixtures/evidence_hand/MANIFEST.json`
- `scripts/expected_measurements.json`
- Contract snippet: `check_request` allowlist + `reject_duplicate_json_keys` + `commit_evidence`

## Numbers (must match cache)
- Sports post-cutoff: 124 markets, 107 settled, 107 agree UMA, 0 disagree, ~$0.0022/market (`docs/BENCHMARK.md`)
- Thin disputed: 34 escalate (`docs/BENCHMARK_DISPUTED.md`)
- Hand disputed: 8 settled / 7 agree / 1 disagree / 0 invent (`docs/BENCHMARK_DISPUTED_HAND.md`)
- Bitquery TOO_EARLY: 243/470 (52%) — https://bitquery.io/investigations/who-decides-polymarket-disputes
- Do **not** claim the rule linter predicts disputes.

## Do not film
- `.env`, API key dashboards with key visible, wallet recovery phrases, private RPC tokens.


## X_POST_DRAFT.md (full)

# X post draft (HUMAN posts — do not auto-post)

Suggested text (edit freely):

---

Built **Verdict** for the NEAR hackathon:

A 3-model AI panel runs in NEAR AI Cloud TEEs, signs every vote (EIP-191), and only auto-settles when all three agree.

Clear markets: auto-settled at ~$0.002, **107/107** agreed with UMA (post-cutoff sports), hardware-attested. Request params allowlisted on-chain.
Unclear markets: escalated to humans — **0 invented answers**. TOO_EARLY is first-class (Bitquery: 243/470 UMA disputes were “too early”).

Hand-checked answer-bearing sources on 8 disputed markets: 8 settled, 7 agree UMA, 1 disagree, 0 invent. Same IDs with thin evidence: all escalated.

Repo: https://github.com/Baheet18/verdict

(Not financial advice. Not an endorsement by NEAR/Illia. Demo video + testnet txs coming after I sign deploy.)

---

Notes for Baheet:
- Attach the demo video when recorded.
- Add explorer links only after you sign testnet txs.
- Do not paste API keys or `.env` into the post.
- Do not claim the rule linter predicts disputes (optional checklist only).
- “Unsteerable” in conversation = request-parameter steering blocked; relayer still picks evidence.


## SUBMISSION_DRAFT.md (full)

# NEAR Hackathon submission payload draft (HUMAN submits)

**Deadline**: Nov 14 23:59 UTC (= Nov 15 00:59 WAT).

## Project
- **Name**: Verdict
- **One-liner**: TEE-attested multi-model resolution panel for prediction markets on NEAR.
- **Repo**: https://github.com/Baheet18/verdict (push latest local commits before submit — needs explicit OK)
- **Demo video**: _(link after recording — see docs/DEMO_VIDEO.md)_
- **X/thread**: _(link after human posts — see docs/X_POST_DRAFT.md)_
- **Testnet**: _(explorer links after human wallet deploy — see deploy/TESTNET.md)_

## Tracks (privacy, defi, security, open)
- **Privacy / confidential compute**: NEAR AI Cloud TEEs; TDX + NVIDIA attestation; allowlisted host-stack measurements.
- **DeFi**: prediction-market resolution primitive; bonded disputes; TOO_EARLY as a first-class round advance.
- **Security**: on-chain request-key allowlist, duplicate-JSON-key rejection, evidence commit before votes, relayer-only submit, replay protection, per-signer model binding.
- **Open**: public repo, offline screener mode, published evidence bundles, scoreboard rebuilt from sanitized cache.

## What to paste into “Description”
Verdict settles markets with three attested models (Qwen3.8-27B, DeepSeek-V4.1-Flash-1017, GLM-5.3-flash) on NEAR AI Cloud. Votes are EIP-191 signatures over `model:sha256(req):sha256(resp)`. The NEAR contract:
1. Requires an exact request-key allowlist (blocks logit_bias / guided decoding steering) and rejects duplicate JSON keys.
2. Requires `commit_evidence` (relayer/owner) after the market deadline, with `evidence_uri`, before any `submit_verdict`.
3. Binds header fields: market_id, round, rules_hash, prompt_template_hash, evidence_bundle_hash.
4. Maps each signer → one model; rejects replayed signed texts.

**Headline**: Clear markets auto-settled at ~$0.002, 107/107 agreed with UMA, hardware-attested; request-parameter steering blocked (relayer evidence choice remains — see Limits). Unclear markets escalate to humans, 0 invented answers. TOO_EARLY is first-class (Bitquery 243/470).

Offline screener mode (`python scripts/offline_demo.py`) needs no API key.

## Numbers (backed by committed scripts + fixtures)
- Post-cutoff sports: `docs/BENCHMARK.md` — 124 markets; 107 settled; 107 agree UMA; 0 disagree; ~$0.0022/market.
- UMA-disputed thin evidence: `docs/BENCHMARK_DISPUTED.md` — 34 markets; 0 settled; 34 escalate; 0 invent.
- Hand-curated answer-bearing disputed: `docs/BENCHMARK_DISPUTED_HAND.md` — 8 settled; 7 agree UMA; 1 disagree; 0 escalate; 0 invent; thin same IDs 8/8 escalate.
- Bitquery TOO_EARLY: 243 of 470 disputes (9 Sep–7 Oct 2026) — https://bitquery.io/investigations/who-decides-polymarket-disputes
- Rule authoring checklist: optional only; **no** dispute-prediction claim (control 27/34 vs 26/34).

## Limits (must stay in the submission)
- Model-serving container not covered by RTMR3 pin.
- Evidence fetched outside the TEE; registered relayer commits the hash.
- Owner registers signers / settles disputes.
- GPU↔TDX binding is the shared nonce only.
- “Unsteerable” ≠ trustless relayer.
- Testnet deploy requires the builder’s wallet (not agent-held keys).

## Checklist before submit
- [ ] Push local round-6 commits to GitHub (explicit OK required)
- [ ] Human signs testnet deploy on a **fresh** contract; paste tx links into README
- [ ] Evidence raw URL resolves (after push)
- [ ] Record demo video from DEMO_VIDEO.md
- [ ] Post X draft manually
- [ ] Confirm no secrets in git (`git grep -i api_key` clean on tracked files)


## Number audit (scripts/cache)

| Claim | Source | Value |
|---|---|---|
| 107/107 agree UMA | `fixtures/benchmark_summaries/sports_postcutoff_124.json` | post_cutoff.panel_settled_agree_uma=107, disagree=0 |
| ~$0.002/market | same, cost_usd/markets | 0.274/124 ≈ **$0.00221** |
| Thin 34 escalate | `disputed_thin_34.json` | settled=0, escalated=34 |
| Hand 8/7/1/0 | `disputed_hand.json` | settled=8, agree=7, disagree=1, escalate=0, invent=0 |
| Bitquery 243/470 | Bitquery investigation (fetched 2026-11-05) | 243 too early of 470 disputes |
| Tests | `cargo test` | **55 passed** |
| Scoreboard votes | `fixtures/scoreboard_cache/_meta.json` | **549** sanitized |

## Approval blockers (human-only)

1. **Push** latest local commits (evidence raw URL 404s until then).
2. **Testnet wallet signing** (fresh contract, 1 NEAR bond, optional dispute tx).
3. **X post** (human posts draft).
4. **Hackathon form submit** (human).
5. **Pin a0c07308** — N/A this reattest (not observed); if observed later, STOP for explicit pin approval.

## Self-review checklist (applied)

- Permissionless influence: request keys allowlisted; duplicate keys rejected; evidence commit required; submit relayer-only; round/replay bound.
- Verified label: shared-signer replica policy (`verify_signer_replicas`); reattest all 3 OK today.
- Offline judge path: `scripts/offline_demo.py` PASS without API key.
- Every public number traced to fixtures/docs above.
