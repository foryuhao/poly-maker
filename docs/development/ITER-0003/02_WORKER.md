# ITER-0003 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved pure REBALANCE quote candidate under `docs/development/ITER-0003/01_ARCHITECT.md`. Changes are limited to `rdp_quotes.py` and `test_rdp_quotes.py`.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Actual HEAD: `4cc8b41b4aa5dbfedfb0a1f2485a502a280499f1`
- The contract records an older archived HEAD; actual repository state was used.
- Preserved untracked `research_outputs/` assets remain untouched.
- No real-money authorization exists.

## Existing Components Reused

- `RDPInventorySnapshot` and existing inventory validation helpers
- `RDPQuoteDecision` and Decimal price helpers
- Existing `MarketMeta`, `Quote`, `Side.BUY`, and `BookView`
- Existing `MarketDataService` tick-change dispatch
- `StateStore` and `UserEventProcessor` for confirmed-fill integration tests

No accounting, shared-core, or other existing production component was modified.

## Files Changed

- `src/polymaker/strategy/rdp_quotes.py`
- `tests/test_rdp_quotes.py`

## Implementation Summary

Added `build_rebalance_quote()`. It selects NO when Delta is positive and YES when Delta is negative, then creates exactly one five-share BUY quote at the selected token’s BestAsk minus its explicit current tick.

It validates only the deficient-side book and tick. It does not apply NORMAL PriceGate, Book Filter, size skew, or economics rejection. The shared result marks post-only as required and lists REBALANCE-specific outstanding gates, including `CanSafelyContinue`, CapitalGate, cancellation/reconciliation, and live authorization.

Decimal exceptions and unrepresentable prices fail closed. Existing NORMAL code and behavior remain unchanged.

## Behavior Before

The RDP quote module built NORMAL dual-maker candidates and returned no candidate for REBALANCE inventory.

## Behavior After

At either exact REBALANCE threshold, and for larger exposure, the builder returns one deficient-side five-share candidate when its selected inputs are legal. Values inside the threshold and invalid selected inputs return no candidate with an explicit reason.

## Tests Added / Changed

Added tests for both threshold signs, larger exposure, just-inside values, fractional confirmed-fill snapshots, selected-side-only books and ticks, live tick updates, BestBid equality/improvement, NORMAL PriceGate independence, negative economics, selected-book/tick/grid/minimum failures, tiny-tick precision failures, identity/inventory validation, post-only and required gates, input immutability, and real accounting snapshot integration.

## Tests Executed

- `uv run pytest -q tests/test_rdp_quotes.py`
- `uv run pytest -q tests/test_rdp_accounting.py tests/test_quoting.py tests/test_orderbook.py tests/test_marketdata.py tests/test_favorite_first.py tests/test_favorite_first_review.py`
- `uv run pytest -q`
- `git diff --check`

## Test Results

- Targeted quote tests: **73 passed**
- Prescribed affected suite: **100 passed**
- Full suite: **752 passed, 2 skipped**
- `git diff --check`: passed

## Regression Results

No failures; no tests were excluded.

## Git Diff Summary

The diff contains only the two permitted files: **296 insertions and 6 deletions**. The only other untracked working-tree content is preserved `research_outputs/`. No Git history operations were performed.

## Safety Checks

No network or trading actions were performed. No real-money order was sent. No accounting, shared-core, FL/IM, configuration, dependency, data, or runtime-interface changes were made.

## Known Limitations

This builder creates a candidate only. It does not evaluate `CanSafelyContinue`, CapitalGate, cancellation/reconciliation, or runtime execution safety.

## Deferred Findings

None.

## Reviewer Attention

Please verify exact BestAsk-minus-tick pricing, selected-side-only validation, no NORMAL gate leakage, and explicit post-only and REBALANCE gate reporting.

## Human Live Trading Performed

NO
