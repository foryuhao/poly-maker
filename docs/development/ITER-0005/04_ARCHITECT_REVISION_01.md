# ITER-0005 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `49291f563757dfd3dc858fac5f47b5a488893d3c`.
- Local remote-tracking branch matches HEAD.
- Working tree: preserved untracked `research_outputs/` only.
- Stage: `ITER-0005` / `ARCHITECT_REVISION`.
- Accounting, NORMAL/REBALANCE candidates and close barrier are CODE_READY.
- Latest reviewed full-suite result: 795 passed, 2 skipped, no exclusions.
- Human decision: `03_HUMAN_DECISION.md`, approved and archived at HEAD.
- MICRO_LIVE_READY: NO. Real-money authorization: NOT_GRANTED.

Read active instructions, Frozen SOP, baseline/index, existing blocked contract/result/handoff, approved human decision, actual RDP components and relevant tests.

This revision resolves the earlier blocker. Previous artifacts remain immutable.

## Problem

Implement one prerequisite: a read-only RDP capital projection and CapitalGate using existing authoritative inventory/order evidence and the approved NORMAL $5 future repair reserve.

## Evidence / Current Behavior

Frozen section 4 requires affordable new orders, total committed capital within $30, and preserved NORMAL repair capability.

The approved decision specifies:

- NORMAL preserves $5 for one future five-share repair.
- Current repair price cannot reduce that amount.
- REBALANCE may use the reserve.
- REBALANCE pricing remains unchanged.

Current code provides confirmed acquisition accounting, quote candidates and a close barrier, but no RDP capital projection/gate.

Existing optimistic positions and open-order remaining quantities can diverge while fills await confirmation. Capital availability must therefore not be inferred from remaining orders alone.

Legacy RiskManager’s marked exposure/scaling does not implement the approved policy.

## Existing Components Reused

- `snapshot_inventory()` and `RDPInventorySnapshot`.
- Existing owned-order registry and processor fill evidence.
- Existing `OpenOrder`, `Quote`, `Side`, `TradeState`, market identity.
- Existing `RDPQuoteDecision` candidates.
- Existing complete `RDPCloseResult` as proof of canceled/reconciled exposure release.
- Existing fake gateway and processor fixtures.

## Existing Components Modified

NONE.

### Shared-Core Change

NO.

### FL Behavior Impact

NONE.

### IM Dependency

NONE.

## New Components Required

- `src/polymaker/risk/rdp_capital.py`.
- `tests/test_rdp_capital.py`.

Small immutable capital and admission-result types are permitted. No mutable reservation ledger, gateway or additional reconciliation subsystem.

## Desired Behavior

### Capital projection

Provide a synchronous read-only interface using:

- one market;
- existing processor;
- complete explicit run-owned order scope;
- supplied authoritative market-token open-order snapshot;
- optional prior complete `RDPCloseResult`;
- explicit caller market/order-state reliability indication.

No network operation occurs here. Later runtime supplies fresh official snapshots and evaluates feed/read freshness.

Validate registered order ownership, condition, token, BUY side and finite submitted economics.

Independently inspect scoped processor evidence. Reject pending MATCHED/MINED/RETRYING fills, failed/quarantined/unresolved evidence, and unadmitted scoped records. A caller reliability flag cannot bypass these checks.

For each supplied resting order:

- require a unique registered run ID;
- require correct token, BUY side, submitted price and valid positive remaining quantity;
- require accepted confirmed quantities to agree with submitted quantity minus remaining quantity, using the existing exchange checksum tolerance;
- reject malformed or contradictory economics/state.

Unknown/manual orders on either market token fail closed and are never mutated. The caller may supply only the market-token portion of an account snapshot; unrelated-token orders are outside this single-market projection.

Every run ID must be represented either by authoritative resting evidence or by complete prior close evidence. Absence from open orders alone cannot release its reservation.

A prior close result may cover an earlier subset of the current run:

- it must be complete with inventory and COMPLETE per-order outcomes;
- its order scope must be a subset of this run, with no duplicate identities;
- its covered orders cannot currently be open;
- a fresh existing inventory projection for its covered scope must still agree with the recorded closed inventory;
- current failed, pending or unadmitted evidence invalidates release.

Do not manufacture complete closure or modify processor quarantine.

For reliable accepted inputs:

```text
Capital = $30.00

InventoryCost = existing confirmed C_total
ReservedCash = sum(resting order remaining quantity × submitted BUY price)
FreeCash = Capital - InventoryCost - ReservedCash
```

The five-dollar admission reserve is not an additional resting reservation or acquisition cost. Keep it separate.

Reject overcommitted/invalid capital rather than clamping negative cash to zero.

Return an immutable snapshot exposing Capital, InventoryCost, ReservedCash and FreeCash. Unreliable input yields explicit failure and no certified capital snapshot.

### CapitalGate

Evaluate an existing candidate for explicitly selected NORMAL or REBALANCE using the certified projection and its inventory.

Validate mode, market/token association, BUY economics, candidate presence and post-only requirement. Reject malformed or empty candidates. Check the mode against Frozen Delta boundaries. Do not recalculate market prices or change quantities.

Define:

```text
NewNotional = sum(candidate price × quantity)

FreeCashAfterNew = FreeCash - NewNotional

CommittedCapitalAfterNew =
    InventoryCost + ReservedCash + NewNotional
```

Admission requires:

```text
FreeCash >= NewNotional
CommittedCapitalAfterNew <= $30
```

NORMAL additionally requires:

```text
FreeCashAfterNew >= $5.00
```

REBALANCE requires no additional five-dollar floor:

```text
FreeCashAfterNew >= $0
```

Exactly sufficient funds pass. No current repair-price parameter may reduce the NORMAL reserve.

Use decimal-safe money arithmetic without rounding to whole cents or adding a broad admission tolerance. Unsupported/nonfinite arithmetic fails closed.

Return an immutable capital-admission result with explicit reason, proposed notional, remaining cash, committed capital and applicable protected amount.

A passed capital gate certifies capital eligibility only; all other strategy, lifecycle, reliability and live-authorization gates remain mandatory.

## Minimal Scope

Only:

```text
src/polymaker/risk/rdp_capital.py
tests/test_rdp_capital.py
```

No existing production/test modifications, runtime wiring, authenticated reads, submission, cancellation, configuration or dependency changes.

The approved full proposed notional plus $5 admission floor is sufficient to preserve $5 under partial fills: spent fills replace reservations, and reliable cancellation releases unspent reservations.

## Invariants

- Confirmed fills alone contribute InventoryCost.
- Resting/pending exposure never disappears through an optimistic order update.
- Uncertain cancellation cannot release funds.
- Existing close evidence owns release proof.
- No duplicated mutable capital, order or fill ledger.
- `FreeCash + ReservedCash + InventoryCost = $30`.
- NORMAL admission leaves at least $5 after reserving the entire proposed candidate.
- REBALANCE may consume that amount.
- No current-price discount or economics-based STOP rule.
- No authoritative-state mutation or execution authority.
- Existing reviewed strategy/FL/IM behavior remains unchanged.

## Acceptance Criteria

1. Reliable confirmed/open-order evidence produces the specified capital identity.
2. Partial confirmed fills move actual cost into InventoryCost and leave remaining posted notional reserved.
3. Pending/unadmitted fills reject even when caller reliability is true.
4. Missing, contradictory, foreign/manual or unproved-absent orders fail closed.
5. Complete current close evidence releases unfilled reservation; uncertain/stale evidence cannot.
6. NORMAL admits exact `$5` remaining and rejects any amount below it.
7. REBALANCE can spend into that reserve, including exact cash exhaustion.
8. Existing $7 fixture rejects NORMAL independently of current repair price.
9. Existing confirmed fractional inventory/candidate outputs remain compatible.
10. Invalid arithmetic, mode, candidate or overcommitment cannot admit risk.
11. Projection/gate reads preserve inputs and processor/store state.
12. Targeted, affected and full-suite regressions pass within scope.

## Required Tests

Use independent numeric expectations and real processor/accounting integration.

Required cases:

- Empty run: FreeCash 30, zero inventory/reservation.
- Confirmed cost plus two resting orders: exact identity and no double counting.
- Confirmed partial fill with price improvement: actual fill cost plus posted remaining reservation.
- Optimistic matched quantity reducing authoritative order remaining while unconfirmed: rejection.
- MINED, RETRYING, FAILED, quarantine, unresolved and unadmitted evidence.
- Resting checksum mismatch, duplicate IDs, invalid submitted/open economics, foreign token/condition/SELL, and manual market-token order.
- Missing order without close evidence: rejection.
- Real fake-gateway `close_rdp_cycle()` success releases canceled remainder; false cancellation/incomplete close cannot.
- Stale close evidence invalidated by new scoped evidence.
- Prior complete closure plus a subsequently registered resting cycle.
- Exact NORMAL cash boundary: new notional `4.40`, FreeCash `9.40` passes; just below fails.
- Existing approved fixture:

```text
InventoryCost = 23
ReservedCash = 0
FreeCash = 7
NORMAL: 5 YES @ .29 and 5 NO @ .59
NewNotional = 4.40
Remaining cash = 2.60
Result: reject for protected repair capacity
```

- Accepted related entry with InventoryCost `20.60`, FreeCash `9.40`: after five NO fills at `.59` and verified YES cancellation, InventoryCost `23.55`, FreeCash `6.45`, no reservation.
- Both possible one-sided and fractional partial outcomes preserve the admission guarantee.
- REBALANCE requiring `4.90` with exactly `4.90` free cash passes; less rejects; no extra reserve.
- Current repair-price changes cannot affect NORMAL protected amount.
- Valid fractional snapshot/candidate integration.
- Malformed mode/candidate, nonfinite values and overcommitment.
- Independent immutable captures for accepted/rejected reads.

Run:

```bash
uv run pytest -q tests/test_rdp_capital.py
uv run pytest -q tests/test_rdp_accounting.py tests/test_rdp_quotes.py tests/test_rdp_lifecycle.py tests/test_state.py tests/test_filltruth.py tests/test_risk.py
uv run pytest -q
```

Preserve exact results/errors. Inspect status, relevant untracked files, diff/stat and whitespace checks. Preserve historical assets and immutable audit artifacts.

## Observability Requirements

Expose:

- Capital, FreeCash, ReservedCash, InventoryCost.
- NewNotional and committed capital after candidate.
- FreeCash after candidate.
- Protected repair amount: NORMAL 5, REBALANCE 0.
- Explicit failure/admission reason and relevant order IDs.

No persistent telemetry system or capital-history counters in this iteration.

## Live Validation

No live reads or actions are required.

After integrated readiness and authorization, compare official cash/order evidence, confirmed acquisition cost and reservation transitions for one supervised market. Verify NORMAL retains the approved reserve and REBALANCE can spend it.

## Explicit Non-Goals

- Runtime cash/allowance parsing and authenticated integration.
- Controller, Book Filter, FillWindow or strategy transitions.
- Order submission/cancellation.
- Settlement, external cashflows or multi-market capital progression.
- Capital-history metrics or optimization.
- Shared-core or FL/IM changes.
- Readiness declaration.

## Risks

- Resting remaining quantities exclude matched pending trades; confirmation checks must prevent accidental cash release.
- Prior close evidence can become stale; current evidence must be checked.
- Float-produced acquisition totals require careful decimal handling without concealing genuine overcommitment.
- Certified inputs do not establish live feed/read freshness; later runtime must preserve that independent gate.

## Deferred Findings

Official collateral-read validation, complete runtime ownership construction, Book Filter, controller, timing, metrics and CLI remain outstanding.

They do not require changing the approved five-dollar policy and are outside this iteration.

## Human Approval Required

NONE beyond the decision already recorded in `03_HUMAN_DECISION.md`.

Implement the approved `$5 FUTURE REPAIR RESERVE` exactly. Do not ask again or reopen current-price alternatives.

Real-money trading and cancellation remain unauthorized.
