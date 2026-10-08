# ITER-0020 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `0798413b628f3858877b4844940df7dd1f491a32`, matching the local remote-tracking reference.
- Latest archived PASS: ITER-0019, `990d5689b165ac1a200c2cf6d50c55a4c6d3a23c`.
- Relevant evidence: ITER-0019 `RESULT.md`, `05_REVIEWER_REREVIEW_01.md` and `06_ARCHIVE_RECEIPT.md`.
- Working tree contains only preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **NO**.

FL Behavior Impact: **NONE**.

IM Dependency: **NONE**.

## Problem

The initial pair’s FillWindow and scoped close barrier are individually implemented, but no retained action connects window expiry to Cancel → Reconcile → confirmed inventory.

Implement exactly one prerequisite: an explicitly activated coordinator that completes an already-started initial NORMAL FillWindow through the existing close composition.

## Evidence / Current Behavior

- Frozen SOP §§20–21 require remaining orders to rest during the FillWindow, then close at expiry or a system-level exception.
- `RDPInputSession.initial_fill_window()` already derives the original confirmed-fill deadline, rejects unreliable evidence and preserves timing after contradiction.
- `close_initial_normal_placement()` already validates ownership, delegates scoped cancellation/reconciliation and derives confirmed inventory/capital/next-mode evidence.
- Existing close tests demonstrate that lost public generations still permit cancellation of known risk while preventing capital and next-mode certification.
- Existing gateway delegation retains SDK operations through cancellation and deadline expiry.
- The session owns admission and close tasks but has no owner for the interval between an established window and its required closure.

## Existing Components Reused

- `initial_fill_window()` and its retained attempt-bound deadline.
- `RDPPlacementOwner` and the registered acknowledgment scope.
- `close_initial_normal_placement()` and its current proof checks.
- Existing input health, market-end and generation checks.
- Existing cancellation/draining helpers, session sleep/poll interval and logging.
- Existing fake SDK, transport and confirmed-fill fixtures.

## Existing Components Modified

- `src/polymaker/rdp_runtime.py`: coordinator, its task ownership/result and teardown integration.
- `tests/test_rdp_placement.py`: composed timing, closure, race and interruption tests.
- `tests/test_rdp_runtime.py` only where necessary to prove existing read-only modes remain inactive.

Do not modify gateway, processor, accounting, capital formulas or the underlying close barrier.

## New Components Required

A small immutable coordinator result and one retained asynchronous action inside the existing RDP runtime.

No separate controller framework, timer service or execution subsystem.

## Desired Behavior

An explicit runtime API activates completion for the current fully acknowledged initial pair after its FillWindow has started.

It retains the pair until the established deadline, allowing ordinary market movement and additional fills. At expiry, input failure, unreliable window evidence or market termination, it invokes the existing scoped close operation once.

It returns the trigger and existing close evidence. It performs no subsequent quote, order creation or trading transition.

Interruption must preserve ownership, perform or drain the scoped cleanup as appropriate, and propagate cancellation after owned work finishes.

## Minimal Scope

1. Add an API such as `complete_initial_fill_window(attempt, *, close_budget_s=...)`. Naming is an implementation choice.
2. Require current attempt identity, a complete safely attributed pair, matching registry economics, retained replacement blocking and an already-established FillWindow.
3. Capture a finite positive operational close budget supplied by the caller. Reject invalid values before activating work.
4. Own one coordinator task spanning waiting and closure.
5. During waiting, query the existing window decision using monotonic time and sleep no longer than the smaller of the existing poll interval and remaining window time.
6. On expiry or system failure, derive an absolute operational barrier deadline from the captured budget and invoke the existing close API.
7. Integrate mutual exclusion with explicit closure and session teardown.
8. Retain the completed result and log its trigger and outcome.

The operational budget bounds the existing close attempt. It is not a new strategy timer, FillWindow extension or REBALANCE limit.

## Invariants

- Activation is an explicit request to complete this owned window; existing CLI paths never activate it.
- A pair without an established window belongs to the deferred pre-fill controller.
- Foreign, observed-only, partial/unknown or mismatched scopes cannot activate this coordinator.
- Additional fills and ordinary book changes cannot extend the deadline or cause early repricing.
- Complete natural fills do not introduce early cycle closure.
- Exactly one close operation is initiated per activation; interruption during an existing close does not initiate another.
- Cancellation targets remain limited to the existing registered acknowledged IDs.
- Current evidence and close proof determine inventory, capital certification and replacement release.
- Unreliable inputs can trigger risk closure but cannot fabricate permission for the next action.
- No SDK work is detached; owner references remain active until draining finishes.
- Execution authorization remains fixed false.
- No order creation or automatic next cycle is introduced.

## Acceptance Criteria

1. A valid started window activates one retained coordinator. Invalid attempt, registry, phase, duration or overlapping activation rejects without gateway calls.
2. With origin `100s` and duration `3s`, no cancellation begins before `103s`; the coordinator initiates closure when it observes the exact deadline.
3. Additional confirmed partial fills, duplicates and healthy ordinary book/filter changes preserve the deadline and continue resting.
4. A completely filled pair still follows the established window deadline.
5. Input staleness, either generation changing, unreliable fill/timing evidence or market termination during waiting triggers scoped closure promptly.
6. Failure-triggered closure preserves its trigger separately from barrier failure. Unreliable state cannot yield a fabricated next mode or replacement release.
7. The existing close result supplies current confirmed inventory, capital evidence and the exact five-share NORMAL/REBALANCE classification.
8. A fill during cancellation is included through existing reconciliation. Incomplete or contradictory close proof retains replacement blocking.
9. A second coordinator or external explicit close cannot overlap the active coordinator. Its own delegated close remains permitted.
10. Caller interruption while waiting performs one scoped cleanup before propagating cancellation. Interruption after closure starts drains that operation and does not retry it.
11. Repeated cancellation and teardown retain task ownership until actual SDK work drains. Gateway/store teardown occurs afterward.
12. Operational timeout retains existing incomplete-proof behavior; it cannot certify late evidence or start subsequent operations after expiry.
13. Existing preflight, observe-only, window queries and ordinary teardown without explicit coordinator activation initiate no cancellation.
14. Targeted, affected integration, predecessor regression and practical full-suite checks pass with zero real network actions.

## Required Tests

Use deterministic monotonic clocks, fake sleeps and fake official SDK/transports. Block and count DNS/socket access.

- Activation validation and already-started-window requirement.
- Default three-second and configured-window expiry boundaries.
- Multiple partial fills, duplicates, ordinary book/filter changes and complete natural fills.
- Public/user health loss, both generation changes, contradictory timing, quarantine and market end during waiting.
- Healthy expiry with final deltas below five, exactly positive five and exactly negative five.
- Confirmed fill during cancellation; MATCHED-only or contradictory close evidence.
- Coordinator/coordinator and coordinator/explicit-close overlap.
- Caller cancellation before expiry, cancellation during closure and repeated cancellation.
- Session teardown while waiting and while a real gateway executor operation is blocked.
- Parameterized blocked SDK operations: open-order read, cancel, order read and trade read. Verify draining, ownership retention and no duplicate close.
- Operational deadline expiry and no later barrier action.
- Existing read-only CLI/API behavior and zero order-create calls.

Run narrow coordinator tests, affected placement/runtime/lifecycle/accounting/capital tests, FL regressions and the full suite when practical. Preserve exact commands and outcomes.

## Observability Requirements

Use existing structured logs to record activation, closure trigger and completion.

Include condition, attempt ID, acknowledged scope, original window deadline, operational close deadline, trigger, barrier/capital outcome, replacement blocking and available next-mode evidence.

Retain the completed result for interruption/handoff inspection. Avoid polling log noise and raw SDK exceptions.

## Live Validation

No live account, order or cancellation actions are permitted during this iteration.

After complete delivery and explicit human authorization, verify that remaining owned orders rest through the documented window and then follow scoped Cancel → Reconcile → confirmed inventory. Compare failure triggers and cancellation/reconciliation timing with durable logs.

Fake validation does not establish live execution or strategy economics.

## Explicit Non-Goals

- Production submission or live-send authorization.
- Pre-fill invalidation or zero-fill requoting.
- Fresh account certification or new capital reservations.
- Subsequent NORMAL admission or REBALANCE execution.
- Unknown-placement recovery.
- Full metrics implementation.
- New runtime interfaces, strategy states or stop policies.
- CLI activation or changes to existing read-only modes.
- Reimplementation of cancellation, reconciliation or proof validation.

## Risks

Cancellation during waiting and teardown can race with the transition into closure. One retained owner and an explicit “closure started” distinction must prevent duplicate action.

An SDK call may outlast its operational deadline while draining. Preserve existing fail-closed behavior and do not treat elapsed wall time as successful proof.

## Deferred Findings

Production submission/final guards, pre-fill handling, continuous account reliability, subsequent NORMAL/REBALANCE operation, unknown recovery and complete metrics remain required.

This coordinator completes only the already-started initial FillWindow. It does not establish MICRO_LIVE_READY.

## Human Approval Required

**NO.**

This composes Frozen-required window closure with existing scoped execution infrastructure. It adds no strategy decision or external interface.

Real-money authorization remains NOT_GRANTED.
