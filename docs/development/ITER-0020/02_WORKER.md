# ITER-0020 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented `docs/development/ITER-0020/01_ARCHITECT.md` only: an explicitly activated, retained coordinator completes an already-started initial FillWindow through the existing scoped close path.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD before changes: `b257cf036537009394a52e4e3cc77309ac3550f4`
- The contract records an earlier HEAD (`0798413...`); the actual repository state and parent-provided task baseline were used.
- Existing untracked `research_outputs/` assets were preserved.

## Existing Components Reused

The implementation uses `initial_fill_window()`, the current placement attempt and registry checks, existing health and market-end checks, `close_initial_normal_placement()`, its strict gateway and reconciliation barrier, and existing task-drain helpers. Tests use the retained placement fixture, real gateway executor with a fake SDK client, deterministic clocks, and the socket/DNS-blocking fixture.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

## Implementation Summary

Added an immutable completion result and `complete_initial_fill_window()` API. Activation validates the explicit close budget, current complete pair, registry, replacement block, and started window before creating a retained task.

The coordinator preserves the original FillWindow deadline, checks current scope and input reliability while waiting, and invokes the existing scoped close once on expiry or a failure trigger. It records the trigger separately from close evidence. Caller cancellation and session teardown wake or drain owned work; an external close cannot overlap the coordinator, while its delegated close is allowed. Execution authorization remains fixed false.

## Behavior Before

The runtime could report FillWindow status and could close an initial placement explicitly, but did not retain the action joining window expiry to scoped closure.

## Behavior After

An explicit caller can retain the current initial pair through its established window and then use the existing close, reconciliation, inventory, and capital evidence path. Additional fills do not extend the deadline. The coordinator does not submit orders or start a subsequent cycle.

## Tests Added / Changed

Added retained-composition tests for activation rejection, exact deadline behavior, natural fills, later fills, book/filter movement, exact ±5 inventory boundaries, input-generation loss, market end, timing contradiction, quarantine, overlap, cancellation, teardown, fill during cancellation, and draining each executor-backed SDK operation.

## Tests Executed

- `uv run pytest -q tests/test_rdp_placement.py`
- `uv run pytest -q tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py tests/test_rdp_accounting.py tests/test_rdp_capital.py tests/test_rdp_book_filter.py tests/test_execution.py`
- `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py`
- `git diff --check`
- Final full suite: `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block`

The temporary `/tmp/rdp_network_block.py` plugin blocked `socket.socket.connect`, `connect_ex`, `socket.getaddrinfo`, and `socket.create_connection`, counted attempts, and failed on any attempt. The final run reported zero attempts.

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

- Placement suite: **139 passed**.
- Affected runtime, lifecycle, accounting, capital, book-filter, and execution tests: **355 passed**.
- Final full suite: **1,384 passed, 2 skipped** in 76.23 seconds; `RDP_NETWORK_GUARD_ATTEMPTS=0`.
- Ruff and `git diff --check`: passed.
- An initial focused run had 15 failures because test fixtures sent `CONFIRMED` without the tracker’s required preceding `MATCHED` state. The fixtures were corrected to use that lifecycle. Later fixture assertions were also corrected where deliberately inconsistent synthetic REST evidence correctly remained fail-closed. The final full suite includes the final test tree.
- The first guard invocation omitted `PYTHONPATH=/tmp` and failed to import the temporary plugin before tests ran; the corrected invocation above completed successfully.

## Regression Results

Existing placement, runtime, lifecycle, accounting, capital, book-filter, execution, and full-suite regressions passed. No production execution infrastructure outside the approved runtime file was modified.

## Git Diff Summary

`src/polymaker/rdp_runtime.py`: 224 insertions, 3 deletions.  
`tests/test_rdp_placement.py`: 603 insertions, 2 deletions.  
`git diff --check` passed. The pre-existing untracked `research_outputs/` directory remains untouched. No Git history operations were performed.

## Safety Checks

All SDK interactions in the new tests use fake clients. The final full suite recorded zero DNS/socket connection attempts. No real account actions, orders, or cancellations were performed. Existing read-only paths do not activate this coordinator.

## Known Limitations

Activation is explicit and is not wired into a CLI or autonomous runtime path. This change does not add pre-fill handling, subsequent NORMAL admission, REBALANCE execution, or live authorization. In the natural-fill deadline fixture, inconsistent synthetic REST evidence correctly prevents close certification; separate ±5 fixtures verify certified close evidence and REBALANCE classification.

## Deferred Findings

The contract’s deferred work remains unchanged, including pre-fill handling, subsequent strategy cycles, unknown-placement recovery, and full metrics.

## Reviewer Attention

Please verify that activation remains gated by current attempt and registry identity; the original window deadline is not extended; only the existing close path is called once; and interruption drains owned gateway work without releasing replacement on unreliable evidence.

## Human Live Trading Performed

NO
