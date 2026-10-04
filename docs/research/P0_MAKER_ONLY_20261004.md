# P0 C-M Maker-only 动态库存回放 — 2026-10-04

Status: **条件性回放为负；真实可执行 PnL 证据不足。** Independent research, awaiting independent review. Git base: `m4a-a1-pass` (`97ab0f9`); P0 C is a local unreviewed comparison. No live order, external request, production modification, commit, or push. Design: `docs/research/P0_MAKER_ONLY_RESEARCH_DESIGN.md`.

## 数据覆盖与固定口径

Exact P0 `sample_coverage.csv` 30-market order, from 2026-10-02 10:25 UTC to 2026-10-03 09:25 UTC; no market was removed or replaced. Three capture blocks contain time gaps. All 30 capture sessions span their five-minute windows, contain both token books and public prints, and have saved identity-checked official winners; local availability timestamps show no reversal. Per-market token IDs, sessions, window boundaries, validity, and metadata flags are in `research_outputs/p0_maker_only/sample_coverage.csv`.

P0's probability, target `50(p−0.5)`, ±25 net cap, 5-share new-order minimum, $100 starting cash, 2-second book freshness, permanent −$20 conservative stop, 60-second Merge release, 15-minute post-close redemption, and zero rebates remain fixed. C-M changes the adjustment order to GTC Post-only BUY at the decision-time best bid, with 500 ms submit and assumed 500 ms cancel delay. Fees are zero for the first 20 markets' saved official taker-only schedule. The later ten lack an equivalent saved fee/minimum snapshot; Maker zero fee and 5-share minimum are explicitly assumptions there. The first 20 contribute +$17.4777; **five of the ten assumption-marked markets trade and contribute −$18.0925**, so this metadata gap matters to the full-sample result.

## Conditional execution results

| Metric | C-M |
|---|---:|
| Total PnL | **−$0.6148** |
| Pair PnL, average含费 allocation | −$1.9950 |
| Residual PnL, average含费 allocation | +$1.3802 |
| Maker fees / rebates | $0 / $0 |
| Largest single-market loss | −$19.5500 (`btc-updown-5m-1791015900`) |
| Markets with trades / zero trades | 17 / 13 |
| Positive / negative / zero-PnL markets | 13 / 4 / 13 |
| BUY shares | 246.23 |
| Submitted / accepted / post-only or invalid-book rejected | 2,169 / 1,786 / 383 |
| Confirmed canceled / fully filled orders | 1,766 / 20 |
| Orders with any fill / fill events / partial-fill events | 22 / 23 / 3 |
| Fill during cancel wait | 16 events, 201 shares |
| Conditional any-fill / full-fill share of accepted orders | 1.232% / 1.120% |
| Mean per-market observed absolute target tracking error | 10.818 shares |
| Maximum observed target tracking error | 45.5 shares |
| Peak one-market outstanding cost incl. reservation | $29.915 |
| Cash-limited skips / below-5-share decision observations | 0 / 15,589 |
| Minimum logged conservative stop metric | −$19.2725 |
| Stop triggered / stop-after markets | No / 0 |
| Cash after all assumed recoveries | $99.3852 |

The below-5 count is repeated decision observations, not independent order attempts. Tracking error is observed at eligible decisions, not time weighted. The −$20 conservative line was approached but never crossed in this conditional path; therefore there is no genuine stop path to display. The T−15 order-cancel rule remained in force; no order happened to remain active at that boundary.

## Queue and lifecycle evidence

An accepted order joins behind the full displayed same-price bid size at its arrival. Only subsequent same-token aggressive `SELL` volume at or below its price, with all recorded clocks after arrival, consumes that queue and then its remaining quantity. Unattributed L2 cancellations never advance it. Twenty-two accepted orders receive any simulated fill; two end partially filled, while 20 fill completely. The 23 fill events include three partial events. All 2,169 submissions close as 383 rejects or 1,786 accepts; all accepts close as 1,766 confirmed cancellations or 20 full fills. A fill may occur while cancellation is pending. One print's volume is allocated first to queue ahead and then to this sole outstanding order; no event can fill a second simultaneously active order.

This rule is conditional rather than observed FIFO execution. Public prints do not carry the hypothetical order ID, hidden queue is unknown, and complementary-token matching may produce fills not represented by same-token `SELL` prints. The 16 cancel-race fill events (201/246.23 filled shares) make the estimate especially sensitive to event timing and public-print attribution. The 383 rejections include 380 orders that would cross the ask on arrival and three invalid-book arrivals; none becomes a Taker order. Reprices mostly follow one-tick-or-larger best-bid changes; canceled remaining quantity is never silently treated as filled.

## Representative paths

- **Profit:** `btc-updown-5m-1790962500` earns +$4.1575 from two NO Maker fills, 18.75 shares total; Pair $0, Residual +$4.1575. The outcome is applied only at settlement.
- **Loss:** `btc-updown-5m-1791015900` buys 21.25 NO shares at $0.92 and the official winner is YES. Residual/total PnL is −$19.55.
- **Near stop, without a stop trigger:** the logged conservative metric reaches its minimum of −$19.2725 in `btc-updown-5m-1790940000`. It remains above −$20; no later market is labeled stopped.
- **No fill:** `btc-updown-5m-1790936700` submits 72 orders, 64 are accepted and canceled, eight rejected, and none fills. Its PnL is zero, not a profitable success.
- **Partial and cancel race:** `btc-updown-5m-1791015300` has one NO order with two public-print fill events (10.65 then 9.60 shares). The order remains live after the first partial fill; its remaining reserve and queue state are carried forward.

Thirteen profitable markets produce +$29.6777 and four losing markets −$30.2925. The worst market alone loses $19.55, greater than the aggregate net loss. The top three winners account for 41.6% of positive-market gain. This concentration prevents a robust edge claim from the small sample.

## Frozen P0 C comparison

| Scope | P0 C Taker | C-M Maker |
|---|---:|---:|
| Full same 30-market table: total PnL | −$6.7993 | −$0.6148 |
| Full table: traded markets | 3 | 17 |
| Full table: stop-after markets | 27 | 0 |
| First three markets: total PnL | −$6.7993 | +$2.7600 |
| First three markets: traded markets | 3 | 1 |

The full-sample PnL difference reflects different trading participation and the permanent stop in C. Even within the first three markets, C-M has no fill in the first two. It cannot be attributed directly to Maker versus Taker price or fees. P0 A/B were not rerun and their files were not overwritten.

## Accounting, validation, and limits

`TotalPnL = Merge income + winning residual settlement − gross BUY spend − fees`, with zero rebate. Pair and Residual use average含费 cost only as an explanatory split. The 30 market rows reconcile to individual fills, Merge, fees, and official settlement in `ledger.csv`; Pair plus Residual equals Total for every row. Cash never becomes negative, and the final $99.3852 equals $100 plus total PnL. Interim locked Pair PnL (−$1.995), final settlement PnL (−$0.6148), and the minimum conservative stop metric (−$19.2725) are separate quantities. Merge and redemption delays are assumptions, and unknown chain cost/failure is not counted.

This is a negative result **under the declared conditional fill rule** on the fixed 30 markets. It does not establish the sign of actual executable PnL, particularly because the last-ten metadata are incomplete and most simulated filled volume occurs during cancel latency. No sample selection, retuning, rebate credit, or future-information direction choice was applied.

## Reproduction and handoff

Run `python3 research_tools/p0_maker_only.py` from the repository root. Local untracked artifacts: `research_outputs/p0_maker_only/{params.json,sample_coverage.csv,orders.csv,ledger.csv,market_results.csv,summary.json}`. Tests: `.venv/bin/python -m pytest -q tests/test_p0_maker_only.py`. The next step is independent review of queue attribution, cancel-timer ordering, reserve and stop accounting, and the five traded markets with assumed metadata. No live authority follows from this research.
