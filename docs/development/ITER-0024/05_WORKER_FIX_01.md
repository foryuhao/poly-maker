# ITER-0024 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repair `MUST_FIX-01` in the approved ITER-0024 contract. Each worker-thread bridge request now retains a monotonic deadline derived from the existing account, status, and market-data freshness limits. The owning-loop callback rejects an expired request before mutable-state capture and checks again before publishing its result. If `Event.wait()` returns false, the worker unconditionally returns timeout; a result or event arriving afterward cannot make that call eligible.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `322fa5be51ec904ae6b9d1394cbc505c683876fa`
- The working tree also contains Orchestrator audit/index artifacts and preserved untracked `research_outputs/`; I did not modify those.
- No Git operations, audit writes, live network actions, or trading actions were performed.

## Existing Components Reused

Reused the ITER-0024 session-bound admission validator, existing freshness limits, event-loop callback bridge, retained categorical result logging, gateway guard locations, and fake SDK executor tests.

## Files Changed

Worker changes are limited to:

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

No gateway, processor, shared-core, CLI, configuration, dependency, or Frozen SOP changes.

## Implementation Summary

Bridge requests carry a monotonic deadline and a lock coordinating result, timeout, and abandonment state. The loop callback checks expiry before capture and after capture. A capture that finishes after timeout cannot publish a positive result. A false timed wait always fails closed, even if completion races with timeout handling.

Added deterministic tests for delivery of a queued callback after `Event.wait()` expires, a positive capture returning after the worker has timed out, and an actual gateway final-guard timeout that prevents POST. The existing first-guard denial test continues to verify zero signatures and zero POSTs.

## Behavior Before

A queued callback could capture state after the bridge’s wait deadline. The worker could then observe the late completion and return a positive result despite `Event.wait()` having returned false.

## Behavior After

A callback delivered after its retained deadline returns categorical timeout without capturing mutable state. A capture completing after the worker abandons the request cannot publish its result. A false `Event.wait()` always returns timeout. The actual gateway final guard denies posting on bridge timeout.

## Tests Added / Changed

Added direct tests for the real wait-expired interleaving, late positive capture completion, and gateway final-guard timeout. Existing bridge timeout, teardown, unavailable-loop, first-guard zero-sign, and signing-time invalidation tests remain in the matrix.

## Tests Executed

These guarded pytest commands used `/tmp/rdp_phase0_network_guard.py`. To reproduce, save this exact helper there:

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

- Focused intent and bridge matrix:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py -k 'initial_intent_revalidation or real_gateway_guard_revalidates or initial_intent_bridge or real_gateway_first_guard'`  
  **50 passed, 260 deselected**

- Actual gateway bridge-timeout test:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py -k real_gateway_bridge_timeout`  
  **1 passed, 309 deselected**

- Affected integration set:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py tests/test_rdp_runtime.py tests/test_execution.py tests/test_rdp_capital.py tests/test_rdp_book_filter.py`  
  **601 passed**

- FL regression set:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_favorite_first.py tests/test_favorite_first_review.py tests/test_favorite_live.py tests/test_favorite_report.py`  
  **106 passed**

- Full suite:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q`  
  **1,584 passed, 2 skipped**

- Lint:  
  `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py`  
  **Passed**

- Whitespace check:  
  `git diff --check`  
  **Passed**

The global DNS/socket guard recorded zero network attempts.

## Test Results

The new deadline-race and gateway-timeout cases pass. No failures occurred in this repair’s final focused, affected, FL, or full-suite runs.

The earlier ITER-0024 implementation run had eight teardown-cancellation failures after an unshielded yield was added to `aclose()`. That yield was removed, and these exact cases passed in the subsequent verification:

- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-open_orders]`
- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-cancel]`
- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-get_order]`
- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-get_trades]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-open_orders]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-cancel]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-get_order]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-get_trades]`

## Regression Results

Affected RDP placement/runtime/execution/capital/filter tests passed. FL predecessor tests passed. The final full suite passed.

## Git Diff Summary

Worker-owned source/test scope: two files. Current diff against HEAD includes the prior ITER-0024 implementation and this repair; source/test diff reports **1,384 insertions, 3 deletions**.

Final SHA-256:

- `src/polymaker/rdp_runtime.py`: `86a59e7359e64c35163f042cb5a4a185939bce72df34a4cb521e08ce747100ce`
- `tests/test_rdp_placement.py`: `82edf84329ac1afc816e892d87747091ca3fc730fef481d77e57d451cc848ffb`

## Safety Checks

No real-money orders, cancellations, or live network actions were performed. Fake gateway tests verify first-guard denial produces zero signatures and zero POSTs, signing-time invalidation produces zero POSTs, and bridge timeout at the actual final gateway guard produces zero POSTs. Factual validation remains separate from execution authorization.

## Known Limitations

This implements only initial-intent factual revalidation. It does not invoke production placement, complete human or operational-time gates, or implement subsequent NORMAL/REBALANCE operation.

## Deferred Findings

Production gated submission still needs to enforce all remaining execution gates and own the gateway operation and outcome, as recorded in the Architect Contract.

## Reviewer Attention

Please independently inspect the retained monotonic deadline, pre-capture and post-capture expiry checks, unconditional false-wait timeout branch, and the three deadline-race/gateway-timeout regressions.

## Human Live Trading Performed

NO
