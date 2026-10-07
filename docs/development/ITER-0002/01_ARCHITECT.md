# ITER-0002 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `3d1853000d258ce575b5b6a0664d03c6fafccf94`.
- Local `origin/rdp-phase0-dev` matches HEAD.
- Framework checkpoint remains in ancestry.
- Working tree has only preserved untracked `research_outputs/`; no source/test changes.
- Current iteration/stage: `ITER-0002` / `ARCHITECT`.
- ITER-0001 accounting: `CODE_READY`, final independent PASS in `07_REVIEWER_REREVIEW_02.md`, archived at `d888a77` with `08_ARCHIVE_RECEIPT.md`.
- Last documented full suite: 679 passed, 2 skipped, without exclusions. Worker must verify the new resulting tree.
- `MICRO_LIVE_READY`: NO. Trading authorization: NOT_GRANTED.

Read the active contract, Frozen SOP, baseline, current index, ITER-0001 result, final PASS and archive receipt. Inspected actual accounting source/tests, existing quoting policy, market-data books/tick updates and gateway placement safeguards.

The index’s “verified development HEAD” describes the prior archived implementation commit; the actual current HEAD additionally contains the archive receipt and ITER-0002 initialization.

## Problem

RDP has confirmed inventory accounting but lacks the Frozen NORMAL quote calculation.

Implement exactly one prerequisite: a pure NORMAL dual-maker quote candidate builder using existing inventory and book types.

## Evidence / Current Behavior

- `strategy/rdp_accounting.py` provides the reviewed `RDPInventorySnapshot`.
- `domain.py` already provides `MarketMeta`, `Quote` and `Side`.
- `marketdata/orderbook.py` provides immutable `BookView` and dynamic `OrderBook.tick_size`.
- `MarketDataService` maintains a separate book for each subscribed token and processes `tick_size_change` events.
- Existing generic quoting uses fair value, volatility, layers, reward sizing and SELL exits. Reusing that policy would change Frozen RDP semantics.
- FavoriteFirst uses first-leg/pair behavior and is unsuitable for RDP NORMAL.
- Existing gateway checks minimum order size and supports forced post-only placement; this iteration does not invoke it.

Frozen SOP sections 14, 15 and 17 specify:

```text
abs(Delta) < 5
BY + BN <= 1.00
MY = BY - TickY
MN = BN - TickN
qY = 5 + max(-Delta, 0)
qN = 5 + max(Delta, 0)
```

Book Filter, CapitalGate and lifecycle remain separate prerequisites.

## Existing Components Reused

- Reviewed `RDPInventorySnapshot`.
- Existing `MarketMeta`, `Quote`, `Side.BUY`.
- Existing immutable `BookView`.
- Existing market-data service/book fixtures for testing dynamic token-specific ticks.
- Existing FL quoting and market-data regression tests.

## Existing Components Modified

NONE.

### Shared-Core Change

NO.

### FL Behavior Impact

NONE.

### IM Dependency

NONE.

## New Components Required

- `src/polymaker/strategy/rdp_quotes.py`.
- `tests/test_rdp_quotes.py`.

A small immutable candidate/result type may accompany the pure function. No new state machine, dependency, service, data directory or execution path is required.

## Desired Behavior

Provide a synchronous pure NORMAL candidate interface accepting:

- one `MarketMeta`;
- its `RDPInventorySnapshot`;
- YES and NO `BookView`;
- explicit current YES and NO tick sizes.

The explicit ticks allow the eventual caller to use each token’s authoritative live book tick rather than one cached market-level tick.

The Worker chooses function/type names.

For valid NORMAL inventory and a passing PriceGate, return exactly two existing `Quote` objects:

```text
BUY YES: price = YES BestBid - TickY
         size  = 5 + max(-Delta, 0)

BUY NO:  price = NO BestBid - TickN
         size  = 5 + max(Delta, 0)
```

Return the pair in deterministic YES/NO order. The result must identify that post-only execution is required.

The builder produces candidate quotes only. It must document that Book Filter, CapitalGate, reliability, current-cycle reconciliation and live authorization remain mandatory before execution. No `ready_to_send` claim is permitted.

Return no quotes with an explicit reason when:

- `abs(Delta) >= 5`: NORMAL is unavailable; REBALANCE is required.
- `BY + BN > 1`: PriceGate fails.
- Either required bid/ask is missing, invalid, nonfinite, outside the binary price interval or the book is crossed/locked.
- Either tick is invalid/nonfinite/nonpositive.
- A bid or resulting price is incompatible with its tick grid.
- Either resulting quote is outside `(0, 1)` or not strictly below its corresponding ask.
- Venue minimum-order metadata is invalid, or either Frozen quantity is below the minimum.

Reject mismatched market/inventory identity and malformed inventory explicitly.

Use the Frozen sizes without rounding, scaling, clipping or increasing them to meet a larger venue minimum. Reject the entire dual candidate if either side is illegal; never emit one NORMAL side alone.

Do not clamp an invalid `BestBid - TickSize` price to a different price. Existing `round_to_tick()` clamps boundaries and therefore cannot be reused to change the Frozen result.

Use decimal-safe arithmetic or equally precise normalization for price subtraction, grid validation and PriceGate. An exact decimal bid sum of one must pass; a materially greater sum must fail. Numerical normalization must not become a strategy tolerance.

## Minimal Scope

Permitted changes:

```text
src/polymaker/strategy/rdp_quotes.py
tests/test_rdp_quotes.py
```

Implement only the pure NORMAL candidate calculation and tests.

No engine, CLI, gateway, accounting, market-data, configuration or dependency modifications.

## Invariants

- NORMAL condition is strictly `abs(Delta) < 5`.
- PriceGate uses current best bids, not proposed prices.
- Prices are exactly one current token-specific tick below BestBid.
- The base size remains five shares.
- Inventory skew affects quantity only.
- Both quotes are BUY, with post-only required.
- NORMAL returns both sides or neither.
- Invalid inputs fail closed without a substitute quote.
- Candidate creation has no I/O or authoritative-state mutation.
- No FL/IM semantics or shared-core behavior change.
- No execution authority is introduced.

## Acceptance Criteria

1. Valid `Delta = 0` produces five YES and five NO shares.
2. `Delta = +2` produces five YES and seven NO; `Delta = -3` produces eight YES and five NO.
3. Fractional inventory skew remains fractional.
4. Both sides use their explicit current tick; differing YES/NO ticks work.
5. Exact PriceGate equality passes; a greater bid sum rejects even if proposed prices sum below one.
6. Both `Delta = +5` and `Delta = -5` reject NORMAL; values strictly inside the boundary remain eligible.
7. Missing/invalid books, illegal prices, off-grid values and invalid ticks return no candidate.
8. Minimum-size failure rejects the whole pair without changing Frozen quantities.
9. Market/inventory mismatch or malformed inventory cannot produce quotes.
10. Post-only requirement and outstanding gates are explicit in the API documentation/result.
11. Inputs remain unchanged across successful and rejected calculations.
12. Targeted, affected and full-suite regressions pass; actual diff remains within scope.

## Required Tests

Use concrete expected prices and quantities, not merely the implementation formula repeated in assertions.

Required cases:

- Flat, positive, negative and fractional Delta.
- Both threshold signs, including just-inside values.
- PriceGate below, equal and above one.
- Above-one bids whose proposed quote-price sum falls below one: rejection.
- YES tick `0.01`, NO tick `0.001`, with expected distinct offsets.
- Repeated construction after a synthetic existing market-data tick-change event: next candidate reflects the new tick.
- Unknown/missing bid or ask; NaN/infinity; locked/crossed book.
- Zero/negative/nonfinite tick.
- Boundary subtraction yielding zero; no clamping.
- Off-grid bid/price.
- Valid minimum of five, incompatible larger minimum, and malformed minimum metadata.
- Identity mismatch and malformed inventory.
- Deterministic pair ordering, BUY sides and post-only requirement.
- Independent captures establishing input immutability.
- Integration with a real `snapshot_inventory()` result generated from existing registered confirmed fills.

Run:

```bash
uv run pytest -q tests/test_rdp_quotes.py
uv run pytest -q tests/test_rdp_accounting.py tests/test_quoting.py tests/test_orderbook.py tests/test_marketdata.py tests/test_favorite_first.py tests/test_favorite_first_review.py
uv run pytest -q
```

Record exact commands/results, failure names and relevant errors. Inspect Git status, untracked files, diff/stat and `git diff --check`. Preserve historical assets and immutable audit whitespace evidence.

## Observability Requirements

Return an explicit reason for absence of a candidate. Distinguish PriceGate failure, non-NORMAL inventory and invalid inputs sufficiently for later runtime logging.

Expose the exact generated prices, quantities and post-only requirement through the immutable result.

No persistent logger, telemetry counters or cycle attribution is required here.

## Live Validation

No live reads, order creation or cancellation are required.

After integrated readiness and explicit human authorization, compare one supervised BTC5m NORMAL attempt against its recorded inventory, current per-token tick and best bids. Verify the submitted quantities/prices and post-only setting.

Unit correctness does not establish fill rate or strategy edge.

## Explicit Non-Goals

- Book Filter history or decisions.
- CapitalGate or cash/reservation accounting.
- NORMAL cycle execution, FillWindow, cancellation or reconciliation.
- REBALANCE quote calculation or execution.
- Trading-state transitions or STOP behavior.
- CLI, market selection, discovery or credentials.
- Runtime integration or live-send changes.
- New metrics, external interfaces or datasets.
- `MICRO_LIVE_READY` declaration.

## Risks

- Reusing generic quote helpers could introduce clamping, fair-value constraints or size adjustment absent from Frozen RDP.
- Using `MarketMeta.tick_size` for both tokens could ignore dynamic side-specific updates.
- A pure candidate must never be treated as permission to submit before the remaining gates are integrated.
- Venue metadata incompatible with Frozen quantities requires safe rejection; this iteration must not invent adjusted sizing.

## Deferred Findings

- Book Filter, CapitalGate, authoritative lifecycle integration, REBALANCE and safe termination remain missing.
- Repair-headroom and timing decisions belong to later focused contracts; any behaviorally meaningful ambiguity must be resolved then.
- Existing gateway signing currently takes one market-level tick. Later execution integration must inspect how to preserve per-token dynamic metadata without changing FL behavior unnecessarily.
- Legacy RPC/merge paths remain excluded from approved RDP runtime dependencies.
- Required CLI, metrics and final integrated readiness review remain outstanding.

These findings do not block pure NORMAL candidate construction.

## Human Approval Required

NONE.

This iteration directly implements frozen NORMAL formulas without changing trading policy, shared core or external interfaces. Real-money trading remains unauthorized.
