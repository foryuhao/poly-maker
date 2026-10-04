# DP Historical Backtest 03 — Three-Layer MM Entry Architecture

**Classification: RISK IMPROVEMENT ONLY.** Research-only replay on the frozen Backtest 01 universe, 2026-10-03, Git base `97ab0f9`. No production code or order path changed. Existing unrelated working-tree changes were left untouched.

## Research question and decision

Does side-specific book quality plus queue-imbalance / weighted-BBO-microprice entry gating improve the fixed asymmetric-skew B policy on 20 BTC5m markets? **No observable entry edge.** D has lower average residual inventory but worse aggregate settlement PnL and no evidence that it removes worse short-horizon fills. It remains active at 69.7% of B's live quote time and 59.5% of its fill count, so this is not the participation collapse seen in Backtest 02. The inventory gain accompanies a fall in matched quantity from 140 to 70 shares and is not evidence of better balancing per unit traded.

## Sources and frozen design

The primary source is the persisted local `research_data/m4a` capture for the exact 20 `execution_replay_usable=True` markets in Backtest 01 `dataset_audit.csv`. All 20 official winning token labels pass the existing market/session/condition/token identity validation. [Backtest 01](DP_BACKTEST_01_ASYMMETRIC_INVENTORY_SKEW.md) and [Backtest 02](DP_BACKTEST_02_QUOTE_FILTER.md) define the comparison and prior exclusions. The previous competition Quote Filter is excluded.

Queue imbalance is studied as a short-horizon book signal, consistent with [Gould and Bonart's primary paper](https://arxiv.org/abs/1512.03492) on next midpoint movement. The calculation here is the specified **weighted BBO midpoint**, `Microprice = (Ask × Q_bid + Bid × Q_ask)/(Q_bid + Q_ask)`. This is a static weighting, not a fitted dynamic estimator; [Stoikov's micro-price paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694) discusses the broader estimator. No claim is made that either source validates the −0.5 threshold or transfer to Polymarket two-token fills.

B is the exact Backtest 01 `Policy("B")`: 5-share BUY orders on YES and NO, best-bid quote, `net = YES−NO`, `ticks = round(abs(net) × min(.08, 2.8/5))`, and retreat only on the heavy side. D uses the same skew, quote-price checks, order lifecycle, 412,289 µs activation delay, 231,100 µs cancel delay, and same-token positive SELL print **strictly below** an active simulated bid. Same-level prints, L2 changes and touches never fill. Both use the same historical decision triggers: BBO/tick changes, lifecycle transitions, and simulated fills. D records current L2 sizes at these triggers; same-BBO queue-size changes alone do not trigger a new decision. The script asserts that every B order and fill row and every B market metric equals the frozen Backtest 01 output before it writes results. This parity guard passed.

### Layer 1: data health and depth

D evaluates each token separately. It requires a reconstructed snapshot from the current connection generation, complete positive-size BBO, bid below ask, valid price bounds, and prices aligned to a positive tick. The repository's live market-data service treats a quiet book on a live connection as fresh and uses current-generation snapshots; it does **not** use book-mutation age as a stale-book cutoff. The replay therefore does not invent one. Monotone available timestamps and event sequence are asserted; the frozen input manifests have zero captured gaps/losses in this universe. A new-generation delta before a new snapshot would withhold D on that token while B reconstruction stays unchanged. There were **zero** such stale-generation decision rejects.

The BBO bid and ask sizes and local cumulative displayed depth at each best price plus two ticks are recorded. The 5-share order size gives interpretable, fixed research buckets `<5`, `5–<10`, `10–<25`, `≥25`; no depth threshold is used to reject a quote because no source-backed cutoff was established. Among 57,850 quality-passing D side decisions, minimum BBO depth occupied these buckets in 1,586 / 10,229 / 12,001 / 34,034 events, respectively. This is a depth profile, not an optimized score or alpha claim.

There are **62,646** D side-decision records, of which **4,796 (7.66%)** fail Layer 1: 1,416 incomplete book and 3,380 crossed/invalid book. Stale-generation, invalid-tick/price, insufficient-depth, and other rejects are all zero. B already skips decisions when either book is incomplete or crossed; these Layer 1 exclusions must be treated as data health, not economic entry filtering. Since D's quality check is side-specific, one healthy token can remain eligible if the other is invalid.

### Layer 2: fixed entry rule and bucket analysis

For each token independently,

```text
Q_bid = displayed best-bid size; Q_ask = displayed best-ask size
Imbalance = (Q_bid − Q_ask)/(Q_bid + Q_ask)
Mid = (Bid + Ask)/2
Microprice = (Ask × Q_bid + Bid × Q_ask)/(Q_bid + Q_ask)
MicropriceShiftTicks = (Microprice − Mid)/tick
```

The five symmetric buckets were fixed before outcome comparison: `[-1,−0.5)`, `[−0.5,−0.2)`, `[−0.2,+0.2]`, `(+0.2,+0.5]`, `(+0.5,+1]`. The binary rule uses the same **−0.5 boundary inclusive**: withhold a maker BUY quote when `Imbalance ≤ −0.5`. At the boundary, the negative shift is half the spread in tick units times −0.5. A negative imbalance/shift suggests pressure toward the bid and hence potential adverse selection for a resting BUY. The inclusive boundary is a predefined research convention, not a tuned or production parameter. Exact −0.5 observations are in the moderate-negative research bucket but are withheld by the binary rule; none of the B fills had that exact imbalance at submission. The rule uses no settlement outcome, future markout, external BTC data, or other signal. YES and NO are evaluated independently; Layer 3 moves only the allowed quote.

| Imbalance bucket | Quality-passing D side decisions | B fills at submission | Mean shift, ticks | Mean B 3 s markout/share |
|---|---:|---:|---:|---:|
| Strong negative | 21,150 | 29 | −0.830 | −$0.0140 |
| Moderate negative | 5,481 | 16 | −0.342 | −$0.0269 |
| Neutral | 6,427 | 10 | +0.000 | +$0.0174 |
| Moderate positive | 5,105 | 6 | +0.336 | +$0.0033 |
| Strong positive | 19,687 | 18 | +0.759 | −$0.0921 |

These are research buckets, not fitted parameters. The strong-positive sample has particularly poor markout, contradicting a simple monotone interpretation in this sample. There are **21,283 Layer 2 rejects**, or **36.79%** of quality-passing D side decisions. D has 36,567 side decisions eligible after Layers 1 and 2, and 26,085 side-specific quote-off records inclusive of health, toxicity, and quote-price checks. Decision-event counts are autocorrelated and differ from time exposure.

## B versus D outcomes

Settlement PnL is winning shares minus gross purchase cost; rebate, rewards and incentives are excluded. Time-weighted residual is the market average of `abs(YES−NO)` integrated over each five-minute interval. Active quote time is the union of effective live-order intervals across YES and NO, including cancel latency. It is not the sum of both sides.

| Metric, 20 markets | B skew only | D three layers |
|---|---:|---:|
| Total settlement PnL | −$6.045 | −$6.450 |
| Mean / median market PnL | −$0.302 / $0 | −$0.323 / −$0.100 |
| Profitable / losing markets | 9 / 7 | 6 / 10 |
| Worst / best market | −$6.200 / +$3.950 | −$5.400 / +$5.400 |
| Mean time-weighted residual | 3.691 | 2.697 |
| Mean final / maximum residual | 5.75 / 20 | 4.75 / 20 |
| Matched quantity, shares | 140 | 70 |
| Imbalance-increasing / balancing fills | 51 / 28 | 33 / 14 |
| Active quote time | 4,811.773 s | 3,352.457 s |
| Simulated orders / strict fills | 5,772 / 79 | 5,567 / 47 |
| Zero-fill markets | 3 | 4 |

`ActiveQuoteTime_D / ActiveQuoteTime_B = 0.6967`; `Fills_D / Fills_B = 0.5949`. D has substantial ongoing activity, but it removes **40.5%** of B's fills and halves matched acquisition. The largest inventory residual remains 20 shares. The lower mean residual is real under replay arithmetic but is largely participation-linked. D's 33 imbalance-increasing fills and 14 balancing fills do not show preferential preservation of balancing fills relative to B's 51 and 28.

## Fill attribution and markouts

At each B order submission, the script records token-side imbalance, weighted-microprice shift in ticks, both BBO sizes, local depth and B inventory. **29 of 79** B fills originated at a decision that the entry rule would reject; 16 increased imbalance and 13 balanced it. These 29 fills have an ex-post settlement contribution of **−$0.800**, but this is diagnostic, not an input or a causal estimate. In the full two-policy replay, 43 trade-event fills are shared, 36 are B-only, and four are D-only. Of the 36 B-only fills, 25 were directly rejected at their submission by Layer 2 (13 imbalance-increasing, 12 balancing; settlement contribution **−$2.050**). The remaining 11 were allowed at their B submission and diverged because earlier D actions changed its order path (eight imbalance-increasing, three balancing; **+$4.455** contribution). B-only contribution totals **+$2.405**. There were no Layer 1-at-submission B-only fills. Four decision-rejected B fills still appear in D owing to existing orders and cancel latency. The D-only path, and occasional shared-fill price differences, mean that removed-fill contribution is not equal to D−B PnL.

For each strict fill, markout is `token midpoint as of t+h − simulated BUY fill price`, per share, at h = 1, 3, 5 seconds. The midpoint is the latest causally available reconstructed BBO at or before the exact horizon, with no future book state used. An invalid or out-of-window BBO leaves the markout blank. Because some books are quiet, the table also shows the subset whose as-of book was at most one second old at the horizon. These are diagnostics only.

| Horizon | Submission-rejected B fills, mean (n) | Submission-allowed B fills, mean (n) | Fresh ≤1 s rejected / allowed means (n) |
|---|---:|---:|---:|
| 1 s | −$0.0193 (29) | −$0.0302 (49) | −$0.0316 (16) / −$0.0367 (38) |
| 3 s | −$0.0140 (29) | −$0.0379 (48) | −$0.0185 (17) / −$0.0579 (26) |
| 5 s | −$0.0238 (29) | −$0.0373 (47) | −$0.0459 (17) / −$0.0461 (32) |

Less negative is better for a BUY. Across all three horizons, the direct submission-rejected fills had **better**, not worse, mean markout than allowed B fills; the fresh subsets do not reverse that pattern. The actual 36 B-only fills likewise have better average markouts than the 43 retained B fills at all three horizons. Thus Layer 2 does **not** demonstrate adverse-selection removal. Small fill counts, shared BBO paths and as-of ages limit precision; no statistical significance or profitability claim is made.

## Market-level consistency and decision answers

D−B total settlement PnL is **−$0.405**: D wins in eight markets, loses in six, and ties in six. The best D−B market contributes **+$3.100**, the worst **−$5.450**. Excluding the best leaves **−$3.505**; excluding the worst yields **+$5.045**. The two largest positive markets sum to **+$5.050** while aggregate improvement is negative. The sign is sensitive to individual markets; there is no broad profitable edge. Full paired rows, including residual and active-time changes, are in `paired_markets.csv`.

1. **Layer 1:** all observed exclusions are incomplete/crossed books; it uses no depth or time-age reject. It removes data-health states and does not establish alpha. Side independence can retain the valid token when the other is invalid.
2. **Layer 2 markout:** no. Submission-rejected B fills have less negative markouts at 1, 3 and 5 seconds.
3. **Fill composition:** no strong selectivity. Among 29 directly decision-rejected B fills, 16 increase imbalance and 13 balance; the rejected fraction is 16/51 (31%) of B's increasing fills versus 13/28 (46%) of B's balancing fills.
4. **Settlement:** no. D is **$0.405 worse** in aggregate, and more markets lose.
5. **Inventory:** mean time-weighted and final residual improve, but maximum residual does not; matched quantity halves.
6. **Breadth:** market deltas are mixed and outlier-sensitive.
7. **Participation:** D remains active as a maker by the quote-time ratio, but the fill loss is material.

## Limits, reproducibility and handoff

Twenty markets span only 100 market-minutes from two observed periods. Public strict trade-through is a conservative hypothetical fill proxy, not verified own fill truth; official settlement labels establish the winner, not execution. A static BBO weighting need not map monotonically to two complementary Polymarket tokens, and D's decision clock does not react to same-BBO queue changes. The Layer 1 health checks cannot be given economic credit. The −0.5 toxicity boundary and depth buckets are frozen research conventions, not established Polymarket production thresholds. No threshold search, skew retuning, rebate, external BTC data, OFI, volatility gate, VPIN, or strategy state was used.

Reproduce from repository root with `python3 research_tools/dp_backtest_03_three_layer.py`. Outputs under `research_outputs/dp_backtest_03/` are `market_results.csv`, `paired_markets.csv`, `decision_events.csv`, `orders.csv`, `fills.csv`, `blocked_fills.csv`, `markouts.csv`, and `summary.json`. `decision_events.csv` is one row per D token-side decision with both layer outcomes; `blocked_fills.csv` contains B-only fills, including downstream path dependence. The exact B parity assertion passed; Python compilation and `git diff --check` passed. Status: **research result recorded; not independently reviewed or checkpointed**. Canonical predecessor for this study is the current repository at `97ab0f9` plus the pre-existing untracked Backtest 01/02 research artifacts.

**Next action:** Validate the research replay and attribution with an independent review before considering another entry-gate experiment.
