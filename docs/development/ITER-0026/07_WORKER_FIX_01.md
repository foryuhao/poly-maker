# ITER-0026 — Worker Fix 01

## Status
IMPLEMENTED

## Architect Contract
Repaired the three findings in the frozen ITER-0026 Architect Contract and verbatim docs/development/ITER-0026/06_REVIEWER.md:
- B1: complete acknowledgments with an actual post-start interruption now reach retained terminal STOP and registered-only closure after gateway SDK work drains.
- B2: public close and FillWindow calls reject after terminal STOP; only retained internal owner tasks may complete authorized cleanup. An in-flight close also cannot certify a next mode or release replacement after STOP.
- M1: terminal registered scope is derived per ID from actual matching processor ownership and attempt economics. Accepted but unregistered IDs remain explicit; any valid registered subset alone may enter the existing close barrier.

No strategy, architecture, runtime-interface, scope or external dependency change was made. DG-006 remains OPEN pending independent re-review.

## Base Repository State
- Repository: /home/ubuntu/poly-maker; branch rdp-phase0-dev.
- HEAD at repair start: 359f343dc265b33361adc0662ad477487e4aac5c (Reviewer evidence archival). Initial implementation source commit: 746c042; its source/test tree is the repair base.
- Existing research_outputs/ was preserved. It remains untracked and unchanged.
- The original Worker and Reviewer artifacts were left immutable. Repair report is prepared at /tmp/ITER-0026-WORKER-FIX-01.md for Orchestrator archival as 07_WORKER_FIX_01.md.
- No commit, push, merge, rebase, reset or other Git history operation was performed.

## Existing Components Reused
- Existing retained gateway/submission evidence, RDPPlacementOwner, processor-owned order records, and registry/economics validation.
- Existing close_initial_normal_placement(), _StrictCloseGateway, and close_rdp_cycle() barrier, including its incomplete/conflicting-scope reporting.
- Existing terminal task ownership, cycle/fill-window delegation, ExecutionGateway executor, and shield/drain behavior.
- Existing fake SDK and placement fixtures. No gateway, processor, store, capital/accounting, CLI or configuration implementation was modified.

## Files Changed
- src/polymaker/rdp_runtime.py
- tests/test_rdp_placement.py

## Implementation Summary
The submission failure predicate now includes actual post_started plus retained interruption_reason evidence, even when the immutable result is COMPLETE. It retains that original result, uses the interruption category as terminal cause, latches STOP, and closes registered matching orders through the same existing barrier. Healthy complete results without interruption and clean pre-POST/no-send behavior remain unchanged.

Terminal scope now validates acknowledged IDs individually against actual processor records and the selected attempt's quote economics. The existing all-or-none registry validator remains in place for ordinary public close behavior. The terminal owner may pass only its computed valid subset to the existing close path, with the same per-ID registry snapshot rechecked after reconciliation. Accepted IDs lacking matching registration are reported as unregistered and never adopted or canceled. If a valid registered subset is closed while another accepted ID remains unregistered/live, the existing barrier reports that contradiction as incomplete; replacement remains blocked.

After the STOP latch, public close_initial_normal_placement() and complete_initial_fill_window() reject before starting reconciliation or producing new capital/next-mode evidence. Retained terminal/cycle/fill-window tasks may perform only their authorized nested cleanup. A close already in progress rechecks the latch before recording evidence and cannot release replacement or return a next mode. Its capital projection is retained only when uncertified; a certified projection is suppressed after STOP.

## Behavior Before
A complete accepted pair returned after caller cancellation, teardown or another post-start interruption retained COMPLETE plus interruption evidence but never latched terminal STOP or began registered cleanup. A later public close could repeat reconciliation, certify capital and release replacement. Terminal output also treated the owner's acknowledgment tuple as proof of registration, even when processor registration had failed.

## Behavior After
Interrupted complete acknowledgments latch STOP after the gateway operation drains and receive one terminal cleanup. A post-STOP public close or FillWindow re-entry raises TERMINAL_STOP_LATCHED without additional SDK calls, capital certification, next mode or owner release. Terminal registered/acknowledgment IDs match actual per-ID ownership evidence. A safe registered subset is the only subset submitted to cleanup; unregistered remaining orders stay untouched, visible and incomplete.

## Tests Added / Changed
Added durable regressions in tests/test_rdp_placement.py:
- test_complete_ack_interruption_latches_and_closes_registered_pair exercises caller cancellation and session teardown while the real ExecutionGateway executor is blocked in fake post_orders; it asserts retained COMPLETE evidence, STOP, one pair cancellation, no retry POST and drain completion.
- test_terminal_registration_evidence_matches_actual_registry_per_id injects failure before any registration and after exactly one successful registration. It compares terminal IDs with actual processor.owned_record() evidence, asserts only the matching registered subset is canceled, and verifies remaining unregistered venue orders stay live and unresolved.
- test_terminal_stop_rejects_public_close_and_fill_window_reentry reproduces full-pair post-registration resolver failure, then asserts both public operations reject before additional REST/cancel calls and terminal/owner replacement state remains blocked.

Updated existing post-start complete/interruption/deadline/waiter tests to expect terminal cleanup and immutable submission snapshots after owner close. Their prior fake FakeBatchClient lacked the SDK read/cancel methods needed by the newly required cleanup path; those controlled POST tests now use the existing close-capable fake SDK through the actual gateway executor. Adjusted the late ambiguous result assertion to preserve its actual UNKNOWN owner evidence. No strategy expectation changed.

## Tests Executed
- New terminal repair slice: PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_placement.py -k 'complete_ack_interruption_latches or terminal_registration_evidence_matches or terminal_stop_rejects_public' — 5 passed, 364 deselected.
- Full placement suite: PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_placement.py — 369 passed in 7.35s.
- Affected RDP integration group: PYTHONPATH=src .venv/bin/pytest -q tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py tests/test_rdp_capital.py tests/test_rdp_accounting.py tests/test_gateway_positions_v2.py — 222 passed in 62.51s.
- FL regression group: PYTHONPATH=src .venv/bin/pytest -q tests/test_favorite_live.py tests/test_favorite_first.py — 79 passed in 0.62s.
- Full suite: PYTHONPATH=src .venv/bin/pytest -q — 1644 passed, 2 skipped in 82.19s.
- Archived independent failure matrix, run with the Reviewer before-import DNS/socket blocker: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q docs/development/ITER-0026/review_evidence/test_iter0026_review_matrix.py — 15 passed in 0.61s; REVIEW_NETWORK_ATTEMPTS [].
- Repair-adjusted copy of the Reviewer probe harness (B2 accepts the contract-permitted TERMINAL_STOP_LATCHED rejection), run with the same blocker: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q /tmp/test_iter0026_review_probes_fix01.py — 4 passed in 0.52s; REVIEW_NETWORK_ATTEMPTS []. The harness emitted one existing websockets.legacy deprecation warning.
- .venv/bin/python -m compileall -q src/polymaker/rdp_runtime.py tests/test_rdp_placement.py — passed.
- git diff --check — passed.

## Test Results
All final repository and independent matrix/probe checks pass. Fake SDK integration covers complete-ack caller cancellation and teardown, no/partial registration, registered-subset cleanup, post-STOP close and FillWindow rejection, and no retry. No DNS/socket attempts were observed by the independent runner.

## Regression Results
Placement tests pass (369); affected RDP lifecycle/runtime/capital/accounting/gateway tests pass (222); FL live/first tests pass (79); full suite passes (1644 passed, 2 skipped). Reviewer failure matrix passes 15/15, and repair-adjusted probes pass 4/4.

## Git Diff Summary
- Repair diff against the archived implementation base: runtime 121 additions / 27 deletions; placement tests 218 additions / 21 deletions; total 339 additions / 48 deletions.
- git diff --check is clean.
- HEAD remains 359f343dc265b33361adc0662ad477487e4aac5c on rdp-phase0-dev.
- Working tree contains only the two authorized tracked modifications plus the pre-existing untracked research_outputs/ (748 untracked files); it was not edited or removed.
- Correction to the immutable initial Worker report's diff-count NOTE: the initial implementation diff was 719 additions / 22 deletions, split runtime 367 / 17 and tests 352 / 5. The original report recorded 384 / 22 and 357 / 0; this repair report corrects those figures without altering the original artifact.

## Safety Checks
- No live network, real-money order or cancellation was used. All trading/client behavior was exercised with fake SDK clients; the independent runner blocked and counted DNS/socket calls before imports.
- Existing registered-only close barrier remains authoritative; only matching registered IDs enter the terminal subset. Observed and accepted-but-unregistered IDs are not adopted or canceled.
- Terminal STOP remains STATE_UNRELIABLE. No retry POST, capital certification, next mode, replacement release, or new strategy behavior follows terminal STOP.
- research_outputs/ remains preserved. No main merge or Git history operation was performed.

## Known Limitations
- A live accepted order without successful registration cannot be safely attributed by this iteration. It remains an explicit unregistered acknowledgment and unresolved exposure requiring human attention.
- If one accepted pair leg is unregistered, the existing barrier may cancel/reconcile the registered leg but reports the still-live unregistered leg as a scope conflict; terminal cleanup stays incomplete and replacement blocked.
- Fake-client tests do not establish live execution correctness. Other DGs, later NORMAL/REBALANCE operation, market-wide termination and CLI activation remain outside scope.

## Deferred Findings
No unrelated finding was changed. Remaining open delivery gaps remain open. DG-006 is not marked closed by Worker; independent Reviewer must verify the full contract and determine its status.

## Reviewer Attention
Please verify:
- Complete acknowledgment plus caller cancellation, teardown, deadline and waiter failure latch only when actual post-start interruption evidence exists, retain the immutable COMPLETE source, and close registered scope once after gateway drain.
- Post-STOP public close and FillWindow calls reject before network/capital/replacement work; in-flight internal cleanup remains possible, and a concurrent close cannot certify/release after latch.
- Per-ID registered scope requires actual processor ownership matching attempt token/side/price/size/condition; accepted-but-unregistered IDs remain explicit. For partial registration, only the registered subset is sent to the existing barrier and the unregistered live leg stays untouched/incomplete.
- Ordinary healthy complete, clean no-send/default denial, existing unsafe cycle-close reuse, cache/idempotence, teardown draining and the full existing failure matrix still pass.
- The historical initial Worker diff count correction above is additive; 05_WORKER.md and 06_REVIEWER.md remain immutable.

## Human Live Trading Performed
NO
