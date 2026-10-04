# BTC5m Continuous Inventory Pool — B1 Research Design

Status: Frozen research-only design from the user's 2026-10-03 B1 specification.
Git base: `97ab0f9` (`m4a-a1-pass`) with unrelated pre-existing uncommitted work.
Comparators: `docs/research/R0_ROLLING_PAIR_REPLAY_20261003.md` and `docs/research/CIP_B0_BACKTEST_20261003.md`.

## Objective and evidence

Test whether a continuous YES/NO inventory pool with persistent maker orders and the already tested Backtest 01 asymmetric inventory skew improves matched throughput and pair economics on the exact 32 execution-replayable BTC5m markets. The market universe, R0 `Book` reconstruction, available-time clock, 415,281 μs submit latency, 234,783 μs cancel latency, full-remaining-order public SELL trade-through fill predicate, R0 official identity-checked settlement labels, and T−60s cutoff are frozen. No new external source, live interface, order or production-code change is authorized.

## Frozen policy

$25 cumulative capital, 5-share BUY post-only maker clips, zero fee/rebate, no SELL/taker/merge/redeem, and a 25 matched-share target. `0.98` is only an ex-post pool-cost diagnostic; it does not gate entry. Import `QTY` and `RATE` directly from `research_tools/dp_backtest_01_inventory_skew.py` and require their frozen values `5` and `min(0.08, 2.8 / 5)`. Backtest 01 uses Python's `round(abs(net) * RATE)` and retreats only the heavy side by that many book ticks. Because its `Policy.decide` reprices on BBO changes, B1 reuses its constants and rounding semantics but implements a separate persistent-order reconciliation.

At balance, new YES and NO quotes use each current best bid. For imbalance, the lighter side uses current best bid and the heavy side uses current best bid less frozen skew ticks. Both sides may remain working. A successfully submitted order keeps its original price through subsequent valid BBO movements. After a fill changes inventory, an existing heavy-side order cancels only if its price is more aggressive than the newly computed heavy-side risk quote; a lighter-side order keeps its queue. Cancellation becomes effective after the frozen delay, with the order still eligible for inherited fills in that interval. Invalid book, target and cutoff are the other cancellation grounds. New orders require valid non-crossing tick prices and the $25 filled-plus-reserved cap. If balanced and both sides absent, both 5-share clips must be affordable together or neither is submitted. If imbalanced, the lighter side gets first claim on available capital.

## Accounting and metrics

Every fill updates QY/CY or QN/CN immediately. `MatchedQty=min(QY,QN)`, `ResidualQty=abs(QY−QN)`, `PoolPairCost=CY/QY+CN/QN` when both sides exist, `PairPnL=MatchedQty*(1−PoolPairCost)`. The final heavy-side residual uses its average acquisition cost and the official settlement winner; `ResidualPnL` remains separate. Filled matched inventory never returns capital. Time-weighted absolute net inventory and capital use the full 300-second market interval. The pool-cost time denominator is only time with both YES and NO inventory, because the cost is undefined before then; the same denominator is used for the `<1.00` and `≤0.98` fractions.

Record every order action with a reason, every fill-level inventory path, per-market and aggregate quote churn, throughput, capital, pair and residual economics. Mean absolute net inventory samples each strategy reconciliation event; the separate time-weighted measure uses elapsed available time. `BBO_ONLY_REPRICE` must equal zero. The R0 rule does not produce partial fills from public print sizes; the quantity update function must still correctly accept a positive partial quantity, and the limitation must be reported.

## Classification rules

`IMPLEMENTATION INVALID` takes precedence if an invariant or replay error appears, including a BBO-only reprice. For a reproducible operational interpretation of “materially higher throughput,” require mean matched shares at least 25% above R0's 6.25, or 7.8125 per market. `CONTINUOUS POOL EDGE OBSERVED` additionally requires positive aggregate PairPnL and TotalPnL and positive TotalPnL after removing the best market. `PAIR EDGE EXISTS BUT RESIDUAL DOMINATES` applies to positive PairPnL and nonpositive TotalPnL with negative ResidualPnL. `CONTINUOUS POOL IMPROVES THROUGHPUT ONLY` applies when throughput clears the material threshold without the edge criteria. `CAPITAL LIMIT DOMINATES` requires at least 16 of 32 markets to record a blocked placement when earlier categories do not apply. Otherwise the outcome is `NO IMPROVEMENT OVER R0`. If input identity or replay quality prevents a conclusion, classify `INSUFFICIENT DATA` and do not infer economics.

## Scope boundary

B1 is a separate historical strategy ablation. It neither alters M4 live architecture nor authorizes live control. The 32 execution-replayable sessions include only 19 passing the stricter M4A telemetry integrity audit; that subset remains a diagnostic, not a replacement universe.
