# ITER-0028 — Worker Implementation

## Status

IMPLEMENTED

DG-003’s retained NORMAL continuation is implemented on the existing selected-market runtime and gateway path. The implementation is ready for independent review; this report does not assign Reviewer PASS.

## Architect Contract

Implemented the approved `docs/development/ITER-0028/01_ARCHITECT.md` contract for **DG-003 — Repeated NORMAL Operation in One Market**. A single explicitly authorized continuation call now obtains fresh factual admission and automatically runs subsequent NORMAL cycles through the existing guarded SDK, FillWindow, close, and reconciliation path. It derives mode and capital from cumulative acknowledged run scope and retained cycle proofs, and returns a closed REBALANCE handoff at either exact cumulative ±5 boundary without a REBALANCE order.

DG-002 and DG-004 remain OPEN. No strategy semantics, external runtime interfaces, Frozen SOP, or excluded subsystem was changed.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Verified base and final implementation HEAD: `ec6c3af559095fa911a14a7008d9a8ebeab70487` (Worker performed no Git history operation).
- The only pre-existing untracked content was `research_outputs/`; it remains untouched and untracked.
- The approved ITER-0028 Architect Contract was inspected along with AGENTS.md, Frozen SOP, baseline/index, prior ITER-0027 evidence, relevant runtime/gateway/lifecycle/market-data code, and tests.

## Existing Components Reused

- `RDPInputSession`, retained public/authenticated input owners, current account/status reads, Book Filter, NORMAL quote builder, and CapitalGate.
- `ExecutionGateway.place()` and its strict pair acknowledgment, post-only, per-token signing-tick, SDK guard, executor drain, and typed outcome behavior.
- `RDPPlacementOwner`, `UserEventProcessor`, `StateStore`, pre-ack event capture, and authoritative registered-order scope.
- Existing initial pre-fill, per-attempt FillWindow, `close_rdp_cycle()`, cancellation, and reconciliation machinery.
- Existing account backing and capital projection; the projector now accepts multiple separately validated close proofs without manufacturing an aggregate close result.
- Existing official selected-market market-data service and retained tick provenance.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/rdp_placement.py`
- `src/polymaker/risk/rdp_capital.py`
- `tests/test_rdp_placement.py`
- `tests/test_rdp_capital.py`

No gateway, lifecycle, tracker/store, quote-builder, CLI, configuration, dependency, Frozen SOP, or iteration-document file was changed.

## Implementation Summary

The runtime now owns one default-denied NORMAL-continuation operation and preserves its admissions, submissions, and per-attempt cycle results separately from the once-only initial result. It refreshes post-cycle market status and account evidence, verifies the registered cumulative owner scope and every retained close proof, projects cumulative confirmed inventory/cost and current backing, and builds the next NORMAL quote from current books and feed-backed per-token ticks.

The existing placement owner can arm an inventory-skewed subsequent pair only after prior attributable closure. Subsequent acknowledgments are registered under continuation provenance. Each cycle retains its own close proof; a new risk cycle is not admitted until the current pair’s own cancellation/reconciliation barrier and cumulative projection both certify. The next-action admission is invalidated as soon as a new refresh starts, including when that refresh fails.

The existing submission path now serves both initial and subsequent attempts while preserving initial once-only result caching. Both SDK guard points validate the same issued attempt and tick intent. A change after signing denies the POST. STOP, cancellation, teardown, deadline, or unreliable exposure cannot start another cycle. The continuation retains cumulative inventory in handoff/terminal evidence and never posts a REBALANCE order.

## Behavior Before

After one completed initial NORMAL cycle, the runtime could produce a factual next-action candidate but could not automatically submit and complete later NORMAL cycles. Reusing initial submission would require its empty-scope, fixed-size pair, once-only submission, and one-result lifecycle assumptions. Current WS tick changes that differed from bootstrap metadata were rejected for subsequent admission, and capital projection accepted only one cycle-close proof.

## Behavior After

A single explicitly authorized continuation call automatically performs successive actual fake-SDK NORMAL cycles while each fresh admission and both send guards remain valid. Confirmed inventory and actual acquisition cost accumulate across cycles; quote skew and current account backing are recalculated each time. A validated current WS tick can supersede the bootstrap tick for the candidate and signing request. The loop stops after completing the pair that brings cumulative Delta to exactly `+5` or `-5`, and returns REBALANCE handoff with no later POST.

The default/non-boolean authorization path remains denied without signing, POST, or cancel. The original initial submission/cycle results remain cached and unchanged after continuation.

## Tests Added / Changed

- `tests/test_rdp_placement.py`: an integrated strict fake-SDK continuation probe starts from a completed initial cycle and makes one continuation call. It covers both exact `+5` and `-5`, fractional inventory skew, third-cycle cumulative quantities/cost, per-cycle close proofs, strict gateway signing, per-token dynamic tick use, cancellation/trade reconciliation before every replacement, and absence of a REBALANCE POST.
- The same integrated probe verifies default-denied continuation produces no additional POST/cancel and preserves the original initial result objects.
- A second-guard probe mutates the current tick after signing; continuation returns `NO_SEND`, records `TOKEN_TICK_EVIDENCE_CHANGED`, and does not POST.
- `tests/test_rdp_capital.py`: multiple non-overlapping close proofs support cumulative cost and a live pair; overlapping or insufficient proof collections fail closed.

## Tests Executed

All commands used the archived pre-import DNS/socket-blocking runner. Each command ended with `REVIEW_NETWORK_ATTEMPTS []`.

1. Focused new continuation/proof tests:

   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_capital.py tests/test_rdp_placement.py -k 'one_normal_continuation_call or multiple_nonoverlapping_close_proofs_support_cumulative_cost_and_live_pair'`

   Result at that point: 3 passed. The later stale-second-guard parameter was separately run with `-k 'one_normal_continuation_call and True'` and passed; all final parameterizations are also included in the final full-suite run.

2. Capital tests:

   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_capital.py`

   Result: 76 passed.

3. Runtime/placement integration:

   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py tests/test_rdp_runtime.py`

   Result: 470 passed.

4. Affected gateway/lifecycle/account/feed/userstream/quote/accounting/filter regressions:

   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_execution.py tests/test_rdp_lifecycle.py tests/test_rdp_account.py tests/test_rdp_marketdata.py tests/test_rdp_userstream.py tests/test_rdp_quotes.py tests/test_rdp_accounting.py tests/test_rdp_book_filter.py`

   Result: 433 passed.

5. FL compatibility:

   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_favorite_live.py`

   Result: 48 passed.

6. Final full suite:

   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q`

   Result: 1687 passed, 2 skipped in 89.38 seconds.

7. Final tree checks:

   `git status --short --branch`, `git rev-parse HEAD`, `git ls-files --others --exclude-standard`, `git diff --stat`, and `git diff --check`.

   `git diff --check` passed with no output. The only untracked tree remains the pre-existing `research_outputs/` directory.

## Test Results

The final required commands all passed on the final tree. Every saved runner log ends with `REVIEW_NETWORK_ATTEMPTS []`.

- Capital: 76 passed.
- Runtime/placement: 470 passed.
- Affected gateway/lifecycle/account/feed/userstream/quote/accounting/filter: 433 passed.
- FL compatibility: 48 passed.
- Full suite: 1687 passed, 2 skipped in 89.38 seconds.
- Final `git diff --check`: passed with no output.

### Exploratory failure manifest

All exploratory output that was redirected to disk is retained at the exact `/tmp` paths below. Each path includes the terminal pytest summary and network-guard line. The listed failures were test-fixture/assertion or implementation issues found during development; the final required suites above reran against the repaired tree and passed.

| Log | Command | Exact result / failure |
|---|---|---|
| `/tmp/iter0028-narrow.log` | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py -k one_normal_continuation_call` | 1 failed, 405 deselected, 12.16s. `test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively`: expected `REBALANCE_HANDOFF`, got `TERMINAL`; tick-change event reset Book Filter warmup, then the continuation loop allowed feed evidence to go stale. Large captured output is retained verbatim in this 25 MB log. |
| `/tmp/iter0028-narrow-debug.log` | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py -k one_normal_continuation_call` | 1 failed, 405 deselected, 12.16s. Assertion showed `BOOK_FILTER_UNREADY`; the no-token side became inactive during the wait. |
| `/tmp/iter0028-narrow-debug2.log` | Same command as `narrow-debug.log` | 1 failed, 405 deselected, 12.20s. Assertion showed `BOOK_FILTER_UNREADY` / `NOT_READY:INVALID_TRADE_EVIDENCE`: direct `_process_raw` fixture calls lacked the consumer’s `_processing_receipt_monotonic` receipt. |
| `/tmp/iter0028-narrow-final.log` | Same command as `narrow-debug.log` | 1 failed, 405 deselected, 12.20s. Same `INVALID_TRADE_EVIDENCE` receipt setup defect remained. |
| `/tmp/iter0028-narrow-final2.log` | Same command as `narrow-debug.log` | 1 failed, 405 deselected, 3.78s. Source assertion expected `WS_TICK_SIZE_CHANGE`, but the subsequent valid tick-bearing book snapshot correctly made current source `WS_BOOK_SNAPSHOT`; assertion was corrected while the preceding tick-change event is separately checked. |
| `/tmp/iter0028-narrow-pass.log` | Same command as `narrow-debug.log` | 1 passed, 405 deselected, 3.64s. |
| `/tmp/iter0028-focus.log` | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_capital.py tests/test_rdp_placement.py -k 'one_normal_continuation_call or multiple_nonoverlapping_close_proofs_support_cumulative_cost_and_live_pair'` | 1 failed, 2 passed, 480 deselected. Positive-side expected second candidate size `5.25`, actual Frozen skew size `7.25`; test expectation corrected. |
| `/tmp/iter0028-focus-pass.log` | Same command as `focus.log` | 1 failed, 2 passed, 480 deselected. `TypeError: unsupported operand type(s) for -: 'float' and 'decimal.Decimal'` from comparing Decimal cost with `pytest.approx(float)`; comparison corrected to Decimal. |
| `/tmp/iter0028-focus-pass2.log` | Same command as `focus.log` | 3 passed, 480 deselected. |
| `/tmp/iter0028-focus-guard.log` | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py -k one_normal_continuation_call` | 3 failed, 405 deselected. `AttributeError: 'RepeatedNormalSDKClient' object has no attribute 'on_create'`; fake-client field initialization was corrected. |
| `/tmp/iter0028-focus-guard2.log` | Same command as `focus-guard.log` | 1 failed, 407 deselected. Stale-tick probe expected `TOKEN_TICK_EVIDENCE_CHANGED`, observed `BOOK_FILTER_UNREADY` because filter readiness was checked first; final guard validation now checks the captured tick before filter readiness. |
| `/tmp/iter0028-tick-guard.log` | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py -k 'one_normal_continuation_call and True'` | 1 failed, 407 deselected. Probe expected `FAILURE_PRE_POST`, actual gateway contract outcome was `NO_SEND`; test expectation corrected and verified. |
| `/tmp/iter0028-tick-guard-pass.log` | Same command as `tick-guard.log` | 1 passed, 407 deselected. |

An earlier combined capital/placement/runtime invocation was run before the runtime fixes:

`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_capital.py tests/test_rdp_placement.py tests/test_rdp_runtime.py`

Its terminal summary, observed in the tool output, was `512 passed, 31 failed in 79.94s`. That output was not redirected to a durable file at the time and is not recoverable from the current workspace; therefore the exact 31 node IDs and individual traceback text cannot be supplied. The retained development note identifies the causes as a missing `ReconciliationOutcome` import, an overly strict feed capture readiness check, and initial cached-completion/cumulative-continuation assumptions. I am recording this evidence gap rather than reconstructing failure names. The final runtime/placement rerun below passed.

Final test logs are retained at `/tmp/iter0028-capital.log`, `/tmp/iter0028-runtime-placement.log`, `/tmp/iter0028-affected.log`, `/tmp/iter0028-fl.log`, and `/tmp/iter0028-full.log`. All ended with zero network attempts. The complete exploratory log list is the table above.

## Regression Results

Initial submission, DG-001/DG-006, gateway, lifecycle, cancellation, fill-window, account, market-data, quote, accounting, Book Filter, and FL regression suites passed. Initial public submission/cycle results remain once-only. The continuation integration confirms replacements are ordered after cancellation and trade reconciliation, and the final tree preserves default-denied behavior.

## Git Diff Summary

- Five authorized source/test files changed.
- `git diff --stat`: 1150 insertions, 53 deletions.
- `git diff --check`: passed.
- Branch and HEAD were not changed; no commit, push, merge, rebase, reset, or tag operation was performed.
- Existing `research_outputs/` remains present and unmodified.

## Safety Checks

- Fake-client tests used the pre-import DNS/socket blocker; `REVIEW_NETWORK_ATTEMPTS []`.
- No live HTTP/WebSocket connection, real order, real cancellation, credential access, or main merge was performed.
- No SDK work was intentionally detached; the existing gateway/owner drain path remains in use.
- Current STOP/default authorization gates were preserved. No continuation action is authorized by this code for real-money trading.
- Tick acceptance used a selected-market `tick_size_change` through the existing service, then a current book snapshot and current trades to satisfy the existing warmup. The later SDK signing request used the resulting `0.005` tick. A separate second-guard test changes the retained tick after signing and verifies no POST.

## Known Limitations

- No real-market or live validation was performed. DG-002 REBALANCE order operation and DG-004 recovery remain outside this contract and OPEN.
- The integrated continuation path directly covers successful repeated operation, exact ±5 handoff, current tick signing, stale post-sign tick denial, default denial, and per-cycle ordering. Subsequent-attempt partial/unknown/interrupted POST cleanup, cancellation while a later FillWindow/reconciliation is held, and later-cycle zero-fill/invalidation are not each separately injected through this new repeated-cycle harness. Their shared gateway/owner/lifecycle behavior is covered by existing initial-path tests listed below, but independent review should probe the subsequent-attempt mapping explicitly.
- The exact tick-change regression warms the existing two-second Book Filter after the official-format feed event; it does not claim an immediate quote before that required warmup.

## Deferred Findings

- DG-002 requires cumulative current-capital checks for successive REBALANCE placements.
- DG-004 requires deficient-side operation/recovery.
- Other gaps remain as enumerated in the frozen contract and DELIVERY_GAPS.md.

## Reviewer Attention

Please independently inspect actual diff and run the acceptance matrix. Direct continuation-path evidence is in `test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively` and `test_multiple_nonoverlapping_close_proofs_support_cumulative_cost_and_live_pair`.

The following related tests exercise reused components, but use the initial public operation/cycle rather than a subsequent continuation attempt: partial and unknown outcome cleanup (`test_partial_submission_latches_stop_and_closes_only_registered_scope`, `test_unknown_zero_id_post_failure_has_no_speculative_cancel`, `test_complete_ack_interruption_latches_and_closes_registered_pair`); executor cancellation/drain (`test_submission_interruption_drains_real_gateway_post_before_return`, `test_submission_interruption_drains_real_gateway_signing_without_post`); held cancel/fill reconciliation (`test_coordinator_reconciles_confirmed_fill_arriving_during_cancel`); zero-fill/resting and original confirmation deadlines (`test_initial_cycle_rests_without_zero_fill_timeout_then_closes_and_caches`, `test_initial_cycle_confirmed_before_activation_uses_original_window_and_delta_five`, `test_initial_cycle_started_window_ignores_ordinary_book_movement`); and post-cycle admission generation/account/status/scope/read cancellation cases (`test_post_cycle_next_action_rejects_*`, `test_post_cycle_next_action_cancellation_drains_read_and_teardown_rejects`).

In particular, please probe subsequent-attempt partial/unknown/interrupted acknowledgments and registered-only cleanup, conflicting manual close/admission calls, cancellation/teardown while a later submission or close is held, zero-fill continuation followed by healthy invalidation, and inventory mutation between admission and the final SDK guard. Do not infer direct coverage from helper/shared-path tests.

## Human Live Trading Performed

No.
