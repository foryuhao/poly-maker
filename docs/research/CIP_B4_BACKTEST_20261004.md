# CIP B4 Residual Budget Backtest — 2026-10-04

Result: **RESIDUAL CONTROL WORKS, STRATEGY STILL NEGATIVE**. Research only. The exact B2 32-market loader, official labels, event clock, books, fill rule, latencies, capital and Reduce Only implementation were reused. B4 adds only the five-share Phase 1 net-inventory guardrail. Source: `research_tools/cip_b4_residual_budget_backtest.py`; outputs: `research_outputs/cip_b4_20261004/`.

## Sample and frozen replay

All 32 B2 execution-replayable BTC5m markets and 32 official settlement labels match, with no replay error; 19 overlap stricter M4A integrity PASS. Submit latency is 415,281 μs, cancel latency 234,783 μs; $25 capital, five-share clips, strict maker SELL-print trade-through, T150 and T300 are unchanged. B2 book-invalid cancel-all behavior remains. The B4 gamma order/fill prefix is deliberately different from B1/B2 because of the sole B4 treatment. No production code, real order or external interface was used.

## B2 versus B4

| Measure | B2 | B4 | B4 − B2 |
|---|---:|---:|---:|
| Phase 1 fills / purchased shares | 238 / 1,190 | 166 / 830 | −72 / −360 |
| Matched shares at T150 | 405 | 360 | −45 (−11.1%) |
| T150 residual | 380 | 110 | −270 (−71.1%) |
| Matched-weighted PoolPairCost at T150 | 1.00434 | 1.05532 | +0.05099 |
| GammaPnL at T150 | −$1.76 | −$19.92 | −$18.16 |
| Reduce fills / shares | 27 / 135 | 16 / 80 | −11 / −55 |
| Residual reduction after T150 | 135 (35.5%) | 70 (63.6%) | −65 shares, +28.1 points |
| Final matched shares | 540 | 435 | −105 |
| Final residual | 245 | 40 | −205 (−83.7%) |
| GammaPnL final | +$3.03 | −$26.96 | −$29.99 |
| ResidualPnL | −$72.45 | −$15.31 | +$57.14 |
| TotalPnL | −$69.42 | −$42.27 | **+$27.15** |
| `REDUCE_CAPITAL_BLOCKED` decision events | 4,075 | 0 | −4,075 |
| Profitable / losing / zero-PnL markets | 7 / 24 / 1 | 4 / 27 / 1 | — |

The residual-PnL gain exceeds the final matched-edge loss by $27.15, but B4 remains materially negative. The favorable paired total delta occurs in 21 markets, unfavorable in 10, tied in one; excluding the best +$7.90 market still leaves +$19.25. The largest adverse paired market is −$12.00 (`btc-updown-5m-1791015900`).

## Phase 1 — Gamma Harvest

The 166 fills bought 410 YES and 420 NO shares. At T150, matched inventory is 360, residual 110, GammaPnL −$19.9167 and weighted PoolPairCost 1.055324. `phase_transitions.csv` records each market's pre-switch QY, QN, Net, Matched, AvgY, AvgN, pool cost, GammaPnL, capital and working orders. Aggregate capital used at T150 is $520.70 across 32 independently capitalized markets.

There were 94 guardrail activations; active duration sums to 3,401.376 market-seconds of 4,800 possible (70.86%). Mean per-market event-sampled absolute net was 3.174 shares; the real-time-weighted mean was 3.543 shares. Maximum Phase 1 absolute net was exactly five. There were 11,676 repeated blocked-placement decisions, no heavy-side cancellation request in the observed path (the qualifying heavy-side order was already filled when the budget became active), **zero Phase 1 overshoot events**, zero overshoot shares and zero affected markets. These skip decisions are repeated evaluation events, not distinct orders. After a balancing fill returns net to zero, the B2 dual-quote engine resumes; 72 recovery transitions were observed.

Phase 1 fill count retained 69.7% of B2 and matched shares retained 88.9%, so the engine did not nearly stop trading. However, the average matched pool became materially more expensive: the budget prevented some low-price heavy-side accumulation that had helped B2's average costs. The Phase 1 matched-edge loss is −$18.16, larger than the 11.1% matched-share decline.

## Phase 2 — Reduce Only

The inherited B2 logic produced 16 Reduce fills (80 shares). One valid B2-style cancel-pending fill in `btc-updown-5m-1791020100` raised net from zero to an absolute five shares after the reduce-stop latch; it explains why 80 purchased Reduce shares changed matched shares by 75 and aggregate absolute residual by only 70. This is **not** a Phase 1 guardrail overshoot. Ten markets were already flat at T150. Fifteen of 22 T150-nonflat markets reached flat during Phase 2 (68.18%); **24 of 32** finished inventory-flat because that cancel-race fill reopened one initially flat market. Median time to first flat was 19.715 seconds for those 15. There were zero `REDUCE_CAPITAL_BLOCKED` decisions. Seven T150-nonflat markets never reached flat; eight markets were nonflat at T300.

## Throughput and economics

| Measure | B2 | B4 |
|---|---:|---:|
| All fills per market | 8.281 | 5.688 |
| Phase 1 fills per market | 7.438 | 5.188 |
| Matched shares per market at T150 | 12.656 | 11.250 |
| Final matched shares per market | 16.875 | 13.594 |
| Total purchased shares | 1,325 | 910 |
| Final PairingEfficiency | 81.51% | 95.60% |
| Phase 1 PairingEfficiency | 68.07% | 86.75% |

`PairingEfficiency=2*Matched/(YES+NO purchased)`. Better pairing efficiency partly reflects less directional inventory, while lower total fills and matched shares show the opportunity cost. The key tradeoff is final GammaPnL −$29.99 versus ResidualPnL +$57.14, leaving TotalPnL +$27.15 relative to B2.

## Representative paths

Times are elapsed seconds within each market. `Y/N` is QY/QN; `W Y/N` is working YES/NO maker BUY price after same-timestamp action; `—` means no working order or undefined pool cost. Capital includes reserved open notional. Full paths and all actions are in `inventory_paths.csv`, `guardrail_events.csv` and `action_audit.csv`.

### Frequent guardrail activation, profitable: `btc-updown-5m-1790961300`

Four activations, 93.3 guardrail-active seconds; final PnL +$0.40. At T150 net −5; a B2 Reduce YES fill at T157.755 flattens it.

| t | Phase | Fill | Y/N | Net / matched | Pool cost / GammaPnL | Guardrail | W Y/N | Capital | Action / reason |
|---:|---|---|---:|---:|---:|---|---|---:|---|
| 13.968 | Gamma | NO 5 @ .66 | 0/5 | −5 / 0 | — / $0.00 | active | .33 / — | $4.95 | SKIP / PHASE1_GUARDRAIL |
| 45.716 | Gamma | YES 5 @ .33 | 5/5 | 0 / 5 | .990 / +$0.05 | off | .12 / .87 | $9.90 | PLACE both / POST_FILL_NEW_ORDER |
| 70.130 | Gamma | YES 5 @ .07 | 15/10 | +5 / 10 | .938 / +$0.62 | active | — / .92 | $14.85 | SKIP / PHASE1_GUARDRAIL |
| 114.353 | Gamma | NO 5 @ .92 | 15/20 | −5 / 15 | 1.013 / −$0.20 | active | .07 / — | $19.75 | SKIP / PHASE1_GUARDRAIL |
| 150.000 | Reduce | switch | 15/20 | −5 / 15 | 1.013 / −$0.20 | off | .04 / — | $19.60 | PHASE_SWITCH / T150 |
| 157.755 | Reduce | YES 5 @ .04 | 20/20 | 0 / 20 | .980 / +$0.40 | off | — / — | $19.60 | FILLED / ORDER_FILLED |

### Large B2 residual improved: `btc-updown-5m-1790939100`

B2 T150 30 → final 25; B4 T150 5 → final 0. B4 PnL −$1.35, a +$7.90 paired improvement, so residual repair did not make the market profitable.

| t | Phase | Fill | Y/N | Net / matched | Pool cost / GammaPnL | Guardrail | W Y/N | Capital | Action / reason |
|---:|---|---|---:|---:|---:|---|---|---:|---|
| 5.721 | Gamma | NO 5 @ .33 | 0/5 | −5 / 0 | — / $0.00 | active | .66 / — | $4.95 | SKIP / PHASE1_GUARDRAIL |
| 150.000 | Reduce | switch | 0/5 | −5 / 0 | — / $0.00 | off | .88 / — | $6.05 | PHASE_SWITCH / T150 |
| 193.355 | Reduce | YES 5 @ .94 | 5/5 | 0 / 5 | 1.270 / −$1.35 | off | — / — | $6.35 | FILLED / ORDER_FILLED |

### Lost Gamma opportunity: `btc-updown-5m-1791015900`

B2 made +$12.10 here; B4 made +$0.10 (−$12.00 paired). Phase 1 fills fell from eight to four; B4 reached T150 flat but bought fewer favorable YES clips.

| t | Phase | Fill | Y/N | Net / matched | Pool cost / GammaPnL | Guardrail | W Y/N | Capital | Action / reason |
|---:|---|---|---:|---:|---:|---|---|---:|---|
| 17.511 | Gamma | NO 5 @ .52 | 0/5 | −5 / 0 | — / $0.00 | active | .47 / — | $4.95 | SKIP / PHASE1_GUARDRAIL |
| 36.025 | Gamma | YES 5 @ .30 | 5/5 | 0 / 5 | .820 / +$0.90 | off | .25 / .74 | $9.05 | PLACE both / POST_FILL_NEW_ORDER |
| 43.896 | Gamma | YES 5 @ .25 | 10/5 | +5 / 5 | .795 / +$1.03 | active | — / .74 | $9.05 | SKIP / PHASE1_GUARDRAIL |
| 137.760 | Gamma | NO 5 @ .91 | 10/10 | 0 / 10 | .990 / +$0.10 | off | .09 / .90 | $14.85 | PLACE both / POST_FILL_NEW_ORDER |
| 150.000 | Reduce | switch | 10/10 | 0 / 10 | .990 / +$0.10 | off | .09 / .90 | $14.85 | CANCEL both / REDUCE_BALANCED |

There is no Phase 1 overshoot market in this replay, so no fourth overshoot path exists.

## Safety audit and interpretation

All 32 transitions occur at exact T150. All 182 fills meet the inherited strict SELL print price and three post-activation timestamp checks. No Phase 1 new order projects a risk-increasing net beyond five; maximum actual Phase 1 absolute net is five. No Phase 2 new order is on the heavy side; all `BOOK_INVALID` cancellations occur with invalid reconstructed books. Capital stays ≤$25 and `BBO_ONLY_REPRICE=0`; final GammaPnL plus ResidualPnL equals settlement-cash-flow TotalPnL. `summary.json` has no implementation-invalid action.

There is a durable specification/code tension: the B2/B1 source contains an inherited `matched >= TARGET` (~25 shares) cancellation condition at balanced inventory, while the prose calls B2 “no pair cap.” B4 retains **the executable B2 rule** to preserve exact-path comparability; it adds no new pair cap. Independent review should confirm this inherited behavior is the intended B2 baseline before promoting the result beyond research.

## Answers and limitation

Q1: T150 residual falls from 380 to **110**. Q2: T150 GammaPnL falls by $18.16, matched T150 by 45 shares and Phase 1 fills by 72. Q3: 15/22 T150-nonflat markets reach flat versus B2's 9/26. Q4: Final residual falls by 205 shares and ResidualPnL improves $57.14. Q5: TotalPnL improves $27.15 to −$42.27, still materially negative. Q6: Gamma trading is reduced, not killed, but pool quality deteriorates sharply: T150 weighted cost rises from 1.0043 to 1.0553.

The inherited full-order SELL-print trade-through rule omits queue position, so fill counts are replay diagnostics rather than live execution forecasts. The 32 markets are selected for replayability and only 19 pass stricter telemetry integrity. B4's independent research output and the local B2 predecessor remain unreviewed; the inherited matched-target discrepancy above needs explicit review. No 10/15-share sweep or strategy optimization was performed.

Next action: independently review the Phase 1 guardrail placement and cancellation paths against B2, including the inherited matched-target behavior.
