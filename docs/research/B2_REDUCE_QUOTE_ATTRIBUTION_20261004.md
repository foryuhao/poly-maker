# B2 Reduce Quote Competitiveness Attribution — 2026-10-04

Result: **MIXED EXECUTION BOTTLENECK**. This is a read-only analysis of the frozen B2 path; no quote, order, inventory, PnL or trading rule was changed.

## Sample and method

The population is all **113** new risk-reducing BUY orders submitted and accepted at or after T150 across B2's **32** markets: **24 filled**, **89 no-fill**. They are all YES BUY with negative net or NO BUY with positive net at acceptance. B2's stage total of 27 Reduce fills includes three pre-T150 orders that filled after the switch; those are excluded from this order-level cohort. B2 source identities and 380 T150 / 245 final residual shares reconciled. The script reconstructed the frozen R0/B2 book from **7,700,057** book events on the original available-time stream; zero nonmonotone events were skipped and no analytical errors were detected.

For each order, the signed distance is `round((same-side best bid − order price) / current tick size)`. Negative means the order is more aggressive than the displayed best bid. Event medians use valid book events; time shares and time-weighted means integrate actual elapsed time. Group time percentages pool valid resting seconds, while the reported median time-weighted distance is the median of per-order time-weighted means. There were **788.780 valid-book order-seconds** and **2.479 invalid-book order-seconds**. Six no-fill orders had no valid-book seconds, including five accepted and canceled at the same activation timestamp; their distance metrics are null, not imputed.

## Q1 — Filled versus no-fill competitiveness

| Metric | Filled | No-fill |
|---|---:|---:|
| Order count | 24 | 89 |
| Median resting lifetime | 4.568s | 2.332s |
| Median distance at acceptance | 0 ticks | 0 ticks |
| Median event-observed distance | −1 tick | 0 ticks |
| Median order time-weighted mean distance | **−1.411 ticks** | **+0.278 ticks** |
| Valid time exactly at best bid | 34.9% | 25.8% |
| Valid time within 1 tick, including negative distances | 88.0% | 56.7% |
| Valid time more than 2 ticks behind | **5.9%** | **37.8%** |
| Valid time more than 5 ticks behind | **0.6%** | **22.4%** |

The difference is large descriptively: no-fill orders spent 31.9 percentage points more valid time over two ticks behind. The median order time-weighted distance gap (no-fill minus filled) was **+1.689 ticks**; a deterministic 5,000-repetition market-cluster bootstrap gave a **95% interval of +0.394 to +2.813 ticks**. Both groups were placed at the current best bid at acceptance (median zero), so the separation developed during resting. This is observational separation, not a randomized estimate of quote distance's causal effect.

## Q2 — Distance at successful fill

All **24** fills occurred with signed distance **≤0 ticks** immediately before the qualifying SELL print. The median was **−3 ticks**, p75 and p90 were **−1 tick**, and every fill had a valid reconstructed book. A negative reading means the persistent order price was higher than the then-displayed bid. Under B2's strict trade-through fill predicate, this is consistent with prints reaching an order that had become more aggressive than the contemporaneous book.

## Q3 — Time spent behind the bid

| Distance bucket | Valid resting seconds | Orders entering bucket | Fills in bucket | Fills per 100 entering orders* |
|---|---:|---:|---:|---:|
| ≤0 ticks | 353.198 | 96 | 24 | 25.0 |
| 1 tick | 134.884 | 51 | 0 | 0 |
| 2 ticks | 44.176 | 41 | 0 | 0 |
| 3–5 ticks | 108.525 | 32 | 0 | 0 |
| >5 ticks | 147.997 | 16 | 0 | 0 |

\* Descriptive count divided by bucket entrants; it is not a queue-fill probability. An order can enter several buckets, and bucket entry counts overlap. No-fill orders alone spent **248.780 seconds** more than two ticks behind and **147.148 seconds** more than five ticks behind, equal to 37.8% and 22.4% of their valid-book resting time. However, the median time from a no-fill order's last ≤1-tick competitive state to closure was **0 seconds** among the 78 with such a state: staleness was often transient rather than a continuous terminal condition. Thirteen of those 78 spent more than two seconds after their last competitive state; 11 no-fill orders never had an observed competitive valid-book interval.

## Best-bid drift

`max(best bid during order lifetime) − order price`, in dollars:

| Group | Mean | Median | p75 | p90 | Defined orders |
|---|---:|---:|---:|---:|---:|
| Filled | −$0.0021 | $0.0000 | $0.0025 | $0.0200 | 24 |
| No-fill | +$0.0272 | +$0.0100 | +$0.0400 | +$0.0700 | 83 |

The six no-fill orders without valid book time are excluded. Drift supports the same descriptive competitiveness gap as the time buckets.

## Q4 — Observational best-bid opportunity

Each of the **89** no-fill orders was screened at valid book events *before its actual cancel request* where the current same-side bid exceeded its price. A hypothetical quote at that bid had to fit B2's actual $25 capital path, wait the frozen **234,783 μs cancel plus 415,281 μs submit delay**, and then meet B2's strict price and three-timestamp trade-through predicate on a later SELL print before the original order closed. **16/89 (18.0%)** had a raw timing/print opportunity; **14/89 (15.7%)** also passed the capital check. The median time from hypothetical quote observation to qualifying print among those 14 was **2.555 seconds**; from the original order's acceptance it was **8.693 seconds**. These are conditional historical opportunities, **not** simulated fills of an implemented requote strategy and **not** additive PnL.

## Inventory and market context

| Inventory at acceptance | Orders | No-fill rate | Valid time >2 ticks behind | Median order time-weighted distance |
|---|---:|---:|---:|---:|
| `abs(Net) ≤ 10` | 74 | 78.4% | 27.5% | −0.381 ticks |
| `abs(Net) > 10` | 39 | 79.5% | 36.5% | +0.263 ticks |

Larger accepted inventory coincided with more stale quote time, but almost the same no-fill rate. Among markets with Reduce orders, those ending with >10 residual shares had **7.9%** median market stale-time share, versus **13.8%** for those ending with ≤10; large terminal residual does not consistently coincide with less competitive Reduce quotes. Per-market T150 residual, order counts, distance and final residual are in `market_summary.csv`.

## Q5 — Persistent Quote Principle in Reduce Only

Persistent Reduce quotes can become less competitive, and that is associated with no-fill. It is not sufficient to explain most of the 89 no-fill orders on this historical path: only 14 had a capital-feasible best-bid print opportunity even under the observational replacement screen, while shorter resting lifetime, cancel timing, book validity and the strict maker print predicate remain relevant. The evidence warrants further measurement of those execution mechanisms; it does not establish that an aggressive requote rule would improve B2 outcomes.

## Limitations and reproducibility

The 32 markets are a selected execution-replayable sample; only 19 overlap the stricter M4A integrity-PASS subset. B2's inherited fill rule fills the whole remaining clip on a qualifying public SELL print without queue position or public-print-size constraints. The best-bid screen keeps B2's historical path and original order-close horizon, so it cannot measure how a real cancel/replace would alter inventory, competing orders, queue position or later PnL. The original B2 research artifact is locally unreviewed and uncheckpointed.

Run `python3 research_tools/b2_reduce_quote_attribution.py` from the repository root. Outputs are `research_outputs/b2_reduce_quote_attribution_20261004/{summary.json,reduce_orders.csv,filled_vs_no_fill.csv,distance_buckets.csv,market_summary.csv,best_bid_counterfactual.csv}`.

Next action: independently review the 14 capital-feasible historical opportunity paths before any quote-management design decision.
