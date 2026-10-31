# V9 independent weather calibration and execution study

Motivation: Qiu & Qiu, arXiv:2610.13593v1 (9 October 2026), reports
overpricing of 0.75–0.95 YES favorites near daily temperature peaks.
Its mean trade prices are explicitly not executable prices. The public code
link returns 404, the operational definition of the peak is unclear, and some
city event counts exceed the 239 calendar days in the stated sample interval.
These are unresolved replication questions, not established author errors.
Neither that paper nor an aggregate bias supplies our trading-edge proof.
Other primary papers, including arXiv:2609.12878, show that weighting and
category selection can reverse a longshot-bias conclusion.

The paper's sample ends 31 July 2026. Before reading our outcome results,
freeze an independent sample: 1 August–30 October 2026 inclusive (91 days),
ten cities: Atlanta, Chicago, London, Miami, NYC, Seattle, Seoul, Shanghai,
Tokyo, Toronto. Exactly one city-date event expected. Preserve missing,
ambiguous, malformed and failed requests, never silently exclude them.
This is a different causal clock rule, not an exact replication of an
unspecified actual-temperature-peak alignment.

Fixed clock: 09:00:00 through 09:04:59 local civil time in the city's IANA
zone. No realized weather maximum, future mean price, or eventual winner
enters selection. Inspect public actual taker BUY fills with cash size >=$10.
Take the first eligible NO-token fill in that window at a price between
0.05 and 0.25 inclusive; tie-break by transaction hash, token, price and size.
At most one observation per parent city-date, so repeated trade prints,
temperature bins and horizons cannot inflate the independent sample size.

This stage estimates calibration at real public transaction prices. It does
NOT simulate our fills after observing another person's transaction. Block
timestamp is settlement time; the historical book, matching latency and
multi-level execution prices are unknown. A positive result still requires
future full-depth order-book availability with a feasible fixed USD-budget
order and actual receipt-time delay. No orders or credentials.

For each selected observation, use $10 fixed cash. Collect Gamma metadata
and native resolution for the exact condition. Fees are taken in shares for
a BUY: winning payout is (10/p)*(1-r*(1-p)); losing payout is zero. Use a
conservative r=max(0.05, recorded exponent-1 rate) scenario, explicitly
distinct from a claim about the historical fee actually charged. Unknown
fee metadata or unconfirmed payouts retain worst loss -$10. A fixed $0.02
per-position overhead is another sensitivity assumption, not a measured fee.
Preserve metadata and raw source pages; do not use 1-minus-YES mean price as
an available NO ask. Current and historical fee-era comparability is audited
before any eventual edge claim.

All 910 requested city-dates stay in coverage reporting. Document absent
events separately from true complete-pagination zero qualifying fills.
Collect the whole event's taker BUY feed with server minimum-CASH filtering;
the v2 condition/event feed ignores start/end, so apply time locally, verify
descending timestamps and walk cursors until past the start or real EOF.
Any pagination/order/filter failure is a data-quality failure, not a zero.

No parameter fitting in August–October. Primary mean is equal-cash,
one-observation-per-event return, including all selected losses and unresolved
worst exposures. Report aggregate cash and per-event distribution; bootstrap
calendar days to retain within-day dependence across cities. Report each
city and leave-one-city-out sensitivity, without selecting a winning city.
Paper/calibration results, historical actual traded prices, and prospective
own-entry feasibility remain separate evidence layers. V8 is unchanged and
continues to its originally registered end.
