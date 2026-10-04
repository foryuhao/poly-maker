# B2 Reduce Quote Competitiveness Attribution — Research Design

Status: Frozen observational analysis from the user's 2026-10-04 request.
Git base: `97ab0f9` (`m4a-a1-pass`), with pre-existing unrelated dirty worktree and unreviewed local B2 research artifacts.
Authority: `docs/research/CIP_B2_RESEARCH_DESIGN.md`, `docs/research/CIP_B2_BACKTEST_20261003.md`, and frozen `research_outputs/cip_b2_20261003/`.

## Question and population

Are B2's post-T150 accepted, risk-reducing BUY maker orders that never fill typically less competitive than those that fill? The population is the 113 new orders submitted and accepted during B2 `REDUCE_ONLY`: 24 filled, 89 unfilled. Three additional Reduce-phase fills on pre-T150 orders are excluded from this order cohort but retained in the B2 stage-total reference of 27. No B3 path is used.

## Book clock and distance

Reconstruct YES/NO books with B2's inherited R0 `Book` class using the same captured `available_at_us` stream, market bounds, nonmonotone-event skip, BOOK/REST_BOOK/DELTA/TICK handling and token identities. B2 advances order activation before each event update; distance at exact acceptance is sampled from the preceding state. At each valid book event during the accepted-to-close interval, compute `round((same-side best bid − order price)/tick size)`. Negative values remain negative. Integrate the piecewise-constant book state between available-time events over the intersection with each order's `[accepted_us, closed_us)` lifetime. Exclude invalid-book intervals from all distance denominators and report their duration. Event medians and time-weighted means are separate; the group comparison of time-weighted means takes the median across orders. At a fill TRADE event, capture the book immediately before processing that event.

The mutually exclusive time buckets are `<=0`, `1`, `2`, `3–5`, and `>5` ticks. Exact zero and negative distance seconds are also separately available. Bucket fill counts use distance immediately before an actual fill; `fills per 100 order-minutes` is a descriptive opportunity intensity, not an estimated queue-fill probability. Orders entering multiple buckets are counted in each; entry counts do not sum to 113.

## Read-only best-bid opportunity screen

For each of the 89 no-fill orders, observe valid book events from acceptance until before the actual B2 cancel request. At a bid strictly above the original order price, consider a hypothetical replacement at that best bid. Preserve B2's 234,783 μs cancel and 415,281 μs submit latencies by setting earliest hypothetical activation to `max(observation + cancel latency, original activation) + submit latency`. Require a later same-token SELL print during the original order's actual accepted-to-close lifetime, price strictly below the hypothetical quote, and all three print timestamps strictly after hypothetical activation. Check that the extra notional at observation would fit the actual B2 filled-cost plus live-order-reservation path under $25. Report both raw timing/print opportunities and capital-feasible opportunities. No hypothetical order is inserted, no inventory is updated, and no PnL is summed. The screen is deliberately conservative about cancel and submit latency and does not model a new queue position.

## Context and interpretation

Use B2's ACCEPT action audit for inventory and capital context. Compare `abs(Net)<=10` versus `>10` at acceptance, and market final residual `<=10` versus `>10`, descriptively; these bins are report summaries, not strategy thresholds. Classify `REDUCE QUOTES ARE TOO PASSIVE` only if no-fill orders have at least a two-tick larger median order time-weighted mean distance, at least 20 percentage points more valid time over two ticks behind, and at least 30% of no-fill orders have capital-feasible hypothetical opportunities. If the median distance difference is under one tick and the stale-time difference under ten points, use `QUOTE DISTANCE IS NOT PRIMARY BOTTLENECK`; otherwise use `MIXED EXECUTION BOTTLENECK`. Source mismatch, path alteration or treating opportunities as realized strategy results is `ANALYSIS INVALID`; fewer than 80% of orders with valid-book exposure is `DATA INSUFFICIENT`.

For uncertainty around the median order time-weighted distance gap, resample the 32 markets with replacement 5,000 times using seed `20261004`, retaining all orders within each sampled market. Report the percentile 95% interval for no-fill minus filled. This quantifies sample sensitivity without claiming randomized causal identification.

## Scope limits

The inherited maker fill model has no queue-position or public-print-size cap. The opportunity screen conditions on the historical B2 path and cannot estimate the fills or PnL of a modified policy. No B2 strategy parameter, order, replay state, production module or external data source is changed.
