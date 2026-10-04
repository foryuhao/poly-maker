# P0 C-M — 概率跟随 BUY ONLY Maker 动态库存研究设计

Status: frozen independent execution variant; 2026-10-04. Git base: `m4a-a1-pass` (`97ab0f9`); P0 C is an unreviewed local research predecessor, not a production baseline. No production, live order, external request, commit, or push is authorized.

## Objective and fixed sample

Change only P0 C's adjustment execution from price-limited Taker to GTC Post-only Maker. Read the exact 30 ordered rows in `research_outputs/p0_probability_inventory/sample_coverage.csv`; assert they match the earliest 30 unique `execution_replay_usable=True` rows of the saved replay audit. Preserve all 30, including zero-trade and stop markets. Keep P0's causal two-sided mid probability `p=(midY+1-midN)/2`, target `50(p−0.5)`, ±25 net cap, at least 5 shares per new order, $100 cash, 2-second two-book freshness, final 15-second no-buy window, zero rebates, 60-second Merge release, 15-minute settlement release, and permanent conservative −$20 stop. Official settlement winners are local identity-checked labels used only at market end. P0 A/B/C outputs are read-only comparators and are not rerun.

## Maker lifecycle

With no outstanding order, submit a BUY for the exact target-minus-actual-net difference if its absolute size is at least 5 shares. Quote the selected token's best bid rounded down to the observed tick, never at or above its ask. Submit latency is 500 ms. At arrival, reject a crossing quote rather than allowing a Taker fill. Only one order can be submitting, active, or canceling per market. Reserve remaining quantity × limit price until confirmed canceled or filled. Potential net exposure must stay within ±25. Pending exposure also participates in the conservative risk check.

The active order is GTC and keeps its initial quantity as long as the selected best bid stays within one tick, the target has not reversed direction, and cash/net/risk/data constraints hold. Book freshness has its own timer at the first side's last update plus two seconds, so a data-invalid cancel request does not wait for another public event. A cancel request takes 500 ms to confirm; prints during that interval can still fill the order. The original order may arrive after an earlier cancel request but before cancel confirmation, so arrival and cancel confirmation are separate timers. Recompute the target after cancel confirmation. Do not replace an existing partial order with a sub-minimum new order. At T−15 seconds, cancel remaining orders. No SELL, Taker, or crossing fallback exists.

## Conditional queue fill rule

At accepted arrival, all displayed bid quantity at the order's price goes ahead of the hypothetical order. Only later public same-token aggressive `SELL` prints at or below the order price, with exchange, receipt, and local availability timestamps no earlier than arrival, can consume this queue. A print's volume first reduces visible queue ahead, then fills at most its remaining volume against our order. Volume is used once. Unknown cancellations never advance queue. This supports partial fills and cancel races, but does not identify the true order queue, hidden liquidity, or complementary-token matching. It is a conditional conservative opportunity replay, not an observed own-fill or proven executable PnL.

## Fees, cash, and accounting

Saved official metadata for 20 markets says `crypto_fees_v2` with `takerOnly=True`; use Maker fee zero and rebate zero there. The ten later markets lack a saved matching metadata snapshot and are marked as Maker-zero/min-5 assumptions. No unknown chain operation cost is invented; Merge failure/cost is unobserved. Confirmed filled inventory can Merge immediately, with cash released only 60 seconds later. Winning residual payout enters cash 15 minutes after market end. Gross buy spend and any fee are separate. Total PnL is Merge income + residual official payout − gross buys − separate fees. Pair/Residual split uses average含费 cost and reconciles to total. Record terminal settlement PnL separately from the interim conservative stop metric, which gives current unmatched inventory zero payout. Carry the stop permanently across later markets.

## Outputs and comparison

`research_tools/p0_maker_only.py` writes only `research_outputs/p0_maker_only/{params.json,sample_coverage.csv,orders.csv,ledger.csv,market_results.csv,summary.json}`. Report conditional aggregate PnL, order/fill/cancel/rejection and partial counts, tracking error, capital, stop time, and representative paths. Compare to frozen P0 C both over the full 30 market table and the common actually traded market range; differing participation prevents interpreting aggregate PnL differences as an isolated Maker effect. No parameter search, sample extension, or predictive signal is permitted.
