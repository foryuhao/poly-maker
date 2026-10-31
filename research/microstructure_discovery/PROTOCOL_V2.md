# Prospective execution study — frozen 2026-10-31

Pilot used only for instrumentation and hypotheses. No pilot performance enters validation. No live orders.

## Hypotheses and candidates (four fixed signal rules)

Signals operate on primary outcome only (first token in discovery metadata), preventing mirror-trade double counting. Buy signals acquire the primary token for continuation, complementary token for fade. Sell signals acquire the complementary token for continuation, primary for fade. Both use fully cash-funded BUY then SELL, never assume a naked short.

1. `follow_imbalance`: trade direction agrees with receipt-time near-depth imbalance >=0.5 in absolute directional terms; spread <=0.02; trade notional >=5 USDC.
2. `fade_imbalance`: trade direction disagrees with near-depth imbalance by >=0.5; same spread/notional limits.
3. `follow_large_flow`: trade notional >=50 USDC, receipt spread <=0.01.
4. `fade_large_flow`: same eligibility as (3), opposite directional bet.

Two fixed holding periods 30s and 120s; eight strategy/horizon combinations. Minimum 120-second signal spacing per economic condition, shared across both outcomes and rules. Signals selected strictly chronologically. Orders simulated at 0.5s, 1s, 2s decision-to-arrival delays; market-depth VWAP for 5 shares both legs. Decision uses only data already received, never outcome labels or subsequent returns. No midpoint fills or maker-fill assumptions.

## Design

Extended 30-minute discovery run: first 15 minutes hypothesis development/training; last 15 minutes initial chronological validation. At least 120s embargo at training/validation boundary and last eligible signal 122s before end. Market/subgroup selection only in training, frozen before reading validation performance. Same-source related outcomes/markets are correlated. Primary statistical blocks: five-minute receive-time intervals; market/event group robustness reported separately. A single match or single event cannot establish generality.

After training choose at most one strategy/horizon/market candidate with at least 5 nonoverlapping training entries and positive conservative net total. If none qualify, record no candidate, do not relax rules retrospectively. Initial validation is only a screen and requires at least 5 entries and positive net mean at every registered execution delay. Proof-standard confirmation requires a second entirely new collection window of at least 90 minutes, positive validation mean, at least 20 nonoverlapping entries, at least 6 time blocks, one-sided family-adjusted 95% lower confidence bound >0, and positive result under 2-second execution delay; rule/market selection must be recorded before it starts. These are operational criteria for evidence, not mathematical proof of permanent profitability.

## Costs, data and failure policy

Preserve Gamma feeSchedule and CLOB fee-rate responses separately; inconsistent fee sources are unresolved. Until reconciled stress charge 0.10*p*(1-p) per share on each taker leg, no rebates, plus 0.001/share/leg adverse execution reserve. This is a conservative scenario, not claimed actual fee schedule. Actual-fee result only when verified from current authoritative per-market parameters. Reject trades outside source/receive clock bounds and duplicate signatures as V1.

Replay L2 from raw messages. On reconnect invalidate all books. After atomic price-change application compare reconstructed best prices with supplied BBO; disagreement invalidates execution until a complete book or a subsequent matching BBO. Source-time regression invalidates affected book until full snapshot. Explicit market resolution/removal prevents new execution. Unfillable entry => no entry; attempted exit with insufficient bids => position marked at zero for conservative return and reported separately; never silently drop losing/non-exitable entries. End-window unmatured signals ineligible at selection, not after seeing outcomes.

A qualifying simulated result is still subject to public-data sequence completeness and paper-execution limitations; no assertion of real fills. Candidate may need external event alignment to establish whether it distinguishes information from liquidity. No live trading unless separately requested.

Amendment before validation collection: five training entries and five initial-validation entries are screening minima because a 15-minute segment cannot contain ten 120-second-separated entries. Twenty entries remains mandatory for the separate 90-minute confirmation.

## Fee amendment before any training-return evaluation

Per-market CLOB `/clob-markets/{condition}.fd` parameters and contemporaneous Gamma `feeSchedule` agree in 37/44 markets; the installed SDK uses fd.r/e. The legacy base_fee endpoint is not the effective-fee model. Candidate selection now uses only markets with matching explicit effective rate/exponent, plus the unchanged adverse execution reserve. Retain the originally registered 0.10-rate stress results alongside actual-parameter returns; stress is a sensitivity scenario, not an actual-fee claim or a requirement that ignores available effective parameters. Unknown-fee markets are ineligible for selection. Parameters were captured during the run and before training returns were inspected. Live fee changes/rounding must still be rechecked for confirmation.
