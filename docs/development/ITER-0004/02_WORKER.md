# ITER-0004 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved `ITER-0004` contract at `docs/development/ITER-0004/01_ARCHITECT.md`: add a scoped, deadline-bounded close barrier that reuses existing cancellation, reconciliation, order-update, and inventory-accounting components.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD before implementation: `f4b085c8bfdd86eafbf2ae76c05623b47866ed0a`
- The contract recorded an earlier HEAD (`8a1fc11`); implementation followed the actual repository state.
- Existing untracked `research_outputs/` assets were preserved.

## Existing Components Reused

- `ExecutionGateway` cancellation and official order, open-order, and trade reads
- `reconcile_owned_order()` and its existing exact-fill recovery
- `normalize_order()` and `UserEventProcessor.on_order()`
- `UserEventProcessor` owned-order, quarantine, fill-admission, and confirmation state
- `snapshot_inventory()` and `RDPInventorySnapshot`
- Existing `StateStore` and processor behavior in tests

## Files Changed

- `src/polymaker/execution/rdp_lifecycle.py` — new
- `tests/test_rdp_lifecycle.py` — new

No other source or test files were changed.

## Implementation Summary

Added immutable `RDPCloseResult` and asynchronous `close_rdp_cycle()`. The close barrier validates registered BUY-order scope before gateway calls, cancels only explicitly scoped open orders, runs existing fill recovery, applies validated terminal order observations through the processor, and returns inventory only after final open-order and fill-evidence checks succeed.

Gateway operations are bounded by the supplied absolute deadline. Cancellation uncertainty, malformed or failed reads, unresolved or unadmitted fills, pending confirmation, conflicting open orders, and nonterminal snapshots fail closed. Task cancellation propagates; no background task is detached. The component never places orders.

Malformed terminal order payloads are treated as uncertain evidence, including exceptions raised during normalization.

## Behavior Before

There was no RDP close barrier coordinating cancellation, recovery, terminal-state checks, and final confirmed-fill inventory.

## Behavior After

A cycle’s inventory is exposed only when the scoped orders are proven terminal, reconciliation is complete, all accepted fills are confirmed and admitted, no conflicting market-token orders remain open, and the accounting projection succeeds. Incomplete results expose no inventory.

## Tests Added / Changed

Added 29 fake-gateway tests covering zero-fill and partial-fill cancellation, late and duplicate fill evidence, fully matched orders, pending confirmation and retry, uncertain cancellation, failed and malformed reads, scope rejection, foreign-order preservation, quarantine recovery, unadmitted fills, terminal-state and checksum races, operation deadlines, task cancellation, call ordering, local stale-order removal, and the prohibition on placement.

## Tests Executed

- `uv run pytest -q tests/test_rdp_lifecycle.py`
- `uv run pytest -q tests/test_filltruth.py tests/test_state.py tests/test_execution.py tests/test_filltest.py tests/test_rdp_accounting.py tests/test_rdp_quotes.py`
- `uv run pytest -q`
- `git diff --check`
- `git diff --no-index --check /dev/null src/polymaker/execution/rdp_lifecycle.py`
- `git diff --no-index --check /dev/null tests/test_rdp_lifecycle.py`

## Test Results

- Lifecycle tests: **29 passed**
- Affected regression tests: **245 passed**
- Full suite: **781 passed, 2 skipped**
- Whitespace checks: **passed**

During test development, initial targeted runs found incorrect test expectations about the `StateStore` API and recoverable quarantine behavior. Those test issues were corrected; the final targeted and regression runs pass.

## Regression Results

The affected filltruth, state, execution, filltest, RDP accounting, and RDP quotes regressions passed. The full repository suite passed with two skips and no exclusions.

## Git Diff Summary

The two permitted implementation files are new and total 680 lines. The ordinary tracked diff is empty because neither file has been archived yet. `git status` also shows the preserved untracked `research_outputs/` tree. No Git history operations were performed.

## Safety Checks

- Tests use an in-memory fake gateway only.
- No live cancellation, order placement, account action, or market mutation occurred.
- The fake placement method raises if called.
- No external runtime interface, shared-core behavior, FL behavior, or IM dependency was added.

## Known Limitations

The caller must serialize trading decisions and block submissions while closure runs. A successful result describes that closure observation; later actions must recheck current safety gates.

## Deferred Findings

Full RDP cycle control, order placement, CapitalGate, strategy transitions, market termination, metrics, and single-market CLI remain outside this iteration, as identified by the Architect.

## Reviewer Attention

Please verify deadline coverage across each gateway await, fail-closed behavior for malformed and contradictory evidence, confirmation completeness, final-read race checks, and that inventory is returned only on complete closure.

## Human Live Trading Performed

NO
