# ITER-0001 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved contract in `docs/development/ITER-0001/01_ARCHITECT.md`: derive an immutable, read-only RDP accounting snapshot from confirmed fills for an explicit set of registered orders.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD at worker start: `29cdc603cebf305457f665b1aee2d0eb705406a3`
- Architect contract base: `fd514299b453d2cd56b7afa093dabbbf6340f99b`
- Preserved untracked `research_outputs/` was present and left untouched.
- No real-money authorization exists.

## Existing Components Reused

- `UserEventProcessor.owned_record()` for registered-order scope validation.
- `economic_fills_for_orders()` for accepted, deduplicated fill evidence.
- Existing quarantine and failed-fill tracking for fail-closed rejection.
- `StateStore`, processor, and REST fill-recovery paths in integration tests.
- Existing `MarketMeta`, `Side`, and `TradeState` domain types.

## Files Changed

- Added `src/polymaker/strategy/rdp_accounting.py`
- Added `tests/test_rdp_accounting.py`

No existing production component was modified.

## Implementation Summary

Added a frozen snapshot and `snapshot_inventory()` projection. It validates explicit order scope and confirmed-fill identity/economics, excludes non-confirmed and unadmitted evidence, and derives inventory, cost, balanced/residual quantities, settlement bounds, and winner-specific `MarketPnL` from actual fill prices.

The projection does not mutate processor state, positions, orders, or callbacks.

## Behavior Before

No RDP-specific confirmed-fill inventory/accounting snapshot existed.

## Behavior After

A caller can request a snapshot for one market and an explicit set of registered BUY orders. Unknown, foreign-market, foreign-token, SELL, failed, quarantined, unresolved, or invalid confirmed evidence raises an explicit error. Only accepted `CONFIRMED` fills contribute to totals.

## Tests Added / Changed

Added independent formula and integration tests covering empty, one-sided, balanced, residual YES/NO, fractional and multiple-price cases; negative locked PnL; explicit winner calculations; confirmed-fill lifecycle and replay; unadmitted CONFIRMED-only events; shared trade IDs across maker orders; scoped exclusions; REST recovery; invalid or quarantined evidence; and read-only behavior.

## Tests Executed

- `uv run pytest -q tests/test_rdp_accounting.py`
- `uv run pytest -q tests/test_state.py tests/test_filltruth.py tests/test_favorite_first.py tests/test_favorite_first_review.py`
- `uv run pytest -q`
- `git diff --check`

## Test Results

- Targeted accounting tests: **15 passed**
- Affected regression tests: **118 passed**
- Full suite: **671 passed, 2 skipped**
- `git diff --check`: passed

## Regression Results

No regression failures. No tests were excluded.

## Git Diff Summary

Only the two contract-permitted files were added. No commits or Git history operations were performed. The preserved `research_outputs/` assets remain unmodified and untracked.

## Safety Checks

No network or trading actions were performed. No real-money order was sent. No shared-core, FL, IM, configuration, dependency, data, or runtime-interface changes were made.

## Known Limitations

This is an accounting projection only. It does not certify reconciliation completeness, pending-fill exposure, capital availability, or runtime readiness.

## Deferred Findings

None.

## Reviewer Attention

Please verify strict confirmed-only inclusion, fail-closed scope and evidence validation, and that repeated snapshot reads have no side effects.

## Human Live Trading Performed

NO
