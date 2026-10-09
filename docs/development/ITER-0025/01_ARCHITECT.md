# ITER-0025 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `8a8efcb8a3b7f1ae2ddea44b71bb6f24ffd1c7fa`, matching `origin/rdp-phase0-dev`.
- Latest implementation PASS: ITER-0024, archived and reviewed.
- Explicit human implementation resume is recorded.
- Inspected updated `AGENTS.md`, Frozen SOP, `DELIVERY_GAPS.md`, approved convergence draft, current index, prior RESULT and actual runtime/gateway/placement implementation and tests.
- Tracked tree clean; preserved `research_outputs/` remains untracked.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **YES — bounded, default-off gateway placement diagnostics/outcome extension described below**.

FL Behavior Impact: **Legacy default behavior preserved; regression verification required**.

IM Dependency: **NONE**.

## Delivery Gap / Closure Target

**DG-001 — Authorized Initial NORMAL Submission.**

Target full closure of DG-001’s submission acceptance condition: default denial or one guarded initial SDK placement ending in safely retained typed ownership.

This does not close DG-002–DG-012. The initial operational-time check is necessary here; complete REBALANCE CanSafelyContinue/STOP remains under DG-004/DG-005.

Reviewer must identify whether every DG-001 closure condition is satisfied. Otherwise retain OPEN and name the precise remaining condition.

## Problem

Connect actual initial NORMAL admission, pending capture, current revalidation and the existing strict gateway into one retained, default-denied submission operation.

**Start:** Fresh session-issued initial admission, healthy selected-market inputs and no prior run orders.

**End:** A no-send result, or one resolved initial placement outcome retaining complete/empty/partial/unknown/error evidence after actual SDK work drains.

## Evidence / Current Behavior

The convergence draft remains applicable. Existing runtime provides admission, arming, coherent factual revalidation and acknowledgment ownership; it makes no production `gateway.place()` call.

Gateway placement provides:

- Pre-sign and post-sign guard calls.
- Forced post-only and strict pair parsing.
- Immutable per-token signing options.
- Typed partial/unknown acknowledgments.

One necessary draft adjustment follows actual code: the gateway’s generic failure branch logs raw `str(exc)` and returns an unclassified empty list. SDK diagnostic suppression does not sanitize this gateway-owned log. The new RDP production connection must not expose that diagnostic or confuse a failure with clean empty evidence.

Frozen §§4, 15–18 and 26, DG-001 and existing safety requirements control.

## Existing Components Reused

- Current official inputs and trusted initial admission/account/status evidence.
- Existing initial arming and processor pre-ack capture.
- `revalidate_initial_normal_intent()` and its coherent thread bridge.
- Existing capital/backing checks, Frozen quotes and supplied signing ticks.
- `ExecutionGateway.place()`, strict pair parser and `PlacementUnknown` machinery.
- `RDPPlacementOwner.resolve()`.
- Existing task ownership, shield/drain and safe gateway diagnostic patterns.

## Existing Components Modified

- `src/polymaker/rdp_runtime.py`: initial submission operation/result, combined guards, ownership and teardown.
- `src/polymaker/execution/gateway.py`: narrowly opted-in placement diagnostics/error handling.
- `tests/test_rdp_placement.py`: composed submission and failure/cancellation tests.
- `tests/test_execution.py`: opt-in outcome/diagnostic and legacy compatibility tests.
- `tests/test_rdp_runtime.py` only for necessary teardown/default-mode checks.

The gateway extension must default off. RDP enables it with strict pair placement; existing callers retain their default behavior.

No processor, strategy, capital formula, CLI, configuration or dependency changes are authorized.

## New Components Required

One retained RDP initial submission operation and immutable result.

A default-off gateway option is required because the existing generic branch prevents safe outcome provenance at this call site. Reuse existing placement/error machinery; do not duplicate SDK placement or parsing.

## Desired Behavior

An explicit operation defaults to denied execution.

When deliberately authorized for this retained market, it validates operational budgets, arms capture before POST and delegates once through the existing gateway. Both guard locations enforce authorization, interruption, operational time and current factual revalidation.

Actual typed outcomes resolve through the existing owner. Cancellation, deadline overrun or teardown cannot discard acknowledgments or detach SDK work.

Authorized development cases use fake clients only.

## Minimal Scope

1. Require explicit authorization whose value is exactly `True`; absent, false or invalid authorization denies before arming, signing or POST. Binding is to this operation’s exact retained market.
2. Require the actual current session-issued initial admission, empty initial scope and no competing submission/lifecycle owner.
3. Require finite positive order-operation and close/reconciliation budgets. Do not accept an unexplained “time safe” boolean.
4. At activation, require remaining market time strictly greater than the sum of those budgets. Establish an absolute monotonic order-operation deadline.
5. At both gateway guard locations, require unexpired operation deadline and sufficient remaining market time for the remaining allocated order interval plus the close/reconciliation budget. Reject equality, invalid clocks and elapsed deadlines.
6. Use existing exact freshness/comparison conventions. Budgets are operational safety controls, not a strategy timeout or hard REBALANCE duration.
7. Arm through the existing runtime method before yielding into placement. Retain one owner across rate waiting, signing, POST, outcome resolution and cleanup.
8. Invoke the existing gateway once with strict placement, forced post-only, opted-in pair parsing and exact captured per-token ticks. Do not adjust prices or quantities.
9. Combine the existing factual validator with operation authorization/interruption/time checks. Mutable market/processor evidence remains evaluated on the owning event loop.
10. Add default-off gateway safe placement handling:
    - Preserve complete/empty/partial/unknown parsing and safely attributed IDs.
    - Preserve typed uncertain outcomes through the opted-in path.
    - Generic failure must return/raise categorical typed failure evidence rather than an unclassified clean empty result.
    - Distinguish known pre-POST failure from uncertain attempted POST in the reason/provenance.
    - Emit categorical diagnostics outside the raw SDK exception handler, using the existing safe diagnostic pattern.
    - Do not deliver raw SDK exception text or active exception chains through opted-in diagnostics.
    - Diagnostic-handler failure must not replace authoritative placement evidence.
11. Shield and drain placement through first/repeated cancellation or teardown. Do not cancel the underlying blocking SDK operation or close its gateway early.
12. Deadline overrun revokes further send permission. If POST already began, drain and retain its actual outcome; timeout cannot prove no order exists.
13. Resolve through the existing owner once. Preserve pre-ack fill replay and separate attributed acknowledgment IDs from observed IDs.
14. If resolution raises after recording ownership, inspect the actual retained owner rather than overwriting it with a fabricated empty result or resolving twice.
15. Caller interruption propagates only after the actual outcome/uncertainty is retained. Keep a reconstructable result available even when the caller receives cancellation.
16. No automatic cycle start, cancellation, retry, requote or unknown-ID recovery follows submission.
17. Default CLI, preflight and observation behavior remain unchanged.

## Invariants

- At most one initial placement operation and one pair POST.
- Capture precedes POST.
- Both existing send guard locations remain active.
- Factual validity alone grants no execution authority.
- Authorization/time checks cannot be skipped after signing.
- Frozen prices, sizes, minimum metadata, $30 capital and approved $5 reserve remain unchanged.
- Inventory changes only through accepted confirmed fills.
- A blocking POST cannot be undone by cancelling its coroutine.
- Actual acknowledgment/uncertainty survives drain, interruption and logging failure.
- Partial/unknown/error evidence never becomes clean empty evidence merely for cleanup.
- Unknown or attributable resting exposure retains replacement blocking.
- Foreign state is not adopted or canceled.
- Existing factual evidence objects remain `execution_authorized=false`.
- No development agent performs a real-money action.

## Acceptance Criteria

1. Default/invalid authorization performs zero arming, signing, POST or cancellation.
2. Invalid budgets, insufficient remaining time and exact budget boundaries deny new risk.
3. Fake authorized valid submission makes one strict post-only pair POST with exact captured signing ticks and Frozen economics.
4. Current facts and operational time are checked at both actual gateway guard locations.
5. Changes during rate waiting/signing, cancellation or operation expiry prevent a not-yet-started POST.
6. Accepted pre-ack confirmation replays exactly once after typed acknowledgment.
7. Complete, authoritative empty, partial, unknown and generic failure retain correct attributable/observed scope and replacement status.
8. Opted-in generic SDK failure is categorical and cannot masquerade as clean empty; raw error text and exception chains do not escape diagnostics.
9. First/repeated cancellation and teardown during actual fake SDK signing/POST drain the operation, resolve its evidence and perform no retry.
10. A late acknowledgment after interruption or deadline remains owned; result does not claim no send or healthy further execution.
11. Concurrent activation and conflicting runtime actions cannot start another submission or destroy capture.
12. Default-off gateway behavior and inactive/default CLI/runtime remain compatible.
13. Required targeted, integration, predecessor and practical full-suite checks pass.

## Required Tests

Block/count real DNS/socket access before imports. Use existing fake clients with actual gateway/executor paths.

- Default/invalid authorization and zero side effects.
- Finite-positive budget validation, exact wall/monotonic boundaries and invalid clocks.
- Actual fresh admission → arm → both guards → one fake POST → typed ownership.
- Expiry during rate waiting and each signing stage.
- Quote/filter/tick/minimum, account/status freshness, generation and pending-evidence invalidation.
- Complete pair, all-rejected clean empty, partial acceptance, observed unknown IDs, ambiguous response and POST exception.
- Generic pre-POST/signing failure and uncertain attempted-POST failure.
- Sensitive sentinel exception text; failing diagnostic handler; sanitized typed evidence without active raw chains.
- Default gateway compatibility, including legacy generic handling.
- Pre-ack buffered fills and malformed capture.
- First/repeated cancellation and teardown during blocked fake signing/POST.
- Deadline overrun with late complete/partial/unknown outcome.
- Resolution failure before/after actual owner update.
- Competing submission/admission/arm/close/window actions.
- No retry, foreign adoption/cancellation, automatic cycle activation or CLI authority expansion.

Run narrow submission/gateway tests, affected runtime/placement/execution/account/capital/lifecycle checks, FL regressions and full suite when practical. Record exact commands, outcomes and reviewed hashes.

## Observability Requirements

Record market/attempt, captured request/ticks, authorization status, operational budgets/deadlines, guard rejection category, no-send versus attempted/uncertain outcome, attributed/observed IDs, resolution and replacement blocking.

Do not claim definitive POST provenance when available evidence supports only uncertainty.

Use existing structured logging and sanitized results. Do not introduce full delivery metrics in this iteration.

## Live Validation

No live requests, orders or cancellations during implementation or review.

After complete delivery and explicit real-money authorization, the human supervises one BTC5m market. Current validation uses fake official clients and real executor ownership only.

## Explicit Non-Goals

- CLI live activation or full runner.
- Automatic cycle start, subsequent NORMAL or REBALANCE operation.
- Complete CanSafelyContinue/STOP controller.
- Unknown-ID recovery or inventory adoption.
- New accounting/reservations or strategy timers.
- Broad gateway refactoring or new runtime interfaces.
- Real-money create/cancel validation.

## Risks

The gateway can finish after cancellation or deadline. Retaining the coroutine and actual SDK outcome is mandatory.

Its SDK-thread final callback must use the existing coherent bridge. Independent mutable-state reads are not an acceptable substitute.

Generic gateway errors currently hide provenance in an empty return. The bounded opt-in extension is required by this submission behavior and cannot be deferred as unrelated work.

## Deferred Findings

DG-002–DG-012 remain OPEN.

The submitted outcome supplies evidence for DG-006 terminal handling; loss of attribution, false replacement release, detached SDK work or raw failure disclosure are current blockers, not deferred features.

Full market-time/STOP orchestration remains DG-004/DG-005. This initial budget implementation adds no performance cutoff or hard REBALANCE timer.

## Human Approval Required

**NO additional architecture approval.**

Explicit human implementation resume is granted. The gateway adjustment is a minimal, default-off implementation correction within existing official-interface and safety authority.

Real-money trading authorization remains **NOT_GRANTED**. No CLI activation, main merge or live execution is authorized.
