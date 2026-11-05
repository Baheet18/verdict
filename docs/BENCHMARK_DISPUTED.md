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
