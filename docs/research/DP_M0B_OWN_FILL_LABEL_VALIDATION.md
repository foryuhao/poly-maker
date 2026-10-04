# DP-M0B — own-fill label validation

**Verdict: FAIL.** The existing five-second strict M0A historical opportunity proxy is not a reliable stand-in for actual own-order fills. This is a label-validation result only; no trading code, market feed, strategy, or live order was changed.

Research date: 2026-10-03. Repository base: `97ab0f9` (`m4a-a1-pass`) with pre-existing unreviewed working-tree changes. The DP-M0A research document and scripts were already untracked in this workspace. This research is unreviewed and uncheckpointed. Source methodology: `docs/research/DP_M0_MAKER_FILL_RESEARCH_DESIGN.md`. Reproduce with `python3 research_tools/dpm0_fill_audit.py` from the repository root.

## 1. Ground-truth sample and scope

The script read the same 20 long `research_data/m4a/` sessions used in M0A: complete manifest, zero reported gaps/lost events, and at least 50,000 events. It made no API call. `ORDER_SUBMIT` request IDs were joined to `ORDER_ACK` order IDs; all 74 ACK-to-quote mappings were one-to-one. A fill key is `(trade_id, maker_order_id)`. Revisions were grouped by key; an event entered ground truth only when its final revision was accepted and not quarantined. For timing, the first accepted `MATCHED` revision was used. All 27 included fills began with `USER_WS`, ended `CONFIRMED`, and matched an ACKed order. The total 74 order records and 27 fill records agree with the corresponding run reports.

| Sample unit | Count |
|---|---:|
| Captured BTC5m markets audited | 20 |
| Markets with ACKed orders | 13 |
| ACKed maker orders | 74 (49 YES, 25 NO) |
| Orders with ≥1 accepted fill | 20 |
| Orders canceled without an accepted fill | 54 |
| Markets with an accepted fill | 11 |
| Accepted fill events | 27 (13 YES, 14 NO; 12 FIRST, 15 PAIR) |
| Fill-event records partial / completing the requested size | 10 / 17 |
| Final filled-order outcomes | 17 fully filled, 3 partially filled then canceled |

Ten partial fill-event rows represent repeated observations of a smaller number of orders. Neither the 27 fill events nor the 74 orders are independent market experiments. Some run reports say `INCOMPLETE_RECONCILIATION` even though these specific accepted fill keys reached `CONFIRMED`; that limits terminal certainty for the broader live episode, while the audited fill ground truth uses the recorded authenticated acceptance chain. All nonfill orders in this sample had a recorded cancel ACK.

## 2. Clock audit and state at acceptance

| Source | Stored time meaning | Use in this audit |
|---|---|---|
| `ORDER_SUBMIT` | Local engine available time when a submit request was emitted | Request chronology only. |
| `ORDER_ACK` | Local available time when the gateway response was observed | Candidate start and **acceptance proxy**. The exchange insertion time is not recorded. Median submit-to-ACK interval: **233.4 ms** across 74 orders. |
| First accepted own `MATCHED` `FILL_REVISION` | Exchange fill event time, local User WebSocket receipt, local available/processing time | Authenticated fill ground truth. Receive minus exchange time: median **91.3 ms**, range **87.4–400.6 ms**, across 27 fills. |
| Public `TRADE`, `BOOK`, `DELTA` | Exchange event time plus local receive/available times where persisted | Book and proxy events are replayed in **local available order**, exactly as M0A did. Across 20 markets, median public-trade receive-minus-exchange delay by market ranged **90.5–913.8 ms**. |
| `CANCEL_REQUEST` / `CANCEL_ACK` | Local engine request / response time | Terminal order evidence; neither is the exact exchange cancellation time. |

All `delta_t` figures in this report compare **public proxy available time minus first own `MATCHED` available time**, so their sign uses one clock. Exchange times are preserved separately in CSV. Clock synchronization error between local and exchange clocks was not independently measured; do not turn subsecond exchange/local differences into exact matching-engine order. Two false-positive public prints arrived after ACK locally while their recorded exchange timestamps preceded local order submission by about **2.8 and 6.4 seconds**. This is strong evidence of stale public arrival unless the clocks had an unobserved multisecond offset. The trade feed's market-level delay range makes 1-second alignment especially uncertain.

At each ACK, the latest **already available** selected-token `BOOK`/`REST_BOOK`/`DELTA` state was reconstructed. `Q_near = best_bid × displayed bid shares`, `Q_opp = best_ask × displayed ask shares`, and `imbalance = (Q_near-Q_opp)/(Q_near+Q_opp)`, exactly as M0A mapped them. All are L2 **proxies**, since the ACK can follow the order's actual insertion and displayed depth may already include our order. Individual liquidity ahead and exact exchange queue rank remain **missing**. The recorded best bid/ask and price-level size are exact values of the locally reconstructed L2 book, subject to capture latency and feed completeness. Market time remaining comes from the verified market-start slug plus 300 seconds.

Only **30/74** ACKed orders were at their selected-token best bid at local ACK. The other **44** were behind it; none were inside the spread in this reconstruction. **24/27** fill-event rows belonged to at-BBO orders, and **3/27** to behind-BBO orders. The M0A top-bid proxy is out of scope for those three behind-BBO fill events.

## 3. Proxy definitions fixed before comparison

The accepted order's `limit_price` is `p`; selected token and complementary token are those in the session manifest. Both public routes were retained because complementary YES/NO buys can match on Polymarket.

| Proxy | Exact audit definition | Role |
|---|---|---|
| A — same-level public trade | Selected-token `SELL` at `p` or complementary-token `BUY` at `1-p`, available within five seconds after ACK. | Observable print only; no queue or touch-exit test. |
| B — trade-through | Selected-token `SELL` below `p` or complementary-token `BUY` above `1-p`, available within five seconds after ACK. | Separate diagnostic; not proof our order filled. |
| C — L2 depletion | Selected-token displayed bid size at `p` first falls to zero within five seconds after ACK. | May reflect cancellation, book replacement, or execution. |
| D — **unchanged strict M0A proxy** | At least one selected-token `SELL` at/below `p` or complementary-token `BUY` at/above `1-p`, available **after ACK and before or at the first selected-token best-bid change**, within five seconds. | Primary validation target. Uses the exact M0A event ordering, routes, price inequalities, and five-second horizon. |

For A–C, the first event is not touch-exit gated; this distinction is deliberate and does not redefine D. No proxy sums public size to infer FIFO consumption. The two public routes can describe related matches, and no durable public trade ID links them to an own maker fill. `public_direct_size_before_fill_raw` and `public_complement_size_before_fill_raw` in the CSV are separate, potentially overlapping raw sums. L2 decreases are never called trades. `public_events.csv` preserves each relevant trade and L2 transition by order and timestamp, including before/after level sizes, first bid move, disappearance, and reappearance.

## 4. Fill-event comparison

The full required one-row-per-fill table is [`research_outputs/dpm0b/fills.csv`](../../research_outputs/dpm0b/fills.csv). It includes the complete market/token/order IDs, ACK time, price, size, price-level depth, `Q_near`, `Q_opp`, imbalance, BBO, first authenticated fill times and size, partial/full state, first observable proxy times, strict detection and signed delta, cancel ACK, and evidence notes. [`orders.csv`](../../research_outputs/dpm0b/orders.csv) contains one row per accepted order. The compact index below retains distinct partial fills; shortened IDs are for display only.

| # | Market start epoch | Token | Order ID | Fill ID | Qty | At BBO | Strict | Δt (s) |
|---:|---:|---|---|---|---:|---|---|---:|
| 1 | 1790937900 | NO | `0x3569bb37…` | `b8f56720…` | 5 | Y | Y | -0.869083 |
| 2 | 1790937900 | YES | `0x19199d4c…` | `2d0fa9ce…` | 1.38 | N | N | — |
| 3 | 1790937900 | YES | `0x19199d4c…` | `6f033df7…` | 3.62 | N | N | — |
| 4 | 1790938500 | YES | `0x3bbc2b53…` | `50ce526f…` | 5 | Y | Y | +0.146677 |
| 5 | 1790938500 | NO | `0x5b80d5ea…` | `81881dda…` | 1.121905 | Y | Y | -0.276947 |
| 6 | 1790938500 | NO | `0x5b80d5ea…` | `24780912…` | 1.587302 | Y | Y | -0.286213 |
| 7 | 1790938500 | NO | `0x5b80d5ea…` | `a771abe6…` | 1.587302 | Y | Y | -0.554580 |
| 8 | 1790940000 | YES | `0x300fcbfc…` | `0d19e3e0…` | 5 | Y | N | — |
| 9 | 1790940000 | NO | `0x24cefb8c…` | `256e259d…` | 5 | N | N | — |
| 10 | 1790940600 | NO | `0xea147da8…` | `bfcb6e27…` | 5 | Y | Y | -5.430601 |
| 11 | 1790940600 | YES | `0xe19f5fd4…` | `49a4c9a8…` | 5 | Y | Y | -1.431591 |
| 12 | 1790941200 | NO | `0x5b78bef1…` | `a8117fef…` | 5 | Y | Y | -0.467829 |
| 13 | 1790941200 | YES | `0xdba43fcc…` | `87f966a3…` | 5 | Y | Y | -1.093709 |
| 14 | 1790942400 | NO | `0x5a85f9ba…` | `74df0de2…` | 0.5 | Y | N | — |
| 15 | 1790942400 | NO | `0x5a85f9ba…` | `7ce9e702…` | 4.5 | Y | N | — |
| 16 | 1790943000 | YES | `0x68b4bf6a…` | `b0353056…` | 5 | Y | N | — |
| 17 | 1790943000 | NO | `0x68a7cba4…` | `6e64e760…` | 5 | Y | Y | -1.076725 |
| 18 | 1790961300 | NO | `0xac5bfa7a…` | `f0299204…` | 5 | Y | Y | -0.026689 |
| 19 | 1790961300 | YES | `0x6ac5bdf1…` | `5698512b…` | 5 | Y | Y | -0.610124 |
| 20 | 1790961900 | NO | `0x39e45f70…` | `3dbf7b37…` | 5 | Y | N | — |
| 21 | 1790961900 | YES | `0x1e728495…` | `bd70d198…` | 3.030241 | Y | Y | -0.080934 |
| 22 | 1790961900 | YES | `0x1e728495…` | `8324d69b…` | 1.96 | Y | Y | -0.606213 |
| 23 | 1790962500 | NO | `0x1d06234f…` | `a95e0ff5…` | 5 | Y | Y | -0.272818 |
| 24 | 1790962500 | YES | `0xe0dcc94a…` | `5fb114b8…` | 1.176466 | Y | Y | -1.399772 |
| 25 | 1790962500 | YES | `0xe0dcc94a…` | `f27b9acf…` | 1.176466 | Y | Y | -1.469018 |
| 26 | 1790962500 | YES | `0xe0dcc94a…` | `cc9749f7…` | 2.64 | Y | Y | -1.566212 |
| 27 | 1790966100 | NO | `0x6c1f7293…` | `cd72af1c…` | 5 | Y | Y | -0.627956 |

## 5. Summary statistics and nonfill comparison

Strict D detected **19/27** accepted fill events (**70.4% recall**, 29.6% missed). Among the 24 fill events whose order was at BBO on ACK, it detected **19/24 (79.2%)**. Of 19 detected events, **18** proxy triggers preceded the local own fill and **one** lagged it by 0.147 s. For detected events, `delta_t = proxy available − own MATCHED available` had **p25 −1.247 s, median −0.610 s, p75 −0.282 s**, range **−5.431 to +0.147 s**. No delta is assigned to misses.

An order-level confusion matrix is possible because the data also contain nonfilled accepted orders. Restricting to the **30** orders ACKed at BBO with a recorded full fill, partial-fill cancellation, or no-fill cancellation yields **18 filled** and **12 nonfilled** orders. Strict D produced **TP 14, FN 4, FP 6, TN 6**: precision **70.0%**, recall **77.8%**, specificity **50.0%**, false-positive rate **50.0%**. Every false-positive proxy trigger preceded its recorded cancel ACK. This is an observational comparison under FavoriteFirst's order placement/cancel policy, not an estimate for random hypothetical orders. A sensitivity limited to runs marked `COMPLETED` still has **3 FP among 8 nonfilled at-BBO orders**, so incomplete-reconciliation run status alone does not explain the false positives.

| Five-second observable proxy | Detected fill events / 27 | At-BBO order TP / FN / FP / TN | Interpretation |
|---|---:|---:|---|
| A: exact-level trade | 25 | 17 / 1 / 10 / 2 | Frequent public touch, including nonfill orders. |
| B: trade-through | 21 | 14 / 4 / 2 / 10 | Often appeared only **after** the actual fill; not a causal fill label by itself. |
| C: L2 level depletion | 24 | 15 / 3 / 4 / 8 | Displayed loss includes cancellation and snapshot changes. |
| D: strict M0A | 19 | 14 / 4 / 6 / 6 | Primary test; material misses and false positives. |

Across all 27 fill paths, the first A event was before/at the fill in **20** and after in **7**; the first B event was before/at in **1**, after in **25**, and absent in **1**; the first C depletion was before/at in **5** and after in **22**. Those timing counts are not interchangeable with five-second detection counts. The comparison table is diagnostic only and does not select a replacement proxy.

## 6. Miss and mismatch classification

| Evidence-backed category | Cases | What the records show |
|---|---:|---|
| G — outside top-bid scope | 3 missed fill events (2 from one order) | Order limit was behind the selected-token best bid at local ACK; M0A's top-bid target did not apply. |
| H — touch exited first | 2 missed fill events (one partial-fill order) | Selected-token best bid changed before a qualifying public print arrived locally. This is an event-order observation, not proof of exchange queue mechanics. |
| J — outside five-second window | 3 missed fill events | First qualifying public print fell outside D's fixed five-second ACK window. |
| F/H — proxy lag | 1 detected fill event | Public proxy arrived 0.147 s after the own fill was available. |
| A/F — stale public arrival | At least 2 of 6 false-positive orders | Qualifying print was locally received after ACK, but its exchange timestamp was 2.8 or 6.4 s before local submission. Exact exchange clock offset is not independently known. |
| C/D — queue/cancellation ambiguity | 6 false-positive at-BBO orders | A qualifying public print appeared before cancel ACK, yet no authenticated fill was accepted. L2 lacks individual priority; the audit cannot distinguish ahead-of-order executions from cancellations or other mechanics for each case. |

No included manifest reports a gap or lost event. Reconnects and `REST_BOOK` replacements are visible in the event audit but were not assigned as causes without a per-case causal link. The categories above describe the recorded observations; they do not imply an unobserved taker or queue event.

## 7. Verdict and handoff

**FAIL.** The unchanged strict proxy misses one in five in-scope fill-event rows and fires on half of comparable nonfill orders. Several misses are explained by its touch-exit/window contract, and at least two false positives involve public messages that arrived after ACK despite carrying pre-submission exchange times. This evidence does not justify treating the M0A historical opportunity label as own-order fill probability. It says nothing about whether the published queue-state relationship itself is economically useful or profitable.

Delivered: this report; reproducible read-only `research_tools/dpm0_fill_audit.py`; machine-readable [`fills.csv`](../../research_outputs/dpm0b/fills.csv), [`orders.csv`](../../research_outputs/dpm0b/orders.csv), [`public_events.csv`](../../research_outputs/dpm0b/public_events.csv), and [`summary.json`](../../research_outputs/dpm0b/summary.json). The output directory is a generated research artifact; production code/tests and M4A checkpoint metadata were not changed. The existing reviewed baseline remains `m4a-a1-pass`, while the external-interface work already in the working tree remains unreviewed and was not touched.

**Next action:** Stop historical fill modelling and collect the minimum 5-share Maker Probe dataset needed for direct fill labels, under separate live-trading authorization.
