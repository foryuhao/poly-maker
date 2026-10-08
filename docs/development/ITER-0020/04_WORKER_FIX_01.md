# ITER-0020 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repair MF-01, MF-02, and MF-03 from `docs/development/ITER-0020/03_REVIEWER.md` within the approved runtime and placement-test scope.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `b257cf036537009394a52e4e3cc77309ac3550f4`
- Preserved untracked `research_outputs/` assets.
- The Orchestrator’s `ITERATION_INDEX.md` edit and untracked iteration audit artifacts were present and left untouched.

## Existing Components Reused

The repair retains the existing FillWindow evaluator, placement registry, scoped close and reconciliation barrier, capital projection, placement owner, gateway executor, and task-drain helpers. Tests use the real close composition with fake SDK clients and the existing socket/DNS-blocking fixture.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

## Implementation Summary

Unreliable FillWindow outcomes now restrict continuation **before** capital certification and `record_close()`. The existing close barrier still cancels and reconciles known orders and preserves confirmed inventory, but cannot certify capital, expose a next mode, or release replacement ownership unless the coordinator reaches a clean `WINDOW_EXPIRED` trigger.

The wait helper now retrieves outcomes from both owned wait tasks, drains pending tasks, and converts sleeper failures to a sanitized `WINDOW_WAIT_FAILED:<type>` trigger. A wait failure causes one scoped close without exposing the raw exception or leaving an unhandled asyncio diagnostic.

Tests now cover consistent close evidence after timing contradiction, configured-window expiry, same-generation public and user health loss, and a failing owned sleeper. Natural full fills use the actual admitted quote prices and consistent order/trade evidence, then certify NORMAL only at the original deadline.

## Behavior Before

A contradictory timing trigger could still produce certified capital, NORMAL as the next mode, and release replacement ownership. A sleeper exception could be left unhandled while the coordinator continued waiting.

## Behavior After

Unreliable, interrupted, or failed-wait paths still close and reconcile known risk, but remain blocked from continuation. Clean expiry can use existing evidence to certify inventory and mode. Wait failures are consumed and recorded by category, with no retry or raw exception leakage.

## Tests Added / Changed

Added or repaired retained-coordinator cases for the three reviewer findings, including a consistent two-fill REST reconciliation after timing contradiction; exact configured-window boundary; public/user health loss without generation change; failure-trigger blocking; and failing-sleeper cleanup.

## Tests Executed

- `uv run pytest -q tests/test_rdp_placement.py`
- `uv run pytest -q tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py tests/test_rdp_accounting.py tests/test_rdp_capital.py tests/test_rdp_book_filter.py tests/test_execution.py`
- `uv run pytest -q tests/test_engine.py`
- `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py`
- `git diff --check`
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block`

The temporary `/tmp/rdp_network_block.py` plugin blocked and counted `socket.socket.connect`, `connect_ex`, `socket.getaddrinfo`, and `socket.create_connection`; the final run reported zero attempts.

```python
import socket

attempts = []

def _blocked(name):
    def deny(*_args, **_kwargs):
        attempts.append(name)
        raise AssertionError(f"network blocked by RDP full-suite guard: {name}")
    return deny

def pytest_configure():
    socket.socket.connect = _blocked("socket.socket.connect")
    socket.socket.connect_ex = _blocked("socket.socket.connect_ex")
    socket.getaddrinfo = _blocked("socket.getaddrinfo")
    socket.create_connection = _blocked("socket.create_connection")

def pytest_sessionfinish(session, exitstatus):
    if attempts:
        session.exitstatus = 1

def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"RDP_NETWORK_GUARD_ATTEMPTS={len(attempts)}")
    if attempts:
        terminalreporter.write_line(f"RDP_NETWORK_GUARD_NAMES={attempts!r}")
```

## Test Results

- Placement tests: **144 passed**.
- Affected RDP runtime, lifecycle, accounting, capital, book-filter, and execution tests: **355 passed**.
- FL engine regression: **11 passed**.
- Final full suite: **1,389 passed, 2 skipped** in 76.15 seconds; `RDP_NETWORK_GUARD_ATTEMPTS=0`.
- Scoped Ruff and `git diff --check`: passed.
- Repair-round targeted tests for configured expiry, same-generation health loss, consistent timing contradiction, sleeper failure, and natural full fills: **6 passed**.

## Regression Results

Affected RDP integration and accounting tests, FL engine regression, placement tests, and final guarded full suite passed.

## Git Diff Summary

The scoped diff contains 265 insertions in `rdp_runtime.py` and 835 insertions in `test_rdp_placement.py`; `git diff --check` passed. Final SHA-256:

- `src/polymaker/rdp_runtime.py`: `62e7f179ff71a246e2b80174be828800d0bc3adc79cc16c8f3c93bb7052b305b`
- `tests/test_rdp_placement.py`: `525dd3a705ead52928aabba2a45bc048aa4b91962b74c8c2967c5803441437df`

No Git history operations were performed.

## Safety Checks

Tests used fake SDK clients; no live account, order, or cancellation actions occurred. The guarded full suite recorded zero network attempts. Unreliable close paths preserve the placement owner’s block and cannot expose a next mode.

## Known Limitations

Coordinator activation remains explicit and is not wired to a CLI or autonomous runtime path. This iteration does not add subsequent strategy cycles or live authorization.

## Deferred Findings

The contract’s deferred work remains unchanged, including pre-fill handling, subsequent NORMAL/REBALANCE operation, unknown-placement recovery, and full metrics.

## Reviewer Attention

Please verify that continuation safety is applied before capital projection and owner release, and that wait-task exceptions are retrieved, sanitized, and converted into a single scoped close.

## Human Live Trading Performed

NO
