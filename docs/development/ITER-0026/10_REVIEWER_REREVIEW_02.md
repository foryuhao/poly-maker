# ITER-0026 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 0. MUST_FIX = 1. Original B1, B2 and M1 remain CLOSED. Fix02 repairs both previously reproduced M2 no-POST cases, but M2 remains OPEN for the same authoritative unresolved-owner evidence masked by an outer `EMPTY` result. DG-006 remains OPEN.

This is a normal implementation repair within frozen Architect01, not a request to redesign or revise strategy. No new human approval is required.

## Reviewed Inputs

- Active `AGENTS.md`, Frozen SOP, baseline, iteration index and DG-006 delivery requirements.
- Frozen `ITER-0026/01_ARCHITECT.md`.
- Original Worker/Reviewer artifacts and immutable `07_WORKER_FIX_01.md`, `08_REVIEWER_REREVIEW_01.md`.
- Verbatim `09_WORKER_FIX_02.md`.
- Actual Fix02 runtime/test diff and current surrounding submission, ownership, terminal, cycle/FillWindow, scoped close, confirmed-fill accounting and teardown implementation.
- Archived original and first re-review independent probes/matrix, new regression tests and a new actual-gateway EMPTY/capture probe.

## Repository / Diff Reviewed

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD/base: `c756194c26185aa705bc7d2646f4e62479e198f2`.
- Inspected local remote ref: `origin/rdp-phase0-dev` at `50df324`. No final remote archive claim is made by Reviewer.
- Actual Fix02 diff: runtime 32 added / 4 removed lines; placement tests 113 added / 0 removed lines. Additional tracked change is Orchestrator's index update.
- Runtime SHA256: `a0ecdc09eb15f9d466ec0c03cce9dadde40eb545597eb82202ae7eaaa070c1bf`.
- Placement-test SHA256: `5c169f693357d4eb896f31d25f9c6da207124127c90f7798688e2f94952a3ba7`.
- Untracked `09_WORKER_FIX_02.md` was inspected. Pre-existing `research_outputs/` remains preserved.
- `git diff --check` passed. Implementation hashes/scope were rechecked unchanged after probes.

Reviewer did not modify source, repository tests/documents, historical audit evidence, configuration or Git history. New probe/report evidence was written only under `/tmp`. This verdict applies to this exact tree.

## Acceptance Criteria Review

| Criterion | Result |
|---|---|
| 1. Partial/unknown evidence automatically reaches STOP after SDK drain | Ordinary partial/unknown and interrupted COMPLETE paths pass. M2 no-send/sign-failure probes now pass; outer EMPTY with actual resolved UNKNOWN remains omitted. |
| 2. Registered-only cleanup | Pass in prior and current independent matrix, including per-ID invalid economics and zero/partial registration. |
| 3. Resolver error preserves evidence without another resolve/adoption | Pass; original B1/B2/M1 regressions remain closed. |
| 4. Unknown zero-ID, no speculative cancellation | Pass in matrix and new no-POST cases. |
| 5. Confirmed fills/cancel races/deduplication | Relevant existing/added tests pass in exact-tree full suite; accounting components unchanged. |
| 6. Uncertain cancellation/reads/adverse fill/capture evidence gives honest incomplete terminal output | Ordinary paths pass; M2's EMPTY/capture branch has no terminal result. |
| 7. Unsafe cycle reuses close evidence | Current independent cached-cycle probes pass without additional cleanup. |
| 8. Post-STOP APIs/guards deny new risk, zero retry POST | Current public-path/in-flight certification probes pass. Missing M2 branch preserves owner blocking but does not latch terminal STOP. |
| 9. Scope, cause, completeness, uncertainty and available inventory retained | Existing terminal paths pass. M2's unresolved EMPTY branch lacks terminal evidence; cause helper also restricts owner reason to no-send/sign-failure labels. |
| 10. SDK cleanup drains before resource cleanup | Current independent teardown-order/repeated-cancellation probes and full existing matrix pass. |
| 11. Cached results/idempotence/conflict rejection | Current original and first-re-review probes pass. |
| 12. Healthy and genuinely clean no-send/empty/default-denied compatibility | Added clean EMPTY and existing clean guard/sign/default-denial cases pass. Unresolved capture must be distinguished from genuine clean EMPTY; M2. |
| 13. Tests | Independent full1647/2 and prior probes32 pass; new remaining-family probe fails. |

Full DG-006 acceptance is not closed. No partial-prerequisite PASS is appropriate: all remaining work is the same required automatic unreliable-state terminal connection.

## Existing Codebase Compatibility

Fix02 is a small RDP-local extension to the terminal predicate and categorical cause selection. It reuses actual retained submission/current owner objects, terminal task/latch/result and existing registered scoped cleanup. Gateway, processor, close barrier, capital/accounting formulas, CLI, configuration and dependencies remain unchanged.

No duplicate subsystem, alternative ledger, parallel execution path or unnecessary replacement was found. FL/IM strategy behavior is unchanged; the exact-tree full suite covers predecessor compatibility.

## Findings

### BLOCKER

None.

Original B1 and B2 remain independently CLOSED: complete acknowledgment plus actual interruption closes once after SDK drain; post-STOP public calls and in-flight close do not certify capital, select a next mode or release replacement.

### MUST_FIX

**M2 — Remaining EMPTY branch still masks actual authoritative UNKNOWN/unresolved capture.**

Locations: `src/polymaker/rdp_runtime.py:1576`, `:1618`, `:1641`.

Fix02 correctly examines the exact current matching owner for `NO_SEND` and `FAILURE_PRE_POST`. However, `_submission_requires_terminal_stop()` returns false for other wrapper labels before examining the same owner evidence. `_submission_terminal_cause()` also restricts the owner cause to those two labels.

Independent actual-gateway reproducer:

1. Warm actual admission and run the real `ExecutionGateway` executor with a fake SDK.
2. During fake `post_orders`, feed a valid selected-market BUY `MATCHED` trade through the existing armed processor capture, with an unattributed maker-order ID and explicit receipt evidence.
3. Return two explicit exchange rejections (`success=False` for each requested leg). The gateway correctly returns authoritative empty acknowledgment evidence for this attempt.
4. The placement owner independently resolves to `UNKNOWN`, `unresolved=True`, `replacement_blocked=True`, `reason=PENDING_CAPTURE_ANOMALY`, and `pending_anomalies=('UNMATCHED_CANDIDATE_TRADE:unmatched-during-post',)`.
5. The outer immutable submission reports `EMPTY`, `post_started=True`, `reason=AUTHORITATIVE_EMPTY_RESPONSE`, no interruption and gateway result type EMPTY. The wrapper filter skips terminal handling.
6. `terminal_failure_result` is `None` and STOP is not latched. The final probe fails on the absent terminal result; no real network attempt occurred.

This is the same M2 evidence-masking family, not a new strategy behavior. The old no-send and sign-failure examples are fixed; the EMPTY sibling remains. Explicit rejection proves the attempted legs were rejected, but does not erase independent anomalous capture evidence that the authoritative owner has already classified unresolved.

The owner remains blocked and pending evidence is preserved. No retry, adoption or speculative cancellation occurred. The finding is the missing actionable terminal connection, not a claim that this rejected attempt created a live order or released admission.

Contract basis: Desired Behavior and Minimal Scope 2, 4–6, 9, 16; acceptance 1, 6, 9, 12; required adverse capture evidence and clean-empty compatibility. Only genuinely clean authoritative empty is the compatibility exception; the reproduced owner state is UNKNOWN/unresolved.

Required repair: evaluate actual matching unresolved owner evidence independently of wrapper labels for authorized actual completed submissions. Do not add another isolated wrapper exception. Keep the provenance/selected-market/current-object checks, the original immutable wrapper/gateway result and existing interruption precedence. Retain the owner's actual categorical failure reason when it drives terminal failure regardless of outer label. Genuinely reliable clean EMPTY/no-send/sign-failure/default-denied cases must remain nonterminal.

Add the actual fake-SDK two-rejection-plus-unmatched-capture regression and assert retained STOP, owner cause and pending anomaly, zero adoption/speculative cancellation/retry, preserved immutable EMPTY gateway evidence, one resolve and stable cached access. Keep all previous M2 cases and clean compatibility tests. Do not erase capture anomalies to manufacture a clean result.

No architecture, Frozen SOP, external-interface or scope change is needed. M2 cannot be deferred while declaring DG-006 closed.

### NOTE

Original M1 remains CLOSED: reported registration derives from actual per-ID matching ownership, unregistered acknowledgments remain separate, and only valid registered subsets enter cleanup.

Fix02's two original M2 probes pass, preserving `NO_SEND` / `FAILURE_PRE_POST`, owner anomaly/cause, one resolve and zero speculative cancellation. The exact-current-object and selected-market matching checks are a sound implementation constraint and should be retained when closing the remaining sibling branch.

No unrelated finding was implemented or proposed.

## Frozen SOP Compliance

The repair retains permitted STOP reason `STATE_UNRELIABLE`, no-new-risk latch, existing NORMAL/REBALANCE behavior and confirmed-fill-only accounting. It introduces no strategy state, timer, quote rule, lack-of-fill stop condition, reserve change or runtime interface.

The remaining defect concerns the already frozen unreliable-state terminal safety connection. No strategy revision is requested.

## External Interface Review

No new runtime dependency, external API, RPC or network path appears in the actual diff. Existing official Polymarket paths are reused.

Every independent check used the archived runner that blocks/counts DNS/socket access before imports. All runs report `REVIEW_NETWORK_ATTEMPTS []`. All SDKs are fake; no live API call, real-money order or cancellation was performed.

## Trading Safety Review

Positive evidence remains: terminal latch precedes ordinary cleanup; matching registered IDs alone enter scoped close; unregistered/observed/foreign IDs are not adopted or canceled; post-STOP admission/guards/public execution calls remain denied; in-flight close cannot release capital/next mode/replacement; repeated cancellation does not detach actual SDK cleanup.

The remaining M2 branch retains fail-closed owner blocking but omits the explicit terminal latch/result required for deliverable failure handling. Remedy is existing terminal handling, not additional order authority or heuristic recovery.

## Inventory / Capital Accounting Review

No formulas or authority changed. Existing confirmed-fill/REST recovery/deduplication/cancel-race cases pass. Terminal inventory remains explicitly scoped, and pre-cleanup/subset/unavailable snapshots are not certified whole-market flat inventory or final PnL.

Current independent invalid-economics and post-STOP certification probes pass. The unmatched MATCHED evidence in M2 must remain unresolved capture evidence; it must not be adopted or counted as confirmed inventory.

## State Machine Review

Original B1/B2 closure remains intact. Healthy success and genuinely clean empty/no-send remain compatible. Ordinary unreliable cycles reuse their retained close result, and all checked post-STOP public actions preserve blocking/idempotence.

The remaining missing transition is actual selected-market UNKNOWN/unresolved owner evidence beneath an EMPTY wrapper. Use the existing STOP reason/result and retain cause/provenance; do not introduce new strategy semantics.

## Test Review

Independent exact-tree checks:

1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q docs/development/ITER-0026/review_evidence/test_iter0026_rereview_nosend01.py docs/development/ITER-0026/review_evidence/test_iter0026_rereview_probes01.py docs/development/ITER-0026/review_evidence/test_iter0026_review_matrix.py docs/development/ITER-0026/review_evidence/test_iter0026_rereview_edges01.py` — **32 passed in 0.82s**, network attempts empty. Includes the two original M2 probes, four original finding probes, actual partial-failure/cached-cycle matrix, invalid economics/public-path/in-flight certification and repeated-teardown SDK ordering cases.
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q` — **1647 passed, 2 skipped in 82.02s**, network attempts empty. Includes final placement372, affected RDP and FL/legacy regressions without exclusion.
3. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q /tmp/test_iter0026_rereview_empty02.py` — **1 failed in 0.55s**, network attempts empty: `test_authoritative_empty_with_unresolved_capture_has_terminal`; expected a retained terminal result, got `None` while actual owner is UNKNOWN/unresolved. M2 remains.
4. `git diff --check` — passed.

New probe: `/tmp/test_iter0026_rereview_empty02.py`. Final probe log: `/tmp/ITER-0026-rereview-empty02.log`. Preserve these new files with this report for durable repair reproduction. Earlier archived probes/artifacts were not changed. The temporary harness emits an existing dependency deprecation warning; standard-suite skips remain existing skips, not an acceptance waiver.

No additional broad repeat was run after final-tree full-suite completion; the failed targeted probe establishes the remaining concern.

## Scope Review

Fix02 stayed within the two authorized RDP-local files. No shared-core change, strategy redesign, unrelated refactor, CLI activation, real-money action, main merge or Git history mutation occurred.

The remaining M2 family belongs to frozen DG-006. Return to Worker for one unified authoritative-evidence selection correction, then independently re-review. User scope remains ITER-0026; no next iteration is authorized by this review.

## Residual Risks

Unregistered acknowledgments, observed IDs and unprovable capture state intentionally remain unresolved and require human attention; no heuristic recovery/adoption is authorized. Offline fake-client checks do not prove live execution. Other delivery gaps remain outside this iteration.

DG-006 remains OPEN. ITER-0026 is not CODE_READY. MICRO_LIVE_READY remains NO. Real-money and main-merge authorization remain NOT_GRANTED.

## Final Decision

RETURN_TO_WORKER.

B1/B2/M1 remain closed and the original two M2 cases pass. Close M2's remaining EMPTY branch by deriving terminal failure and cause from actual matching unresolved owner evidence independently of outer wrapper label, preserving genuinely clean compatibility. Add the concrete fake-SDK regression and obtain independent review before claiming full DG-006 closure.
