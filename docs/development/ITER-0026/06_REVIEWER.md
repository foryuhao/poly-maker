# ITER-0026 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 2. MUST_FIX = 1. DG-006 remains OPEN. The reviewed implementation satisfies much of the ordinary partial/unknown cleanup path, but does not satisfy the frozen contract's complete-acknowledgment interruption, permanent terminal blocking, and truthful registration-evidence requirements.

These are implementation defects within Architect01. No strategy, architecture, scope, or external-interface decision is required for repair.

## Reviewed Inputs

- Active `AGENTS.md`, including serial roles, immutable artifacts, safety and delivery convergence requirements.
- `docs/strategy/RDP_PHASE0_FROZEN.md`, particularly §§13, 18, 21, 26–27, 34–35.
- `docs/development/BASELINE.md`, `ITERATION_INDEX.md`, and `DELIVERY_GAPS.md` (DG-006).
- Frozen `ITER-0026/01_ARCHITECT.md` (`APPROVED_FOR_IMPLEMENTATION`), `02_DISPATCH_BLOCKER.md`, and verbatim `05_WORKER.md`.
- Actual runtime/test diff and surrounding admission, submission guards, placement ownership, terminal cleanup, pre-fill evaluation, FillWindow, initial-cycle completion, close and teardown implementation.
- Existing gateway, placement owner, scoped lifecycle barrier, processor, accounting and capital components; relevant existing and added placement tests.

ITER-0025 is already archived and was treated as the implementation baseline. It was not reimplemented or redesigned.

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD/base: `50df3242113bc7719322bae9b47a5d4b7cf80c1a`, with `origin/rdp-phase0-dev` at the same commit on inspected local refs.
- Actual implementation diff: `src/polymaker/rdp_runtime.py` (367 added / 17 removed lines) and `tests/test_rdp_placement.py` (352 added / 5 removed lines).
- Runtime SHA256: `238b4ed82ec08f00d2cfbee953fcd468fc1c2c00270c1530d9fab97a78036214`.
- Placement-test SHA256: `e154ce5f6d323c347a1721f5f59dc7d21a83210ffab56a00886a1edeb56a1b50`.
- Additional tracked change: Orchestrator's `ITERATION_INDEX.md` stage/status update. Untracked `05_WORKER.md` was inspected. Pre-existing `research_outputs/` was identified and preserved, outside implementation scope.
- `git diff --check` passed. HEAD, branch, implementation hashes and intended scope were rechecked after independent tests and remained unchanged.
- No source, repository test, configuration, project document or Git history was modified by Reviewer. Reviewer-only harnesses/report were written under `/tmp`.

This verdict applies only to this exact tree and diff.

## Acceptance Criteria Review

| Architect criterion | Review result |
|---|---|
| 1. Actual partial/unknown submission automatically reaches retained STOP after SDK drain | Ordinary partial/unknown paths verified. Complete acknowledgment with interrupted continuation is omitted; B1. |
| 2. Registered accepted legs get scoped cleanup; observed/unregistered/foreign IDs do not | Cancellation boundary verified for ordinary valid scope and foreign orders. Terminal registration labels can falsely claim ownership; M1. |
| 3. Resolver failures before/after owner update preserve evidence without second resolve/adoption | Added and existing cases pass; one resolve and gateway evidence preservation verified. Failed registration evidence requires M1. |
| 4. Unknown zero-ID outcomes report uncertainty without speculative cancel | Verified in new tests and independent inspection. |
| 5. Cancel/fill races and confirmed partial fills remain accounted once | Existing reconciliation/deduplication tests pass; new partial integration preserves confirmed inventory. |
| 6. Uncertain cancel, failed/malformed reads and adverse fills remain incomplete | Verified by independent actual-submission matrix for cancel exception/noop, open/order/trade reads, malformed open rows, pending/FAILED/quarantined/unadmitted fills and registry contradiction. |
| 7. Existing unreliable-cycle closure becomes terminal without duplicate cleanup | Independently verified for failed sleeper, failed order read and failed public health; cached cycle uses identical retained cleanup and performs no second read/cancel. |
| 8. All subsequent new-risk APIs/guards reject; no retry POST | Admission, arm and actual submission permission latch checks exist. Permanent no-capital/no-next-mode/no-release invariant is bypassed through public close; B2. |
| 9. Retained scope/cause/cleanup/acknowledgments/observed/completeness/inventory | Ordinary valid-scope result verified; M1 misclassifies absent ownership as registration. Inventory scope/capture phase remain explicit. |
| 10. Cancellation/teardown drain actual cleanup SDK work | Eight added SDK-operation × cancellation/teardown cases pass. Complete-ack interruption drains POST but never starts required terminal closure; B1. |
| 11. Retained result idempotent; competing operations serialized | Cached terminal access and active-task guards verified. Post-terminal public close can repeat reconciliation and release ownership; B2. |
| 12. Healthy/default denial/no-send/inactive behavior compatible | Relevant existing tests and full suite pass. Typed pre-POST no-send conversion is limited to explicit no-POST/no-evidence proof. |
| 13. Targeted/affected/predecessor/practical full suite passes | Independent placement364 and full1639/2 pass. Four independent contract probes fail; acceptance is therefore not met. |

No full delivery acceptance condition is closed. This is not a justified partial prerequisite: the frozen iteration explicitly targets full DG-006 failure closure, and the missing conditions are repairable implementation defects. Leave DG-006 OPEN.

## Existing Codebase Compatibility

The Worker reuses the existing `ExecutionGateway`, strict close adapter, placement owner, confirmed-fill processor, `close_rdp_cycle`, inventory projection and shield/drain helpers. No duplicate execution gateway, alternate ledger, parallel strategy state machine or unnecessarily replaced subsystem was found.

Changes are RDP-local. Gateway, processor, capital/accounting formulas, CLI, configuration and dependencies remain untouched. FL/legacy compatibility receives coverage from the independently passing full suite. No IM dependency was introduced.

The terminal integration currently fails to constrain one existing public close path (B2); reuse alone does not establish safety.

## Findings

### BLOCKER

**B1 — Fully acknowledged submission interrupted during POST escapes terminal handling and leaves both registered orders live.**

Location: `src/polymaker/rdp_runtime.py:1597` and `:1605` (`_submission_requires_terminal_stop`); submission cancellation and teardown callers.

The predicate considers only `PARTIAL`, `UNKNOWN`, and `RESOLUTION_ERROR`. It ignores retained interruption/continuation evidence when the actual SDK returns a complete accepted pair. An independently controlled real gateway executor was held inside fake `post_orders`, then either the caller was canceled or session teardown was started before the fake SDK returned two valid acknowledgments.

Both probes retained `outcome=COMPLETE` and the true `CALLER_CANCELLED` or `SESSION_TEARDOWN` interruption. However, `terminal_failure_result` stayed `None`, STOP was not latched, no scoped cancellation ran, and both fake venue rows remained `LIVE`. In the teardown case, teardown finished after draining submission without closing those registered orders.

Contract basis: Desired Behavior and Minimal Scope 2, 6, 15; required complete-acknowledgment/interrupted-or-unreliable-continuation test; acceptance 1, 8, 10; Frozen §§13, 18, 21. Draining acknowledgment is necessary but is not terminal risk closure.

Required repair: consume actual retained interruption/unreliable-continuation evidence even with complete acknowledgments, latch before cleanup, preserve immutable COMPLETE gateway/submission evidence, and clean only registered matching scope once. Preserve genuinely healthy COMPLETE and clean no-send behavior. Add actual fake-SDK caller cancellation and teardown tests asserting terminal result, latch, one registered cleanup, zero retry POST and correct drain order.

**B2 — A subsequent public close after terminal STOP recertifies capital, exposes NORMAL and releases replacement.**

Location: `src/polymaker/rdp_runtime.py:3768`, `:3872`, `:3916`, `:3930`; related FillWindow terminal entry/delegation checks.

Reproducer: actual gateway accepts both legs; resolver registers them then raises. The runtime correctly retains `RESOLUTION_ERROR`, latches STOP and closes the registered pair with unsafe continuation (`next_mode=None`, replacement blocked). After that operation finishes, call `close_initial_normal_placement(session.placement_owner.current, deadline=loop.time()+2)` using its normal public defaults.

The second close repeats reconciliation, returns certified capital and `next_mode=NORMAL`, and sets the authoritative owner's `replacement_blocked=False` while `_terminal_stop_latched` remains true. The immutable terminal evidence still says replacement is blocked, so current owner and terminal evidence now disagree. The public entry and close-state certification logic do not consume the terminal latch.

The admission latch still prevents a new submission through the currently implemented initial API; the reproduced defect is the explicitly forbidden certification/next-mode/replacement release and duplicate terminal closure work, not a claim that another real order was sent.

Contract basis: Minimal Scope 8, 10, 13–14; invariants prohibiting capital certification, next-mode eligibility and replacement release after terminal failure; acceptance 8 and 11. The FillWindow check also only rejects the latch when `_initial_cycle_call_allowed()` is false, which becomes true with no active initial-cycle owner. Audit that companion route during repair.

Required repair: make latched terminal closure authoritative across all close/cycle/FillWindow entry and certification paths. Legitimate retained internal cleanup must remain possible; external subsequent actions must either reuse retained cleanup or reject without new cleanup/certification/release. Do not release replacement or certify a next action after STOP. Add a full-pair post-registration resolver-error probe followed by each relevant public operation; assert stable cached evidence, no additional barrier work, no capital certification/next mode and owner remains blocked.

### MUST_FIX

**M1 — Terminal result labels acknowledgment IDs as registered even when registration failed and no owned record exists.**

Location: `src/polymaker/rdp_runtime.py:1761`–`:1770` (`_retain_terminal_failure`).

The cleanup path validates registry/economics in `_terminal_scope`, but the terminal evidence builder independently copies `current.acknowledged_order_ids` into `registered_order_ids`. Those attempt IDs do not prove successful registration.

Independent actual-submission probe: return two valid acknowledgments and make the existing processor's first `register_order` call raise before creating ownership. The placement owner retains the two acknowledgment IDs and a partial outcome, with no owned records. Cleanup correctly refuses speculative cancellation with `TERMINAL_OWNERSHIP_SCOPE_UNPROVABLE`. The result nevertheless reports `registered_order_ids=('no-order','yes-order')` and `unregistered_acknowledgment_ids=()`. Actual registered ownership is empty.

Contract basis: Desired Behavior's separate registered scope and unregistered acknowledgment evidence; Minimal Scope 7, 9, 16; acceptance 2, 3, 9; ownership-boundary invariants. The terminal result must remain actionable and honest even when registration itself fails.

Required repair: derive reported registered scope from actual matching ownership, not the acknowledgment tuple, and retain every accepted-but-unregistered ID explicitly. Preserve contradiction/invalid-scope uncertainty without adopting those IDs or claiming them reconciled. Test first-registration failure and partial-registration failure (one owned record plus one unregistered acknowledgment), along with existing before/after-resolver cases.

### NOTE

- Independent existing suites pass, but the newly added tests do not assert the full failure matrix's terminal outputs. In particular, the existing complete-ack interruption test still accepts `COMPLETE` without asserting required terminal cleanup. Passing that inherited expectation cannot establish DG-006 closure.
- Worker diff-count prose is inaccurate: actual implementation totals are 719 added / 22 removed, split 367/17 runtime and 352/5 tests. The report's 384/22 and 357/0 per-file figures do not match Git. Preserve the immutable original report; record any correction in a new specialist artifact.
- A first Reviewer-only matrix trial used a bare quarantine flag with no contradictory fill record; existing reconciliation cleared that recoverable flag. The final matrix uses contradictory foreign-token REST fill evidence instead and passes. This harness correction is not classified as a production defect.

## Frozen SOP Compliance

The introduced terminal reason is the permitted `STATE_UNRELIABLE`. No additional trading state, strategy timer, stop condition based on lack of fill, hard REBALANCE duration, quote rule, reserve formula or accounting definition was introduced. Dynamic tick/minimum metadata behavior is preserved by reuse and the passing suite.

The target safety connection is consistent with Frozen §§13, 18, 21, 26–27, but B1 and B2 prevent the implementation from meeting that connection. No Frozen SOP change is requested.

## External Interface Review

No new external runtime interface, dependency or network path appears in the actual diff. Trading-side work continues through the existing official Polymarket gateway interfaces. No blockchain RPC or third-party runtime service was introduced.

Independent test runner blocks/counts `socket.connect`, `connect_ex`, DNS (`getaddrinfo`, `gethostbyname`, `gethostbyname_ex`) and `create_connection` before importing pytest or project modules. All final independent runs report `REVIEW_NETWORK_ATTEMPTS []`. All SDK clients are fake; no real-money order/cancel or live call occurred.

## Trading Safety Review

Positive evidence: failure latch precedes awaited ordinary terminal cleanup; valid attributed scope goes through the existing strict barrier with unsafe continuation; unknown zero-ID and unregistered/observed IDs do not trigger speculative cancellation; fake SDK operations are drained; foreign selected-market orders remain untouched; POST is not retried.

Safety failures: B1 leaves registered accepted risk without terminal cleanup after interruption; B2 permits forbidden post-STOP certification/replacement release. M1 makes unresolved ownership evidence misleading. None can be deferred to achieve delivery convergence.

## Inventory / Capital Accounting Review

Existing confirmed-fill processor/deduplication and inventory/accounting formulas remain unchanged. Partial integration records an accepted confirmed fill once, including REST recovery; existing preack and cancel/fill-race cases pass. A failed close-time snapshot can preserve the pre-cleanup confirmed snapshot with explicit `BEFORE_CLEANUP` phase and scoped order IDs. `None` or an explicitly clean registered subset is not presented as whole-market flat inventory or final PnL.

Already-unreliable orders are excluded by the existing inventory projection. No additional finding is asserted merely because a certified snapshot is unavailable for that scope; uncertainty is retained, and Reviewer did not invent an alternate accounting ledger.

Ordinary terminal cleanup does not certify capital or select a next mode. B2 violates this invariant on a subsequent public close. M1 requires reported inventory/cleanup scope and ownership labels to remain consistent with real registration.

## State Machine Review

The implementation adds a retained terminal STOP latch/result, not a third trading state. Normal healthy invalidation/window completion continues to use existing NORMAL/REBALANCE delta classification. Unsafe initial-cycle results reuse existing close evidence, avoiding automatic duplicate cancellation in the verified paths.

B1 omits STOP for complete acknowledgment with interrupted submission continuation. B2 allows post-STOP close output and current ownership to revert to next-action eligibility. Audit all public close/FillWindow paths during the authorized repair; do not add new strategy semantics.

## Test Review

Independent commands used the same before-import blocking runner, `/tmp/iter0026_review_pytest.py`, with bytecode writes disabled and pytest cache provider disabled:

1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python /tmp/iter0026_review_pytest.py -q tests/test_rdp_placement.py` — **364 passed in 6.46s**, network attempts empty.
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python /tmp/iter0026_review_pytest.py -q` — **1639 passed, 2 skipped in 81.26s**, network attempts empty. This includes affected RDP and FL/legacy regressions without exclusion.
3. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python /tmp/iter0026_review_pytest.py -q /tmp/test_iter0026_review_matrix.py` — **15 passed in 0.58s**, network attempts empty. Twelve actual partial-submission faults: cancel exception/noop; failing open/order/trade reads; malformed open row; pending, FAILED, quarantined and unadmitted fills; registry contradiction; foreign selected-market order. Three complete-cycle faults: sleeper, order read, public health. Tests assert honest incomplete output, no retry POST/foreign cancel and cached unsafe-cycle close reuse.
4. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python /tmp/iter0026_review_pytest.py -q /tmp/test_iter0026_review_probes.py` — **4 failed in 0.57s**, network attempts empty, failures reproduced against the exact reviewed implementation:
   - `test_complete_ack_interruption_requires_terminal[caller-cancel]` — expected terminal result, got `None`; B1.
   - `test_complete_ack_interruption_requires_terminal[teardown]` — expected terminal result, got `None`; B1.
   - `test_terminal_registration_labels_match_actual_registry[failed-registration]` — reported two registered IDs, actual matching owned-record set empty; M1.
   - `test_terminal_cannot_reclose_to_certify_or_release` — expected `next_mode=None`, got `RDPMode.NORMAL` with certified capital and replacement released; B2.
5. `git diff --check` — passed.

Final probe log: `/tmp/ITER-0026-review-probes.log`. Final matrix log: `/tmp/ITER-0026-review-matrix.log`. Reviewer harnesses use existing placement fixtures, actual gateway/executor paths and fake SDKs; they are review evidence, not repository implementation edits. Preserve them with this report if needed for durable repair reproduction.

The standard suite's two skipped cases remain existing skips, not an acceptance waiver. The temporary harness outside project testpaths emits a dependency deprecation warning; no runtime failure is concealed. A preliminary bad-economics test attempted to mutate the processor's `_owned` set as a mapping and was discarded as invalid harness setup; the final four failing probes above are valid and deterministic.

## Scope Review

Authorized file/scope boundaries are respected. No unrelated refactor, gateway/processor change, shared-core change, CLI activation, strategy redesign, live trading or main merge occurred. The explicit typed no-POST normalization is narrowly connected to the contract's clean no-send compatibility.

All three required repairs remain within DG-006/Architect01. Return to Worker rather than Architect. Preserve this report and the existing Worker report; record fixes and re-review as new numbered immutable artifacts.

## Residual Risks

Unregistered acknowledgments/observed IDs intentionally remain unresolved and require human attention; no heuristic adoption/recovery is authorized. Offline fake-client evidence does not certify live execution. Successive NORMAL/REBALANCE operation, full market termination, official resolution, complete metrics and CLI composition remain other open DG requirements.

This tree is not CODE_READY for ITER-0026. DG-006 remains OPEN. MICRO_LIVE_READY remains NO. Real-money and main-merge authorization remain NOT_GRANTED.

## Final Decision

RETURN_TO_WORKER.

Fix B1, B2 and M1 under the frozen contract; add genuine integration coverage for complete-ack interruption, post-terminal close/FillWindow certification denial and failed/partial registration evidence. Repeat appropriate targeted/affected/regression checks and obtain independent actual-diff re-review. Do not close DG-006 or claim PASS from the currently passing standard suite.
