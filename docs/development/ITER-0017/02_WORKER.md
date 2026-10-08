# ITER-0017 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented `docs/development/ITER-0017/01_ARCHITECT.md`: connect initial NORMAL placement-attempt ownership to the existing strict gateway result and retained user-event processor, without enabling order submission.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `97e763da15775e941a63873dcc3d0bab66294f12`
- Working tree contained only preserved, untracked `research_outputs/` before this implementation.
- Frozen SOP and Architect Contract were read.

## Existing Components Reused

- Existing strict `ExecutionGateway.place` result types, including `PlacementUnknown`.
- Existing processor pending capture, order registration, fill replay, deduplication, quarantine, and confirmed-fill accounting.
- Existing `StateStore` order ownership and local order records.
- Existing RDP initial NORMAL admission and retained `RDPInputSession`.

## Files Changed

- `src/polymaker/execution/rdp_placement.py` — new RDP-local placement ownership adapter.
- `src/polymaker/rdp_runtime.py` — retain the adapter, expose arm/resolve methods, block admission after unresolved outcomes, and mark an armed attempt unknown on session close.
- `tests/test_rdp_placement.py` — new gateway-to-processor ownership and replay coverage.
- `tests/test_rdp_runtime.py` — assert observe-only runtime does not arm placement capture.

## Implementation Summary

The adapter binds an eligible initial pair to the selected market scope and current public/user generations, validates session health and expiry, and arms the existing processor capture for the exact two BUY quotes. It accepts only typed gateway outcomes; it does not parse SDK payloads.

Safely attributable acknowledgments are validated against token, side, price, size, live state, unique ID, and existing ownership. Valid orders are inserted into the existing store before processor registration and buffered-event replay. The adapter does not restore stale acknowledgment size after replay.

Partial, malformed, conflicting, or unknown results retain attempt evidence and block replacement, including unknown outcomes with no attributable IDs. Observed-only IDs remain unowned. Pending malformed-event, overflow, and unmatched-candidate evidence is captured before registration can clear the processor buffer. Clean authoritative empty results may clear capture. Duplicate completion is rejected.

The adapter and runtime add no placement, cancellation, signing, retry, or send-authorization path. Attempt evidence always reports `execution_authorized=False`.

## Behavior Before

RDP had initial NORMAL admission and strict gateway result types, but did not connect an initial pair’s acknowledgments to the retained processor’s pre-ack event capture and replay.

## Behavior After

A caller can arm the existing capture for an eligible initial pair and resolve a typed gateway outcome into the same retained store and processor. Safe acknowledgments establish ownership before buffered events replay; unresolved attempts block subsequent admission.

## Tests Added / Changed

`tests/test_rdp_placement.py` exercises the actual `ExecutionGateway.place` strict pair path using a fake SDK client, then resolves the result through the retained processor. Coverage includes accepted and rejected pairs, both partial directions, unknown and malformed results, duplicate IDs, attribution mismatches, existing-ID collisions, pending anomalies, pre-ack MATCHED/CONFIRMED replay, duplicate fill delivery, late local remaining-size changes, repeated completion, overlap, and zero-ID unknown outcomes.

`tests/test_rdp_runtime.py` verifies observe-only runtime does not create a placement attempt.

## Tests Executed

Narrow test command:

```bash
uv run pytest -q tests/test_rdp_placement.py --tb=short
```

Result: **21 passed**.

Focused runtime/placement command after the final test adjustment:

```bash
uv run pytest -q tests/test_rdp_placement.py tests/test_rdp_runtime.py --tb=short
```

Result: **81 passed**.

Scoped lint command:

```bash
uv run ruff check src/polymaker/execution/rdp_placement.py src/polymaker/rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_runtime.py
```

Result: **All checks passed**.

The affected regression set and full suite were run with this socket/DNS guard:

```python
import socket
import sys
import pytest

attempts = []

def blocked(name):
    def deny(*args, **kwargs):
        attempts.append(name)
        raise AssertionError(f"network blocked by ITER-0017 test guard: {name}")
    return deny

socket.getaddrinfo = blocked("getaddrinfo")
socket.create_connection = blocked("create_connection")
socket.socket.connect = blocked("socket.connect")
socket.socket.connect_ex = blocked("socket.connect_ex")

code = pytest.main(TEST_ARGUMENTS)
print(f"NETWORK_GUARD_ATTEMPTS={len(attempts)}")
if attempts:
    print(f"NETWORK_GUARD_OPERATIONS={attempts!r}")
    raise SystemExit(2)
raise SystemExit(code)
```

Affected regression arguments were:

```text
-q
tests/test_rdp_placement.py
tests/test_rdp_runtime.py
tests/test_execution.py
tests/test_engine.py
tests/test_state.py
tests/test_rdp_account.py
tests/test_rdp_accounting.py
tests/test_rdp_capital.py
```

Full-suite arguments were:

```text
-q
```

## Test Results

- A first affected guarded run reported **364 passed, 1 failed**. The failure came from an added assertion that observe-only runtime left the processor’s pending set empty. The `admission_local_pending` scenario intentionally preloads pending capture to test admission rejection, so that assertion was incorrect. I removed only that assertion; the existing check that `placement_owner.current is None` verifies observe-only did not arm an RDP attempt.
- After that correction, the focused runtime/placement run passed: **81 passed**.
- Final affected guarded regression run: **365 passed in 62.90s**, `NETWORK_GUARD_ATTEMPTS=0`.
- Final full guarded suite: **1,262 passed, 2 skipped in 73.74s**, `NETWORK_GUARD_ATTEMPTS=0`.

## Regression Results

Existing runtime, gateway/execution, engine, state, account, accounting, and capital regression tests passed in the affected run. The full suite passed. No source changes were made after the final suite.

## Git Diff Summary

- Tracked diff: `src/polymaker/rdp_runtime.py` and `tests/test_rdp_runtime.py`, **48 insertions**.
- New implementation and test files: `rdp_placement.py` (370 lines) and `test_rdp_placement.py` (562 lines); these are untracked until Orchestrator archival.
- `git diff --check` passed.
- Final working tree contains the four implementation/test changes and the pre-existing untracked `research_outputs/`. Those research assets were preserved.
- No Git history operation was performed.

## Safety Checks

- No real network attempts occurred in the final affected or full test runs.
- Gateway placement-path tests used a fake SDK client; no real order was submitted, signed, or canceled.
- Production runtime additions contain no placement, cancellation, signing, retry, or order-adoption call.
- No Frozen SOP or shared-core implementation was changed.

## Known Limitations

This adds initial-pair capture and acknowledgment ownership only. Submission authorization, final submit revalidation, authoritative order-state reconciliation, cancellation, unknown-outcome recovery, and subsequent NORMAL/REBALANCE execution remain outside this contract.

## Deferred Findings

- Future submission work must add final market/time/account checks and maintain executor ownership.
- Unknown outcomes require authoritative reconciliation before any recovery action.
- FillWindow, later NORMAL admission, REBALANCE execution, and complete strategy metrics remain unimplemented.

## Reviewer Attention

Please verify that typed acknowledgments are fully validated before registration, that anomaly evidence survives processor replay, and that observed-only or conflicting IDs never become owned. Confirm the actual strict gateway fake-client path replays pre-ack fills once and preserves updated local remaining quantity.

## Human Live Trading Performed

NO
