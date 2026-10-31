# V10 weather maker-counterparty comparison

V9 coverage, inspected without inspecting its financial outcomes, contains
only one qualifying NO-taker fill in 910 city-dates. Keep V9 unchanged.
Cheap NO taker prices and the complement of favorite YES taker prices are
different sides of the spread. This motivates a separate microstructure
comparison, rather than silently treating 1-minus-a-YES-fill as a NO ask.

Use the same August 1–October 30 ten-city grid, 09:00–09:05 local clock and
complete raw taker BUY pages already captured for V9. No outcome labels have
been inspected for this comparison. This is a secondary study on the same
collected parent events, not a completely untouched data collection or an
independent confirmation of V9. It does not alter the live V8 policy.

Fixed maker exposure: first eligible YES-taker BUY at p=0.75–0.95 inclusive
within that clock window, with actual matched quantity >=2/(1-p). Treat its
aggregate opposing economic exposure as NO acquired for cost 1-p. Normalize
to $2 of that exposure. Require recorded taker-only fee metadata (or zero
rate). Maker fee is zero; subtract an additional $0.02 scenario overhead.
At most one selected observation per parent city-date. Ties resolve by
timestamp, transaction hash, token, price and size, never by eventual outcome.
Native unconfirmed/unknown cases retain -$2.02 loss bounds.

This describes the actual aggregate counterparties' economic price, not an
order we could place at that price after seeing the trade. A public aggregate
fill may sweep levels or multiple makers. Own queue priority, inventory,
mint/merge mechanics, matching and capacity still need future raw-book and
execution validation. No maker queue fill is assumed from a trade print.

Primary output is equal-$2 counterparty return including all losses, with
calendar-day clustering, city breakdown and leave-one-city-out sensitivity.
Use a 99% day-bootstrap interval (two-sided) for this secondary hypothesis,
not an ordinary unadjusted 95% claim from many searched variants. Preserve
all missing/ambiguous events; unknown grid cases have worst exposure bounds.
Do not pool the two arms as independent observations. Any positive number
is a calibration/microstructure candidate requiring prospective validation,
not completion of the tradable-edge goal.
