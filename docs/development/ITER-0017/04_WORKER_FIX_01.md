# ITER-0017 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repair MF-01 and MF-02 in `docs/development/ITER-0017/03_REVIEWER.md`, within the original ITER-0017 contract. The repair permanently excludes ambiguous duplicate-token acknowledgments and adds the required retained-session, scope/generation, and sanitized-exception tests.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `97e763da15775e941a63873dcc3d0bab66294f12`
- Preserved untracked `research_outputs/` existed before this repair and was left untouched.
- Orchestrator-owned index/audit changes were present in the working tree; this repair did not modify them.

## Existing Components Reused

- Existing typed acknowledgment validation and `PlacementUnknown`.
- Existing retained `RDPInputSession` admission and placement owner.
- Existing strict gateway placement path, invoked only with a fake SDK client in tests.
- Existing processor capture, order registry, replay, and account/filter/quote/capital admission paths.

## Files Changed

- `src/polymaker/execution/rdp_placement.py`
- `tests/test_rdp_placement.py`

`rdp_runtime.py` and `tests/test_rdp_runtime.py` were not changed during this repair.

## Implementation Summary

Acknowledgment validation now counts all rows claiming each submitted token before choosing safe orders. Any token claimed more than once is permanently excluded from ownership, including when three or more rows claim that token. Independently safe acknowledgments for the other token may still be retained. Oversized and ambiguous results remain unresolved and replacement-blocked.

Added tests exercise actual initial admission followed by arming and strict fake-SDK gateway resolution on the same retained processor; wrong market scope and public/user generation rejection before mutation; and a generic exception containing a credential sentinel, which must produce sanitized UNKNOWN evidence and block admission.

## Behavior Before

Three same-token acknowledgments could remove each other from the candidate mapping, then allow a later occurrence to be registered. The required actual-admission composition, pre-mutation scope/generation rejection, and generic credential-sentinel exception coverage were missing.

## Behavior After

Every repeated expected token is excluded from ownership for the entire result. A unique safe leg can still be registered while the attempt remains unresolved. Rejected scope/generation inputs leave capture, orders, and registry unchanged. Generic exception details do not enter attempt evidence or logs.

## Tests Added / Changed

- Three same-token acknowledgments, plus an independently safe opposite-token acknowledgment.
- Actual retained admission → capture arm → strict fake-SDK gateway result → same processor ownership.
- Wrong condition scope, public generation, and user generation rejection before mutation.
- Generic exception with credential sentinel; sanitized reason, no ownership, pending capture retained, admission blocked, and no sentinel in evidence/logs.

## Tests Executed

Focused repair cases:

```bash
uv run pytest -q tests/test_rdp_placement.py -k 'three_duplicate or real_initial_admission or wrong_scope_or_generation or generic_exception' --tb=short
```

Result: **6 passed, 21 deselected**.

Focused placement/runtime integration:

```bash
uv run pytest -q tests/test_rdp_placement.py tests/test_rdp_runtime.py --tb=short
```

Result: **87 passed in 61.85s**.

Scoped lint:

```bash
uv run ruff check src/polymaker/execution/rdp_placement.py src/polymaker/rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_runtime.py
```

Result: **All checks passed**.

Affected regression suite used `uv run python` to install guards on `socket.getaddrinfo`, `socket.create_connection`, `socket.socket.connect`, and `socket.socket.connect_ex`, then invoked `pytest.main(["-q", ...])` for:

```text
tests/test_rdp_placement.py
tests/test_rdp_runtime.py
tests/test_execution.py
tests/test_engine.py
tests/test_state.py
tests/test_rdp_account.py
tests/test_rdp_accounting.py
tests/test_rdp_capital.py
```

Result: **371 passed in 62.35s; NETWORK_GUARD_ATTEMPTS=0**.

Full suite used the same guard and invoked:

```python
pytest.main(["-q"])
```

Result: **1,268 passed, 2 skipped in 74.46s; NETWORK_GUARD_ATTEMPTS=0**.

## Test Results

The first focused run of the new real-admission test failed because it submitted the fixture’s `.39` quotes while the real admission produced different quote prices. The test now passes the actual admitted quotes to the fake gateway. The focused repair selection and subsequent placement/runtime, affected, and full-suite runs all passed.

An initial Ruff run found unsorted imports in the new test file. Ruff fixed the import order; the final scoped lint check passed.

## Regression Results

The affected gateway/execution, engine, state, RDP account, accounting, capital, runtime, and placement tests passed. Full suite passed with two skips. Both final guarded runs recorded zero network attempts.

## Git Diff Summary

- Repair changed only `rdp_placement.py` and `test_rdp_placement.py`.
- Final SHA-256:

```text
271bd22535b577b9254dc44f688ea843ea6ae72cea460579db6e1a5a6f69de2e  src/polymaker/execution/rdp_placement.py
be6963d54e1a6ac5ecd8654c03d3eb52aa0832a9a1f396eb32f33b778230580d  src/polymaker/rdp_runtime.py
c2a805f056b29b7919eb61dbf866328be03d40dceb9f016d8dfc19894075e9cf  tests/test_rdp_placement.py
980c2d81ba288c6408d317962bf7ca7eacf60d79002d6d5200457a7689f4ebdc  tests/test_rdp_runtime.py
```

`git diff --check` passed. New placement source/test files remain untracked for Orchestrator archival. Preserved research outputs and Orchestrator-owned audit/index files were not modified by this repair. No Git history operations were performed.

## Safety Checks

- DNS/socket guard recorded zero attempts in affected and full-suite runs.
- Strict gateway tests used a fake SDK client.
- No production placement, cancellation, signing, or retry call was added.
- No real-money order was sent.

## Known Limitations

This repair does not add submission authorization or any live execution behavior. Reconciliation and recovery of unknown outcomes remain deferred.

## Deferred Findings

Original contract deferrals remain: final market/time/account execution gates, authoritative reconciliation, cancellation, unknown-outcome recovery, subsequent NORMAL/REBALANCE orders, and full metrics.

## Reviewer Attention

Verify that three repeated same-token acknowledgments cannot register any of those IDs, while the opposite unique safe leg can be retained; confirm scope/generation rejection occurs before mutation and the generic exception sentinel is absent from evidence and logs.

## Human Live Trading Performed

NO
