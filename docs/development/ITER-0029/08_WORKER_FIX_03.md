# ITER-0029 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

ITER-0029 frozen Architect Contract: docs/development/ITER-0029/01_ARCHITECT.md. Current repair authority is the immutable ITER-0029 Fix03 dispatch. Fix only remaining B1-F final-guard operation-clock history, M1-C primary terminal cause after held SDK drain, and M5-R safe diagnostic association. Preserve prior Fix01/Fix02 behavior and all safety assertions. No strategy or scope redesign.

## Base Repository State

Branch: rdp-phase0-dev. Base HEAD remains 3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827. The cumulative iteration tree was already modified by prior authorized Worker rounds and includes Orchestrator-owned audit/evidence artifacts; this Fix03 added code/test edits only to runtime and placement tests. Existing untracked research_outputs/ was preserved. No Git history operation was performed.

## Existing Components Reused

Reused the current _RDPInitialSubmissionOperation.guard_lock, existing SDK final submission permission guard, existing primary terminal STOP latch/drain path, owner’s attempt-keyed _close_proofs mapping, and existing NORMAL continuation integration/fake SDK tests. No new production subsystem or runtime interface was added.

## Files Changed

Fix03 code/test edits:

- src/polymaker/rdp_runtime.py
- tests/test_rdp_placement.py

The cumulative pre-review iteration diff also contains already-authorized prior-round edits in src/polymaker/execution/gateway.py, src/polymaker/execution/rdp_placement.py, src/polymaker/risk/rdp_capital.py, tests/test_execution.py, tests/test_rdp_capital.py, plus prior Orchestrator-owned DELIVERY_GAPS.md and ITERATION_INDEX.md changes. Fix03 did not edit those files.

## Implementation Summary

The operation now retains its last accepted wall-clock sample from admission through the submission operation. The SDK’s existing final permission guard checks and advances that same operation-local history while holding the existing guard lock. A backward sample between signing and the actual SDK POST therefore returns a no-send result and latches STOP before any new POST. This preserves fresh official receipts as separate evidence while preventing them from overriding a clock regression within the same operation.

After the SDK await drains, the continuation consumes an already-latched deadline/teardown/caller terminal outcome before performing a secondary wall-clock read. A later NaN clock cannot replace the first terminal cause or change the original caller-visible deadline/cancellation result; normal cleanup still waits for the actual registered-order cleanup path.

The failure-only diagnostic now reports close proofs by their existing owner-map attempt key. It does not assume RDPCloseResult has an attempt_id, and includes the associated retained cycle close fields. A new controlled integrated test causes the positive NORMAL case to fail at attempt 2 proof release and confirms the diagnostic itself remains available. This controlled probe validates diagnostic usability; it does not establish the root cause of the historical intermittent NORMAL failure.

The placement tests’ structured-event capture now attaches a temporary root logging handler and captures dictionary LogRecord.msg values. This avoids mutating structlog processors after cached loggers have been initialized, which caused existing event assertions to receive empty lists in runtime-suite-then-placement order. Assertions remain unchanged in meaning and still check actual event fields.

## Behavior Before

The final SDK guard checked fresh market/account evidence but did not compare its wall-clock sample with the operation’s previously accepted sample; a backward sample after signing could still produce a POST before STOP. A secondary clock read after the SDK drained could obscure an already-latched terminal cause. Failure diagnostics dereferenced a nonexistent cycle.reason / RDPCloseResult.attempt_id-style association and could raise instead of reporting the actual keyed proof state. Existing capture_logs assertions could lose records after earlier tests initialized cached structlog loggers.

## Behavior After

A regressed operation clock is denied by the final guard before POST and yields retained STOP evidence. A primary deadline or teardown remains the result even if the post-drain clock becomes NaN; cleanup drains and cancels the registered order once. Failure-only diagnostics retain the exact owner attempt-to-proof key association. Existing healthy NORMAL positive assertions and all fail-closed assertions remain active.

## Tests Added / Changed

Added durable fake-SDK integration coverage for:

- clock regression between signing and the final SDK guard with fresh status/account receipts: zero POST, no speculative cancel, terminal STOP, and OPERATION_CLOCK_REGRESSED guard evidence;
- held POST followed by deadline or teardown, then a NaN clock at drain release: primary cause/result preserved, registered-order cleanup completed once, and no detached SDK operation;
- controlled attempt-2 close-proof rejection on the actual repeated NORMAL continuation path: diagnostic contains CLOSE_PROOF_NOT_RELEASED and associates attempt 2 using owner._close_proofs[2] and the retained cycle map;
- existing healthy three-cycle NORMAL continuation, exact ±5 handoff, and post-sign market evidence mutation, retained as positive/negative controls.

Changed only the test capture mechanism for pre-existing structured event assertions; did not relax their assertions.

## Tests Executed

All commands used the repository’s pre-import DNS/socket blocking runner:

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py ...

Test outputs are preserved under /tmp/ITER-0029-worker-fix-03-evidence/; exact command/result manifest:

| Log | Exact pytest command arguments | Result |
|---|---|---|
| reviewer_final_initial_01.log | -q -s --tb=short --show-capture=all docs/development/ITER-0029/rereview_02_evidence/final_guards_and_diagnostics.py | 1 failed, 3 passed. B1 performed one POST before STOP; M5 diagnostic raised on absent attempt association. M1 deadline/teardown passed because its terminal-before-secondary-clock change had already been applied. Network attempts []. |
| final_matrix_after_fixes_01.log | -q -s --tb=short --show-capture=all docs/development/ITER-0029/rereview_02_evidence/final_guards_and_diagnostics.py tests/test_rdp_placement.py::test_iter0029_fix03_final_guard_rejects_regressed_operation_clock_before_post tests/test_rdp_placement.py::test_iter0029_fix03_primary_interruption_survives_postdrain_clock_failure tests/test_rdp_placement.py::test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively | 2 failed, 9 passed. B1 and both M1 probes passed. M5 exposed helper access to nonexistent RDPInitialCycleCompletion.reason (same helper also blocked the controlled durable case). Network attempts []. |
| final_matrix_after_diagnostic_fix_02.log | Same command as above | 1 failed, 10 passed. Probe called the parametrized test directly with its prior six positional arguments; new required diagnostic parameter caused TypeError. |
| final_matrix_after_diagnostic_fix_03.log | Same command as above | Collection failed: pytest rejects a parametrized argument with a default value (diagnostic_probe). |
| final_matrix_after_diagnostic_fix_04.log | -q -s --tb=short --show-capture=all docs/development/ITER-0029/rereview_02_evidence/final_guards_and_diagnostics.py tests/test_rdp_placement.py::test_iter0029_fix03_final_guard_rejects_regressed_operation_clock_before_post tests/test_rdp_placement.py::test_iter0029_fix03_primary_interruption_survives_postdrain_clock_failure tests/test_rdp_placement.py::test_iter0029_fix03_normal_failure_diagnostic_uses_attempt_keyed_proofs tests/test_rdp_placement.py::test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively | 11 passed in 22.07s; network attempts []. |
| affected_rdp_runtime_placement_capital_01.log | -q --tb=short tests/test_rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_capital.py | 11 failed, 590 passed in 91.35s. Six structured-event assertions had empty captures and five raised StopIteration; pytest logged ValueError: I/O operation on closed file after cached structlog/logging configuration changes. Network attempts []. The original failures are preserved. |
| diagnose_capture_test_01.log | -q --tb=short tests/test_rdp_placement.py::test_initial_prefill_terminal_freshness_boundary_matches_result_and_event | 4 passed in 1.91s in a fresh process; network attempts []. |
| affected_rdp_runtime_placement_capital_s_01.log | -q -s --tb=short tests/test_rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_capital.py | Same 11 event-capture failures (590 passed); established that -s alone did not fix the capture-order issue. Network attempts []. |
| logging_order_isolation_01.log | -q -s --tb=short tests/test_rdp_placement.py::test_gateway_safe_diagnostics_hide_sdk_sentinel_and_error_chain tests/test_rdp_placement.py::test_initial_prefill_terminal_failure_recaptures_freshness_and_event tests/test_rdp_placement.py::test_initial_prefill_terminal_freshness_boundary_matches_result_and_event | 14 passed in 1.85s; network attempts []. |
| runtime_then_capture_isolation_01.log | -q -s --tb=short tests/test_rdp_runtime.py tests/test_rdp_placement.py::test_initial_prefill_terminal_failure_recaptures_freshness_and_event tests/test_rdp_placement.py::test_initial_prefill_terminal_freshness_boundary_matches_result_and_event tests/test_rdp_placement.py::test_rebalance_safety_stop_closes_scope_without_reposting tests/test_rdp_placement.py::test_iter0029_fix01_unregistered_repair_ack_keeps_known_inventory_and_no_false_close | 11 failed, 62 passed in 64.97s before the capture adapter. This isolated the failure to runtime-suite-then-placement capture order. |
| runtime_then_capture_after_harness_fix_01.log | Same arguments as above | 73 passed in 63.60s; network attempts []. |
| affected_rdp_runtime_placement_capital_capturefix_01.log | -q -s --tb=short tests/test_rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_capital.py | 601 passed in 90.56s; network attempts []. |
| full_suite_01.log | -q --tb=short | 1,749 passed, 2 skipped in 102.95s; network attempts []. |

The logging-order correction is limited to the approved placement test file and was verified first against the exact failing runtime→placement sequence, then affected suites and the full suite. Historical intermittent NORMAL failure root cause remains unproven; a controlled diagnostic failure verifies the report is usable without weakening positive NORMAL assertions.

## Test Results

The Fix03 reviewer probe and durable regressions pass. The affected RDP suites pass after the capture-state correction. The full suite passes: 1,749 passed, 2 skipped. The exact final-tree test run had no blocked network attempts.

## Regression Results

Healthy repeated NORMAL continuation remains directly tested with cumulative cycles and both exact ±5 REBALANCE handoffs. The final clock guard rejects before POST; deadline and teardown preserve their original terminal result through a failing secondary clock; and registered cleanup is exercised. The controlled proof-release diagnostic test confirms keyed attempt evidence. The independent probe and complete regression suite passed on the final tree.

## Git Diff Summary

Cumulative iteration diff against base: 9 tracked files, 2,951 insertions and 215 deletions. Fix03 itself touched src/polymaker/rdp_runtime.py and tests/test_rdp_placement.py. git diff --check exited 0 with no output. HEAD remains the stated base because Worker performed no Git history operations.

Fix03 final SHA-256:

- src/polymaker/rdp_runtime.py: cbe73dcf589ea2d7f14e1df3b300cd49991c40d4c93a5b9a52c699e4891b0c98
- tests/test_rdp_placement.py: 3930b7ecea76c0452f958582dbe57fe85f6f5c8f25efb3919fdf468bd9be0e95

## Safety Checks

- No live-money trading, real order, real cancellation, external network API request, commit, push, merge, rebase, or history rewrite was performed.
- Fake SDK tests used the pre-import runner; every final run recorded REVIEW_NETWORK_ATTEMPTS [].
- The final STOP path denies the regressed operation-clock attempt before POST, retains the first terminal cause, and performs registered-order cleanup after the held SDK call drains.
- Existing untracked research_outputs/ was left untouched.

## Known Limitations

The controlled M5 test intentionally synthesizes an attempt-2 close-proof release rejection to verify diagnostics. It does not prove the root cause of the historical intermittent NORMAL failure; that remains for independent review. No live-market evidence is produced by fake SDK tests.

## Deferred Findings

No unrelated production findings were implemented. The historical NORMAL intermittent failure is not declared resolved by this report; only the previously unavailable failure diagnostic is corrected and directly tested.

## Reviewer Attention

Please inspect operation-clock history propagation from admission through signing and final SDK permission guard; STOP precedence after held SDK drain; the attempt-keyed close proof diagnostic; and the test-only event-capture change. The six event assertions retain their field-level checks, the five corresponding capture-order errors are preserved above, and final affected/full suites pass without weakening safety assertions.

## Human Live Trading Performed

No.
