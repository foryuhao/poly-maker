# DP-M0B2 — Pair edge slack at authenticated FIRST fills

**Decision: THIN SLACK.** This cross-sectional study uses actual own FIRST fills and opposite-token L2 book states. It does not reuse M0A public events as hypothetical fill labels. The M0B historical fill-proxy verdict remains **FAIL**. No order, live API call, trading code, or future price path was involved.

Research date: 2026-10-03. Repository base: `97ab0f9` (`m4a-a1-pass`) with pre-existing unreviewed working-tree changes. This research is unreviewed and uncheckpointed. Reproduce from the repository root with `python3 research_tools/dpm0_fill_audit.py` followed by `python3 research_tools/dpm0b2_edge_slack.py`. The second command uses the authenticated fill inventory in `research_outputs/dpm0b/fills.csv` and the original `research_data/m4a/` event captures.

## 1. Sample and ground truth

The M0B audit found 27 accepted own fill events. Only the **12 `FIRST` fill events** enter this study: 11 unique orders in 11 BTC5m markets, 3 YES and 9 NO. Ten events filled 5 shares each. One NO first-leg order filled in two authenticated events of 0.5 and 4.5 shares, which remain separate observations. All 12 have an accepted `MATCHED` revision originating from `USER_WS` and a final accepted `CONFIRMED` revision. Their trade ID and maker order ID identify the fill-event grain; exchange fill time, User WS receipt time, ACK time, actual fill price and size are retained in the CSV.

The two partial events use `paired_quantity = min(fill_event_size, 5)` for the immediately executable paired-quantity comparison and for category A. The requested `P_taker_5` and `Edge_taker_5` are also calculated for them as **book benchmarks**, but they do not represent a five-share matched pair from a 0.5- or 4.5-share fill. These observations share an order and market, so event percentages are descriptive, not independent estimates.

## 2. Causal reconstruction and data quality

For each FIRST fill, the script replays only the **complementary token's** official public `BOOK` and `DELTA` events with `exchange_at_us <= authenticated fill exchange_at_us` **and** `available_at_us <= own User WS receipt time`. It sorts those selected events by exchange time and event sequence, starts from full `BOOK` snapshots, then applies price-level deltas. A `REST_BOOK` or `TICK` without exchange time is never inserted into exchange-time replay. The reported tick comes from the last included exchange-stamped `BOOK`. Full reconstructed opposite bid and ask ladders are saved as JSON columns in each CSV row.

This is an **exchange-time-bounded L2 approximation**, not an exact matching-engine book. Exchange timestamps are millisecond precision; two partial fills have an opposite-book event with the same exchange millisecond as the fill, so within-millisecond order is unknown. Replaying strictly earlier book events gives the same bid, ask, passive edge and taker edge for both. One fill uses a last exchange-stamped book event **7.910 seconds** before the fill, received only 48 ms before own User WS receipt; that row is stale and should not support precise contemporaneous claims. The other nine rows have a strictly earlier last book event within 206 ms. All 12 rows have a reconstructable positive-depth bid, ask and book-sourced tick; none had an intervening untimed REST replacement or tick message after the last selected snapshot. Capture manifests report zero gaps/lost events for these sessions. No independent exchange/local clock offset estimate exists.

| Reconstruction quality | Fill events | Interpretation |
|---|---:|---|
| Exchange-bounded WS L2, earlier event within 1 s | 9 | Best available causal book proxy; still aggregated L2. |
| Same-millisecond exchange ordering ambiguous | 2 | Strictly earlier replay gives the same edge result. |
| Last opposite event more than 1 s old | 1 | Retained and separately flagged; no claim of live freshness. |

The one-second quality screen is a diagnostic freshness flag, not a tuned edge threshold. The decision below is the same for all 12 rows and the nine-row cleaner subset.

## 3. Prices, edge and category rules

For each actual first fill, `P1 = authenticated fill price`, `PairCost = P1 + P2`, and `PairEdge = 1 - PairCost`. `P_taker_5` is the ask-side displayed-depth VWAP to buy exactly 5 complementary shares; `P_taker_paired` uses the actual paired quantity. In all 12 reconstructed books, displayed size at the best ask exceeds 5 shares, so both VWAPs equal the best ask. Displayed depth is an L2 quote, not a guarantee it remains available when an order reaches the exchange.

Maker candidates use the opposite book's recorded 0.01 tick. A candidate is reported only on the tick grid and strictly below the best ask. `max_positive_edge_ticks`, `max_1c_edge_ticks`, and `max_2c_edge_ticks` count the greatest **nonnegative** integer tick move from best bid toward, but not through, the ask while preserving `>0`, `>=0.01`, or `>=0.02` PairEdge respectively. Zero means only the current bid qualifies. Blank means no quote at or above the current bid qualifies.

**All 12 opposite books have a one-tick spread.** Consequently `bid + 1 tick` and `bid + 2 ticks` would cross or reach the ask and are NA as maker quotes. `ask - 1 tick` equals the current best bid, while `ask - 2 ticks` is **one tick behind the best bid**. Its larger edge is gained by quoting less aggressively and must not be interpreted as execution-priority slack. Category A requires positive paired-quantity taker edge. B requires positive edge at a valid maker quote **strictly above** the current best bid. C requires positive edge at or behind the best bid when A/B are false. D has no positive edge at any of these reasonable maker candidates. The three zero-edge-at-bid observations fall in C because one tick behind the bid retains one cent of edge.

## 4. Per-fill index

The complete [one-row-per-fill CSV](../../research_outputs/dpm0b2/edge_slack.csv) contains all requested IDs, timestamps, best-level sizes, tick, price ladder, edge ladder, raw L2 levels, slack counts, category, quality and notes. This compact index uses the market-start epoch and shortened trade ID only for display.

| Market epoch | FIRST outcome | Fill ID | Size | P1 | Opp bid / ask | Edge bid | Edge ask−1 | Edge ask−2 | Edge taker 5 | Quality |
|---:|---|---|---:|---:|---|---:|---:|---:|---:|---|
| 1790937900 | NO | `b8f56720…` | 5 | .63 | .36 / .37 | .01 | .01 | .02 | .00 | stale 7.910 s |
| 1790938500 | YES | `50ce526f…` | 5 | .69 | .30 / .31 | .01 | .01 | .02 | .00 | bounded |
| 1790940000 | YES | `0d19e3e0…` | 5 | .65 | .34 / .35 | .01 | .01 | .02 | .00 | bounded |
| 1790940600 | NO | `bfcb6e27…` | 5 | .62 | .37 / .38 | .01 | .01 | .02 | .00 | bounded |
| 1790941200 | NO | `a8117fef…` | 5 | .61 | .38 / .39 | .01 | .01 | .02 | .00 | bounded |
| 1790942400 | NO | `74df0de2…` | .5 | .64 | .36 / .37 | .00 | .00 | .01 | −.01 | same ms |
| 1790942400 | NO | `7ce9e702…` | 4.5 | .64 | .36 / .37 | .00 | .00 | .01 | −.01 | same ms |
| 1790943000 | YES | `b0353056…` | 5 | .62 | .37 / .38 | .01 | .01 | .02 | .00 | bounded |
| 1790961300 | NO | `f0299204…` | 5 | .88 | .11 / .12 | .01 | .01 | .02 | .00 | bounded |
| 1790961900 | NO | `3dbf7b37…` | 5 | .89 | .10 / .11 | .01 | .01 | .02 | .00 | bounded |
| 1790962500 | NO | `a95e0ff5…` | 5 | .87 | .12 / .13 | .01 | .01 | .02 | .00 | bounded |
| 1790966100 | NO | `cd72af1c…` | 5 | .87 | .13 / .14 | .00 | .00 | .01 | −.01 | bounded |

## 5. Edge ladder and opportunity statistics

All percentages below use the 12 FIRST fill events as the denominator. NA means no legal strictly noncrossing maker quote at that candidate price. Dollar values are PairEdge per one YES+NO share pair, before excluded add-ons.

| Candidate | Valid / 12 | Positive / 12 | Required threshold result | p25 / median / p75 edge |
|---|---:|---:|---|---|
| Opposite best bid | 12 | 9 (75.0%) | — | .0075 / .01 / .01 |
| Bid + 1 tick | 0 | NA | Reaches best ask | NA |
| Bid + 2 ticks | 0 | NA | Crosses best ask | NA |
| Ask − 2 ticks | 12 | 12 (100%) | **Behind best bid** | .0175 / .02 / .02 |
| Ask − 1 tick | 12 | 9 (75.0%) | ≥ .01: 9 (75.0%); ≥ .02: 0 | .0075 / .01 / .01 |
| Immediate displayed 5-share VWAP | 12 | 0 | ≥ .01: 0 | −.0025 / .00 / .00 |

The remaining requested positive checks are: `Edge_ask_minus_2 > 0`: **12/12**; `Edge_bid_plus_1 > 0`: **NA (0 valid)**; `Edge_bid_plus_2 > 0`: **NA (0 valid)**; `Edge_bid > 0`: **9/12**; `Edge_taker_5 > 0`: **0/12**. For the two partial fills, paired-quantity immediate edge equals the displayed 5-share benchmark because best-ask size exceeds 5, but their paired quantity is only 0.5 or 4.5 shares.

YES FIRST fills (n=3) have median best-bid/ask−1 edge **.01**, ask−2 edge **.02**, and taker-5 edge **.00**. NO FIRST fills (n=9) have the same medians, with best-bid edge positive in **6/9** versus **3/3** YES; this split is too small for a stable cross-market inference.

**Execution-priority slack:** Among nine fills with positive edge at the best bid, `max_positive_edge_ticks = 0` and `max_1c_edge_ticks = 0` for every event; the other three are blank because even the current best bid has zero edge. `max_2c_edge_ticks` is blank for all 12. The three blank positive-slack rows become positive only by moving **one tick behind** the bid. The nine-row cleaner subset still has 0 immediately positive pairs, 0 above-bid maker ticks, and category C for all nine.

| Category | Fills | Share |
|---|---:|---:|
| A — immediately executable positive pair | 0 | 0% |
| B — aggressive above-bid maker positive pair | 0 | 0% |
| C — passive/thin maker pair | 12 | 100% |
| D — no positive pair edge at reasonable maker prices | 0 | 0% |

## 6. Decision and handoff

**THIN SLACK.** The observed structural gross edge is generally one cent at the opposite best bid and disappears at the best ask. Every spread is one tick, leaving no legal inside-spread maker quote that can buy priority while retaining positive edge. The apparent two-cent `ask−2` edge comes from stepping *behind* the best bid. This is a small, strategy-selected, nonindependent pilot with one stale book and two same-millisecond ordering ambiguities, but those caveats do not change the one-tick-spread/no-aggressive-slack finding in the cleaner subset. This result does not establish future execution, realized Pair PnL, or profitability; M0B's historical-fill-proxy failure remains in force.

Delivered research-only script and [machine-readable summary](../../research_outputs/dpm0b2/summary.json). No production or live behavior changed. No checkpoint or independent review was performed. Canonical reviewed repository baseline remains `m4a-a1-pass`; existing unrelated unreviewed working-tree changes were left untouched.

**Next action:** Collect the minimum 5-share Maker Probe dataset with authenticated direct fill labels, under separate live-trading authorization.
