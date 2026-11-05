# Visa 5648379 — panel vs UMA disagreement (case study)

**Market**: Polymarket `5648379` — *Visa (V) Q4 payments volume growth (constant dollars) 9.5% to 10.5%?*  
**Sibling**: `5648378` — same metric, band *8.5% to 9.5%* (UMA resolved **NO**).  
**Panel (hand-curated answer-bearing run)**: unanimous **NO**.  
**UMA**: **YES** on `5648379` and **NO** on `5648378` → UMA’s effective figure is in **9.5–10.5**, not 8.5–9.5.  
**Curator (AI) read of the same bundle**: **NO** (9% is below 9.5–10.5).  
**Baheet has not personally verified this row.**

This note quotes the market’s exact rules and the named release lines. It does **not** say UMA was wrong.

## Exact market rules (from Polymarket description / hand bundle)

> This market will resolve according to Visa's payments volume growth (constant dollars) (year-over-year) for the upcoming fourth fiscal quarter, as reported in its official company earnings materials.
>
> The specified metric will be considered as reported in the company's official earnings materials. Subsequent revisions will not be considered.
>
> […]
>
> The resolution source for this market is the specified company's official earnings materials, including press releases, investor presentations, and regulatory filings. If the specified metric is not reported in these materials, recordings or transcripts of the company's earnings webcast may also be used.
>
> Note: This market will resolve based on the most numerically precise version of the specified metric reported in the company's official earnings materials. Only the specified metric will be considered; alternate versions that differ in definition or scope from the specified metric will not be considered.

Bracket mechanics (also in the rules): value exactly between two brackets → higher bracket; reported as a range → midpoint; metric missing / materials not released by Nov 30, 2026 11:59 PM ET → lowest bracket.

## Exact source lines used in the hand bundle

**Primary (SEC Exhibit / earnings release), Oct 27, 2026** —  
https://www.sec.gov/Archives/edgar/data/1403161/000140316126000126/q42026earningsrelease.htm

Quoted lines committed in `fixtures/evidence_hand/7f4d2d3172e91c520650d58e752443de5764f5f690d38c832c672bd5f573dcdc.json`:

1. **Key Business Drivers table (constant dollars), Q4 2026: Payments Volume = 9%**  
   (integer percent; no extra decimal for Payments Volume appears in this Exhibit 99.1 text).
2. **Narrative for the Q4 volume period**:  
   “Payments volume for the three months ended **September 30, 2026** increased **9%** over the prior year on a constant-dollar basis.”
3. **Separate June-30 lag line (service-revenue recognition period, not the Q4 volume period)**:  
   “Payments volume for the three months ended **June 30, 2026**, on which fiscal fourth quarter service revenue is recognized, increased **10%** over the prior year on a constant-dollar basis.”

**Secondary (Investing.com earnings-call transcript summary)** — same 9% for the quarter ended September 30, 2026.

**IR page snapshot in the bundle** also lists **Processed transactions Q4 2026: 10%** — a **different metric** than payments volume growth (constant dollars). Under the rules’ “only the specified metric” clause, processed transactions is out of scope.

**Operational Performance Data (OPD)**: Visa’s IR event page links an OPD PDF for Q4/FY 2026. Automated fetch returned HTTP 403 / captcha during this case write-up, so **no more-precise decimal for Payments Volume from OPD is claimed here**. If a future human fetch of OPD shows a more precise Payments Volume figure for the Q4 (Sept 30) period, that would be the “most numerically precise” candidate under the rules — and should be appended as a new evidence snapshot rather than assumed.

## How the two readings map

| Reading | Figure used | Fits “payments volume … fourth fiscal quarter” (Sept 30 period)? | Band 9.5–10.5 |
|---|---|---|---|
| Panel + curator (AI) | Key Business Drivers **Payments Volume 9%** + Sept 30 narrative **9%** | Yes | **NO** (below band) |
| Implied by UMA YES on 5648379 + NO on 5648378 | Something ≈ **10%** | June-30 lag **10%** is explicitly a different period; processed transactions **10%** is a different metric | **YES** |

## Classification

UMA’s YES on 9.5–10.5 does **not** clearly follow from the Key Business Drivers **Payments Volume** line or the Sept 30 payments-volume narrative in the Oct 27, 2026 release (both **9%**). A 10% reading appears to rest on either the June-30 lag sentence or another metric (e.g. processed transactions), which the rules’ “specified metric only” language does not clearly authorize for this market.

Therefore this is **not** labeled a panel error.

**Report**: panel and UMA disagreed; evidence is in the hand bundle and this note. **Never**: “UMA was wrong.”

## Artifacts

- Hand evidence: `fixtures/evidence_hand/7f4d2d3172e91c520650d58e752443de5764f5f690d38c832c672bd5f573dcdc.json`
- Hand summary: `docs/BENCHMARK_DISPUTED_HAND.md`
- Sibling UMA NO on 8.5–9.5: Polymarket `5648378` (context only; not in the hand-8 settle set)
