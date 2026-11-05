# Hand-curated disputed markets (round 6) — answer-bearing sources

**Method**: human-extracted official result page or Wikipedia + 2 reputable reports. Each snapshot has `answer_present: true` checked **before** the panel ran. Archive.org links recorded only when the archive check succeeded; otherwise marked unavailable (no fabricated archive URLs).

**Column name**: `answer-bearing sources`

**Script**: `scripts/hand_curate_disputed.py` (bundles) + `scripts/run_hand_disputed.py` (panel).

**Summary**: settled 8/8 · agree UMA 7 · disagree UMA 1 · escalated 0 · invented answers 0 · thin-same-ids escalated 8/8 · cost $0.0195

| ID | Question | Answer-bearing sources | Archives OK | Thin panel | Hand panel | UMA | Human read |
|---|---|---|---|---|---|---|---|
| 1235552 | Will the Cleveland Guardians win the 2026 World Series? | 3 | 2 | ESCALATE | **NO** | NO | NO |
| 5969230 | Fukuoka SoftBank Hawks vs. Yokohama BayStars | 3 | 0 | ESCALATE | **YES** | YES | YES |
| 5285913 | Blues vs. Red Wings: O/U 4.5 | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 4588336 | 3Q Spread: Broncos (-3.5) | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 1281165 | Will the Democratic Party win the CA-34 House seat? | 3 | 1 | ESCALATE | **YES** | YES | YES |
| 1284815 | Will the Republican Party win the TX-02 House seat? | 3 | 3 | ESCALATE | **YES** | YES | YES |
| 5648379 | Visa (V) Q4 payments volume growth (constant dollars) 9.5% t | 3 | 1 | ESCALATE | **NO** | YES | NO |
| 5936743 | Spread: FC Andorra (-2.5) | 3 | 1 | ESCALATE | **NO** | NO | NO |

## Disagree note
**5648379 Visa Q4 payments volume growth 9.5%–10.5%**: panel unanimous NO (official materials report 9%, below the band); human read also NO; UMA resolved YES. Reported as a panel–UMA disagreement, not as UMA being wrong.

## Contrast
Same 8 market IDs with thin rules-only / non-answer-bearing fetches: **8/8 escalated**. With answer-bearing text: **8/8 settled** (7 agree UMA).

