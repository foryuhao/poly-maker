# ITER-0012 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `220d964b37839cd3b7a196309549cacd7a330fb7`.
- Latest archived PASS: ITER-0011 at `b5215d26856e73a2c063b95bad436f2e7b3a242c`.
- Parent verified the initialization push and recorded its receipt; no implementation changes resulted.
- Inspected authority, baseline/index, latest result/final review/receipt, actual account/preflight/capital code and relevant tests.
- Preserved untracked `research_outputs/` remains untouched.
- Prior full-suite evidence: `1076 passed, 2 skipped`.

## Problem

The frozen capital projection starts with $30 regardless of observed account backing. RDP account preflight reports cash, allowance and existing state but cannot determine whether a new, empty $30 run is backed without adopting human positions or overlooking other resting BUY commitments.

Certify only the initial run’s capital backing and expose that evidence through account preflight.

## Evidence / Current Behavior

- `project_rdp_capital()` correctly projects the frozen $30 budget from confirmed run fills and owned reservations.
- `account_snapshot()` provides validated Decimal cash/allowance and selected positions/orders.
- It retains only selected order economics and a total account order count. Other account BUY commitments cannot currently be calculated.
- Existing selected inventory cannot be imported into RDP accounting without attributable confirmed-fill history.
- The approved $5 reserve constrains subsequent NORMAL admission; it does not change starting capital or create a separate resting reservation.

## Existing Components Reused

- Existing account reader, strict official gateway reads and immutable evidence types.
- Existing `risk/rdp_capital.py`, `CAPITAL` and subsequent NORMAL/REBALANCE gate.
- Existing RDP bootstrap, account-check preflight, CLI and logging.
- Existing Decimal monetary validation.
- Existing account and capital fixtures.

## Existing Components Modified

- `src/polymaker/rdp_account.py`: retain complete immutable account order evidence and selected-market identity.
- `src/polymaker/risk/rdp_capital.py`: add read-only initial-backing evaluation.
- `src/polymaker/rdp_live.py`: evaluate after an account snapshot.
- `src/polymaker/cli.py`: report the distinct capital-backing result.
- Relevant tests.

**Shared-Core Change: YES**, limited to CLI reporting. Gateway, shared processors and predecessor capital behavior are unchanged.

**FL Behavior Impact:** NONE.

**IM Dependency:** NONE.

## New Components Required

A small immutable initial-capital result in the existing RDP capital module. No mutable ledger, new account client or subsystem.

## Desired Behavior

With `--account-check`, the existing command also reports whether the observed account can back an initially empty $30 RDP run.

Extend account evidence additively to include:

- selected condition ID;
- every validated account open order’s existing order-evidence fields.

Preserve existing selected-order and count fields. Older fixtures/construction may remain compatible through defaults, but incomplete evidence cannot certify backing.

Evaluate:

```text
AccountBUYCommitment =
    sum(price × remaining_size for every observed BUY order)

ObservedCashBacking =
    balance_usd − AccountBUYCommitment

ObservedAllowanceBacking =
    allowance_usd − AccountBUYCommitment
```

Both independent backing values must be at least `CAPITAL = $30`.

This deduction is a conservative startup backing check. It does not redefine Frozen FreeCash, count inventory cost again or add account commitments to the RDP run’s own accounting.

Selected positions must be exactly zero and selected open orders absent. Otherwise return an explicit ineligible result: the empty-run baseline cannot be established without adopting unreconciled state.

Foreign positions are not imported. Foreign orders are preserved; BUY commitments reduce backing, while SELL orders do not reserve collateral cash.

## Minimal Scope

Provide one synchronous, read-only evaluator using selected bootstrap identity, account snapshot, current time and a read-age limit.

Validation must require:

- complete account order evidence, count consistency and unique IDs;
- selected-order fields consistent with the complete order collection;
- exact selected condition/token/outcome mapping;
- valid nonnegative monetary/order quantities and legal order sides/prices;
- complete required-spender evidence already certified by the account reader;
- finite, ordered read timestamps not in the future;
- all reads within the supplied age limit and before selected-market expiry.

Use the existing configured `risk.user_ws_blind_halt_s` as the preflight read-age limit; validate it as finite and positive. This is startup evidence freshness, not a new strategy timer.

Return explicit eligible/ineligible evidence rather than creating state, modifying the account or changing the frozen capital constant.

A completed account-read preflight remains an account-read success when backing is ineligible. Report the separate backing outcome/reason; do not imply the market is ready to trade.

## Invariants

- Starting RDP capital remains exactly $30.
- Excess wallet cash cannot enlarge that budget.
- The $5 NORMAL reserve remains a subsequent admission constraint inside $30.
- Existing positions/orders are never adopted, canceled or rewritten.
- Balanced pre-existing positions with Delta zero still cannot establish an empty inventory baseline.
- All account BUY commitments are deducted once in the backing calculation.
- SELL orders do not become cash reservations.
- No snapshot grants send authority.
- No network interface or real-money action is added.

## Acceptance Criteria

1. Fresh flat selected state with no orders and cash/allowance exactly $30 is eligible.
2. Either backing value below $30 is ineligible, independently.
3. Higher wallet cash does not change reported RDP capital.
4. Foreign BUY commitments are included; foreign SELL commitments are not counted as collateral cash.
5. Nonzero selected positions or any selected open order prevent empty-run certification without account mutation.
6. Complete collection/count/selected-subset inconsistency, duplicates and malformed economics fail closed.
7. Missing account scope, wrong token mapping, stale/future/unordered timestamps or expiry cannot certify backing.
8. Results are immutable and expose independent cash/allowance backing.
9. Account preflight/CLI preserve read-success behavior while separately reporting backing eligibility.
10. Existing capital projection and NORMAL/REBALANCE admission tests pass unchanged.
11. No gateway write, inventory import or persistent reservation ledger appears.

## Required Tests

- Exactly $30 and one micro-dollar below, for balance and allowance separately.
- Wallet $100 retaining a $30 RDP budget.
- Wallet/allowance $35 with a $5 foreign BUY commitment: eligible; slightly greater commitment: ineligible.
- Multiple foreign BUY orders and SELL orders.
- Partial fills using remaining quantity, without reserving original submitted quantity.
- Nonzero one-sided and balanced selected inventory.
- Existing selected BUY/SELL orders; prove no cleanup.
- Missing complete order evidence, count mismatch, duplicate IDs and inconsistent selected subset.
- Wrong condition/token/outcome mapping and malformed values.
- Exact freshness boundary, expired/stale/future/unordered reads and invalid age limit.
- Immutable capture.
- Actual account-reader retention of all orders.
- Complete preflight/CLI eligible and ineligible reporting.
- Existing empty-run projection and candidate gate interoperability through real processor fixtures.

Run focused tests, affected account/capital/preflight regressions and the full suite when practical. Record exact commands/results; inspect status, untracked files, diff stat and `git diff --check`. Safely exercise CLI help and complete commands through fixtures.

## Observability Requirements

Reuse the RDP preflight log. Separately record:

- initial-backing eligible/ineligible and reason;
- frozen capital `$30`;
- account BUY commitment;
- observed cash and allowance backing;
- read age;
- selected-state baseline outcome;
- `real_money_trading_performed: false`.

Do not label this startup backing evidence as execution authorization or full CapitalGate readiness.

## Live Validation

No order/cancellation is authorized.

A later safe account-check invocation can produce observed backing evidence. Synthetic cash/order fixtures are not proof of live spendability. Final readiness must also verify trading-time revalidation.

## Explicit Non-Goals

- Trading-time cash binding or continuous reservations.
- User WebSocket.
- Importing existing inventory or adopting orders.
- Settlement/deposit/withdrawal accounting.
- NORMAL/REBALANCE controller.
- Quote admission or send authorization.
- Account cleanup.
- Real-money trading.

## Risks

Sequential account reads are not atomic. Eligibility describes captured startup evidence; a runtime must revalidate after relevant account changes and immediately before risk admission.

A conservative backing check may reject an account until reliable current evidence is available. It must never silently create missing capital.

## Deferred Findings

- Continuous cash/allowance/order revalidation remains necessary before subsequent orders.
- Authenticated stream reliability must invalidate stale startup evidence.
- Controller startup must consume this baseline alongside authoritative empty run state.
- Per-token minimum-size and final tick-provenance integration remain outstanding.

## Human Approval Required

NO.

This certifies the frozen $30 starting assumption using existing read evidence and preserves unreconciled human account state. It changes no strategy semantics or trading authority.
