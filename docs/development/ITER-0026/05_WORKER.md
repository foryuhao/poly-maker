# ITER-0026 — Worker Implementation

## Status
IMPLEMENTED

## Architect Contract
Implemented the approved DG-006 contract in `docs/development/ITER-0026/01_ARCHITECT.md`: connect actual partial/unknown/unresolved initial placement results and unreliable initial-cycle results to a retained terminal STOP result; latch off new risk before cleanup; clean only matching registered ownership through the existing scoped close barrier; retain acknowledgment, observed, inventory, completeness, and unresolved exposure evidence; drain cleanup work on cancellation and teardown.

## Base Repository State
- Repository: `/home/ubuntu/poly-maker`; branch: `rdp-phase0-dev`.
- HEAD at Worker start: `50df3242113bc7719322bae9b47a5d4b7cf80c1a` (Orchestrator's additive dispatch-artifact archival commit; implementation base remained unchanged at `e325febe78df95a99637ccbd148fb9ff3bcd113a`).
- Tracked tree was clean before implementation. Existing untracked `research_outputs/` was preserved and not modified.
- Frozen SOP, baseline, index, ITER-0026 Architect Contract and dispatch-blocker/recovery records were read. ITER-0025 was not reimplemented.
- No Git history operation was performed.

## Existing Components Reused
- `RDPInitialSubmissionResult`, gateway outcome evidence, `RDPPlacementOwner`, registry/economics validation, existing processor fill accounting and quarantine.
- Existing `close_initial_normal_placement()` / registered scoped close barrier and strict gateway.
- Existing initial-cycle result, operation delegation, shield/drain helpers, session teardown and categorical logging.
- Existing fake SDK, actual `ExecutionGateway` executor, and placement test fixtures.

## Files Changed
- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

## Implementation Summary
Added immutable `RDPTerminalFailureResult` and a runtime-local latched terminal owner/result/task. Actual partial, unknown, resolution-error, and unsafe initial-cycle outcomes latch `STOP / STATE_UNRELIABLE` synchronously before awaiting cleanup. The latch is checked by admission, arming, submission permission, cycle, and fill-window paths. Only a non-ARMED current attempt with matching registered IDs and economics is passed to the existing scoped close path. Nested terminal delegation is permitted while competing actions remain rejected. Existing cycle-close evidence is retained to avoid duplicate cleanup.

Unregistered acknowledgments and observed IDs remain separate and are never adopted or canceled. A zero-ID unknown outcome therefore records unresolved exposure without speculative cancellation. The result records registered and closed IDs, cleanup completeness, unresolved exposure, human-attention requirement, and available confirmed inventory. Inventory is explicitly scoped to registered orders; if close-time quarantine removes the post-close snapshot, the pre-cleanup confirmed snapshot is retained with `BEFORE_CLEANUP` provenance. Neither snapshot certifies final whole-market inventory or free capital. Repeated terminal access is cached, POST is never retried, and teardown/cancellation drains terminal SDK work.

A typed `PlacementNotAttempted` with no POST start (and equivalent explicit no-POST/no-evidence unknown) now clears the armed capture as clean no-send. This prevents a false unresolved owner/terminal cleanup and preserves the contract's healthy/no-send compatibility.

## Behavior Before
Submission and initial-cycle failures retained categorical evidence, but did not uniformly retain a terminal no-new-risk result that connected failure to registered-only closure and unresolved-exposure reporting. Resolver failure could leave an armed owner while accepted IDs remained only gateway evidence. A known scoped close existed, but the terminal failure path did not own it through teardown.

## Behavior After
Failure automatically latches STOP before cleanup. Safe registered scope receives one existing bounded close; unregistered or observed IDs remain evidence only. Failed reads, uncertain cancellation, pending/failed/quarantined/unadmitted fills, and invalid ownership remain incomplete and blocked. Available confirmed fills are retained with explicit scope and capture phase. Clean pre-POST no-send and default denial do not gain cancellation side effects.

## Tests Added / Changed
New direct tests in `tests/test_rdp_placement.py`:
- `test_rdp_runtime_import_is_dns_and_socket_blocked`: subprocess blocks/counts socket connect, connect_ex, DNS resolution and create_connection before importing the runtime; observed count is zero. Integration tests also use the existing blocked-network fixture to block/count network attempts.
- `test_partial_submission_latches_stop_and_closes_only_registered_scope`: actual submission through fake SDK and gateway; one accepted leg is registered, closed and reconciled, while an observed-only ID stays unowned; confirmed fill, STOP/latch, guard denial, and no retry are asserted.
- `test_unknown_zero_id_post_failure_has_no_speculative_cancel`.
- `test_terminal_incomplete_cleanup_retains_preclose_confirmed_inventory`: confirmed inventory is retained with pre-cleanup provenance despite a failed order read/quarantine.
- `test_terminal_resolver_failure_preserves_acknowledgment_without_adoption` (before-owner-update and after-owner-update cases): preserves acknowledgment/observed evidence, invokes resolver once, and cleans only registered scope.
- `test_terminal_cleanup_drains_blocked_sdk_operation_before_release`: four real fake-SDK executor operations (`open_orders`, `cancel`, `get_order`, `get_trades`) crossed with caller cancellation and teardown (8 cases).

Five existing expectation groups were changed to reflect the approved terminal behavior:
1. Sign failure now expects the armed capture to be cleared, no terminal result, and no admission block; the typed pre-POST `PlacementNotAttempted` is authoritative evidence that no order POST occurred. POST failure remains unresolved and terminal.
2. Actual outcome-matrix PARTIAL case now expects the immutable submission's `resolved_attempt` snapshot to differ from the owner's current snapshot after terminal close advances ownership.
3. Resolver-exception assertions distinguish pre-update (same still-ARMED owner) from post-update: the submission retains its pre-cleanup acknowledgment evidence while the current owner advances; no second resolve is permitted.
4. Post-start gateway interruption/cancel-fill race PARTIAL case expects the submission result snapshot to differ from the owner after terminal cleanup; non-PARTIAL cases retain identity.
5. Post-start deadline outcome matrix applies the same PARTIAL snapshot distinction; all cases still assert a single POST and preserved actual gateway evidence.

Relevant existing coverage reused (not newly authored here) includes:
- Stale/malformed/failing order, open-order and trade reads; uncertain cancellation; pending/MATCHED, FAILED, unresolved/quarantined, contradictory and unadmitted fills: `tests/test_rdp_lifecycle.py` (including `test_uncertain_cancel_never_completes_even_if_recovered_truth_is_terminal`, `test_malformed_and_failed_order_trade_reads_fail_closed`, `test_malformed_trade_payload_and_unresolved_quarantine_block`, `test_unadmitted_fill_record_blocks_closure`) and placement lifecycle cases for matched-only, contradictory and quarantined fills.
- Confirmed fills during cancel and REST recovery/deduplication: lifecycle cancel/fill race tests and placement confirmed-fill cases.
- Foreign orders, wrong condition/token, registry mismatch and invalid scope rejected before cancellation/read: lifecycle scope-validation tests and placement `test_coordinator_rejects_attempt_and_registry_mismatch_before_gateway_reads`, `test_foreign_market_order_is_never_a_close_cancellation_target`, `test_observed_only_market_order_is_never_adopted_or_canceled`.
- Initial-cycle unreliable closure/cache/no duplicate cancel; existing cycle and close-barrier test cases.
- Healthy, empty/default-denial, inactive/read-only and CLI compatibility; existing placement/runtime tests and FL regressions.

## Tests Executed
- `PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_placement.py -k 'terminal_incomplete_cleanup_retains_preclose_confirmed_inventory'` — 1 passed, 363 deselected.
- `PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_placement.py -k 'partial_submission_latches_stop or unknown_zero_id_post_failure or terminal_resolver_failure or terminal_cleanup_drains_blocked_sdk_operation_before_release or initial_cycle_failed_sleeper_closes_once_with_sanitized_cached_result or initial_cycle_confirmation_with_prefill_read_failure_closes_unsafe'` — 14 passed, 349 deselected (before final matrix expansion).
- Expanded SDK cleanup matrix: `PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_placement.py -k 'terminal_cleanup_drains_blocked_sdk_operation_before_release'` — 8 passed, 355 deselected.
- `PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_placement.py` — 364 passed in 6.48s.
- `PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py tests/test_rdp_capital.py tests/test_rdp_accounting.py tests/test_gateway_positions_v2.py` — 222 passed in 62.36s.
- `PYTHONPATH=src .venv/bin/pytest -q tests/test_favorite_live.py tests/test_favorite_first.py` — 79 passed in 0.62s.
- `PYTHONPATH=src .venv/bin/pytest -q` — 1639 passed, 2 skipped in 81.44s.
- `.venv/bin/python -m compileall -q src/polymaker/rdp_runtime.py tests/test_rdp_placement.py` — passed.
- `git diff --check` — passed.
- One initial compile invocation used unavailable system `python` and returned `/bin/bash: python: command not found`; the repository virtualenv compile command above then passed.

## Test Results
All final targeted, affected, FL, and full-suite checks passed. The full suite includes the final added inventory-preservation test and expanded SDK cancellation/teardown matrix.

## Regression Results
RDP runtime, lifecycle, capital, accounting and gateway integration passed (222 tests); FL favorite live/first regressions passed (79 tests). Full suite passed with 1639 passed and 2 skipped.

## Git Diff Summary
- Runtime: 384 insertions / 22 deletions.
- Placement tests: 357 insertions / 0 deletions.
- Total tracked diff: 719 insertions / 22 deletions across the two authorized files.
- `git diff --check`: clean.
- `git status --short --branch`: `rdp-phase0-dev`; only the two authorized files modified, plus pre-existing untracked `research_outputs/` preserved.
- `git ls-files --others --exclude-standard` confirms the untracked tree is `research_outputs/`; it was not edited or removed.
- HEAD remains `50df3242113bc7719322bae9b47a5d4b7cf80c1a`; no commit/push/history operation performed.

## Safety Checks
- Runtime has no new external interface or network path. Import-time DNS/socket blocking and fake-client network-block/count assertions passed.
- No real-money order, cancellation, or live call was made.
- STOP reason remains the permitted `STATE_UNRELIABLE`; no new strategy state/reason, replacement, POST retry, unregistered ownership adoption, capital certification, or next-mode eligibility was introduced.
- Cleanup is limited to matching registered ownership and uses existing bounded barrier. Unknown exposure remains explicit.

## Known Limitations
- Retained unregistered acknowledgments and observed IDs are not adopted/recovered/canceled; they remain unresolved evidence requiring human attention.
- Confirmed inventory is only the available confirmed snapshot within explicitly listed registered order IDs. It is not a certified final whole-market inventory or final PnL.
- Successful later NORMAL/REBALANCE operation, market-wide termination, CLI activation, and remaining DGs are outside DG-006.

## Deferred Findings
No unrelated findings were changed. DG-002–DG-005 and DG-007–DG-012 remain open. Unregistered acknowledgment recovery remains explicitly deferred per the Architect Contract.

## Reviewer Attention
Please verify:
- The latch is set before any awaited close and all new-risk entry/permission paths honor it.
- Cleanup is registered-only after attempt/economics validation; acknowledgment and observed IDs remain separate, and resolver is never called a second time.
- Incomplete cleanup preserves confirmed inventory only as registered-order scoped evidence with the correct pre/post-cleanup capture phase.
- Cancellation/teardown drains each of the four fake SDK operations and repeated access performs no second close/POST.
- The five expectation groups above preserve healthy/default/no-send compatibility while reflecting immutable pre-cleanup submission evidence and explicit no-POST proof.
- Existing reused failure-matrix cases plus new direct integration tests cover malformed/stale reads, uncertain cancel, pending/FAILED/quarantined/unadmitted fills, registry contradiction, foreign orders, complete acknowledgment with unreliable continuation, cycle close reuse, guards, and cleanup interruption.

## Human Live Trading Performed
NO
