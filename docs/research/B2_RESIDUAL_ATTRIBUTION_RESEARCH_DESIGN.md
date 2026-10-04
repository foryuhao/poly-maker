# B2 Residual Attribution — Research Design

Status: Research-only diagnostic of the frozen B2 result; no strategy change.
Git base: `97ab0f9` (`m4a-a1-pass`), with unrelated pre-existing working-tree changes.
Authority: User's B2 residual-attribution request, `docs/research/CIP_B2_RESEARCH_DESIGN.md`, and the current B2 research implementation and seven output files.

## Question and source

Explain the 245 final residual shares and −$72.4467 residual PnL across B2's exact 32-market universe. All actual strategy actions are reproduced by subclassing `TwoPhaseReplay`; the diagnostic subclass adds observation and research-only capital injections in separate counterfactual runs. It does not edit B2 policy, data, fill logic, quote price, timing, or settlement source.

## Causal unit and accounting

The final inventory shares in each market receive exactly one primary cause and optional secondary cause. Assign that market's entire final residual shares and residual PnL to its primary cause, so the category totals reconcile to 245 shares and −$72.4467. This is a market-level primary-cause partition; the separate capital counterfactual is the estimate of *marginally recoverable* shares and need not equal the number assigned to the capital category. The distinction prevents counting the same remaining shares in two categories.

For market-level primary classification, capital is primary when a joint research-only removal of its reduce-phase capital constraint lowers its final residual by at least half. Otherwise an accepted reduce-phase lighter-side order that received no fill before cancellation is primary `REDUCE_ORDER_WORKING_NO_FILL`, even if later canceled when the reconstructed book became invalid. An order awaiting activation at market end is `TIME_EXHAUSTED_AFTER_VALID_REDUCE`. If none of those applies and the book or quote is invalid, classify `REDUCE_BOOK_OR_EXECUTION_BLOCKED`. An actual minimum-size/sign-flip gate would take its own category; B2 has no such gate. `OTHER` is reserved for an unexplained state and must be individually disclosed. The half-residual rule resolves a single primary label; it is not a trading parameter.

## Blocked episodes and diagnostics

A capital block is a B2 decision with nonzero net, a valid book and lighter-side 5-share maker quote, no existing lighter-side order, and filled cost plus existing reservations plus required notional greater than $25. Consecutive blocked decision events form one episode until the next reconciliation where that state ends. Integrate blocked time over the existing available-time event clock. At each event record net, quote, required notional, filled cost, reservations, available cash and shortfall. Count invalid-book time separately while reduce-only and nonflat. A block event count is not an order count.

## Repair-only capital counterfactual

For each blocked episode, hypothetically provide exactly the shortfall plus floating-point tolerance at its start and run the original B2 replay thereafter. A necessary-condition screen skips repeated full replays only when no official same-token SELL print could satisfy B2's strict price and three post-activation timestamp predicates before the first subsequent book-invalid cancellation becomes effective; such an order cannot fill under the frozen model. Candidate episodes receive a complete B2 replay. Each episode reports whether the single order was submitted/accepted, whether it filled, fill timestamp/price/size, and the resulting final residual. Episode effects overlap, so they are **not summed**.

For the nonduplicated capital estimate, run a separate replay per blocked market. Keep $25 through T150; after T150, add only the amount necessary whenever the unchanged B2 strategy is about to reject a legal lighter-side 5-share order for capital. Keep all later B2 rules and fill predicates. Sum the market-level difference between actual and this joint counterfactual final absolute net. This estimates `CapitalRecoverableResidualShares`; the alternate capital is analytical only and is never used in the baseline output.

## No-fill, time, and state analysis

Use B2 orders and official captured events to describe accepted but unfilled reduce-phase submissions: quote, accepted-to-close duration, time remaining at submission, best bid at acceptance, later SELL prints, and cancel reason. Compare with accepted-to-fill times of actual reduce-phase fills. Bucket by T150 absolute net (0, 1–5, 6–10, 11–15, 16–20, >20) and T150 committed capital (<$15, $15–$20, $20–$23, $23–$24, >$24). These are descriptive diagnostic buckets, not strategy thresholds.

## Validation and limitations

For every diagnostic baseline replay, require exact B2 final net, residual, PnL, fill count and capital-block event count. Require 32 markets, 245 final shares, 135 post-switch reduction, and −$72.4467 residual PnL. The inherited full-remaining-order trade-through rule has no queue or public-print-size cap. Book-invalid stretches may reflect missing valid book evidence; this analysis cannot impute fills or repair capture gaps. Counterfactual outcomes depend on the frozen historical fill model and do not establish live executable quantity.
