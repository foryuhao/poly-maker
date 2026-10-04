# P0-CM-30 独立研究设计

Status: frozen for this run, 2026-10-04. Git base `m4a-a1-pass` (`97ab0f9`). This is a research-only child of the local, unreviewed P0 C-M implementation; it carries no production or live authority. User specification in the current task is the parameter authority.

## Question and sample

On the original P0 earliest 30 unique replay-usable BTC 5-minute markets, what happens when each market starts with an **independent $30 account**, the net target is clamped to ±15, and complete direction reversals may fall back to lawful pure-reduction Maker buys? Preserve all markets and frozen local labels. The arithmetic sum of 30 independent market PnLs is an experimental summary, not a continuous-account equity curve or daily projection.

## Fixed strategy

Use C-M's valid, locally available bilateral books and `p=(midYES+1-midNO)/2`. Target `clamp(50(p−0.5),−15,+15)` without tenth-probability rounding. Actual net is confirmed YES minus NO inventory. A new order requires at least five shares and is floored to a 0.01-share study grid; the saved market metadata confirms minimum size for 20 markets but does not independently prove this quantity increment, which remains a flagged execution limitation. The remaining ten markets also lack matching historical minimum/fee metadata. Use zero Maker fee and all rebates zero, as in frozen C-M.

Use one outstanding GTC Post-only BUY at best bid aligned to the observed tick. Submit and cancellation each take 500 ms. Preserve C-M's queue rule: on accepted arrival, full visible same-price bid volume is ahead; subsequent known same-token aggressive SELL print volume at/through price first consumes that queue, then can partially fill the order. All three exchange, receipt, and available timestamps must be after order arrival. No price touch, unknown-cancel queue benefit, SELL, Taker, or crossing fallback. Last 15 seconds: cancel and stop buying. Two-second bilateral freshness applies.

## Position, cash, and stop accounting

Normal desired adjustment is the continuous target-minus-actual-net difference. For a full flip blocked by cash, exit reserve, or the conservative stop constraint, first attempt `min(|net|,|delta|)` shares opposite the old net, floored to legal precision, without crossing zero. If that pure reduction is below minimum, a smaller legal flip is allowed only if every possible partial state passes. For other blocked orders, shrink downward to the largest legal 0.01-share quantity at least five and no greater than the desired adjustment.

For any order quantity `q`, check `q=0`, the point at which old net inventory is eliminated, and `q=full`. Cash after each state must cover filled spend. For new risk states, it must also cover `R_exit=|net_after|×$1`. This exit reserve restricts new risk; it is not a cash debit and pure reduction can use available cash. Potential net must remain within ±15. The conservative zero-residual payout metric is `confirmed Merge income − all BUY gross − separate fees`, with equal-side inventory merged immediately on confirmed fill. At each partial-state breakpoint, a pure reduction must not lower this metric; new-risk states must stay above −$20. If an active order's possible remaining fills cross the threshold, trigger the per-market stop and request cancellation; after stop only legal target-directed pure-reduction Maker orders may be placed. All pending fills through cancel confirmation remain live in the simulation.

Confirmed Merge cash is released after an assumed 60 seconds; official-winning residual pays 15 minutes after market end. These delays, unknown chain costs/failures, and conditional queue fills are limitations. Pair and Residual use average含费 cost as accounting attribution; only Total PnL judges a market.

## Outputs and acceptance

`research_tools/p0_cm_30.py` writes `research_outputs/p0_cm_30/{params.json,sample_coverage.csv,orders.csv,ledger.csv,market_results.csv,summary.json}`. A separate read-only local audit writes `audit.json`. Check exact sample identity; no SELL or crossing accepted order; order lifecycle and print-volume conservation; all partial states' cap, cash, exit reserve and stop checks; no early Merge cash; full ledger/Pair/Residual reconciliation; historical metadata flags; and a full `1791015900` path. Compare to C-M descriptively only: cap, capital, independent accounts, and reduction admission all changed. No parameter search, extra market, external data, production edit, real order, commit, tag, or push is authorized.
