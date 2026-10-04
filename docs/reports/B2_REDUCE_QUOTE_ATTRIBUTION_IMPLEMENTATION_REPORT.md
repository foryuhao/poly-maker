# B2 Reduce Quote Attribution Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `97ab0f9` on `m4-dynamic-ev`; all pre-existing dirty worktree files were preserved.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) for Git. The local B2 research script and output are the frozen analytical predecessor, without an independent reviewed checkpoint.
Architecture reference: User's 2026-10-04 attribution specification and `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_RESEARCH_DESIGN.md`. No production architecture changed.

## Scope implemented

Read-only order-level and market-level competitiveness attribution for B2's 113 accepted new Reduce-only BUY orders. Reconstructed the original book clock from saved capture events, measured event and elapsed-time quote distance, filled/no-fill differences, distance buckets, best-bid drift, inventory context, and historical best-bid SELL-print opportunities without changing B2 orders or inventory.

## Files added

- `research_tools/b2_reduce_quote_attribution.py`
- `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_RESEARCH_DESIGN.md`
- `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_20261004.md`
- `docs/reports/B2_REDUCE_QUOTE_ATTRIBUTION_IMPLEMENTATION_REPORT.md`
- Untracked local output: `research_outputs/b2_reduce_quote_attribution_20261004/{summary.json,reduce_orders.csv,filled_vs_no_fill.csv,distance_buckets.csv,market_summary.csv,best_bid_counterfactual.csv}`.

## Files modified

- `docs/CURRENT_STATUS.md`: concise research handoff entry only.

## Important implementation decisions

- Used B2's actual orders, fills, accept action audit, market results, captured events and R0 `Book` class. Did not run or modify a strategy path. T150-preexisting orders are excluded from the 113-order cohort but reported as the difference between 24 cohort fills and 27 total Reduce-phase fills.
- Integrated signed distance over actual accepted-to-close time, excluding invalid book intervals. Event-observation quantiles and time-weighted metrics are distinct. Negative distance remains negative; fill distance is captured before the trade event is processed.
- The hypothetical best-bid screen observes only bids before an actual no-fill order's cancel request, imposes frozen cancel and submit latencies, checks later strict trade-through SELL prints before actual order closure, and checks additional notional against B2's historical $25 capital path. It neither modifies orders nor produces a strategy PnL.
- Classification thresholds were recorded in the research design before interpreting the output. `MIXED EXECUTION BOTTLENECK` reflects a large descriptive distance gap but only 14/89 capital-feasible historical print opportunities.

## Deviations from architecture

- NONE. This is an independent read-only analysis and does not revise B2 or the M4 production architecture.

## Tests added

- NONE. The full analysis and output checks validate the research artifact.

## Tests executed

- `python3 research_tools/b2_reduce_quote_attribution.py`: processed all 32 B2 markets and generated the six required output files.
- Verified 113 accepted Reduce orders, 24 filled, 89 no-fill, 27 B2 stage fills, and 380/245 B2 residual baseline; every cohort order was risk-reducing at acceptance.
- The book reconstruction processed 7,700,057 book events with no nonmonotone skips or analytical errors. All 24 filled orders had valid-book pre-fill distance observations.
- `python3 -m py_compile research_tools/b2_reduce_quote_attribution.py`, `git diff --check`, and new-file whitespace checks passed. The six required output files, 113/24/89 cohort counts, total accepted-to-close time versus valid-plus-invalid intervals, and distance-bucket seconds reconciled.
- Recomputed filled cost plus open-order reservation at acceptance for all 108 positive-lifetime orders; zero mismatches against B2's acceptance audit. Five zero-lifetime orders were accepted and closed at the same activation timestamp, so their transient audit capital includes an order absent from a half-open static time slice.
- A seeded 5,000-repetition market-cluster bootstrap placed the no-fill-minus-filled median time-weighted distance gap at +1.689 ticks with a 95% interval of +0.394 to +2.813 ticks.

## Test results

The filled/no-fill median per-order time-weighted distance was −1.411/+0.278 ticks, and valid time >2 ticks behind was 5.9%/37.8%. Fourteen of 89 no-fill orders had a capital-feasible historical best-bid SELL-print opportunity after frozen latency; no PnL was imputed. Classification: **MIXED EXECUTION BOTTLENECK**, pending independent review.

## Known limitations / unresolved issues

- The historical opportunity screen is not a policy replay and does not model altered queue position, subsequent inventory or PnL. It is conditioned on the original B2 close time.
- B2 fill semantics assume full remaining size on a strict trade-through print and have no queue or print-size model.
- Six no-fill orders have no valid-book duration, including five accepted-and-canceled-at-activation orders; their distance metrics are null. Only 19 of 32 markets overlap stricter M4A integrity-PASS sessions.
- B2 research source/output and this analysis remain unreviewed, untracked local artifacts. No PASS checkpoint was created.

## Safety / behavior impact

Live trading semantics changed: **NO**. No production code, order, external interface or B2 strategy setting changed.

## Git diff summary

Four research-only source/documents, six untracked generated outputs, and a short `CURRENT_STATUS` entry. Existing unrelated production/config/test modifications were not touched; no commit, tag or push was made.

## Handoff to Reviewer

Inspect accepted-order cohort construction, available-time book reconstruction and interval integration, negative-distance handling, fill-time book sampling, capital calculation and strict hypothetical print predicate. Verify that the screen never mutates B2 paths or treats opportunities as realized fills. Review tracked and untracked files before any checkpoint decision.
