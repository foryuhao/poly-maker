# DP Historical Backtest 02 — Original Quote Filter Ablation

**Result: OVER-FILTERS. Transferability: STRUCTURALLY MISMATCHED.**  Research-only pilot, 2026-10-03, Git base `97ab0f9`. The fixed Backtest 01B universe, settlement labels, skew policy, five-share size, order lifecycle, latency, and conservative fill rule were retained. No production code or trading action was used. Existing unrelated working-tree changes were left untouched.

## Research question and decision

On the same 20 BTC5m markets, did adding the documented competition strategy's *normal-regime* Quote Filter to B improve settlement return without materially worsening inventory risk? **No usable active strategy remains in this replay.** C has zero conservative fills, zero gross purchase cost, and zero settlement PnL, because its quote exposure collapses. The apparent C−B return gain of **+$6.045** is not evidence of adverse-selection skill: C removes all 79 B fills, including profitable and balancing acquisitions, and the paired gain reverses when the single best C market is removed. This is a negative transfer result for this original gate on the frozen B policy, not a claim that market making or other regime-selection methods cannot work.

## Authoritative competition source and exact normal-regime rule

The source is the author's [documented strategy implementation](https://github.com/octavi42/prediction-market-maker/blob/main/strategies/strategy_documented.py), read directly. Its [README](https://github.com/octavi42/prediction-market-maker/blob/main/README.md) describes a binary-claim simulator with integer cent ticks, 2,000 steps, a replenishing competitor, an informed arbitrageur, and retail flow. The competition score was fill-price edge against the simulator's true probability, **not** settlement PnL. This study imports only the normal-regime gate. Monopoly mode, expiry controls, variable size, cash logic, and all other source components remain outside scope.

The original `on_step` reads `state.competitor_best_bid_ticks` and `state.competitor_best_ask_ticks`; the normal regime requires both. It starts with `CancelAll()`. In source tick units, one tick is one cent:

```text
mid = (comp_bid + comp_ask) / 2
comp_spread = comp_ask - comp_bid
vol_ema starts at 1.0
if prev_mid exists: vol_ema = 0.10 * abs(mid - prev_mid) + 0.90 * vol_ema
prev_mid = mid
spread_value = (comp_spread - 2) / 2
if spread_value < 0.5: reject
prob_est = max(0.02, min(0.98, mid / 100))
d = abs(prob_est - 0.5)
phi = .20 if d>.45; .35 if d>.40; .50 if d>.35;
      .65 if d>.30; .82 if d>.20; .93 if d>.10; else 1.0
horizon = max(1, float(state.steps_remaining))
model_vol = phi * 39.9 / sqrt(horizon)
sigma_est = max(model_vol, vol_ema)
z = spread_value / sigma_est
reject if (spread_value >= 3 and z < 0.4)
       or (spread_value < 3 and z < 0.8)
```

The thresholds use strict `<`; equality passes. For the source's integer-cent book, spreads of one or two cents always fail the first test; a three-cent spread proceeds to the z test. `prob_est` is a clipped **observable competitor midpoint**, not the simulator's privileged true probability. The source's normal-regime quotes, after this gate, are inside the competitor spread (`comp_bid + 1` buy and `comp_ask - 1` sell before skew). B instead buys YES and NO at each token's best bid. Thus `spread_value` does not measure B's actual captured half-spread; this is a material structural mismatch even though the gate formula itself was copied.

## Input and transferability audit

| Original input | Source definition | BTC5m mapping used | Status |
|---|---|---|---|
| Competitor bid and ask | BBO of one binary YES claim in integer cents | Causally reconstructed public YES L2 BBO, converted from dollars to cents | Observable; competitor identity is a proxy |
| Midpoint and `prob_est` | Competitor midpoint; clipped midpoint / 100 | YES public midpoint and same clipping | Faithful market-observable proxy; not true probability |
| `comp_spread`, `spread_value` | YES ask−bid in cents; `(spread−2)/2` | YES ask−bid in cents, unchanged constants | Exact arithmetic; economic meaning differs for B's best-bid quotes |
| `prev_mid`, `vol_ema` | Midpoint on each simulator step; initial 1.0, alpha .10 on absolute cent change | Latest already available YES midpoint on an evenly spaced 150 ms clock | Proxy: simulator steps are absent in WebSocket history |
| `steps_remaining` | Actual remaining simulator steps | `max(1, 2000 × remaining market time / 300 s)` | Proxy; 2,000-step source horizon mapped linearly to BTC5m time |
| `phi`, `model_vol`, `sigma_est`, `z`, thresholds | Source piecewise factor and equations above | Same formula and constants, in source cent units | Exact conditional on proxy inputs |
| Reject action | Source returns only `CancelAll()` for the one claim | Suppress both YES and NO desired BUY quotes; cancel using frozen B latency/lifecycle | Two-token action mapping; not an exact source action |

No external price, exchange, privileged probability, or additional market data entered the replay. The source's 2,000 discrete simulator steps cannot be recovered exactly from asynchronous BTC5m events. We predeclared a linear 150 ms step clock, sampling only public state known at each boundary. C makes decisions on the same causal public events as B. The original `CancelAll()` is implemented as “no desired quote” in the unchanged B cancel/replace lifecycle, preserving the experiment's latency rather than importing the simulator's instant per-step cancellation. This tests the **original numerical gate** as an added B component; it is not a full replication of the source strategy or simulator market.

The event replay advances by the persisted local `available_at_us` clock and never uses a later locally received book state at an earlier decision. A book event's exchange timestamp may precede local availability; using local availability preserves the fixed Backtest 01 causal ordering. Only the final official winner is applied after replay for settlement accounting.

## Fixed dataset and execution method

The 20 execution-usable markets in Backtest 01 `dataset_audit.csv` were reused exactly, with no market additions or exclusions. The start epochs run from `1790936700` through `1790966700` (2026-10-02 10:25–18:50 UTC including the final market end), totaling 100 market-minutes across two observed periods. Both YES and NO public books, event availability timestamps, 5-minute expiry, and identity-validated official winning labels were present for all 20; the fixed settlement coverage is **20/20**. The remaining six markets in the original 26-market audit remain outside the fixed replay universe for their documented Backtest 01 reasons. [Backtest 01 report](DP_BACKTEST_01_ASYMMETRIC_INVENTORY_SKEW.md) and `research_outputs/dp_backtest_01/dataset_audit.csv` contain the original exclusions and settlement identity audit.

**B:** frozen `Policy("B")`, BUY YES/NO at best bid, five shares each; the original asymmetric heavy-side skew is `round(abs(YES−NO) × min(.08, 2.8/5))` ticks. **C:** the same policy with only the above gate; rejection suppresses both quote sides. Quote bounds, tick validity, order replacement, settlement, and lack of a cash optimization are unchanged.

The Backtest 01 strict fill condition remains: a positive-size public SELL trade strictly **below** an active simulated BUY quote, with valid available, receive, and exchange timestamps all later than simulated activation and before effective cancel. Same-price trades, book reductions, and touches are insufficient. Activation delay is **412,289 µs** from 74 real submit→ack observations (median 233.416 ms, p75 412.289 ms, p90 472.433 ms). Cancel delay is **231,100 µs** after Python rounding of p75 231.1005 ms from 176 real cancel→ack observations (median 214.897 ms, p90 445.683 ms). C inherits the same state machine and delays. The script asserts every B order row, fill row, and market metric matches Backtest 01B before writing results; that parity check passed. Settlement PnL is winning shares minus gross purchase cost, with no rebate or merge.

## Spread regime and filter activity

Valid-book time is time-weighted between observed public book changes; it is not a count of WebSocket messages. The spread categories use each token's actual tick size, while the source gate continues to use **cents**, as written. The YES book, which is the gate input, had **4,472.129 s** of valid BBO across the fixed 6,000 s universe. The NO book had 4,471.935 s.

| Time-weighted valid-book spread | YES | NO |
|---|---:|---:|
| One actual tick | 94.227% | 94.229% |
| Two actual ticks | 3.581% | 3.575% |
| Three or more actual ticks | 2.193% | 2.196% |

| Gate / exposure diagnostic | B reference | C |
|---|---:|---:|
| Valid quote decisions | 27,106 | 21,179 |
| Gate passes | 2,342 | 2,260 |
| Gate rejects | 24,764 (91.36%) | 18,919 (89.33%) |
| C rejection reason: spread / z | — | 16,323 / 2,596 |
| C pass duration / valid quote duration | — | 30.943 / 4,471.199 s (0.692%) |
| Active quote time, at least one live side | 4,811.773 s | 3.937 s |
| Simulated submitted orders | 5,772 | 772 |
| Markets with no C submitted order / no C fill | — | 0 / 20 |

Event-weighted rejection and time-weighted exposure differ sharply because the rare wide-spread passing states were short-lived. C's active exposure is **0.066% of the 6,000 market-seconds** and **0.088% of valid-book time**. Across C decisions for which z exists, passing z has p25 / median / p75 **1.062 / 1.358 / 2.087** (n=2,260); z-rejected states have **0.385 / 0.512 / 0.584** (n=2,596). The 16,323 first-stage spread rejects do not calculate z in the source and are blank in the output, rather than assigned an artificial score. `filter_events.csv` retains timestamp, both token BBOs/spreads, source cent spread, `prob_est`, phi, model volatility, EMA volatility, sigma, z, threshold, pass/reject reason, unfiltered desired quotes, and inventory for each valid decision.

## B versus C settlement and inventory results

| Metric, 20 markets | B skew only | C skew + filter |
|---|---:|---:|
| Total settlement PnL | −$6.045 | $0 |
| Mean / median PnL per market | −$0.302 / $0 | $0 / $0 |
| Profitable / losing markets | 9 / 7 | 0 / 0 |
| Worst / best single market | −$6.20 / +$3.95 | $0 / $0 |
| Mean time-weighted residual | 3.691 shares | 0 |
| Mean final / largest residual | 5.75 / 20 shares | 0 / 0 |
| Conservative fills / fills per market | 79 / 3.95 | 0 / 0 |
| Fills / submitted orders | 1.37% | 0% |
| Balancing / imbalance-increasing fills | 28 / 51 | 0 / 0 |
| Gross purchase cost | $221.045 | $0 |

The lower C residual is mechanical: it acquired no inventory. It cannot show that the gate balances inventory better or selectively avoids adverse fills.

## Removed fills and adverse-selection attribution

All **79 B fills** are absent in C on the same public trade-event key; there are **zero shared** and **zero C-only** fills. At their B submission decisions, all 79 were in gate-rejected states. At the eventual public trade, 74 were still gate-rejected and five had a gate-passing book state; C did not retain an active quote through those paths. The removed fills span **17 markets**, with **51 imbalance-increasing** and **28 balancing** fills. Forty-three bought the eventual winner and 36 bought the loser. Their direct, post-hoc settlement contributions sum to **−$6.045**: winner-side fills **+$53.105**, loser-side fills **−$59.150**. By fill class, balancing fills contributed **+$4.550**, imbalance-increasing fills **−$10.595**. These are additive accounting contributions in B's replay, not causal estimates of live adverse selection. `filtered_fills.csv` contains each fill's market, side, price, prior inventory, source spread/volatility/score at submission, gate result, winner, class, and ex-post contribution.

There is no shared-fill comparison group, so the filter's differential short-horizon adverse-selection effect cannot be estimated. A post-fill market-price path would not rescue that comparison; it was not introduced into policy decisions or used to change the verdict.

## Market-level consistency and outliers

`paired_markets.csv` is the primary 20-row market-level unit, with winner, fills, PnL, residual, and active quote time for both policies. C beats B on settlement PnL in **7** markets, loses in **9**, and ties in **4**. Median market C−B PnL is **$0**. Aggregate C−B is **+$6.045**; excluding the best C market (+$6.20) leaves **−$0.155**, while excluding the worst (−$3.95) leaves **+$9.995**. The top two positive markets contribute **+$8.90**, or **147%** of the net gain. The apparent aggregate improvement is therefore outlier-sensitive and arises from near-zero participation, not broad removal of specifically bad fills.

| Market start epoch | Winner | B/C fills | B PnL | C PnL | C−B |
|---:|:---:|---:|---:|---:|---:|
| 1790936700 | YES | 3 / 0 | +1.450 | 0 | −1.450 |
| 1790937900 | NO | 0 / 0 | 0 | 0 | 0 |
| 1790938500 | NO | 6 / 0 | −2.700 | 0 | +2.700 |
| 1790939100 | YES | 6 / 0 | +1.050 | 0 | −1.050 |
| 1790940000 | NO | 1 / 0 | +0.650 | 0 | −0.650 |
| 1790940600 | YES | 4 / 0 | −2.550 | 0 | +2.550 |
| 1790941200 | NO | 8 / 0 | +0.050 | 0 | −0.050 |
| 1790941800 | YES | 9 / 0 | +0.805 | 0 | −0.805 |
| 1790942400 | NO | 2 / 0 | −0.500 | 0 | +0.500 |
| 1790943000 | NO | 2 / 0 | −6.200 | 0 | +6.200 |
| 1790961300 | NO | 2 / 0 | +0.550 | 0 | −0.550 |
| 1790961900 | NO | 3 / 0 | +0.300 | 0 | −0.300 |
| 1790962500 | NO | 1 / 0 | +1.450 | 0 | −1.450 |
| 1790963100 | YES | 3 / 0 | 0 | 0 | 0 |
| 1790963700 | YES | 0 / 0 | 0 | 0 | 0 |
| 1790964300 | NO | 5 / 0 | −1.400 | 0 | +1.400 |
| 1790964900 | NO | 0 / 0 | 0 | 0 | 0 |
| 1790965500 | YES | 6 / 0 | −1.550 | 0 | +1.550 |
| 1790966100 | NO | 4 / 0 | −1.400 | 0 | +1.400 |
| 1790966700 | YES | 14 / 0 | +3.950 | 0 | −3.950 |

## Interpretation, limitations, and durable handoff

The source filter is **STRUCTURALLY MISMATCHED** to this BTC5m B policy: most observed spreads are one actual tick; its cent-based spread-value requirement was designed for quoting inside a competitor's wider, integer-cent spread, while B buys at each token's best bid. Its fixed 2,000-step volatility horizon and single-claim cancel action also require explicit proxies. The input is **not** privileged true probability. The replay's hypothetical trade-through fills remain conservative evidence, not verified own fills; this small fixed sample cannot establish production profitability or the value of a different regime filter. We did not tune thresholds, alter the original gate, use future data in decisions, or replace it with another component.

Reproduce with `python3 research_tools/dp_backtest_02_quote_filter.py` from the repository root. It uses only local official Polymarket event records and Backtest 01's persisted identity-validated settlement labels. Its parity guard fails if the fixed universe, latency or B execution has changed. Research outputs are in `research_outputs/dp_backtest_02/`: `market_results.csv`, `orders.csv`, `fills.csv`, `filter_events.csv`, `filtered_fills.csv`, `fill_attribution.csv`, `paired_markets.csv`, and `summary.json`. The first seven give row-level evidence and the JSON contains aggregate checks. The prior Backtest 01 report remains unchanged.

**Next action:** Open a separate, source-based research design for an established market-regime or participation-timing mechanism.
