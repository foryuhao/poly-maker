# ITER-0015 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-01 and MF-02 from `docs/development/ITER-0015/03_REVIEWER.md` with test-only changes. No production defect was indicated, and production source was not changed for this repair.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `70c3c0f8f12cad28c82791c523985344dcf4bdf6`
- Production file SHA-256 hashes match the Reviewer’s recorded hashes.
- Orchestrator-owned index/audit changes and preserved untracked assets were left untouched.

## Existing Components Reused

- Existing fake WebSocket and actual shared bounded-queue consumer fixture.
- Existing RDP Book Filter, retained public session, and `book_filter_result()` API.
- Existing production-generation checks and callback-emitted state behavior.

## Files Changed

- `tests/test_rdp_marketdata.py`

## Implementation Summary

The fake WebSocket test fixture now records a deterministic acknowledgment at the internal queue’s `put_nowait` boundary. The delayed-trade test verifies the trade entered that queue at receipt time `1.0` while the consumer was blocked, then processes it at `9.0` and evaluates at `9.1`.

Added production-wiring regressions for invalid receipt context after warmup, generation-1 history reset with stale backlog rejection, separate reversing messages within one frame, queued feed frames reaching the retained session result, and selected trade activity for both outcome tokens with foreign-event isolation.

## Behavior Before

The delayed-trade test advanced its fake clock before confirming the socket reader had consumed the trade. Its recorded trade receipt was therefore `9.0`, so it did not distinguish socket receipt from processing time. Several required production-wiring cases were also absent.

## Behavior After

The test acknowledges receipt at the internal bounded-queue insertion point and asserts the queued timestamp is `1.0` before releasing blocked processing. A temporary processing-time mutant made the test fail because the trade appeared recent. The mutation was reverted, and production hashes match the reviewed versions.

The added cases verify that invalid receipt context invalidates warmed evidence, generation 2 requires new warmup despite stale generation-1 backlog, separate reversing messages retain cumulative variation, and actual queued frames produce the retained session’s filter result.

## Tests Added / Changed

Added or strengthened tests for:

- Deterministic socket-reader queue acknowledgment and delayed processing.
- `None`, boolean, and NaN receipt context after valid warmup.
- Warm generation-1 history, rejected old backlog, and generation-2 warmup.
- Reversing messages inside one frame, contrasted with existing aggregate-message behavior.
- Queued feed-to-retained-session evaluation.
- YES and NO selected trade activation with foreign-event isolation.

## Tests Executed

All controlled pytest commands used the temporary network-block plugin shown below.

- `PYTHONPATH=/tmp${PYTHONPATH:+:$PYTHONPATH} uv run pytest -q -p rdp_network_block tests/test_rdp_marketdata.py tests/test_marketdata.py tests/test_rdp_runtime.py tests/test_rdp_book_filter.py` — **148 passed**, 0 network attempts.
- `PYTHONPATH=/tmp${PYTHONPATH:+:$PYTHONPATH} uv run pytest -q -p rdp_network_block tests/test_rdp_marketdata.py tests/test_marketdata.py tests/test_rdp_runtime.py tests/test_rdp_book_filter.py tests/test_rdp_live.py tests/test_rdp_userstream.py tests/test_rdp_capital.py` — **303 passed**, 0 network attempts.
- `PYTHONPATH=/tmp${PYTHONPATH:+:$PYTHONPATH} uv run pytest -q -p rdp_network_block` — **1,212 passed, 2 skipped**, 0 network attempts.
- `uv run ruff check src/polymaker/marketdata/service.py src/polymaker/rdp_marketdata.py src/polymaker/rdp_runtime.py tests/test_rdp_marketdata.py tests/test_rdp_runtime.py` — **passed**.
- `git diff --check` — **passed**.

The mutation probe temporarily substituted `time.monotonic()` for the queued receipt assignment, ran `uv run pytest -q -p rdp_network_block tests/test_rdp_marketdata.py::test_queue_uses_socket_receipt_not_delayed_trade_processing_time`, and observed the expected failure: `real_trade_in_2s` became `True`. The source was restored before final checks.

Temporary plugin contents:

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

## Test Results

All final focused, affected, and full-suite checks passed. Each network-blocked command reported `NETWORK_BLOCKED_ATTEMPTS=0`.

## Regression Results

Shared market-data, RDP feed/filter/runtime, bootstrap, user-stream, capital, and full-suite regressions passed.

## Git Diff Summary

This repair changed only `tests/test_rdp_marketdata.py`. No Git history operations were performed. Existing Orchestrator-owned changes and untracked `research_outputs/` assets were preserved.

## Safety Checks

No live network calls, credentials, account actions, orders, or cancellations were performed. Production source was unchanged by this repair.

## Known Limitations

None introduced by this test-only repair.

## Deferred Findings

The contract’s deferred NORMAL admission composition, current account/order reliability, execution lifecycle, and remaining runtime metrics remain outside this iteration.

## Reviewer Attention

Verify that the acknowledged receipt is taken from the actual internal queue insertion, that the mutant failure demonstrates processing-time substitution would fail, and that the generation/session tests exercise the existing production components.

## Human Live Trading Performed

NO
