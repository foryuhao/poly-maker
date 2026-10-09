# ITER-0024 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented `docs/development/ITER-0024/01_ARCHITECT.md`: retain actual session admission provenance, revalidate the exact armed initial NORMAL intent at both existing gateway guard points, and bridge SDK-worker callbacks to one coherent event-loop capture. No production `gateway.place()` call or complete execution authorization was added.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Starting HEAD: `322fa5be51ec904ae6b9d1394cbc505c683876fa`
- Preserved pre-existing untracked `research_outputs/` assets.
- No commits, pushes, audit writes, or live network/trading actions were performed.

## Existing Components Reused

Reused initial admission and retained account/status evidence, the placement owner and pre-ack processor capture, existing freshness/health/generation/backlog checks, book filter, dynamic metadata, quote builder, capital projection and $5 reserve gate, and the gateway’s existing two submit-guard locations.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

No gateway, processor, shared-core, CLI, configuration, dependency, or Frozen SOP changes.

## Implementation Summary

The session retains the exact eligible admission and account evidence it issued, then binds validation to that admission and the exact armed attempt. Revalidation captures current state on the owning event loop and verifies scope, pre-ack cleanliness, health and generations, market/account/book freshness, filter and metadata, unchanged quotes and signing ticks, and CapitalGate backing.

The callback works on the event loop directly or uses a bounded worker-thread bridge. Timeout, unavailable loop, teardown, and evaluation failures reject. Abandoned queued callbacks skip mutable-state capture. Results are retained as categorical evidence; factual validity remains separate from execution authorization.

## Behavior Before

There was no session-bound callback to revalidate initial NORMAL intent at the gateway’s existing pre-sign and final pre-POST guard points.

## Behavior After

The exact admitted and armed intent can be revalidated at both guard points. Changes during signing prevent posting; first-guard rejection prevents signing and posting. The final gateway check runs on the event loop even though the callback originates on the SDK executor.

## Tests Added / Changed

Added direct coverage for actual admission-to-arm validation, copied and foreign provenance, quote/tick/minimum changes, filter rejection and readiness, PriceGate rejection, exact status/account/book freshness boundaries, invalid receipts, cash/allowance backing, processor contamination, session-health changes, bridge timeout/closed-loop/teardown behavior, and actual gateway guard execution before and after signing.

## Tests Executed

The test commands below used a process-start global DNS/socket guard. To reproduce, save this exact helper as `/tmp/rdp_phase0_network_guard.py`:

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

Commands and final results:

- Focused final matrix:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py -k 'initial_intent_revalidation or real_gateway_guard_revalidates or initial_intent_bridge or real_gateway_first_guard'`  
  **48 passed, 259 deselected**

- Teardown and bridge subset:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py -k 'teardown and (drains_before_close_ownership_clears or coordinator_drains_each_real_gateway_operation_once) or initial_intent_bridge'`  
  **11 passed, 296 deselected**

- Affected integration set:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_rdp_placement.py tests/test_rdp_runtime.py tests/test_execution.py tests/test_rdp_capital.py tests/test_rdp_book_filter.py`  
  **598 passed**

- FL regression set:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q tests/test_favorite_first.py tests/test_favorite_first_review.py tests/test_favorite_live.py tests/test_favorite_report.py`  
  **106 passed**

- Full suite:  
  `uv run python /tmp/rdp_phase0_network_guard.py -q`  
  **1,581 passed, 2 skipped**

- Lint:  
  `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py`  
  **Passed**

- Whitespace validation:  
  `git diff --check`  
  **Passed**

## Test Results

The first affected integration run produced **8 failures and 590 passes**. All eight were teardown-cancellation expectations caused by an added unshielded yield in `aclose()`:

- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-open_orders]`
- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-cancel]`
- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-get_order]`
- `test_each_real_gateway_operation_drains_before_close_ownership_clears[teardown-get_trades]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-open_orders]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-cancel]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-get_order]`
- `test_coordinator_drains_each_real_gateway_operation_once[teardown-get_trades]`

I removed the unshielded yield; abandonment is synchronous, and queued callbacks skip via their abandonment flag. The teardown subset and full affected set passed afterward.

An initial freshness-boundary test expectation was corrected to match existing semantics: account evidence is stale only when age is greater than its limit, while status and book freshness reject at their exact limits. The bridge-timeout test was made deterministic by holding the queued callback until the worker timed out, then invoking it to confirm it skips capture.

The global guard recorded **zero blocked DNS/socket attempts**.

## Regression Results

Affected RDP execution, runtime, placement, capital, and filter tests passed. FL predecessor regression tests passed. The full suite passed.

## Git Diff Summary

Two tracked files changed: runtime implementation and placement tests. Diff: **1,187 insertions, 3 deletions**. Pre-existing untracked `research_outputs/` assets remain untouched.

Final file SHA-256:

- `src/polymaker/rdp_runtime.py`: `1938d6726c2c0d756241820f95df687b16ab3690653eb6ba74eadad8f18c697b`
- `tests/test_rdp_placement.py`: `c20532897072e8f5f10e9d8f4275154e818adf0705b3bc9168627ae988111ee0`

## Safety Checks

No real-money orders, cancellations, or live network actions were performed. Fake SDK integration verified zero signatures and zero POSTs on first-guard denial, and zero POSTs when state changed during signing. Revalidation cannot grant execution authorization; the result continues to expose outstanding gates.

## Known Limitations

This implements only initial-intent factual revalidation. It does not invoke production placement, complete human or operational-time gates, or deliver subsequent NORMAL/REBALANCE operation.

## Deferred Findings

Production gated submission must still enforce all remaining execution gates and own the gateway operation and outcome, as recorded in the Architect Contract.

## Reviewer Attention

Please independently inspect admission provenance retention, exact intent comparison, timestamp boundaries, bridge timeout/abandonment behavior, and the test proving invalidation during signing prevents POST.

## Human Live Trading Performed

NO
