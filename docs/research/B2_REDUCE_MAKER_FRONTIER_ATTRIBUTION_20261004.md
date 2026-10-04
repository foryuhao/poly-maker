# B2 Reduce Maker Execution Frontier Attribution — 2026-10-04

Result: **BEST BID IS NEAR MAKER EXECUTION FRONTIER**. This is historical, research-only opportunity attribution on the unchanged B2 path, not a strategy backtest or realized PnL forecast.

## Sample and exact controls

The sample is B2's **89** accepted/unfilled new risk-reducing BUY orders after T150, across **18 markets with such orders** in the same 32-market universe. The other 24 accepted new Reduce orders that filled are a frontier-gap reference. The B2 baseline reconciles to 113 accepted new orders, 24 of those filled, 27 total Reduce-phase fills including three older orders, 380 T150 residual and 245 final residual shares. The script processed 7,700,057 book events with the inherited R0 book representation and B2 available-time order, with zero skipped nonmonotone events. It used frozen 234,783 μs cancel and 415,281 μs submit delays, the same strict three-timestamp SELL print predicate, $25 original-path capital and 5-share clips. No B3 path was used.

The frontier is computed in integer price units only when both books are valid and `best_bid ≤ best_ask−tick < best_ask`, with price strictly positive. A new hypothetical quote is never placed at or above ask. The instant layer changes its *observed* quote to the latest legal frontier at every valid book update, without delay; the latency layer requests cancellation and waits for both frozen delays. Neither layer changes any B2 order, inventory, capital or later event.

## Opportunity ladder and Q1–Q2

| Execution rule | Opportunity orders | % of 89 | Capital-valid | % of 89 |
|---|---:|---:|---:|---:|
| Original B2 price | 0 | 0% | 0 | 0% |
| Current best bid, frozen delays | 16 | 18.0% | **14** | 15.7% |
| Always-current maker-safe frontier, instant | 7 | 7.9% | **7** | 7.9% |
| Dynamic frontier, frozen delays | 0 | 0% | **0** | 0% |

Thus `IncrementalFrontierVsBestBid` is **7−14 = −7 orders (−7.9 percentage points of 89)** at the instant layer, and **0−14 = −14** with latency. All seven frontier instant opportunities are a subset of the 14 capital-valid best-bid orders; no new order is recovered by moving to the always-current frontier. This counterintuitive non-monotonic result is real under the *prescribed dynamic rule*: a persistent best-bid quote can remain above a later, downward-moving frontier. The instant layer is an upper bound for that always-current-frontier rule under zero requote delay; it is **not** an upper bound over every possible persistent maker policy.

For example, order `btc-updown-5m-1790939100:B1:25` could have rested at a previously observed $0.96 best bid and met a later $0.95 SELL print. At that print the valid book bid/frontier was $0.95 (`ask=$0.96`, `tick=$0.01`), so a dynamically reset frontier BUY at $0.95 fails B2's strict `SELL price < BUY price` test. All **seven** best-bid capital-valid opportunities absent from the dynamic frontier layer have this downward-frontier/strict-price pattern at their prior best-bid print.

The latency sequence requested **278** raw hypothetical cancellations, made **249** submissions and recorded **194** activations across the 89 order paths; **124** SELL prints occurred while a hypothetical order was active or cancel-pending under a valid book. None met the maker-safe active quote's strict price condition. With the original-path capital filter, counts were 251 cancellations, 222 submissions, 167 activations and 102 such SELL prints, again zero qualifying. These are diagnostic lifecycle observations, not B2 orders.

## Q3 — Residual-share ceilings

| Capital-valid layer | Gross 5-share ceiling | Market-deduplicated ceiling from T150 | Also capped by B2 final residual per market |
|---|---:|---:|---:|
| Best bid | 70 shares | 60 shares | 25 shares |
| Instant frontier | **35 shares** | **35 shares** | **15 shares** |
| Frontier with latency | **0** | **0** | **0** |

The instant frontier's 7 orders occur in five markets. Two of those markets end B2 with zero residual, so only **15 shares (6.1% of B2's 245 final residual)** remain when the upper bound is also capped market by market at the observed final residual. Even the looser 35-share T150-state ceiling is far below the roughly 180 shares attributed to execution/no-fill or book blockage. These quantities cannot be added to actual B2 residual improvement: a hypothetical fill would change later inventory, capital, order placement and possibly the winner-side residual.

## Q4 — Price concession

First **capital-valid** opportunity per order, relative to its original B2 BUY price:

| Layer | Orders | Mean cents/share | Median cents/share | p75 | p90 | Mean / median extra cost per 5-share clip |
|---|---:|---:|---:|---:|---:|---:|
| Best bid | 14 | 8.04¢ | 4.50¢ | 6.50¢ | 25.20¢ | $0.402 / $0.225 |
| Instant frontier | 7 | **7.07¢** | **2.00¢** | 4.50¢ | 16.20¢ | **$0.354 / $0.100** |
| Frontier with latency | 0 | — | — | — | — | — |

The frontier row has a different seven-order selection and different first-print times from the best-bid row. Its lower median concession does **not** imply that frontier quoting is cheaper than best-bid quoting for the same opportunity. Each row measures the extra purchase price against that order's original B2 quote.

## Q5 — Local pool economics

Using B2 inventory strictly before each hypothetical qualifying print, adding one 5-share BUY at the first capital-valid opportunity gives:

| Layer | Median / mean projected PoolPairCost change | Median / mean projected PairPnL change |
|---|---:|---:|
| Best bid | +0.0385 / +0.0382 (12 defined pool-cost cases) | −$0.910 / −$0.689 (14 cases) |
| Instant frontier | **+0.0573 / +0.0686** (6 defined cases) | **−$0.845 / −$0.930** (7 cases) |
| Frontier with latency | no qualifying cases | no qualifying cases |

All seven instant-frontier opportunities were still risk-reducing against the actual B2 net at their print time. The static projections show meaningful matched-pair cost deterioration on the few observable opportunities; they are not additive strategy PnL and do not account for the value of reduced terminal residual risk.

## Time remaining and frontier gap

Of the seven instant, capital-valid frontier opportunities, **4** occurred with 90–150 seconds remaining, **1** with 60–90 seconds, **2** with 30–60 seconds and **0** in the final 30 seconds. There are no *incremental* frontier orders over best bid in any time bucket. The median per-order time-weighted gap from original quote to frontier was **+0.386 ticks** for no-fill orders with valid-book duration (83 orders), versus **−1.294 ticks** for the 24 filled reference orders; many books had only a one-tick spread, leaving little legal maker price above the best bid.

## Q6 — Maker-only frontier sufficiency

Under the specifically requested always-current-frontier observation, maker-only aggression creates **no additional** capital-valid opportunities over the prior persistent best-bid screen. Its instantaneous opportunity ceiling covers at most 15 shares of observed final residual after market-level capping, and the frozen-latency sequence finds none. This does not prove that every possible maker quote-management policy is ineffective: the always-current rule can cancel a valuable higher resting quote when the frontier moves down. It does show that this frontier rule does not solve B2's main residual problem in the observed 32-market path.

## Limits and reproducibility

The historical SELL print rule fills a whole remaining clip without a queue-position or print-size model. The latency sequence is observational: it does not model exchange acknowledgement, queue loss, inventory feedback or new capital after a hypothetical fill. The instant layer can retain a maker-safe price chosen on the last valid book through a brief invalid interval, but cannot create a new price from an invalid book. `time_saved_vs_original_s` is null because the original 89 orders never filled, so no realized fill-time difference exists. Only 19 of the 32 execution-replayable markets overlap the stricter M4A integrity-PASS set.

Run `python3 research_tools/b2_reduce_maker_frontier_attribution.py` from the repository root. Outputs are `research_outputs/b2_reduce_maker_frontier_attribution_20261004/{summary.json,order_frontier_analysis.csv,opportunity_ladder.csv,latency_frontier.csv,market_dedup_upper_bound.csv,price_concession.csv}`.

Next action: independently review the seven lost best-bid opportunities and the zero-hit latency sequence before any maker quote-management design.
