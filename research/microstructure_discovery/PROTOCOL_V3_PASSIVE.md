# Prospective passive-liquidity study V3

Registered after V2 training rejection, before collecting the V3 dataset. V2 frozen selection remains null; its holdout will never be used to choose a V2 candidate.

## Question

Can improving an existing spread by one tick and restricting inventory duration earn positive net returns from incoming liquidity demand, with conservative queue/execution evidence? Compare a neutral quote with an observable pressure filter. This studies the economics of liquidity provision; it does not assert private information identification.

## Data and split

Use the fixed discovery market list from extended_20261031_1810; first outcome per binary condition only. Capture a new, disjoint two-hour raw stream. First 30 minutes training, remaining 90 minutes initial holdout, 122-second embargo. Code must be audited for causal replay before evaluation; freeze one rule/horizon/market after training only, before opening holdout returns. Independent subsequent confirmation remains required for a claimed edge. If no candidate qualifies, record rejection without changing the registered thresholds.

## Two fixed quote policies, two holding periods

- neutral_inside: quote primary outcome BUY at best bid + one market tick if spread >= two ticks (epsilon tolerance), quote strictly below ask, price in [0.02,0.98].
- pressure_filtered: same quote only when near-depth imbalance (bid_depth-ask_depth)/(bid_depth+ask_depth) >=0.5 and no receipt-time decline of bid in the preceding 3 seconds. Unknown past data => no quote.

Five shares, fully cash funded. New quote decisions at most every 15s per condition; fixed 5s live lease. Decision-to-post latency scenarios 0.5s,1s,2s. On simulated arrival require the quote still strictly improves best bid and remains strictly below ask; otherwise no post. One active quote/inventory per market per independent policy/horizon/delay simulation. No repricing or invisible queue priority. Holdings close at 30s or120s from first fill (not from order decision). Cancel residual quote upon first fill to bound exposure; cancellation latency same as post latency, so continue admitting eligible partial fills during cancel latency, capped at five shares.

## Strict counterfactual fill evidence

At posting price no visible queue exists because it must improve best bid. Credit only subsequent primary-outcome SELL trade messages at prices <= our quote, with source timestamps at least 0.25s after simulated arrival, bounded source/receipt lag and same connection. This is a strict trade-through paper-fill assumption, not an actual exchange fill. Never credit bid depletion, cancellations, midpoint touches, or BUY messages. Consume reported sizes cumulatively up to order quantity. Partial executions are positions, not ignored samples. Cancel/expiry can race executions; use source time plus conservative cancellation-arrival bound. Exits below market minimum size or with insufficient trusted executable depth get zero recovery stress rather than dropping the entered position.

Cash entry uses our quote (never the better reported trade price); exit uses trusted full-depth bid VWAP after decision latency, with per-market verified effective taker fees; entry maker fee=0 only where official effective market parameters agree. No rebates or incentives assumed. Add0.001/share/leg adverse reserve; report double-reserve sensitivity. Unknown fees => ineligible to establish edge. Broken books, source regression, reconnect invalidate new posts; entered positions remain in PnL and get conservative zero recovery when exit evidence is missing. If price trades above our limit it is not a fill.

## Candidate and proof gates

Select at most one market/rule/horizon on training-only returns with >=10 entered positions, verified costs and positive net mean at every latency scenario. Record candidate immutably before reading holdout. Initial holdout needs >=20 entered positions and >=6 five-minute blocks, positive net at every delay and double-reserve sensitivity, no single-block concentration (>50% of total positive PnL), and a family-adjusted one-sided95% block inference lower bound >0. Related contracts from the same event do not count as independent corroboration.

Fresh independent confirmation repeats the frozen candidate for >=90min with >=20 entered positions, same latency/cost/statistical tests, no adaptive threshold changes. Market resolution or rule changes terminate new entries, not an opportunity to relabel outcomes. A paper-screen pass is evidence conditional on counterfactual fills; it cannot establish real fill quality. Before claiming a tradeable edge require independent validation of the fill assumption from paired public trade/depth timing, explicit capacity limits and evidence that the advantage survives plausible adverse selection. No live orders authorized by this research protocol.

## Implementation clarification before any passive-return evaluation

A shadow quote can also be crossed by a newly visible ask without a reported SELL execution (the incoming order might be post-only). Ignoring this would selectively miss adverse fills. Such quotes are therefore treated as ambiguous full-size exposures: evaluate the ordinary exit but cap their net contribution at min(0,net), never crediting positive returns; partial positions below minimum exit size receive zero recovery. This is a conservative partial-identification bound, not an assertion of exchange execution. Record the ambiguous count separately. Proof cannot use favorable unobserved fills. Training selection and holdout require known market fee parameters; actual totals use actual hypothetical quantities, not five shares for partials.

Clock-uncertainty clarification before return inspection: trades with source timestamps within ±0.25s of posting/cancel arrival may have executed during our uncertain ownership interval. They are not silently discarded. Treat these as ambiguous exposures with loss-only PnL bounds; only interior [posted+0.25, cancel_arrival-0.25] timestamps are strict-credit fills. Drain remains open until cancel+2.25s to admit bounded-lag boundary evidence. This prevents avoiding adverse fills by filtering a timing uncertainty window.

Data-gap ownership clarification before return inspection: a stream break can hide a maker execution even when no fill message was received. Every LIVE quote at a known break is converted to a full-size potential exposure with loss-only return bound; retain existing positions and forbid optimistic marks across generations. Terminal market resolution persists across reconnects and prevents new posts. Holdout eligibility begins no earlier than max(split+122s, actual freeze time+122s), so slow training cannot consume already-elapsed prospective validation signals.

Tick/admission clarification before return inspection: a tick-size change pauses further quotes for that asset under this fixed-universe study; existing LIVE quotes become loss-only potential exposures instead of assuming unchanged order validity. A late book update within2.25s of posting whose source time predates posted+0.25 and whose ask crosses our bid makes post-only acceptance uncertain, including if a full strict paper fill was credited first. Such positions cannot contribute positive PnL. New independent runs must refresh market ticks rather than inherit the paused grid.

Complementary execution clarification BEFORE any passive-return evaluation: the exchange can match complementary BUY orders through minting, so primary BUY liquidity is also exposed to aggressive complementary-token BUY flow. Normalize a complementary BUY at p to an economically equivalent primary SELL at1-p; include it in trade-through evidence with identical timing/size rules. Conservatively deduplicate primary SELL and complementary BUY mirror prints by primary asset, source timestamp, canonical price, size and transaction hash. Never count both legs as twice the volume. This closes a material selective-adverse-fill omission; evidence retains original asset/side and canonical price. Official mechanism source: https://github.com/Polymarket/ctf-exchange/blob/main/docs/Overview.md and https://docs.polymarket.com/concepts/prices-orderbook .
