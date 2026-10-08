# ITER-0014 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-03, MF-04, and MF-05 from immutable `05_REVIEWER_REREVIEW_01.md` within the original ITER-0014 contract. Kept the previously repaired public-health monitoring and final startup-expiry check intact.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `96071c399c1f4aca216324e5c30530855b57af68`
- Existing iteration changes and untracked audit/research assets were preserved.
- No Git history operations were performed.

## Existing Components Reused

Reused the existing runtime observer, public market-data owner, authenticated user-stream owner, account-read gateway, backing evaluator, in-memory store, processor, and CLI.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_runtime.py`

Both files are untracked as part of the current ITER-0014 working tree. Other modified and untracked files were preserved.

## Implementation Summary

The observer now retains selected condition, available public/user generations, and the supplied clock for startup termination logs. Context is recorded as bootstrap and startup progress, so earlier failures retain the scope available at their point of failure.

Failed-startup draining distinguishes child cancellation from cancellation of the owning observer. It drains through repeated owner cancellation before propagating `CancelledError`. Child teardown errors are logged without unsafe details and remain chained to the categorized public failure.

The existing real-owner integration test now covers post-start book and tick changes, actual selected-state rejection and cleanup, health failure at the market-end boundary, exact public/user subscription scope, secret-free logs and evidence, and repeated cancellation during actual account-read and gateway cleanup.

## Behavior Before

Startup failure records could omit known market and generation context. Cancellation of the observer during failed-startup draining could be swallowed. The composed test did not exercise post-start market updates, actual selected-state startup rejection, termination-boundary health changes, or cancellation through the real account-read and gateway-drain path.

## Behavior After

Termination records include available selected scope and generations and use the supplied observation clock. Owner cancellation propagates only after owned work drains, including repeated cancellation. Child teardown errors remain observable. The real-owner test covers the previously missing integration cases and verifies cleanup and secret redaction.

## Tests Added / Changed

Extended `test_cli_observe_only_uses_real_retained_owners_and_ends_market` with `market_end`, `selected_state`, `health_at_end`, and `cancel_account_drain` scenarios.

Added tests for owner cancellation during failed-startup drain, child teardown error propagation while preserving public failure category, and earlier bootstrap failure logging with the supplied clock.

## Tests Executed

Focused:

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
uv run ruff check src/polymaker/rdp_runtime.py src/polymaker/rdp_userstream.py src/polymaker/cli.py tests/test_rdp_runtime.py
uv run polymaker rdp-live --help
git diff --check
```

## Test Results

- Focused runtime tests: **25 passed**.
- Affected RDP/account/userstream tests: **262 passed**, `NETWORK_ATTEMPTS=0`.
- Full suite: **1177 passed, 2 skipped**, `NETWORK_ATTEMPTS=0`.
- Scoped Ruff: passed.
- CLI help: passed.
- `git diff --check`: passed.

The first lint pass found one async polling pattern in the new test harness; it was replaced with an event before final test runs.

## Regression Results

The full suite passed. The actual-owner integration verifies that account reads drain before gateway close, the store and both sockets close, and cancellation propagates after cleanup. Public failure categories remain intact when child teardown raises.

## Git Diff Summary

No commit, push, or other Git history operation was performed. The tracked `git diff --stat` includes prior ITER-0014 work and excludes the untracked runtime and runtime-test files. Existing `research_outputs/` and audit artifacts were preserved.

## Safety Checks

All affected and full test runs blocked socket connections and DNS and recorded zero attempts. The composed tests use fake WebSockets and a fake derive-only gateway. No order, cancellation, signing, or real account action was performed.

## Known Limitations

This iteration remains a read-only observation path and does not establish MICRO_LIVE_READY.

## Deferred Findings

Trading orchestration, continuous account refresh, reconnect reconciliation, and complete Phase 0 metrics remain deferred.

## Reviewer Attention

Please review the retained startup context, owner-cancellation versus child-cancellation distinction, chained child teardown errors, and the expanded real-owner integration scenarios.

## Human Live Trading Performed

NO
