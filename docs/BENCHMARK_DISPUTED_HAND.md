# Hand-curated disputed markets (round 6–7) — answer-bearing sources

**Method**: curator-(AI)-extracted official result page or Wikipedia + 2 reputable reports. Each snapshot has `answer_present: true` checked **before** the panel ran. Archive.org links recorded only when the archive check succeeded; otherwise marked unavailable (no fabricated archive URLs).

**Curator column**: **curator (AI)** — Grok Bot’s pre-panel check of the committed text. **Baheet has not personally verified each row.**

**Column name**: `answer-bearing sources`

**Scripts**: `scripts/hand_curate_disputed.py` (bundles) + `scripts/run_hand_disputed.py` (panel).

**Summary**: settled 8/8 · agree UMA 7 · disagree UMA 1 · escalated 0 · invented answers 0 · thin-same-ids escalated 8/8 · cost $0.0195

Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.

## Selection rule (hand-8)

From the 34 UMA-disputed Polymarket markets settled after the panel’s 2026-10-17 cutoff window:

1. Prefer markets where an **official result page or Wikipedia** plus **two reputable reports** could be hand-extracted as plain text (not a JS shell) inside a half-day curation budget.
2. Require `answer_present: true` on every snapshot **before** any panel call.
3. Prefer a single named resolution metric (final score, seat winner, Key Business Drivers line) over intraday rankings or long-horizon award odds.
4. Do **not** retune selection after seeing panel votes; Visa 5648379 was kept in the set.

## Dropped from the earlier JS-shell curated attempt (not in hand-8)

| ID | Question | Why dropped |
|---|---|---|
| 608558 | Will Raphinha win the 2026 Ballon d'Or? | Ceremony / longshot award market; official winners materials were still thin or JS-rendered during the time cap; near-duplicate of other Ballon d'Or longshots — not used to pad agree rate. |
| 608564 | Will Cristiano Ronaldo win the 2026 Ballon d'Or? | Same as above (Ballon d'Or ×2). |
| 5180406 | Will Apple be the second-largest company by market cap on …? | Intraday market-cap **rank** needs a precise as-of timestamp from a named ranking table; available pages were JS shells without a stable extractable close-of-day ranking during the time cap. |
| 5166966 | Will NVIDIA be the third-largest company by market cap on …? | Same market-cap rank problem. |
| 5166968 | Will Apple be the third-largest company by market cap on …? | Same market-cap rank problem (also dropped with the Apple/NVIDIA rank cluster). |

## Results

| ID | Question | Answer-bearing sources | Archives OK | Thin panel | Hand panel | UMA | curator (AI) |
|---|---|---|---|---|---|---|---|
| 1235552 | Will the Cleveland Guardians win the 2026 World Series? | 3 | 2 | ESCALATE | **NO** | NO | NO |
| 5969230 | Fukuoka SoftBank Hawks vs. Yokohama BayStars | 3 | 0 | ESCALATE | **YES** | YES | YES |
| 5285913 | Blues vs. Red Wings: O/U 4.5 | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 4588336 | 3Q Spread: Broncos (-3.5) | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 1281165 | Will the Democratic Party win the CA-34 House seat? | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 1284815 | Will the Republican Party win the TX-02 House seat? | 3 | 3 | ESCALATE | **YES** | YES | YES |
| 5648379 | Visa (V) Q4 payments volume growth (constant dollars) 9.5%–10.5%? | 3 | 1 | ESCALATE | **NO** | YES | NO |
| 5936743 | Spread: FC Andorra (-2.5) | 3 | 1 | ESCALATE | **NO** | NO | NO |

## Disagree note

**5648379 Visa Q4 payments volume growth 9.5%–10.5%**: panel unanimous **NO**; curator (AI) **NO**; UMA **YES**. Full source-line analysis: **`docs/VISA_CASE.md`**. **Status**: under review — a more precise 9.5% figure may support UMA's YES.

## Contrast

Same 8 market IDs with thin rules-only / non-answer-bearing fetches: **8/8 escalated**. With answer-bearing text: **8/8 settled** (7 agree UMA).

Settles correctly when answer-bearing evidence is committed; gathering that evidence is the relayer's job.
