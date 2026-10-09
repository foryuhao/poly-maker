# ITER-0026 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 0. MUST_FIX = 1. Original B1, B2 and M1 are independently verified CLOSED. One further required DG-006 failure connection remains missing: an initial no-POST result with actual unresolved placement-capture evidence does not latch STOP or produce terminal output. DG-006 remains OPEN.

Repair remains within the frozen Architect01 contract. No redesign, strategy revision, new external interface or human decision is needed.

## Reviewed Inputs

- Active `AGENTS.md`, Frozen `RDP_PHASE0_FROZEN.md`, baseline, iteration index and DG-006 delivery requirements.
- Frozen `ITER-0026/01_ARCHITECT.md`.
- Immutable original `05_WORKER.md` and `06_REVIEWER.md` and archived Reviewer evidence.
- Verbatim `07_WORKER_FIX_01.md`.
- Actual repair diff, current runtime and placement tests, relevant existing gateway, ownership, close barrier, confirmed-fill processor, accounting and capital implementation.

This is independent actual-diff re-review, not acceptance of Worker-reported test results.

## Repository / Diff Reviewed

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD/base: `359f343dc265b33361adc0662ad477487e4aac5c`; archived implementation source at `746c042` supplies the unchanged repair base.
- Inspected local remote ref: `origin/rdp-phase0-dev` at `50df324` before repair archival. No remote archive/PASS claim is made by this review.
- Repair source diff: `src/polymaker/rdp_runtime.py`, 121 additions / 27 deletions.
- Repair test diff: `tests/test_rdp_placement.py`, 218 additions / 21 deletions.
- Runtime SHA256: `2a07531d73a33b96084521e1b0291e64e01909645c58468262085ff6ebeaac14`.
- Placement-test SHA256: `22c203dbb259e66458e8a5bda09544ca0f6e9842b31e0905807cf048ef4007ce`.
- Orchestrator index updates and untracked `07_WORKER_FIX_01.md` were inspected. Pre-existing untracked `research_outputs/` remains preserved.
- `git diff --check` passed. HEAD, branch, hashes and changed-file scope were rechecked after independent tests and remained unchanged.

Reviewer did not edit source, repository tests/documents, configuration, old audit artifacts or Git history. New Reviewer-only probes/report reside in `/tmp`. Original archived probes are immutable; the permitted B2 rejection adaptation is a new file.

## Acceptance Criteria Review

| Criterion | Re-review evidence / status |
|---|---|
| 1. Actual partial/unknown evidence automatically reaches retained STOP after SDK drain | Ordinary partial/unknown and interrupted COMPLETE paths pass. Resolved UNKNOWN capture hidden beneath NO_SEND/FAILURE_PRE_POST remains unconnected; M2. |
| 2. Registered-only cleanup, no observed/unregistered/foreign cancellation | Verified, including zero/one successful registration and five independently tested registry/economics contradictions. |
| 3. Resolver errors preserve evidence without another resolve/adoption | Original probes and existing cases pass; per-ID reporting now reflects actual matching ownership. |
| 4. Unknown zero-ID no speculative cancellation | Verified in existing/new tests and archived independent matrix. |
| 5. Confirmed partial fills and cancel/fill races accounted once | Relevant existing/added tests and full suite pass; confirmed accounting components unchanged. |
| 6. Uncertain cancel, failed/malformed reads and adverse capture/fill evidence remain honestly incomplete | Actual-submission matrix passes for ordinary terminal paths. Unresolved pre-POST capture has no terminal output; M2. |
| 7. Unsafe cycle reuses its existing cleanup without duplicate work | Three independent cycle failure/cache probes and existing cases pass. |
| 8. Subsequent APIs/guards deny new risk and never retry POST | Independent post-STOP public-path test passes; B2 closed. M2's owner already blocks admission, but lacks the required terminal latch/result. |
| 9. Scope/cause/registration/acknowledgment/observed/completeness/inventory retained | Ordinary paths and M1 repairs pass. M2 omits terminal evidence for actual unresolved capture. |
| 10. Cancellation/teardown drain actual SDK cleanup before owned resource cleanup | Existing eight operation/interruption cases pass; four independent SDK-operation cases additionally verify repeated teardown cancellation waits before user/gateway cleanup. |
| 11. Cached evidence/idempotence and conflict rejection | Public post-STOP close/FillWindow deny before additional SDK work; unsafe-cycle reuse and in-flight certification guard pass. |
| 12. Healthy, genuinely clean no-send/empty, default denial and inactive behavior | Standard suites pass. M2 repair must preserve the distinction between genuinely clean no-send/empty and no-POST with unresolved capture. |
| 13. Required implementation checks | Independent placement369 and full1644/2 pass; positive independent probes30 pass; two new required-behavior probes fail. |

Full DG-006 closure is not established. This is not a partial-prerequisite PASS: the frozen iteration targets complete failure handling, and the remaining connection is a normal implementation defect.

## Existing Codebase Compatibility

The repair continues to reuse existing `ExecutionGateway`, `RDPPlacementOwner`, processor-owned records, strict close adapter, scoped `close_rdp_cycle`, confirmed-fill accounting and retained task/drain helpers. No duplicate gateway, execution path, ledger or state machine was introduced. No stable subsystem was unnecessarily replaced.

RDP-local per-ID scope validation permits an actually matching registered subset through the existing barrier. Ordinary public close still validates its full scope. Unregistered or mismatching IDs remain separate and uncanceled; the barrier may remain incomplete because other selected-market orders remain. This preserves fail-closed behavior without inventing ownership.

Shared core, FL/IM strategy semantics, CLI, configuration, dependencies, accounting and capital formulas remain untouched. The independent full suite covers predecessor compatibility.

## Findings

### BLOCKER

None.

Original **B1 CLOSED**: COMPLETE after actual post-start interruption now latches STOP, retains immutable COMPLETE submission/gateway evidence and closes matching registered scope. Caller cancellation and teardown probes pass; repository deadline and waiter-failure cases also pass.

Original **B2 CLOSED**: external public close and FillWindow reject after STOP; independent all-public-path probe detects no additional SDK work or owner release. A close already in progress observes the latch and returns neither certified capital nor next mode, keeping replacement blocked.

### MUST_FIX

**M2 — NO_SEND/FAILURE_PRE_POST wrapper bypasses terminal handling when the actual resolved capture is UNKNOWN/unresolved.**

Locations: `src/polymaker/rdp_runtime.py:1557`, `:1576`, `:1597`, `:1607` (`_submission_requires_terminal_stop`).

The repaired predicate correctly handles interrupted COMPLETE, but still classifies terminal failure solely using selected outer submission outcomes or post-start interruption. The authoritative retained `resolved_attempt` can be UNKNOWN and unresolved even when no POST began: the existing placement owner detected actual anomalous capture evidence. That case is different from clean no-send or clean authoritative empty.

Independent actual-gateway/executor reproducer:

1. Warm the existing real admission fixture and begin an authorized initial submission.
2. During the fake SDK `create_order` callback, feed an actual selected-market `MATCHED` trade through the existing processor while capture is armed. Use an unmatched maker-order ID, with valid token/side/economics and receipt evidence; do not register or adopt it.
3. In the guard-denial case, return the fake signature normally. The real second Gateway guard rejects `PREACK_CAPTURE_NOT_CLEAN`; POST is never called.
4. In the signing-failure case, raise a sanitized synthetic signing failure after feeding the same event. The real gateway retains typed `PlacementNotAttempted`; POST is never called.
5. Existing owner resolution retains `outcome=UNKNOWN`, `unresolved=True`, `replacement_blocked=True`, `reason=PENDING_CAPTURE_ANOMALY`, and `pending_anomalies=('UNMATCHED_CANDIDATE_TRADE:unattributed-before-post',)`.
6. The outer submission result is respectively `NO_SEND / PREACK_CAPTURE_NOT_CLEAN` or `FAILURE_PRE_POST / PLACEMENT_NOT_ATTEMPTED`. The terminal predicate returns false. `terminal_failure_result` remains `None` and STOP is not latched.

Both final independent probes fail at the missing terminal result. Network attempts are empty. No POST or speculative cancellation occurred. The existing owner correctly preserves blocking and unresolved evidence; this finding does not claim the no-POST attempt created an exchange order or that admission was released. The defect is the missing required automatic unresolved-evidence → retained actionable STOP connection.

Contract basis: Desired Behavior (partial/unknown/unresolved initial outcome); Minimal Scope 2, 4–6, 9, 16; acceptance 1, 6, 9; required pending/unadmitted capture evidence and no-send compatibility matrix. A no-POST proof describes this submission's send behavior; it does not make existing anomalous capture evidence reliable.

Required repair: consume the actual matching retained owner/result's unresolved placement/fill evidence in terminal failure selection, even when the wrapper reports no-send or pre-POST failure. Preserve the original immutable submission/gateway classification, retain actual categorical cause and unresolved capture evidence, latch STOP before any cleanup, and perform no cancellation without safely registered attribution. Do not clear/drop pending evidence to manufacture a clean result.

Add durable actual fake-SDK regressions for both second-guard denial and typed pre-POST signing failure with anomalous captured trade evidence, asserting one retained STOP, unresolved exposure/human attention, zero POST/retry/speculative cancellation and no second resolve. Also explicitly keep genuinely clean guard denial, clean sign failure, clean authoritative empty and default-denied behavior free of terminal cleanup side effects.

This requires no architecture/strategy change and must not be deferred.

### NOTE

Original **M1 CLOSED**: terminal registration is computed from actual matching processor-owned records per ID; acknowledgments without matching ownership remain explicit. First-registration failure reports zero registered IDs and both acknowledgments unregistered. One-registration failure cleans only the matching subset and leaves the unregistered venue leg untouched and unresolved.

The initial Worker diff-count correction was recorded additively in Fix01; the original artifact remains immutable.

No other unrelated finding is proposed or implemented.

## Frozen SOP Compliance

STOP still uses permitted `STATE_UNRELIABLE` and denies new risk. No additional trading state, strategy timer, lack-of-fill stop condition, REBALANCE policy, quote rule, accounting meaning or capital formula was introduced. Dynamic tick/minimum-size behavior remains inherited and covered by passing tests.

M2 concerns the already approved fail-closed connection under Frozen §§13, 18, 21, 26–27. No Frozen SOP revision is requested.

## External Interface Review

No runtime interface/dependency change appears in the diff. Existing official Polymarket gateway/API paths are reused. No blockchain RPC, third-party runtime service or new network path was added.

Every independent run used the archived before-import DNS/socket blocking/counting runner. All report `REVIEW_NETWORK_ATTEMPTS []`. SDK clients are fake; no live order, cancellation or real-money action occurred.

## Trading Safety Review

Original risk-closure and certification defects are repaired. Valid registered subsets alone enter cancellation/reconciliation; observed, unregistered, foreign and economically contradictory IDs remain untouched. STOP is checked at public entry and before recording close evidence. Retained SDK work drains before user/gateway cleanup even under repeated teardown cancellation.

M2 remains fail-closed at the placement-owner admission boundary, but omits the required terminal latch and actionable failure result. Full delivery requires both blocking and honest termination evidence. No safety relaxation or heuristic adoption is authorized for its repair.

## Inventory / Capital Accounting Review

Confirmed-fill processor/deduplication and accounting definitions remain unchanged. Existing preack, partial-fill, cancel/fill-race and REST-recovery tests pass. Terminal snapshots remain scoped to matching registered orders, with explicit clean-subset or pre-cleanup provenance when applicable; unavailable snapshots are not represented as flat whole-market inventory.

Independent invalid-condition/token/side/price/size probes exclude the contradictory order from registered cleanup/inventory scope and retain its acknowledgment as unresolved evidence. In-flight and subsequent post-STOP actions do not certify capital, select a next mode or release replacement.

M2 repair must preserve pending capture evidence and report uncertainty; it must not count unadmitted/MATCHED evidence as confirmed economic inventory.

## State Machine Review

The repaired terminal latch remains an irreversible no-new-risk control for the implemented current-market runtime. Healthy NORMAL/REBALANCE close classification remains unchanged. Existing unsafe-cycle/FillWindow close evidence is retained rather than automatically repeated; cached accesses are stable.

The remaining missing transition is actual UNKNOWN/unresolved captured state beneath an outer no-send/pre-POST result. It must terminate with existing permitted STOP semantics, not introduce a new strategy state or stop reason.

## Test Review

Independent checks against the exact repaired hashes:

1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py` — **369 passed in 6.47s**, network attempts empty.
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q` — **1644 passed, 2 skipped in 81.31s**, network attempts empty; affected RDP and FL/legacy tests included without exclusion.
3. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q /tmp/test_iter0026_rereview_probes01.py docs/development/ITER-0026/review_evidence/test_iter0026_review_matrix.py` — **19 passed in 0.64s**, network attempts empty. Four original-defect probes (new copy accepts contract-permitted B2 rejection), twelve actual partial-failure cases and three unsafe-cycle/cache cases.
4. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q /tmp/test_iter0026_rereview_edges01.py` — **11 passed in 0.57s**, network attempts empty. Five invalid registry/economics cases, all post-STOP public entry/guard paths, in-flight close certification suppression, four SDK-operation teardown-order cases with repeated cancellation.
5. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q /tmp/test_iter0026_rereview_nosend01.py` — **2 failed in 0.53s**, network attempts empty:
   - `test_no_post_with_unresolved_capture_still_has_terminal_result[guard-denial]`.
   - `test_no_post_with_unresolved_capture_still_has_terminal_result[sign-failure]`.
   Both preserve authoritative UNKNOWN/unresolved owner evidence but return `terminal_failure_result=None`; M2.
6. `git diff --check` — passed.

Final M2 log: `/tmp/ITER-0026-rereview-nosend01.log`. All new probes are separate `/tmp` files; original archived evidence was not changed. Preserve the new reproducer/log alongside this immutable re-review for Worker repair. The temporary harness outside project testpaths emits an existing dependency deprecation warning. Standard-suite skips remain existing skips, not an acceptance waiver.

Repository regression success does not satisfy M2's unmet acceptance condition.

## Scope Review

Fix01 stayed within the approved RDP-local runtime/tests and addressed the original three findings without strategy redesign, shared-core change, CLI activation, live action, main merge or Git history mutation. Existing no-POST normalization remains narrow; the missing follow-through for anomalous capture evidence is now precisely identified.

M2 belongs to the same frozen DG-006 contract. Route to Worker, preserve reports, and re-review the next actual repair diff independently.

## Residual Risks

Unregistered acknowledgments/observed orders and unprovable state intentionally require human attention and cannot be heuristically adopted. Offline fake-client tests do not prove live correctness. Other open DG requirements, subsequent NORMAL/REBALANCE operation, market-wide termination, resolution accounting and CLI composition remain outside this iteration.

DG-006 remains OPEN. ITER-0026 is not CODE_READY. MICRO_LIVE_READY remains NO. Real-money and main-merge authorization remain NOT_GRANTED.

## Final Decision

RETURN_TO_WORKER.

Original B1, B2 and M1 are closed. Repair only M2's missing authoritative unresolved-capture → terminal STOP connection under Architect01; preserve truly clean no-send/empty compatibility and original immutable evidence. Add the two actual SDK regressions, run appropriate checks and obtain independent re-review before closing DG-006.
