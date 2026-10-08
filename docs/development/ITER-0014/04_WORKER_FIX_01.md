# ITER-0014 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-01 through MF-04 in the approved ITER-0014 contract and immutable `03_REVIEWER.md`. The change monitors public health during authenticated startup, rejects market expiry at the final startup boundary, records sanitized failure and cancellation outcomes, and adds a composed integration test using the real retained owners.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `96071c399c1f4aca216324e5c30530855b57af68`
- Existing ITER-0014 changes and untracked research and audit assets were preserved.
- No Git history operations were performed.

## Existing Components Reused

The implementation reuses the existing public market-data owner, authenticated user-stream owner, account capture and backing evaluator, `StateStore`, `UserEventProcessor`, confirmed-fill projection, and `rdp-live` CLI.

## Files Changed

Repair edits are limited to:

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/rdp_userstream.py`
- `src/polymaker/cli.py`
- `tests/test_rdp_runtime.py`

`rdp_userstream.py` now resolves injectable gateway, stream, and store factories when called, enabling tests to exercise the real retained owners with fake transports.

## Implementation Summary

Public generation, fatal feed status, disconnection, and market expiry are checked while authenticated startup and account reads are pending. On failure or cancellation, the startup task is cancelled and drained; a user owner returned at the same time as a public failure is closed before the failure propagates.

Startup checks market expiry at the final certification boundary before logging success or returning the session. The observer records categorized bootstrap/feed/account/user failures and cancellation during startup, observation, and cleanup. CLI failure output includes the category but omits exception details.

The composed test drives the real CLI observer, public owner, user stream, account capture, backing evaluator, and retained processor using fake WebSockets and a fake derive-only gateway. On the shared retained processor, a synthetic MATCHED event produces no inventory, while CONFIRMED evidence produces the expected inventory. The test also exercises dynamic tick updates and selected-inventory backing rejection.

## Behavior Before

Public health was not monitored until authenticated startup and account capture returned. Startup could pass its expiry check and then succeed after the market ended. Some categorized startup failures and cancellations lacked durable termination evidence. Composed tests did not establish retained ownership through the real observer path.

## Behavior After

Public health loss during user startup cancels and drains that startup operation. Simultaneous public failure and user-session completion closes the returned owner and preserves the public failure category. Expiry at final startup certification fails closed. The observer logs sanitized categories for startup failures and cancellation, including cancellation during cleanup.

## Tests Added / Changed

Added regressions for:

- Public failure while authenticated startup is suspended.
- Public failure concurrent with user-session completion and owner cleanup.
- Expiry during final startup certification.
- Actual `rdp-live --observe-only` composition through market end with real retained owners and fake WebSockets/gateway.
- Shared processor MATCHED versus CONFIRMED synthetic fill projection.
- Actual backing evaluator rejection of selected inventory.
- Sanitized categorized startup failure logging.
- Cancellation during startup with task draining and termination logging.

## Tests Executed

Focused:

```bash
uv run pytest -q tests/test_rdp_runtime.py
```

Affected RDP/account/userstream suite, with socket and DNS operations blocked:

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

Full suite, with socket and DNS operations blocked:

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
uv run ruff check src/polymaker/rdp_runtime.py src/polymaker/rdp_userstream.py src/polymaker/cli.py tests/test_rdp_runtime.py
uv run polymaker rdp-live --help
git diff --check
```

## Test Results

- Focused runtime tests: **19 passed**.
- Affected RDP/account/userstream tests: **256 passed**, `NETWORK_ATTEMPTS=0`.
- Full suite: **1171 passed, 2 skipped**, `NETWORK_ATTEMPTS=0`.
- Scoped Ruff: passed.
- CLI help: passed and displays `--observe-only`.
- `git diff --check`: passed.

Early fixture runs exposed and corrected a coroutine-versus-async-context-manager mismatch, an environment-derived credential alias in the test configuration, and incomplete readiness book frames. The final test runs above passed. An initial Ruff run also identified an exception-chain lint and import ordering; these were corrected before the final runs.

## Regression Results

The full suite passed. Existing bounded account and market-data APIs were exercised by the affected suite. No FL or IM changes were made.

## Git Diff Summary

No commits or other Git history changes were made. The working tree also contains other ITER-0014 changes, audit files, and preserved untracked `research_outputs/`; these were not removed or rewritten.

`git diff --stat` for tracked changes reported 6 files changed, 267 insertions, and 42 deletions. This excludes untracked files, including `src/polymaker/rdp_runtime.py` and `tests/test_rdp_runtime.py`.

## Safety Checks

All validation blocked socket connections and DNS lookups; the affected and full runs recorded zero attempts. The composed test uses fake WebSockets and a fake gateway. The gateway only connects in derive-only mode and raises immediately if placement or cancellation is attempted. No order, cancellation, signing, or real account action was performed.

## Known Limitations

This remains a read-only observation path. It does not implement trading orchestration, continuous account refresh, reconnect recovery, or MICRO_LIVE_READY.

## Deferred Findings

The contract’s deferred trading, reconciliation, continuous account refresh, and Phase 0 metrics remain unresolved and outside this repair scope.

## Reviewer Attention

Please verify the final startup expiry boundary, simultaneous starter/public-failure ownership cleanup, sanitized cancellation logging, and the real-owner CLI composition test with its zero-network guard.

## Human Live Trading Performed

NO
