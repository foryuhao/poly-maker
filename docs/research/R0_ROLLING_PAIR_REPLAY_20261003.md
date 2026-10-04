# R0 Rolling Pair Replay — 2026-10-03

Status: **PAIR EDGE EXISTS BUT RESIDUAL DOMINATES**
Research only; no production trading code changed.
Replay universe: the exact 32 unique markets marked `execution_replay_usable=True` in `research_outputs/execution_replay_audit_20261003/dataset_audit.csv`.

## Question and frozen controls

Test whether a rolling maker BUY/BUY engine can complete five 5-share pairs in one BTC5m market under a $25 cash cap and 0.98 maximum combined purchase price. Controls were fixed at $25 starting capital, 5 shares per side, 0.98 pair cap, five target pairs, BUY only, maker/post-only, zero fees/rebates, and no merge/redeem or cash recycling. Filled pair costs stay locked through settlement.

The source implementation is [direkturcrypto/polymarket-terminal](https://github.com/direkturcrypto/polymarket-terminal). Its documented main strategy places maker BUY orders on both outcomes, holds them without repricing, waits for both sides, and re-enters after a completed cycle ([README](https://github.com/direkturcrypto/polymarket-terminal#readme), [executor](https://github.com/direkturcrypto/polymarket-terminal/blob/main/src/services/makerRebateExecutor.js)). The replay follows the source quote construction: quote the cheaper best bid one tick higher, back off to two ticks below the ask when needed, derive the other bid from the 0.98 combined cap, and enforce the source default cheap-side range of 0.30–0.69 and its one-tick minimum-combined rule ([quote code](https://github.com/direkturcrypto/polymarket-terminal/blob/main/src/services/makerRebateExecutor.js#L633-L691), [defaults](https://github.com/direkturcrypto/polymarket-terminal/blob/main/src/config/index.js#L81-L96)). These are source defaults, not values optimized on this sample. Once submitted, quotes remain fixed. Re-entry is immediately eligible after a pair completes.

The source executor also cancels outstanding orders in its default 60 seconds before market end. The replay applies that source default and uses the existing pooled p75 cancel latency. It does not apply the source's merge or redeem behavior ([cut-loss handling](https://github.com/direkturcrypto/polymarket-terminal/blob/main/src/services/makerRebateExecutor.js#L474-L500)).

## Data and replay method

- Read the saved 42-session audit and selected exactly its 32 execution-replay-capable sessions / 32 unique markets. The 10 sessions already excluded as too short stayed excluded. No sessions were added, combined, or repaired.
- Reconstructed both token books from recorded `BOOK`, `REST_BOOK`, `DELTA`, and `TICK` events and made decisions on recorded available-time order.
- Reused the existing execution replay's p75 activation latency of **415,281 μs** and p75 cancel latency of **234,783 μs**, from `execution_replay_audit_20261003/summary.json`.
- Reused its fill rule exactly: a same-token public SELL print strictly below an active resting BUY, with exchange, received, and available timestamps all strictly after activation. An eligible print fills the full 5-share order, regardless of the print's size. Equal-price trades and book touches do not fill. No queue position or partial order fill is simulated.
- Tracked filled purchase cost plus open-order reserve at each decision; checked the $25 cap and recorded peak usage. The second-leg maximum is `0.98 − first_leg_price`.
- No official resolution labels were persisted in the execution audit. Retrieved each winner through the official Polymarket Gamma events API and checked closed/resolved status, condition ID, and ordered YES/NO token IDs against the capture manifest before marking residual positions. Matched-pair PnL is kept separate from residual settlement PnL.

The implementation is [r0_rolling_pair_replay.py](../../research_tools/r0_rolling_pair_replay.py). Detailed output is in the local, ignored research artifact directory `research_outputs/r0_rolling_pair_replay_20261003/` (`summary.json`, `markets.csv`, `pairs.csv`, `orders.csv`, `fills.csv`, `settlement_labels.csv`, `sample_audit.csv`, and `cycle_timelines.md`).

## Primary results — all 32 markets

### Rolling throughput

- **0/32 markets (0%)** completed 5/5 pairs.
- Mean pairs per market: **1.25**; median **1**; p25 **0**; p75 **2**.
- Mean matched shares per market: **6.25**.
- Three markets reached four completed pairs; none reached five.

### Economics

- PairPnL: **+$5.45 aggregate**, **+$0.1703 mean**, **+$0.15 median** per market.
- ResidualPnL: **−$50.70 aggregate**.
- TotalPnL: **−$45.25 aggregate**, **−$1.4141 mean** per market.
- **7 profitable / 25 losing / 0 flat** markets.

Each completed pair had positive gross edge under the cap. That edge did not cover the settlement losses on unmatched legs.

### Pair costs

- 40 completed pairs; weighted mean combined cost **0.97275**, median **0.97**.
- Best / worst pair cost: **0.97 / 0.98**.
- Cost buckets: **0% ≤0.95**, **72.5% at 0.951–0.97**, **27.5% at 0.971–0.98**.

The source's one-tick minimum-combined filter largely restricts this implementation to 0.97–0.98 pair costs.

### Capital

- Mean / median / maximum peak capital used: **$10.3375 / $9.70 / $24.30**.
- Markets blocked by the $25 cap: **0**.
- No cash was recycled after a pair completed.

### Second-leg risk

- Second-leg lag mean **48.88s**, median **29.61s**, p75 **61.89s**, p90 **112.39s**, maximum **170.69s**.
- **28 of 68 first legs (41.2%)** never completed their opposite leg.
- Primary market failure counts: **FIRST_LEG_STUCK 28**, **LOW_FILL_RATE 4**, **NO_PAIR_OPPORTUNITY 0**, **CAPITAL_BLOCKED 0**, **TIME_EXPIRED 0**, **DATA_FAILURE 0**, **EXECUTION_LOGIC_FAILURE 0**.
- Secondary quote-decision counts: valid source quote **14,452**; source cheap-side band rejected **18,887**; invalid/incomplete book **4,636**; source minimum combined-cost rule **129**; YES/NO tick mismatch **1**.
- Replay recorded **108 full-clip fill records** and **0 partial-fill records**. The inherited simulator cannot represent partial fills.

### Representative timelines

Highest-throughput market (four pairs, then a fifth first leg remained unmatched):

```text
btc-updown-5m-1790966700
Pair 1: YES @ 0.610 → 6.799s → NO @ 0.360 → +$0.150
Pair 2: YES @ 0.650 → 29.848s → NO @ 0.320 → +$0.150
Pair 3: NO @ 0.300 → 7.450s → YES @ 0.680 → +$0.100
Pair 4: YES @ 0.500 → 75.500s → NO @ 0.470 → +$0.150
Pair 5: YES @ 0.620 → NO never completed (max $0.360)
```

No market achieved the 5/5 target. A zero-pair failure illustrates the stuck-leg loss:

```text
btc-updown-5m-1790937900 (official winner: NO)
Pair 1: YES @ 0.450 → NO never completed (max $0.530)
ResidualPnL: −$2.25
```

All per-market sequences are preserved in `cycle_timelines.md`.

## M4A-passing diagnostic

The M4A audit records **29 integrity-passing sessions across the full 42-session corpus**. Only **19** of those overlap the 32 selected replay sessions; the other 10 passing sessions are outside the replayable universe. On the 19-market overlap: **0/19** reached 5/5; mean pairs were **1.21**; PairPnL **+$3.15**; ResidualPnL **−$26.80**; TotalPnL **−$23.65**; **6 profitable / 13 losing**. This diagnostic agrees with the primary finding and is not a substitute for the 32-market primary sample.

## Classification and interpretation

**PAIR EDGE EXISTS BUT RESIDUAL DOMINATES.** The replay completed 40 pairs at 0.97–0.98 combined cost for +$5.45 gross pair edge, but 28 first legs remained unmatched and settlement residuals totaled −$50.70. Capital was not the bottleneck; $25 never blocked a cycle. The dominant R1 blocker is **FIRST_LEG_STUCK**.

## Limitations

The replay uses the repository's conservative price-trade-through rule but that rule grants the entire 5-share fill on any qualifying public SELL print regardless of printed size and does not model queue position. Thus it is not a calibrated own-order fill probability model, and full-clip fills may be overstated when print size is below five shares. The sample is 32 recorded markets from two capture periods on one date, while the source's default cheap-side filter and one-tick combined-cost floor constrain the quote opportunities. Residual outcomes are official API outcomes checked against capture identity, but this replay is still historical simulation evidence rather than proof of live profitability.

## Reproducibility and handoff

Run `python3 research_tools/r0_rolling_pair_replay.py` from the repository root. It reads the audited session list and event captures, reuses the identity-checked settlement labels once saved, and rewrites only the ignored R0 output directory. No production code or raw capture was changed. No parameter search was performed.

**Next authorized research action:** make R1 specifically address the dominant `FIRST_LEG_STUCK` blocker while preserving this R0 run as the frozen comparator; do not tune R0 parameters.
