# ITER-0006 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `e52e7c47d72298f6c4acdbec91545f69ada3bfcf`.
- Local remote-tracking branch matches HEAD.
- Working tree: preserved untracked `research_outputs/` only.
- Stage: `ITER-0006` / `ARCHITECT_REVISION`.
- ITER-0001–0005 are CODE_READY and archived.
- Latest reviewed full suite: 835 passed, 2 skipped, no exclusions.
- Approved Book Filter definition: `03_HUMAN_DECISION.md`, archived at HEAD.
- Approved $5 capital policy remains unchanged.
- MICRO_LIVE_READY: NO. Real-money authorization: NOT_GRANTED.

Read active instructions, Frozen SOP, baseline/index, blocked iteration records, approved decision, actual RDP modules and existing market-data/orderbook/parsing/tests.

This revision resolves the previous Inactive-definition blocker. Earlier artifacts remain immutable.

## Problem

Implement one prerequisite: the NORMAL Book Filter v0, with rolling market-data evidence and the approved cumulative depth-change definition.

## Evidence / Current Behavior

Frozen section 16 defines Inactive, Depth Extreme and Fast Move as negative NORMAL filters.

Human-approved Inactive semantics are:

```text
Q_eff = Size(BestBid) + Size(BestBid - current TickSize)

DepthChange2s =
    sum(abs(consecutive valid Q_eff changes))

InactiveSide =
    no real trade in last 2 seconds
    AND DepthChange2s < 5

Inactive = InactiveYES OR InactiveNO
```

Exactly five shares is active. Unchanged snapshots add zero.

Existing `MarketDataService` owns separate token books and supplies book-mutation/trade callbacks. Existing `OrderBook` exposes exact levels and dynamic ticks. No RDP Book Filter exists.

Existing EWMA/flow estimators implement different semantics and must not substitute for these predicates.

## Existing Components Reused

- Existing `MarketMeta` and outcome-token identity.
- Existing `OrderBook` bid levels and dynamic TickSize.
- Existing parsed `TradePrint`.
- Existing market-data book/trade callbacks and connection generation.
- Existing synthetic market-data fixtures.

## Existing Components Modified

NONE.

### Shared-Core Change

NO.

### FL Behavior Impact

NONE.

### IM Dependency

NONE.

## New Components Required

- `src/polymaker/strategy/rdp_book_filter.py`.
- `tests/test_rdp_book_filter.py`.

One small rolling-evidence class and immutable filter-result types are sufficient. No replacement book, predictive model, telemetry service or execution path.

## Desired Behavior

### Evidence collection

Create one filter instance for one market.

Provide synchronous methods to observe an existing token `OrderBook` and a parsed real `TradePrint`, using explicit monotonic receipt time and connection generation. Worker chooses API names.

Store immutable observations, not references to mutable book state.

Compute current `Q_eff` using the exact bid and bid-minus-current-tick levels. A missing second level contributes zero. Do not substitute `second_bid`, aggregate band depth or combined YES/NO data.

Validate token/condition association and finite legal bid/tick/depth/trade economics. Only a positive-size valid official trade print qualifies as a real trade.

Keep per-token histories in local processing order. Equal receipt times are allowed; decreasing/nonfinite time or invalid scoped evidence invalidates reliable history. Foreign tokens are ignored without altering this market’s evidence.

Use rolling deques and retain the most recent observation at or before the two-second cutoff as an anchor. Prune expired evidence while preserving that anchor.

Connection-generation changes invalidate both histories. Token TickSize changes invalidate that token’s comparable history and require a new baseline. Never combine old/new tick regimes for Fast Move.

Evaluation must receive current generation, current token ticks and explicit state-reliability input. A cached old tick cannot continue to certify after an authoritative tick change. Tick-change processing currently does not call `on_dirty`; the API must allow the eventual caller to detect/invalidate it during evaluation or explicit observation.

### Rolling boundaries

Use receipt-time intervals:

```text
(now - window, now]
```

Treat book state as constant between valid updates.

For total variation, include a consecutive change when its later observation falls inside the two-second interval. Retain the preceding observation to measure that change. Do not interpolate depth or prorate a discrete update.

Trade presence uses the same two-second interval. A trade exactly at the expired boundary no longer supplies activity.

Require at least two seconds of continuously valid history for both tokens after initialization/reset. A valid quiet book can cover elapsed time without synthetic mutations; it will normally fail Inactive when no trades or changes occurred.

Insufficient/disconnected/unreliable history returns an explicit rejection. This is evidence readiness, not an additional strategy filter.

### Predicates

Evaluate approved Inactive exactly.

Depth Extreme uses current token depths:

```text
high = max(QY_eff, QN_eff)
low = min(QY_eff, QN_eff)

DepthExtreme =
    high / max(low, 5) > 4
    AND high > 50
```

Both comparisons are strict.

For Fast Move, inspect observed BestBid excursion over the last one second, including the carried book value at its start:

```text
FastMoveSide =
    max(observed bids) - min(observed bids)
    >= 2 × current token TickSize
```

This detects a two-tick move reached within the window even if it subsequently reverses. Repeated one-tick oscillation does not accumulate into a two-tick price move.

Overall rejection is the OR of the three Frozen predicates. Report all triggered predicates, not merely the first.

Use decimal-safe threshold/grid calculations so exactly five depth shares, ratio four, depth fifty and two ticks retain their specified boundaries. Unsupported precision fails closed.

Return an immutable result exposing readiness, per-token Q_eff/depth variation/trade presence/bid excursion, predicate flags and rejection reasons.

Passing this filter is NORMAL Book Filter eligibility only. It does not grant placement authority or apply to REBALANCE.

## Minimal Scope

Only:

```text
src/polymaker/strategy/rdp_book_filter.py
tests/test_rdp_book_filter.py
```

No existing market-data, parser, engine, CLI, accounting, capital, quote, configuration or dependency changes.

Tests wire the existing callbacks to the new component using an injected monotonic clock. Production runtime wiring belongs to a later iteration.

## Invariants

- Approved cumulative absolute variation is used unchanged.
- Unchanged Q_eff contributes zero.
- Exactly five shares is not inactive.
- Either inactive side rejects.
- A valid recent trade activates only its own side.
- Depth Extreme retains both strict conditions.
- Fast Move triggers at two ticks.
- Dynamic tick and generation boundaries cannot reuse incompatible history.
- Existing books remain authoritative and unmodified.
- Observer state has no placement/cancellation authority.
- No new predictive filter, trading state or REBALANCE condition.

## Acceptance Criteria

1. Both approved reversing-depth examples report cumulative variation 12 and 6.
2. Exact activity/depth/movement boundaries follow the specified comparisons.
3. Real trades and token-local activity are handled independently.
4. Exact two-level depth is used, including absent bid-minus-tick levels.
5. Rolling expiration retains anchors without retaining expired activity.
6. Warmup, invalid evidence, reconnect and tick changes fail closed.
7. Synthetic existing market-data callbacks populate correct evidence.
8. Both normal filter failure and pass results are immutable and reconstructable.
9. Book/filter evaluation introduces no authoritative mutations or I/O.
10. Targeted, affected and full-suite regressions pass within scope.

## Required Tests

At minimum:

- `20 → 26 → 20`: variation 12; `20 → 23 → 20`: variation 6.
- Variation exactly five, just below five, unchanged snapshots and repeated evaluation.
- Trade within window versus exactly expired boundary.
- Either token inactive; only one token traded; both active.
- Two-level Q_eff with missing neighbor and nonadjacent second bid.
- Depth Extreme: ratio exactly four, just above; high exactly fifty, just above; denominator floor five.
- Fast Move: one tick, exactly two ticks, greater; upward/downward; reversal; repeated one-tick oscillation.
- Window-boundary anchor, discrete-update inclusion and expiry.
- Initial warmup, quiet valid coverage and backward/future-invalid evaluation times.
- Nonfinite/invalid book, depth, tick and trade evidence.
- Generation reset affecting both sides; token tick change requiring new baseline.
- A tick change without `on_dirty` cannot leave cached-history eligibility valid.
- Event updates with equal receipt times preserve consecutive variation.
- Synthetic snapshots/deltas/trades through existing `MarketDataService` callbacks.
- Independent captures proving existing books and trade inputs are unchanged.
- History pruning preserves results while discarding expired data.

Run:

```bash
uv run pytest -q tests/test_rdp_book_filter.py
uv run pytest -q tests/test_marketdata.py tests/test_orderbook.py tests/test_estimators.py tests/test_rdp_quotes.py tests/test_rdp_capital.py
uv run pytest -q
```

Record exact results/failures. Inspect status, relevant untracked files, diff/stat and whitespace checks. Preserve historical assets and audit evidence.

## Observability Requirements

Expose each evaluated predicate and its numeric evidence, window coverage, generation/tick compatibility and readiness reason.

The total-variation definition must reference `03_HUMAN_DECISION.md`.

No durable writer, runtime metrics or live observer wiring in this iteration.

## Live Validation

No live action is required.

After integrated readiness and authorization, preserve the official book/trade history around NORMAL admission and recompute these predicates independently. The tests establish mechanics, not fill economics.

## Explicit Non-Goals

- Reopening total variation or the $5 reserve.
- Additional filters or configurable threshold optimization.
- REBALANCE filtering.
- Full runtime/controller/CLI.
- Shared market-data changes.
- Trading-state transitions, orders or cancellation.
- Persistent telemetry.
- Readiness declaration.

## Risks

- Using generic band/second-level depth would change Q_eff.
- Lost cutoff anchors would undercount discrete activity.
- Cached ticks or reconnect history could falsely certify readiness.
- Book-mutation callbacks aggregate changes within a processed message; the component must use the valid book states actually emitted by existing infrastructure, without inventing intermediate states.
- Existing parser output still requires local finite/identity validation.

## Deferred Findings

Production callback composition, broader feed freshness/queue health, runtime/controller timing, authenticated cash integration, metrics and CLI remain outstanding.

They are outside this focused filter prerequisite.

## Human Approval Required

NONE beyond the recorded total-variation decision.

Ordinary receipt-time boundaries, evidence warmup and history storage are defined here as implementation choices preserving the approved filter and fail-closed behavior.

Real-money trading and cancellation remain unauthorized.
