# ITER-0004 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `8a1fc11a1394625da187d854e56f01aef57c3a39`.
- Local remote-tracking branch matches HEAD.
- Working tree: preserved untracked `research_outputs/` only.
- Current iteration/stage: `ITER-0004` / `ARCHITECT`.
- Accounting and NORMAL/REBALANCE candidates are `CODE_READY`.
- Latest PASS: ITER-0003 `03_REVIEWER.md`; archived implementation `1e4105f`; receipt `04_ARCHIVE_RECEIPT.md`.
- Last documented full suite: 752 passed, 2 skipped, no exclusions.
- `MICRO_LIVE_READY`: NO. Real-money authorization: NOT_GRANTED.

Read active instructions, Frozen SOP, baseline, index and relevant prior result/PASS/receipt. Inspected actual RDP modules/tests, gateway cancellation/order reads, existing filltruth recovery, processor order updates, generic reconciliation, FL finalization and filltest cleanup.

## Problem

RDP cannot yet prove that an old cycle is canceled and reconciled before its final inventory is used for the next action.

Implement one integration prerequisite: a scoped asynchronous close barrier joining the existing gateway, exact fill recovery, authoritative processor and reviewed accounting snapshot.

## Evidence / Current Behavior

Frozen SOP sections 18, 21 and 25 require:

```text
Cancel
→ Reconcile
→ Update Inventory
→ Next Action
```

Existing components already perform the underlying operations:

- `ExecutionGateway.cancel()` cancels explicit exchange IDs.
- `open_orders()` performs strict authenticated official CLOB reads.
- `get_order()` and `get_trades()` reject malformed reads.
- `reconcile_owned_order()` verifies exact maker evidence against order cumulative quantity.
- `UserEventProcessor.on_order()` validates registered identity and updates/removes existing local orders.
- `snapshot_inventory()` derives confirmed acquisition accounting.

However, `ReconciliationOutcome.COMPLETE` alone does not prove an order is terminal or that its accepted fills are all CONFIRMED. A canceled order may also receive a late fill.

The generic quote reconciler computes actions; it does not implement this close barrier. FL/filltest cleanup demonstrates reusable operations but includes strategy-specific behavior and optimistic economics unsuitable for direct RDP reuse.

## Existing Components Reused

- Existing `ExecutionGateway` cancellation and official order/open-order/trade reads.
- Existing owned-order registry and `UserEventProcessor`.
- Existing `reconcile_owned_order()` and its deadline/retry behavior.
- Existing `normalize_order()` and `processor.on_order()`.
- Reviewed `snapshot_inventory()` and `RDPInventorySnapshot`.
- Existing fake gateway, StateStore and exact-fill recovery test patterns.

Use these implementations directly. Do not create another gateway, fill parser, ledger or recovery engine.

## Existing Components Modified

NONE.

### Shared-Core Change

NO.

### FL Behavior Impact

NONE.

### IM Dependency

NONE.

## New Components Required

- `src/polymaker/execution/rdp_lifecycle.py`.
- `tests/test_rdp_lifecycle.py`.

A small immutable close-result type is permitted. This component coordinates existing operations; it is not a replacement execution subsystem or separate trading state machine.

## Desired Behavior

Provide one asynchronous close operation accepting:

- one market;
- the existing processor and gateway;
- explicit complete run-owned order IDs;
- explicit closing-cycle order IDs, a subset of the run scope;
- a caller-supplied absolute monotonic operational deadline.

The operation never places orders.

Validate all IDs before any cancellation: they must be registered BUY orders for this market and its two tokens. Reject unknown, foreign-condition/token, SELL or invalid subset scope without touching orders.

Do not reject existing quarantine before recovery: closing must still attempt safe cleanup and reuse existing reconciliation to resolve it.

Perform this sequence:

1. Read authoritative open orders.
2. Identify open orders in the closing scope.
3. Cancel only those exact IDs through the existing gateway.
4. Obtain a fresh authoritative open-order read proving those IDs are absent.
5. Reconcile run-owned order evidence through the existing `reconcile_owned_order()` with the operational deadline.
6. Obtain fresh exact order snapshots, normalize them and apply validated terminal observations through existing `processor.on_order()`.
7. Read authoritative open orders again.
8. Without another await, recheck scoped evidence, terminal cumulative checksums and confirmation completeness, then derive the run’s inventory through existing `snapshot_inventory()`.

Completion requires:

- cancellation succeeded for any requested cancellations;
- no run-owned order remains open;
- no unowned/unscoped order exists on either market token;
- all run-order reconciliation outcomes are COMPLETE;
- fresh exact order observations prove cancellation or complete filling;
- unknown/missing order status does not establish terminality;
- terminal cumulative quantities still agree with current exact accepted-fill quantities;
- all accepted run fills are CONFIRMED;
- no run quarantine, failed/unresolved key, or unadmitted scoped fill record remains;
- the accounting projection succeeds.

For terminality, reuse normalized cancellation semantics and existing fully matched-order conventions. Tests must cover CANCELED/CANCELLED partial orders and fully matched MATCHED orders. Do not treat a zero local remaining balance or absence from `open_orders` alone as terminal proof.

Any order on these market tokens outside the supplied run scope blocks completion. Preserve it: never cancel an account’s foreign/manual order.

Return an immutable result containing completion status, explicit reason, per-order reconciliation outcomes and the final inventory only on successful completion. Incomplete results must not offer a final actionable inventory.

Read failures, uncertain cancellation, still-live orders, contradictory evidence, pending confirmation and deadline exhaustion fail closed. They must not be converted into zero fills or clean closure.

Attempt available exact reconciliation after an uncertain cancellation when time permits, but never mark the barrier complete on that invocation. Preserve recovered fills; do not roll back truth because closure failed.

Use an overall asynchronous deadline bound covering reads, cancellation and reconciliation. Do not abandon background reconciliation after timeout. Preserve task cancellation propagation.

The deadline limits an operational close attempt. It is not MaxRebalanceSec, a strategy stop timer, or a new StopReason. A later runtime may retry incomplete closure while adding no risk.

Document the caller obligation to serialize trading decisions and prevent submissions while closing. This result proves this closure observation only; later execution must recheck current safety gates.

## Minimal Scope

Only:

```text
src/polymaker/execution/rdp_lifecycle.py
tests/test_rdp_lifecycle.py
```

No engine/CLI, strategy policy, gateway, processor, parser, accounting, configuration or dependency changes.

No placement, startup discovery, full cycle controller, FillWindow or REBALANCE loop.

## Invariants

- Only explicit registered closing IDs may be canceled.
- No new order is created.
- Cancellation precedes reconciliation and final inventory.
- Cancel acknowledgment is not fill truth.
- Absence from open orders is not sufficient terminal proof.
- Existing processor/recovery remain authoritative.
- Confirmed partial fills surviving cancellation are retained exactly once.
- Pending or contradictory fill state cannot authorize next action.
- Foreign/manual orders are preserved.
- Failure preserves authoritative evidence and returns no completed inventory.
- No RPC, merger, settlement or third-party interface is used.
- FL/IM and existing send gates remain unchanged.

## Acceptance Criteria

1. Valid zero-fill closure cancels, proves terminality, reconciles and returns zero inventory.
2. Confirmed partial fills and late cancel/fill races appear exactly once in final accounting.
3. Fully filled orders close without an unnecessary cancellation.
4. COMPLETE reconciliation with MATCHED/MINED/RETRYING evidence remains incomplete.
5. A later confirmation/recovery permits a new close attempt to complete.
6. Wrong scope fails before cancellation; foreign/manual orders are preserved and block completion.
7. Uncertain cancellation, remaining live orders, unknown terminal status, checksum gaps, read failure and quarantine fail closed.
8. Fresh terminal processor observations remove canceled local orders through existing APIs.
9. Evidence changing during awaits is revalidated after the final await.
10. Deadline exhaustion leaves no continuing background task and grants no completion.
11. No placement, alternative ledger/parser/recovery path or shared-core change occurs.
12. Targeted, affected and full-suite regressions pass.

## Required Tests

Use real StateStore/processor and fake official-interface gateway methods. Do not call live cancellation.

Required cases:

- Zero-fill canceled cycle.
- Confirmed partial-fill cancellation.
- Fill discovered during cancellation or final REST recovery.
- Duplicate late WS/REST evidence.
- Fully filled MATCHED order with CONFIRMED trades.
- COMPLETE filltruth outcome with nonconfirmed trades: incomplete.
- Recovery/confirmation followed by successful repeated closure.
- Cancel false/exception and order still present.
- Failed/malformed open-order, order and trade reads.
- Terminal cumulative checksum changes during another await.
- Missing/unknown terminal status.
- Quarantine resolved through existing recovery; unresolved contradiction remains blocked.
- Unadmitted CONFIRMED-only record blocks until existing recovery admits it.
- Unknown/foreign/SSELL scope and invalid subset fail before mutation.
- Foreign/manual token order is never canceled.
- Already-closed earlier run orders remain included in final inventory.
- Local stale resting orders removed by validated processor order updates.
- Timeout during each operation category; cancellation propagates and no work continues afterward.
- Call-order assertions establish cancellation → recovery → final accounting.
- A fake placement method raises if invoked.

Use existing numeric tolerances for exchange cumulative quantity comparisons. Do not add trading thresholds.

Run:

```bash
uv run pytest -q tests/test_rdp_lifecycle.py
uv run pytest -q tests/test_filltruth.py tests/test_state.py tests/test_execution.py tests/test_filltest.py tests/test_rdp_accounting.py tests/test_rdp_quotes.py
uv run pytest -q
```

Record exact results/failures and inspect complete diff, status, relevant untracked files and whitespace checks. Preserve unrelated historical assets.

## Observability Requirements

The close result must distinguish cancellation uncertainty, open-order conflict, terminal-state uncertainty, reconciliation failure, pending confirmation and operational deadline.

Include affected order IDs and per-order recovery outcomes where available. Final inventory is exposed only for complete closure.

No new persistent telemetry system is required.

## Live Validation

No live actions are authorized.

After integrated readiness and human authorization, validate one supervised market’s cycle close against cancellation acknowledgments, fresh official order/trade evidence, confirmed quantities and final inventory. Demonstrate that a late fill is retained before another action.

## Explicit Non-Goals

- Full runnable RDP CLI.
- Order placement or authorization.
- CapitalGate, Book Filter, quote policy or strategy timing.
- NORMAL/REBALANCE/STOP transitions.
- FillWindow and repricing loops.
- Account-position startup baseline or cash reservation.
- Market-end settlement.
- Metrics beyond close-result evidence.
- Changes to FL/IM or shared core.
- Readiness declaration.

## Risks

- Existing COMPLETE recovery can include optimistic fills; confirmation requires an additional gate.
- Live evidence can change during awaited reads; stale earlier checks must not release closure.
- Cancellation failure does not erase fills already recovered.
- Caller omission of an owned order cannot be inferred solely from a supplied set; conflicting live market-token orders must block, and later runtime ownership construction remains required.

## Deferred Findings

Book Filter, CapitalGate/repair headroom, startup reliability, trading controller, metrics, market termination and single-market CLI remain outstanding.

Their behaviorally meaningful ambiguities must be resolved in focused later contracts. This closure prerequisite needs no additional strategy decision.

## Human Approval Required

NONE for implementation and fake-gateway testing.

Production cancellation capability is implemented for the future authorized runtime, but development agents must not invoke it against live funds. Real-money trading and cancellation remain unauthorized.
