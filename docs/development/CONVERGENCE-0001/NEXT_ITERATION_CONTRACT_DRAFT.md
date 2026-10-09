# Next Iteration — Architect Contract Draft

## Status

DRAFT — IMPLEMENTATION_NOT_AUTHORIZED

Stored under CONVERGENCE-0001 only.
Iteration ID: UNASSIGNED.
Do not initialize ITER-0025 or dispatch Worker from this document.

Explicit human resume and a fresh repository/contract check are required before approval for implementation.

## Base Repository State

Root: /home/ubuntu/poly-maker.
Branch: rdp-phase0-dev.
Draft baseline: 7aa329e02bc5708bc5c2f5df956c9e5036085a07.
Latest archived implementation: ITER-0024, CODE_READY, final Reviewer06 PASS.
Development status: PAUSED_FOR_DEVELOPMENT_CONVERGENCE.
MICRO_LIVE_READY: NO.
Real-money authorization: NOT_GRANTED.

Shared-Core Change: NO.
FL Behavior Impact: NONE.
IM Dependency: NONE.

## Delivery Gap

DG-001 — Authorized Initial NORMAL Submission.

This draft proposes closing the initial submission behavior, not the integrated runner, subsequent cycles or all terminal handling. DG-002–DG-012 remain open.

The operational-time check is a necessary send-gate element. Its implementation here does not close the full CanSafelyContinue/STOP behavior under DG-004/DG-005.

## Problem

Actual initial admission, ARMED capture, coherent factual revalidation and strict pair placement exist, but no retained RDP operation connects them into one deliberately authorized initial submission and typed ownership result.

Start: a healthy retained single-market session with a freshly issued initial NORMAL admission and no prior run orders.

End: either a default-denied/no-send result, or one resolved initial placement attempt containing the safely attributed complete/empty/partial/unknown outcome after actual SDK work has drained.

## Evidence / Current Behavior

- cli.py: rdp_live exposes read-only modes only.
- rdp_runtime.py issues initial admission and arms capture.
- revalidate_initial_normal_intent checks current facts through both callback threads but grants no live authority.
- Admission retains HUMAN_LIVE_AUTHORIZATION and OPERATIONAL_TIME_SUFFICIENCY as outstanding.
- gateway.place supports strict post-only pairs, supplied per-token ticks, two guard locations and typed uncertain outcomes.
- RDPPlacementOwner resolves acknowledgments and preserves blocked unknown/partial capture.
- No production RDP gateway.place call exists.

Sources: Frozen §§4, 15–18, 26; AGENTS §§4–6, 31, 36; DELIVERY_GAPS DG-001.

## Existing Components Reused

- Retained public/user inputs and exact official initial account/status evidence.
- Actual initial admission and trusted provenance.
- Initial arming and existing processor pre-ack capture.
- Coherent initial intent revalidation and bridge.
- Existing gateway.place with strict placement, forced post-only, opted-in two-order pair and per-token ticks.
- Existing typed acknowledgment/PlacementUnknown evidence.
- RDPPlacementOwner.resolve and current shield/drain patterns.

## Existing Components Modified

Proposed scope after resume:

- src/polymaker/rdp_runtime.py: explicit default-denied initial submission operation, task ownership, combined send guard and teardown integration.
- tests/test_rdp_placement.py: actual composed submission and failure-race tests.
- tests/test_rdp_runtime.py only for necessary teardown/default-observer compatibility.

Small RDP-local helper extraction is permitted. No production shared gateway, processor, CLI, configuration, strategy or capital formula changes are proposed.

If actual implementation requires broader scope, return to Architect rather than silently expanding.

## New Components Required

One retained initial submission operation and a small immutable submission result describing no-send or the resolved existing attempt.

No new transport, reservation ledger, strategy state or general controller.

## Desired Behavior

An explicit operation defaults to denied execution. Only deliberate authorization for this exact market can activate the submission path.

With authorization, validated operational budgets and current factual evidence, arm capture before the single pair POST, pass immutable per-token signing ticks and combine factual revalidation with authorization, interruption and time gates at both existing guard locations.

Resolve the actual typed result into the existing owner. Retain uncertainty and safely attributed IDs without retrying.

No production activation is exercised by development agents. Authorized cases are tested only with fake official SDK/network infrastructure.

## Minimal Scope

1. Default/absent/invalid authorization rejects before signing or POST. Read-only callers do not acquire authority by observing eligible data.
2. Bind explicit activation to the retained session's exact selected market and current initial admission.
3. Require finite positive order-operation and close/reconciliation budgets. Do not assume OPERATIONAL_TIME_SUFFICIENCY is already satisfied.
4. Implement TimeAvailable from remaining market time and those operational budgets: new risk requires time for the order operation plus scoped Cancel → Reconcile. Reject at the boundary and recheck at both guard locations.
5. Treat budgets as operational safety controls, not MaxRebalanceSec, a no-fill timeout or a strategy performance cutoff. Select/record ordinary engineering defaults only when the eventual caller needs them; this API may require explicit budgets.
6. Reuse initial admission, arming and the current factual revalidation. Verify all outstanding send gates; factual validity alone cannot authorize execution.
7. Retain pending capture and one submission owner before yielding into gateway.place. Serialize competing admission/arm/submit/lifecycle actions.
8. Delegate once with strict placement, forced post-only, opted-in pair parsing and exact captured token ticks.
9. Combine current authorization, interruption, operational deadline and factual validation in the existing pre-sign/final guard. Do not read mutable strategy state from the SDK thread.
10. Shield and drain the placement operation through first/repeated caller cancellation or teardown. SDK completion may outlast its operational budget; retain ownership and honest uncertainty rather than detaching or retrying.
11. Resolve complete, clean authoritative empty, typed partial/unknown and unexpected-error outcomes using the existing owner. Preserve original typed attribution and buffered fill evidence.
12. Caller interruption after a POST must not discard a returned acknowledgment or turn an uncertain operation into a clean empty result.
13. Return immutable no-send or owned-outcome evidence. Do not automatically start a cycle, retry POST, requote or recover unknown IDs.
14. Existing CLI/preflight/observe defaults remain unchanged. There is no agent-authorized live run.

## Invariants

- At most one initial placement operation and one pair POST.
- Capture precedes POST; inventory remains confirmed-fill-only.
- Frozen prices, sizes, minimum metadata and $5 reserve are unchanged.
- Both send guards enforce current facts and outstanding authorization/time conditions.
- A budget deadline cannot forcibly undo a blocking SDK POST; ownership survives until it drains.
- No ambiguity is rewritten as clean rejection.
- Attributed acknowledgments and merely observed IDs remain separate.
- Unknown/partial state blocks replacement and preserves available evidence.
- Foreign orders/inventory are neither adopted nor canceled.
- Existing evidence objects remain factual and execution_authorized=false.
- No real-money action occurs in development tests.

## Acceptance Criteria

1. Default/absent/invalid authorization yields zero signing, POST and cancel calls.
2. Invalid budgets, insufficient remaining time or equality at the required budget boundary deny new risk.
3. Fake authorized valid execution makes one strict post-only pair POST with the captured per-token ticks and exact Frozen economics.
4. Stale/changed evidence or elapsed time during rate wait/signing denies the final POST.
5. Pre-ack buffered confirmation and typed complete acknowledgment register/replay through the existing processor exactly once.
6. Clean empty results release only when existing owner criteria allow; partial/unknown/error outcomes retain blocking and attribution.
7. First/repeated cancellation and teardown during signing/POST wait for actual fake SDK work, resolve its evidence and perform no retry.
8. Concurrent submission and conflicting runtime actions are rejected without a second operation.
9. Result/logs accurately distinguish authorization, no-send, attempted POST and unresolved outcome; no sensitive raw exception text.
10. Existing read-only CLI/default runtime and FL behavior remain compatible.
11. Targeted, affected, predecessor and practical full-suite checks pass for the reviewed implementation.

## Required Tests

Block/count real DNS/socket access before imports. Use existing fake clients and real executor fixtures.

- Default denial and invalid authorization.
- Exact operational-time boundary, clock invalidity, budget expiry during rate wait and signing.
- Actual fresh admission → arm → both guard locations → one fake successful POST → typed ownership.
- Dynamic tick, quote/filter, account/status freshness and generation changes before final guard.
- Complete, authoritative empty, partial, ambiguous response, POST exception and unexpected placement error.
- Pre-ack accepted fill replay and malformed/pending capture.
- First/repeated cancellation and teardown during actual fake executor signing and POST.
- No duplicate submit, no detached work, no unknown retry, no foreign adoption/cancel.
- Existing observation/CLI and lifecycle regressions.

Run narrow submission tests, affected runtime/placement/execution/account/capital/lifecycle tests, FL regressions and full suite when practical. Record exact commands/results and reviewed hashes.

These tests are required only after implementation resume. Do not run them for this document-only draft.

## Observability Requirements

Record selected market, attempt, captured request/ticks, authorization state, operational budgets/deadlines, guard rejection category, attempted-send status, attributed/observed IDs, resolution outcome and replacement blocking.

Use existing structured logging. Do not claim real-money trading occurred in fake tests or that factual validation alone granted authorization.

## Live Validation

None during this proposed implementation.

After full delivery and explicit human real-money authorization, the human supervises one BTC5m market. Until then, all create/cancel validation remains fake; safe official read-only checks belong to final path evidence.

## Explicit Non-Goals

- CLI live activation or complete integrated runner.
- Automatic cycle start or subsequent NORMAL/REBALANCE execution.
- Complete CanSafelyContinue/STOP controller.
- Heuristic unknown-order recovery.
- New ledger, formula, reserve or external interface.
- Reporting/metrics beyond evidence required for this operation.
- Real-money order/cancel actions.

## Risks

The existing gateway has asynchronous rate waiting and blocking signing/POST. The operation must retain one owner across all phases.

Authorization/time enforcement cannot be delegated to the factual validator implicitly.

An SDK operation may finish after cancellation or deadline. Its actual typed outcome must survive drain and resolution; a timeout does not prove no POST.

## Deferred Findings

The remaining delivery behaviors DG-002–DG-012 stay OPEN.

Safe partial/unknown terminal handling remains DG-006; this submission operation must already retain blocking and evidence needed by that handler. It cannot defer loss of attribution, detached SDK work or false replacement release.

No new strategy-semantic decision is required by the proposed budget-based implementation. If implementation discovers a material timing policy beyond Frozen TimeAvailable, stop and identify the smallest missing authority.

## Human Approval Required

Implementation resume: REQUIRED and not granted.

Real-money trading: NOT_GRANTED.

This is a reviewable draft, not APPROVED_FOR_IMPLEMENTATION. Document approval and archival do not authorize Worker dispatch, iteration initialization or execution.
