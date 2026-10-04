# BTC5m Continuous Inventory Pool — B1 Backtest, 2026-10-03

Result: **PAIR EDGE EXISTS BUT RESIDUAL DOMINATES**. This is a read-only historical replay. It changed no production trading behavior and used no new external interface.

## Sample and frozen method

The replay used the exact 32 execution-replayable unique markets in `research_outputs/execution_replay_audit_20261003/dataset_audit.csv`, the same market set as R0 and B0. All 32 replayed without data errors and matched an existing R0 official settlement label by market, condition ID and ordered YES/NO token IDs. Nineteen also overlap the stricter M4A integrity-PASS subset; the other 13 remain in the frozen primary universe. Book reconstruction and available-time event order use the R0 research `Book`; submit/cancel delays are 415,281/234,783 μs, and the cutoff is exactly T−60s. One qualifying same-token public SELL print strictly below an accepted BUY fills the order's entire remaining quantity, regardless of print size, exactly as R0. Fees, rebate, SELL, taker and merge remain disabled. `0.98` is only an ex-post diagnostic.

The policy imports Backtest 01's frozen `QTY=5` and `RATE=min(0.08, 2.8/5)`, and uses its `round(abs(NetInventory)*RATE)` tick retreat on the heavy side when placing a new order. Existing valid resting orders keep their prices across BBO changes. Its action audit records every placement, cancellation, acceptance, fill completion, blocked placement and replacement reason.

## Primary results

| Metric | B1 |
|---|---:|
| Submissions / accepted / cancellations / replacements | 1,422 / 1,422 / 1,125 / 1,079 |
| Raw fills / purchased shares | 297 / 1,485 |
| Fills per 100 submissions | 20.886 |
| Median / mean resting lifetime | 3.476s / 7.642s |
| `BBO_ONLY_REPRICE` | **0** |
| Mean / median fills per market | 9.281 / 10 |
| Total / mean / median matched shares | 540 / 16.875 / 17.5 |
| Aggregate PairingEfficiency | 72.73% |
| Aggregate PairPnL | +$16.2105 |
| Weighted final PoolPairCost / PairPnL per matched share | 0.969981 / +$0.030019 |
| Fraction of two-sided-inventory time PoolPairCost <1.00 | 47.17% |
| Fraction of two-sided-inventory time PoolPairCost ≤0.98 | 35.23% |
| Final PoolPairCost ≤0.98 | 11/32 markets |
| Mean decision-sampled / time-weighted absolute net inventory | 8.724 / 10.191 shares |
| Maximum absolute net inventory | 40 shares |
| Final residual shares | 405 total; 12.656 mean per market |
| Aggregate ResidualPnL | −$115.9805 |
| Mean / median / maximum peak capital | $22.5302 / $24.35 / $25.00 |
| Capital-blocked decision events / affected markets | 11,456 / 19 |
| Aggregate TotalPnL | **−$99.77** |
| Profitable / losing / flat markets | 7 / 24 / 1 |

Six markets reached at least 25 matched shares. Positive aggregate pair economics survived removal of the highest PairPnL market: +$4.1105 remained. Aggregate TotalPnL was negative because residual settlement loss exceeded matched edge by $99.77. The 11,456 capital-blocked counts are decision events during which a new clip could not be reserved, so repeated BBO updates can count the same ongoing constraint; they are not 11,456 distinct rejected orders. Existing matched inventory did not release capital.

On the 19-market M4A integrity-PASS overlap, mean matched shares were 14.737, PairPnL +$13.3241, ResidualPnL −$76.1441 and TotalPnL −$62.82. This diagnostic has the same pair-positive/residual-negative pattern and does not replace the frozen 32-market comparison.

## R0 comparison

| Measure | R0 cycle baseline | B1 continuous pool |
|---|---:|---:|
| Markets | 32 | 32 |
| Mean matched shares / market | 6.25 | 16.875 |
| Fill records | 108 | 297 |
| PairingEfficiency | 74.1% | 72.7% |
| Aggregate PairPnL | +$5.45 | +$16.21 |
| Aggregate ResidualPnL | −$50.70 | −$115.98 |
| Aggregate TotalPnL | −$45.25 | −$99.77 |
| Maximum peak capital | $24.30 | $25.00 |

B1 acquired 2.7× R0's matched shares per market and more than doubled aggregate matched edge, while also carrying much larger residual inventory and loss. The resulting total economics were $54.52 worse than R0.

## B0 execution comparison

| Measure | CIP-B0 quote churn diagnostic | B1 persistent quotes |
|---|---:|---:|
| Submissions | 6,628 | 1,422 |
| Cancellations | 6,626 | 1,125 |
| Fills | 2 | 297 |
| Fills / 100 submissions | 0.030 | 20.886 |
| Matched shares | 5 | 540 |
| `BBO_ONLY_REPRICE` | Not separately audited; B0 repriced on best-bid changes | 0 |

B0 serves only as the invalid quote-churn diagnostic. B1's action audit records **1,132 BOOK_INVALID cancel requests** and **1,079 subsequent BOOK_INVALID replacements**, while **1,125 orders finished canceled** by market end. Some requested cancellations were overtaken by fills during cancel latency, so request and final-status counts differ. There were 11 cutoff cancel requests and one target cancel request. No ordinary valid BBO change canceled a resting order; invalid reconstructed books still caused substantial permitted churn.

## Positive PairPnL attribution

This is post-hoc attribution, never an input to trading. For a market ending with positive PairPnL, compare final YES/NO average cost with the first moment both sides held inventory. Six positive-pair markets lowered only YES VWAP (combined positive PairPnL +$5.1371), six lowered only NO VWAP (+$6.3890), and three lowered both (+$18.7944); zero were `NONE`. These gross positive contributions are offset by negative PairPnL in 16 other markets, producing the +$16.2105 net. The best market moved from initial two-sided PoolPairCost 0.82 to final 0.5967 as later low-price fills lowered both averages; the worst total-PnL market ended with PairPnL +$0.08 but 40 excess YES shares and ResidualPnL −$10.48.

## Validation and interpretation

Checks on the saved outputs confirmed exact market-set equality with R0 and B0; all 297 fills have a matching after-fill path row, trade-through prices and three timestamps strictly beyond activation; all placements were below the then-current ask; every market satisfies filled cost plus open reservations ≤$25 and `PairPnL + ResidualPnL = TotalPnL`; final settlement cash flow also equals TotalPnL. Every `BOOK_INVALID` cancellation in the action audit occurred with an invalid reconstructed book, and no BBO-only repricing was recorded. A direct persistent-order check kept both orders through valid BBO changes. A synthetic 2-of-5-share fill immediately updated QY/CY, requested a heavy-side risk retreat when the remaining YES quote was too aggressive, and retained the lighter-side NO order at its original price. Those checks validate bookkeeping and reconciliation; the inherited public-print rule produced no partial fills in the 32-market sample.

The replay remains a deterministic maker-fill proxy, not a queue or actual own-order execution model. Its qualifying public print can be smaller than five shares yet fill a full simulated clip. The inherited R0 book validity has no independent age threshold for a quiet book; invalid/crossed book states do trigger fail-closed cancellations in B1. The 32 captures span two collection periods on one date, and only 19 satisfy the stricter M4A integrity audit. These limits matter most when interpreting the 297 simulated fills and the large residual settlement loss.

Run `python3 research_tools/cip_b1_backtest.py` from the repository root. The ignored local artifacts under `research_outputs/cip_b1_20261003/` are `summary.json`, `market_results.csv`, `orders.csv`, `fills.csv`, `inventory_paths.csv`, and `action_audit.csv`.

## Representative paths

The following tables list **every fill** for the best total-PnL market, worst total-PnL market and a median-activity market (10 fills). Working-order prices and capital are recorded immediately after fill reconciliation. Timestamps are UTC. A dash means no inventory or no working order.

### Best total-PnL market: `btc-updown-5m-1791015900` — TotalPnL $+12.10

| Time (UTC) | Fill | QY | QN | AvgY | AvgN | Net | Matched | Pool cost | PairPnL | Working YES | Working NO | Capital |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-10-03 08:25:17.510 | NO 5 @ 0.520 | 0 | 5 | — | 0.520 | -5 | 0 | — | 0.000 | 0.470 | 0.490 | 7.40 |
| 2026-10-03 08:25:36.025 | YES 5 @ 0.300 | 5 | 5 | 0.300 | 0.520 | 0 | 5 | 0.820 | 0.900 | 0.250 | 0.690 | 8.80 |
| 2026-10-03 08:25:43.895 | YES 5 @ 0.250 | 10 | 5 | 0.275 | 0.520 | 5 | 5 | 0.795 | 1.025 | 0.240 | 0.690 | 10.00 |
| 2026-10-03 08:26:30.268 | YES 5 @ 0.330 | 15 | 5 | 0.293 | 0.520 | 10 | 5 | 0.813 | 0.933 | 0.260 | 0.660 | 11.60 |
| 2026-10-03 08:26:55.474 | YES 5 @ 0.210 | 20 | 5 | 0.273 | 0.520 | 15 | 5 | 0.792 | 1.038 | 0.190 | 0.770 | 12.85 |
| 2026-10-03 08:26:57.106 | YES 5 @ 0.190 | 25 | 5 | 0.256 | 0.520 | 20 | 5 | 0.776 | 1.120 | 0.160 | 0.770 | 13.65 |
| 2026-10-03 08:27:02.937 | YES 5 @ 0.090 | 30 | 5 | 0.228 | 0.520 | 25 | 5 | 0.748 | 1.258 | 0.050 | 0.880 | 14.10 |
| 2026-10-03 08:27:17.760 | NO 5 @ 0.910 | 30 | 10 | 0.228 | 0.715 | 20 | 10 | 0.943 | 0.567 | 0.060 | 0.900 | 18.80 |
| 2026-10-03 08:28:03.720 | NO 5 @ 0.220 | 30 | 15 | 0.228 | 0.550 | 15 | 15 | 0.778 | 3.325 | 0.750 | 0.210 | 19.90 |
| 2026-10-03 08:28:06.808 | NO 5 @ 0.210 | 30 | 20 | 0.228 | 0.465 | 10 | 20 | 0.693 | 6.133 | 0.750 | 0.180 | 20.80 |
| 2026-10-03 08:28:18.764 | NO 5 @ 0.180 | 30 | 25 | 0.228 | 0.408 | 5 | 25 | 0.636 | 9.092 | 0.750 | 0.170 | 21.65 |
| 2026-10-03 08:28:21.171 | NO 5 @ 0.170 | 30 | 30 | 0.228 | 0.368 | 0 | 30 | 0.597 | 12.100 | — | — | 17.90 |

### Worst total-PnL market: `btc-updown-5m-1791020100` — TotalPnL $-10.40

| Time (UTC) | Fill | QY | QN | AvgY | AvgN | Net | Matched | Pool cost | PairPnL | Working YES | Working NO | Capital |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-10-03 09:35:37.651 | YES 5 @ 0.490 | 5 | 0 | 0.490 | — | 5 | 0 | — | 0.000 | 0.450 | 0.500 | 7.20 |
| 2026-10-03 09:35:52.022 | YES 5 @ 0.450 | 10 | 0 | 0.470 | — | 10 | 0 | — | 0.000 | 0.410 | 0.500 | 9.25 |
| 2026-10-03 09:35:59.264 | YES 5 @ 0.380 | 15 | 0 | 0.440 | — | 15 | 0 | — | 0.000 | 0.360 | 0.600 | 11.40 |
| 2026-10-03 09:36:09.457 | YES 5 @ 0.360 | 20 | 0 | 0.420 | — | 20 | 0 | — | 0.000 | 0.190 | 0.600 | 12.35 |
| 2026-10-03 09:36:20.570 | NO 5 @ 0.680 | 20 | 5 | 0.420 | 0.680 | 15 | 5 | 1.100 | -0.500 | 0.270 | 0.670 | 16.50 |
| 2026-10-03 09:36:43.511 | YES 5 @ 0.270 | 25 | 5 | 0.390 | 0.680 | 20 | 5 | 1.070 | -0.350 | 0.200 | 0.670 | 17.50 |
| 2026-10-03 09:37:19.707 | YES 5 @ 0.190 | 30 | 5 | 0.357 | 0.680 | 25 | 5 | 1.037 | -0.183 | 0.140 | 0.780 | 18.70 |
| 2026-10-03 09:37:22.669 | NO 5 @ 0.780 | 30 | 10 | 0.357 | 0.730 | 20 | 10 | 1.087 | -0.867 | 0.140 | 0.770 | 22.55 |
| 2026-10-03 09:37:54.489 | YES 5 @ 0.200 | 35 | 10 | 0.334 | 0.730 | 25 | 10 | 1.064 | -0.643 | 0.130 | 0.770 | 23.50 |
| 2026-10-03 09:38:18.829 | YES 5 @ 0.140 | 40 | 10 | 0.310 | 0.730 | 30 | 10 | 1.040 | -0.400 | 0.090 | 0.830 | 24.30 |
| 2026-10-03 09:38:30.670 | YES 5 @ 0.090 | 45 | 10 | 0.286 | 0.730 | 35 | 10 | 1.016 | -0.156 | 0.050 | 0.830 | 24.55 |
| 2026-10-03 09:38:56.785 | YES 5 @ 0.050 | 50 | 10 | 0.262 | 0.730 | 40 | 10 | 0.992 | 0.080 | 0.010 | 0.830 | 24.60 |

### Median-activity market: `btc-updown-5m-1790940600` — TotalPnL $-5.00

| Time (UTC) | Fill | QY | QN | AvgY | AvgN | Net | Matched | Pool cost | PairPnL | Working YES | Working NO | Capital |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-10-02 11:30:08.548 | NO 5 @ 0.490 | 0 | 5 | — | 0.490 | -5 | 0 | — | 0.000 | 0.490 | 0.410 | 6.95 |
| 2026-10-02 11:31:05.901 | YES 5 @ 0.490 | 5 | 5 | 0.490 | 0.490 | 0 | 5 | 0.980 | 0.100 | 0.460 | 0.410 | 9.25 |
| 2026-10-02 11:31:21.315 | NO 5 @ 0.600 | 5 | 10 | 0.490 | 0.545 | -5 | 5 | 1.035 | -0.175 | 0.360 | 0.430 | 11.85 |
| 2026-10-02 11:31:23.587 | NO 5 @ 0.260 | 5 | 15 | 0.490 | 0.450 | -10 | 5 | 0.940 | 0.300 | 0.730 | 0.230 | 14.00 |
| 2026-10-02 11:31:26.122 | NO 5 @ 0.160 | 5 | 20 | 0.490 | 0.378 | -15 | 5 | 0.868 | 0.662 | 0.810 | 0.130 | 14.70 |
| 2026-10-02 11:31:40.077 | NO 5 @ 0.130 | 5 | 25 | 0.490 | 0.328 | -20 | 5 | 0.818 | 0.910 | 0.810 | 0.070 | 15.05 |
| 2026-10-02 11:32:21.977 | YES 5 @ 0.960 | 10 | 25 | 0.725 | 0.328 | -15 | 10 | 1.053 | -0.530 | 0.950 | 0.010 | 20.25 |
| 2026-10-02 11:32:34.962 | YES 5 @ 0.940 | 15 | 25 | 0.797 | 0.328 | -10 | 15 | 1.125 | -1.870 | 0.930 | 0.040 | 25.00 |
| 2026-10-02 11:32:51.611 | YES 5 @ 0.950 | 20 | 25 | 0.835 | 0.328 | -5 | 20 | 1.163 | -3.260 | — | — | 24.90 |
| 2026-10-02 11:32:54.916 | NO 5 @ 0.020 | 20 | 30 | 0.835 | 0.277 | -10 | 20 | 1.112 | -2.233 | — | — | 25.00 |

The best market moved BALANCED → NO_HEAVY → BALANCED → YES_HEAVY → BALANCED as fills accumulated. Its pool cost briefly rose when NO filled at 0.91, then later low-price NO fills lowered the final pool cost. The worst market accumulated 40 excess YES shares; its small positive PairPnL did not cover residual settlement loss.

Next action: independently review the B1 action audit and fill paths against the frozen R0 replay and Backtest 01 skew semantics.
