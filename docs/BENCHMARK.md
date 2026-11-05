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

Finding: during the legacy rerun, one Qwen attestation entry for signer `0x835ab53d…aa26` presented a different `nearaidev/compose-manager` digest (`a320bb48…756a`, not the pinned `6e035c8f…84be`). The check failed closed, so legacy Qwen votes are labelled `Signature valid · hardware check pending`. We did not add the new digest to the allowlist because its provenance is not yet checked.
