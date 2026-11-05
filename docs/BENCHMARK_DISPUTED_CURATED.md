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
