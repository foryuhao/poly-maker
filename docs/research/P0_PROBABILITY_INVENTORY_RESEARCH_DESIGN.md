# P0 概率跟随 BUY ONLY 动态库存研究设计

Status: fixed V0; independent research, no live authority. Date: 2026-10-04. Git base: `m4a-a1-pass` (`97ab0f9`); pre-existing working-tree changes are preserved.

## Question and sample

Test whether the stated inventory mapping has positive total settlement PnL under three fixed execution assumptions. Select the earliest 30 unique markets by five-minute opening epoch from the locally saved `execution_replay_usable=True` rows in `research_outputs/execution_replay_audit_20261003/dataset_audit.csv`. Do not replace any selected market. Source captures: `research_data/m4a/<session>/events.jsonl` and `manifest.json`. Official winner: locally saved, identity-checked `research_outputs/r0_rolling_pair_replay_20261003/settlement_labels.csv`. No external request is permitted.

## Frozen policy

At a causal `available_at_us` book update, both token books must have bids below asks and both last updates must be at most two seconds old. The calculation uses `midY`, `midN`, and `p=(midY+1-midN)/2`. A targets `10(p−0.5)` shares of net YES; B/C target `50(p−0.5)` and cap net inventory at ±25. A buys the exact imbalance at p or 1−p with no fees, spread, latency, or cash limit. B buys only if the absolute imbalance is at least 5 shares, but buys the exact imbalance instead of rounding upward; its prices and other ideal conditions match A. C uses B's target and threshold, $100 initial cash, a 500 ms buy latency, and one outstanding order. It submits a price-limited BUY at submit-time best ask plus one observed tick, capped below $1. The arriving order uses the most recent causal depth at its own due time and fills up to that limit. Remaining quantity expires as a partial-fill-or-kill assumption. Both sides are BUY only. No fresh order can arrive in the final 15 seconds.

After each buy, equal YES/NO inventory is merged; the $1 per pair cash is reusable after 60 seconds in C. Residual winning shares redeem at market end plus 15 minutes; these are timing assumptions. A/B credit merge immediately and have unrestricted cash. All three modes use the same official outcome only at settlement. Rebates are zero. C uses a 7% fee-rate model, exponent 1, i.e. `fee = shares × 0.07 × price × (1−price)`, recorded separately from purchase spend. The saved official market metadata confirms this schedule and minimum 5 shares for the earliest 20 markets. The remaining 10 have saved outcome labels but no saved fee/minimum metadata snapshot; C applies the 20-market values as an explicit assumption. The public data do not prove actual exchange acceptance or exact fill receipts for hypothetical orders.

C stops creating new orders when cumulative settled/locked PnL plus current conservative residual valuation (zero payout) is at most −$20. An existing order can fill after the threshold is crossed; settlement can exceed the threshold. No borrowed cash or unconfirmed merge/redemption proceeds are available.

## Accounting and acceptance

Each market has gross buy spend, fees, merge income, and final residual settlement income. `TotalPnL = merge income + residual income − gross spend − fees`. Average含费 cost is assigned to merged and remaining shares solely for Pair/Residual explanation. Pair and Residual must sum to TotalPnL. Report positive and negative paths, skip reasons, quantity constraints, cash constraints, incomplete depth, and official-label provenance. A/B are mathematical diagnostics, not executable simulations. C is a conditional execution replay; it does not prove fills or future profitability. Preserve all sample markets, including zeros. No parameter optimization or new signal is authorized.

## Reproduction

`python3 research_tools/p0_probability_inventory.py` reads only the listed local files and writes local, untracked artifacts to `research_outputs/p0_probability_inventory/`. `params.json`, `sample_coverage.csv`, `ledger.csv`, `market_results.csv`, and `summary.json` are the outputs. Tests: `.venv/bin/python -m pytest -q tests/test_p0_probability_inventory.py`.
