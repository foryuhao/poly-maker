# ITER-0028 — Worker Implementation

## Status

IMPLEMENTED

Fix 01 addresses Reviewer03 B1, M1, and M2 within the approved scope. Independent re-review is pending. DG-003 remains OPEN; this report does not claim PASS or gap closure.

## Architect Contract

Implemented the frozen ITER-0028 DG-003 continuation contract and the authorized Fix 01 findings in `docs/development/ITER-0028/01_ARCHITECT.md` and immutable `03_REVIEWER.md`. The repair preserves the existing one-call NORMAL continuation and owner lifecycle. It adds no strategy semantics or runtime interface.

## Base Repository State

Base branch: `rdp-phase0-dev`; base HEAD: `ec6c3af559095fa911a14a7008d9a8ebeab70487`. The Orchestrator had confirmed a clean tracked/staged base before implementation. The pre-existing `research_outputs/` directory remains untouched.

At final checks the shared worktree also contained Orchestrator-owned edits to `docs/development/DELIVERY_GAPS.md` and `ITERATION_INDEX.md`, plus untracked archived Worker/Reviewer evidence. I did not edit or archive those files. No Git history operation was performed.

## Existing Components Reused

The repair uses the existing `RDPPlacementOwner`, immutable placement-attempt history, close proofs, `UserEventProcessor` pending-capture state, terminal inventory capture, current account/market/tick/book guards, and existing close/reconciliation/cancellation path. Clean empty attempts remain in the owner's attempt history, while the prior fully closed attempt remains the current cumulative anchor for fresh admission.

## Files Changed

The current authorized ITER-0028 implementation diff from the base touches:

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/rdp_placement.py`
- `src/polymaker/risk/rdp_capital.py`
- `tests/test_rdp_placement.py`
- `tests/test_rdp_capital.py`

Fix 01 itself changed runtime/placement ownership handling and placement tests. The risk/capital implementation and capital tests were part of the already-existing approved Worker02 change and were not altered in this repair. No other source or test paths were changed.

## Implementation Summary

- Operation clock exceptions, booleans, non-finite values, non-numeric values, and values before the BTC5m bootstrap epoch now latch terminal STOP before another admission can proceed. The first sanitized cause remains authoritative.
- After any submission, a latched STOP is returned immediately. An interrupted but completely acknowledged SDK result remains COMPLETE in its immutable submission record; the continuation result retains the primary deadline/cancellation/teardown cause instead of replacing it with a secondary cycle exception.
- Continuation completion now snapshots current confirmed inventory from the currently registered owner scope. It exposes `inventory_order_ids`, `inventory_scope`, `capital_certified`, and `unresolved_exposure`. A still-valid certified current projection supplies exact Decimal cost; when current inventory has moved beyond the retained projection, the result carries current known inventory/cost and explicitly reports capital as uncertified.
- A clean continuation `EMPTY` attempt with no acknowledgements, observations, pending anomalies, or unresolved capture remains historical evidence but restores its proven closed source attempt as the current admission anchor. The next attempt must use fresh admission and final guards.
- Only clean pre-POST quote, price, tick, and book-filter invalidations may wait/re-admit. Cash/allowance/capital, stale market data, unresolved capture, partial/unknown placement, and ambiguous post outcomes remain fail-closed. No stale signed intent is retried.

## Behavior Before

A clock failure returned TERMINAL without setting the STOP latch, so a later call could reach new POSTs. Terminal continuation results reused an old close projection and omitted later confirmed fills. A complete acknowledgement interrupted by an operation deadline could be followed by a secondary cycle exception and lose the primary cause. A reliable quote/tick/filter invalidation with no order exposure was treated as unconditional STOP, without retaining a safe re-admission path.

## Behavior After

Terminal clock failures latch before return and cannot re-enter. Terminal inventory reflects all currently registered, non-quarantined confirmed fills in its explicit scope; capital remains uncertified whenever the retained capital projection no longer matches that inventory. Complete-ack interruption preserves the original immutable COMPLETE result and terminal cause while registered-only cleanup drains. A clean no-send attempt preserves history and may wait for fresh, scoped admission; captured anomalies and risky or ambiguous outcomes still STOP.

## Tests Added / Changed

Added 13 durable continuation-path tests in `tests/test_rdp_placement.py`, covering:

- clock exception, boolean, NaN, and string clock values, plus re-entry denial;
- partial and unknown subsequent acknowledgements with current registered inventory;
- late confirmed fill on an earlier closed order, retaining current inventory without stale capital certification;
- held COMPLETE acknowledgement across an operation deadline, preserving QY/QN and primary cause;
- clean quote invalidation followed by fresh admission and a fresh guarded pair;
- an empty wrapper with pending unmatched candidate-trade evidence, which remains terminal;
- held close with late fill, repeated cancellation, and teardown; all verify owner exclusion, captured inventory, and cleanup containment.

The existing signing-tick regression now refreshes its book timestamp when injecting a tick update, so the guard exercises tick evidence rather than accidentally exercising stale-book evidence. Its assertion records the clean no-send; later re-admission waits for the book filter, and the test verifies that market-data staleness, not the initial clean invalidation, causes STOP with no additional continuation POST.

## Tests Executed

All commands used the repository's pre-import DNS/socket-blocking runner. Each final test log reports `REVIEW_NETWORK_ATTEMPTS []`.

- Targeted durable regressions:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py -k 'iter0028_'`
- Affected placement and capital suites:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py tests/test_rdp_capital.py`
- Full suite:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no`
- Final static checks:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python -m compileall -q src/polymaker/rdp_runtime.py src/polymaker/execution/rdp_placement.py`
  and `git diff --check`.
- Reviewer evidence commands, each using the same runner prefix above:
  - `docs/development/ITER-0028/review_evidence/iter0028_reviewer_probes_v3.py`
  - `docs/development/ITER-0028/review_evidence/iter0028_reviewer_lifecycle_v3.py -k 'held_subsequent_close or later_zero_fill or reliable_no_send or subsequent_complete'`
  - `docs/development/ITER-0028/review_evidence/iter0028_reviewer_window_v2.py -k 'later_window or nonboolean or subsequent_guard_current'`
  - `docs/development/ITER-0028/review_evidence/iter0028_reviewer_terminal.py -k 'clock_failure_terminal or higher_current_cash'`
  - `docs/development/ITER-0028/review_evidence/iter0028_reviewer_conflicts.py`

## Test Results

- Durable Fix 01 regressions: **13 passed, 408 deselected**.
- Affected placement/capital suites: **497 passed**.
- Full suite: **1700 passed, 2 skipped in 98.55s**.
- Final compileall and `git diff --check`: passed.
- Final reviewer probes: `probes_v3` **3 passed**; `terminal.py -k 'clock_failure_terminal or higher_current_cash'` **2 passed**; `window_v2.py -k 'later_window or nonboolean or subsequent_guard_current'` **13 passed**; `conflicts.py` **1 passed**.
- The archived lifecycle probe remains **1 failed, 5 passed, 3 deselected** solely at its old assertion `c.posts==0` in `test_reliable_no_send_quote_movement_is_not_stop`. The implementation now deliberately re-admits the clean no-send intent and can submit only the newly admitted, freshly guarded pair. The direct durable replacement asserts this behavior and passes. The original probe and Reviewer report were not modified.
- Final full-suite log: `/tmp/iter0028_fix01_full_suite_final2.log`.

## Regression Results

The full suite includes FL and shared-runtime regressions and passes. The separately rerun affected placement/capital suites pass. Fake-client tests block/count DNS and socket access before imports and during execution; final logs report zero attempts.

Exploratory failure evidence was preserved without rewriting earlier logs:

- `/tmp/iter0028_fix01_pre_terminal.log`: original B1 clock-failure re-entry test failed because subsequent call produced 2 POSTs.
- `/tmp/iter0028_fix01_pre_probes_v3.log`: original partial-fill result reported QY 1.25 rather than 2.25; late-prior-fill result reported QY 1.25 rather than 1.5; the unknown placement case passed.
- `/tmp/iter0028_fix01_pre_lifecycle_v3.log`: original clean quote invalidation latched STOP; interrupted COMPLETE acknowledgement lost QN (0 instead of 1) and produced a secondary continuation exception.
- `/tmp/iter0028_fix01_pre_window_v2.log`: 13 passed; `/tmp/iter0028_fix01_pre_conflicts.log`: 1 passed.
- The exact pre-repair reviewer commands were the corresponding `iter0028_reviewer_*.py` paths above, run with `-q --tb=short --show-capture=no`; the v3 placement probe used `-q -s --tb=short`. Their preserved outputs are the five `/tmp/iter0028_fix01_pre_*.log` files named here.
- `/tmp/iter0028_fix01_reviewer_lifecycle_final3.log`: preserved legacy M2 expectation failure (`c.posts==0`, observed 2 after fresh re-admission); 5 other selected lifecycle checks passed.
- `/tmp/iter0028_fix01_reviewer_window_final.log`: the first broad retry implementation also retried low cash and allowance; the cash and allowance probes then observed posts and failed. The retry classifier was narrowed to quote/price/tick/filter only; final `/tmp/iter0028_fix01_reviewer_window_final2.log` passes all 13 selected checks.
- `/tmp/iter0028_fix01_narrow_durable_final.log`: first malformed-clock validation used nonexistent `RDPBootstrap.start_epoch`, yielding AttributeError and 9 failures. It was corrected to the existing `epoch`; final `/tmp/iter0028_fix01_narrow_durable_final2.log` passes all 13 tests.
- `/tmp/iter0028_fix01_affected_final.log`: the previous full affected run passed 494 tests before the additional malformed-clock cases; final rerun is `/tmp/iter0028_fix01_affected_final2.log` (497 passed).
- The first compile command, `python -m compileall -q src/polymaker/rdp_runtime.py src/polymaker/execution/rdp_placement.py`, used unavailable system `python` and failed with `/bin/bash: python: command not found`; the final `.venv/bin/python -m compileall` command passed.

Other intermediate, corrected test runs remain preserved under `/tmp/iter0028_fix01_*.log`, including their exact failure output. None of those failures were suppressed or edited.

## Git Diff Summary

The authorized source/test diff is five files, 1685 insertions and 54 deletions across the complete ITER-0028 implementation against the base HEAD. The shared worktree also has the two Orchestrator-owned delivery register/index edits and untracked archived audit evidence. `research_outputs/` remains pre-existing and untouched. `git diff --check` is clean. No commit, push, merge, reset, or other history operation was performed.

## Safety Checks

No live network, Polymarket API, real-money order, real cancellation, or real account access occurred. All SDK interactions were deterministic fake-client tests. Every final fake-client test used the pre-import network-blocking runner and recorded zero DNS/socket attempts. Existing execution gateway, owner registry, close barriers, CapitalGate, and safety guards remain in use.

## Known Limitations

A clean no-send retry is allowed only for quote/price/tick/book-filter categories with exact empty-owner evidence and an intact closed source proof. Fresh market/account evidence may still fail or age stale while waiting; that path remains terminal and does not submit the old intent. When the current confirmed-fill scope changes without a matching certified projection, current inventory/cost are exposed with capital explicitly uncertified.

The preserved Reviewer lifecycle probe's `c.posts==0` assertion conflicts with its required fresh re-admission behavior. Its immutable source was left unchanged; the repository-owned regression covers the updated acceptance condition.

## Deferred Findings

No unrelated finding was changed. DG-003 remains OPEN pending independent Reviewer assessment of the full continuation path.

## Reviewer Attention

Please verify:

- Clock-invalid STOP latch and one-time result behavior, including malformed clock values.
- Current terminal inventory scope/IDs and primary cause across partial, unknown, late-fill, deadline, cancellation, and teardown cases; confirm capital is explicitly uncertified when the latest projection is stale or incomplete.
- Clean EMPTY continuation history and source-anchor restoration; only clean quote/price/tick/filter categories re-admit, and every later submission uses newly issued admission and final guards.
- Pending unmatched capture evidence, account cash/allowance failures, stale book evidence, and ambiguous SDK/post outcomes remain fail-closed with no stale-intent retry.
- Cancellation/teardown waits for held cleanup and does not release replacement ownership prematurely.

## Human Live Trading Performed

No.
