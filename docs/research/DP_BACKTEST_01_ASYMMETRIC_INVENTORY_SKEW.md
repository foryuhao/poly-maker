# DP Historical Backtest 01 — Asymmetric Inventory Skew Ablation

**01B settlement completion, 2026-10-03.** The current decision and completed return analysis are below. The pre-settlement 01A report is retained at the end as historical evidence; its former insufficient-label verdict is superseded for this fixed 20-market sample. Research-only work at Git base `97ab0f9`; unrelated pre-existing working-tree changes were left untouched.

## 01B Result

**SUPPORTS ASYMMETRIC SKEW**, limited to this conservative historical replay. B reduced mean time-weighted residual from 4.55 to 3.69 shares, mean final residual from 8.25 to 5.75, and the largest single-market residual from 25 to 20. Aggregate settlement PnL was nearly the same: **A −$6.250; B −$6.045; B−A +$0.205**. Both strategies lost money in this replay. The tiny aggregate return difference is not broad-based and cannot support a claim that B improves return or that either strategy is profitable.

The stronger `SUPPORTS BOTH RETURN AND RISK` classification is not warranted: only two markets favored B on PnL, three favored A, 15 tied, and removal of one outlier reverses the aggregate sign. `RISK-REDUCING BUT RETURN-COSTLY` is also unsupported because B did not suffer a material aggregate PnL loss relative to A.

## 01B Official Settlement Coverage and Identity

**20/20** fixed replay markets have an unambiguous official winner: **8 YES, 12 NO**. The research-only retrieval read the [official CLOB market endpoint](https://clob.polymarket.com/markets/0x86bfc189f2111e2cfec9dd4833ce5c61be3e280435885233e2c5b55a504d1c96) by historical condition ID and the [official Gamma market endpoint](https://gamma-api.polymarket.com/markets/slug/btc-updown-5m-1790936700) by historical slug. Polymarket's [market details documentation](https://docs.polymarket.com/market-data/market-details) describes these identifiers and the Gamma outcome/token arrays. No third-party, BTC-price, or blockchain result was used.

For each market, `settlement_labels.csv` records the original replay session ID, slug, condition ID, YES/NO token IDs, start/end epoch, both official URLs, retrieval timestamp, both closed flags, explicit CLOB `tokens[].winner`, Gamma `outcomePrices`, and identity decision. A label entered the backtest only if slug, condition ID, both token IDs, and five-minute end time matched the original manifest; both official responses were closed; exactly one CLOB token had `winner=true`; and Gamma's 1/0 outcome prices agreed by token ID. All 20 passed. `settlement_sources.json` preserves the official response bodies. The recorded `winning_outcome` is the winning token mapped through the original YES/NO manifest, not an inference from an “Up/Down” title.

## 01B Exact Replay Check

Only `persisted_winner()` in `research_tools/dp_backtest_01_inventory_skew.py` was changed to accept identity-validated `settlement_labels.csv`; it falls back to the prior episode payout logic and rejects conflicting labels. Strategy A/B quotes, five-share size, skew formula, book reconstruction, latency sample and percentiles, cancel lifecycle, fill rule, and 20-market selection were unchanged. The post-label rerun reproduced the **exact same `orders.csv` and `fills.csv` bytes** as 01A:

| Frozen execution artifact | Before/after SHA-256 |
|---|---|
| `orders.csv` | `56c1baeea78abca87e0e43460764b6b6a00707aff5a9144d974106a97d4c4216` |
| `fills.csv` | `62840fbbc19644cad378fa4cf74d8fc8b65e28b2e8e810d98e2eea451e90908b` |

The rerun still has **89 A fills, 79 B fills**, and the same 20 replay markets. Settlement is `winning shares − gross purchase cost`, with no rebate, rewards, merge, mark-to-market, or external price. Blank PnL fields became computed values. `fill_attribution.csv`, `avoided_fills.csv`, `market_comparison.csv`, and `fill_difference_decomposition.csv` are post-hoc outputs that do not feed decisions.

## 01B A/B Results

| Metric, 20 markets | A symmetric | B asymmetric |
|---|---:|---:|
| Total settlement PnL | −$6.250 | −$6.045 |
| Mean / median PnL per market | −$0.313 / $0 | −$0.302 / $0 |
| Profitable / losing markets | 8 / 8 | 9 / 7 |
| Worst / best market | −$6.20 / +$9.50 | −$6.20 / +$3.95 |
| Mean time-weighted residual shares | 4.55 | 3.69 |
| Mean / median final residual shares | 8.25 / 5 | 5.75 / 5 |
| Largest residual during any market | 25 | 20 |
| Mean matched quantity | 7 | 7 |
| Strict fills / purchased shares | 89 / 445 | 79 / 395 |
| Gross purchase cost | $256.250 | $221.045 |
| Balancing / imbalance-increasing fills | 28 / 61 | 28 / 51 |

Average-cost attribution, for explanation only, assigns the same per-side average purchase price to matched and residual lots. Aggregate matched-inventory PnL is **A −$6.761, B −$4.854**; residual-settlement PnL is **A +$0.511, B −$1.191**. Each pair sums to its strategy's total PnL. This accounting split is sensitive to the lot-cost convention; total settlement PnL is not. Matched quantity is 140 shares for both strategies. No pairing or repair decision was added to the policy.

## 01B Avoided Fill Analysis

Exactly **10 A fills have no B fill on the same public trade event; there are zero B-only fills**. All ten A-only fills increased absolute inventory imbalance when they occurred. Seven bought the eventual winning token and three bought the loser. Their direct ex-post settlement contributions sum to **+$0.050** for A, so suppressing them alone slightly *reduced* B's PnL. On public trade events filled by both policies, B's lower quote prices improved B's PnL by **$0.255**. The additive replay accounting is `ΔPnL = −$0.050 + $0.255 = +$0.205`. This is an ex-post decomposition, not proof that any one fill would recur live.

| Market start | Side | A price | A inventory before (Y/N) | B resting / desired quote | Winner | A fill class | A ex-post contribution |
|---:|---|---:|---:|---|---|---|---:|
| 1790939100 | YES | .88 | 15 / 5 | .87 / .86 | YES | increasing | +$0.60 |
| 1790939100 | YES | .92 | 20 / 5 | .91 / .90 | YES | increasing | +$0.40 |
| 1790939100 | YES | .96 | 30 / 10 | .95 / .94 | YES | increasing | +$0.20 |
| 1790941200 | YES | .62 | 20 / 5 | .61 / .60 | NO | increasing | −$3.10 |
| 1790941200 | YES | .29 | 30 / 15 | .28 / .26 | NO | increasing | −$1.45 |
| 1790941800 | YES | .98 | 25 / 15 | .97 / .96 | YES | increasing | +$0.10 |
| 1790965500 | NO | .49 | 0 / 10 | .48 / .47 | YES | increasing | −$2.45 |
| 1790966700 | YES | .30 | 30 / 15 | .29 / .28 | YES | increasing | +$3.50 |
| 1790966700 | YES | .57 | 45 / 25 | none / .52 | YES | increasing | +$2.15 |
| 1790966700 | YES | .98 | 50 / 30 | .97 / .96 | YES | increasing | +$0.10 |

The B “resting” column is the actual simulated active quote immediately before the public trade; “desired” is the contemporaneous book-and-inventory target for diagnosis. In nine rows B rested at a lower price than A's fill quote; in one row B had no active order during a lower desired-quote transition. The table identifies policy-path differences in the replay; it does not establish that all ten misses are caused solely by the quote distance rather than accumulated inventory and cancel timing.

## 01B Market-Level Paired Comparison

`market_comparison.csv` is the primary 20-row table with full market slug, winner, fills, PnL, residual and matched quantities for each policy. The compact table uses the market start epoch from the slug; `Δ` means B−A.

| Start epoch | Winner | Fills A/B | PnL A | PnL B | ΔPnL | Final residual A/B | Max residual A/B | Matched A/B |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1790936700 | YES | 3 / 3 | +1.45 | +1.45 | 0 | 5 / 5 | 5 / 5 | 5 / 5 |
| 1790937900 | NO | 0 / 0 | 0 | 0 | 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 1790938500 | NO | 6 / 6 | −2.70 | −2.70 | 0 | 0 / 0 | 10 / 10 | 15 / 15 |
| 1790939100 | YES | 9 / 6 | +2.25 | +1.05 | −1.20 | 25 / 10 | 25 / 10 | 10 / 10 |
| 1790940000 | NO | 1 / 1 | +0.65 | +0.65 | 0 | 5 / 5 | 5 / 5 | 0 / 0 |
| 1790940600 | YES | 4 / 4 | −2.55 | −2.55 | 0 | 10 / 10 | 10 / 10 | 5 / 5 |
| 1790941200 | NO | 10 / 8 | −4.55 | +0.05 | +4.60 | 20 / 10 | 20 / 15 | 15 / 15 |
| 1790941800 | YES | 10 / 9 | +0.90 | +0.805 | −0.095 | 20 / 15 | 20 / 15 | 15 / 15 |
| 1790942400 | NO | 2 / 2 | −0.50 | −0.50 | 0 | 0 / 0 | 5 / 5 | 5 / 5 |
| 1790943000 | NO | 2 / 2 | −6.20 | −6.20 | 0 | 10 / 10 | 10 / 10 | 0 / 0 |
| 1790961300 | NO | 2 / 2 | +0.55 | +0.55 | 0 | 10 / 10 | 10 / 10 | 0 / 0 |
| 1790961900 | NO | 3 / 3 | +0.30 | +0.30 | 0 | 5 / 5 | 5 / 5 | 5 / 5 |
| 1790962500 | NO | 1 / 1 | +1.45 | +1.45 | 0 | 5 / 5 | 5 / 5 | 0 / 0 |
| 1790963100 | YES | 3 / 3 | 0 | 0 | 0 | 5 / 5 | 5 / 5 | 5 / 5 |
| 1790963700 | YES | 0 / 0 | 0 | 0 | 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 1790964300 | NO | 5 / 5 | −1.40 | −1.40 | 0 | 5 / 5 | 10 / 10 | 10 / 10 |
| 1790964900 | NO | 0 / 0 | 0 | 0 | 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 1790965500 | YES | 7 / 6 | −4.00 | −1.55 | +2.45 | 15 / 10 | 15 / 10 | 10 / 10 |
| 1790966100 | NO | 4 / 4 | −1.40 | −1.40 | 0 | 0 / 0 | 10 / 10 | 10 / 10 |
| 1790966700 | YES | 17 / 14 | +9.50 | +3.95 | −5.55 | 25 / 10 | 25 / 20 | 30 / 30 |

Five markets have lower B final residual, five lower B maximum residual, and none higher. B beats A on both PnL and final residual in **two** markets; there are no markets where B is worse on both. Fifteen markets have exactly equal PnL because the replay fills and quote prices match there.

## 01B Outlier and Decision Audit

Aggregate `ΔPnL` is **+$0.205**; median market `ΔPnL` is **$0**. B beats A in **2** markets, loses in **3**, and ties in **15**. The best B delta is **+$4.60**; excluding it changes the aggregate to **−$4.395**. The worst B delta is **−$5.55**; excluding it changes the aggregate to **+$5.755**. The top one/two positive markets contribute **+$4.60 / +$7.05**, versus a net +$0.205 after offsets. The near-zero return difference is therefore an outlier-sensitive cancellation, while the lower residual metrics are observed in five distinct markets. This supports the *risk-control structure* under the fixed replay, not a stable return advantage.

## 01B Files and Reproduction

Validation executed: all 20 labels were independently rederived from the persisted official CLOB/Gamma response bodies; all 20 market-table rows and all ten avoided-fill rows were checked against their CSV outputs; every market's matched-plus-residual attribution reconciles to its settlement PnL; and the A-only/B-only/common-fill decomposition reconciles to the paired PnL delta. The pre/post order and fill SHA-256 checks above passed. No production files, live strategy settings, or authenticated trading actions were changed by 01B.

- `research_tools/dp_backtest_01b_settlement_labels.py` — read-only official HTTP retrieval and identity gates.
- `research_outputs/dp_backtest_01/settlement_labels.csv` and `settlement_sources.json` — labels and official responses.
- `research_tools/dp_backtest_01_inventory_skew.py` — settlement-label input only; quote/fill behavior unchanged.
- Existing `market_results.csv`, `paired_deltas.csv`, `dataset_audit.csv`, and `summary.json` — same replay with settlement values.
- `research_tools/dp_backtest_01b_attribution.py` — post-hoc accounting only.
- `research_outputs/dp_backtest_01/fill_attribution.csv`, `avoided_fills.csv`, `market_comparison.csv`, `fill_difference_decomposition.csv`, and `attribution_summary.json` — diagnostics.
- `orders.csv` and `fills.csv` — regenerated with exact pre-settlement hashes above.

To reproduce from the fixed local event data, run `python3 research_tools/dp_backtest_01b_settlement_labels.py`, then `python3 research_tools/dp_backtest_01_inventory_skew.py`, then `python3 research_tools/dp_backtest_01b_attribution.py`. Only the first command makes read-only calls to official Polymarket HTTP. This report is research evidence, not an independently reviewed production checkpoint.

The replay script's `summary.json` uses `REQUIRES_RESULT_INTERPRETATION` after full labels because the risk/return verdict is a research judgment rather than an invented numerical threshold. The current final verdict is recorded in this report.

## 01B Next Action

Independently review the official identity checks and the byte-identical execution replay before any further DP experiment.

---

## Historical 01A Pre-Settlement Record

The following record documents the earlier incomplete-label result and is retained without treating its prior verdict as the current decision.

### Result

**INSUFFICIENT CONSERVATIVE FILLS for an outcome-evaluable A/B test.** The replay produced 89 strict trade-through fills for A and 79 for B, but **zero of the 20 markets have a persisted official winning side**. Thus zero simulated fills can be assigned settlement PnL. The requested five-class decision vocabulary has no separate missing-resolution category; this verdict denotes an *insufficient outcome-evaluable sample*, not a claim that the raw trade-through count is small. Neither support nor rejection of asymmetric skew is established.

This is a read-only research result at Git base `97ab0f9` with unrelated pre-existing working-tree changes. Prior M0B fill-proxy FAIL remains in force. The trade-through rule here is the user's separately specified conservative counterfactual rule, not the failed M0A same-level/L2 proxy. No production code or live order was touched.

### Dataset

`research_data/m4a/` contains **30 sessions for 26 distinct BTC5m slugs**. Twenty complete, gap-free sessions with at least 50,000 persisted events have both token books and enough updates for execution replay; ten short sessions represent six excluded slugs (five four-event attempts for `1790929200`, and one short session each for `1790930100`, `1790930700`, `1790931900`, `1790935800`, `1790939700`). The 20 included slugs each provide a five-minute market interval: **100 market-minutes total**, noncontiguous between 2026-10-02 **10:25 and 18:45 UTC**. The exact session audit is in `research_outputs/dp_backtest_01/dataset_audit.csv`.

The manifests map YES and NO token IDs, exchange and local timestamps, market start, market end, and capture quality. Their event streams contain `BOOK`, `REST_BOOK`, `DELTA`, public `TRADE`, and tick-change records; book snapshots include L2 price-level sizes, BBO, and tick size. Within market windows the included streams have 43,172 full book events, 5,302,526 deltas, 21,550 public trades, and no decreasing local-available timestamps. All 20 streams reconstruct both books at valid decision points. **Settlement winners are missing:** all 124 existing episode records have null `payout_yes`, `payout_no`, and `resolution_known_at_us`; run reports have null `final_net_pnl`. A near-terminal price is not an official resolution. The script reads only explicit non-null episode payouts, so it would compute settlement PnL if such persisted labels were present.

The source skew formula is documented by the competition participant in the [published strategy source](https://github.com/octavi42/prediction-market-maker/blob/main/strategies/strategy_documented.py) and [research write-up](https://github.com/octavi42/prediction-market-maker). Its full strategy includes other filters and size logic; this study isolates the requested skew term at size five. The [official Polymarket market-stream schema](https://docs.polymarket.com/market-data/realtime-data) documents book, price change, last-trade-price, and resolution event types. Only locally persisted official events enter the replay.

### Historical Fill Method

The script walks events in local `available_at_us` order. A hypothetical five-share BUY at price `p` counts as filled only by a later **same-token public `SELL` trade strictly below `p`** while the simulated order is active and before cancel effectiveness. It also requires the trade's exchange event, local receipt, and local available timestamps all to exceed simulated activation. Same-price prints, price touches, L2 reductions/depletions, and opposite-token prints do not fill orders. `SELL` is treated as selling pressure on that token; this is directionally corroborated by 24 of 27 prior authenticated BUY fill events having a direct selected-token public SELL observation, but the public print is not proof of a particular counterfactual order's fill. Public L2 is aggregated and the hypothetical order is absent from history. This remains an intentionally conservative, conditional price-priority assumption.

The persisted `TRADE.side` is taken from `tp.aggressor.value` in `src/polymaker/marketdata/service.py`; it is therefore a direction field for the reported aggressor, not an inferred maker side. No future market path is used for decision or quote selection. Future public trades are consulted only for the prescribed execution test. The data include live own orders in the observed book, so the counterfactual market path is not independent of the historical trading activity.

### Latency Assumptions

From the prior 20-session own-order audit, **74** accepted maker submit-to-ACK intervals have median **233.416 ms**, p75 **412.289 ms**, p90 **472.433 ms**. The primary replay uses **412.289 ms** activation delay. Joining existing `CANCEL_REQUEST` and successful `CANCEL_ACK` by request ID yields **176** intervals: median **214.897 ms**, p75 **231.101 ms**, p90 **445.683 ms**. The replay uses **231.101 ms** cancel delay. A cancel request leaves the old order fill-exposed until effective; replacement starts only after that time and receives a fresh activation delay. If cancel is requested before activation, effectiveness is delayed until at least activation. Local ACK time is a proxy for exchange insertion/removal, whose exact timestamps are not recorded. The same assumptions apply to A and B.

### Strategy A

At a valid decision point, A desires one nonmarketable five-share BUY at each token's current best bid. Inventory does not change the desired price. Existing resting orders remain if the desired price is unchanged; otherwise each side cancels, waits for cancel effectiveness, then submits the new desired quote. Decision points are BBO/tick changes, simulated order lifecycle transitions, and simulated fills. Both books must exist with bid below ask. A quote failing the current tick, bounds, or nonmarketable test is skipped and counted.

### Strategy B

At each identical decision point, `net = YES − NO`, `size = 5`, `skew_rate = min(0.08, 2.8/size) = 0.08`, and `skew_ticks = round(abs(net) × 0.08)`. Only the inventory-heavy side retreats to `best_bid − skew_ticks × tick_size`; the light side remains at best bid. Balanced inventory produces symmetric quotes. The source uses `int(round(...))`; the replay uses Python `round`, which is identical at observed five-share inventory increments because no half-tick tie occurs. Constants and quantity were not tuned. There are no SEED, PAIR, or REPAIR modes. No cash cap was fabricated because no starting balance was persisted; gross cost is reported, and this prevents a claim of fully cash-feasible replay. The maximum gross cost in A is $45.50 per market.

### Original Formula Activity

Across **27,106** B decision calls, skew was zero **20,031 (73.9%)**, one tick **6,870 (25.3%)**, and two or more ticks **205 (0.8%)**; maximum was **two ticks**. B's desired quotes therefore differed from A on **7,075 (26.1%)** decision calls. Event-to-event wall-clock integration gives **727.3 seconds with differing quotes out of 4,471.2 seconds with valid two-sided books (16.3%)**. Decision calls are autocorrelated, while the time measure avoids calling their frequency a percentage of market time. B is active enough to test inventory effects; the parameterization did not collapse to zero at this scale.

### A/B Results

| Metric, 20 markets | A symmetric | B asymmetric |
|---|---:|---:|
| Settlement PnL / mean / median / profitable rate / worst loss | unavailable | unavailable |
| Strict trade-through fills | 89 | 79 |
| Activated orders | 5,778 | 5,772 |
| Fill rate / activated order | 1.54% | 1.37% |
| Canceled orders | 5,650 | 5,657 |
| Quote changes | 5,739 | 5,736 |
| Mean / median matched shares | 7 / 5 | 7 / 5 |
| Mean / median time-weighted residual shares | 4.55 / 4.13 | 3.69 / 4.13 |
| Mean / median final residual shares | 8.25 / 5 | 5.75 / 5 |
| Largest residual during any market | 25 | 20 |
| Mean gross purchase cost | $12.81 | $11.05 |
| Maximum gross purchase cost | $45.50 | $36.05 |
| YES-heavy / NO-heavy markets | 8 / 6 | 8 / 6 |

Both policies have three zero-fill markets. B has 10 fewer strict fills and 50 fewer residual shares summed over all markets; the same total matched shares (140) is an observation under this assumed execution rule, **not** a return conclusion. Exactly five markets have lower B final residual, five have lower B maximum residual, and no market has higher B residual. The apparent inventory improvement cannot be weighed against the PnL cost of lost or avoided positions without official winners. Fill rate uses activated orders as denominator; order counts are high because continuously following BBO triggers frequent cancel/replacement.

### Market-by-Market Comparison

`research_outputs/dp_backtest_01/paired_deltas.csv` has all 20 paired rows. The only nonzero residual deltas are:

| Market start epoch | Δ final residual (B−A) | Δ max residual (B−A) | A/B strict fills |
|---:|---:|---:|---:|
| 1790939100 | −15 | −15 | 9 / 6 |
| 1790941200 | −10 | −5 | 10 / 8 |
| 1790941800 | −5 | −5 | 10 / 9 |
| 1790965500 | −5 | −5 | 7 / 6 |
| 1790966700 | −15 | −5 | 17 / 14 |

All other 15 markets have zero final and maximum residual delta. No market can be counted as improving or worsening both PnL and residual, because PnL is unlabelled.

### Outlier Check

The largest single-market final-residual improvement is 15 shares, occurring twice; either contributes 30% of the total 50-share difference. Excluding one such market leaves a 35-share difference. The smallest nonzero improvements are five shares. Best/worst PnL contribution, PnL difference with an outlier removed, and extreme-resolution-event attribution are **not computable** without an official winner. The residual result is concentrated in five of 20 markets and must not be used as a surrogate for return.

### Interpretation

The strict execution replay is reproducible and shows that the unmodified formula frequently retreats a heavy-side quote under this event-driven protocol. The observed reduction in residual inventory is conditional on the trade-through convention, measured ACK latency, a missing capital cap, and historical book behavior. The requested return-and-risk decision gate **cannot be passed**: the 20 outcomes are unknown in persisted data. There is no claim that skew improves total PnL, profitable-market rate, worst loss, or the risk/return combination. No alternative fill rule, parameter sweep, settlement inference from the book, or later model component was used.

### Files Changed

- `research_tools/dp_backtest_01_inventory_skew.py` — local-only replay and audits.
- `research_outputs/dp_backtest_01/dataset_audit.csv` — session inclusion and resolution audit.
- `research_outputs/dp_backtest_01/market_results.csv` — strategy/market execution and inventory rows; missing PnL is blank.
- `research_outputs/dp_backtest_01/paired_deltas.csv` — paired inventory deltas; missing PnL is blank.
- `research_outputs/dp_backtest_01/orders.csv` and `fills.csv` — simulated lifecycle and strict evidence rows.
- `research_outputs/dp_backtest_01/summary.json` — latency, formula activity, metrics, and assumptions.
- This report.

Reproduce with `python3 research_tools/dp_backtest_01_inventory_skew.py` from the repository root. Outputs remain under the research output directory; no production import or network access occurs. This research is not independently reviewed or checkpointed.

### Next Action

Persist the **official Polymarket winning token for each of the 20 replayed markets** in the research dataset.
