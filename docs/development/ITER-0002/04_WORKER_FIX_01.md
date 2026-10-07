# ITER-0002 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-01 and MF-02 from the immutable ITER-0002 Reviewer Report within the approved quote-builder contract. Changes are limited to `rdp_quotes.py` and `test_rdp_quotes.py`.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Actual HEAD: `15a87320f26eafc640c383f2d0455f3c081da713`
- The contract records HEAD `3d18530`; actual repository state was used.
- Preserved untracked `research_outputs/` remains untouched.
- No real-money authorization exists.

## Existing Components Reused

- Existing `RDPInventorySnapshot` and `snapshot_inventory()`
- Existing `MarketMeta`, `Quote`, `Side`, and `BookView`
- Existing `StateStore` and `UserEventProcessor` for confirmed-fill integration tests

No accounting or shared-core component was modified.

## Files Changed

- `src/polymaker/strategy/rdp_quotes.py`
- `tests/test_rdp_quotes.py`

## Implementation Summary

For MF-01, inventory consistency now follows the existing accounting producer’s exact float subtraction when checking `QY - QN == Delta`. This accepts its valid `0.3 - 0.1` result without introducing a tolerance. The NORMAL boundary remains a strict `abs(Delta) >= 5` rejection.

For MF-02, Decimal arithmetic exceptions return an explicit `INVALID_PRECISION` rejection. Before constructing quotes, the builder verifies each output float preserves the exact Decimal price and tick grid. If float conversion erases the one-tick offset, the entire pair is rejected as `UNREPRESENTABLE_PRICE`.

Added regressions for real confirmed fractional fills on both sides and the `1e-30` and `1e-20` tick reproductions.

## Behavior Before

A valid accounting snapshot with fractional YES and NO quantities could be rejected because Decimal subtraction did not match the accounting producer’s float Delta. Very small ticks could raise an uncaught Decimal exception or lose the one-tick discount when converted to float.

## Behavior After

Valid fractional accounting output remains eligible without a trading tolerance. Decimal precision failures and output prices that cannot preserve the exact required offset return no quotes with explicit reasons.

## Tests Added / Changed

Added real confirmed-fill integration using YES quantity `0.3` and NO quantity `0.1`, verifying the snapshot’s producer-consistent Delta and resulting NORMAL quotes. Added tiny-tick tests verifying `1e-30` and `1e-20` reject safely. Existing ordinary tick, strict threshold, and PriceGate cases remain.

## Tests Executed

- `uv run pytest -q tests/test_rdp_quotes.py`
- `uv run pytest -q tests/test_rdp_accounting.py tests/test_quoting.py tests/test_orderbook.py tests/test_marketdata.py tests/test_favorite_first.py tests/test_favorite_first_review.py`
- `uv run pytest -q`
- `git diff --check`

## Test Results

- Targeted quote tests: **40 passed**
- Prescribed affected suite: **100 passed**
- Full suite: **719 passed, 2 skipped**
- `git diff --check`: passed

## Regression Results

No failures; no tests were excluded.

## Git Diff Summary

Only the two permitted new implementation/test files are present. No tracked implementation files changed. No Git history operations were performed.

## Safety Checks

No network or trading actions were performed. No real-money order was sent. No accounting, shared-core, FL/IM, configuration, dependency, data, or runtime-interface changes were made.

## Known Limitations

The builder remains a candidate calculator only. Book Filter, CapitalGate, data and order-state reliability, current-cycle reconciliation, and live authorization remain required before execution.

## Deferred Findings

None.

## Reviewer Attention

Please verify exact compatibility with accounting’s float Delta, strict NORMAL boundaries without tolerance, and fail-closed handling of Decimal exceptions and float output precision.

## Human Live Trading Performed

NO
