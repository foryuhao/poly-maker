# DP Historical Backtest 04 — Convergence-Aware Target Inventory

**Classification: RESIDUAL QUALITY IMPROVES BUT PNL NOT YET POSITIVE.** Research-only pilot, 2026-10-03, Git base `97ab0f9` plus the pre-existing untracked Backtest 01–03 artifacts. No production trading code or orders changed. Existing unrelated working-tree changes were left untouched.

## Question and verdict

Does replacing B's fixed zero inventory target with a simple price-time target create observable BTC5m maker edge? **E improves residual quality and raises settlement PnL by $0.350, but remains negative at −$5.695 and does not establish a robust alpha.** Only three of 20 markets change PnL, two E-only fills are associated with the target, and the observed high-price convergence region has just 14 independent markets. The improvement is an informative structural result, not a production-ready risk/return claim.

## Frozen universe, source and causal surface

The universe is the same 20 `execution_replay_usable=True` markets selected in Backtest 01 `dataset_audit.csv`, covering 100 market-minutes in two observed periods. The market/session/condition/token identity-validated official winner from Backtest 01 is read only for the **post-hoc** convergence surface and settlement accounting. It never enters E's target or quote decision. The original competition Quote Filter and Backtest 03 toxicity gate are excluded.

The surface was built and inspected **before** declaring the E rule or comparing E PnL. Each market is sampled at one-second boundaries using the most recent locally available YES and NO BBO; both token books must be complete and non-crossed. The higher midpoint is the leader. A tie is excluded. The sample records the YES/NO mids, leader mid, elapsed and remaining seconds, and eventual official winner. There are **4,478** valid one-second observations over 20 markets. Those are correlated within a market. Each cell reports raw observations and distinct markets; it also uses the **first qualifying second per market in that cell** for a market-level win rate and price edge. All cells and all second-level rows are in `convergence_surface.csv` and `convergence_observations.csv`.

Time buckets are fixed at 0–60, 60–120, 120–180, 180–210, 210–240, 240–270, and 270–300 seconds. Leader-mid buckets are fixed at 0.50–0.60, 0.60–0.70, 0.70–0.80, 0.80–0.90, 0.90–0.95, and 0.95–1.00. Buckets are research summaries, not fit parameters. Edge is `leader final win rate − mean contemporaneous leader midpoint`; reversal rate is `1 − win rate`.

### Late convergence surface

| Elapsed | Leader mid | Observations | Markets | Raw win rate | Mean mid | Raw edge | First/market edge |
|---|---:|---:|---:|---:|---:|---:|---:|
| 210–240 s | 0.80–0.90 | 70 | 8 | 78.6% | 0.8650 | −7.93 pp | −11.62 pp |
| 210–240 s | 0.90–0.95 | 46 | 6 | 100% | 0.9305 | +6.95 pp | +7.92 pp |
| 210–240 s | 0.95–1.00 | 75 | 9 | 100% | 0.9774 | +2.26 pp | +2.72 pp |
| 240–270 s | 0.90–0.95 | 7 | 2 | 100% | 0.9436 | +5.64 pp | +5.50 pp |
| 240–270 s | 0.95–1.00 | 70 | 8 | 100% | 0.9762 | +2.38 pp | +3.39 pp |
| 270–300 s | 0.95–1.00 | 34 | 3 | 100% | 0.9785 | +2.15 pp | +2.40 pp |

The required overlapping focus regions are below. `First/market` retains only each market's earliest qualifying observation in that entire region, so the rows cannot be summed across regions.

| Focus region | Observations | Markets | Leader win rate | Reversal markets | Mean mid | Raw edge | First/market edge |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mid ≥0.90, elapsed ≥210 s | 233 | 14 | 100% | 0 | 0.9667 | +3.33 pp | +4.82 pp |
| Mid ≥0.95, elapsed ≥210 s | 179 | 14 | 100% | 0 | 0.9772 | +2.28 pp | +3.21 pp |
| Mid ≥0.90, elapsed ≥240 s | 112 | 8 | 100% | 0 | 0.9744 | +2.56 pp | +3.64 pp |
| Mid ≥0.95, elapsed ≥240 s | 104 | 8 | 100% | 0 | 0.9770 | +2.30 pp | +3.39 pp |

The positive point edges are **not** persuasive evidence that market prices understate the win probability. For the 14 earliest mid ≥0.90 observations after 210 s, the average midpoint is **0.9518**. Treating those midpoints as calibrated independent win probabilities, their product is **0.498**: observing all 14 leaders win is an ordinary event under the market's own prices. The one-sided 95% binomial lower bound for 14/14 wins is only **80.7%**, below the observed mean midpoint; cross-market correlation would weaken that bound. These are uncertainty diagnostics, not an alternative target model.

## The single predeclared E rule

The surface supports a minimal test of the strongest late region and does not justify a second size tier. Before seeing E outcomes, the rule was frozen as:

```text
if both token BBOs valid and elapsed_seconds >= 210 and leader_mid >= 0.90:
    target_inventory_E = +5 if YES leads else -5
else:
    target_inventory_E = 0
inventory_error_E = (YES_shares - NO_shares) - target_inventory_E
skew_ticks = round(abs(inventory_error_E) * min(0.08, 2.8/5))
retreat only the side with positive inventory_error_E
```

This uses only currently available prices and elapsed time. It is a structural, in-sample research convention, not an optimized threshold or validated production parameter. A stronger ±10-share tier was not used. The sign convention is YES positive, NO negative. When inventory equals the target, E applies no skew; when inventory exceeds the target on a side, the original asymmetric heavy-side retreat applies. The coefficient, rounding, five-share size, maker quote level, validity checks, order lifecycle, and fill assumptions are unchanged.

B is the exact Backtest 01 `Policy("B")` with target zero. The replay shares the B decision clock: BBO/tick changes, order lifecycle transitions, and simulated fills. The same measured 412,289 µs activation and 231,100 µs cancel delays apply. A fill requires a positive public SELL print strictly below an active simulated BUY quote, with exchange, receipt and local-availability timestamps all after activation. Same-price prints and book reductions never fill. The script compares every B order, fill, and original market metric to the frozen Backtest 01 output; all parity checks passed.

## B versus E economics and inventory

Settlement PnL is winner-side shares minus gross purchase cost, with no rebate, reward, incentive, or external BTC signal. Time-weighted residual is the average over the full five minutes of `abs(YES−NO)`. Signed residual is positive when net inventory points toward the eventual official winner; this label is post-hoc only.

| Metric, 20 markets | B, zero target | E, convergence target |
|---|---:|---:|
| Total settlement PnL | −$6.045 | −$5.695 |
| Mean / median market PnL | −$0.302 / $0 | −$0.285 / $0 |
| Profitable / losing markets | 9 / 7 | 9 / 7 |
| Best / worst market | +$3.950 / −$6.200 | +$4.050 / −$6.200 |
| Mean time-weighted absolute residual | 3.691 | 3.745 |
| Mean final / maximum residual | 5.75 / 20 | 6.00 / 20 |
| Mean signed final residual toward winner | +1.75 | +2.50 |
| Matched / unmatched shares | 140 / 115 | 140 / 120 |
| Simulated orders / conservative fills | 5,772 / 79 | 5,770 / 80 |

E's absolute residual risk rises slightly, while its residual **direction** improves. No normal entry gate or participation suppression explains this result. Its one additional net fill arises from two E-only strict fills and one B-only fill.

### Matched and residual decomposition

The accounting assigns each token's purchase cost at that market's volume-weighted average purchase price, then attributes `min(YES, NO)` shares to paired inventory and the remaining shares to residual inventory. This is an **approximate cost allocation**, not a claim that specific simulated fills were merged. It reconciles exactly to each market's settlement PnL.

| Component | B | E | E−B |
|---|---:|---:|---:|
| Matched shares | 140 | 140 | 0 |
| Matched contribution | −$4.854 | −$7.338 | −$2.484 |
| Final winning residual | 75 shares | 85 shares | +10 |
| Final losing residual | 40 shares | 35 shares | −5 |
| Residual contribution | −$1.191 | +$1.643 | +$2.834 |
| Total PnL | −$6.045 | −$5.695 | +$0.350 |

The incremental result is residual-led, while matched economics become worse. It does **not** show that the combined market-making strategy is profitable.

## Exposure, fill counterfactual and reversal risk

The target is nonzero in **13 of 20** replay markets for a combined **226.023 seconds**, split into 56 intervals as prices cross the fixed boundary. The surface has 14 qualifying markets; one has a sampled qualifying second but no subsequent B-clock decision that sets E's target. Six E fills occur while a target is on: four buy the current leader (20 shares) and two buy the other token (10 shares) through previously live orders. Four buy the eventual winner, two the loser. The maximum leader-aligned net position observed during a target interval is **20 shares**. Twenty-one target intervals carry some leader-aligned residual.

Of the 80 E fills, **78** share the same public trade-event key with B. Two E-only fills are associated with a nonzero target; both buy the eventual winner at $0.98 and contribute **+$0.200** combined after settlement. One B-only fill buys the eventual loser at $0.03, avoiding **−$0.150** of B contribution. These three path differences account for the **+$0.350** PnL delta. `convergence_fills.csv` records every E fill's side, price, elapsed time, target at order submission and fill, inventory before/after, leader, winner, shared-versus-E-only status, and ex-post correct/wrong classification. Shared fills while the target is on are not claimed as target-caused.

There are **zero observed leader reversals** in the target region, zero intervals where E carries biased inventory toward a leader that later loses, and zero realized reversal-loss cases. `reversal_cases.csv` is header-only to make that null result explicit. This is a severe sampling limitation, not evidence of negligible tail risk: the rule can coexist with up to 20 shares of leader-aligned residual in this sample, while only 14 markets independently enter the sampled high-price late region. No reversal-loss distribution can be estimated.

## Market-level robustness

E beats B in **3** markets, loses in **0**, and ties in **17**. Aggregate E−B is **+$0.350**. The best single delta is **+$0.150** and the worst is $0; excluding the best leaves **+$0.200**, and excluding the worst leaves **+$0.350**. The top one and top two positive markets contribute **+$0.150** and **+$0.250** respectively, or 43% and 71% of the aggregate. The sign is not solely one-market-driven, but economic activity is concentrated in three markets and two target-associated E-only fills. There is no broad replay evidence of a scalable edge.

## Interpretation, limits and handoff

E improves the *observed quality* of final residual and its approximate residual settlement contribution, with a $0.350 core PnL gain, but its total PnL remains negative and mean absolute residual grows. The high-price win count alone cannot establish mispricing: all 14 winners are compatible with contemporaneous prices. The threshold was chosen from this same 20-market surface, so B/E results are in-sample; there is no independent holdout and no observed high-price reversal to characterize the downside. Public strict trade-through remains a hypothetical execution proxy, not verified own fills. The decision clock may miss elapsed-time threshold crossings until another B decision trigger occurs. No threshold or size sweep, skew retuning, entry filter, rebate, external BTC, directional model, or production change was used.

Reproduce from repository root with `python3 research_tools/dp_backtest_04_convergence_target.py`. The optional `--surface-only` mode writes the surface and `surface_preview.json` without running E; that preview from the pre-rule pass is retained. Outputs are `convergence_surface.csv`, `convergence_observations.csv`, `market_results.csv`, `paired_markets.csv`, `orders.csv`, `fills.csv`, `convergence_fills.csv`, `target_intervals.csv`, `reversal_cases.csv`, and `summary.json` under `research_outputs/dp_backtest_04/`. Python compilation, frozen B parity, residual-accounting reconciliation, and output sanity checks passed. Status: **research result recorded; not independently reviewed or checkpointed**.

**Next action:** Test this single frozen target rule on additional independent BTC5m markets that include late high-price reversals.
