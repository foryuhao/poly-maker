# ITER-0019 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `7d00c77997ac2190929b31cb54df609092d1f66b`, matching the local remote-tracking reference.
- Latest archived PASS: ITER-0018, commit `7a787b1d83b4894e71bb2ec16cd57bfc92428561`.
- Reviewed prior evidence: ITER-0018 `RESULT.md`, `08_REVIEWER_REVISION_01.md` and `09_ARCHIVE_RECEIPT.md`.
- Working tree contains only preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **YES**, limited to default-off receipt metadata in the existing user stream and processor.

FL Behavior Impact: **NONE intended**; default event processing, accounting, callbacks and research behavior must remain compatible.

IM Dependency: **NONE**.

## Problem

The retained initial NORMAL placement has no FillWindow derived from its first accepted confirmed fill. Original receipt times are unavailable in production RDP, so a fill received before acknowledgment cannot reliably establish the correct deadline after replay.

Implement exactly this prerequisite: a scoped FillWindow decision bound to the existing initial placement attempt.

## Evidence / Current Behavior

- Frozen SOP §§18–21 require one current cycle, a configurable FillWindow initially three seconds, and closure after expiry.
- Frozen confirmed inventory/accounting and ActiveCycle rules govern accepted RDP fill evidence. ITER-0013’s approved contract explicitly requires confirmed-fill authority for FillWindow integration.
- `UserStream` forwards receipt timestamps to the existing processor, but currently captures them only when a research observer is attached.
- `UserEventProcessor` already buffers and replays receipt metadata during pre-ack races. It owns validation, attribution, lifecycle progression, deduplication and quarantine.
- Confirmed records can arrive before MATCHED evidence and remain excluded from `snapshot_inventory` until admitted into accepted economic evidence.
- `RDPPlacementOwner` retains the exact acknowledged scope and replacement block.
- `RDPInputSession` retains those components and exposes explicit scoped closure, but has no FillWindow decision.

## Existing Components Reused

- `UserStream` parsing, normalization and connection generations.
- `UserEventProcessor` pending capture, registration replay and accepted fill records.
- `snapshot_inventory` for confirmed inventory.
- `RDPPlacementOwner.current` for attempt identity and acknowledged IDs.
- Existing input-session health checks and explicit close composition.
- Existing logging and fake transport/SDK test infrastructure.

## Existing Components Modified

1. `src/polymaker/userstream/client.py`: default-off production receipt capture, enabled by RDP independently of research.
2. `src/polymaker/state/tracker.py`: default-off retention of original confirmed-event receipt metadata alongside existing evidence.
3. `src/polymaker/rdp_userstream.py`: enable both options when constructing RDP’s existing stream and processor.
4. `src/polymaker/rdp_runtime.py`: retain configurable FillWindow duration and expose the current initial-attempt window decision.
5. Relevant tests.

Shared changes must retain metadata only. Do not alter fill admission, optimistic legacy store updates, deduplication, replay ordering or callback semantics.

## New Components Required

One small RDP-local component, preferably `src/polymaker/execution/rdp_fill_window.py`, containing the immutable window result and scoped timing logic.

No new transport, fill ledger, reconciliation engine or background scheduler.

## Desired Behavior

For the current fully acknowledged initial NORMAL pair:

- Before accepted confirmed fill evidence exists, the window has not started.
- The first accepted scoped confirmed fill establishes the window using its original monotonic receipt.
- Default duration is `3.0` seconds and is configurable through the retained RDP session API.
- Later fills, duplicates, replay, ordinary book movement and repeated queries cannot restart or extend it.
- At `now >= deadline`, the result reports closure due.
- Inventory comes exclusively from `snapshot_inventory`.
- Missing or unreliable timing/evidence produces an uncertified result with a reason.

This result schedules a necessary future cycle action. It does not submit, cancel, reconcile or authorize execution.

## Minimal Scope

1. Capture each opted-in user frame’s monotonic receipt when delivered, before an intervening asynchronous operation. Clear transient receipt context afterward, including control frames and failures.
2. Preserve the original receipt and connection generation through the existing pending-event replay.
3. Retain confirmed-event timing only after existing attribution and economics validation. The timer additionally requires accepted economic evidence; a CONFIRMED-only record does not independently qualify.
4. Bind one retained window to the placement owner’s current resolved, complete pair.
5. Expose a synchronous decision containing attempt identity, scope, reliability/reason, started flag, first receipt, duration, deadline, closure-due flag and confirmed inventory.
6. Validate duration as finite and positive; reject booleans and invalid values.
7. Keep existing CLI modes and resource ownership unchanged.

Names and internal representation are implementation choices within these requirements.

## Invariants

- Only the exact safely acknowledged pair may supply window evidence.
- Foreign, observed-only, quarantined, failed, zero-size or unaccepted records cannot start a certified window.
- MATCHED, MINED and RETRYING evidence does not start it.
- Confirmed receipt history supplements the existing processor; it is not independent fill truth.
- Original arrival time survives replay. Registration time, exchange timestamps, polling time and wall-clock changes are not substitutes.
- Missing original timing cannot be repaired by assigning a later duplicate’s receipt.
- A duration is captured for the attempt and cannot change an already established deadline.
- Ordinary price/depth movement cannot invalidate or retime a started window.
- Partial/unknown placement outcomes remain blocked.
- Expiry does not release replacement blocking; existing authoritative close proof remains required.
- Execution authorization remains false.
- No new strategy state or stop policy is introduced.

## Acceptance Criteria

1. Default-off shared callers retain existing behavior. Production RDP obtains frame receipt metadata without enabling research.
2. A valid MATCHED → CONFIRMED sequence starts the window at the CONFIRMED receipt and exposes the existing confirmed inventory.
3. CONFIRMED-before-MATCHED progression uses the original confirmation receipt once the processor admits economic evidence; it does not start prematurely.
4. Pre-ack replay preserves the earliest qualifying receipt across both orders, regardless of registration order.
5. With first receipt `100s` and duration `3s`, closure is not due before `103s` and is due at exactly `103s`.
6. Later partial fills and duplicate/out-of-order lifecycle events preserve that deadline.
7. Complete natural fills before expiry do not introduce an unapproved early-close optimization.
8. Missing, malformed, future or generation-inconsistent receipt evidence cannot certify a window or fabricate a fresh period.
9. REST-only discovery without preserved arrival timing produces an uncertified result. Existing reconciliation behavior remains unchanged.
10. Quarantine, failed evidence, placement uncertainty, unhealthy input generations, closed session or market termination prevents a certified continuation decision.
11. Ordinary healthy book changes, including a Book Filter rejection, do not restart or shorten an already started window.
12. Window queries and teardown perform zero create, cancel or additional account/reconciliation calls. Existing explicit close remains independently usable.
13. Targeted, affected integration and predecessor regressions pass; the full suite passes when practical.

## Required Tests

Use injected monotonic clocks and fake official transports with DNS/socket access blocked.

- Production stream receipt capture without a research observer; context cleanup and disabled-option compatibility.
- Pending capture/replay retaining original timestamp and generation.
- MATCHED-only, MINED-only, RETRYING, CONFIRMED-only and later accepted confirmation sequences.
- Pre-ack fills on both sides with reversed acknowledgment/registration order.
- Default and configured durations; exact expiry boundary.
- Partial fills, duplicate confirmations, older lifecycle replay and both orders completely filled.
- Foreign IDs, observed-only IDs, wrong market/token, malformed economics, zero size, quarantine, FAILED and pending overflow.
- Missing/invalid receipt, conflicting generation, future receipt and missing original receipt followed by a valid later duplicate.
- REST-only confirmed recovery without timing, preserving existing close-barrier tests.
- Public/user health failure, market end and session teardown.
- Healthy ordinary book movement preserving the window.
- Counters proving zero create/cancel/read calls from window decisions and cleanup.

Run narrow tests first, then existing userstream/processor, RDP placement/runtime/accounting/lifecycle integration tests, FL regressions and the full suite. Preserve exact commands and results.

## Observability Requirements

Record window start and first observed expiry once per attempt using existing structured logging.

Include condition, attempt ID, acknowledged scope, generations, first monotonic receipt, configured duration, deadline and reliability/reason. Expose confirmed inventory in the decision.

Do not log raw authenticated frames or introduce a separate telemetry system.

## Live Validation

No live actions are required or permitted for this iteration.

After complete delivery and explicit human authorization, compare actual confirmed-event receipt timing with the documented three-second window and subsequent scoped Cancel → Reconcile sequence. Fake validation establishes implementation behavior, not live execution readiness.

## Explicit Non-Goals

- Production submission or live-send authorization.
- Automatic cancellation, reconciliation or next action.
- Pre-fill invalidation controller.
- Subsequent NORMAL or REBALANCE execution.
- Unknown-placement recovery.
- New capital reservations or accounting formulas.
- Broad metrics implementation.
- Research activation or transport replacement.
- Frozen SOP, CLI or external-interface changes.

## Risks

Pre-ack replay and status reordering can obscure the original confirmation receipt. The shared receipt extension must preserve provenance without altering admission.

Missing timing must fail closed rather than extend resting time. Shared defaults require regression coverage.

## Deferred Findings

Production submission, final execution guards, continuous account certification, automatic cycle closure, subsequent NORMAL/REBALANCE operation, unknown recovery and complete metrics remain required.

A later controller must consume this decision and the existing close proof. This iteration alone does not make the runner tradable or MICRO_LIVE_READY.

## Human Approval Required

**NO.**

This is a bounded implementation of the existing confirmed-fill integration requirement and Frozen FillWindow timing. It changes neither strategy semantics nor approved runtime interfaces.

Real-money trading remains unauthorized.
