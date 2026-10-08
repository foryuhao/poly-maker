# ITER-0016 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repair MF-01 and MF-02 from `docs/development/ITER-0016/03_REVIEWER.md` within the approved initial NORMAL admission scope. Add stable selected-market identity to all admission results and test partial foreign BUY commitment subtraction plus sanitized account-read failure and cleanup.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `e5e3ef46238a8df8ea235aeaf300db7a50d99ad0`
- Preserved `research_outputs/` remains untouched.
- The working tree also contains Orchestrator-owned index and audit-artifact changes; this Worker changed only the two approved source/test files.

## Existing Components Reused

The retained session, bootstrap identity, existing strict/exact `ExecutionGateway.open_orders` producer, account capture and backing evaluator, and existing cleanup path are reused. No account client, ledger, or execution path was added.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_runtime.py`

## Implementation Summary

Added immutable `slug` and `condition_id` fields to `RDPInitialNormalAdmission`. Both successful results and every early or later rejection populate them from the session’s bootstrap. Scope identity distinguishes otherwise equivalent rejection results for different markets. The result remains frozen, and `execution_authorized` remains fixed false.

Extended the retained-session tests with:

- A partially filled foreign BUY parsed through the real strict/exact gateway producer: original size `1.1`, matched size `0.2`, price `0.5`. The test verifies exact remaining size `0.9`, commitment `0.45` counted once, and cash and allowance backing each at the `$30.00` boundary.
- An SDK-shaped account read failure containing a credential sentinel, injected on the admission refresh after successful startup. The result is categorized as `ACCOUNT_READ_FAILED`; admission/read ownership drains, sentinel text is absent from evidence and logs, and the observer closes normally.
- Distinct immutable market identity for early `SESSION_CLOSED` results, plus identity and no-authority assertions for local-scope and overlap rejections.

## Behavior Before

Admission evidence omitted market slug and condition ID, so early rejection results could not identify their selected market. Tests did not cover partial cumulative-quantity subtraction through the admission account pipeline or an admission-specific read failure.

## Behavior After

Every admission result identifies its bootstrap market. The exact producer-to-backing path proves that a partially filled foreign order contributes only its remaining commitment. Admission read failures return sanitized categorized evidence and leave no active read or admission ownership.

## Tests Added / Changed

Updated `tests/test_rdp_runtime.py` with the partial-foreign-order and SDK-read-failure scenarios, and added `test_closed_admission_rejections_keep_distinct_immutable_market_scope`. Existing high-precision foreign BUY coverage remains unchanged.

## Tests Executed

- `uv run pytest -q tests/test_rdp_runtime.py -k 'closed_admission_rejections or admission_partial_foreign_buy or admission_read_failure' --tb=short` — **3 passed, 57 deselected**.
- `uv run pytest -q tests/test_rdp_runtime.py` — **60 passed**.
- Affected runtime/account/filter/capital/quote/lifecycle/gateway regression suite, run through the socket/DNS guard with `pytest.main(["-q", *paths])` — **532 passed in 65.45s; `NETWORK_GUARD_ATTEMPTS=0`**. The path list was `tests/test_rdp_runtime.py`, `tests/test_rdp_account.py`, `tests/test_rdp_accounting.py`, `tests/test_rdp_capital.py`, `tests/test_rdp_quotes.py`, `tests/test_rdp_book_filter.py`, `tests/test_rdp_marketdata.py`, `tests/test_rdp_userstream.py`, `tests/test_rdp_lifecycle.py`, `tests/test_rdp_live.py`, and `tests/test_gateway_positions_v2.py`.
- Full suite, run through the same socket/DNS guard using `pytest.main(["-q"])` — **1,241 passed, 2 skipped in 74.19s; `NETWORK_GUARD_ATTEMPTS=0`**.
- `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_runtime.py` — passed.
- `git diff --check` — passed.

The guard replaced `socket.getaddrinfo`, `socket.create_connection`, `socket.socket.connect`, and `socket.socket.connect_ex` with functions that record and reject attempts.

The first focused run had two fixture failures: the partial-fill assertion used nonexistent `cash_backing` instead of `observed_cash_backing`; the read failure was injected on startup rather than the admission refresh, so startup correctly failed before admission. Both fixture issues were corrected; the focused rerun and full checks above passed.

## Test Results

All required focused, affected, and full-suite checks passed. No network attempts were recorded in the guarded affected or full-suite runs.

## Regression Results

The full suite passed with **1,241 passed, 2 skipped**. Existing FL and other predecessor tests were included; no shared-core or FL changes were made.

## Git Diff Summary

For the two Worker-owned files:

```text
src/polymaker/rdp_runtime.py | 384 +++++++++++++++++++++++++++++++-
tests/test_rdp_runtime.py    | 511 ++++++++++++++++++++++++++++++++++++++++++-
2 files changed, 879 insertions(+), 16 deletions(-)
```

`git status` also showed Orchestrator-owned changes to `docs/development/ITERATION_INDEX.md`, untracked ITER-0016 audit artifacts, and preserved untracked `research_outputs/`. These were not modified by this Worker. No Git history operation was performed.

## Safety Checks

No real network operation or real-money action occurred. Tests use fake external transports; the guarded affected and full-suite runs recorded zero DNS/socket connection attempts. The admission result cannot authorize execution; order placement and cancellation remain unavailable in this path.

## Known Limitations

Admission still applies only to the initial empty-run NORMAL decision. Evidence may become stale after return and does not replace submit-time revalidation.

## Deferred Findings

Subsequent-cycle reconciliation, nonempty inventory admission, REBALANCE, submit-time checks, live authorization, and complete runtime metrics remain outside this iteration.

## Reviewer Attention

Please verify stable scope identity on every success and rejection path, distinct closed-market results, exact partial-fill remaining quantity and single-count backing, sanitized credential-sentinel failure evidence, and read/session cleanup.

## Human Live Trading Performed

NO
