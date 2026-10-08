# ITER-0015 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved ITER-0015 contract in `docs/development/ITER-0015/01_ARCHITECT.md`. Scope stayed limited to receipt-time handoff, RDP Book Filter feed wiring, retained-session evaluation, and relevant tests.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Starting HEAD: `70c3c0f8f12cad28c82791c523985344dcf4bdf6`
- The contract artifact records an earlier base HEAD; implementation proceeded on the Orchestrator-authorized current HEAD.
- Preserved untracked `research_outputs/` assets.

## Existing Components Reused

- Shared bounded market-data queue, mutation callbacks, parser, and connection generation.
- Existing `RDPBookFilter`, `TradePrint`, authoritative order books, and dynamic tick handling.
- Existing retained public and user session health checks.

## Files Changed

- `src/polymaker/marketdata/service.py`
- `src/polymaker/rdp_marketdata.py`
- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_marketdata.py`
- `tests/test_rdp_runtime.py`

## Implementation Summary

The shared queue consumer exposes the queued monotonic receipt time only while processing a frame and its callbacks, clearing it in `finally`. RDP’s market-data service owns one existing filter instance and feeds it selected, current-generation book states and parsed trades. Tick changes are observed after the shared tick mutation. Selected trades are validated before any frame mutation, preserving whole-frame rejection for malformed attributable trades.

`RDPInputSession.book_filter_result()` evaluates the filter using current authoritative books and ticks, monotonic time, market interval, retained public generation, and public/user owner health. Unreliable, expired, or closed sessions return fail-closed evidence.

## Behavior Before

RDP recorded selected books, deltas, and ticks, but did not feed production market-data callbacks into the Book Filter or expose its result through the retained session. Ordinary callbacks had no access to the queue’s monotonic receipt timestamp.

## Behavior After

Selected current-generation feed events update the existing filter using socket receipt time. Delayed queue processing cannot renew a trade’s rolling-window activity. The retained session returns the filter’s immutable result only while its feed and session state remain reliable.

## Tests Added / Changed

Added controlled fake-WebSocket tests covering queue receipt availability and cleanup, delayed trades, aggregate callback behavior, multiple-message cumulative variation, exact-five variation, unchanged snapshots, warmup, Depth Extreme, Fast Move, selected/foreign trades, malformed-trade atomicity, dynamic tick reset, and stale-generation rejection. Added retained-session checks for public/user failure, expiry, closure, and healthy evaluation.

## Tests Executed

- `uv run pytest -q tests/test_rdp_marketdata.py tests/test_marketdata.py tests/test_rdp_runtime.py tests/test_rdp_book_filter.py` — **141 passed**.
- `PYTHONPATH=/tmp${PYTHONPATH:+:$PYTHONPATH} uv run pytest -q -p rdp_network_block tests/test_rdp_marketdata.py tests/test_marketdata.py tests/test_rdp_runtime.py tests/test_rdp_book_filter.py tests/test_rdp_live.py tests/test_rdp_userstream.py tests/test_rdp_capital.py` — **296 passed**, 0 network attempts.
- `PYTHONPATH=/tmp${PYTHONPATH:+:$PYTHONPATH} uv run pytest -q -p rdp_network_block` — **1,205 passed, 2 skipped**, 0 network attempts.
- `uv run ruff check src/polymaker/marketdata/service.py src/polymaker/rdp_marketdata.py src/polymaker/rdp_runtime.py tests/test_rdp_marketdata.py tests/test_rdp_runtime.py` — **passed**.
- `git diff --check` — **passed**.

The full-suite command used this temporary `/tmp/rdp_network_block.py` plugin:

```python
import socket

import pytest

attempts = []
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo


def _blocked(kind):
    def call(*args, **kwargs):
        attempts.append(kind)
        raise AssertionError(f"network blocked: {kind}")
    return call


def pytest_configure(config):
    socket.socket.connect = _blocked("connect")
    socket.socket.connect_ex = _blocked("connect_ex")
    socket.getaddrinfo = _blocked("dns")


def pytest_unconfigure(config):
    socket.socket.connect = _original_connect
    socket.socket.connect_ex = _original_connect_ex
    socket.getaddrinfo = _original_getaddrinfo


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    print(f"NETWORK_BLOCKED_ATTEMPTS={len(attempts)}")
    if attempts:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
```

An earlier run of the focused command had one failure in `test_depth_extreme_and_fast_move_reach_filter_through_socket_queue[fast_move]`: the fixture’s price update fell outside the filter’s one-second excursion window. The fixture was corrected to provide an in-window anchor; the final focused, affected, and full-suite commands above passed.

## Test Results

Final focused, affected, and full-suite results passed. The network-block plugin reported `NETWORK_BLOCKED_ATTEMPTS=0`.

## Regression Results

Shared market-data receipt/research behavior, RDP feed, filter, runtime, bootstrap, user-stream, and capital regressions passed. The full suite passed with network access blocked.

## Git Diff Summary

Five permitted files changed: **427 insertions, 2 deletions**. No commits or other Git history operations were performed. The only untracked paths are the preserved `research_outputs/` assets.

## Safety Checks

No live HTTP/WebSocket calls, credentials, orders, cancellations, or account actions were performed. No runtime interface or execution authority was added. Shared-core behavior is limited to the temporary processing receipt context.

## Known Limitations

This exposes Book Filter evidence through the retained session; it does not compose that evidence into NORMAL admission or authorize execution.

## Deferred Findings

NORMAL candidate/gate composition, current account/order reliability, execution lifecycle integration, and remaining required runtime metrics remain deferred as specified by the contract.

## Reviewer Attention

Verify that selected trade validation precedes all frame mutation, receipt context clears on both success and processing failure, and session evaluation derives reliability solely from retained owners and market time.

## Human Live Trading Performed

NO
