# ITER-0014 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-06 from immutable `07_REVIEWER_REREVIEW_02.md`. Both failed-startup drain branches preserve their failure category and cleanup behavior while preventing raw child teardown messages from appearing in the outward exception chain.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `96071c399c1f4aca216324e5c30530855b57af68`
- Existing iteration changes, audit artifacts, and `research_outputs/` were preserved.
- No Git history operations were performed.

## Existing Components Reused

Reused the existing startup drain helper, observer error categories, sanitized structured logging, and test fixtures. No new subsystem was added.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_runtime.py`

Both files remain untracked as part of the current ITER-0014 working tree.

## Implementation Summary

When user-startup teardown fails during public failure or market expiry, the observer logs only the safe drain category and exception type. The outward `RDPObservationError` retains the original public/expiry category and suppresses the raw exception chain.

Parameterized regressions cover both triggers. They check the returned exception message and formatted traceback for a secret sentinel, assert its cause and context are absent, and verify cleanup and sanitized diagnostic fields.

## Behavior Before

The public/expiry category and cleanup were correct, but raising from the raw teardown error exposed its message through `__cause__` and formatted tracebacks.

## Behavior After

The observer preserves `FEED_DISCONNECTED` or `MARKET_INTERVAL_ELAPSED`, completes cleanup, and reports the teardown exception type without exposing its message through the returned exception or formatted traceback.

## Tests Added / Changed

Updated the teardown regression to cover:

- Public failure during startup drain.
- Market expiry during startup drain.
- No sentinel in `str(exception)` or its formatted traceback.
- No raw exception cause or context.
- Sanitized `USER_STARTUP_DRAIN_FAILED` event with `RuntimeError` type.
- Public-owner cleanup.

## Tests Executed

Focused regression:

```bash
uv run pytest -q tests/test_rdp_runtime.py::test_child_teardown_error_is_redacted_without_losing_category
```

Focused runtime suite:

```bash
uv run pytest -q tests/test_rdp_runtime.py
```

Affected RDP/account/userstream suite, with socket and DNS blocked:

```bash
uv run python - <<'PY'
import socket
import pytest

attempts = []
def blocked(*args, **kwargs):
    attempts.append("socket_or_dns")
    raise AssertionError("blocked socket/DNS attempted")
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.getaddrinfo = blocked
result = pytest.main([
    "-q",
    "tests/test_rdp_runtime.py",
    "tests/test_rdp_marketdata.py",
    "tests/test_rdp_live.py",
    "tests/test_rdp_account.py",
    "tests/test_rdp_userstream.py",
    "tests/test_rdp_capital.py",
])
print(f"NETWORK_ATTEMPTS={len(attempts)}")
raise SystemExit(result)
PY
```

Full suite, with socket and DNS blocked:

```bash
uv run python - <<'PY'
import socket
import pytest

attempts = []
def blocked(*args, **kwargs):
    attempts.append("socket_or_dns")
    raise AssertionError("blocked socket/DNS attempted")
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.getaddrinfo = blocked
result = pytest.main(["-q"])
print(f"NETWORK_ATTEMPTS={len(attempts)}")
raise SystemExit(result)
PY
```

Additional checks:

```bash
uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_runtime.py
git diff --check
```

## Test Results

- Focused teardown regressions: **2 passed**.
- Focused runtime suite: **26 passed**.
- Affected suite: **263 passed**, `NETWORK_ATTEMPTS=0`.
- Full suite: **1178 passed, 2 skipped**, `NETWORK_ATTEMPTS=0`.
- Scoped Ruff: passed.
- `git diff --check`: passed.

An initial assertion tried to capture the structured logger through pytest’s text capture. The logger uses a different sink, so the test was changed to inspect the structured logging call directly; final tests pass.

## Regression Results

Repeated cancellation behavior and the previous public-health, expiry, and real-owner scenarios remain covered by the runtime suite. Full regression testing passed.

## Git Diff Summary

No commit, push, or other Git history operation was performed. The tracked `git diff --stat` reflects earlier ITER-0014 changes and excludes the untracked runtime and test files. Other working-tree and historical assets were preserved.

## Safety Checks

The affected and full suites blocked socket connections and DNS lookups and recorded zero attempts. No orders, cancellations, signing, or real account actions were performed.

## Known Limitations

The ITER-0014 path remains read-only and does not establish MICRO_LIVE_READY.

## Deferred Findings

No deferred findings were changed by this repair.

## Reviewer Attention

Please verify both redacted drain-error branches, including absence of the original message from exception cause/context and formatted traceback.

## Human Live Trading Performed

NO
