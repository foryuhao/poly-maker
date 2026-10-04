# CIP B3 Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `97ab0f9` on `m4-dynamic-ev`, with unrelated pre-existing modified and untracked work preserved.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) for Git; local B2 research code/output is the frozen analytical comparator but has no independent reviewed checkpoint.
Architecture reference: User's B3 specification, `docs/research/CIP_B3_RESEARCH_DESIGN.md`, and unchanged B2 research implementation. The M4 production architecture was not revised.

## Scope implemented

Added a research-only B3 replay over the same 32 historical BTC5m sessions, with a single strategy change: on reconstructed invalid books, keep a known active risk-reducing BUY, cancel risk-increasing or flat-state BUY orders and freeze all new orders. The rule applies in both phases. Recorded invalid episodes, safety counts, exact B2 order matches, same-state B2 policy cancellation counterfactuals, unfilled-order print diagnostics and a labeled Phase 2-only attribution diagnostic.

## Files added

- `research_tools/cip_b3_book_invalid_backtest.py`
- `docs/research/CIP_B3_RESEARCH_DESIGN.md`
- `docs/research/CIP_B3_BACKTEST_20261003.md`
- `docs/reports/CIP_B3_IMPLEMENTATION_REPORT.md`
- Untracked local output: `research_outputs/cip_b3_20261003/{summary.json,market_results.csv,orders.csv,fills.csv,inventory_paths.csv,book_invalid_episodes.csv,kept_reduce_orders.csv,action_audit.csv}`.

## Files modified

- `docs/CURRENT_STATUS.md`: concise research handoff entry only.

## Important implementation decisions

- Subclassed B2's `TwoPhaseReplay`; inherited book reconstruction, event clock, activation and cancellation lifecycle, maker fill predicate, inventory accounting, quote placement, $25 check and Phase 2 normal-book logic. Only invalid-book reconciliation changes strategy behavior.
- Kept orders require `accepted_us` and `status == active`; cancel-pending orders retain their existing lifecycle. A submitted order is not assumed accepted. No quote is computed while invalid.
- Episodes are bounded by invalid/valid reconciliation observations and T300. Reason labels use only fields supported by the frozen `Book` object; stale/sequence/inactive causes cannot be reliably inferred.
- The B3 gamma path necessarily diverges from B2 after the first retained order. Exact B2 order matching is therefore distinguished from same-state B2-policy cancellation. The Phase 2-only diagnostic preserves B2's exact pre-T150 order prefix but is not the requested full B3 strategy.
- The user-required `kept_reduce_orders.csv` includes risk-reducing orders first kept in either phase. Fields identify phase and exact versus same-state attribution. Direct per-fill PnL is winner cash payout minus purchase cost, not a full-market incremental strategy PnL.

## Deviations from architecture

- NONE from the requested strategy. The diagnostic adds a separately labeled non-B3 control run solely to isolate Phase 2 attribution.

## Tests added

- NONE. The full frozen replay and direct output invariants are the validation harness for this research slice.

## Tests executed

- `python3 research_tools/cip_b3_book_invalid_backtest.py`: completed 32 primary B3 and 32 Phase 2-only diagnostic replays, with eight required output files.
- Verified 32 capture/settlement/B2 market identities and the exact B2 pre-T150 order prefix plus T150 residual in the Phase 2-only diagnostic.
- Checked no data error, no invalid-book new order, no reduce-only heavy-side new order, no `BBO_ONLY_REPRICE`, no KEEP violation, the inherited $25 bound, and zero risk-increasing fills while invalid.
- Audited the two cancel-pending risk-increasing fills after T150 and a representative kept Phase 2 order that filled after the same-state B2 cancellation would have been effective.
- `python3 -m py_compile research_tools/cip_b3_book_invalid_backtest.py` and `git diff --check` completed without output.
- A separate output audit asserted eight required files, 32 markets, 232 fills satisfying strict price and three-timestamp predicates, per-market `GammaPnL + ResidualPnL = TotalPnL`, peak capital ≤$25, and zero recorded safety/invariant failures.

## Test results

The primary B3 run has 1,127 invalid episodes, 71 reducing orders kept, zero KEEP violations, 29 Reduce-phase fills / 145 shares, 575 T150 residual, 450 final residual, −$127.1214 ResidualPnL and −$65.65 TotalPnL. Two valid-book cancel-race fills added 10 risk-increasing shares after T150; none occurred while invalid. The Phase 2-only diagnostic has 380 T150 residual, 22 Reduce-phase fills, 270 final residual and −$76.85 TotalPnL. The classification is **NO MATERIAL BENEFIT VS B2**, pending independent review.

## Known limitations / unresolved issues

- B2 and B3 research artifacts are local untracked work without an independent reviewed checkpoint. The production working tree was already dirty before B3.
- The fill model has no queue position or public-print-size cap. The 32-market universe includes only 19 stricter M4A integrity-PASS sessions.
- Phase 1 B3 path divergence limits exact Phase 2 order-by-order matching to actual B2. Same-state policy comparisons are explicitly labeled and are not whole-strategy causal PnL deltas.
- `Book.valid()` cannot classify stale, sequence-gap or market-inactive causes. No data repair or cause inference was attempted.

## Safety / behavior impact

Live trading semantics changed: **NO**. No production code, live order or external interface was used. This script reads repository-local captures and cached official labels and writes local research outputs only.

## Git diff summary

Four B3 research/source/report documents plus eight untracked generated outputs; a brief `CURRENT_STATUS` entry records the handoff. Existing production/config/test changes were preserved untouched. No commit, PASS tag or push was made.

## Handoff to Reviewer

Independently inspect B3's invalid-book branch, known-active order gating, episode boundaries, zero invalid-book KEEP violations, the exact B2-versus-B3 order-match predicate, the Phase 2-only B2 prefix, the two cancel-race fills and the report's aggregate reconciliation. Review tracked and untracked artifacts before any checkpoint decision.
