# ITER-0010 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired reviewer findings MF-01 through MF-04 within the approved ITER-0010 adapter scope. The changes preserve the shared market-data parser and mutation path, reject invalid selected data before mutation, and prevent failed or overdue feed tasks from certifying readiness.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `7c567154b3dc4fc461aae49aa44505ba7b78fdc7`
- Orchestrator-owned iteration index and audit files were present in the worktree and left untouched.
- Preserved untracked `research_outputs/` assets were left untouched.

## Existing Components Reused

- `MarketDataService` remains responsible for transport, subscription, parsing, and book mutation.
- The RDP adapter now decodes raw JSON numbers as `Decimal` for validation, then delegates the original payload to the existing parser.
- Existing connection-generation and book snapshot-generation state remains authoritative.

## Files Changed

- `src/polymaker/rdp_marketdata.py`
- `tests/test_rdp_marketdata.py`

## Implementation Summary

Raw JSON integer and float literals retain decimal precision through selected-frame validation. Unsupported precision is rejected before delegation, including when a later item in a batch is invalid. The untouched original JSON is then passed to the existing parser after the entire batch validates.

An absent snapshot `tick_size` remains valid and preserves REST-seeded provenance. An explicitly present `null` tick is rejected before mutation.

The readiness loop now checks the owned service task’s completion or failure before accepting readiness. Task completion wakes the waiter even if the service emitted no readiness notification. Monotonic startup deadline checks run before readiness acceptance and again after a wake, preventing late publication from passing. Failure, timeout, and cancellation paths still stop and await the owned task.

## Behavior Before

JSON numeric literals could lose precision before validation; explicit null snapshot ticks were treated like absent ticks; and readiness could pass after owned-task failure or after the startup deadline.

## Behavior After

Precision-loss values and explicit null ticks fail before selected-book mutation. A terminated or failed task cannot certify readiness, and late readiness publication returns a startup-timeout failure with cleanup.

## Tests Added / Changed

Added regressions for:

- JSON numeric price and quantity literals that cannot be represented by the shared float parser.
- Atomic rejection when a later frame in a batch contains an unsupported numeric literal.
- Absent versus explicitly null snapshot ticks.
- Owned-task failure after both books are populated, with and without an explicit readiness notification.
- Readiness publication after the monotonic deadline and task cleanup.

## Tests Executed

- `uv run ruff check src/polymaker/rdp_marketdata.py src/polymaker/rdp_live.py src/polymaker/cli.py tests/test_rdp_marketdata.py tests/test_rdp_live.py`
- `uv run pytest -q tests/test_rdp_marketdata.py tests/test_rdp_live.py`
- `uv run pytest -q tests/test_marketdata.py tests/test_orderbook.py tests/test_catalog.py tests/test_btc5m.py tests/test_live_marketdata.py`
- `uv run pytest -q`
- `git diff --check`

## Test Results

- Ruff: passed.
- Focused RDP tests: **84 passed**.
- Affected shared regressions: **34 passed, 2 skipped**.
- Full suite: **1039 passed, 2 skipped**.
- `git diff --check`: passed.

## Regression Results

The focused adapter and CLI tests, existing shared market-data and order-book tests, catalog and BTC5m regressions, and full suite pass.

## Git Diff Summary

This repair changed only `src/polymaker/rdp_marketdata.py` and `tests/test_rdp_marketdata.py`. The existing iteration changes and Orchestrator-owned audit/index files remain in the worktree. Preserved `research_outputs/` assets remain untouched. No Git history operations were performed.

## Safety Checks

- Only fake WebSocket transports and mocked HTTP were used.
- No account, order-placement, cancellation, or RPC path was used.
- No shared market-data service or parser was modified.
- No real-money trading was performed.

## Known Limitations

Feed readiness remains a point-in-time public-data observation. It does not establish account readiness or ongoing feed reliability after capture.

## Deferred Findings

Continuous runtime feed guards, trading-time tick certification, account readiness, Book Filter orchestration, and execution remain outside this iteration.

## Reviewer Attention

Verify exact JSON numeric validation before delegation, explicit-null tick rejection, owned-task failure precedence, and monotonic deadline enforcement after late wakeups.

## Human Live Trading Performed

NO
