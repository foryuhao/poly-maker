# P0 概率跟随 BUY ONLY 动态库存回放 — 2026-10-04

Status: **INCONCLUSIVE for executable positive edge; C negative under the fixed conservative stop rule.** Independent research, not production authorization. Git base `m4a-a1-pass` (`97ab0f9`); no commit or push. Research design: `docs/research/P0_PROBABILITY_INVENTORY_RESEARCH_DESIGN.md`.

## Sample and source quality

The fixed universe is the first 30 unique markets, chronologically, from the saved 32-market `execution_replay_usable=True` list. All 30 remain in `sample_coverage.csv`; the first opens 2026-10-02 10:25 UTC and the last 2026-10-03 09:25 UTC. They are three capture blocks separated by gaps, not 30 consecutive five-minute windows. All 30 capture sessions start before their market opens and end after its five-minute close. Every selected capture has both token books, public trade records, and a saved identity-checked official winner. Book updates use local `available_at_us`; a quote is only formed when both books are valid and each has updated within two seconds. The capture's reported zero lost-event/gap count does not certify exchange-complete depth at every instant. Full per-market capture boundaries, ticks, source session IDs, token IDs, first/last usable quote times, and rejection reasons are in `sample_coverage.csv`.

Twenty markets have saved historical official market metadata showing 5-share minimum and `crypto_fees_v2`, 7% rate, exponent 1, taker-only, with 0.001 minimum tick. Ten later markets have local official winner labels but no equivalent saved fee/minimum snapshot. C retains them in the fixed sample and marks the 5-share/7% terms as an assumption. This analysis uses no network call. Rebates are zero.

All three markets in which C actually buys belong to the 20 with saved historical fee/minimum metadata; the ten assumption-marked markets have zero C buys because the stop has already triggered. The coverage check found no decreasing local availability timestamps in the selected sessions.

## Fixed results

| Mode | Total PnL | Pair PnL | Residual PnL | Fees | Worst market | Buy shares | Adjustments | Direction switches |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: original 10× ideal | +$25.772 | +$4.904 | +$20.868 | $0 | −$1.806 | 1,151.705 | 31,772 | 4,312 |
| B: 50×, ≥5-share ideal | +$68.245 | −$53.654 | +$121.898 | $0 | −$9.084 | 1,765.775 | 326 | 115 |
| C: B with taker execution and $100 | −$6.799 | −$2.939 | −$3.861 | $1.196 | −$12.653 | 90.125 | 14 | 3 |

A and B are mathematical diagnostics, not tradable results. Their fair-price execution, no spread, unlimited cash, and ideal fills explain why their positive totals cannot establish a tradeable edge. B changes mapping scale as well as adding the 5-share threshold, so its higher PnL cannot be attributed to the minimum-order rule alone. B's Pair accounting is negative even in the ideal case; directional Residual wins offset it in this sample.

C's conservative stop rule triggered in the third market (`btc-updown-5m-1790938500`). It made no buys in the remaining 27 markets. C had two profitable markets, one losing market, and 27 zero-trade markets. The −$20 threshold is a conservative interim exposure measure, not a cap on final settlement loss or aggregate final PnL. C's observed maximum per-market unreturned cash deployment was about $31.05; no order was skipped for lack of the modeled $100 cash. The model assumes 60 seconds for Merge proceeds and 15 minutes after market end for residual redemption. These are not measured settlement or chain times.

| Mode | Final net inventory, summed across markets | Max absolute final net, one market | Cash deployment / gross outlay proxy | Cash-limited skips | Below-5-share decision observations | Incomplete/unfilled orders |
|---|---:|---:|---:|---:|---:|---:|
| A | −16.775 | 4.985 | $51.779 gross outlay proxy | 0 | 0 | 0 |
| B | −76.975 | 24.775 | $101.788 gross outlay proxy | 0 | 31,459 | 0 |
| C | +16.375 | 20.250 | $31.051 maximum outstanding cost | 0 | 1,218 before stop | 7 |

The A/B outlay proxy is not a capital requirement because those modes have unrestricted funds and immediate ideal Merge. Below-threshold counts are repeated decision observations, not independent order opportunities. The final C cash after all assumed recovery events is $93.201, matching initial $100 plus the −$6.799 total PnL.

### Path explanation

The first C market (`btc-updown-5m-1790936700`) earned +$1.198 overall: Pair −$2.939 and Residual +$4.137. The second earned +$4.655 with no Pair gain. Across these and the third market, C bought 90.125 shares; the third ended −$12.653 after the conservative zero-payout valuation tripped the global stop. The exact fills, fees, timestamps, Merge credits, and delayed cash releases are in `ledger.csv`.

Across the full ideal B run, Residual was positive despite negative total PnL in `btc-updown-5m-1790964300` (Pair −$9.397, Residual +$3.561, Total −$5.837), `1790966700` (−$7.668, +$4.880, −$2.788), and `1791017100` (−$5.089, +$4.253, −$0.835). There is no such path among the three C markets with trades. The largest three A profitable markets supplied 12.9% of A's positive-market gain; B's largest three supplied 17.3%; C has only two profitable markets, so its positive side is necessarily concentrated in them.

## Interpretation and limits

The minimum 5-share condition suppresses small rebalances: A makes 31,772 ideal adjustments while B makes 326. C makes 14 simulated buys before the stop; subsequent zero-trade markets are not successes. C incurs explicit taker fees and ask-side execution, and seven submitted orders are incomplete or unfilled within the allowed limit/depth among active markets. An exact numerical attribution of B-to-C degradation to spread versus latency is unsupported because fills and the stop change the inventory path and the number of participating markets. The saved books can support a conditional taker depth replay, but cannot prove hypothetical exchange fill or actual cash-fee treatment. The fee formula is applied as a separate USDC cost, as the local code's stated fee model does; hypothetical actual token receipts are unavailable.

Accounting uses gross buy spend plus separately stated fees once, Merge income, and residual official payout. Average含费 cost allocates Pair and Residual for explanation only. All 30 market rows and ledger entries reconcile to the reported totals. The sample was not filtered for profit and parameters were not tuned. The 27 C zero-trade markets are included in the aggregate; they do not show that the strategy would lose or win there absent the prescribed stop. A/B and C therefore answer different execution questions on the same fixed universe.

## Artifacts and reproduction

Run `python3 research_tools/p0_probability_inventory.py` from the repository root. The local untracked directory `research_outputs/p0_probability_inventory/` contains `params.json`, `sample_coverage.csv`, `ledger.csv`, `market_results.csv`, and `summary.json`. Source code: `research_tools/p0_probability_inventory.py`; checks: `tests/test_p0_probability_inventory.py`. No production source, real order, external API, or account state was used.

## One next question

**With independently verified taker fee/receipt semantics and recovery times, does the same frozen C policy still trip the conservative −$20 stop within the first three markets?** Answer this before changing mapping, threshold, or signal.
