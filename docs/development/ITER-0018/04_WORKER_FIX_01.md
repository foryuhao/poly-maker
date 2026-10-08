# ITER-0018 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the repair for MF-01, MF-02, and MF-03 in ITER-0018’s approved contract: revalidate close evidence before releasing the replacement block, drain actual derive-only executor work before clearing close ownership, and sanitize RDP cancellation-failure diagnostics.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `977b5739938cfeccd14ef6bfdeaf8ab3901950b7`
- Shared-core change: NO
- FL behavior impact: NONE
- IM dependency: NONE

## Existing Components Reused

Reused the existing `close_rdp_cycle` barrier, retained `ExecutionGateway` and its `_io` executor, token-bucket cancellation limiter, order-observation hooks, processor/store, inventory snapshot, and `project_rdp_capital` evaluator.

## Files Changed

- `src/polymaker/execution/rdp_placement.py`
- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

## Implementation Summary

`record_close` now re-evaluates current capital and registry evidence before it releases the replacement block. A late MATCHED fill, reopened local order, or changed submitted price or condition leaves the block in place.

Close cancellation now tracks and drains the retained gateway’s actual executor futures through first or repeated cancellation, deadline handling, and session teardown. The close-operation marker remains owned until that work finishes.

The existing gateway cancel method logs raw exception text, and a wrapper around it could not sanitize that log. The RDP adapter therefore mirrors the existing cancel path’s paper/empty handling, rate limiter, request ID, observation events, retained SDK client call, executor, and boolean result. It records only the exception type in an RDP-local warning and returns failure so the existing barrier retains uncertainty. This adaptation is limited to the RDP close path; the shared gateway and FL behavior remain unchanged.

## Behavior Before

A previously certified capital projection could release the replacement block after new fill or registry evidence appeared. Cancelling the close caller could also clear close ownership while an SDK executor call still ran. Cancellation errors could expose raw exception text through the shared gateway logger.

## Behavior After

Close release requires fresh proof consistent with the current registry, inventory, and capital state. Cancellation and teardown retain operation ownership until executor work drains, including repeated cancellation. RDP close diagnostics retain the failure category without logging raw SDK exception text.

## Tests Added / Changed

Added regressions for late MATCHED fills and changed/reopened registry state after certification; real derive-only executor blocking under caller cancellation, deadline, overlap, and teardown; and credential-sentinel exclusion from cancellation results and logs.

The sentinel test uses a temporary in-memory logging handler and restores the prior handlers. This avoids a stale closed pytest stream handler observed in test-suite ordering while still checking emitted RDP log records.

## Tests Executed

- `uv run pytest -q tests/test_rdp_placement.py -k 'real_derive_only_executor or redacts_gateway_cancel' --tb=short`
- `uv run pytest -q tests/test_rdp_placement.py -k stale_close_proof --tb=short`
- `uv run pytest -q tests/test_rdp_placement.py --tb=short`
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block tests/test_rdp_runtime.py tests/test_rdp_placement.py --tb=short`
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block tests/test_rdp_lifecycle.py tests/test_rdp_runtime.py tests/test_rdp_capital.py tests/test_filltruth.py tests/test_rdp_accounting.py --tb=short`
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block`
- `uv run ruff check src/polymaker/rdp_runtime.py src/polymaker/execution/rdp_placement.py tests/test_rdp_placement.py`
- `git diff --check`

## Test Results

- Executor and redaction tests: 4 passed.
- Stale-proof tests: 4 passed.
- Placement module: 57 passed.
- Runtime and placement together: 117 passed; network guard recorded zero attempts.
- Lifecycle/runtime/capital/fill-truth/accounting regressions: 256 passed.
- Final guarded full suite: **1,298 passed, 2 skipped**; network guard recorded zero attempts.
- Scoped Ruff and `git diff --check`: passed.

An earlier guarded full run reported 1,297 passed, 2 skipped, and one failure in the sentinel test. The test-order-dependent failure came from a stale root handler writing to a closed pytest stream; its logging error printed the active fake exception context. After changing the test to use a temporary in-memory handler, runtime-plus-placement and the final full suite passed.

## Regression Results

The final full suite passed. The scoped Ruff check and whitespace check passed.

## Git Diff Summary

No Git history operations were performed. Implementation files’ final SHA-256:

- `src/polymaker/rdp_runtime.py`: `44229acd30bb0f6cf5cd11b7208b4a46c0a81892acb175f22ee8907142871895`
- `src/polymaker/execution/rdp_placement.py`: `2d2bcfdb99b26565b27cb622ca0dfb256f147b48dea846f2a7ed8beb00f4c592`
- `tests/test_rdp_placement.py`: `a6d73967bfcddbf1a8cd50bb0d3b3219f79a33634eb0d7bce2305c78edf91520`

The worktree also contains parent-owned iteration index/audit changes and untracked `research_outputs/`; they were not modified.

## Safety Checks

All cancellation tests used fake SDK clients. Socket and DNS operations were blocked during guarded checks; the final full run recorded zero attempts. No real account calls, orders, or cancellations were performed. No live authorization or order-create path was added.

## Known Limitations

This iteration implements explicit close of a safely owned initial placement only. It does not add subsequent quote-cycle orchestration or grant execution authority.

## Deferred Findings

Other delivery gaps recorded in the Architect Contract remain deferred, including unknown-placement recovery, continuous account reliability, subsequent NORMAL/REBALANCE execution, and complete metrics.

## Reviewer Attention

Please verify that the RDP-local cancellation adapter remains equivalent to the existing gateway path for rate limiting, observation events, retained-client use, and cancellation outcomes, while keeping diagnostic sanitization local to RDP.

## Human Live Trading Performed

NO
