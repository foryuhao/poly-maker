# BTC5m Continuous Inventory Pool — B0 Backtest, 2026-10-03

Result: **NO IMPROVEMENT OVER CYCLE MODEL**. Research only; no production code, live orders or external data feed was used.

## Sample and controls

The replay used exactly the 32 markets selected in `research_outputs/execution_replay_audit_20261003/dataset_audit.csv`; all 32 had both books reconstructed and an identity-checked official R0 settlement label. The other 10 short sessions remain excluded. The selected sample overlaps 19 M4A integrity-PASS sessions. Frozen submit/cancel latencies were 415,281/234,783 μs; cutoff was T−60s. The script uses the same `Book` reconstruction and full-remaining-order trade-through maker fill semantics as R0. No public print size was used to manufacture partial fills. A direct 2-of-5-share accounting check passed; observed partial-fill count was zero.

## Results

| Metric | B0 |
|---|---:|
| Fill records | 2 (mean 0.0625, median 0 per market) |
| Matched shares | 5 total (mean 0.15625, median 0 per market) |
| Pool-wide PairingEfficiency | 100% of the 10 acquired shares; 31 market ratios undefined |
| Aggregate PairPnL | −$0.35 |
| Mean PairPnL / market | −$0.01094 |
| Weighted PoolPairCost | 1.07 |
| PairPnL / matched share | −$0.07 |
| Mean time-weighted absolute net inventory | 0.00402 shares per market |
| Maximum absolute net inventory | 5 shares |
| Aggregate ResidualPnL | $0.00 |
| Mean / median / maximum peak capital | $5.1031 / $4.90 / $10.25 |
| CAPITAL_BLOCKED events | 0 |
| Aggregate TotalPnL | −$0.35; 0 profitable, 1 losing, 31 flat |

The 100% PairingEfficiency is a two-fill artifact, not evidence of sustained balancing. There were 6,628 submitted orders and 6,626 effective cancellations. Current-best-bid refresh and the 0.98 balanced admission rule kept most orders from remaining eligible long enough to meet the inherited maker fill test. No market reached the 25 matched-share target. There was no repeated pool-average improvement from later fills.

## Frozen R0 comparison

| Measure | R0 cycle baseline | B0 continuous pool |
|---|---:|---:|
| Markets | 32 | 32 |
| Mean matched shares / market | 6.25 | 0.15625 |
| PairingEfficiency, aggregate acquired shares | 74.1% | 100% (10 shares only) |
| Aggregate PairPnL | +$5.45 | −$0.35 |
| Aggregate ResidualPnL | −$50.70 | $0.00 |
| Aggregate TotalPnL | −$45.25 | −$0.35 |
| Maximum peak capital | $24.30 | $10.25 |
| Fill records | 108 | 2 |

The smaller B0 total loss reflects almost no filled exposure. It does not establish an economic improvement at comparable throughput.

## Representative paths

Full decision and action paths are in `research_outputs/cip_b0_20261003/{decisions,inventory_path}.csv`. Times below are UTC. Empty averages/pool cost mean that side has no shares.

### Best performing tie: `btc-updown-5m-1790936700`, $0.00

| Time | YES bid | NO bid | QY | QN | AvgY | AvgN | Matched | Net | Pool cost | PairPnL | Capital | Action |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10:25:01.038 | .42 | .56 | 0 | 0 | — | — | 0 | 0 | — | $0 | $4.90 | Place both |
| 10:27:43.017 | .98 | .01 | 0 | 0 | — | — | 0 | 0 | — | $0 | $4.90 | Cancel both |

### Worst performing: `btc-updown-5m-1790966700`, −$0.35

| Time | YES bid | NO bid | QY | QN | AvgY | AvgN | Matched | Net | Pool cost | PairPnL | Capital | Action |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 18:45:01.738 | .62 | .36 | 0 | 0 | — | — | 0 | 0 | — | $0 | $4.90 | Place both |
| 18:48:24.961 | .29 | .68 | 5 | 0 | .30 | — | 0 | +5 | — | $0 | $4.90 | YES fill; YES_HEAVY |
| 18:48:25.031 | .29 | .70 | 5 | 0 | .30 | — | 0 | +5 | — | $0 | $5.00 | Place NO |
| 18:48:32.687 | .23 | .76 | 5 | 5 | .30 | .77 | 5 | 0 | 1.07 | −$0.35 | $5.35 | NO fill; BALANCED |
| 18:48:59.278 | .57 | .42 | 5 | 5 | .30 | .77 | 5 | 0 | 1.07 | −$0.35 | $10.25 | Cancel both |

### Median performing tie: `btc-updown-5m-1790964900`, $0.00

| Time | YES bid | NO bid | QY | QN | AvgY | AvgN | Matched | Net | Pool cost | PairPnL | Capital | Action |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 18:15:00.556 | .53 | .44 | 0 | 0 | — | — | 0 | 0 | — | $0 | $4.85 | Place both |
| 18:17:16.058 | .02 | .97 | 0 | 0 | — | — | 0 | 0 | — | $0 | $4.90 | Cancel both |

The only observed inventory transition was BALANCED → YES_HEAVY → BALANCED. There was no NO_HEAVY transition and no second matched increment. The resulting 1.07 pool cost is allowed by the frozen B0 heavy-side rule; it is not a code rejection or repaired result.

## Validation and limits

The rerun completed all 32 markets with zero replay data errors. A separate check confirmed exact market-set equality with R0, every market's peak filled-plus-reserved capital ≤$25, `PairPnL + ResidualPnL = TotalPnL`, strictly lower public SELL print prices for both fills, a path row for each fill, and immediate 2-share partial-fill accounting in a synthetic in-memory order. The latter validates quantity bookkeeping, not observed partial-fill behavior: R0's frozen rule has only full remaining-order fills. The book validity is the inherited reconstructed BBO validity; the existing replay does not have a separate age threshold on a quiet book. The sample spans two capture periods on one date and 13 of the selected markets fail stricter M4A telemetry integrity checks, so this is execution-replay evidence, not a live maker-fill estimate.

Reproduce with `python3 research_tools/cip_b0_backtest.py` from the repository root. Outputs under `research_outputs/cip_b0_20261003/` are ignored local research artifacts: `summary.json`, `market_results.csv`, `fills.csv`, `decisions.csv`, and `inventory_path.csv`. The R0 settlement labels are read from the existing ignored R0 output; no network call is made.

Next action: independently review the B0 replay implementation and its two-fill path against the frozen R0 execution semantics.
