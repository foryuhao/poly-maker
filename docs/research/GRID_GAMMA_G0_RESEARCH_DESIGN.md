# BTC5m Grid Gamma Opportunity Study — G0 Research Design

Status: Fixed, read-only research protocol; 2026-10-04.
Git base: `m4a-a1-pass` (`97ab0f9`), with unrelated pre-existing uncommitted work preserved.

## Question and scope

Does the BTC5m YES-price path itself provide enough 5¢ alternating grid crossings to support BUY-only synthetic complete-set opportunity before execution frictions? This is a price-path study, not a strategy backtest. It does not place or simulate orders, fills, inventory, capital, settlement PnL, or policy changes. Grid step is fixed at 0.05; no other step is evaluated.

## Frozen evidence

Use exactly the 32 distinct sessions marked `execution_replay_usable=True` in `research_outputs/execution_replay_audit_20261003/dataset_audit.csv`. Read their local `research_data/m4a/<session>/events.jsonl` and `manifest.json`. Match market, condition and YES/NO token IDs to the already saved official resolution labels in `research_outputs/r0_rolling_pair_replay_20261003/settlement_labels.csv`. Resolution is an identity check only; neither winner nor outcome enters crossing or edge arithmetic. No network or outside price feed is used.

## Causal price and crossing definitions

Replay YES `BOOK`, `REST_BOOK`, and `DELTA` events in saved order at `available_at_us`, restricted to market start through just before start+300s. Reuse the historical replay's `Book` reconstruction and its valid-book predicate (seen, nonempty bid/ask, bid<ask, positive tick). At each valid YES book event, `YES_mid=(best_bid+best_ask)/2`, in 1e-4 price units. Trades never substitute for mid. A transition from previous valid mid `a` to new valid mid `b` emits each fixed level 0.05–0.95 satisfying `a<L<=b` as UP, in increasing level order, or `b<=L<a` as DOWN, in decreasing order. This handles multi-level jumps in traversal order. Same-side book updates cannot emit a crossing. No cooldown or smoothing is applied.

An invalid YES book terminates the current valid-book segment. The previous mid is cleared and any open candidate is marked `CENSORED_INVALID_BOOK`; a later valid mid begins a fresh segment. This prevents an unobserved invalid interval from being treated as a known path or paired across an unknown move. The first valid mid is an anchor, not a crossing.

## Deterministic theoretical pairing

Each market has one `open_crossing`. Empty state stores the crossing as a candidate. A further same-direction crossing supersedes the candidate only when it is more extreme: lower DOWN level or higher UP level. An opposite-direction crossing forms one pair, then starts the next rolling candidate. DOWN maps to theoretical YES price at its grid level; UP maps to theoretical NO price `1−level`. `pair_cost=yes_price+no_price`; `pair_edge=1−pair_cost`. Every pair stores first/second timestamps and elapsed seconds, holding duration, levels and prices. Positive/zero/negative edges are counted separately. Same-level reversals produce zero edge and are included in alternating-pair counts. Candidate records are path-state accounting, not orders or independent trades.

## Denominators and timing

`active_seconds` sums observed valid YES-book intervals between successive YES book events within the market window; time after the final event and invalid intervals are excluded. `GridCrossingRate=crossings/active_seconds`; `TheoreticalGridEdgePerMinute=sum(pair_edge)/(active_seconds/60)`. The 30-second bucket table includes total valid-book seconds, contributing market count, raw crossings, pair initiations/completions, active-minute rates, and edge distribution. A pair is initiated in its first-crossing bucket and completed in its second-crossing bucket.

The entry-time completion table groups candidate starts into 0–150, 150–180, 180–210, 210–240, 240–270 and 270–300 seconds. Completion rate is completed candidates divided by all candidates started in the bucket. Same-direction supersession, invalid-book censoring, and final-open candidates are shown separately; the rate is therefore a conservative observed-candidate rate, not a survival-adjusted probability. Market-end `unpaired_final_crossing` counts only a candidate that remains open at the end of the observed stream. Invalid-book-censored candidates are a separate exposure/observability risk.

## Interpretation and decision

A material 5¢ edge requires a positive median pair edge and at least half of the markets with a positive-edge pair, rather than merely a high count of same-level reversals. If either gate fails, classify `NO MATERIAL GRID EDGE`. Otherwise assess turnover and timing against the task's other named classifications. This is a descriptive interpretation gate, not a tuned trading parameter or execution recommendation. No live authority follows from a positive path result.

## Reproducibility and artifacts

Run `python3 research_tools/grid_gamma_opportunity_g0.py` from the repository. Output is local under `research_outputs/grid_gamma_g0_20261004/`: `summary.json`, `crossings.csv`, `theoretical_pairs.csv`, `market_summary.csv`, `time_buckets.csv`, and `completion_by_entry_time.csv`. The durable interpretation is `docs/research/GRID_GAMMA_G0_20261004.md`. Raw captures are read only.
