# DP-M0 — maker fill model replication on Polymarket BTC5m

Status: **INCONCLUSIVE for own-order fill; weak directional historical execution-opportunity signal.**
Research date: 2026-10-03. Repository base: `97ab0f9` (`m4a-a1-pass`) plus pre-existing unreviewed working-tree changes. This research did not change trading behavior, live architecture, or external interfaces. The existing M4A acceptance gate remains blocked; see `docs/CURRENT_STATUS.md` and `docs/reports/M4A_ACCEPTANCE_PREFLIGHT.md`. No independent DP-M0 review or checkpoint has occurred.

Question: Does the published relation between top-of-book queue sizes and maker fills appear in BTC5m outcome-token markets? The present data can test a **public execution-opportunity proxy**, not the paper's actual own-order fill outcome. No price-prediction, rebate, entry, pairing policy, or profitability claim follows from this report.

## A. Selected published model

**Source.** Jakob Albers, Mihai Cucuringu, Sam Howison, and Alexander Y. Shestopaloff, *The Market Maker's Dilemma: Navigating the Fill Probability vs. Post-Fill Returns Trade-Off*, 2025 arXiv preprint, §1.2, §2, §4. [Original paper](https://arxiv.org/pdf/2502.18625). It is a public preprint, not evidence of peer review. The fill-frequency method is an empirical baseline, not a fitted production probability model.

**What the paper actually did.** On Binance's USDT-margined BTC perpetual, the authors placed minimum-size live maker orders at the best bid and ask without a trading signal. The principal continuous-quoting experiment ran 12–19 February 2024: 232,897 orders, 127,051 filled, 105,846 canceled. A separate periodic mode placed orders every ten seconds in August 2024; the paper says its results were essentially identical but reports the continuous mode. An unfilled order was canceled when its price ceased to be at the touch. The dataset contained actual order price, submission time, and fill/cancel terminal state. This actual-order design is central to the paper's methodology. [Paper §2](https://arxiv.org/pdf/2502.18625).

For a **buy** maker order, `near` is the best **bid** and `opp` the best **ask**; for a sell order they reverse. `Q_near` and `Q_opp` are the total displayed sizes at those two top price levels, reported in USD in Figure 4. Initial liquidity ahead `LA_t0(o)` is the sum of quantities of orders preceding order `o` in its near-side FIFO queue. For a newly appended order at the touch, the paper equates `LA_t0(o)` to the pre-existing `Q_near`. The paper defines global book imbalance `(B-A)/(B+A)` from best-bid size `B` and best-ask size `A`; its side-oriented regression uses `(Q_near-Q_opp)/(Q_near+Q_opp)`. These are distinct when the maker order is a sell. [Paper §1.2 and §4](https://arxiv.org/pdf/2502.18625).

The empirical output is `P(actual order fills before its touch-exit cancellation | Q_near at submission, Q_opp at submission)`. Each queue size is divided into **20 equal-width bins** from its observed minimum to 99th percentile, with larger values placed in the last bin. The paper computes observed fill fractions in the resulting 20×20 grid, interpolates the surface for Figure 4, and fits OLS to the **smoothed binned surface** using normalized queue sizes and side-oriented imbalance. Its reported surface fit is `R²=0.946`; this is not an out-of-sample order-level predictive score. The paper reports fill frequency decreasing with `Q_near` and increasing with `Q_opp`, with roughly 30% in one large-near/small-opp region and above 90% in one small-near/large-opp region. [Paper §4](https://arxiv.org/pdf/2502.18625).

**Our interpretation.** The two raw queue sizes, rather than imbalance alone, are the smallest faithful submission-state representation. The paper's later intermediate-time queue-position analysis is not needed for this first replication and cannot be copied from L2 without additional assumptions. The paper did **not** evaluate the fixed 1/3/5/10/20-second horizons requested here; those are DP-M0 adaptations and must be labeled as such.

## B. Why this baseline fits DP-M0

Both settings have displayed best prices and aggregate near/opposite queues, and the relevant DP second-leg candidate is a passive **buy at the outcome token's best bid**. Both can ask whether a shorter queue ahead and a larger opposing queue are associated with a higher chance of near-term execution. The paper uses real posted orders to avoid unknown placement/cancel policies of other traders; that choice directly identifies the evidence missing from synthetic BTC5m states. [Paper §2–4](https://arxiv.org/pdf/2502.18625).

Transfer is constrained by four concrete differences. Binance BTC perpetual is a single, liquid, continuing instrument; BTC5m has two bounded outcome tokens and a five-minute market window. Polymarket can match a YES buy with a complementary NO buy, so a same-token sell print is not the only observable route to a bid-side fill. [Polymarket prices and orderbook](https://docs.polymarket.com/concepts/prices-orderbook). The captured YES and NO books exhibit mirrored complementary levels, so token sides from one market are not independent experiments. Finally, the paper's actual fill/cancel labels and FIFO queue-ahead measurement do not exist for hypothetical orders in the historical capture. These differences prevent literal replication of the paper's fill probability from these files.

## C. Exact variable mapping

| Published variable | Original definition | BTC5m mapping | Available now? | Exact / proxy / missing |
|---|---|---|---|---|
| Order `o`, side | Actual minimum-sized top-bid buy or top-ask sell, with submission and exchange order identity | Hypothetical post-only **BUY** at YES or NO best bid; actual live orders have IDs in order events | Hypothetical candidates yes; actual selected orders yes | Candidate proxy; actual orders exact only for observed placements |
| `t0(o)` | Actual order submission time | Candidate local `available_at_us` sampling time; own requests/ACKs have local times | Yes | Proxy for hypothetical exchange insertion |
| `p_near`, `p_opp` | Best price on order side and opposite side at submission | Selected token best bid and best ask, separately reconstructed | Yes | L2 exact as received; causal alignment subject to feed latency |
| `Q_near`, `Q_opp` | Displayed quantity at those best price levels; Figure 4 reports USD | Displayed share size × its own level price in USDC; raw share size also retained | Yes | Notional mapping proxy; venue unit differs |
| `LA_t0(o)` | Sum of earlier individual orders at order's price; equals initial displayed near queue for a new back-of-queue order | Pre-placement aggregate top-bid size, assuming candidate joins behind all displayed liquidity | Yes as L2 | Initial queue-ahead proxy; insertion/latency unknown |
| `imb` | Global `(B-A)/(B+A)`; regression's order-oriented `(Q_near-Q_opp)/(Q_near+Q_opp)` | Compute the same definitions on one selected token; for BUY, near is bid | Yes | Exact algebra on L2 proxy sizes; do not substitute imbalance for both queue sizes |
| `T(o)` and terminal state | Actual full fill or cancellation after price ceases to be top-of-book | First subsequent top-bid move can be reconstructed; own fill/cancel exists only for actual selected orders | Partial | Missing for hypothetical own fill |
| Fill indicator | Actual submitted order filled before touch-exit cancellation | Public trade at/through candidate bid via same-token SELL **or** complementary-token BUY, before candidate bid moves | Yes as historical opportunity | Proxy only; no FIFO rank, placement, exact trade-to-order ID or guaranteed execution |
| Queue position after placement | Individual liquidity ahead/behind and relative position | L2 level totals only | No | Missing; not part of selected submission-state baseline |

The official market stream describes `book`, `price_change`, and `last_trade_price` messages with token, price, size, side, and timestamps. It does not provide individual order priority in those public messages. [Polymarket real-time data](https://docs.polymarket.com/market-data/realtime-data). The public trade-to-own-fill equivalence is an **inference** and requires validation, especially across complementary matching.

## D. Current data audit

Read-only audit of the **persisted files**, not just runtime support:

| Item | Observed state |
|---|---|
| Research capture | `research_data/m4a/`: 30 sessions, 26 distinct BTC5m slugs, 5,751,850 manifest-recorded events. Ten sessions ended before the named market began or were very short. |
| Pilot-usable market set | 20 distinct long sessions, each `manifest.close_status=COMPLETE`, zero manifest gap/lost counts, and at least 50,000 events; 5,750,067 manifest-recorded events. Named market starts span 2026-10-02 10:25–18:45 UTC, non-contiguously. Session coverage includes the five-minute market and runs roughly to T+300. This is a pilot inclusion rule, not M4A engineering acceptance. |
| Stored book evidence | `events.jsonl` contains token-specific `BOOK` snapshots with full price/size levels and hashes, `DELTA` price-level updates, occasional `REST_BOOK` replacements and `TICK` events. Both token IDs are in each manifest; both books produced samples in every usable market. `states.jsonl` exists but is often empty when no first-fill episode; it is not the main book history. |
| Stored trade evidence | `TRADE` events carry token, side, price, size, exchange time, local receive time and local available time. Public research payload has no order-level FIFO position or reliable own-order linkage. |
| Clock quality | Capture records microsecond-valued exchange, receive, and available timestamps plus sequence/generation. In the audited market windows, median receive-minus-exchange delay by market ranged about **90–1,052 ms**. Thus 1-second exchange-time interpretation is fragile; local-availability-time horizons are measurable but represent what the recorder saw. 3 seconds remains a sensitivity horizon; emphasize 5/10/20 seconds. |
| Own-order evidence | Actual `ORDER_SUBMIT`, `ORDER_ACK`, `ORDER_STATE`, `CANCEL_REQUEST`, `CANCEL_ACK`, and accepted `FILL_REVISION` rows contain request/order IDs, quote price/size, fill quantity, role and fill status. An inspected fill revision identifies `USER_WS` as its source and retains exchange, receive and available times. Partial fills and status revisions exist. Across the 20 long sessions, **27 distinct accepted own maker fill keys** occur in 11 markets, including first and pair roles; these are strategy-selected orders, not random probe orders. |
| Market identity/time | Manifest contains condition ID, market slug, YES/NO IDs and session bounds; corresponding `livecfg/logs/runs/*/run.json` has selected market start/end and actual order/fill summaries. The slug epoch was checked against a run report. |
| Reconstruction limits | Manifest gap count is zero in the included sessions, but this does not certify exact L2 reconstruction. Reconnects, REST replacements, mirrored YES/NO liquidity, source-message ordering and the blocked M4A acceptance preflight require care. `quality.json` still says `FINALIZING` in inspected completed sessions. |

An own-fill/public-print spot audit used the 27 distinct accepted fill keys and exchange times. Within ±1 second, 7 had a same-token `SELL` print at/through the fill price and 25 had a complementary-token `BUY` print at/through the complementary price; 26 had either. These are **price/time coincidences**, not matched trade identities. One actual first fill had no public print of either kind within ±1 second. A same-token-only label demonstrably misses plausible fill routes. The audit does not prove the dual-route public label is complete. [Official complementary matching example](https://docs.polymarket.com/concepts/prices-orderbook).

Audit commands/scripts: `python3 research_tools/dpm0_pilot.py` and `python3 research_tools/dpm0_fill_audit.py`. Both read only local persisted files. The scripts contain the exact exploratory inclusion, sampling, clock, reconstruction, and label rules used here. No external market data were queried.

## E. Can M0 start now?

**YES — for a historical execution-opportunity pilot.** The 20 long sessions contain enough top-book states and future public market events to test direction and stability. **NO — for a literal own-order-fill replication** of the paper from hypothetical states. The 27 actual own fills are selected by FavoriteFirst and do not form the paper's signal-agnostic placement experiment. More historical L2 volume alone cannot reveal a hypothetical FIFO fill.

## F. Replication experiment and completed pilot

**Closest feasible experiment.** Reconstruct each actual-token L2 book in recorded availability order from `BOOK`, `DELTA`, and `REST_BOOK`, without manufacturing exchange-time foresight. Restrict to the named market's T+30 through T+260 and sample both token bids every ten seconds. Require valid spread and both token books mutated in the prior two seconds. This yielded **736 candidate states** (368 YES, 368 NO) in 20 markets; 224 token-time opportunities were excluded by the two-second freshness rule. Treat each market as one cluster. A candidate uses `Q_near=best_bid_price×bid_shares` and `Q_opp=best_ask_price×ask_shares` in USDC and the published imbalance definition. Candidate price is the actual best bid, not a selected strategy quote.

The **primary historical proxy** is at least one public trade at/through that bid via selected-token `SELL` or complementary-token `BUY`, observed after the candidate and **before the first change away from its best bid**. Its five requested windows are 1, 3, 5, 10, and 20 seconds in local available time. The weaker broad sensitivity label ignores that first bid change. Neither requires traded size to pass liquidity ahead plus proposed order size; neither asserts an own-order fill. Book-level depletion and crossed-through prices are available as separate diagnostics, but should not silently replace this stated target. A confirmed queue-consumption label would need a proven deduplicated trade identity/volume mapping.

First reproduce the paper's 20 equal-width bins up to development-set 99th percentiles. In this pilot they are too sparse for a defensible smoothed surface or OLS: **102/400** development and **60/400** validation joint cells are occupied, with median occupied-cell counts **1.5 and 1**. No paper-like smooth surface or fitted probability claim is reported. For an inspectable direction check, split whole markets chronologically into 13 development and 7 validation markets and use **development medians only** (`Q_near=108.602` USDC, `Q_opp=101.997` USDC) to contrast low-near/high-opp with high-near/low-opp states. This two-group compression is an explicitly disclosed pilot adaptation, not the paper's 20×20 estimator. YES/NO within one market are correlated.

| Historical opportunity proxy | Development favorable / unfavorable | Validation favorable / unfavorable | Interpretation |
|---|---:|---:|---|
| Strict, 1 s | 41.2% / 43.1% (`n=102/102`) | 46.8% / 42.9% (`n=79/77`) | Too sensitive to subsecond feed timing. |
| Strict, 3 s | 54.9% / 49.0% | 54.4% / 46.8% | Directional but modest. |
| Strict, 5, 10, 20 s | 54.9% / 50.0% at 5 s; same at 10/20 s | 54.4% / 46.8%; same at 10/20 s | Most qualifying prints occur before the bid first moves; no additional horizon resolution. |
| Broad, 5 s | 95.1% / 65.7% (`n=102/102`) | 93.7% / 71.4% (`n=79/77`) | Larger separation, but counts trades after touch exit and saturates; unsuitable as fill probability. |

On the strict 5-second label, held-out YES was **60.5% vs 53.8%** (`38/39` states), and NO **48.8% vs 39.5%** (`41/38` states), in expected direction. These are descriptive frequencies from overlapping observations, not independent Bernoulli trials. The broad label's stronger contrast and the original same-token-only label's instability show that the **historical label definition is material**. The shares-versus-USDC unit choice was also material in sensitivity work; the table above uses the paper's dollar-denominated form. Do not reuse these frequencies as an actual order-fill model.

The minimum next statistical output after label validation is the paper's occupancy-aware 20×20 frequency grid (with raw counts, never empty-cell interpolation presented as evidence), one strict-label frequency table for each horizon and token, market-level paired contrasts with a market bootstrap interval, and a plot of market-specific contrasts. Preserve the 13/7 whole-market split; no row-random split and no ML model.

## G. M0 pass/fail interpretation

1. **Strong replication:** after an opportunity label has been validated against own orders, low near and high opposite size show large, monotone, same-direction differences in market-level results for both tokens and both development/held-out markets. This would support the paper's two-queue baseline for later time-to-fill work, not profitability.
2. **Weak but directional:** the current strict public-opportunity result fits this description. It has positive ordering in the held-out aggregate and each token, but modest separation, sparse original cells, and no proven mapping to actual fills. Status remains **INCONCLUSIVE** for the original fill question.
3. **No replication:** a validated label and adequate event quality show no stable ordering. Then classify whether the cause is data (A), variable mapping (B), label (C), market microstructure (D), sample (E), or genuine non-transfer (F); do not invent replacement features.
4. **Contradictory result:** robust sign reversal across markets/tokens after exact variable and label checks would argue against transfer. A reversal only under a changed proxy is first a label/microstructure warning, not evidence of a new trading signal.

Current failure classification: **C (label) and D (complementary matching mechanics) are unresolved; B (share versus dollar depth) is material; A (clock and acceptance quality) remains a limitation.** The present pilot does not identify F. The roughly 20 markets are adequate for a directional pilot and are not the reason to reject it.

Handoff status: source-paper review, persisted-file audit, read-only pilot and label/unit sensitivities are complete. Exact own-fill replication, validated public-to-own fill matching, the paper's populated 20×20 surface, and independent review remain incomplete. This research is unreviewed and uncheckpointed; the existing code predecessor remains `m4a-a1-pass`. M4A live collection and trading controls are outside DP-M0.

## H. Minimal next step

**Reconcile the existing 27 accepted own fills, their order IDs, and the simultaneous YES/NO public trade and L2 sequences to validate one historical opportunity label.** Use only the already captured Polymarket-official data. Until that single check establishes how public events map to actual execution, DP-M0 should not declare the paper's own-order fill relation replicated or start a new model.
