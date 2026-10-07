# ITER-0001 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Base HEAD: `fd514299b453d2cd56b7afa093dabbbf6340f99b`.
- `rdp-phase0-framework-ready` points to this HEAD.
- Local `origin/rdp-phase0-dev` points to this HEAD.
- RDP baseline: `rdp-phase0-baseline`, commit `6a5ed93`.
- FL predecessor: `fl-reconstructed-baseline`, commit `76fe93f`.
- Latest completed prerequisite: `ITER-0000`, `READY_FOR_RDP_DEVELOPMENT`.
- Current iteration: `ITER-0001`, stage `ARCHITECT`.
- Observed working tree: Orchestrator changes to `docs/development/ITERATION_INDEX.md` and preserved untracked `research_outputs/`. No source/test changes observed.
- Baseline documents record an unfiltered suite of 656 passed, 2 skipped. This inspection did not rerun that suite; Worker must verify the resulting tree.
- No real-money authorization exists.

Read: `AGENTS.md`, `docs/strategy/RDP_PHASE0_FROZEN.md`, `docs/development/BASELINE.md`, `docs/development/ITERATION_INDEX.md`, and `docs/development/ITER-0000/RESULT.md`.

## Problem

RDP lacks a single-market confirmed-fill inventory/accounting projection implementing the Frozen SOP’s acquisition-cost and settlement-economics formulas.

The smallest missing prerequisite is to derive an immutable RDP accounting snapshot from existing owned-maker-fill evidence. Later quoting, capital and lifecycle work needs this foundation; implementing those consumers is outside this iteration.

## Evidence / Current Behavior

1. `src/polymaker/domain.py` already defines `MarketMeta`, `Side`, `TradeState`, and exchange token identities.
2. `src/polymaker/state/store.py` maintains the owned-order registry, persisted fill deduplication, positions and open orders. `apply_fill()` changes positions optimistically at MATCHED.
3. `src/polymaker/state/tracker.py` already owns exact `(trade_id, maker_order_id)` fill identity, ownership validation, lifecycle advancement, accepted-fill evidence and quarantine.
4. `UserEventProcessor.economic_fills_for_orders()` returns applied, non-quarantined, nonfailed economic fills. Its output can include MATCHED and MINED records; it is not a confirmed-only accounting interface.
5. `src/polymaker/execution/filltruth.py` already recovers exact owned-order evidence through official REST order/trade reads and the existing processor.
6. `TradingEngine._favorite_economics()` derives FL economics from existing fill evidence, but partitions it into first-leg/pair-leg concepts. RDP must aggregate YES/NO inventory directly.
7. `src/polymaker/risk/manager.py` uses marked inventory and legacy risk behavior. Its inventory value is not Frozen RDP `InventoryCost`.
8. Existing tests cover duplicate fills, exact maker ownership, REST recovery, failures, cancellation followed by late fills, and out-of-order confirmation.
9. Searching `src/` and `tests/` found no RDP implementation.

Frozen SOP sections 2–12 and 35 define the required inventory and accounting formulas. This contract introduces no new trading policy.

## Existing Components Reused

- `MarketMeta` and its YES/NO token mapping.
- `Side.BUY` and `TradeState.CONFIRMED`.
- `UserEventProcessor.owned_record()` for validating the explicitly supplied order scope.
- `UserEventProcessor.economic_fills_for_orders()` as the accepted, deduplicated evidence source.
- Existing processor quarantine information for rejecting unreliable scoped evidence.
- Existing StateStore/processor fixtures and fake REST gateways for integration tests.
- Existing `execution.filltruth` recovery paths in tests, without modifying them.

The snapshot must remain a projection of the existing evidence owner. It must not maintain another mutable fill ledger, duplicate registry, incremental inventory counter, or reconciliation state machine.

## Existing Components Modified

No existing production component requires modification.

Existing test files need not be modified; add RDP-specific tests using existing components.

### Shared-Core Change

NO.

### FL Behavior Impact

NONE. Preserve existing optimistic positions, callbacks, execution, FavoriteFirst economics, risk policies and CLI behavior.

### IM Dependency

NONE. Do not import or copy IM implementation or deferred IM shared-core changes.

## New Components Required

- `src/polymaker/strategy/rdp_accounting.py`: a small RDP-specific, read-only inventory/accounting projection and immutable snapshot.
- `tests/test_rdp_accounting.py`: formula and existing-tracker integration tests.

No new package, runtime dependency, service, external interface or data directory is required.

## Desired Behavior

Provide a synchronous interface accepting:

- one `MarketMeta`;
- the existing `UserEventProcessor`;
- an explicit set of owned order IDs belonging to that market’s RDP run.

The Worker may choose ordinary class/function names.

Validate every supplied order ID against the existing registry. Reject unknown IDs, another condition, another token, or a registered SELL order. The explicit order set establishes the caller’s run scope; do not silently aggregate all account orders.

Reject a snapshot request containing a scoped quarantined order or unresolved/quarantined fill key. Do not silently report an apparently complete snapshot by dropping contradictory scoped evidence.

For the accepted evidence returned by the existing processor, count only records whose lifecycle status is exactly `TradeState.CONFIRMED`. MATCHED, MINED, RETRYING and FAILED evidence must not contribute inventory or acquisition cost.

Derive:

```text
QY = sum(confirmed YES BUY fill quantities)
QN = sum(confirmed NO BUY fill quantities)

CY = sum(confirmed YES BUY fill quantity × actual fill price)
CN = sum(confirmed NO BUY fill quantity × actual fill price)

Delta = QY - QN
InventoryExposure = abs(Delta)
C_total = CY + CN
InventoryCost = C_total

AvgYESCost = CY / QY when QY > 0, otherwise None
AvgNOCost = CN / QN when QN > 0, otherwise None

BalancedQty = min(QY, QN)
ResidualYES = max(Delta, 0)
ResidualNO = max(-Delta, 0)
ResidualQty = abs(Delta)

BalancedYESCost = BalancedQty × AvgYESCost
BalancedNOCost = BalancedQty × AvgNOCost
BalancedCost = BalancedYESCost + BalancedNOCost
ResidualCost = C_total - BalancedCost

LockedPnL = BalancedQty - BalancedCost
ResidualWorstLoss = ResidualCost
ResidualBestPnL = abs(Delta) - ResidualCost

WorstSettlementPnL = BalancedQty - C_total
BestSettlementPnL = max(QY, QN) - C_total
```

When `BalancedQty` is zero, both balanced-side costs and `BalancedCost` are zero without multiplying an unavailable average.

Provide a small calculation on the snapshot for an explicitly supplied resolved winning token:

```text
MarketPnL = winning-token quantity - C_total
```

Reject a winning token outside the market. Do not infer resolution, fetch a result, or report hypothetical winner calculations as realized profit.

Before counting a confirmed event, validate finite quantity/price, positive quantity, a valid binary price strictly between zero and one, BUY side, market token membership, and consistency with the scoped order identity. Reject invalid evidence without modifying authoritative state.

Use actual fill prices, not posted prices, BestBid, marks, account averages or resting-order notionals.

An empty valid order scope produces zero inventory/cost, zero derived economics, and unavailable side averages.

The projection is an accounting view, not a certificate that order reconciliation, account reconciliation or pending-fill exposure is complete. A future runtime must preserve those independent gates. Do not make StateStore’s optimistic positions agree by altering them.

## Minimal Scope

Permitted implementation changes:

```text
src/polymaker/strategy/rdp_accounting.py
tests/test_rdp_accounting.py
```

Implement the snapshot, scoped read-only adapter, mathematical derivations and tests only.

Do not wire this component into `TradingEngine`, CLI, configuration, callbacks or order submission in this iteration.

Do not add mutable RDP inventory storage. Recomputing from the explicitly scoped existing accepted-fill evidence is sufficient.

If an existing public interface proves insufficient, return `WORKER_BLOCKED` with the exact deficiency rather than modifying shared core.

## Invariants

- Inventory and acquisition cost derive only from accepted confirmed fills.
- Exact maker-fill deduplication remains owned by the existing tracker.
- One trade ID may legitimately contribute through distinct maker order IDs.
- Resting orders never contribute `CY`, `CN` or `InventoryCost`.
- Account/optimistic position changes cannot independently change this accounting view.
- The snapshot does not mutate the processor, StateStore or registry.
- No Pair State is introduced.
- `QY = BalancedQty + ResidualYES`.
- `QN = BalancedQty + ResidualNO`.
- At most one residual side is positive.
- `BalancedCost + ResidualCost = C_total`.
- `WorstSettlementPnL = LockedPnL - ResidualCost`.
- Both possible winner calculations lie within the reported settlement bounds.
- Negative `LockedPnL` is legitimate evidence and must not be clamped, rejected or converted into a STOP decision.
- Existing FL behavior and safety controls remain unchanged.
- Frozen SOP and runtime external interfaces remain unchanged.

Floating-point comparisons in tests may use a tight numerical tolerance. The implementation must not round quantities or cash to whole shares or cents.

## Acceptance Criteria

1. The new module exposes an immutable, single-market inventory/accounting snapshot with all quantities and economics specified above.
2. Snapshot construction uses the existing processor and exact explicit owned-order scope.
3. Unknown, foreign-condition, foreign-token and SELL order scopes fail explicitly.
4. MATCHED/MINED/RETRYING evidence and resting orders do not affect confirmed RDP inventory or cost.
5. MATCHED followed by CONFIRMED contributes exactly once; duplicate and older replay events do not increase totals.
6. Separate maker orders sharing a trade ID contribute independently.
7. Quarantined scoped orders/fill keys and invalid confirmed economics fail explicitly.
8. Formula tests establish empty, one-sided, balanced, residual YES, residual NO, fractional and multiple-price cases.
9. Negative locked economics remain representable.
10. Explicit YES/NO winner calculations implement Frozen `MarketPnL`; no resolution or cashflow is inferred.
11. Repeated snapshot reads have no state or callback side effects.
12. Existing state, filltruth, FL strategy and full-suite regression tests pass.
13. No shared-core, FL/IM, configuration, dependency, dataset or runtime interface changes appear in the actual diff.

## Required Tests

Add independent numeric expectations rather than reproducing the implementation’s formulas in every assertion.

At minimum:

- Empty snapshot: zero quantities/cost/economics and `None` averages.
- One-sided YES and one-sided NO inventory: balanced costs zero, correct residual, bounds and unavailable opposite average.
- Multiple fill prices on one side: actual cumulative cost and weighted average.
- Balanced profitable inventory.
- Balanced inventory whose combined average cost exceeds one: negative `LockedPnL` retained.
- Residual YES and mirrored residual NO cases.
- Fractional quantities and prices retained.

Use this fixed residual fixture:

```text
YES: 2 @ 0.20, then 3 @ 0.40
NO: 3 @ 0.50

QY = 5
QN = 3
CY = 1.60
CN = 1.50
Delta = 2
BalancedQty = 3
AvgYESCost = 0.32
AvgNOCost = 0.50
BalancedCost = 2.46
ResidualCost = 0.64
LockedPnL = 0.54
WorstSettlementPnL = -0.10
BestSettlementPnL = 1.90
YES-winning MarketPnL = 1.90
NO-winning MarketPnL = -0.10
```

Integration cases must use a real `StateStore` and `UserEventProcessor`:

- Register an order; MATCHED changes legacy positions but leaves RDP confirmed inventory zero; CONFIRMED changes the projection.
- Duplicate MATCHED/CONFIRMED and out-of-order lifecycle replay count once.
- Distinct maker order IDs sharing one trade ID both count.
- Exact confirmed REST recovery through existing `reconcile_owned_order` contributes once across repeated recovery.
- Resting-order and unrelated account-position changes leave RDP accounting unchanged.
- Failed/contradictory scoped evidence produces explicit rejection.
- Another registered market/run outside the supplied order set is excluded.
- Invalid scope and confirmed economics are rejected.
- Snapshot reads do not mutate positions, orders, fill evidence or callbacks.

A CONFIRMED-only WS event not yet admitted into the existing processor’s applied evidence must not be synthesized into a new fill by this module. Existing recovery owns admission.

Run in increasing scope:

```bash
uv run pytest -q tests/test_rdp_accounting.py
uv run pytest -q tests/test_state.py tests/test_filltruth.py tests/test_favorite_first.py tests/test_favorite_first_review.py
uv run pytest -q
```

Record exact commands/results and any failure names. Inspect untracked files, `git diff --stat`, and `git diff --check`. Preserve the known pre-existing Frozen SOP whitespace evidence; do not repair it.

## Observability Requirements

The immutable snapshot must expose the Frozen metric names through documented fields or a clear field mapping.

Document:

- confirmed-only inclusion;
- unavailable averages;
- acquisition cost versus locked and settlement economics;
- explicit winning-token input for final PnL calculation;
- the requirement for separate reconciliation/pending-state safety gates.

Explicit failures must identify the offending order or evidence where practical.

No new logging service, persistent writer, cycle telemetry or live observer is required in this iteration.

## Live Validation

No live order, cancellation or authenticated network operation is required or authorized.

After the integrated runtime reaches `MICRO_LIVE_READY` and human authorization is granted, validate this component against one supervised BTC5m market’s exact owned confirmed fills:

- independently sum YES/NO quantities and paid cost;
- compare `QY`, `QN`, `CY`, `CN`, `Delta` and economic fields;
- confirm pending/resting orders are excluded from acquisition cost;
- compare final `MarketPnL` after official resolution.

This future comparison validates accounting correctness, not strategy edge.

## Explicit Non-Goals

- NORMAL/REBALANCE/STOP execution or transition logic.
- PriceGate, Book Filter, FillWindow and quote sizing.
- CapitalGate, reservation management, cash balances or repair-headroom policy.
- Order placement, cancellation, lifecycle or recovery redesign.
- CLI/runtime entrypoint or market discovery integration.
- Dynamic TickSize/minimum-size behavior.
- Cycle, timing or attribution metrics.
- Market-resolution ingestion or settlement execution.
- Persistence/restart changes.
- FL/IM behavior changes.
- New external interfaces, dependencies or datasets.
- Claiming `MICRO_LIVE_READY`.

## Risks

- Existing economic-fill access includes optimistic statuses. Missing the explicit confirmation filter would violate Frozen accounting.
- Existing optimistic positions may temporarily differ from confirmed RDP inventory. This difference must remain visible rather than be “corrected” by mutating FL state.
- Newly admitted CONFIRMED status may not be present in applied evidence until existing recovery completes; this projection must not replace recovery.
- A confirmed-only accounting view cannot itself authorize new orders while pending execution exposure is unresolved.
- Later runtime construction must supply the complete owned order set for its run; this component cannot discover omitted order IDs.
- Floating-point arithmetic requires tight tolerance and no premature rounding.

## Deferred Findings

- Capital reservation and repair-headroom policy are not implemented. Any behaviorally meaningful ambiguity must be considered by the later minimal CapitalGate contract.
- Full RDP lifecycle integration must account for optimistic fills, pending confirmations, quarantine, account mismatches and restart recovery before authorizing new risk.
- Existing FL engine contains legacy RPC/merge behavior excluded from approved RDP runtime interfaces. A later integration contract must prove RDP isolation without changing unrelated FL behavior.
- CLI, official single-market selection/data, NORMAL, REBALANCE, safe termination and required runtime metrics remain absent.
- The projection alone does not complete the single-market deliverable.

These findings do not block the accounting prerequisite.

## Human Approval Required

NONE for this iteration.

The approved work implements existing Frozen formulas through a read-only projection, introduces no trading policy and changes no shared core or external runtime interface.

Real-money trading remains unauthorized.
