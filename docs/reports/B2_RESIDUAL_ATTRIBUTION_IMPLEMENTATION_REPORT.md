# B2 Residual Attribution Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `97ab0f9` on `m4-dynamic-ev`, with unrelated pre-existing modified and untracked files preserved.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) for Git. The local B2 research script/output are the frozen analytical predecessor but have no independent reviewed checkpoint.
Architecture reference: User's residual-attribution specification, `docs/research/B2_RESIDUAL_ATTRIBUTION_RESEARCH_DESIGN.md`, and existing `research_tools/cip_b2_two_phase_backtest.py`. No production architecture changed.

## Scope implemented

Added a read-only diagnostic replay of the 32 B2 markets, a market-level one-primary-cause attribution that reconciles shares and residual PnL, capital-block episode/event diagnostics, accepted maker no-fill diagnostics, T150 residual/capital buckets, and per-episode plus joint capital-only counterfactuals using B2's original lifecycle and fill semantics.

## Files added

- `research_tools/b2_residual_attribution.py`
- `docs/research/B2_RESIDUAL_ATTRIBUTION_RESEARCH_DESIGN.md`
- `docs/research/B2_RESIDUAL_ATTRIBUTION_20261003.md`
- `docs/reports/B2_RESIDUAL_ATTRIBUTION_IMPLEMENTATION_REPORT.md`
- Ignored local outputs: `research_outputs/b2_residual_attribution_20261003/{summary.json,market_attribution.csv,blocked_episodes.csv,blocked_events.csv,capital_counterfactual.csv,joint_capital_counterfactual.csv,no_fill_orders.csv,residual_pnl_attribution.csv}`.

## Files modified

- NONE. Existing B2, B1, R0, production code, configuration and tests were not changed.

## Important implementation decisions

- Subclassed B2's `TwoPhaseReplay` only to observe blocked/invalid states and, in separate hypothetical runs, raise the research-only capital limit at a legal lighter-side order rejection. The inherited B2 strategy, quote, latencies, event clock, fill predicate and market data remain authoritative.
- Consecutive blocked decision events are one episode until the next reconciliation ends the block. The 4,075 events merge into 150 episodes; duration is integrated over B2's available-time clock.
- An episode can be proven unfillable without a duplicate full replay when no official same-token SELL print meets B2's strict price and three post-activation timestamp conditions before the first subsequent book-invalid cancellation becomes effective. The 25 candidate episodes with eligible prints received complete alternate B2 replays. Separate joint replays add only enough capital at each actual post-T150 rejection and measure market-level net residual change without summing overlapping episode effects.
- One primary cause partitions each market's final residual and residual PnL. The marginal capital-only recoverable quantity remains a separate counterfactual measure. The primary-cause convention and its half-residual tie-break are explicit in the research design.
- `execution_blocked_seconds` counts Reduce Only, nonflat, invalid-book intervals; no missing book event or fill is inferred.

## Deviations from architecture

- NONE. This research diagnostic does not revise the B2 strategy or M4 production architecture.

## Tests added

- NONE. The requested replay, reconciliation and counterfactual checks directly validate the analysis.

## Tests executed

- `python3 research_tools/b2_residual_attribution.py` ran all 32 markets and generated eight local result files.
- Every diagnostic baseline market was asserted equal to frozen B2 for final net, residual, PnL and raw fill count; capital-block event counts and blocked duration were reconciled to B2.
- Aggregate checks require 4,075 blocked events, 380 T150 shares, 135 post-switch reduction, 245 final shares, −$72.4467 residual PnL, and exact category sums.
- Full B2 alternate replays were run for every episode with a potentially eligible SELL print and once jointly for each capital-blocked market. Each full alternate replay was checked for B2 data errors and successful targeted order placement.
- Python syntax and whitespace checks are recorded in the final handoff after execution.

## Test results

All completed replay and reconciliation assertions passed. The market-level partition is 65 capital, 130 accepted/no-fill, 50 book/execution, and zero constraint/time/other shares. Residual PnL partitions to −$18.2357, −$37.7300 and −$16.4810. The joint capital-only counterfactual reduces final residual from 245 to 180 shares: 65 recoverable shares, or 26.5% of the actual final residual. The result is classified **EXECUTION / NO-FILL IS PRIMARY BOTTLENECK**, pending independent review.

## Known limitations / unresolved issues

- The inherited fill model ignores queue position and public print size; the counterfactual is conditional on that model.
- All 89 accepted and unfilled Reduce-phase submissions were later canceled after book invalidation. The split between no-fill and book/execution is a transparent primary-label convention, not a claim that these causes are independent.
- The 32-market execution-replay universe includes only 19 sessions in the stricter M4A integrity-PASS subset.
- B2 and this analysis are local untracked research work awaiting independent review and Git checkpointing; no Worker PASS is claimed.

## Safety / behavior impact

Live trading semantics changed: **NO**. No live order, external interface or production file was used. The script reads existing captured official data and local B2 artifacts and writes ignored research results.

## Git diff summary

Four new research-only source/document files and eight ignored local output files. Existing B2 and production code remain unchanged; unrelated pre-existing working-tree changes were preserved.

## Handoff to Reviewer

Review the capital-injection hook against B2's original `reconcile`, `place`, `trade` and `check` methods; verify the necessary-condition no-print pruning, 25 fully replayed candidate episodes, 150 episode boundaries, the nonduplicated 65-share joint result, and all market-level primary/secondary labels. The Worker has not issued PASS or created a tag.
