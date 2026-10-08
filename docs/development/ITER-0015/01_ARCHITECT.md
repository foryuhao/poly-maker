# ITER-0015 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `74b0861ef11d1328ba1963fbf85a0f24a3ff6e3d`
- HEAD and remote match.
- ITER-0014: CODE_READY, archived at `1f4f53356c4a55e300c30567707d02fe1db9f0f1`; final Reviewer09 PASS.
- Current iteration: ITER-0015, ARCHITECT.
- Working tree contains only preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

**Shared-Core Change: YES** — expose the existing queue’s monotonic receipt time during market-frame processing, without changing transport, callbacks, queue order or research timestamp behavior.

**FL Behavior Impact:** NONE intended; FL does not consume the new receipt context.

**IM Dependency:** NONE.

## Problem

The existing NORMAL Book Filter is not connected to production market-data events. The retained RDP input session therefore cannot supply its required rolling gate to subsequent NORMAL admission.

Implement one prerequisite: bind that existing filter to the authoritative retained public feed and expose a current, fail-closed result through the input session.

## Evidence / Current Behavior

- Frozen section16 requires Inactive, Depth Extreme and Fast Move as negative NORMAL filters.
- `RDPBookFilter` already implements those predicates and the approved cumulative absolute Q_eff variation.
- ITER-0006 explicitly deferred production runtime wiring.
- `RDPMarketDataService` currently records books, deltas and dynamic ticks, but owns no filter and forwards no public trades to one.
- The shared service already provides book-mutation and parsed-trade callbacks.
- Its socket reader queues monotonic receipt time. `_consume` uses that time for lag metrics but does not expose it to ordinary production callbacks.
- RDP whole-frame validation currently covers books/deltas/ticks; selected `last_trade_price` fields pass through the more permissive shared parser.
- ITER-0006’s approved contract explicitly uses book states emitted by existing callbacks, including aggregation within one price-change message. Do not invent intermediate book states.

## Existing Components Reused

- `RDPBookFilter` and immutable `RDPBookFilterResult`.
- Existing `OrderBook`, dynamic ticks, `TradePrint` and parser.
- Shared market-data queue and callbacks.
- RDP selected-frame validation, generation tracking and fatal-error handling.
- Retained public owner and `RDPInputSession` health checks.
- Existing synthetic feed/filter fixtures.

## Existing Components Modified

- `marketdata/service.py`: narrowly scoped, read-only processing receipt context.
- `rdp_marketdata.py`: one filter instance, callback binding, selected trade validation and current-result evaluation.
- `rdp_runtime.py`: expose the retained filter result using actual owner health.
- Relevant tests.

Do not modify filter formulas, quote builders, capital modules, execution gateway, user processor, CLI modes or configuration.

## New Components Required

NONE beyond small local methods/property and tests. Reuse the existing filter; no new rolling-history implementation, parser, service or controller.

## Desired Behavior

Each valid selected book state emitted by existing infrastructure is observed by the same filter instance, in processing order, using its actual socket receipt time and generation.

Each valid selected public trade reaches that filter through the existing `TradePrint` path. It activates only its own outcome side under the already approved rolling-window semantics.

The retained input session can return the filter’s current immutable result using current authoritative books/ticks and observed input health. That result remains a Book Filter result, not authorization to submit orders.

## Minimal Scope

1. Expose the queue’s existing monotonic receipt time while `_process_raw` and its callbacks execute. Clear it in `finally`; preserve existing research receipt fields and behavior.
2. Create one `RDPBookFilter` in the RDP public service.
3. Bind existing book-mutation and parsed-trade callbacks to it. Observe only selected tokens with a valid current-generation snapshot baseline.
4. Handle tick changes explicitly because the shared tick handler does not invoke `on_dirty`. Reuse the existing tick mutation, then notify the filter from the current authoritative book when applicable.
5. Extend existing whole-frame validation to selected `last_trade_price` events before any frame mutation or filter observation:
   - Correct market/token identity.
   - Explicit supported BUY/SELL side.
   - Finite, representable price inside `(0,1)`.
   - Finite, representable, strictly positive size.
   - Reject malformed attributable selected-market trades with a sanitized category.
6. Use the existing parser after validation. Do not replace it.
7. Expose evaluation through the retained session using one monotonic clock domain, current generation/ticks and actual health. Closed, expired, failed or changed-generation owners cannot return an accepted result.
8. Preserve existing readiness APIs. Book Filter warmup or strategy rejection does not become a public-feed startup failure.

Missing or invalid receipt context must leave filter evidence unready; processing time must not substitute for delayed socket receipt time.

## Invariants

- Frozen predicates and approved total variation remain unchanged.
- Exactly five shares of variation is active; unchanged snapshots contribute zero.
- A recent public trade affects only its own side.
- Use existing callback-emitted book states; no synthetic polling samples or reconstructed intermediate mutations.
- Preserve callback order across messages in a frame.
- A quiet valid book can cover elapsed time without synthetic updates.
- Initial and changed-generation history requires existing warmup.
- Dynamic tick changes invalidate incompatible history.
- Evaluation cannot certify expired or unreliable input owners.
- Whole-frame validation precedes shared book mutation and filter observation.
- Book Filter evidence neither certifies authoritative account/order state nor grants execution authority.
- No order creation, cancellation, inventory mutation or new runtime interface.

## Acceptance Criteria

1. Production-style queue processing drives the existing filter through authoritative book and trade callbacks.
2. Queue receipt time, rather than processing completion time, determines rolling activity. Delayed processing cannot make an expired trade appear recent.
3. Multiple messages retain callback order and cumulative variation; one price-change message retains existing aggregate callback semantics.
4. Valid selected public trades reach the existing parser/filter; foreign trades do not alter selected histories.
5. Malformed selected trade fields fail before any earlier book message in the same frame mutates books or filter history.
6. Initial warmup, exact-five variation, unchanged snapshots, one-sided activity, Depth Extreme and Fast Move reproduce existing module results through real feed wiring.
7. Tick/generation changes invalidate old history and require compatible baseline/warmup.
8. Session evaluation derives reliability from retained owners and market time, rather than accepting a caller-supplied readiness boolean.
9. Unreliable, expired or closed sessions return unready/rejected evidence.
10. Existing public/account/user preflight and observation behavior remains intact. A valid but inactive market remains an observable input session.
11. No duplicate parser/history/state machine or execution action appears.
12. Required tests pass with zero attempted live network operations.

## Required Tests

Use actual shared queue consumption with controlled monotonic receipt times and fake external transport.

- Receipt context availability during callbacks, clearing after success/error, and stale-generation queue rejection.
- Existing research timestamp and market-data regressions.
- Actual book/delta/trade frames feeding the retained filter.
- Delayed processing with a trade outside the two-second window.
- Reversing depth updates totaling six/twelve, exact five, unchanged snapshots and existing aggregate-message behavior.
- Multiple-message frame ordering.
- Quiet coverage and initial warmup.
- Each side’s trade activation; foreign-event isolation.
- Missing side/identity, booleans, zero/negative size, nonfinite/unrepresentable economics and malformed selected trades.
- Whole-frame atomic rejection when a valid book precedes a malformed selected trade.
- Dynamic tick reset, reconnect/generation reset and old-generation events.
- Current session evaluation versus public/user failure, market expiry and closure.
- Existing preflight/observe-only and no-mutation regressions.

Run targeted tests, affected filter/feed/runtime/parser regressions, predecessor regressions and the full suite when practical. Block socket connections and DNS during controlled validation; record attempted-network count.

## Observability Requirements

Expose the existing immutable result and reasons through the retained session. Preserve generation, evaluation time, window coverage, Q_eff, variation, trade activity and predicate evidence already present in that result.

Malformed selected trades use categorized, sanitized existing feed-failure reporting.

No periodic logging expansion or new telemetry subsystem is required.

## Live Validation

Controlled integration fixtures must exercise:

```text
official-shaped frame
→ existing queue/receipt context
→ validated shared book/trade handling
→ existing filter
→ retained session result
```

A later safe read-only observation may inspect live filter evidence. This iteration requires no live calls, credentials or orders and makes no strategy-edge claim.

## Explicit Non-Goals

- NORMAL admission composition or order submission.
- REBALANCE Book Filter usage.
- FillWindow, order registration or cycle orchestration.
- Continuous account refresh, reconciliation or capital certification.
- Changes to thresholds, windows or approved aggregate-update semantics.
- New filters, configuration knobs, CLI modes or metrics services.
- MICRO_LIVE_READY declaration.

## Risks

- Polling current books would lose discrete variation; callbacks must supply observed states.
- Processing timestamps would falsely renew queued trade activity.
- Tick updates can bypass ordinary dirty callbacks.
- Missing-side defaults in the shared parser could incorrectly certify a malformed trade unless RDP validates first.
- A ready Book Filter could be mistaken for complete execution readiness; keep its scope explicit.

## Deferred Findings

- NORMAL candidate/gate composition remains required.
- Authoritative account refresh and current order/fill reliability remain required.
- Placement ownership, strict acknowledgments, FillWindow, close barrier and NORMAL/REBALANCE execution remain unintegrated.
- Complete required activity/accounting metrics remain outstanding.

## Human Approval Required

NO.

The contract wires an approved Frozen predicate to existing authoritative inputs. It changes no strategy semantics, trading authority or external runtime interface.
