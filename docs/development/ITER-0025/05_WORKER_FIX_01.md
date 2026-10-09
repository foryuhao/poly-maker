# ITER-0025 — Worker Implementation

## Status

IMPLEMENTED

The two findings in `04_REVIEWER.md` have been addressed in the implementation and covered by tests. Reviewer re-review is still required; this is not a PASS.

## Architect Contract

Implemented the repair within `docs/development/ITER-0025/01_ARCHITECT.md`: retain typed placement evidence across resolver failures, preserve exactly-once resolution, and enforce the absolute submission deadline at the SDK guard boundary. No strategy or Architect scope changes were made.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `d8cddca33fb595d7f6be1874af16a07fb3c70890`
- Working tree already contained ITER-0025 implementation and audit changes, plus untracked `research_outputs/`. Those files and datasets were preserved.
- No commit, push, or live action was performed.

## Existing Components Reused

The existing gateway, placement owner and resolver, initial submission operation, loop-owned factual validation, SDK executor bridge, task drain, and structured logging were retained.

## Files Changed

This repair changed:

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

The current ITER-0025 working tree also contains the original iteration’s changes to `src/polymaker/execution/gateway.py` and `tests/test_execution.py`, and the Orchestrator’s update to `docs/development/ITERATION_INDEX.md`. Those were not repair changes.

## Implementation Summary

Before resolving an SDK result, the submission now stores a frozen, sanitized snapshot of its categorical outcome, acknowledged order fields, observed IDs, and POST status. This evidence remains separate from the placement owner’s registered state: it does not register orders, confirm fills, release replacement blocking, or authorize further trading.

If resolution raises, the result retains that snapshot and the actual owner state. Teardown no longer calls the resolver a second time for the same submission. Snapshotting does not retain raw SDK messages or exception objects and chains.

At each gateway guard, after retaining the loop-captured factual validation, the SDK thread rechecks operational permission against the absolute deadline, finite clocks, and available market time. The thread does not inspect mutable books or processor state. A delayed bridge publication therefore cannot preserve expired send permission.

## Behavior Before

A resolver exception before owner update could leave the result with only an `ARMED` owner and lose the gateway’s returned acknowledgment IDs. Teardown could then attempt a second resolution. Also, a delayed validation-retention/logging step could carry a prior successful deadline check past the deadline and still permit POST.

## Behavior After

Gateway evidence is retained before resolver execution, with acknowledged and observed IDs represented separately. Failed resolution preserves uncertainty and owner state; teardown does not retry resolution. The final SDK guard denies POST when deadline permission has expired or its clock/market-time evidence is invalid.

## Tests Added / Changed

Added coverage for:

- Complete, partial, and observed-unknown outcomes with resolver failure before and after owner update.
- Exactly-once resolution through teardown and retained evidence after caller cancellation.
- Delayed retention at first and final actual executor guard locations.
- Deadline equality, invalid monotonic clock, and market-time boundary denial.
- Existing owner state, POST count, resolution errors, and sanitized evidence.

## Tests Executed

All pytest runs used a global network guard installed before importing pytest. Its complete source was:

```python
import socket
import sys

blocked = []

def deny(name):
    def _blocked(*_args, **_kwargs):
        blocked.append(name)
        raise AssertionError(f"network access blocked during guarded pytest: {name}")
    return _blocked

socket.socket.connect = deny("socket.connect")
socket.socket.connect_ex = deny("socket.connect_ex")
socket.getaddrinfo = deny("socket.getaddrinfo")
socket.create_connection = deny("socket.create_connection")

import pytest

result = pytest.main(sys.argv[1:])
if blocked:
    print(f"GLOBAL_NETWORK_GUARD_BLOCKED={blocked}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(int(result))
```

Commands and results:

- `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py` — passed.
- `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py -k 'submission_keeps_actual_owner_state or delayed_retention or sdk_thread_final_guard_fails_closed or gateway_evidence_survives_caller_cancel'` — **12 passed, 338 deselected**.
- `uv run python /tmp/rdp_phase0_network_guard.py -q --maxfail=1 tests/test_rdp_placement.py tests/test_execution.py tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py tests/test_rdp_account.py tests/test_rdp_capital.py tests/test_favorite_first.py tests/test_favorite_live.py tests/test_engine.py` — **780 passed**.
- `uv run python /tmp/rdp_phase0_network_guard.py -q --maxfail=1 tests/test_favorite_first.py tests/test_favorite_live.py tests/test_engine.py` — **90 passed**. This separate FL subset is redundant with the affected group but is recorded as executed.
- `uv run python /tmp/rdp_phase0_network_guard.py -q` — **1625 passed, 2 skipped**.
- `git diff --check` — passed.

The guard reported no blocked network attempts.

## Test Results

The repair-focused tests, affected group, FL subset, and full suite passed. No test failure occurred during this repair. The two full-suite skips remain skips; no failure was excluded.

The preceding Reviewer report identified MF-001 (lost Gateway evidence / duplicate resolver attempt at teardown) and MF-002 (late POST permission after delayed guard publication). The added tests exercise those failure paths, including actual executor guards and resolver-before/after-owner-update cases. Reviewer re-review should independently verify closure.

## Regression Results

The affected run included placement, execution, RDP runtime/lifecycle/account/capital, Favorite First/live, and engine tests. The full suite passed.

## Git Diff Summary

Current working-tree diff: **5 tracked files, 1684 insertions, 27 deletions**, plus untracked audit artifacts and preserved `research_outputs/`. The repair itself changed `rdp_runtime.py` and `test_rdp_placement.py`; no Git history operation was performed.

Final working-tree hashes:

```text
f3c0d5db3914df7d91743c8fa88ecc6b549d6915f38107db6c31d1c80b2622cb  src/polymaker/rdp_runtime.py
bcb4c544ec3e638a940a9daed3ffe9582985d409178b6f02aad123a2e9cd7733  tests/test_rdp_placement.py
01321c410a842adef8ad825d1be4f6ad1e03a647d62cbf43cbf89f3548b4e0ef  src/polymaker/execution/gateway.py
24609cacf882ec70daf6cdb540cbfdde943eec880a4cc4b4ab28c8aa0f066cda  tests/test_execution.py
75244ac50488aa14b82e9f118bc849cf3eadc4fc84d49814c6e174c3209870c6  docs/development/ITERATION_INDEX.md
```

## Safety Checks

- No real HTTP or WebSocket requests; global DNS/socket guard was active before pytest imports.
- Tests used fake SDK clients and executor paths.
- No real-money order, cancellation, or other live trading action occurred.
- Gateway acknowledgment evidence is observational and does not imply registered ownership or confirmed inventory.

## Known Limitations

DG-001 still requires independent Reviewer re-review of the exact resulting tree. This repair does not activate a CLI runner or authorize live execution.

## Deferred Findings

DG-002–DG-012 remain deferred under the Architect Contract.

## Reviewer Attention

Please verify that the frozen Gateway evidence is sanitized and distinct from owner-confirmed state; resolver attempts remain exactly once through cancellation/teardown; and the delayed first/final executor guards deny after deadline while pre-deadline POST outcomes still drain.

## Human Live Trading Performed

NO.
