# ITER-0027 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented only the approved partial prerequisite for DG-002: one post-cycle factual next-action admission on the retained RDP session, backed by its completed initial cycle, current confirmed processor inventory, current official account/status reads, current market data, and current model/physical capital checks.

DG-002 remains OPEN. The admission result is not an order intent or send authorization. This iteration does not implement successive NORMAL or REBALANCE submissions.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Verified base HEAD: `b5bd9a854f43578f3baa3ae370fba0551129d526`.
- The only pre-existing untracked tree was `research_outputs/` (748 files); it was preserved.
- No Git history operations were performed.

## Existing Components Reused

- Retained `RDPInputSession`, completed initial-cycle result, placement owner, and exact placement registry.
- `UserEventProcessor` confirmed fill evidence, quarantine/pending state, and owned records.
- Existing `project_rdp_capital`, close evidence, and `evaluate_rdp_capital_gate`.
- Existing initial account evidence validator, official account gateway capture, and official market-status read.
- Existing market-data service, current connection generations, books, dynamic token metadata, and Book Filter.
- Existing `build_normal_quotes` and `build_rebalance_quote` implementations.
- Existing asynchronous task shielding, drain, and session teardown behavior.

No new gateway, ledger, execution path, external runtime interface, or subsystem was added.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/risk/rdp_capital.py`
- `tests/test_rdp_capital.py`
- `tests/test_rdp_placement.py`

## Implementation Summary

Added an immutable `RDPNextActionAdmission` result and one explicit retained-session admission operation. The operation accepts no caller-supplied inventory, order scope, close proof, or requested mode. It derives the attempt and order scope from the completed initial cycle and current placement owner, reads current official market status and account evidence, then synchronously rechecks the close barrier, owner/registry, processor state, feed health and generations, backlog, current books, ticks, market status, and time before retaining a result.

The runtime reprojects the existing $30 model allocation from confirmed processor evidence. It derives NORMAL versus REBALANCE from current confirmed delta, uses the existing quote builders, and applies the existing CapitalGate. A new current-account backing evaluator reuses the existing strict account validator, checks selected positions against confirmed quantities, subtracts account-wide BUY commitments once from both collateral and allowance, and checks candidate notional plus the approved $5 NORMAL reserve (no added REBALANCE reserve). It does not adopt account positions or use account average price as acquisition-cost authority. The processor's float-derived quantity is matched to exact account quantities with a documented 0.000001 share tolerance.

The operation rejects stale, future, malformed, incomplete, contradictory, changed, unresolved, or quarantined evidence. It serializes against initial admission/submission and drains caller cancellation and teardown. The result always has `execution_authorized = false` and leaves operational time sufficiency, final submission revalidation, and human authorization outstanding. No order placement, signing, cancellation, pending capture, reconciliation, or owner release is performed by this operation.

A pre-existing waiter-failure test had a race: it released the fake SDK POST immediately after observing POST start, before its injected `asyncio.wait` failure was guaranteed to execute. The test now waits for an event emitted immediately before the injected failure, making the intended branch deterministic without changing production submission behavior.

## Behavior Before

After a successfully reconciled initial cycle, the session retained confirmed inventory and its close evidence, but had no integrated operation to admit a factual next candidate against that inventory and current account spendability. Reusing initial admission would have required an empty selected-market account and could have reset the model to the initial $30 backing condition.

## Behavior After

A completed retained cycle can produce a fresh factual NORMAL or REBALANCE candidate only when the same authoritative close, current owner/registry, confirmed processor evidence, account positions, current collateral/allowance, market status, books, ticks, filter, and capital gates remain coherent. Any read-time mutation or contradiction fails closed with a categorized rejection. The outcome is evidence only and cannot send an order.

## Tests Added / Changed

- Capital tests cover retained confirmed cost, low current cash, account-wide BUY commitment subtraction exactly once, cash/allowance boundaries, and NORMAL versus REBALANCE reserve behavior.
- Actual retained-session tests complete an acknowledged pair through the existing cycle owner/close barrier, then cover fractional NORMAL inventory, both `Delta = +5` and `Delta = -5` deficient-side candidates, and foreign account BUY commitments.
- Direct integrated rejection probes cover absent completion, STOP, partial close, unresolved owner, changed processor fill evidence, unrelated owned scope, registry contradiction, pending capture, FAILED/quarantined fill evidence, stale/future/malformed account receipt times, malformed and contradictory positions, malformed orders, closed-run order resurrection, stale/future/closed/wrong-market status, changed public and user generations, backlog, stale books, and incompatible dynamic ticks during account reads.
- Concurrency tests cover overlapping next-action calls, initial admission conflict, repeated caller cancellation with read draining, and session teardown during a blocked status read.
- Existing fake gateway/SDK infrastructure is used throughout. No real order or cancellation was sent.

## Tests Executed

All new runtime/capital tests used the archived pre-import network-blocking runner:

`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py ...`

The runner blocks DNS/socket connection methods before importing project code. Each completed command printed `REVIEW_NETWORK_ATTEMPTS []`.

- Narrow direct matrix: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_capital.py tests/test_rdp_placement.py -k 'current_backing or post_cycle_next_action'` — **35 passed, 445 deselected** after all code and test changes.
- Capital-focused: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_capital.py` — **75 passed**.
- Placement/runtime integration: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py tests/test_rdp_runtime.py` — **467 passed** (final run: 69.32s).
- Affected prerequisites: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_account.py tests/test_rdp_quotes.py tests/test_rdp_accounting.py tests/test_rdp_lifecycle.py tests/test_rdp_marketdata.py tests/test_rdp_userstream.py tests/test_rdp_book_filter.py tests/test_execution.py` — **433 passed**.
- FL compatibility: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_favorite_live.py` — **48 passed**.
- Initial full suite: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q` — **1 failed, 1680 passed, 2 skipped** in 84.32s. The failure was `tests/test_rdp_placement.py::test_submission_waiter_failure_still_drains_and_resolves_post_outcome`: expected `SUBMISSION_WAIT_FAILED:RuntimeError`, observed `None`. Investigation identified a race in that test's fake-client synchronization, not a runtime failure: the fake SDK operation could finish after POST-start but before the injected failing waiter resumed. The exact test passed alone before correction. The test was synchronized on the injected wait failure before releasing its fake POST.
- Exact synchronized waiter-failure test: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py::test_submission_waiter_failure_still_drains_and_resolves_post_outcome` — **1 passed**.
- Final full suite after final matrix additions: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q` — **1683 passed, 2 skipped** in 83.64s.
- Source compile: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m py_compile src/polymaker/rdp_runtime.py src/polymaker/risk/rdp_capital.py` — passed.
- `git diff --check` — passed.

## Test Results

The final targeted, affected, FL, and full-suite checks passed. The earlier full-suite failure and its test-only race correction are recorded above. No current test failure remains. All network-guard runs reported no attempted network connections.

## Regression Results

The final full suite passed with **1683 passed, 2 skipped**. Existing initial admission, initial placement, fill-window, lifecycle, accounting, shared data, and FL tests passed. The new factual admission neither submits nor cancels orders and leaves the DG-002 successive-order acceptance condition open.

## Git Diff Summary

Final source/test diff: **4 files changed, 1303 insertions(+), 12 deletions(-)**. Changed paths are exactly the two authorized runtime/capital source files and the two authorized capital/placement test files. `git diff --check` passed. `research_outputs/` remains preserved with 748 files. The Orchestrator separately persisted untracked `docs/development/ITER-0027/02_EXECUTION_INTERRUPTION.md` after final tests; it is documentation-only and outside the Worker source/test diff. Final untracked-file count is 749.

## Safety Checks

- Branch remained `rdp-phase0-dev`; HEAD remained `b5bd9a854f43578f3baa3ae370fba0551129d526`.
- No source or test changes outside the approved scope.
- No live Polymarket network access; pre-import network runner reported `REVIEW_NETWORK_ATTEMPTS []`.
- No real-money placement, signing, cancellation, or trading was performed.
- The operation does not arm pending capture, reconcile, release ownership, or grant execution authorization.
- No commit, push, merge, rebase, reset, or other Git history operation was performed.

## Known Limitations

- This iteration establishes only the factual post-cycle admission prerequisite. It does not implement successive order submission or consume cumulative run evidence before each future send; DG-002 remains OPEN.
- Operational time sufficiency, final submit revalidation, and human live authorization remain explicit outstanding gates.
- Dynamic token metadata incompatible with the retained current book tick is rejected; no metadata refresh path or operating loop is added here.
- Quantity matching permits up to 0.000001 share of processor float serialization difference, as documented in the evaluator.

## Deferred Findings

No unrelated findings were changed. Complete successive NORMAL/REBALANCE operation and its final-send evidence remain deferred to the registered open DG-002 acceptance condition and subsequent authorized iterations.

## Reviewer Attention

Please independently inspect:

- Whether the new current-backing evaluator preserves every strict account validation rule while allowing confirmed nonempty selected inventory.
- Whether close/owner/registry and processor checks remain sufficient across both official-read awaits and the final synchronous capture.
- Whether direct integrated tests adequately establish fractional NORMAL and both exact-five REBALANCE boundaries without implying a send loop.
- Whether the added test synchronization is minimal and properly isolates the pre-existing waiter-failure test race.
- Confirm DG-002 remains OPEN and `execution_authorized` is always false for both eligible and rejected factual results.

## Human Live Trading Performed

No.
