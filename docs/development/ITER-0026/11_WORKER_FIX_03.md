# ITER-0026 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented only the authorized Fix03 repair to ITER-0026 Architect Contract `01_ARCHITECT.md`, delivery behavior DG-006, as directed by `10_REVIEWER_REREVIEW_02.md`. The change connects actual, matching unresolved placement-owner evidence to the existing terminal STOP path independently of the outer authorized submission result label. No contract, SOP, strategy, or scope changes were made.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `ff2e5cbd2771693c9c46e8440cfab92f6ef59eb5`
- Pre-existing untracked `research_outputs/` was preserved; it was not inspected, modified, or included in the implementation diff.
- The final tracked diff is limited to the authorized RDP runtime and placement test files. No Git history operation was performed.

## Existing Components Reused

Reused the existing immutable `RDPInitialSubmissionResult` and gateway evidence, exact current `RDPPlacementAttempt` owner snapshot, placement-owner provenance, terminal failure latch/result, cached submission behavior, and existing cleanup path. The regression uses the existing `ExecutionGateway`, `UserEventProcessor` capture, and fake SDK client.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

## Implementation Summary

Added one shared exact-owner matcher that accepts evidence only when the resolved snapshot is the current owner object, matches the submission attempt ID and selected market slug/condition, and is `PARTIAL` or `UNKNOWN`, unresolved, and replacement-blocked. The terminal predicate now checks this evidence for any authorized outer result before wrapper-outcome classification. The terminal-cause selector uses the same matcher and retains the owner's categorical reason; interruption reason retains precedence.

Added an actual `ExecutionGateway` fake-SDK regression where `post_orders` injects an unmatched selected-market MATCHED trade and returns two explicit rejections. It verifies the immutable outer EMPTY result and gateway evidence survive, while the matching UNKNOWN owner causes STOP with `PENDING_CAPTURE_ANOMALY`. The test checks one resolve and post, no retry, no cancellation or ownership adoption, no registered/unregistered IDs or cleanup barrier, preserved pending anomaly, blocked admission, and stable cached submission. Existing clean EMPTY, no-send, sign-failure, and default-denied compatibility coverage remains.

## Behavior Before

An authorized submission whose gateway wrapper result was `EMPTY` bypassed the terminal predicate even if the exact current owner independently retained an unresolved, replacement-blocking capture anomaly. The owner evidence remained unresolved, but terminal STOP evidence was absent.

## Behavior After

Any authorized result label reaches the existing STOP path when the submission carries the exact current, market-compatible unresolved owner evidence described above. The immutable outer result remains EMPTY with `AUTHORITATIVE_EMPTY_RESPONSE` and its EMPTY gateway evidence; terminal cause comes from the retained owner classification. Clean EMPTY and clean no-send/sign-failure/default-denied paths remain nonterminal. Existing interruption precedence and registered-only cleanup behavior remain in place.

## Tests Added / Changed

- Added `test_authoritative_empty_with_unresolved_capture_latches_terminal_without_retry_or_cancel` using the real gateway executor with a fake SDK and real processor capture path.
- No prior tests were removed or weakened. Existing clean EMPTY and no-send/sign-failure regressions continue to run.

## Tests Executed

1. `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_rdp_placement.py -k 'no_post_with_unresolved_capture or clean_authoritative_empty or authoritative_empty_with_unresolved_capture or complete_ack_interruption'`
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q docs/development/ITER-0026/review_evidence/test_iter0026_rereview_empty02.py`
3. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py`
4. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py tests/test_rdp_capital.py tests/test_rdp_accounting.py tests/test_gateway_positions_v2.py`
5. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_favorite_live.py tests/test_favorite_first.py`
6. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py -k 'no_post_with_unresolved_capture or clean_authoritative_empty or authoritative_empty_with_unresolved_capture' docs/development/ITER-0026/review_evidence/test_iter0026_rereview_nosend01.py docs/development/ITER-0026/review_evidence/test_iter0026_rereview_empty02.py`
7. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q`
8. `git diff --check`

The archived reviewer runner blocks and counts DNS/socket access before imports. The reported independent probe and suite runs each ended with `REVIEW_NETWORK_ATTEMPTS []`.

## Test Results

- Narrow placement lifecycle slice: **6 passed, 367 deselected**.
- Archived independent EMPTY/capture probe: **1 passed**; `REVIEW_NETWORK_ATTEMPTS []`.
- Placement suite: **373 passed**; `REVIEW_NETWORK_ATTEMPTS []`.
- Affected RDP integration group: **222 passed**; `REVIEW_NETWORK_ATTEMPTS []`.
- FL regression group: **79 passed**; `REVIEW_NETWORK_ATTEMPTS []`.
- Final focused M2 and compatibility slice, including both archived no-send probes and EMPTY probe: **7 passed, 369 deselected**; `REVIEW_NETWORK_ATTEMPTS []`.
- Final full suite on the final tree: **1648 passed, 2 skipped in 82.65s**; `REVIEW_NETWORK_ATTEMPTS []`.
- `git diff --check`: passed.

One intermediate focused run failed because the new test incorrectly expected `RDPTerminalFailureResult` to expose gateway fields directly. The retained gateway evidence is carried by its `source_submission`; the assertion was corrected to verify that the terminal result retains the same submission and gateway-evidence objects. The focused rerun and final full suite passed. This was a test assertion correction, not a runtime failure.

## Regression Results

The final full suite includes the prior Fix01/Fix02 regression coverage, including complete-ack interruption and teardown, post-STOP public API/certification guards, actual per-ID registration evidence and partial-registration cleanup, no-send/sign-failure unresolved-capture cases, and clean EMPTY compatibility. The targeted archived EMPTY reproducer also passes. No regression failures remain in the final run.

## Git Diff Summary

- `src/polymaker/rdp_runtime.py`: 18 insertions, 14 deletions.
- `tests/test_rdp_placement.py`: 81 insertions.
- Total: 99 insertions, 14 deletions across two files.
- `git diff --check`: passed.
- Final working tree: only those two tracked modifications plus the preserved untracked `research_outputs/` directory (748 untracked files); HEAD remains `ff2e5cbd2771693c9c46e8440cfab92f6ef59eb5`.

## Safety Checks

- No live network access occurred; the blocked runner recorded zero DNS/socket attempts.
- No real-money order or cancellation was performed; the SDK was fake.
- The unmatched trade remained an unowned pending capture anomaly; no adoption or speculative cancellation was introduced.
- Terminal cause preserves `PENDING_CAPTURE_ANOMALY`, while the original EMPTY result and gateway evidence remain available through the retained submission.
- No strategy behavior, shared-core file, external runtime interface, Frozen SOP, or unrelated file was changed.
- No commit, push, merge, rebase, tag, reset, or other Git history operation was performed. No merge to `main` occurred.

## Known Limitations

The terminal state intentionally retains unresolved exposure and requires human attention when capture evidence cannot be attributed. This repair does not infer ownership or certify flat inventory from the unmatched event. ITER-0026 still requires independent Reviewer re-review before DG-006 can be declared closed.

## Deferred Findings

None introduced. Any findings outside the Fix03 M2 repair remain deferred under the frozen ITER-0026 contract.

## Reviewer Attention

Please verify that the single exact-owner matcher applies across authorized wrapper outcomes, preserves attempt/market/current-object provenance, and supplies the owner's reason after interruption precedence. Confirm the new actual-SDK EMPTY-with-capture regression, prior no-send/sign-failure cases, and clean EMPTY/default-denied compatibility. Confirm that terminal evidence retains the immutable EMPTY submission/gateway result and that no retry, adoption, or speculative cancellation occurs.

## Human Live Trading Performed

NO
