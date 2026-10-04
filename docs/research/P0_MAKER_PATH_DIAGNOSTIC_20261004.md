# P0 C-M 库存形成与退出路径诊断 — 2026-10-04

Status: **IMPLEMENTED — AWAITING INDEPENDENT REVIEW**. This is a read-only attribution of the frozen `p0_maker_only` run, not a new replay or executable-PnL claim. Git base: `m4a-a1-pass` (`97ab0f9`); the local C-M implementation and output remain unreviewed. No parameter, model, fill rule, production code, or original P0 file was changed.

## Question and answer

`btc-updown-5m-1791015900` lost **$19.55** because a target-consistent NO entry remained unmatched when the market resolved YES. The NO order filled while cancellation was pending, but **its direction still matched the probability target at the fill time**: `p=.085`, target `N=-20.75`, actual net moved `0 → -21.25`, and absolute target error fell `20.75 → 0.50`. The cancellation was requested for `invalid_book`, not a direction reversal. Thus the loss is not principally a stale, target-opposing fill. In the conditional C-M path, a legal 5-share YES adjustment was submitted later but received no fill; after its cancellation, full-delta YES orders failed the fixed potential-stop-risk check. This is an inventory-adjustment execution/risk-gate path, followed by an adverse settlement. No counterfactual PnL improvement is claimed.

## All 23 fill events

[`fill_event_diagnosis.csv`](../../research_outputs/p0_maker_path_diagnostic_20261004/fill_event_diagnosis.csv) has one row per event: submit, accepted arrival, cancel request, fill, cancel confirmation or its absence; side, quantity, order/trade price, submit and causal fill probabilities/targets, net inventory before/after, error before/after, adjustment-side match, cancel-pending flag, queue-ahead amount, print clocks, cash, stop metric, and source event sequence. All 23 rows were joined to same-token aggressive `SELL` prints in the existing local event files, and all fill-time books were valid under C-M's two-second freshness and price-probability rules. No future book interpolation was used. A missing state would be emitted as `UNKNOWN` rather than estimated; there are zero such cases here.

| Fill-time test | Events | Shares |
|---|---:|---:|
| Absolute target error decreased | 22 | — |
| Error unchanged | 1 | 6.00 |
| Error increased | 0 | 0 |
| Fill direction opposed then-current adjustment need | 0 | 0 |
| Filled during cancel wait | 16 | 201.00 |
| All fills | 23 | 246.23 |

The unchanged event is NO 6 shares in `1790964300`: target at fill `-12.75`, net `-9.75 → -15.75`, so error stays 3. This is an overshoot around the current target, yet the NO direction still matched the pre-fill need. The fill table preserves original float precision; displayed figures in this report are rounded.

## `1791015900` timeline, UTC

| Time | Causal observation / order action | Result |
|---|---|---|
| 08:25:22.764–08:27:03.389 | 53 NO submissions as the model target moved negative. Prior orders were canceled/rejected without filling. | Zero inventory before `CM01843`. |
| 08:27:03.389 | `CM01843` NO 21.25 @ $0.92 submitted; `p=.075`, target `-21.25`. | 500 ms submit delay. |
| 08:27:03.889 | Accepted behind 134.38 visible NO bids at $0.92. | Conditional FIFO queue begins. |
| 08:27:08.687 | Cancel requested for `invalid_book`; visible queue ahead had fallen to 27.80. | Order can still fill before confirmation. |
| 08:27:08.894 | Same-token NO `SELL` print 97.30 @ $0.92 consumes 27.80 ahead and all 21.25 own shares. At this available time `p=.085`, target `-20.75`. | NO cost $19.55, fee $0, net `-21.25`; simulator processes the fill 207 ms after cancel request. Order fills fully, so no cancel confirmation exists. |
| 08:27:13.593 | First valid book-change observation after fill: `p=.0875`, target `-20.625`. | YES need 0.625, below 5-share new-order minimum. |
| 08:27:41.711 | `CM01844` YES 5 @ $0.15 submitted; `p=.175`, target `-16.25`, net `-21.25`. | Legal opposite adjustment, quoted cost $0.75. |
| 08:27:41.719–08:27:42.219 | `invalid_book` causes cancel request 7.3 ms after submit. Order is accepted at 08:27:42.211 with zero visible queue ahead and canceled at 08:27:42.219. | Accepted-live interval is 7.3 ms; no public trade prints occur in it, so no conditional fill. |
| 08:27:42.307 | First later valid, order-free BBO decision: `p=.5675`, target `+3.375`, net `-21.25`; full delta 24.625 YES at best bid $0.46 would reserve $11.3275. | Fixed potential-stop-risk gate fails: observed conservative metric `-$18.5973` minus $11.3275 is below `-$20`. |
| 08:29:00.874 | Last valid simulator observation before the 08:29:45 no-buy cutoff: `p=.985`, target `+24.25`, net `-21.25`, available cash $81.4027, reserve $0. | Full delta 45.5 YES at displayed bid $0.98 would require $44.59; cash is sufficient, potential-stop-risk gate fails. Later book state does not supply another valid C-M decision. |
| 08:30:00 | Official winner YES; residual 21.25 NO shares pay $0. | Pair $0, Residual/Total `-$19.55`; no Merge. |

The market has a complete capture manifest (`COMPLETE`, 208,034 accepted/persisted events, zero recorded gaps or lost events), both token IDs and an official YES label in the saved P0 coverage. **Historical market-specific Maker fee/minimum snapshot is absent** for this market: the frozen C-M run marks it `assumed_maker_zero_min5`. The 5-share floor blocks the initial sub-5 adjustment but does not explain the entire failure, since the 5-share YES order was actually submitted. No cash-limited skip occurred. The final 15 seconds were not the primary blocker; the last valid observation was about 44 seconds before that cutoff.

The C-M market result records 710 `potential_stop_risk` decision skips, 57 `invalid_book` observations, 261 below-minimum decision observations, 55 submitted/51 accepted/4 rejected orders, and one full fill. These counts are repeated simulator decisions, not independent opportunities. The CSV [`key_market_book_decisions.csv`](../../research_outputs/p0_maker_path_diagnostic_20261004/key_market_book_decisions.csv) records BBO-change observations with actual net inventory, outstanding order, candidate full-delta cost, and a diagnostic reconstruction of the risk gate. The authoritative action sequence is in [`key_market_timeline.csv`](../../research_outputs/p0_maker_path_diagnostic_20261004/key_market_timeline.csv) and [`key_market_orders.csv`](../../research_outputs/p0_maker_path_diagnostic_20261004/key_market_orders.csv).

Clock qualification: `cancel_pending` refers to **simulator processing / local available time**. For `CM01843`, the public print's `exchange_at` is 08:27:08.642, about 45 ms *before* the 08:27:08.687 cancel request, while its received/available clocks are after that request. Across all 16 simulator cancel-wait fills, eight prints totaling 86 shares carry exchange timestamps before their cancel requests. Thus the flag diagnoses a local event-order race; it does not prove an exchange-side trade happened after a cancel was sent. Fill-time probability is calculated from the book locally available at the simulator fill decision, as C-M itself does.

## Other losses and positive control

| Market | Total / Pair / Residual PnL | Inventory path and exit observation |
|---|---:|---|
| `1790943000` | `-$3.875 / $0 / -$3.875` | YES 6.25 @ $0.62 filled during cancel wait and resolved NO. Afterwards 57 NO adjustment orders were submitted; 43 accepted, 14 rejected, none filled. Same residual-loss / unfilled-opposite path; saved historical metadata. |
| `1790966700` | `-$0.5775 / -$0.945 / +$0.3675` | NO 6.75 then YES 7.5 filled. Merge 6.75; residual YES 0.75 won, but Pair loss exceeded Residual gain. This is a different loss mechanism and proves Residual profit does not imply positive Total. |
| `1791017700` | `-$6.29 / $0 / -$6.29` | YES 9.25 @ $0.68 filled during cancel wait and resolved NO. One opposite NO 6-share order was accepted, then canceled without fill. Same broad unmatched-inventory path; historical fee/minimum metadata assumed. |
| **Control `1790962500`** | `+$4.1575 / $0 / +$4.1575` | Two target-consistent NO fills total 18.75 shares @ $0.71 and $0.87; NO won. No Pair or exit fill was needed for positive PnL. Historical metadata saved. |

[`market_mechanisms.csv`](../../research_outputs/p0_maker_path_diagnostic_20261004/market_mechanisms.csv) gives the same-grain comparison and order counts. All four losing markets remain in the 30-market table; this diagnosis removes no fill or market. All 23 fill quantities sum to the C-M per-market buy quantities. Every market reconciles `Merge income + residual settlement - BUY gross - separate fees = Total PnL` and `Pair + Residual = Total PnL`. The worst-market cash after the NO fill is $81.4027 and remains so through settlement; the zero redemption is not used early.

## Evidence boundaries and next question

**Verified within frozen files:** event clocks, price/quantity, contemporaneous valid books, target-error changes, order actions, no opposite fill, official outcome, accounting, and simulator skip reasons. **Execution assumptions inherited unchanged:** visible same-price queue fully ahead at accepted arrival; eligible public `SELL` prints consume that queue; no unknown-cancel queue benefit; 500 ms submit and 500 ms cancel latency; zero Maker fee/rebate where metadata is missing; 60-second Merge and 15-minute redemption release. The NO fill is therefore a **conditional simulated fill**, not an observed hypothetical-order execution. **Mechanism inference:** failure to acquire enough opposite inventory under Maker-only execution and the fixed full-delta risk gate dominates the worst-market loss after a target-consistent entry. This is not proof that a different lifecycle, smaller quantity, or changed target would improve PnL; no modified path was replayed.

The single next research question is **库存调整可成交性**: when an adverse winner reversal makes the opposite side necessary, can a legal Maker adjustment acquire inventory before the risk gate and data-validity window close? In the worst market there was one legal opposite order, zero opposite fills, and 710 recorded potential-stop-risk skips; two other residual-loss markets likewise had no opposite fills despite submitted orders. Preserve this result for independent review before any new strategy experiment.

Reproduce: `python3 research_tools/p0_maker_path_diagnostic.py` from repository root. All local artifacts are in `research_outputs/p0_maker_path_diagnostic_20261004/`; inputs are the frozen `research_outputs/p0_maker_only/` and local `research_data/m4a/`. No external data call, order, commit, tag, or push was made.
