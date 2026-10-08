# ITER-0018 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `bc2b591d42f29e7286ffe55038bf77477de7d39b`
- HEAD and remote match.
- ITER-0017: CODE_READY, archived at `a2ce5f163b48494ad76373330c7bc182f8ca4cb7`; final Reviewer05 PASS.
- Current iteration: ITER-0018, ARCHITECT.
- Only preserved `research_outputs/` is untracked.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

**Shared-Core Change: NO.**

**FL Behavior Impact:** NONE.

**IM Dependency:** NONE.

## Problem

The retained placement owner can establish initial-pair ownership, but it cannot consume the existing cancellation/reconciliation barrier and preserve authoritative post-close inventory/capital evidence.

Implement one prerequisite: close the initial placement’s safely owned orders through the existing barrier, retaining its replacement block until closure is proven.

## Evidence / Current Behavior

- Frozen sections18/21 require Cancel → Reconcile → Update Inventory before another action.
- `RDPPlacementOwner` retains attributable acknowledgments, unknown evidence and replacement blocking.
- `close_rdp_cycle` already performs scoped cancellation, reconciliation, terminal checks and confirmed inventory projection.
- `project_rdp_capital` already validates close evidence and computes post-close capital.
- These components are not composed in the retained session.
- `session.aclose` closes input resources; it does not close orders. Preserve that distinction and existing read-only behavior.
- Gateway strict account reads already reject unavailable or malformed cumulative-order evidence.

## Existing Components Reused

- Placement-attempt evidence and owned registry.
- Retained processor, store and gateway.
- `close_rdp_cycle` and `RDPCloseResult`.
- Existing authenticated cancellation/order/trade reads.
- Confirmed inventory and capital projection.
- Existing cancellation-resistant cleanup.
- Existing `RDPMode`.

## Existing Components Modified

- `rdp_runtime.py`: explicit initial-placement close operation, operation ownership and immutable result.
- `execution/rdp_placement.py`: proof-based recording of successful closure; no generic unblock switch.
- Relevant tests.

Shared gateway, processor, accounting and reconciliation implementations remain unchanged.

## New Components Required

One immutable composed close result containing:

- Attempt identity and scoped owned IDs.
- Existing barrier evidence.
- Capital projection when certifiable.
- Closure/replacement status and reason.
- Derived next mode when supported by reliable completed evidence.
- Execution authorization: false.

A small RDP-local helper is permitted. No new cancellation or reconciliation subsystem.

## Desired Behavior

An explicit close operation uses the retained gateway and processor:

```text
safely owned IDs
→ existing cancellation/reconciliation barrier
→ confirmed inventory
→ existing post-close capital projection
→ proof-based placement closure
```

For a fully attributed, anomaly-free initial pair, complete current closure evidence can release its placement replacement block.

Partial/unknown attempts may close independently owned IDs, but closing those IDs does not prove the entire placement outcome. Their unresolved block remains.

Observed-only IDs are never cancellation targets.

## Minimal Scope

1. Accept only the session’s current resolved placement attempt. Reject ARMED, foreign, obsolete or overlapping operations.
2. Derive scope from its safely acknowledged IDs, verified against the existing registry and submitted economics.
3. Call `close_rdp_cycle` with that explicit run/closing scope and a caller-supplied absolute monotonic deadline.
4. Use a thin delegation adapter if needed to request `open_orders(strict_account=True)` while preserving the barrier’s existing `OpenOrder` interface. Delegate cancellation/order/trade reads unchanged.
5. Preserve incomplete barrier evidence without releasing ownership, reservations or replacement blocking.
6. For a complete, anomaly-free initial pair, certify capital using the same processor and existing close proof. Any certification or final-state contradiction prevents release.
7. Derive NORMAL for `abs(Delta) < 5`, REBALANCE otherwise, only from completed reliable evidence. This classification grants no next-action authority.
8. Record closure additively; preserve original acknowledgment/unknown evidence.
9. Track and drain the active close coroutine during session cancellation/closure.

No existing CLI or ordinary `aclose` path may initiate order cancellation. This is an explicit lifecycle operation for later runner composition.

## Invariants

- Cancel/reconcile mechanisms remain existing components.
- Only safely registered RDP IDs are canceled.
- No observed-ID ownership inference or broad cancel-all.
- Authoritative confirmed fills alone determine inventory/cost.
- Incomplete, stale, contradictory or interrupted evidence cannot release replacement blocking.
- Partial/unknown placement uncertainty is not erased by closing its known subset.
- Capital remains $30; no alternate reservation ledger.
- Old owned records remain available for accounting and later lifecycle evidence.
- Public/user health loss does not prevent an explicit attempt to close known risk through official HTTP. It does prevent unsupported readiness certification.
- Market expiry does not silently skip known-order reconciliation.
- Operational deadline is an attempt bound, not a strategy timer or MaxRebalanceSec.
- No order creation, retry, new live authorization or automatic next action.
- Existing read-only commands remain read-only.

## Acceptance Criteria

1. A complete initial pair closes through the actual existing barrier and retained processor/gateway.
2. Cancellation targets exactly the safely owned live IDs.
3. Cancel/fill races and partial confirmed fills enter final inventory through existing reconciliation.
4. Post-close capital uses existing proof validation; ReservedCash is zero only after complete authoritative closure.
5. NORMAL/REBALANCE classification follows the exact five-share boundary.
6. Uncertain cancellation, remaining orders, failed reads, pending confirmation, quarantine, checksum contradiction and deadline expiry retain the block.
7. Partial/unknown attempts close only their known subset and remain unresolved, even if that subset’s barrier completes.
8. Unknown attempts with no attributable IDs perform no cancellation and remain blocked.
9. Observed-only and foreign/manual orders are never adopted or canceled.
10. Complete barrier evidence cannot be reused after processor/scope evidence changes.
11. Closure recording preserves acknowledgment history and does not delete owned accounting records.
12. Overlapping calls, cancellation and session teardown cannot detach close work or release the block prematurely.
13. No preflight/observe-only path triggers cancellation.
14. Required tests pass with zero attempted live network operations.

## Required Tests

Use actual admission → placement capture/registration → close-barrier → capital composition with fake external gateways/SDKs.

- Complete pair, zero fill and partial confirmed fills.
- Fill during cancel, late confirmation and terminal checksum changes.
- Delta just below five, exactly five and either sign.
- Actual strict account-open-order producer failures and malformed cumulative fields.
- Cancellation false/exception, order still open, pending MATCHED-only evidence, failed/quarantined fill.
- Partial acceptance and known-plus-unknown outcomes: known subset closes but unresolved replacement block survives.
- Observed-only IDs, empty attributable scope and foreign/manual orders.
- Wrong attempt identity/scope and existing-registry contradiction.
- Stale close proof and late processor change prevent release.
- Deadline expiration, first/repeated cancellation, overlapping closes and teardown during a blocking gateway operation.
- Prior read-only CLI behavior and no implicit cancellation on ordinary cleanup.
- Credential sentinels absent from surfaced results/errors/logs.

All cancellation tests use fakes. Block socket connections and DNS and record attempted-network count. Run targeted, affected lifecycle/placement/runtime/capital/fill-truth regressions, predecessor regressions and full suite when practical.

## Observability Requirements

Record one sanitized close outcome with:

- Attempt/condition and scoped owned IDs.
- Barrier completion/reason.
- Placement uncertainty retained or resolved.
- Replacement-block status.
- Confirmed QY/QN/Delta and capital evidence when available.
- Next-mode classification when valid.
- Execution authorization: false.

Do not label incomplete known-subset closure as a completed quote cycle.

## Live Validation

Controlled fixtures must exercise the real composed lifecycle with fake official-interface responses.

No real orders or cancellations are authorized for development. A later human-supervised runner can use this operation for owned-order closure.

## Explicit Non-Goals

- Production submission or send authorization.
- FillWindow and cycle scheduling.
- Unknown-ID discovery/adoption.
- Recovery of unresolved partial/unknown placement coverage.
- Subsequent NORMAL admission or REBALANCE submission.
- Automatic order closure from read-only cleanup.
- Continuous account-backing refresh.
- Complete metrics or MICRO_LIVE_READY declaration.

## Risks

- Known-subset closure can be mistaken for resolution of an unknown POST.
- HTTP closure may be valid while input health is insufficient for another action.
- Late fill evidence can invalidate a previously captured proof.
- Executor work may outlive cancellation unless ownership drains correctly.
- Clearing a block by a boolean would bypass authoritative closure; proof validation is mandatory.

## Deferred Findings

- Gated submission and final market/time checks remain required.
- Full quote-cycle/FillWindow orchestration remains absent.
- Subsequent NORMAL and REBALANCE admission/execution remain uncomposed.
- Unknown placement recovery and continuous account reliability remain outstanding.
- Complete required metrics remain outstanding.

## Human Approval Required

NO.

This contract composes existing approved lifecycle mechanisms without changing strategy semantics, external interfaces or live authorization. Development validation performs no real-money order or cancellation.
