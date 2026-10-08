# ITER-0017 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `4c54117480e3721711f5f6c3daa8e266de28d650`
- HEAD and remote match.
- ITER-0016: CODE_READY, archived at `a761cd5900c63bd6579df044610d1e84f1c0e06f`; final Reviewer05 PASS.
- Current iteration: ITER-0017, ARCHITECT.
- Only preserved `research_outputs/` is untracked.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

**Shared-Core Change: NO.**

**FL Behavior Impact:** NONE.

**IM Dependency:** NONE.

## Problem

RDP has initial NORMAL admission and strict gateway acknowledgments, but no placement-attempt owner connects acknowledgment outcomes to the retained processor’s existing fill-before-ack capture.

Implement one prerequisite: ownership capture for the initial NORMAL pair, including partial and unknown acknowledgments.

This iteration does not enable order submission.

## Evidence / Current Behavior

- `ExecutionGateway.place` already supports strict dual-pair acknowledgments, forced post-only and per-token signing ticks.
- `PlacementUnknown` retains safely attributed acknowledged orders and separately observed IDs.
- `UserEventProcessor.arm_pending_many` buffers trade events before exchange IDs are registered.
- `register_order` establishes ownership and replays buffered events through existing validation, deduplication and accounting.
- RDP currently calls none of these ownership methods.
- Existing Engine placement handling discards pending capture on errors and uses broader cleanup behavior. That orchestration must not be imported into RDP.
- Frozen quote-cycle ordering prohibits replacement while prior order/fill state remains unresolved.

## Existing Components Reused

- Initial NORMAL admission and retained session scope.
- Strict gateway typed results and `PlacementUnknown` evidence.
- Existing processor pending capture, registration, quarantine and replay.
- Existing owned-order registry and local order store.
- Confirmed-fill accounting.
- Existing structured logging.

## Existing Components Modified

- `rdp_runtime.py`: retain one initial placement-attempt owner and prevent overlapping or unresolved capture attempts.
- Relevant tests.

Shared gateway, processor, store and accounting implementations remain unchanged.

## New Components Required

One small RDP placement ownership adapter, preferably:

```text
src/polymaker/execution/rdp_placement.py
```

It owns attempt evidence and delegates authoritative order/fill handling to the existing processor.

No new order or fill ledger, acknowledgment parser or execution client.

## Desired Behavior

A future execution caller can:

```text
eligible initial NORMAL decision
→ arm existing pending capture
→ obtain strict gateway outcome
→ apply acknowledgment ownership
→ retain complete/empty/partial/unknown evidence
```

Arming capture grants no permission to send.

For valid attributable acknowledgments, register exact submitted economics in the same retained processor. Buffered fills then replay through existing processor behavior.

Partial or unknown outcomes preserve investigation evidence and block replacement. Observed IDs without safe attribution never become owned orders or cancellation targets.

## Minimal Scope

1. Bind one attempt to the selected condition, admitted YES/NO quotes and retained processor.
2. Require an eligible initial admission with matching scope/generations, current session health, unexpired interval and empty initial local ownership before arming.
3. Call `arm_pending_many` for the exact two BUY quotes.
4. Consume either the existing typed gateway result or `PlacementUnknown`; do not parse raw SDK response JSON.
5. Validate each attributable acknowledgment against submitted token, side, price, quantity, usable unique ID and expected order representation before registration.
6. Reuse local-order insertion and `register_order`; insert acknowledged local orders before buffered replay, and never overwrite them afterward with stale original-size acknowledgments.
7. Preserve partial/unknown reason, attributable IDs, observed IDs and relevant pending anomalies.
8. An empty result with no contradictory candidate-related pending evidence may clear capture and report no acknowledged orders.
9. Partial/unknown outcomes remain blocked. Generic errors or interrupted attempts are unknown unless an existing authoritative outcome proves otherwise.
10. Reject duplicate completion or overlapping attempts without repeating registration or fill effects.

Expose no send switch. Neither the adapter nor the runtime addition may call placement, cancellation or signing.

## Invariants

- Existing processor remains the ownership/fill authority.
- Acknowledgment is not fill confirmation.
- MATCHED-only evidence does not change RDP confirmed inventory.
- Safely attributable accepted orders remain registered even when the pair is incomplete.
- An observed ID alone never establishes ownership.
- Unknown outcomes never authorize retry, new admission or capital release.
- Pending overflow, malformed-event evidence and unresolved candidate-related events cannot be silently discarded as successful closure.
- Capture anomalies must be retained before `register_order` can automatically clear the processor’s pending buffer.
- Unrelated foreign events do not become RDP inventory.
- Prices and five-share initial sizes remain unchanged.
- No automatic cancellation, recovery or order adoption.
- Current market tradability, operational time sufficiency, final submit revalidation and human authorization remain outstanding execution gates.
- Existing CLI modes remain read-only.

## Acceptance Criteria

1. The exact retained processor is armed before a simulated gateway outcome.
2. Two valid acknowledgments establish two correctly scoped registry entries and retain both IDs.
3. A pre-ack MATCHED/CONFIRMED sequence replays exactly once after registration; existing confirmed accounting reports the resulting inventory correctly.
4. A late acknowledgment cannot resurrect local remaining quantity already changed by replayed events.
5. One accepted leg plus rejection/unknown preserves that leg’s ownership and marks the attempt unresolved.
6. Unknown responses retain observed IDs without fabricating ownership.
7. Missing/conflicting economics, duplicate IDs or IDs already registered elsewhere fail closed without overwriting registry entries.
8. Clean empty results clear capture; contradictory pending evidence or overflow prevents ordinary empty/success certification.
9. Partial/unknown attempts block another capture and the initial-empty admission path, including unknown outcomes with no attributable IDs.
10. Repeated completion does not duplicate registration or fills.
11. Frozen accounting and existing processor behavior remain unchanged.
12. No production placement, cancellation or signing call is introduced.
13. Required controlled tests pass with zero attempted live network operations.

## Required Tests

Use the actual gateway strict parser/placement path with fake SDK clients, the actual retained processor and controlled user events.

- Initial admission → capture arm → fake two-order gateway outcome → registration.
- Both accepted, both rejected and each direction of partial acceptance.
- Accepted-plus-unknown, malformed envelope, duplicate/conflicting IDs and POST exception.
- Observed-only IDs remain unowned.
- Trade before acknowledgment, MATCHED then CONFIRMED before acknowledgment, later confirmation and duplicate replay.
- Local order changes during registration are not overwritten afterward.
- Pending malformed maker evidence, buffer overflow and unmatched candidate-related events.
- Clean empty outcome versus contradictory buffered evidence.
- Wrong market/token/side/economics and existing-ID collision.
- Duplicate completion and overlapping attempts.
- Unresolved outcome with zero attributable IDs still blocks replacement.
- Initial admission remains execution-unauthorized.
- Existing preflight/observe-only paths never arm placement capture.
- Existing gateway, processor, accounting and capital regressions.

Any test invoking `place` must use a fake SDK or existing paper implementation. Block socket connections and DNS; forbidden real execution methods must fail immediately. Record attempted-network count.

Run targeted, affected integration, predecessor and full-suite tests when practical.

## Observability Requirements

Record one sanitized attempt outcome containing:

- Selected condition and captured generations.
- Outcome/reason.
- Safely attributable order IDs.
- Separately observed IDs.
- Pending anomaly status.
- Whether replacement remains blocked.
- Execution authorization: false.

Preserve submitted economics in existing registry entries and immutable attempt evidence. Do not log credentials, raw SDK exceptions or authentication payloads.

## Live Validation

Controlled fixtures must exercise real acknowledgment classification and processor replay together.

No live validation is required or authorized here. When execution is later enabled under human supervision, these same registry/attempt records will support acknowledgment and fill reconstruction.

## Explicit Non-Goals

- Live or paper runner activation.
- Production order submission.
- New authorization flags.
- Current market/time execution gates.
- Cancellation or unknown-outcome recovery.
- REST ownership discovery from ambiguous IDs.
- FillWindow or cycle-close controller.
- Subsequent NORMAL/REBALANCE orders.
- Capital reservation/release integration.
- Complete metrics or readiness declaration.

## Risks

- Registration can clear pending capture automatically; anomaly evidence must survive that transition.
- An acknowledgment containing original quantity must not overwrite newer local state.
- Clearing unknown capture without retaining a blocked attempt would allow duplicate risk.
- Observed IDs can be ambiguous and must remain investigation evidence.
- Existing pre-ack order events are not generally buffered like trades; authoritative order-state reconciliation remains required before future execution decisions.

## Deferred Findings

- A gated submission owner must integrate final market/time/account checks and executor ownership.
- Authoritative order-state refresh and the scoped close barrier remain uncomposed.
- Unknown outcomes require reconciliation before recovery.
- FillWindow, later NORMAL admission, REBALANCE execution and complete metrics remain outstanding.
- No initial placement capture outcome establishes MICRO_LIVE_READY.

## Human Approval Required

NO.

This contract connects existing acknowledgment and ownership mechanisms without enabling a send path or changing Frozen strategy semantics, trading authority or external interfaces.
