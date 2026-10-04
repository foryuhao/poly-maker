# BTC5m Continuous Inventory Pool — Baseline B0 Research Design

Status: Frozen research-only design, supplied by the user on 2026-10-03.
Code base: `97ab0f9` plus unrelated pre-existing working-tree changes.
Comparator: `docs/research/R0_ROLLING_PAIR_REPLAY_20261003.md`.

## Question and sample

On the exact 32 unique execution-replayable markets in `research_outputs/execution_replay_audit_20261003/dataset_audit.csv`, determine whether a continuous YES/NO inventory pool increases matched-share throughput and improves matched economics enough to cover residual settlement loss relative to frozen R0. No directional signal or parameter search is permitted.

## Frozen controls

$25 committed capital, 5-share BUY maker clips, zero fees/rebates, no SELL/taker/merge/redeem, 25 matched-share target, and the R0 T−60s pre-close cutoff. Reuse the audit's 415,281 μs submit and 234,783 μs cancel latencies, R0's `Book` reconstruction and strictly-below-bid public SELL fill rule, and R0's identity-checked official settlement labels. The inherited rule fills an entire remaining order from one qualifying print regardless of print size. It yields no partial fills in this corpus; quantity accounting must still be able to apply a positive partial quantity immediately.

## Decision and accounting

At each available-time BBO/tick, simulated lifecycle transition or fill, read both valid non-crossed books. At balance, quote both current best bids only when their sum is at most 0.98 and capital can support both clips. When YES or NO is heavy, quote only the lighter side at its current best bid and cancel the heavy-side order. Cancel an order whose price differs from the desired price and replace only after the existing cancel latency; keep an equal-price order. Invalid books, target completion and cutoff prohibit new exposure. Capital is cumulative filled purchase cost plus every open order's remaining notional, including orders pending cancellation. Matched inventory never releases capital.

Maintain total YES/NO quantity and purchase cost. For both sides held, `PoolPairCost = CY/QY + CN/QN`; `PairPnL = min(QY,QN) × (1 − PoolPairCost)`. Price the final excess inventory using the heavy side's average purchase cost and the official winner, producing separate `ResidualPnL`. Thus `TotalPnL = PairPnL + ResidualPnL`. Time-weighted inventory and capital use the full 300-second market interval; time-weighted pool cost uses only intervals during which both sides are held.

## Decision criterion

Classify the frozen run using exactly one of the user's six labels. Positive aggregate pair and total economics, higher matched throughput, and a result surviving removal of the best market are required for `CONTINUOUS POOL EDGE OBSERVED`. A market with no inventory has null PairingEfficiency. The paired comparison uses R0's 32-market aggregate and exact market universe, never the obsolete per-cycle stuck-leg concept.

## Boundaries

This is an isolated historical simulation. It grants no live trading authority and makes no change to the M4 program's production architecture or FavoriteFirst strategy. The available 32 captures are execution replayable; only 19 overlap M4A's stricter integrity-PASS subset. That distinction remains visible in interpretation.
