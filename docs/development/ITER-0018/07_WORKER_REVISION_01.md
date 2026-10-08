# ITER-0018 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented ITER-0018 revision 01, `06_ARCHITECT_REVISION_01.md`, addressing MF-02 through MF-04 while retaining the fresh-proof repair for MF-01.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `c9855e4f003741042437b5bb5665947ac87dae87`
- Shared-core change: YES, limited to default-off diagnostics on four existing gateway methods.
- FL behavior impact: NONE intended; defaults remain unchanged.
- IM dependency: NONE.

## Existing Components Reused

Reused `ExecutionGateway` cancellation, open-order, order, and trade methods; its retained client, executor, limiter, and order-observation events; the existing close barrier; and the current inventory, capital, and registry-proof checks.

## Files Changed

- `src/polymaker/execution/gateway.py`
- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/rdp_placement.py`
- `tests/test_rdp_placement.py`

## Implementation Summary

The gateway’s existing `cancel`, `open_orders`, `get_order`, and `get_trades` methods accept a keyword-only `safe_diagnostics=False` option. With the option enabled, each method retains only the exception type, emits a sanitized diagnostic after leaving the SDK exception handler, and returns its existing failure category or outcome without a raw exception cause/context. A diagnostic-handler failure cannot convert an uncertain cancel or failed read into success. Default behavior remains unchanged.

`_StrictCloseGateway` now delegates all four operations to those methods with safe diagnostics enabled. Each delegated call runs in a shielded owned task. On caller cancellation, repeated cancellation, deadline expiry, or teardown, the adapter drains that task before propagating interruption. The close marker and replacement block remain active during the drain, and the barrier starts no further operation after interruption.

The existing placement-owner fresh capital and registry validation remains in place.

## Behavior Before

Cancellation was locally mirrored in the RDP adapter, duplicating gateway internals. Read operations could outlive close-task ownership after cancellation. Sanitized logs still risked exposing the active SDK exception when a standard logging handler failed.

## Behavior After

RDP close delegates to the existing gateway methods. All four operation types remain owned until completion under interruption. Opted-in failures retain useful categories while excluding raw SDK exception text and chains, including when the diagnostic handler fails.

## Tests Added / Changed

Added actual derive-only executor tests parameterized across `open_orders`, `cancel`, `get_order`, and `get_trades`, each covering caller cancellation with repeated cancellation, deadline expiry, and teardown. Tests assert overlap rejection, retained close ownership while the fake SDK remains blocked, no later barrier calls, and continued replacement blocking.

Added normal and failing `StreamHandler` diagnostic tests for all four gateway methods, outward error-chain and sentinel checks, safe-diagnostic delegation assertions, and a default-off compatibility test. Preserved the late-fill and changed-registry fresh-proof regressions.

## Tests Executed

- `uv run pytest -q tests/test_rdp_placement.py -k 'gateway_safe_diagnostics or each_real_gateway_operation or close_adapter_delegates or diagnostic_opt_in_defaults_off' --tb=short`
- `uv run pytest -q tests/test_rdp_placement.py --tb=short`
- `uv run pytest -q tests/test_execution.py tests/test_favorite_live.py --tb=short`
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block tests/test_rdp_placement.py tests/test_execution.py tests/test_favorite_live.py tests/test_rdp_lifecycle.py tests/test_rdp_runtime.py tests/test_rdp_capital.py tests/test_filltruth.py tests/test_rdp_accounting.py tests/test_state.py --tb=short`
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block`
- `uv run ruff check src/polymaker/execution/gateway.py src/polymaker/rdp_runtime.py src/polymaker/execution/rdp_placement.py tests/test_rdp_placement.py`
- `git diff --check`

The full-suite guard used `/tmp/rdp_network_block.py`. Its contents were:

```python
"""Pytest guard for RDP development: fail and count all socket/DNS calls."""
import socket

_ATTEMPTS = []

def _blocked(name):
    def deny(*_args, **_kwargs):
        _ATTEMPTS.append(name)
        raise AssertionError(f"network operation blocked: {name}")
    return deny

def pytest_configure(config):
    socket.socket.connect = _blocked("socket.connect")
    socket.socket.connect_ex = _blocked("socket.connect_ex")
    socket.create_connection = _blocked("socket.create_connection")
    socket.getaddrinfo = _blocked("socket.getaddrinfo")

def pytest_sessionfinish(session, exitstatus):
    print(f"RDP_NETWORK_GUARD_ATTEMPTS={len(_ATTEMPTS)} names={_ATTEMPTS!r}")
    if _ATTEMPTS:
        session.exitstatus = 1
```

## Test Results

- Final focused operation, interruption, delegation, and diagnostic tests: **22 passed**.
- Execution and FL live regressions: **160 passed**.
- Final guarded affected suite: **507 passed**, zero network attempts.
- Final guarded full suite: **1,316 passed, 2 skipped**, zero network attempts.
- Final scoped Ruff and `git diff --check`: passed.

A placement-module run reported **75 passed** before the final category/context assertions were added. Those assertions were then covered by the final 22-test focused run and the final affected and full-suite runs above; the 75-test count is not a final-tree placement-module rerun.

During development, an early placement run had 56 passed and one failure because an assertion still expected the prior RDP-local warning after diagnostics moved to the gateway. An initial diagnostic test run had eight failures because it assumed a particular `LogRecord.msg` representation. The tests were corrected to configure and inspect the stdlib structured logger directly, including a genuinely failing handler. A Ruff run also found an import-order issue and a suppressible exception block; both were corrected. All final checks passed.

## Regression Results

The final guarded full suite passed, including gateway, placement, lifecycle, accounting, runtime, and predecessor FL execution tests.

## Git Diff Summary

No Git history operations were performed. Final SHA-256:

- `src/polymaker/execution/gateway.py`: `35b97766df2afd35fdfa272fb281432468a5af6de4de4dda56c90c036c50de81`
- `src/polymaker/rdp_runtime.py`: `ce43a7a3996a8c34ab7ff62fa0bcdce2f1d951665395f4669eb92b1500858c69`
- `src/polymaker/execution/rdp_placement.py`: `2d2bcfdb99b26565b27cb622ca0dfb256f147b48dea846f2a7ed8beb00f4c592`
- `tests/test_rdp_placement.py`: `47a31e9c2d69cd55a082d2522f7768c3320be94c3a3c52f8dbf348ed9082c23e`

The worktree also contains 748 untracked files under preserved `research_outputs/`; they were not modified.

## Safety Checks

Tests used fake SDK clients and blocked socket/DNS operations; the final full suite recorded zero attempts. No real account reads, orders, or cancellations were performed. No send authority was added.

## Known Limitations

This iteration covers explicit closure of safely owned initial-placement orders. It does not add subsequent quote-cycle orchestration or grant execution authority.

## Deferred Findings

Unknown-placement recovery, subsequent NORMAL/REBALANCE execution, final submission gates, continuous account reliability, and complete metrics remain deferred.

## Reviewer Attention

Please verify the default-off gateway diagnostic behavior remains compatible with existing callers, and that all four delegated operations retain ownership through repeated interruption and failing diagnostic handlers.

## Human Live Trading Performed

NO
