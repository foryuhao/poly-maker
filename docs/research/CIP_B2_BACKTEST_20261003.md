# BTC5m Two-Phase Continuous Inventory Strategy — B2 Backtest, 2026-10-03

Result: **TWO_PHASE MODEL SUPPORTED** under the frozen 20% residual / $10 total-PnL materiality criteria in `CIP_B2_RESEARCH_DESIGN.md`. Research only; no production trading code or external interface changed.

## Sample and exact replay controls

All 32 R0/B0/B1 execution-replayable BTC5m markets replayed with the same captured official data and identity-checked official settlement labels. There were zero replay data errors. Nineteen overlap the stricter M4A integrity-PASS subset. The B2 script calls B1's existing gamma `reconcile` before T150; **every pre-T150 order submission and fill matches B1 exactly** by market, side, price, timestamp and fill evidence. Both use the same R0 book and full-remaining-order trade-through fill model, available-time event order, 415,281 μs activation and 234,783 μs cancel latency. B2 switches at the exact 150-second boundary, with the T150 snapshot taken before its cancellation request. Reduce-only lasts to T300 as specified; B1's T240 cutoff is not applied to B2.

## Phase 1 — Gamma Harvest, T0–T150

| Metric | B2 at T150 |
|---|---:|
| Fills / purchased shares | 238 / 1,190 |
| YES / NO shares acquired | 580 / 610 |
| Matched shares | 405 total; 12.656 per market |
| Residual shares | 380 total; 11.875 per market |
| Matched-share-weighted PoolPairCost | 1.004336 |
| Aggregate GammaPnL | −$1.7560 |
| Markets already flat at switch | 6 |

The aggregate T150 pool cost is slightly above 1.00. Later reduction fills increase matched quantity and move final average costs, but are classified as risk reduction rather than fresh gamma harvesting.

## Phase 2 — Reduce Only, T150–T300

| Metric | B2 |
|---|---:|
| Risk-reducing fills / acquired shares | 27 / 135 |
| Matched shares added mechanically after switch | 135 |
| Residual reduction from B2 T150 to final | 135 shares; 35.53% |
| Nonflat at T150 / reached flat after switch | 26 / 9 |
| Flat rate of T150-nonflat markets | 34.62% |
| Median / p75 / p90 time to flat | 28.987s / 44.936s / 53.877s |
| Not flat by T300 | 17 markets |
| `REDUCE_CAPITAL_BLOCKED` | 4,075 decision events in 11 markets |
| Time capital blocked / mean absolute net while blocked | 506.804s / 14.987 shares |
| `REDUCE_CANCEL_RACE_OVERSHOOT` | 0 events, 0 added shares |
| `REDUCE_OVERSHOOT_SIGN_FLIP` | 0 events, 0 excess shares |

Capital-blocked events can repeat while a constraint persists; they are not distinct rejected orders. The 5-share frozen fill rule and five-share inventory increments produced no observed sign flips. The simulator nevertheless retains cancel-latency fill eligibility and records both overshoot types if they occur.

## Final economics and B1 comparison

| Measure | Frozen B1 | B2 | B2 − B1 |
|---|---:|---:|---:|
| Final matched shares | 540 | 540 | 0 |
| Gamma/PairPnL | +$16.2105 | +$3.0267 | −$13.1838 |
| Final residual shares | 405 | 245 | −160 (−39.51%) |
| ResidualPnL | −$115.9805 | −$72.4467 | +$43.5338 |
| TotalPnL | −$99.77 | −$69.42 | **+$30.35** |
| Mean / median / max peak capital | $22.53 / $24.35 / $25.00 | $21.23 / $23.25 / $25.00 | lower mean and median |
| Profitable / losing / flat markets | 7 / 24 / 1 | 7 / 24 / 1 | unchanged |

The B1 full-horizon GammaPnL of +$16.2105 exceeds B2 final GammaPnL by $13.1838; this is the observed same-market matched-edge cost of the B2 policy. Because their post-T150 actions diverge and B1 stops new exposure at T240 while B2 reduces through T300, it is not an isolated estimate of one missed trade opportunity. B2's $43.5338 residual-PnL improvement exceeds that matched-edge cost, improving TotalPnL by $30.35. Excluding the single best paired market improvement (+$7.15) still leaves +$23.20. The B2 T150 GammaPnL is negative, so an aggregate `GammaRetention=TotalPnL/GammaPnL_T150` is undefined under the specified positive-denominator rule; 16 markets have a defined per-market ratio in `market_results.csv`.

## Quote persistence and safety audit

| Measure | B2 |
|---|---:|
| Order submissions / accepted orders | 1,244 / 1,244 |
| Cancel requests / final canceled orders | 996 / 979 |
| Raw fills / purchased shares | 265 / 1,325 |
| Fills per 100 submissions | 21.302 |
| Median / mean resting lifetime | 3.480s / 7.495s |
| `BBO_ONLY_REPRICE` | **0** |

The action audit contains no reduce-only placement on the heavy side, no return to gamma after T150, and no `BOOK_INVALID` cancellation with a valid reconstructed book. All 265 fills meet the inherited strict price and three post-activation timestamp predicates. Every market satisfies filled purchase cost plus cancel-pending/open reserve ≤$25, and `GammaPnL + ResidualPnL = TotalPnL` equals the final official winner payout less cumulative purchase cost. The 32 T150 transition timestamps and the 32 final phase states are exact. A separate in-memory check exercised a cancel-pending heavy-side fill and a `+2 → −3` sign flip; neither rare event occurred in the frozen 32-market replay.

## Representative paths

The five tables below include every fill plus the exact T150 switch and final snapshot for the best, worst, median-activity, largest-T150-residual and separately flattened examples. The T150 row shows working order prices **after the cancel request**; `phase_transitions.csv` preserves the mandatory pre-cancel T150 quantities, costs, averages, capital, order IDs/prices/remaining quantities/statuses, and the post-switch order statuses. All times are UTC. `—` means no fill, no inventory average, or no working order.

### Best total-PnL market: `btc-updown-5m-1791015900`

T150 residual 20 → final 0 shares; TotalPnL +$12.10.

| Time (UTC) | Phase | Fill | QY/QN | Net/Matched | AvgY/AvgN | Cost/GammaPnL | Working YES/NO | Capital | Action | Reason code |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 10-03 08:25:17.510 | GAMMA | NO 5 @ 0.520 | 0/5 | -5/0 | —/0.520 | —/0.000 | 0.470/0.490 | 7.40 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:25:36.025 | GAMMA | YES 5 @ 0.300 | 5/5 | 0/5 | 0.300/0.520 | 0.820/0.900 | 0.250/0.690 | 8.80 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:25:43.895 | GAMMA | YES 5 @ 0.250 | 10/5 | 5/5 | 0.275/0.520 | 0.795/1.025 | 0.240/0.690 | 10.00 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:26:30.268 | GAMMA | YES 5 @ 0.330 | 15/5 | 10/5 | 0.293/0.520 | 0.813/0.933 | 0.260/0.660 | 11.60 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:26:55.474 | GAMMA | YES 5 @ 0.210 | 20/5 | 15/5 | 0.273/0.520 | 0.792/1.038 | 0.190/0.770 | 12.85 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:26:57.106 | GAMMA | YES 5 @ 0.190 | 25/5 | 20/5 | 0.256/0.520 | 0.776/1.120 | 0.160/0.770 | 13.65 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:27:02.937 | GAMMA | YES 5 @ 0.090 | 30/5 | 25/5 | 0.228/0.520 | 0.748/1.258 | 0.050/0.880 | 14.10 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:27:17.760 | GAMMA | NO 5 @ 0.910 | 30/10 | 20/10 | 0.228/0.715 | 0.943/0.567 | 0.060/0.900 | 18.80 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-03 08:27:30.000 | REDUCE | PHASE_SWITCH | 30/10 | 20/10 | 0.228/0.715 | 0.943/0.567 | 0.060/0.900 | 18.80 | PHASE_SWITCH|CANCEL | T150|PHASE_SWITCH_HEAVY |
| 10-03 08:28:03.720 | REDUCE | NO 5 @ 0.220 | 30/15 | 15/15 | 0.228/0.550 | 0.778/3.325 | —/0.210 | 16.15 | FILLED|PLACE | ORDER_FILLED|REDUCE_RISK |
| 10-03 08:28:06.808 | REDUCE | NO 5 @ 0.210 | 30/20 | 10/20 | 0.228/0.465 | 0.693/6.133 | —/0.180 | 17.05 | FILLED|PLACE | ORDER_FILLED|REDUCE_RISK |
| 10-03 08:28:18.764 | REDUCE | NO 5 @ 0.180 | 30/25 | 5/25 | 0.228/0.408 | 0.636/9.092 | —/0.170 | 17.90 | FILLED|PLACE | ORDER_FILLED|REDUCE_RISK |
| 10-03 08:28:21.171 | REDUCE | NO 5 @ 0.170 | 30/30 | 0/30 | 0.228/0.368 | 0.597/12.100 | —/— | 17.90 | FILLED | ORDER_FILLED |
| 10-03 08:30:00.000 | REDUCE | FINAL | 30/30 | 0/30 | 0.228/0.368 | 0.597/12.100 | —/— | 17.90 | — | — |

### Worst total-PnL market: `btc-updown-5m-1790936700`

T150 residual 15 → final 15 shares; TotalPnL −$9.95.

| Time (UTC) | Phase | Fill | QY/QN | Net/Matched | AvgY/AvgN | Cost/GammaPnL | Working YES/NO | Capital | Action | Reason code |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 10-02 10:25:15.085 | GAMMA | YES 5 @ 0.420 | 5/0 | 5/0 | 0.420/— | —/0.000 | 0.360/0.560 | 6.70 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:25:25.089 | GAMMA | NO 5 @ 0.650 | 5/5 | 0/5 | 0.420/0.650 | 1.070/-0.350 | 0.340/0.640 | 10.25 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:25:30.465 | GAMMA | NO 5 @ 0.640 | 5/10 | -5/5 | 0.420/0.645 | 1.065/-0.325 | 0.340/0.570 | 13.10 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:25:34.990 | GAMMA | NO 5 @ 0.570 | 5/15 | -10/5 | 0.420/0.620 | 1.040/-0.200 | 0.340/0.540 | 15.80 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:25:37.263 | GAMMA | NO 5 @ 0.540 | 5/20 | -15/5 | 0.420/0.600 | 1.020/-0.100 | 0.340/0.480 | 18.20 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:25:42.100 | GAMMA | NO 5 @ 0.340 | 5/25 | -20/5 | 0.420/0.548 | 0.968/0.160 | 0.640/0.240 | 20.20 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:25:50.594 | GAMMA | YES 5 @ 0.750 | 10/25 | -15/10 | 0.585/0.548 | 1.133/-1.330 | 0.720/0.220 | 24.25 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:25:52.575 | GAMMA | YES 5 @ 0.720 | 15/25 | -10/15 | 0.630/0.548 | 1.178/-2.670 | —/0.220 | 24.25 | FILLED|SKIP | ORDER_FILLED|CAPITAL_CONSTRAINT |
| 10-02 10:25:56.559 | GAMMA | NO 5 @ 0.360 | 15/30 | -15/15 | 0.630/0.517 | 1.147/-2.200 | —/— | 24.95 | FILLED|SKIP|SKIP | ORDER_FILLED|CAPITAL_CONSTRAINT|CAPITAL_CONSTRAINT |
| 10-02 10:27:30.000 | REDUCE | PHASE_SWITCH | 15/30 | -15/15 | 0.630/0.517 | 1.147/-2.200 | —/— | 24.95 | PHASE_SWITCH|SKIP | T150|REDUCE_CAPITAL_BLOCKED |
| 10-02 10:30:00.000 | REDUCE | FINAL | 15/30 | -15/15 | 0.630/0.517 | 1.147/-2.200 | —/— | 24.95 | — | — |

### Median-activity market: `btc-updown-5m-1790937900`

T150 residual 15 → final 15 shares; TotalPnL −$2.95.

| Time (UTC) | Phase | Fill | QY/QN | Net/Matched | AvgY/AvgN | Cost/GammaPnL | Working YES/NO | Capital | Action | Reason code |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 10-02 10:45:21.707 | GAMMA | NO 5 @ 0.600 | 0/5 | -5/0 | —/0.600 | —/0.000 | 0.390/0.580 | 7.85 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:45:34.727 | GAMMA | NO 5 @ 0.620 | 0/10 | -10/0 | —/0.610 | —/0.000 | 0.370/0.570 | 10.80 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:45:59.472 | GAMMA | YES 5 @ 0.370 | 5/10 | -5/5 | 0.370/0.610 | 0.980/0.100 | 0.330/0.600 | 12.60 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:46:00.170 | GAMMA | YES 5 @ 0.330 | 10/10 | 0/10 | 0.350/0.610 | 0.960/0.400 | 0.300/0.600 | 14.10 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:46:02.578 | GAMMA | YES 5 @ 0.290 | 15/10 | 5/10 | 0.330/0.610 | 0.940/0.600 | 0.280/0.700 | 15.95 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:46:27.410 | GAMMA | YES 5 @ 0.280 | 20/10 | 10/10 | 0.318/0.610 | 0.927/0.725 | 0.250/0.700 | 17.20 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:46:42.281 | GAMMA | YES 5 @ 0.210 | 25/10 | 15/10 | 0.296/0.610 | 0.906/0.940 | 0.180/0.770 | 18.25 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:47:02.353 | GAMMA | NO 5 @ 0.810 | 25/15 | 10/15 | 0.296/0.677 | 0.973/0.410 | 0.170/0.760 | 22.20 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:47:19.546 | GAMMA | YES 5 @ 0.080 | 30/15 | 15/15 | 0.260/0.677 | 0.937/0.950 | 0.050/0.860 | 22.50 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 10:47:30.000 | REDUCE | PHASE_SWITCH | 30/15 | 15/15 | 0.260/0.677 | 0.937/0.950 | 0.050/0.860 | 22.50 | PHASE_SWITCH | T150 |
| 10-02 10:50:00.000 | REDUCE | FINAL | 30/15 | 15/15 | 0.260/0.677 | 0.937/0.950 | —/— | 17.95 | — | — |

### Largest T150 residual: `btc-updown-5m-1790939100`

T150 residual 30 → final 25 shares; TotalPnL −$9.25.

| Time (UTC) | Phase | Fill | QY/QN | Net/Matched | AvgY/AvgN | Cost/GammaPnL | Working YES/NO | Capital | Action | Reason code |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 10-02 11:05:05.720 | GAMMA | NO 5 @ 0.330 | 0/5 | -5/0 | —/0.330 | —/0.000 | 0.660/0.290 | 6.40 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:05:47.172 | GAMMA | NO 5 @ 0.430 | 0/10 | -10/0 | —/0.380 | —/0.000 | 0.560/0.400 | 8.60 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:06:14.012 | GAMMA | NO 5 @ 0.450 | 0/15 | -15/0 | —/0.403 | —/0.000 | 0.480/0.270 | 9.80 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:06:21.635 | GAMMA | NO 5 @ 0.270 | 0/20 | -20/0 | —/0.370 | —/0.000 | 0.480/0.240 | 11.00 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:06:46.825 | GAMMA | NO 5 @ 0.230 | 0/25 | -25/0 | —/0.342 | —/0.000 | 0.740/0.190 | 13.20 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:07:09.728 | GAMMA | NO 5 @ 0.200 | 0/30 | -30/0 | —/0.318 | —/0.000 | 0.770/0.080 | 13.80 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:07:30.000 | REDUCE | PHASE_SWITCH | 0/30 | -30/0 | —/0.318 | —/0.000 | 0.880/0.090 | 14.40 | PHASE_SWITCH|CANCEL | T150|PHASE_SWITCH_HEAVY |
| 10-02 11:08:13.354 | REDUCE | YES 5 @ 0.940 | 5/30 | -25/5 | 0.940/0.318 | 1.258/-1.292 | 0.910/— | 18.80 | FILLED|PLACE | ORDER_FILLED|REDUCE_RISK |
| 10-02 11:10:00.000 | REDUCE | FINAL | 5/30 | -25/5 | 0.940/0.318 | 1.258/-1.292 | —/— | 14.25 | — | — |

### Flattened after T150: `btc-updown-5m-1790940000`

T150 residual 10 → final 0 shares; TotalPnL +$0.80.

| Time (UTC) | Phase | Fill | QY/QN | Net/Matched | AvgY/AvgN | Cost/GammaPnL | Working YES/NO | Capital | Action | Reason code |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 10-02 11:20:07.972 | GAMMA | NO 5 @ 0.360 | 0/5 | -5/0 | —/0.360 | —/0.000 | 0.620/0.350 | 6.65 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:20:20.473 | GAMMA | YES 5 @ 0.690 | 5/5 | 0/5 | 0.690/0.360 | 1.050/-0.250 | 0.670/0.300 | 10.10 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:20:32.720 | GAMMA | NO 5 @ 0.300 | 5/10 | -5/5 | 0.690/0.330 | 1.020/-0.100 | 0.670/0.280 | 11.50 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:21:04.040 | GAMMA | NO 5 @ 0.380 | 5/15 | -10/5 | 0.690/0.347 | 1.037/-0.183 | 0.610/0.280 | 13.10 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:21:05.154 | GAMMA | NO 5 @ 0.280 | 5/20 | -15/5 | 0.690/0.330 | 1.020/-0.100 | 0.610/0.240 | 14.30 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:21:14.750 | GAMMA | NO 5 @ 0.240 | 5/25 | -20/5 | 0.690/0.312 | 1.002/-0.010 | 0.610/0.200 | 15.30 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:21:58.928 | GAMMA | YES 5 @ 0.620 | 10/25 | -15/10 | 0.655/0.312 | 0.967/0.330 | 0.580/0.350 | 19.00 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:22:18.735 | GAMMA | YES 5 @ 0.620 | 15/25 | -10/15 | 0.643/0.312 | 0.955/0.670 | 0.600/0.360 | 22.25 | FILLED|PLACE | ORDER_FILLED|POST_FILL_NEW_ORDER |
| 10-02 11:22:30.000 | REDUCE | PHASE_SWITCH | 15/25 | -10/15 | 0.643/0.312 | 0.955/0.670 | 0.640/0.340 | 22.35 | PHASE_SWITCH|CANCEL | T150|PHASE_SWITCH_HEAVY |
| 10-02 11:22:43.731 | REDUCE | YES 5 @ 0.690 | 20/25 | -5/20 | 0.655/0.312 | 0.967/0.660 | 0.660/— | 24.20 | FILLED|PLACE | ORDER_FILLED|REDUCE_RISK |
| 10-02 11:22:56.929 | REDUCE | YES 5 @ 0.660 | 25/25 | 0/25 | 0.656/0.312 | 0.968/0.800 | —/— | 24.20 | FILLED | ORDER_FILLED |
| 10-02 11:25:00.000 | REDUCE | FINAL | 25/25 | 0/25 | 0.656/0.312 | 0.968/0.800 | —/— | 24.20 | — | — |

In `btc-updown-5m-1790940000`, T150 found Net = −10 with YES and NO resting. The switch requested cancellation of the heavy NO order and kept YES. Two subsequent YES maker fills at 11:22:43.731 and 11:22:56.929 reduced Net to −5 and then zero; the market stopped with no working orders.

## Limitations and reproducibility

The inherited R0 maker fill rule fills an entire remaining clip on one qualifying public SELL print regardless of print size and does not model queue position; it may overstate executable shares. The 32 sessions are execution replayable but only 19 pass stricter M4A integrity. B2 uses the specified T150–T300 reduce window, whereas B1 used a T240 pre-close cutoff, so the B1 comparison reflects the complete policy difference. No parameter tuning, external price feed, on-chain source or real order was used. Run `python3 research_tools/cip_b2_two_phase_backtest.py` from the repository root; ignored local outputs are under `research_outputs/cip_b2_20261003/`.

Next action: independently review B2 phase-transition timing, reduce-only action safety and the 27 post-switch fill paths.
