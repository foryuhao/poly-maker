# BTC5m Two-Phase Continuous Inventory Strategy — B2 Research Design

Status: Frozen research-only design from the user's 2026-10-03 B2 specification.
Git base: `97ab0f9` (`m4a-a1-pass`) with unrelated pre-existing working-tree changes.
Comparator: `docs/research/CIP_B1_BACKTEST_20261003.md` and its frozen 32-market output.

## Research question and evidence

Test whether an exact T150 transition from unchanged B1 gamma harvest to buy-only, maker-only reduce-only control retains enough matched-inventory edge while decreasing terminal residual risk. Use the same 32 audited execution-replayable BTC5m sessions, R0/B1 book reconstruction, available-time order, 415,281 μs submit and 234,783 μs cancel delays, R0 full-remaining-order trade-through fill semantics, and R0's already identity-checked official settlement labels. No external query, market feed, live order, production import or raw-data repair is permitted.

## Frozen strategy

$25 cumulative capital, 5-share clips, zero fee/rebate, no SELL/taker/merge, and no parameter search. For `0 ≤ t < 150s`, invoke B1's existing `PoolReplay.reconcile` without editing its gamma engine: persistent quotes, continuous average-cost inventory, frozen Backtest 01 asymmetric skew, and no 0.98 entry gate. At exactly `t=150s`, settle simulated order activation/cancellation lifecycle to that instant, record the complete pre-cancel T150 inventory/order snapshot, switch phase permanently, then cancel the heavy-side BUY or all BUY orders when flat. Outstanding orders remain fill-eligible through the frozen cancel latency.

For `150s ≤ t < 300s`, no new order may be on the current heavy side. When net inventory is nonzero, retain any legal resting lighter-side maker order at its original price and place a missing lighter-side 5-share BUY at its current best bid if valid and affordable. Cancel a heavy-side order on phase transition, fill-driven sign change, or other reduce-only reconciliation; invalid books cancel unsafe orders. On reaching net zero, cancel all and latch trading stopped. If a pending order fills after stop, record the resulting risk but do not restart. A 5-share risk-reducing fill can cross the sign when the imbalance is smaller than five; recognize the new heavy side at the next reconciliation. At market end T300, request cancellation of all remaining orders and terminally close them; no T240 pre-close cutoff applies in B2's specified reduce-only phase. No fills after T300 are simulated.

## Accounting and audits

Every fill immediately updates `QY,CY,QN,CN`; no public print-size partial inference is introduced. `MatchedQty=min(QY,QN)`, `PoolPairCost=CY/QY+CN/QN` when both sides held, `GammaPnL=MatchedQty*(1−PoolPairCost)`. Price final unmatched shares using the heavy side's average cost and the official winner to calculate separate `ResidualPnL`. `TotalPnL=GammaPnL+ResidualPnL`. Filled inventory never releases capital; every open or cancel-pending order reserves remaining notional.

Record the pre-cancel T150 snapshot and final snapshot, every order and fill, after-fill and transition paths, reason-coded actions and per-market metrics. `ResidualReduction=abs(Net_T150)−abs(Net_Final)`; the rate is null and status `ALREADY_FLAT_AT_SWITCH` when `Net_T150=0`. Time to flat is measured from T150 to the first subsequent zero net for markets nonflat at T150. `GammaRetention=TotalPnL/GammaPnL_T150` only when the denominator is positive. A risk-increasing fill on an order pending cancellation during reduce-only is a cancel-race overshoot; record event count, shares added, maximum single-event increase and maximum post-race absolute net. A risk-reducing fill that changes the sign of net inventory is a sign-flip overshoot, with count and excess shares. Capital-blocked reduce-only decision events require a valid lighter-side quote, no working lighter-side order and insufficient remaining capital for one 5-share clip; integrate blocked duration and absolute net over available time. Never interpret repeated blocked decisions as distinct rejected orders.

`BBO_ONLY_REPRICE` must be zero. Any new order during reduce-only that does not oppose the immediately prior nonzero net, any return to gamma after T150, or a market-set mismatch invalidates the implementation. Ordinary valid BBO moves never reprice an existing order. An invalid reconstructed book may still trigger a safety cancellation as in B1.

## Comparison and classification

Compare the same-market B2 results with frozen B1: matched shares, final GammaPnL, final residual shares, ResidualPnL, TotalPnL, capital and profitable-market count. B1 full-horizon GammaPnL is +$16.2105; B2 T150 GammaPnL is a within-policy diagnostic. Their difference is descriptive, because B1 can trade after T150 and B2 switches objectives. T150 matched inventory added later by risk reduction is labeled as post-switch matched change, never active gamma harvest.

For a transparent interpretation of “material,” require at least a 20% reduction from B1's 405 final residual shares and at least a 20% improvement from B1's $115.9805 residual-loss magnitude; require TotalPnL at least $10 better than B1's −$99.77, with positive improvement after excluding the best paired market delta, for `TWO_PHASE MODEL SUPPORTED`. `GAMMA PRESERVED, REDUCE INEFFECTIVE` means final GammaPnL at least 80% of B1 with residual shares failing the 20% reduction. `REDUCE WORKS BUT TOO MUCH GAMMA LOST` means residual shares clear 20% reduction but TotalPnL does not improve. `CAPITAL BLOCKS REDUCE PHASE` requires at least half of T150-nonflat markets to be capital blocked for at least half of their remaining time and failure to achieve material residual reduction. `EXECUTION BLOCKS REDUCE PHASE` requires at least half of T150-nonflat markets to have an accepted lighter-side order, no reduce fill and no material capital blockage. Otherwise classify `NO BENEFIT VS B1`. Critical strategy/replay invariant failure takes precedence as `IMPLEMENTATION INVALID`; inability to establish the frozen market/label evidence is `INSUFFICIENT DATA`.

## Scope boundary

This independent historical ablation does not change M4 architecture or grant live trading authority. The 32-market execution-replay universe includes only 19 sessions passing stricter M4A telemetry integrity, and the inherited maker fill predicate has no queue model or observed partial-fill inference.
