# CIP B1 Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `97ab0f9` on `m4-dynamic-ev` with unrelated pre-existing production/config/test/document changes.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`). R0 and B0 local research outputs are frozen comparators but are untracked, not reviewed Git checkpoints.
Architecture reference: User's B1 specification and `docs/research/CIP_B1_RESEARCH_DESIGN.md`; no change to the M4 production architecture.

## Scope implemented

Implemented and ran a research-only, continuous YES/NO inventory-pool replay with persistent maker quotes and Backtest 01's frozen asymmetric inventory skew. The run produces per-market results, raw orders/fills, after-fill inventory paths, a reason-coded action audit, aggregate summary and a full research report with best/worst/median-activity paths.

## Files added

- `research_tools/cip_b1_backtest.py`
- `docs/research/CIP_B1_RESEARCH_DESIGN.md`
- `docs/research/CIP_B1_BACKTEST_20261003.md`
- `docs/reports/CIP_B1_IMPLEMENTATION_REPORT.md`
- Ignored local outputs: `research_outputs/cip_b1_20261003/{summary.json,market_results.csv,orders.csv,fills.csv,inventory_paths.csv,action_audit.csv}`.

## Files modified

- NONE by this task. Pre-existing unrelated modified and untracked files were preserved.

## Important implementation decisions

- Imported R0's research-only `Book` and CSV writer for the exact reconstruction contract; imported Backtest 01's `QTY` and `RATE` and used its Python rounding formula. Backtest 01's full `Policy.decide` was unsuitable because it cancels/replaces on BBO changes, so B1 implements a separate persistent-order reconciler without changing the frozen skew values.
- Order price is sticky after submission. Only fill-driven heavy-side risk retreat, invalid book, target and T−60s cutoff can request cancellation. A canceled order keeps its remaining notional reserved and remains fill-eligible until effective cancellation. The action audit includes book validity, contemporaneous BBO and inventory for checking each reason. It records a subsequent same-side submission as `REPLACE` with the originating cancel reason; 1,079 invalid-book replacements occurred. `BBO_ONLY_REPRICE` was zero.
- The cutoff request is timestamped at the exact T−60s boundary, even when the next recorded event arrives later, matching R0's cancellation timing. Activation and cancellation effective timestamps use the audited p75 delays. The simulation does not react to recorded production-owned account orders; those are unrelated to its hypothetical orders.
- Balanced placement with both sides absent is atomic under the $25 reserve check. When imbalanced, a missing lighter-side order gets first claim on capital. Filled matched inventory does not release capital. Each fill updates cost and quantity immediately, and the surviving lighter-side order keeps its quote.
- The R0 fill predicate still fills the entire remaining order after one eligible public print. `apply_fill` accepts arbitrary positive quantity so a partial fill immediately changes inventory and can trigger heavy-side retreat, but the frozen historical replay does not synthesize partial fills from public print size.
- Pool-cost time fractions use only intervals with both sides held. Positive PairPnL attribution compares the first two-sided average costs against final averages and is strictly post-hoc.
- The classification gives residual-dominated economics precedence over the capital diagnostic because +$16.21 PairPnL was overwhelmed by −$115.98 ResidualPnL. Nineteen markets also recorded capital-blocked decision events.

## Deviations from architecture

- NONE from the user-supplied B1 strategy. The current M4 architecture does not specify this independent research ablation; no production architecture was revised.

## Tests added

- NONE. The full backtest and targeted checks directly exercised the requested research behavior.

## Tests executed

- `python3 research_tools/cip_b1_backtest.py`: completed all 32 selected markets; produced six required output files.
- Output validation checked exact R0/B0 universe equality; one after-fill path per each of 297 fills; strict trade-through price and three post-activation timestamps; post-only placement below ask; $25 capital invariant; PairPnL/ResidualPnL decomposition and winner-payout cash-flow identity; allowed cancellation reasons; `BOOK_INVALID` cancellation only while book invalid; frozen skew tick rounding; and zero `BBO_ONLY_REPRICE` per market.
- Targeted in-memory check: a valid BBO movement left both existing order IDs unchanged with zero cancellations. A 2-share partial YES fill immediately recorded QY=2/CY=$1.00 and, with the now-more-aggressive remaining YES order, requested a heavy-side cancel while keeping the NO order at its original price.
- `python3 -m py_compile research_tools/cip_b1_backtest.py`, `git diff --check` for tracked changes, and a direct newline/trailing-whitespace check of all four newly untracked B1 files.

## Test results

All executed validations passed. The final run had 1,422 submissions, 1,125 final cancellations, 1,079 invalid-book replacements, 297 full-order fills, 540 matched shares, +$16.2105 PairPnL, −$115.9805 ResidualPnL and −$99.77 TotalPnL. `BBO_ONLY_REPRICE=0`, zero data errors, and no market exceeded $25 except floating-point representation at $25.000000000000004, within the 1e−8 invariant tolerance. Full metrics and complete representative paths are in `docs/research/CIP_B1_BACKTEST_20261003.md`.

## Known limitations / unresolved issues

- The inherited maker fill rule has no queue position, print-size cap or observed partial fills; the synthetic partial check validates code behavior, not historical partial-fill incidence.
- R0 book validity has no independent quiet-book age threshold. B1 cancels on invalid/crossed reconstructed books, accounting for 1,132 cancellation requests, and does not claim a live queue estimate.
- The sample has 32 execution-replayable markets but only 19 overlapping the stricter M4A integrity-PASS subset. The current B1 script and results have no independent review or Git checkpoint.

## Safety / behavior impact

Live trading semantics changed: **NO**. The script only reads local official captures, cached official settlement labels and local audit summaries. It writes ignored research outputs; it sends no order and calls no network interface.

## Git diff summary

Four new research-only source/document files and ignored generated outputs. Production trading source, config and tests were not edited by this task; their pre-existing working-tree modifications remain separate.

## Handoff to Reviewer

Inspect the persistent-order transition rules and all action reasons against the B1 design, especially 1,132 invalid-book cancel requests, exact cutoff handling, $25 reservation, the 297 fill predicates and all after-fill paths. Compare skew rounding with `research_tools/dp_backtest_01_inventory_skew.py` and book/fill semantics with `research_tools/r0_rolling_pair_replay.py`. An independent verdict is pending; this Worker report does not claim PASS.
