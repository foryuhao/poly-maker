# ITER-0006 — Architect Contract

## Status

ARCHITECT_BLOCKED

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `89659ebe51a10af9acc12b3a2fdaa9978470c948`.
- Local remote-tracking branch matches HEAD.
- Working tree: preserved untracked `research_outputs/` only.
- Current iteration/stage: `ITER-0006` / `ARCHITECT`.
- ITER-0001 through ITER-0005 are CODE_READY and archived.
- Latest successful result: ITER-0005 `09_RESULT_REVISION_01.md`.
- Latest final PASS: `08_REVIEWER_REREVIEW_01.md`.
- Archive receipt: `10_ARCHIVE_RECEIPT.md`; reviewed implementation archive: `63246bf`.
- Latest independently verified full suite: 835 passed, 2 skipped, no exclusions.
- Approved capital policy remains `ITER-0005/03_HUMAN_DECISION.md`.
- MICRO_LIVE_READY: NO. Real-money authorization: NOT_GRANTED.

Read active instructions, Frozen SOP, baseline/index, relevant prior evidence and approved human decision. Inspected actual RDP capital/accounting/quote components, official market-data callbacks, L2 books, parsers and existing estimators.

No edits, network actions or trading occurred.

## Problem

Implement the remaining NORMAL Book Filter v0 prerequisite.

Its Inactive predicate requires “aggregate depth change < 5 shares” over two seconds, but the Frozen SOP does not define the temporal change calculation. Different definitions produce different admission decisions for the same authoritative market history.

This strategy-semantic definition requires human authority before implementation.

## Evidence / Current Behavior

Frozen section 16 rejects when either side has both:

- no real trade in the last two seconds;
- aggregate depth change near BestBid/BestBid minus one tick below five shares.

Existing infrastructure supplies the necessary evidence:

- `MarketDataService` maintains token-specific L2 books.
- `on_dirty` fires after book mutations.
- `on_trade` supplies parsed official trade prints.
- Tick changes update existing token books.
- `OrderBook` provides exact levels.

No RDP Book Filter is implemented. Searches of active strategy and relevant durable architecture/decision artifacts found no approved definition of the temporal depth-change quantity.

Existing EWMA/flow estimators do not define this Frozen predicate and cannot replace it.

Two fixed-touch examples isolate the difference:

```text
Observation times: t-2.0, t-1.0, t
Aggregate depth:   20,    26,    20 shares
```

With no real trades:

```text
Absolute endpoint difference: |20 - 20| = 0  → Inactive
Window range:                  26 - 20 = 6  → not Inactive
Cumulative absolute change:    6 + 6 = 12    → not Inactive
```

A second history distinguishes range from cumulative variation:

```text
Aggregate depth: 20 → 23 → 20

Endpoint difference = 0 → Inactive
Window range = 3        → Inactive
Cumulative variation=6 → not Inactive
```

For both examples, keep BestBid and TickSize fixed, let the other token have a real trade within the window, and keep both effective depths below 50. PriceGate, CapitalGate and Fast Move can pass. Therefore the unspecified formula alone changes NORMAL admission.

This is not a choice of queue, sampling implementation or storage structure.

## Existing Components Reused

After the decision:

- Existing token L2 books and dynamic ticks.
- Existing official `on_dirty` and trade-print callbacks.
- Existing parsed `TradePrint`.
- Existing market identity and quote/capital gates.
- Existing synthetic market-data test fixtures.

No external interface or replacement orderbook is needed.

## Existing Components Modified

NONE authorized by this blocked artifact.

### Shared-Core Change

NO changes approved.

### FL Behavior Impact

NONE.

### IM Dependency

NONE.

## New Components Required

A small RDP-specific rolling Book Filter component will be required after the definition is approved. Its implementation scope belongs in a new Architect revision.

No Worker implementation is authorized yet.

## Desired Behavior

Apply the human-approved temporal depth calculation with the existing Frozen thresholds and negative-filter semantics.

Preserve:

- two-second Inactive window;
- five-share threshold;
- real-trade alternative;
- existing Depth Extreme formula;
- one-second/two-tick Fast Move requirement;
- per-token live TickSize;
- Book Filter applicability to NORMAL only.

Do not silently substitute an EWMA, activity score or additional strategy filter.

## Minimal Scope

Resolve exactly the mathematical definition of Inactive aggregate depth change over its two-second window.

Then issue a new immutable Architect revision defining one minimal Book Filter implementation. Preserve this blocked artifact.

## Invariants

- Frozen thresholds and trading states remain unchanged.
- Authoritative market books remain owned by existing infrastructure.
- Historical observer/filter state gains no order authority.
- No current-price reduction of the approved $5 repair reserve.
- No Book Filter requirement added to REBALANCE.
- No source, test or historical-data mutation while blocked.

## Acceptance Criteria

Architecture can resume when:

1. Human approval identifies the exact temporal depth-change formula.
2. Both concrete histories above have unambiguous expected Inactive results.
3. The decision is recorded durably.
4. A new Architect revision defines implementation and boundary tests.

ITER-0006 is NOT CODE_READY. Do not spawn Worker under this artifact.

## Required Tests

The revised contract must require:

- both divergent histories above;
- exact five-share boundary and just-below cases;
- either token independently triggering Inactive;
- real trade suppressing Inactive on that token;
- Frozen Depth Extreme boundaries;
- Fast Move boundaries;
- dynamic TickSize and genuine market-data callback integration;
- insufficient/unreliable history failing closed;
- unaffected FL and full-suite regression.

No tests were executed or changed in this read-only inspection.

## Observability Requirements

The eventual filter must expose the evaluated per-token depth-change quantity, trade-presence evidence, window coverage and specific rejection reason.

The approved formula must remain durable mathematical authority for later runtime and reviews.

## Live Validation

No live action is required.

After integrated readiness and human authorization, preserve market-data histories around admission decisions and recompute the approved predicate independently.

Market observation cannot replace specifying the admission predicate.

## Explicit Non-Goals

- Selecting a temporal formula without authority.
- Changing the five-share threshold or time window.
- Adding predictive models or filters.
- Full controller, CLI or metrics implementation.
- Shared-core/FL/IM changes.
- Runtime interface changes.
- Real-money activity or readiness declaration.

## Risks

- Endpoint difference can classify substantial reversible depth activity as inactive.
- Cumulative variation can exceed five through several individually small changes.
- Window range and cumulative variation disagree on such histories.
- Silently choosing one changes which NORMAL cycles run and invalidates faithful Frozen implementation.

## Deferred Findings

Rolling-history implementation, reliable reconnect coverage, Fast Move details, controller integration, runtime timing, metrics and CLI remain outstanding.

They are not bundled into this human question. The capital decision is already settled and must not be reopened.

## Human Approval Required

**Exact blocking question:**

For Inactive, is the two-second aggregate depth change:

1. `abs(last depth - depth at window start)`;
2. `max(depth in window) - min(depth in window)`; or
3. `sum(abs(consecutive depth changes))`?

The smallest required decision is to approve one formula, or provide its exact replacement, for this predicate only.

The two examples above distinguish all three. No choice is assumed or approved by this report.

After the definition is recorded, the Architect can immediately issue a focused Book Filter contract and resume Worker → Reviewer → subsequent iterations.

Program stop classification: `HUMAN_DECISION_REQUIRED`.
