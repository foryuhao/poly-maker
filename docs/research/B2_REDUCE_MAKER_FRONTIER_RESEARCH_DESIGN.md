# B2 Reduce Maker Execution Frontier — Research Design

Status: Frozen observational design for the 2026-10-04 user request.
Git base: `97ab0f9` (`m4a-a1-pass`); B2 and earlier quote attribution are local unreviewed research predecessors.
Authority: `docs/research/CIP_B2_RESEARCH_DESIGN.md`, `docs/research/CIP_B2_BACKTEST_20261003.md`, and `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_20261004.md`.

## Question and immutable path

For the 89 accepted, unfilled post-T150 B2 risk-reducing BUY orders, measure the largest historical same-side SELL-print execution opportunity reachable without crossing the ask. Use only saved B2 orders, fills, inventory, capture events, official book reconstruction and frozen $25 capital path. Do not submit, cancel or modify an actual B2 order; no hypothetical fill changes a later observation. The 24 filled post-T150 orders are a frontier-gap reference. The 32-market B2 sample must reconcile to 113 accepted new Reduce orders, 24 filled, 89 unfilled, T150 residual 380, final residual 245 and 27 total Reduce-phase fills.

## Maker-safe frontier and event timing

At each event, reconstruct both books with the inherited R0 `Book`. A legal frontier exists only when both books are valid, same-side bid/ask/tick are known, `bid < ask`, tick >0, and `frontier = ask−tick` satisfies `frontier > 0`, `frontier >= bid`, `frontier < ask`. Preserve integer prices in 1e-4 units; no quote at or above ask is allowed. Trade qualification remains B2's strict same-token SELL print below BUY quote with available, exchange and receive timestamps strictly after activation. Order analysis stops at the original B2 accepted-to-close interval; no post-close print is borrowed.

## Four layers and capital

1. Original B2 price: assert zero qualifying prints for the 89 actual no-fill orders under B2's frozen active/cancel lifecycle.
2. Current best bid: rerun the prior quote-attribution observation using its same candidate, cancel/submit latency, trade and capital definitions; require exact reconciliation of 16 raw and 14 capital-feasible opportunities. This layer is latency-respecting.
3. Frontier upper bound: at every valid book event, instantaneously update the hypothetical quote to that event's legal frontier. Keep the last legal quote through an invalid-book interval; do not create a new quote from invalid data. Ignore cancel/requote latency, but retain the strict B2 print rule and original order-close horizon. Check capital at the print independently. This is an upper bound for the prescribed *always-current-frontier* rule, not a mathematical supremum over all persistent maker quote paths: downward frontier moves can forfeit fills available to an earlier resting bid.
4. Frontier latency observation: begin with the existing B2 quote at acceptance. On a frontier change or invalid book, request hypothetical cancellation, preserve fill eligibility through 234,783 μs cancel delay, then submit at the latest valid frontier with 415,281 μs submit delay. During a pending transition, wait rather than stack replacements. At activation, require the quote still be maker-safe; otherwise wait for a new valid book event. An invalid book produces no new quote. Count a SELL print only while a hypothetical order is active, current book valid and its price no higher than the current legal frontier. Capital-feasible observations additionally require that the incremental notional above the original B2 order fit the historical $25 capital path at hypothetical submission and at the print. This is an observational sequence, not a strategy replay.

The B2 capital check is historical filled purchase cost plus reservations of the original open and cancel-pending orders; a hypothetical replacement substitutes its own 5-share notional for the original order reservation. No hypothetical fill releases capital. Price concessions, local pool economics and time remaining are computed at each order's first qualifying opportunity within each layer. Report raw and capital-feasible flags separately.

## Deduplication and economics

Gross shares are `5 × number of capital-feasible no-fill orders` for a layer. At market level, sort each order's first opportunity by time, begin with B2 T150 absolute net and subtract at most `min(5, remaining residual)` for each distinct order. Never cross zero or propagate the synthetic fill into B2. Also cap this bound by the market's actual B2 final residual when expressing potential coverage of the 245 final shares. This is a shares opportunity ceiling, not realized reduction.

At a hypothetical print, compute `price−original_price`, its 5-share cost, and a local static inventory projection from actual B2 fills strictly before that print. Report `PoolPairCost` after a five-share BUY and `PairPnL_after−PairPnL_before`; do not propagate the projected state. If either side is absent before the fill, pre-fill pool cost is null and the PnL change still uses the existing matched amount. Group first opportunities by 0–30, 30–60, 60–90 and 90–150 seconds remaining. Compare filled versus no-fill orders' observed frontier gap during valid-book resting intervals.

## Classification and limits

Critical identity, maker-safety, print or B2-path invariant failure is `ANALYSIS INVALID`; insufficient valid-book exposure for 80% of the no-fill cohort is `DATA INSUFFICIENT`. If frontier capital-valid upper-bound opportunities add at most five orders over best bid, use `BEST BID IS NEAR MAKER EXECUTION FRONTIER`. If the final-residual-capped market upper bound remains below 65 shares, use `MAKER-ONLY EXECUTION IS STRUCTURALLY INSUFFICIENT`. If frontier creates more opportunities but the upper bound covers fewer than 180 of the 245 final shares, use `FRONTIER HELPS BUT CANNOT SOLVE RESIDUAL`. For larger ceilings, use `PRICE CONCESSION DESTROYS GAMMA EDGE` if median capital-valid frontier concession exceeds $0.10/share and median local projected PairPnL change is negative; otherwise `AGGRESSIVE MAKER FRONTIER HAS MATERIAL HEADROOM` only when latency-respecting capital-valid opportunities exceed best bid by at least 20 orders. All remaining cases are `FRONTIER HELPS BUT CANNOT SOLVE RESIDUAL`. These thresholds classify historical opportunity scale; they are not strategy parameters.

The inherited fill predicate has no queue model or public-print-size cap. Even the latency sequence lacks queue loss, feedback to inventory and realistic replacement acknowledgement. Upper-bound and local economics rows must never be called realized B2 fills or PnL.
