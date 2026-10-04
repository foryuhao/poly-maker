# B2 Residual Attribution, 2026-10-03

**Result: EXECUTION / NO-FILL IS PRIMARY BOTTLENECK.** This is a research-only diagnostic of the frozen B2 replay, not a strategy change. The 32-market baseline was rerun through the existing B2 class; every market reproduced its final net, PnL, fill count and capital-block event count. No external data or order interface was used.

## Inventory and PnL reconciliation

`380 T150 residual shares − 135 shares reduced after T150 = 245 final residual shares.` The 27 actual Reduce Only fills acquired exactly 135 shares and increased matched quantity by 135; there were no observed cancel-race or sign-flip overshoots. The final residual-PnL sum is −$72.4467. The category partition below sums to exactly 245 shares and −$72.4467, within floating-point rounding.

| Primary cause | Markets with final residual | Final shares | ResidualPnL |
|---|---:|---:|---:|
| Capital blocked | 4 | 65 | −$18.2357 |
| Accepted reduce order, no fill | 9 | 130 | −$37.7300 |
| Book/execution blocked | 4 | 50 | −$16.4810 |
| Minimum order/sign flip | 0 | 0 | $0 |
| Time exhausted after valid reduce | 0 | 0 | $0 |
| Other | 0 | 0 | $0 |
| **Total** | **17** | **245** | **−$72.4467** |

Each market receives one primary label and at most one secondary label. Capital is primary only if the joint capital-only counterfactual removes at least half that market's final residual; otherwise a historical accepted/unfilled lighter-side order takes the no-fill label, and a remaining invalid book/quote takes the execution label. This market-level partition and the 65-share marginal capital counterfactual are different measures; their numeric equality here is coincidental. The complete 32-market assignments, secondary causes, blocked seconds and counterfactual reductions are in `market_attribution.csv`.

## Capital blocks: events, episodes and shortfall

The 4,075 `REDUCE_CAPITAL_BLOCKED` decision events collapse to **150 continuous episodes** in 11 markets, totaling 506.804 blocked seconds. Median/p75/p90 episode duration was **0.773/3.173/9.311 seconds**; the maximum was 54.296 seconds. Median/mean event-level shortfall for the intended 5-share lighter-side quote was **$2.15/$2.19** (p90 $3.90). Each row of `blocked_events.csv` records timestamp, net, lighter side, desired price, 5-share notional, filled cost, existing reservations, available cash and shortfall; `blocked_episodes.csv` gives episode bounds and net/shortfall extremes. Thus the 4,075 count materially overstates the number of distinct capital failures, while the cumulative blocked time is still substantial.

## Capital-only counterfactual

All **150** episode-start repair orders were placeable with only the exact capital shortfall supplied. **25** would fill under the unchanged B2 maker predicate and lifecycle. For 125 episodes, there was no eligible official same-token SELL print after activation and before the first book-invalid cancellation became effective, so an exact necessary-condition screen proves the hypothetical order could not fill; the 25 candidates received full B2 replays. These episode effects overlap and are not summed.

In the nonduplicated joint replay, capital was left at $25 through T150 and raised after T150 only when B2 was about to reject a legal lighter-side 5-share order. All subsequent B2 rules, sticky quotes, latencies and fills remained unchanged. Final residual fell from **245 to 180 shares**, making **65 shares (26.5%)** capital recoverable. This is meaningful but does not make capital the dominant source of the remaining 245 shares. `capital_counterfactual.csv` records each episode's order/fill result; `joint_capital_counterfactual.csv` records the market-level net effect.

## Maker no-fill and timing

The Reduce phase submitted **113** new orders; all were accepted, **24** filled and **89** did not. Three of the 27 Reduce-phase fills came from orders already resting at T150. All 89 accepted/unfilled new orders were later canceled for `BOOK_INVALID`. Their median/mean accepted-to-close lifetime was **2.332/7.423 seconds**; p75/p90 was 9.785/18.114 seconds. At submission, the market had a median **118.584 seconds** remaining, so simple lack of nominal market time does not explain most failures. The median quote distance from contemporaneous best bid was **zero**. During each order's actual resting interval, 82 of 89 had no public SELL print below its quote; the other seven had lower prints that still did not satisfy all frozen activation/timing/cancellation predicates. For the 27 successful Reduce fills, mean/median/p75/p90/max accepted-to-fill time was **5.942/6.360/7.473/11.969/13.542 seconds**. These observations support a combined maker no-fill and book-validity bottleneck, not systematic BBO chasing or a universal late-placement problem.

## T150 state diagnostics

| T150 residual bucket | Markets | T150 → final shares | Reduction | Capital-blocked markets | Flat markets |
|---|---:|---:|---:|---:|---:|
| 0 | 6 | 0 → 0 | NA | 0 | 6 |
| 1–5 | 3 | 15 → 0 | 100.0% | 1 | 3 |
| 6–10 | 8 | 80 → 30 | 62.5% | 1 | 4 |
| 11–15 | 7 | 105 → 80 | 23.8% | 5 | 1 |
| 16–20 | 5 | 100 → 70 | 30.0% | 3 | 1 |
| >20 | 3 | 80 → 65 | 18.8% | 1 | 0 |

| T150 committed capital | Markets | Mean T150 residual | Final residual shares | Reduce success rate | Blocked-market rate |
|---|---:|---:|---:|---:|---:|
| <$15 | 6 | 13.33 | 60 | 50.0% | 0% |
| $15–$20 | 7 | 12.14 | 30 | 71.4% | 0% |
| $20–$23 | 8 | 12.50 | 65 | 50.0% | 50.0% |
| $23–$24 | 3 | 8.33 | 25 | 0% | 66.7% |
| >$24 | 8 | 11.25 | 65 | 50.0% | 62.5% |

Larger T150 imbalance correlates with a lower observed reduction rate: the >20-share bucket reduced only 18.8%, versus 62.5% for 6–10 shares. High T150 capital commitment raises the incidence of later blocks, but does not determine the result by itself: four of eight markets above $24 still flattened, while markets below $15 retained 60 shares with no capital block.

## Answers to the six diagnostic questions

1. **Capital-primary final residual:** 65 shares across four markets by the declared market-level primary rule. A further five shares are capital-recoverable in one market whose primary cause is book/execution, so primary labels are not marginal effects.
2. **Capital-only recoverability:** 65 additional shares, 26.5% of the 245; joint counterfactual final residual is 180 shares.
3. **Remaining causes:** 130 shares are assigned to accepted maker orders that did not fill before book invalidation, and 50 to book/execution blockage without such an accepted unfilled order. The 180-share noncapital total is larger than the capital counterfactual effect.
4. **Meaning of 4,075 events:** They represent 150 episodes, not independent order opportunities; median episode duration is 0.773 seconds. The 506.804 cumulative seconds and 65 recoverable shares show a real but secondary constraint.
5. **T150 capital:** Commitment above $20 is associated with more blocks, but does not predetermine Reduce success. Baseline inventory size and subsequent book/fill evidence also matter.
6. **Maker-only capacity:** It recovered 135 shares in the actual B2 run and could recover 65 more under the frozen capital-only counterfactual, but still left 180 shares. It is capable of partial reduction within 150 seconds, not reliable full flattening on this sample.

## Scope and limitation

The no-fill/book distinction is a **primary-cause convention** on markets that often experienced both: every one of the 89 unfilled accepted orders was later invalidated by the reconstructed book. The counterfactual uses the inherited full-remaining-order trade-through predicate, with no queue model or public-print-size cap, so 65 is a model-based recoverability estimate rather than live fill capacity. Only 19 of the 32 execution-replayable markets overlap the stricter M4A integrity-PASS subset. No missing book history was repaired and no alternate price, sizing, timing or capital policy was optimized.

Reproduce with `python3 research_tools/b2_residual_attribution.py` from the repository root. The script reads repository-local official captures and B2 artifacts and writes only under `research_outputs/b2_residual_attribution_20261003/`.

Next action: independently review the 25 filled episode counterfactual paths and the joint 65-share reconciliation.
