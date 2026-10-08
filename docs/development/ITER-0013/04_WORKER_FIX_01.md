# ITER-0013 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-01 through MF-04 within the original ITER-0013 contract: keep PING cadence independent of PONG delay, propagate cancellation received during cleanup, preserve recognized failure categories, and redact diagnostics from the installed WebSockets protocol logger on the opt-in RDP connection.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `46a774d9a9e7bda38823e7ab0da9825a2148b280`
- Preserved orchestrator audit files and untracked `research_outputs/`; did not modify them.
- No Git history operations performed.

## Existing Components Reused

Reused the existing `UserStream`, installed WebSockets `ClientProtocol`, account observer cleanup, and existing fake WebSocket/gateway test fixtures. No transport or parser was duplicated.

## Files Changed

This repair changed:

- `src/polymaker/userstream/client.py`
- `src/polymaker/rdp_userstream.py`
- `tests/test_rdp_userstream.py`

## Implementation Summary

The heartbeat now schedules PINGs on monotonic fixed intervals and tracks the oldest unanswered PING, so delayed responses do not shift cadence or extend the timeout indefinitely. Cleanup records cancellation received while draining owned tasks or closing the gateway, finishes all cleanup, then propagates cancellation. `run_once()` assigns a generic category only when no specific failure has already been recorded.

For sanitized, opt-in RDP streams, `websockets.connect` receives a per-instance logger. It records redacted frame markers and exception types without formatting wire payloads or exception text. Default callers receive no logger override.

## Behavior Before

A delayed but timely PONG delayed the next PING. Cancellation first received during successful cleanup could be swallowed. A teardown exception could replace a recognized rejection or heartbeat-timeout category. Installed WebSockets frame diagnostics could log subscription credentials.

## Behavior After

PING cadence is independent of response latency, while timeout remains bounded from the oldest unanswered request. Cleanup propagates cancellation only after owned resources drain. Specific failure categories survive teardown errors. The RDP connection’s installed protocol logger redacts frames and exception details; the default UserStream path remains unchanged.

## Tests Added / Changed

Added regressions for delayed PONG cadence, oldest-outstanding timeout, cancellation first received during successful stream and gateway cleanup, repeated cancellation, rejection and timeout category preservation through teardown exceptions, and redaction of installed Sans-I/O protocol frame and exception logs. The default-mode test verifies no application PING or logger override.

## Tests Executed

- `uv run pytest -q tests/test_rdp_userstream.py`
- Scoped Ruff:
  `uv run ruff check src/polymaker/userstream/client.py src/polymaker/rdp_userstream.py tests/test_rdp_userstream.py tests/test_rdp_live.py`
- Focused and full suites were also run with socket connection and DNS functions patched to reject and count network attempts. The full-suite command was:

```bash
uv run python - <<'PY'
import socket
import sys
import pytest

attempts = []

def deny(name):
    def blocked(*_args, **_kwargs):
        attempts.append(name)
        raise AssertionError(f"network blocked: {name}")
    return blocked

socket.socket.connect = deny("socket.connect")
socket.socket.connect_ex = deny("socket.connect_ex")
socket.getaddrinfo = deny("socket.getaddrinfo")
status = pytest.main(["-q"])
print(f"NETWORK_ATTEMPTS={len(attempts)}")
if attempts:
    print(f"NETWORK_ATTEMPT_METHODS={attempts}")
sys.exit(int(status) or bool(attempts))
PY
```

- `git diff --check`

## Test Results

- `tests/test_rdp_userstream.py`: **20 passed**
- Focused RDP/account/userstream/fill-truth/preflight tests with network blocked: **240 passed; 0 network attempts**
- Full suite with network blocked: **1,147 passed, 2 skipped; 0 network attempts**
- Scoped Ruff: passed.
- `git diff --check`: passed.

## Regression Results

The delayed-PONG test verifies scheduled sends continue during a response delay, and the silent-stream test verifies timeout is measured from the oldest unanswered PING. Cleanup tests verify cancellation received during successful cleanup, including repeated cancellation, remains caller-visible only after stream, store, and gateway cleanup finish. Failure tests verify authentication rejection and heartbeat timeout retain their categories through subsequent transport errors. Installed `ClientProtocol` tests verify credential sentinels are absent from DEBUG frame and ERROR exception logs.

This repair made no external requests. Separately, the original Worker run’s accidental read-only activity remains recorded in the immutable original report and Reviewer NOTE: two parameterized startup-backing test cases used stale mocks and reached existing-key derivation (`derive_only=True`), official account GETs, and an authenticated user WebSocket subscription before failing on unresolved spender metadata. The logs showed GETs to `/time`, `/data/orders`, Data API `/v2/positions`, and `/version`; balance/allowance reads were not reached. No credential creation, order, cancellation, allowance update, or RPC action occurred. Subsequent and repair tests used mocks; this repair’s network-blocked full suite recorded zero attempts.

## Git Diff Summary

Three implementation/test files changed for this repair. Current HEAD remains `46a774d9a9e7bda38823e7ab0da9825a2148b280`; no commit or other Git history operation performed.

## Safety Checks

No live network call or trading action occurred during this repair. The network-blocked test runs recorded zero socket or DNS attempts. Sanitized protocol logging is enabled only for the opt-in RDP stream; the default UserStream mode remains unchanged.

## Known Limitations

PONG remains transport evidence, not proof of authentication or subscription acceptance. Long-running runtime ownership and reconnect reconciliation remain deferred.

## Deferred Findings

No new deferred findings. Existing long-running RDP runtime and authoritative reconnect recovery work remains outside this contract.

## Reviewer Attention

Verify cadence with delayed PONGs, cancellation propagation during both cleanup stages, first-failure retention, and installed protocol log redaction. Check that default UserStream callers do not receive the sanitized logger override.

## Human Live Trading Performed

NO
