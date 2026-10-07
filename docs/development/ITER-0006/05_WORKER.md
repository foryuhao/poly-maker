# ITER-0006 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented `docs/development/ITER-0006/04_ARCHITECT_REVISION_01.md`, including the approved cumulative absolute depth-change definition in `03_HUMAN_DECISION.md`. Changes are limited to the authorized new filter module and its tests.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `10e94b5f6e519eca3c2618efeaaa524d3a333711`
- Preserved untracked `research_outputs/` was left untouched.

## Existing Components Reused

- `MarketMeta`, `OrderBook`, and parsed `TradePrint`.
- Existing `OrderBook` bid levels and dynamic tick size.
- Existing `MarketDataService` `on_dirty` and `on_trade` callbacks for synthetic integration tests.

## Files Changed

- `src/polymaker/strategy/rdp_book_filter.py`
- `tests/test_rdp_book_filter.py`

## Implementation Summary

Added a per-market, read-only filter with immutable results and per-token rolling book and trade evidence. It snapshots exact best-bid and best-bid-minus-current-tick depth, computes cumulative absolute Q_eff variation, checks trade presence, strict depth-extreme thresholds, and one-second bid excursion.

Insufficient warmup, unreliable state, invalid or backward evidence, generation changes, and tick changes fail closed. Evaluation detects tick changes even without a dirty callback. Results include per-side numeric evidence, depth ratio, readiness, and all triggered rejection reasons.

## Behavior Before

No RDP NORMAL Book Filter or rolling evidence collector existed.

## Behavior After

The filter evaluates the three Frozen SOP predicates from compatible, warmed-up evidence. It preserves total variation across reversing changes, uses token-local trades and exact tick-neighbor depth, and resets incompatible history.

## Tests Added / Changed

Added 36 tests covering reversing variations of 12 and 6 shares; the exact five-share boundary; trade-window expiry and token locality; adjacent-level depth; strict depth-extreme and Fast Move boundaries; reversals and oscillation; rolling anchors and pruning; warmup; invalid and backward times/evidence; unreliable state; generation and tick resets; equal receipt times; synthetic market-data callbacks; and input/result immutability.

## Tests Executed

- `uv run pytest -q tests/test_rdp_book_filter.py`
- `uv run pytest -q tests/test_marketdata.py tests/test_orderbook.py tests/test_estimators.py tests/test_rdp_quotes.py tests/test_rdp_capital.py`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/strategy/rdp_book_filter.py tests/test_rdp_book_filter.py`
- `git diff --check`
- No-index whitespace checks for both new files

## Test Results

- Book Filter tests: **36 passed**. Rerun after the final lint-only edit: **36 passed**.
- Affected integration/regression tests: **138 passed**.
- Full suite: **871 passed, 2 skipped**.
- Ruff and whitespace checks: **Passed**.

The full suite ran before the final explicit `zip(strict=False)` lint correction; the targeted Book Filter suite, Ruff, and whitespace checks ran afterward.

## Regression Results

The Architect-specified market-data, orderbook, estimator, quote, and capital regression set passed: **138 passed**. The full suite had no failures or exclusions.

## Git Diff Summary

Only the two authorized implementation files are new. Final status also contains the preserved untracked `research_outputs/` tree (748 files); no other untracked files or tracked diffs were reported.

## Safety Checks

No market-data service changes, runtime wiring, I/O, network calls, order placement/cancellation, shared-core edits, or Git history operations were performed. Tests used synthetic books, trades, and callbacks.

## Known Limitations

Production callback wiring is deferred to a later iteration. The filter’s tests validate mechanics, not trading economics.

## Deferred Findings

No additional findings were addressed.

## Reviewer Attention

Please verify exact rolling-boundary anchor treatment, evaluation-time tick invalidation, and strict Decimal threshold calculations against the approved decision and Frozen SOP.

## Human Live Trading Performed

NO
