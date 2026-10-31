# V5 ETH endgame: independent historical replication and fresh public books

Registered 2026-10-31 after exploratory 144 settled ETH five-minute markets.
Twenty policies were explored. The last30s, price exactly0.99 policy had34/34
winners and +0.0073 per gross share after0.07 fee rate and0.002 reserve.
This sample does NOT establish an edge; its tail uncertainty is much too large.
Other broad thresholds mostly lost money. No original BTC/CA data is used.

Freeze one follow-up hypothesis: earliest chronological actual taker BUY at0.99
(upper bound0.995 retained from screen, but actual tick prices must be audited),
size>=5, in block-time [end-30,end). One row per market; price-independent
transaction hash/token tie-break. All requested chronological market slots are
included; missing/unresolved/fee-unknown slots explicitly reported. The first
2016 slots immediately BEFORE the exploratory144 slots form an unseen historical
replication set. No parameter search on it. Report exact per-slot profits, daily
dependence and one-sided binomial tail bound; fees deduct outcome shares on BUY.
Verify final oracle payouts and contract versions. Current Gamma fee metadata
is not itself proof of historical fee activation.

IMPORTANT: block time is on-chain settlement time, not execution time. Historical
replication can establish a conditional PnL pattern among actual participants;
it cannot establish a causal end-30s trading policy. A positive replication must
be followed by fresh receive-time books and trades with actual fee/depth/latency
audit. Public shadow execution remains conditional on fill availability and
cannot be relabelled as real live fills. No live orders are part of this study.

Fresh public capture starts after registration, for24h across every chronological
ETH five-minute market. Capture last60s through expiry+5s, with raw source/receipt
timestamps, reconnect generations and full-depth initial books/deltas/trades.
Select first valid receive-time ask0.99 in last30s; five gross shares, post delay
0.5s, FOK limit0.99, minimum full depth. Record same1s/2s stress as diagnostics.
No negative-loss exposure may be discarded because of later book invalidity.
Orderbook display is not proof of a real fill; match subsequent actual trade
prints where possible and retain uncertain branches. Reconnection after entry
requires loss bounds. Resolution payouts fetched after expiry, including losers.

Exploratory/follow-up population separation is fixed by epoch in config.json.
No statistical edge declaration from a short sequence of0.99 winners. Require
fee-adjusted probability bounds above breakeven, stability across time blocks,
and executable prospective evidence. Do not impose arbitrary2s profitability as
the definition of the user's objective; use measured deployment latency.
