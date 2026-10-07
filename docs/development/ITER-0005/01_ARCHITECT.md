# ITER-0005 — Architect Contract

## Status

ARCHITECT_BLOCKED

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `3b2f1f3cff9ac73bfc5ffa48ff162e257ddb68f1`.
- Local remote-tracking branch matches HEAD.
- Working tree: preserved untracked `research_outputs/` only.
- Current iteration/stage: `ITER-0005` / `ARCHITECT`.
- ITER-0001 through ITER-0004 are `CODE_READY` and archived.
- Latest final PASS: ITER-0004 `07_REVIEWER_REREVIEW_02.md`.
- Latest implementation archive: `c92a2a0`; receipt: `08_ARCHIVE_RECEIPT.md`.
- Latest Worker full-suite result: 795 passed, 2 skipped, no exclusions. Final Reviewer independently checked the repaired targeted suite and inspected the final tree.
- `MICRO_LIVE_READY`: NO.
- Real-money authorization: NOT_GRANTED.

Read active contract, Frozen SOP, baseline, index and prior result/PASS/receipt. Inspected actual accounting, quote builders, close barrier, existing risk management and official collateral-read methods. No changes or live actions were performed.

## Problem

The next required gate is NORMAL CapitalGate, including preserving subsequent inventory-repair capability.

Frozen SOP states this survival requirement but does not specify the future repair-price assumption used to decide whether a NORMAL entry is affordable. The choice changes which otherwise valid NORMAL entries execute.

A human decision is required before implementing that portion of CapitalGate.

## Evidence / Current Behavior

Frozen SOP section 4 requires:

1. FreeCash sufficient for new orders.
2. Inventory plus resting and new orders within $30.
3. NORMAL must not exhaust subsequent inventory-repair capability.

Sections 23–24 define five-share REBALANCE orders at current BestAsk minus TickSize. Section 26 requires enough FreeCash for a minimum legal repair order.

No approved durable definition establishes whether NORMAL must protect repair funds at current prices or against subsequent legal price movement. Earlier contracts explicitly deferred repair-headroom policy.

Actual implementation has:

- confirmed acquisition accounting;
- exact NORMAL and REBALANCE candidates;
- cancel/reconcile close barrier;
- no RDP CapitalGate or protected repair-cash calculation.

Legacy `RiskManager` uses marked exposure, scaling and unrelated limits. It does not resolve this requirement and cannot supply RDP policy by inheritance.

Concrete divergent behavior:

```text
Starting capital:                 $30
Existing balanced InventoryCost:  $23
FreeCash:                         $7
ReservedCash:                     $0
Delta:                            0

Current YES bid/ask/tick: .30 / .31 / .01
Current NO bid/ask/tick:  .60 / .61 / .01

Frozen NORMAL candidates:
5 YES @ .29
5 NO  @ .59

New-order total notional: $4.40
```

The first two capital conditions pass.

If only the five NO shares fill, cancellation releases the unfilled YES reservation:

```text
Additional confirmed cost: $2.95
Remaining cash:             $4.05
Delta:                      -5
```

A current-price YES repair costs:

```text
5 × (.31 - .01) = $1.50
```

A later legal YES ask of `.99` requires:

```text
5 × (.99 - .01) = $4.90
```

The same NORMAL entry preserves repair capability under the current-price interpretation but cannot fund that later legal repair price. Choosing the guarantee changes acceptance, rather than merely changing implementation structure.

## Existing Components Reused

After the decision:

- `RDPInventorySnapshot` for confirmed acquisition cost and Delta.
- Existing quote candidates for proposed order quantities/prices.
- Existing owned-order/accepted-fill evidence.
- Existing official CLOB collateral/order reads.
- Existing close barrier before releasing canceled exposure.

No alternative ledger, gateway or runtime interface is justified.

## Existing Components Modified

NONE authorized by this blocked contract.

### Shared-Core Change

NO changes approved.

### FL Behavior Impact

NONE.

### IM Dependency

NONE.

## New Components Required

A small RDP-specific capital projection/gate will be needed after the policy decision. Its precise scope and acceptance criteria must be frozen in a new Architect revision artifact.

No implementation is authorized by this artifact.

## Desired Behavior

Apply the first two Frozen capital conditions and the human-approved interpretation of protected repair capability.

Retain:

- $30 capital assumption;
- actual confirmed acquisition accounting;
- reservation accounting distinct from inventory cost;
- existing five-share repair behavior;
- official HTTP/WebSocket-only runtime architecture;
- fail-closed treatment of unreliable capital/order/fill state.

Do not silently choose a repair-price assumption, disable the third condition, or substitute legacy risk policy.

## Minimal Scope

Resolve exactly one strategy-semantic question: the future-price guarantee required by NORMAL repair headroom.

Then produce a new Architect revision for the minimal capital-accounting/gate implementation. Do not alter the previous immutable contract or Frozen SOP without explicit human authority.

## Invariants

- Capital survival remains the first principle.
- Resting orders are not confirmed InventoryCost.
- Cancel acknowledgment alone cannot release uncertain economic exposure.
- NORMAL cannot proceed under an undefined repair-headroom policy.
- REBALANCE quantity/price semantics remain frozen.
- No new state, predictive filter, economic stop condition or MaxRebalanceSec is introduced.
- Existing reviewed code and historical assets remain preserved.

## Acceptance Criteria

For unblocking architecture:

1. A durable human-approved decision identifies the repair-price guarantee.
2. It specifies whether funds must survive future legal price movement.
3. The divergent $7-cash example has an unambiguous expected acceptance result.
4. The new revision defines the smallest concrete gate implementation without unrelated scope.

The iteration is not CODE_READY, and no Worker should be spawned under this blocked artifact.

## Required Tests

After the decision, the revised contract must require:

- exact new-order cash sufficiency and $30 boundary tests;
- confirmed cost versus resting/pending reservation distinction;
- NORMAL partial-fill outcomes and cancellation release;
- the concrete divergent example;
- both possible deficient sides and existing fractional Delta;
- REBALANCE affordability;
- unreliable cash/order/fill state failing closed;
- affected and full-suite regressions.

No tests were executed or changed during this read-only architecture inspection.

## Observability Requirements

The eventual gate must expose FreeCash, ReservedCash, InventoryCost, proposed notional, protected repair requirement and explicit rejection reason.

The human decision must be durable repository authority rather than session-only context.

## Live Validation

No live action is necessary to resolve this specification question.

After readiness and human authorization, real-market cash, reservation and repair outcomes can validate the approved rule. Live experimentation cannot replace defining the rule before initial execution.

## Explicit Non-Goals

- Implementing an unapproved repair reserve.
- Assuming current-price or worst-price policy silently.
- Capital optimization.
- Book Filter, controller, CLI or metrics implementation.
- Runtime/external-interface changes.
- Real-money orders or cancellation.
- Readiness declaration.

## Risks

- Current-price protection may permit a NORMAL entry that cannot finance later legal repair.
- A price-independent guarantee can reject additional entries, affecting strategy activity.
- Treating either choice as an ordinary storage/calculation detail would hide a material trading-behavior decision.

## Deferred Findings

Book Filter, timing/reliability integration, controller, runner and metrics remain outstanding. They are not included in this decision.

Official collateral response validation and pending-exposure treatment require focused implementation review after the capital semantics are approved.

## Human Approval Required

**Exact blocking question:**

Must NORMAL protect enough cash, after any permitted partial-fill outcome and cancellation of unfilled orders, to fund one future five-share REBALANCE order at **any legal binary maker price**, using a price-independent upper bound of **$5**, or is protection evaluated only at the **current BestAsk minus TickSize**?

**Recommended minimal decision:** approve the price-independent $5 repair bound. The later Architect can derive the required partial-fill calculation without adding strategy states or changing quote sizes.

This recommendation is not an approved policy.

After the decision is recorded, architecture can immediately resume with a minimal capital-accounting/CapitalGate contract and the normal Worker → Reviewer loop.

Program stop classification: `HUMAN_DECISION_REQUIRED`.
