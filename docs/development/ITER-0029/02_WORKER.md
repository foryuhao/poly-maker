# ITER-0029 — Worker Implementation

## Status

IMPLEMENTED

This records implementation and test evidence for the frozen DG-004 contract. It is not a Reviewer PASS, a DG closure, MICRO_LIVE_READY, or authority for live execution.

## Architect Contract

Implemented the approved `docs/development/ITER-0029/01_ARCHITECT.md` (SHA-256 `fa2c9fab2346b94de58353c5564e9dbe6cc85bc6a6d34d01a0859c4ceb62423c`) for DG-004: operate a retained, reconciled REBALANCE handoff and return through the existing retained NORMAL continuation after confirmed recovery. Existing NORMAL-only entry behavior and pair invariants remain supported.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Verified base HEAD: `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`.
- Frozen contract and its archive are present. No Git history operation was performed.
- The pre-existing untracked `research_outputs/` tree was preserved and not read or changed as part of implementation.

## Existing Components Reused

Reused the retained RDP input session and official market/account evidence, existing quote builders and mode-specific gates, gateway and live-send guard path, pre-POST capture, placement owner, user-event fill processor, local order store, capital projector, and existing cancel/reconcile/close-proof machinery. The recovered NORMAL operation composes the existing NORMAL continuation rather than duplicating it.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/gateway.py`
- `src/polymaker/execution/rdp_placement.py`
- `src/polymaker/risk/rdp_capital.py`
- `tests/test_execution.py`
- `tests/test_rdp_capital.py`
- `tests/test_rdp_placement.py`

No other tracked file was changed. The existing untracked `research_outputs/` files remain untouched.

## Implementation Summary

Added an explicit authorized retained REBALANCE continuation. It accepts only a complete, released REBALANCE handoff, submits a five-share post-only repair on the deficient side, and uses the existing market/book/tick, account, capital, ownership, fill and close evidence. It retains repair attempts and confirmed fills in the existing cumulative inventory and cost scope. No-fill alone does not terminate or repeatedly replace an unchanged resting order. Repricing and recovery close the existing remainder through cancel/reconcile before reconsidering inventory or posting again. Confirmed recovery proceeds into the existing NORMAL continuation, which can send the actual skewed dual pair.

Added an opt-in strict single-response acknowledgement path for repair only. It distinguishes authoritative success or clean rejection from uncertain/malformed/conflicting responses; observed IDs remain distinct from acknowledged ownership. Mode-aware placement ownership and capital validation admit a matching active repair without weakening the NORMAL pair shape or closed-order resurrection checks. Repair and recovery/STOP evidence is emitted through safe structured events using retained facts; no raw account or SDK payload is logged.

Cancellation and teardown drain the continuation and close the registered repair scope. The caller receives cancellation when it cancels the monitor; session `aclose()` completes normally after it drains that task. The terminal result retains the cleanup barrier and does not claim an unresolved repair order is closed.

## Behavior Before

A closed NORMAL cycle could expose a factual REBALANCE handoff, but the runtime had no operation to own the repair through fills, close, confirmed recovery and a subsequent NORMAL pair. The strict pair path assumed two orders, and legacy single acknowledgement semantics were not sufficient to establish attributable repair ownership.

## Behavior After

An explicitly authorized call can consume an initial or later retained REBALANCE handoff and operate the deficient-side repair loop. It uses current evidence and real close barriers between replacements, preserves cumulative confirmed inventory/cost, and resumes the established NORMAL loop only after current inventory remains below the Frozen `< 5` boundary following close and reconciliation. Denied or concurrent calls do not post. Unsafe or uncertain outcomes use the existing fail-closed cleanup path.

## Tests Added / Changed

Added actual fake-official-client integration coverage for the full retained handoff-to-repair-to-NORMAL path, including both deficiency signs, initial and later NORMAL handoff, larger deltas, no-fill resting, close-before-reprice, dynamic tick and post-await revalidation, fractional fills/cost, late fill/cancel race, final-guard re-admission, safety exits, minimum-size rejection, strict acknowledgement, unauthorized/concurrent calls, cancellation and teardown. Extended gateway and capital tests for strict single parsing, active repair backing and closed-scope contradictions. Existing NORMAL compatibility, pair gateway, capital and placement tests remain part of the regression runs.

The first teardown probe had one incorrect assertion: it expected `aclose()` itself to raise `CancelledError`. The observed and intended behavior is that teardown returns normally after draining, while the concurrent continuation caller receives `CancelledError`; registered-order cleanup is complete. The test now asserts that behavior, and both cancellation/teardown probes pass together. The initial caller-cancellation implementation probe also failed before its expected cleanup condition was aligned to the conservative replacement-blocked result; the corrected direct test passes and verifies barrier, closed ID, and no unresolved exposure.

## Tests Executed

All pytest commands below use the pre-import network-blocking runner. The recorded footer is `REVIEW_NETWORK_ATTEMPTS []` for each final run.

- Focused continuation/cancellation check:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py::test_cancelled_rebalance_monitor_drains_then_closes_repair_scope tests/test_rdp_placement.py::test_rebalance_session_teardown_drains_then_closes_repair_scope`
- Focused REBALANCE tests:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py -k rebalance`
- Affected placement, capital, gateway/execution and runtime suite:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py tests/test_rdp_capital.py tests/test_execution.py tests/test_rdp_runtime.py`
- Adjacent regression suite:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_account.py tests/test_rdp_accounting.py tests/test_rdp_lifecycle.py tests/test_rdp_live.py tests/test_rdp_marketdata.py tests/test_rdp_userstream.py tests/test_rdp_quotes.py tests/test_rdp_book_filter.py tests/test_favorite_live.py`
- Full suite:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q`
- Final tree checks: `git status --short`, `git ls-files --others --exclude-standard`, `git diff --stat`, and `git diff --check`.

Full per-run logs and a per-log result/failure-node manifest are preserved at `/tmp/ITER-0029-worker-evidence/`; see `MANIFEST.md` there. The captured exploratory logs preserve the pytest summaries and failure nodes. Earlier shell selector strings were not embedded in those log files, so no unrecorded selectors are reconstructed here.

## Test Results

- Final focused cancellation and teardown: **2 passed** in 1.77s; network attempts 0.
- Final focused REBALANCE suite: **24 passed, 420 deselected** in 2.56s; network attempts 0 (`rebalance_focused_final5.log`, exact final tree).
- Final affected suite: **703 passed** in 85.63s; network attempts 0 (`affected_rdp_execution_final3.log`).
- Adjacent regressions: **467 passed** in 4.23s; network attempts 0 (`related_regressions_final.log`).
- Final full suite on the exact delivered tree: **1731 passed, 2 skipped** in 100.31s; network attempts 0 (`full_suite_final2.log`).
- `git diff --check`: passed with no output.
- The first final-tree teardown run (`rebalance_cancel_teardown_final1.log`) had 1 passed / 1 failed because the teardown test expected an exception from `aclose()`; corrected semantics/assertion are described above and the exact two-test rerun passed.

## Regression Results

Existing NORMAL continuation and pair-acknowledgement behavior was exercised in the affected and full suite, including the adjacent RDP/account/market-data/user-stream/quote/BookFilter and favorite-live tests. Final full-suite results include all modified files and the teardown assertion correction.

One earlier affected run (`affected_rdp_execution_final.log`) had **699 passed, 1 failed** in `test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively[fills1-5.0-False]`, with `CLOSE_PROOF_NOT_RELEASED`. Its exact isolated rerun passed (`normal_continuation_flake_repro.log`), and the following affected run passed 700 tests (`affected_rdp_execution_final2.log`); the final affected and full runs also pass. I cannot establish a root cause from these results and therefore preserve the intermittent failure as unresolved diagnostic history rather than claiming it was caused by or conclusively fixed by this iteration.

Other exploratory failures during integration are preserved with their test node IDs in the evidence manifest. They were followed by correction iterations and passing focused, affected, or full runs; the final full-suite run is authoritative for this tree. There were no external network attempts in any recorded run.

## Git Diff Summary

`git diff --stat`: **7 files changed, 1841 insertions(+), 25 deletions(-)**. The final test-only teardown assertion correction is included in this count. `git diff --check` passes. Working tree contains only the seven authorized tracked modifications and pre-existing untracked `research_outputs/`; nothing is staged. HEAD remains `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`.

## Safety Checks

- No real-money order or cancellation was performed. All SDK scenarios are deterministic fakes.
- Every pytest invocation used the pre-import DNS/socket-blocking runner; final affected, adjacent, focused and full logs report zero network attempts.
- Explicit authorization, existing send gates, post-only requirements, capture-before-POST, close/reconcile barriers, ownership checks and fail-closed uncertainty handling remain in the execution path.
- No Frozen SOP, external runtime interface, CLI, dependency, shared core, FL/IM strategy, or Git history was changed. No merge to `main` occurred.
- Untracked `research_outputs/` was preserved.

## Known Limitations

This is fake-client implementation evidence only; it establishes neither live venue behavior nor economic performance. The earlier transient `CLOSE_PROOF_NOT_RELEASED` result has no proven root cause despite successful isolated, affected and full reruns. Independent review must inspect the actual diff and probe the full acceptance matrix before DG-004 can be considered closed. Direct repair-path evidence includes `test_rebalance_conflicting_single_ack_stops_with_observed_id_unadopted`, `test_rebalance_cancel_race_fill_is_reconciled_before_normal_recovery`, and the caller-cancellation/session-teardown repair tests. Held SDK POST drain and per-ID registration-failure mechanics are covered by existing shared placement tests (`test_submission_interruption_drains_real_gateway_post_before_return`, `test_terminal_cleanup_drains_blocked_sdk_operation_before_release`, and `test_terminal_registration_evidence_matches_actual_registry_per_id`), not a repair-specific held-POST or induced one-leg registration-failure integration probe. Those shared tests establish the reused mechanisms but do not by themselves prove the new REBALANCE composition under those exact fault injections; this is an explicit reviewer check/coverage limitation. The direct cancel/fill proof race is repair-specific; other owner/proof contradiction cases are exercised by existing shared close-scope tests. This Worker report does not claim Reviewer PASS or close any gap.

## Deferred Findings

DG-002, DG-005, DG-007–DG-012 and remaining delivery requirements stay open as specified by the Architect and DELIVERY_GAPS. No work outside DG-004 was undertaken.

## Reviewer Attention

Please inspect mode/cardinality checks at every placement ownership boundary, strict-single acknowledgement alias/cardinality handling, active-versus-closed repair evidence in capital projection, and the actual close/reconcile/recovery-to-NORMAL path. Verify the two deficiency signs and later handoff with current cumulative economics, cancellation/late-fill barriers, post-await/final-guard freshness, and the unresolved intermittent close-proof history. The temporary evidence manifest is `/tmp/ITER-0029-worker-evidence/MANIFEST.md`; its SHA-256 is `aa3c8388c6b792775510b6c8e2b2e97007e3e15aafc5867ee2b3f23a0e24c63a`.

## Human Live Trading Performed

No. No live order, cancellation, or account action was performed.
