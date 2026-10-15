# RDP-SM: 20 USDC cap and late-market Merge

Development change: 2026-10-13, following the Single Market Pilot release.
The published release and its historical Journals are unchanged.

## Funding

The existing test capital **upper limit** changes from 30 to 20 USDC. It is
not a minimum startup balance. M1 still uses the official account balance and
applicable allowance; M2 still checks current-session order, pending and
confirmed inventory exposure. Human output and capital utilization use 20.
Old Journals recording the former 30 cap remain readable without rewriting them.

## REBALANCE order quantity

REBALANCE BUY quantities use the largest of the actual confirmed inventory gap,
5.00 shares, the official market minimum order size, and the quantity required
for 1 USDC notional. That value is rounded **up** to the 0.01-share SDK grid
before checking order legality. A gap of 5.003207 shares therefore quotes 5.01
shares; a BUY quantity of 4.99 is never submitted. A low price can still require
more shares to meet the existing 1 USDC rule.

M3 planning, M2's final quote refresh and late-market REBALANCE admission all use
the same size calculation. M2 rechecks capital against the actual rounded
order. The NORMAL/REBALANCE state boundary rounds the confirmed Delta to whole
shares using half-up rounding before comparing its absolute value with 5.
An absolute gap of 4.5 or 4.994407 therefore enters REBALANCE. Recorded fills,
inventory and the gap used to size new orders retain their original precision.
Buying 5.01 against a 5.003207 gap leaves an actual opposite residual
of 0.006793 shares, which is retained in inventory and reported.

This addresses the quantity rejection in the 22:00 market. The independent
settlement sweep's concurrent Merge of the active market is a separate cause of
inventory mismatch and is not changed by this sizing correction.

## Merge schedule and scope

The production `rdp-sm-run` path checks for Merge at or after T=240, between
closed windows. It merges `min(ConfirmedYES, ConfirmedNO)` of the current
session's remaining holdings, never MATCHED/MINED quantities or unrelated
markets. Subsequent confirmed pairs can be merged again, with each transaction
tracked separately. Equal quantities are removed from both sides, so Delta and
the inventory-deficient side remain unchanged.

The current trading schedule is: wait until T=15; NORMAL and REBALANCE until T=120;
only REBALANCE until T=300; no new BUY at or after T=300. NORMAL still waits for
market end. The final safe close also checks for remaining confirmed pairs.
Transaction confirmation and closing can extend beyond the market end.
The NORMAL cutoff was moved from T=180 to T=120 by user instruction on 2026-10-15.

The library's explicit offline-only `SMTradingLoop.run(offline=True)` does not
automatically Merge: its HTTP mock guard covers CLOB/Gamma rather than the
settlement SDK and RPC transports. Production-path Merge tests independently
fake those external boundaries and deny all real network access.

## Official API and transaction proof

Merge reuses `PolymarketSettlementBackend` and the already pinned official
`polymarket-client==0.11.0` SDK's `merge_positions`. The SDK resolves the
market's actual protocol/contracts; no collateral contract is hardcoded here.
See [Polymarket: Manage Positions](https://docs.polymarket.com/trading/positions/manage).

Before submission, the existing order Close Barrier must be safe. Cancellation,
terminal verification and confirmed fill reconciliation run again. The signer,
funder, condition, token pair, protocol and dispatch route are bound to the
executor. Fresh on-chain holdings must exactly match the session's confirmed
remaining quantities. Quantities use six-decimal integer units.

A durable Journal intent precedes SDK submission. Recovered funds and reduced
inventory costs are credited only after all of the following:

- The official SDK confirms the transaction and a successful receipt is read.
- Fresh balances show the exact equal burn of YES and NO.
- Fresh collateral balance shows the exact recovered amount.

The amount of inventory cost retired is calculated from actual confirmed fills,
not from the collateral payout. Confirmed fill records remain intact. Stale Data
API positions after a verified Merge pause order admission and require new Data
API reads under the inventory policy below. There is no on-chain position-read
fallback for inventory discrepancies; the Merge transaction's own token-burn
and collateral verification remains in place.

Unknown submission, confirmation or post-state keeps Merge pending, retains
capital exposure, blocks the Close Barrier and forbids automatic resubmission.
Incomplete/conflicting Merge history also blocks a later session, even if its
market has ended. Explicitly failed transactions do not credit inventory or cash.
Display callback failures cannot interrupt transaction accounting.

## Inventory discrepancy policy

RDP uses an inclusive **0.01** numeric tolerance: quantities are measured in
shares and prices in USDC/share. This covers Tracker fill admission/replay,
order metadata and cumulative fill reconciliation, M2 final fill proofs,
Data API inventory differences and
historical Merge fill-price validation. Decimal comparisons include the exact
boundary. RDP's processor supplies these tolerances to shared FillTruth;
other strategies retain their original default precision.

MATCHED order remaining quantity has a separate inclusive tolerance, default
**0.1 share**, configurable in the selected config directory's `config.toml`:

```toml
[rdp_sm]
matched_remaining_tolerance_shares = 0.1
```

The value must be finite and nonnegative. It is loaded at process startup and
captured by each executor. For the observed `5.01` order with `4.996924` matched,
the `0.013076` remainder now passes the terminal-quantity check. Complete trade
evidence and CONFIRMED fills are still required; LIVE orders remain blocked.
Price, cumulative-fill and Data API inventory tolerances remain **0.01**.
Actual fills are preserved, and SDK order-size precision remains **0.01 share**.

The observed 22:30 small maker fill (`0.165406 @ 0.6300013300605782` against a
`0.63` limit) previously exceeded the Tracker's `1e-6` price check and remained
quarantined despite REST reporting CONFIRMED. It now reconciles without changing
its actual price, quantity or lifecycle. The final early exit in that run was
the operator's Ctrl+C, not a market cutoff.

Identity, ownership, associate-trade IDs and CONFIRMED lifecycle remain exact.
Tolerance does not substitute for missing trade records, permit LIVE orders to
close, change the price tick/minimum order rules, relax the 20 USDC capital cap,
or relax Merge's integer token-burn and collateral-recovery proofs. Zero-fill
proofs used to release an external/historical commitment still require actual
zero fills and complete evidence; signed request economics remain exact.

Each YES/NO Data API quantity may differ from the session's remaining economic
fill quantity by **at most 0.01 share**, inclusive. Decimal comparisons avoid
floating-point boundary errors. Strategy inventory still uses confirmed fills
less verified merged pairs; an allowed API difference never rewrites fill
records or rounds away actual exposure.

A larger difference pauses admission. The same market loop waits up to one
second between retries, then rereads its usual official account snapshot and
Data API positions. It does not query on-chain positions to reconcile the
discrepancy. A successful recheck resumes the same session under its existing
phase, capital and order safety checks. M2's final pre-submit check uses this
policy too: a discrepancy there submits no order and returns to the wait loop.

If the difference persists, no new BUY or Merge is submitted while the loop
waits until T=300. It then completes cancellation, terminal verification and
confirmed fill reconciliation and exits with `execution_status=BLOCKED` and
`stop_reason=ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM`. A safe final order Close
Barrier is reported separately from the unresolved inventory admission block.
Other operating failures follow the pause/recheck policy in
[RDP-SM runtime recovery](RDP_SM_RUNTIME_RECOVERY.md). Identity conflicts,
unresolved mutation risk and Journal integrity still prohibit trading.

Human logs show `BLOCKED_WAIT_UNTIL_300` or `RESUMED`, the market elapsed time,
source, tolerance, and expected/actual/difference quantities for both sides.
All order and fill records are preserved. Existing JSON fields and Journal
event formats remain compatible; the new inventory observations are human
output rather than additional order or fill events.

## Historical Merge price precision correction

The 2026-10-13 T=21:35 startup was blocked while reading the previous completed
session's Merge evidence. An official fractional maker fill reported a price of
`0.6900000649895042` for a `0.69` BUY limit. The live Tracker accepted it under
its existing `1e-6` price tolerance, but the historical Merge validator required
the fill price to be strictly at or below the limit. That disagreement caused
`HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW` before any new orders.

The initial correction aligned historical validation with Tracker's `1e-6`
price tolerance. The subsequent RDP-wide numeric correction above now aligns
both with the requested inclusive `0.01` price tolerance.
Actual fill prices and costs are preserved, and Merge quantity, successful
receipt, token-burn and collateral proofs still require their existing exact
checks. Unknown or incomplete Merge transactions remain blocking.

A read-only replay of the affected account's 2,413 original Journal records
across 22 sessions passes after the correction. The original bytes were verified
unchanged. New production-path tests reproduce the previously failing startup
with fractional fills and reject excessive price differences, unowned makers,
SELL evidence, MATCHED evidence and altered released cost.

## Credentials and output

Safe accounts require the existing `POLY_BUILDER_KEY`, `POLY_BUILDER_SECRET` and
`POLY_BUILDER_PASSPHRASE` configuration. Missing credentials fail before Merge
submission with `MERGE_PREPARE_FAILED:BUILDER_AUTH_MISSING`. No credentials are
created or fetched automatically. EOA accounts use the existing settlement
backend's signed transaction path and configured Polygon RPC.

Human logs distinguish Merge INTENT, SUBMITTED and RESULT; only a verified
result reports recovered collateral. JSON adds `merged_pairs` and
`recovered_collateral_usdc`. Inventory reports remaining holdings after Merge;
`completed_pairs` includes merged pairs. Journal adds `sm_merge_intent`,
`sm_merge_submitted` and `sm_merge_result` without changing previous records.

## Offline verification

- Dynamic tick recovery: **635 RDP tests passed**, including 11 new cases for
  transient/persistent mismatch at startup, M3 decision reads and M2 final
  admission, unsupported/malformed ticks, fresh legal REBALANCE quotes,
  retained fills through T=300, human diagnostics and append-only history
  compatibility. Tick consistency remains strict, independent of the 0.01
  economic tolerance. No new Journal event or RunResult field was introduced.
- Unified RDP numeric tolerance: **624 RDP tests passed**, including the actual
  22:30 small-fill reproduction, exact/excessive 0.01 price and quantity
  boundaries, metadata reconciliation, strict foreign-identity rejection,
  unconfirmed-fill blocking, tightened MATCHED remainder boundaries and proof
  that other strategies retain their original tolerance. Shared FL/positions/
  settlement regressions: **173 passed**. Read-only replay of **3,615 original
  Journal records across 29 sessions passed**, with the original bytes
  unchanged. Zero real API/network operations, orders or Merge transactions.
- REBALANCE quantity correction: **88 passed** across minimum-notional, quote
  refresh, trading-phase and M3 loop tests. Twelve new cases cover 4.99/5.00
  share boundaries, fractional rounding, official minimum size, minimum
  notional, both inventory-deficient sides before/after T=180, actual SDK
  signed quantities, retained residual inventory and M2 rejection of invalid
  sizes before submission. The prior test that expected fractional REBALANCE
  rejection now requires 5.01-share quoting and still rejects an off-tick price.
- Complete RDP-SM regression after runtime recovery and the historical Merge
  price precision correction: **598 passed**, including
  **29 Merge cases**, **19 inventory tolerance/recheck cases** and **20 business
  recovery cases**.
- Existing FL execution, FillTruth, positions and settlement regression:
  **173 passed**.
- Merge cases include T=239/240/270/300, fractional residuals, Safe identity,
  missing credentials, failed/unknown transactions, closed-market lookup, stale
  indexed inventory, append-only historical compatibility and capital reuse
  before a subsequent REBALANCE.
- Inventory tests cover both sides of the exact 0.01-share boundary, recovery
  at loop-head and final M2 admission, a persistent discrepancy through T=300,
  Safe CLI diagnostics, zero-order historical compatibility, and Data API lag
  after Merge without an on-chain fallback.
- Ruff and Git whitespace checks passed.
- Hardened offline guards: **0 network/DNS attempts**, workspace credentials
  denied. **0 real orders, 0 real Merge transactions** executed.

These are offline production-path results; an actual on-chain Merge has not
been performed during this change. This change has not been pushed to GitHub.
