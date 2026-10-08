# ITER-0016 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved initial NORMAL admission for an empty RDP run, using the retained runtime session and existing account, filter, quote, backing, and capital components. No strategy semantics or execution authority changed.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `e5e3ef46238a8df8ea235aeaf300db7a50d99ad0`
- Architect contract records HEAD `178870e66476c33add8dbaee5b3f26153355fa64`.
- Preserved untracked `research_outputs/` assets were left untouched.

## Existing Components Reused

The implementation reuses the retained input session and gateway, `_capture_account_with_gateway`, `evaluate_initial_rdp_backing`, `project_rdp_capital`, the production-wired Book Filter, the NORMAL quote builder, CapitalGate, and existing sanitized-error and cleanup conventions.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_runtime.py`

## Implementation Summary

Added an immutable initial-admission result and a session method that refreshes account state through the retained gateway, confirms empty local and selected-market scope, checks captured freshness and current input health, and composes the existing filter, five-share NORMAL quote builder, and capital gate.

The method rejects stale, nonempty, invalid, overlapping, or unreliable states. It validates each token’s current tick and CLOB minimum without resizing the Frozen clip. Active account reads are tracked and drained on cancellation or session close, including repeated cancellation. Returned evidence reports outstanding execution gates and cannot authorize execution.

## Behavior Before

The runtime exposed retained market and account evidence but had no method to compose a fresh account read, current filter result, initial NORMAL pair, and capital admission into one decision.

## Behavior After

A healthy, empty session can produce immutable initial NORMAL admission evidence from the current retained inputs. The result preserves the existing $30 capital model and $5 NORMAL reserve, and `execution_authorized` is always false.

## Tests Added / Changed

Expanded `tests/test_rdp_runtime.py` with retained-session composition tests covering exact quotes and capital evidence, production strict/exact open-order parsing for fractional foreign BUY commitments, independent cash and allowance failures, existing account/local state, filter and PriceGate rejection, unequal ticks and minima, health/generation/expiry/backlog changes, overlap, repeated cancellation, session-close draining, and forbidden execution calls.

The success fixture uses fake external transports and the actual `ExecutionGateway.open_orders(strict_account=True, exact_decimal=True)` producer path.

## Tests Executed

- `uv run pytest -q tests/test_rdp_runtime.py` — initial run: 56 passed, 1 failed. `test_cli_observe_only_uses_real_retained_owners_and_ends_market[initial_admission]` rejected its fixture because cash was set to `$31` while allowance remained `$30`. The fixture allowance was corrected to `$31`; the rerun passed: **57 passed**.
- `uv run pytest -q tests/test_rdp_runtime.py -k 'initial_admission or close_drain or executor_cancel'` — **3 passed, 54 deselected**.
- `uv run pytest -q tests/test_rdp_account.py tests/test_rdp_accounting.py tests/test_rdp_capital.py tests/test_rdp_quotes.py tests/test_rdp_book_filter.py tests/test_rdp_marketdata.py tests/test_rdp_userstream.py tests/test_rdp_lifecycle.py tests/test_rdp_live.py tests/test_gateway_positions_v2.py` — **472 passed**.
- Full suite ran using the following inline socket/DNS guard:

```bash
uv run python -c 'import socket, pytest
attempts=[]
def blocked(name):
 def deny(*args, **kwargs):
  attempts.append(name)
  raise AssertionError("network blocked by RDP full-suite guard")
 return deny
socket.getaddrinfo=blocked("getaddrinfo")
socket.create_connection=blocked("create_connection")
socket.socket.connect=blocked("socket.connect")
socket.socket.connect_ex=blocked("socket.connect_ex")
code=pytest.main(["-q"])
print("NETWORK_GUARD_ATTEMPTS=" + str(len(attempts)))
if attempts:
 print("NETWORK_GUARD_OPERATIONS=" + repr(attempts))
 raise SystemExit(2)
raise SystemExit(code)'
```

Result: **1,238 passed, 2 skipped in 69.65s; `NETWORK_GUARD_ATTEMPTS=0`.**

An earlier unguarded full-suite attempt was interrupted after 301 passing tests when it reached `test_active_lag_deadline_returns_to_cancel_then_final_reconciliation`. It was not a completed test result; the guarded full-suite run above completed successfully.

## Test Results

The corrected targeted runtime suite, focused cancellation/admission checks, affected regressions, and guarded full suite passed.

## Regression Results

Existing RDP account, accounting, capital, quote, filter, market-data, user-stream, lifecycle, live-mode, and gateway-position tests passed. Full suite: **1,238 passed, 2 skipped**.

## Git Diff Summary

`git diff --stat`:

```text
src/polymaker/rdp_runtime.py | 355 +++++++++++++++++++++++++++++++++++-
tests/test_rdp_runtime.py    | 411 +++++++++++++++++++++++++++++++++++++++++--
2 files changed, 750 insertions(+), 16 deletions(-)
```

`git diff --check` passed. `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_runtime.py` passed. Working tree contains only these two modified files and the pre-existing untracked `research_outputs/` assets.

## Safety Checks

No live network operation was attempted in the guarded full suite. Controlled tests use fake transports; the full-suite guard recorded zero DNS or socket connection attempts. The implementation does not sign, place, cancel, reserve, or authorize orders. No Git history operations were performed.

## Known Limitations

This covers only the initial empty-run NORMAL decision. Eligibility can become stale after return and does not replace later submit-time revalidation.

## Deferred Findings

Subsequent-cycle reconciliation, nonempty inventory admission, REBALANCE, operational time sufficiency, live authorization, and complete runtime metrics remain outside this iteration’s scope.

## Reviewer Attention

Please verify empty-scope enforcement, retained-gateway identity, freshness and final synchronous health checks, token-specific minimum handling, read draining during repeated cancellation, and that admission evidence cannot grant execution authority.

## Human Live Trading Performed

NO
