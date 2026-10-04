# External Interface Cleanup M1 Implementation Report

Status: **IMPLEMENTED — AWAITING INDEPENDENT REVIEW**
Commit / working tree base: `m4a-a1-pass` (`97ab0f9`); pre-existing uncommitted changes were preserved.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`).
Architecture reference: User-authorized `External Interface Cleanup M1 — Trading Core Only` scope for startup, position reconciliation, and live auto-merge.

## Scope implemented

- FavoriteFirst `_account_baseline` reads selected-market positions only from the Polymarket Data API; the on-chain zero-balance check was removed. Existing non-flat `validate_preflight` behavior remains fail-closed.
- Removed the periodic on-chain divergence poll and `Engine._check_position_divergence`, including its `force_set_position(..., source="onchain")` mutation. Existing Data API positions and Fill Truth reconciliation remain.
- Disabled live automatic merge at `Engine._maybe_merge`; the engine no longer creates `_merge_task` from the trading path. The legacy merge implementation and settlement code were not changed.

## Files added

- `docs/reports/EXTERNAL_INTERFACE_CLEANUP_M1_IMPLEMENTATION_REPORT.md`

## Files modified

- `src/polymaker/favorite_live.py`: removed RPC balance requirement from startup baseline.
- `src/polymaker/engine.py`: disabled automatic merge and removed on-chain position override polling.
- `tests/test_favorite_live.py`: added flat Data API startup/no-RPC and non-flat fail-closed regressions; preserved the pre-existing startup-profile test.
- `tests/test_engine.py`: added Data API position reconciliation/no-RPC and paired-position auto-merge-disabled regressions.
- `tests/test_gateway_token_balances.py`: replaced the obsolete startup RPC-gate expectation with a Data API-only baseline regression.
- `tests/test_hardening2.py`: replaced the obsolete on-chain correction test with an assertion that the override path is absent.
- `docs/CURRENT_STATUS.md`: recorded current implementation state and review handoff.

Pre-existing uncommitted changes in `livecfg/strategy.toml`, `src/polymaker/execution/gateway.py`, two earlier implementation reports, and other hunks in the touched test/status files were preserved.

## Important implementation decisions

- `_account_baseline` still returns the YES/NO Data API quantities, which flow into the existing preflight snapshot and unchanged non-flat rejection.
- `_maybe_merge` remains as an inert private hook because both existing engine call sites use it. Its body returns before marking a market, creating a task, reading gateway balances, or calling `Merger.merge`.
- `_merge_task`, `merge.py`, and settlement code remain in the repository but are no longer reached from engine auto-merge call sites.

## Deviations from architecture

- NONE within the explicitly authorized M1 scope.

## Tests added or updated

- FavoriteFirst preflight with flat Data API positions and an RPC mock that raises if called.
- FavoriteFirst preflight with a non-flat Data API position; verifies fail-closed rejection without RPC.
- Live position reconcile loop with Data API position and an RPC mock that raises if called.
- Paired inventory above `merge_min_size`; verifies no merge task, gateway RPC read, or merger call.
- Updated former expectations that required startup RPC availability or on-chain position correction.

## Tests executed

- Targeted M1 regressions, including updated legacy expectations: **6 passed**.
- First full `uv run pytest`: **663 passed, 2 skipped, 2 failed** because two old tests still required the removed startup RPC gate and on-chain position override.
- Updated those two directly related legacy tests to assert the new M1 behavior; reran the complete suite: **664 passed, 2 skipped**.
- User-requested static `grep` over `favorite_live.py` and `engine.py`: no matches for `publicnode`, `llamarpc`, `ankr`, `polygon_rpc`, `POLYGON_RPC`, or `HTTPProvider`.

## Test results

The focused M1 tests and final full suite pass. No live order, FavoriteFirst runtime, merge, settlement, or third-party API was run by this implementation task. The two skipped tests are `tests/test_live_marketdata.py` tests.

## Known limitations / unresolved issues

- Legacy `Engine._merge_task` and lower-level gateway/merge code remain, but current `Engine._maybe_merge` has no path that schedules the task.
- RPC use in `merge.py`, settlement sweep, and SDK internals remains explicitly outside this M1 scope.

## Safety / behavior impact

- FavoriteFirst strategy policy, quote and pair logic, risk thresholds, order execution, Fill Truth semantics, and research identity were not modified.
- Startup no longer requires chain balance availability; non-flat Data API positions still abort preflight.
- On-chain balance no longer corrects internal live positions.
- Live automatic merging is disabled.

## Git diff summary

No commit or tag was created. Pre-existing working-tree changes remain present and were not reverted.

## Handoff to Reviewer

Independently review only the M1 changes against `m4a-a1-pass` (`97ab0f9`), including startup fail-closed behavior, RPC-free position reconciliation, and paired-inventory auto-merge suppression. The final full suite completed with 664 passed and 2 skipped. Do not run a live trading session, merge, or settlement as part of review.
