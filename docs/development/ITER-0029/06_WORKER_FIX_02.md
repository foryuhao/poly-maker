# ITER-0029 — Worker Implementation

## Status

IMPLEMENTED

The ordinary Fix02 findings B1-R, M2-R, and M4 are implemented and directly covered. The previous reviewer-reported full-suite M5 failure did not recur in the exact node or this full-suite run. Its original cause remains unproven; the positive regression now emits detailed continuation/owner/close/capital diagnostics if it fails again. Reviewer should make the final M5 determination from this evidence.

## Architect Contract

ITER-0029 frozen Architect Contract `docs/development/ITER-0029/01_ARCHITECT.md`, approved for DG-004; Fix02 authority is the immutable Reviewer re-review `docs/development/ITER-0029/05_REVIEWER_REREVIEW_01.md` (SHA-256 `109deef077489b71a1522b629cefeec0ebd0345198a2194f3d5e2157d5218922`). Fix02 addressed only B1-R, M2-R, M4 and the requested bounded M5 diagnostic. It preserves Fix01 behavior and does not change Frozen SOP strategy semantics or scope.

## Base Repository State

Branch `rdp-phase0-dev`; source baseline HEAD `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`. Fix01 source/test changes were already present and uncommitted when this repair started. The shared tree also contains Orchestrator-owned iteration documents and prior `research_outputs/`; I did not modify or remove those. Worker Fix02 added changes only to `src/polymaker/rdp_runtime.py` and `tests/test_rdp_placement.py`. No Git history operation was performed.

## Existing Components Reused

The repair uses the existing REBALANCE continuation body, placement owner, `_monitor_rebalance_attempt`, registered-scope close/reconciliation, terminal failure retention, and existing fake SDK integration fixture. It adds no execution subsystem or runtime interface.

## Files Changed

- `src/polymaker/rdp_runtime.py` — operation-local accepted-clock tracking, complete operation-budget checks, repair-category latching before terminal evidence retention, and cleanup after a post-ack invalid clock.
- `tests/test_rdp_placement.py` — direct clock/budget boundary regressions, assertions over retained terminal/log classifications, and detailed failure output for the existing multi-cycle NORMAL positive-path regression.

Final SHA-256: runtime `65a4bc22eb993afeb8e7d757d4fa0b4710efad93862355e45cfffaf1a10b4223`; placement tests `8efced276a85587c50d73e4b2705dd7c8960b7b821ea32abc421d9cd1e97c967`.

## Implementation Summary

A single REBALANCE continuation operation now validates each sampled wall-clock value and rejects a backward sample relative to the last accepted value, including samples after admission, submission, market/account awaits, and monitor waits. A clock fault after an acknowledged post latches STOP and invokes cleanup for the exact resolved attempt before returning. The monitor requires strictly more than `order_budget_s + close_budget_s` at entry and after each awaited evidence read/wait; equality and less time close the registered repair scope without another risk action. REBALANCE cleanup classifies the STOP category before retaining the cached terminal result or emitting terminal-finished evidence, while preserving the detailed triggering cause. The existing NORMAL positive-path assertion is unchanged; failure now includes reason, terminal state, owner/close proofs, submissions, cycles, capital reasons, and admission evidence.

## Behavior Before

- A finite in-market backward clock could be treated as ordinary elapsed time and only stop later at the market boundary.
- The monitor could continue resting with less time remaining than the requested order plus cancel/reconcile budgets.
- The cleanup path could retain a default `STATE_UNRELIABLE` terminal result before later public reclassification to `TIME_EXHAUSTED`, `CAPITAL_INSUFFICIENT`, or `MARKET_NOT_TRADABLE`.
- A post-ack invalid clock detected in the newly added guard initially returned without closing the acknowledged order; the first Fix02 rerun exposed this and the branch was corrected to run registered-attempt cleanup.

## Behavior After

- An invalid, non-finite, pre-epoch, or regressed clock latches `STATE_UNRELIABLE`; a live registered repair is closed/reconciled and the monitor does not poll again.
- The entry and live monitor stop at the exact order-plus-close boundary and below it. Just-above-budget monitoring may perform one evidence cycle, then rechecks after its await before continuing.
- Terminal failure result and terminal-finished log carry the same frozen stop category as the public completion; the detailed cause remains separately retained.
- Healthy bounded NORMAL continuation still reaches its original positive assertion and is not classified terminal by these repair guards.

## Tests Added / Changed

- Added `test_iter0029_fix02_backward_clock_inside_market_stops_before_second_poll` using the real fake SDK continuation and registered-order cleanup.
- Added exact/below entry budget integration cases and exact/below/just-above live-monitor boundary cases.
- Extended `test_rebalance_safety_stop_closes_scope_without_reposting` to verify the cached terminal result and stop/failure-finished/rebalance-stopped event categories agree for time, capital, market, and state failures.
- Added failure-only diagnostics to `test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively`; its original `REBALANCE_HANDOFF`, cycle, fill, and capital assertions remain unchanged.

## Tests Executed

All Python tests used the pre-import DNS/socket-blocked runner `docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py`; runner logs report `REVIEW_NETWORK_ATTEMPTS []`.

1. Original Reviewer A2 probe reproduction:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no docs/development/ITER-0029/rereview_01_evidence/additional_probes_v2.py`
2. First core repair probe plus backward-clock test, before fixing the newly exposed post-ack cleanup omission:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no docs/development/ITER-0029/rereview_01_evidence/additional_probes_v2.py tests/test_rdp_placement.py::test_iter0029_fix02_backward_clock_inside_market_stops_before_second_poll`
3. Reviewer A2 probes plus backward-clock regression after the cleanup correction:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0029/rereview_01_evidence/additional_probes_v2.py tests/test_rdp_placement.py::test_iter0029_fix02_backward_clock_inside_market_stops_before_second_poll`
4. Reviewer probes plus all new budget boundary regressions:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0029/rereview_01_evidence/additional_probes_v2.py tests/test_rdp_placement.py::test_iter0029_fix02_backward_clock_inside_market_stops_before_second_poll tests/test_rdp_placement.py::test_iter0029_fix02_entry_requires_strictly_more_than_order_and_close_budget tests/test_rdp_placement.py::test_iter0029_fix02_live_budget_rechecks_after_poll_awaits`
5. Exact historical M5 positive node with full capture:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=long --show-capture=all 'tests/test_rdp_placement.py::test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively[fills1-5.0-False]'`
6. Affected placement suite:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no tests/test_rdp_placement.py`
7. Full suite:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no`
8. Final tree checks:
   `git diff --check && git status --short --branch && git ls-files --others --exclude-standard && git diff --stat`

## Test Results

- Original A2 reproduction: **5 failed, 13 passed**. Exact failures were `test_retained_terminal_stop_category_agrees[time]`, `[capital]`, and `[market]` (cached `STATE_UNRELIABLE` instead of expected categories); `test_monitor_checks_full_order_cancel_reconcile_budget` (one wait occurred with 1.5 seconds remaining against 2 seconds required); and `test_backward_clock_inside_market_is_unreliable` (`TIME_EXHAUSTED` instead of immediate `STATE_UNRELIABLE`). State-category, invalid-clock shapes, pending/malformed fill and other cases passed in this first run.
- First core repair rerun: **5 failed, 14 passed**. All five failures were the live invalid-clock cases `[bool-live]`, `[str-live]`, `[nan-live]`, `[inf-live]`, `[before_epoch-live]`; each observed no cancel after an acknowledged post. This exposed the missing cleanup in the post-submit clock-failure branch. That branch now calls the existing registered-attempt terminal cleanup. Exact failure output is preserved.
- Reviewer probes plus clock regression after that correction: **19 passed**.
- Reviewer probes plus new backward-clock and budget boundary integration tests: **24 passed**.
- Exact M5 node: **1 passed**. Captured evidence shows the existing four-cycle run reached its expected `REBALANCE_HANDOFF` with close proofs and cumulative inventory.
- Affected placement suite: **458 passed**.
- Full suite: **1745 passed, 2 skipped** in 101.09s; `REVIEW_NETWORK_ATTEMPTS []`.
- `git diff --check`: passed. Repository remains on `rdp-phase0-dev` with shared pre-existing iteration/audit and research outputs visible; none were removed.

## Regression Results

The repaired A2 matrix and new direct integration tests cover backward-clock cleanup/no second poll; entry equality/below-budget denial before post; monitor equality/below-budget close; just-above-budget continuation followed by an exact-boundary recheck; and consistent retained category/event evidence across time, capital, market, and state STOP causes. Existing pending/malformed-fill cleanup, malformed clock shapes, held post drain, registration behavior, and other continuation tests passed in the affected/full suites.

The historical M5 reviewer failure was a full run with one failure and 1738 passes / 2 skips; the exact node was reported as `TERMINAL` instead of `REBALANCE_HANDOFF`, while capture was disabled. No authoritative original failure cause is present in the evidence available for this repair. The exact node and the current full suite pass. Its original assertions remain intact, and an unexpected outcome now emits detailed retained STOP, owner, close-proof, submission, cycle, capital, and admission evidence in the assertion message. This bounds future diagnosis but does not prove the cause of the historical failure.

## Git Diff Summary

Fix02 changes are limited to the runtime and placement-test files listed above. The shared working tree’s complete diff also includes the already-present ITER-0029 initial implementation/Fix01 files and Orchestrator-owned documentation; its observed aggregate was 9 tracked files, 2553 insertions and 69 deletions. No commit, push, merge, rebase, reset, or tag operation was performed.

Current evidence logs are preserved without overwrite under `/tmp/ITER-0029-worker-fix-02-evidence/`:

- `reviewer_a2_initial_01.log` — initial 5-failure/13-pass reproduction.
- `reviewer_a2_after_core_01.log` — 5-failure/14-pass intermediate run; includes the five live invalid-clock cleanup failures.
- `reviewer_a2_after_postclock_cleanup_01.log` — 19-pass rerun after fixing cleanup.
- `reviewer_a2_budget_matrix_01.log` — 24-pass expanded matrix.
- `m5_exact_node_capture_01.log` — exact positive node before final cleanup correction, pass.
- `m5_exact_node_after_fix_01.log` — exact positive node after fixes, pass with full capture.
- `affected_placement_01.log` — 458-pass placement suite.
- `full_suite_01.log` — final 1745-pass/2-skip full suite.

## Safety Checks

No real-money order or cancellation was sent. The continuation, post, cancellation, and reconciliation evidence used only the existing fake SDK/client tests. The pre-import runner blocked DNS/socket attempts and reported zero attempts in every test run. Live-send authorization and existing order ownership/registered-scope cleanup remain in place. No new external runtime interface or strategy behavior was introduced. `research_outputs/` was preserved.

## Known Limitations

The original M5 full-suite failure cause remains undetermined because its original run used capture suppression and no exact failure log with internal cause was available to this repair. Current exact-node and full-suite runs pass, and future failure output is materially richer; this is evidence for independent review, not a claim that the historic root cause has been established.

## Deferred Findings

None added. The unrelated historical M5 failure cause is retained for Reviewer disposition rather than addressed by speculative production changes.

## Reviewer Attention

Please verify the operation-local clock guard covers every relevant awaited stage and that an acknowledged attempt is cleaned before return on clock failure. Check that cached `RDPTerminalFailureResult.stop_reason` and the corresponding STOP/failure-finished events preserve the category and detailed cause together. Confirm the equality/just-above budget semantics match the frozen order-plus-close allowance. For M5, decide whether the intact positive regression plus full-suite pass and failure-only diagnostics are sufficient, given that the prior single full-run failure’s cause remains unavailable and unreproduced.

## Human Live Trading Performed

No.
