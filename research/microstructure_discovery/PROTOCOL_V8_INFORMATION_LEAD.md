# V8 fresh information-lead validation

Training evidence is the completed13:18–14:18UTC ManchesterUnited observation
and earlier exploratory captures. One large information-lag episode had real
old-price executions; small signals and sequential delayed hedges also failed.
None of those observations count as V8 validation. Registration is the NEW
JSON timestamp and source hash written when the V8 program starts.

Fixed universe: the32 previously matched cross-venue propositions,22 Poly
conditions,44 tokens. No return-based universe change. Read future public
batch-book snapshots. Compare with the most recent observation at least3s
earlier, at most5s earlier; require continuous recent data, receipt skew<=.5s,
and processing age<=.35s. On the desired Kalshi hedge side its ask must have
fallen>=.05 and its bid>=.03 while the cheap Poly ask changed by<=.02. This
selects reference-market information leading a local stale quote, rather than
arbitrary isolated local quote moves. Same-game60s cooldown after an eligible
decision. If several qualify together choose highest CURRENT margin.

Target10 net matched units. Poly is an actual USD-budget BUY FOK model, not a
fixed-share model: choose the best ask as cap, round USD budget UP to.01 to
cover10 shares even at twice the unrounded fee (five-decimal rounding bound).
Require budget>=1USD and the book minimum size, sufficient cash-depth at cap.
Kalshi BUY10 contracts at its best implied ask; require full depth there.
Use current multiplier1, .07 fee curve and.01-contract minimum. Bound possible
per-fill centicent rounding by at most1000*.0001USD. Require the NORMAL-game
paired payoff minus ALL cap costs, fee bounds and.02USD reserve >=.50USD.
This is an entry rule for this trial, not a universal definition of edge.

Future execution observations: Kalshi .5s, Poly1.5s after decision (different
venue matching delays); also preserve diagnostics at2.5s and5s. Original caps
and USD budgets cannot improve after seeing future books. Active-market and
fee metadata checked. Both fills create a pending settlement observation,
not immediate realized PnL. The guaranteed ordinary-payoff margin is credited
only provisionally; final market payouts and differing exception rules need
independent verification. Extra received shares marked0. No actual orders.

If only Kalshi fills, unwind at5s using a predecided1cent minimum SELL limit,
full depth, exit fee/rounding bound; no bid => zero recovery. If only Poly
fills, charge full USD budget as a zero-recovery bound: do not assume unmined
tokens can be sold. Network/metadata uncertainty retains worst exposure.
Neither fill =>0. All failed/zero signals stay in the denominator. A synthetic
1000USD capital reserve, no recycling before settlement, limits cumulative
commitments. No claim of actual account funding or own trades.

The validation period ends with the already registered batch producer, around
2026-11-01 01:53UTC. Do not stop or select only successful episodes to claim
strategy profitability. Require multiple independent games/time episodes,
cost-correct total including failures, and settled or explicitly bounded
remaining exposures. Exact statistical scope and dependence must be reported;
no short-sample annualization or proof of permanent future returns.
