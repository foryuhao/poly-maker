# ITER-0007 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Inspected HEAD: `8f0dde8abc332293236f49bbf5956ee14244bf0a`.
- ITER-0006 final reviewed implementation was archived at `6c87a39`; its result, final Reviewer PASS, archive receipt and human decision were inspected.
- Existing untracked `research_outputs/` assets are preserved.
- Current RDP capabilities include accounting, NORMAL/REBALANCE candidate construction, scoped close/reconciliation, capital projection/gating and Book Filter.
- Previous full-suite evidence: `880 passed, 2 skipped`. This is baseline evidence, not validation of this iteration.

## Problem

The existing gateway submits multiple signed orders but rejects every multi-quote response during acknowledgment parsing. Consequently, RDP’s NORMAL dual-maker candidate cannot receive usable acknowledgments through the existing execution infrastructure.

Solve only safe acknowledgment handling for one two-order RDP pair.

## Evidence / Current Behavior

`ExecutionGateway.place()` already:

- signs every supplied quote;
- submits the batch through the installed official CLOB SDK;
- preserves rate limiting, minimum-size checks and pre-submit/final-submit guards;
- supports forced post-only placement and strict unknown-outcome propagation.

However, `_parse_place_response()` rejects `len(quotes) != 1`. A successful two-order POST therefore becomes `PlacementUnknown`, with no retained acknowledged orders for subsequent scoped reconciliation.

The official batch API returns a result for each submitted order and permits individual acceptance or rejection. This makes whole-batch success assumptions unsafe. See [Polymarket Place Orders documentation](https://docs.polymarket.com/trading/place-orders#post-a-batch-of-orders).

Existing single-order tests intentionally accept legacy envelopes and rejection behavior. Preserve those defaults.

## Existing Components Reused

- `ExecutionGateway.place()` and its existing signing/POST path.
- Installed `py_clob_client_v2` batch submission.
- Existing `Quote`, `MarketMeta`, `OpenOrder`, `OrderState` and `Side`.
- Existing `PlacementUnknown` strict error propagation.
- Existing journal/research acknowledgment hooks.
- Existing fake-client placement and submit-guard tests.
- The existing scoped RDP close barrier remains the eventual consumer of acknowledged order evidence; it is not modified here.

## Existing Components Modified

- `src/polymaker/execution/gateway.py`: add an explicitly opted-in two-order acknowledgment path and exception evidence.
- `tests/test_execution.py` and/or a focused new execution test file.

**Shared-Core Change: YES.** This contract authorizes only the local gateway extension specified here.

**FL Behavior Impact:** NONE for existing callers. The new mode defaults off; existing single-order parsing, default multi-order behavior, exception messages relied upon by tests, guards and call sites remain compatible.

**IM Dependency:** NONE.

## New Components Required

Within the existing gateway module:

- A strict two-order response parser.
- A `BatchPlacementIncomplete` exception compatible with `PlacementUnknown`, distinguishing a known one-leg acceptance from a wholly successful pair.
- Optional tuple-valued evidence on placement exceptions: independently acknowledged orders and observed order IDs. Existing construction of `PlacementUnknown(message)` remains valid.

No new gateway, runtime subsystem or dependency is required.

## Desired Behavior

Add an opt-in keyword to `place()`; its exact name may be chosen locally.

The opt-in requires exactly two BUY quotes, one for each distinct token of the supplied market, together with `force_post_only=True` and `strict_placement=True`. Invalid opt-in requests fail before signing or POST.

For this mode:

1. Accept only a two-item list of per-order response mappings, attributed by their official batch result positions to the original request positions.
2. A row is accepted only when `success is True` and canonical `orderID` is a nonempty string, with no conflicting usable ID aliases.
3. A row is explicitly rejected only when `success is False` and it contains no usable order ID.
4. Two accepted rows with distinct IDs return two corresponding `OpenOrder` objects in request order.
5. Two explicit rejections return an empty list.
6. One accepted row plus one explicit rejection raises `BatchPlacementIncomplete`, carrying the acknowledged order.
7. Any unresolved row, malformed envelope, contradictory receipt, conflicting ID or duplicate accepted ID raises `PlacementUnknown`; retain independently attributable acknowledged orders where attribution is unambiguous.
8. Preserve observed usable IDs separately, including those from ambiguous receipts, as investigation evidence. An observed ID alone does not establish token attribution, ownership or permission to cancel.
9. A POST exception remains unknown, with no automatic retry.

Duplicate IDs cannot certify two different orders. Wrong-length envelopes cannot establish positional attribution.

Acknowledgment is not fill confirmation. Preserve the existing pending/live order representation; neither response status nor response amounts update RDP inventory.

## Minimal Scope

Implement and test the opt-in pair acknowledgment path in the existing gateway.

Exception evidence must survive the gateway’s strict-mode catch/rethrow path. Known partial acceptance must never return an ordinary one-order “successful pair” result.

Do not wire a runner or controller in this iteration. Do not perform cleanup actions inside acknowledgment parsing.

## Invariants

- Frozen SOP semantics remain unchanged.
- RDP NORMAL remains dual maker; acknowledgment handling does not alter prices or sizes.
- No order is retried because an acknowledgment is missing.
- No automatic cancel occurs in this iteration.
- Inventory changes only through authoritative confirmed-fill processing.
- A partial or unknown pair cannot authorize a subsequent quote.
- Existing rate limits, minimum-size checks, submit guards, GTC construction and forced post-only behavior remain active.
- Default FL placement behavior remains unchanged.
- Runtime interfaces remain the existing official Polymarket APIs.
- No real-money action is performed by development agents.

## Acceptance Criteria

1. Existing default placement tests pass unchanged.
2. Valid opt-in pair submission makes one batch POST with two GTC entries and `post_only=True`, even when configuration otherwise disables post-only.
3. Two accepted IDs produce correctly attributed orders in request order.
4. Both explicit rejections produce no acknowledged orders.
5. Either one-leg acceptance raises `BatchPlacementIncomplete` and preserves exactly the accepted order and its ID.
6. Accepted-plus-unknown responses raise `PlacementUnknown` and preserve the independently attributable accepted order.
7. Missing/invalid `success`, missing canonical ID, conflicting aliases, contradictory rejection IDs, duplicate IDs, non-mapping rows and wrong envelope lengths fail closed.
8. Ambiguous IDs are retained as observed evidence but never fabricated into attributable `OpenOrder` objects.
9. POST exceptions propagate unknown in opted-in strict mode and cause no retry.
10. Invalid opt-in request shape/flags, minimum-size rejection and failed submit guards cause zero POSTs. Guards retain their existing final check after signing.
11. Paper mode supports the valid opted-in pair without invoking a client and preserves existing default paper behavior.
12. No caller, lifecycle, accounting, quote, filter or strategy behavior changes accompany this extension.

## Required Tests

Use fake clients only.

- Successful pair, both rejection, and each direction of accepted/rejected partial receipt.
- Accepted/unknown in each position; verify exception evidence survives `place()`.
- Duplicate IDs, conflicting aliases, whitespace IDs, missing canonical IDs and rejection-with-ID contradictions.
- Missing/non-boolean `success`, malformed rows, non-list responses and short/long result lists.
- Unknown responses with observable IDs but no safe positional attribution.
- POST timeout/exception; verify one POST attempt and no retry/cancel.
- Opt-in validation: wrong count, duplicate/foreign token, SELL quote and missing strict/post-only flags.
- Minimum-size and pre-submit/final-submit guard denial; include the existing signing-race pattern.
- Forced post-only with configuration disabled, two GTC payloads and request-order attribution.
- Paper pair and existing single-order/default parser regressions.

Run in increasing scope:

1. New focused tests.
2. `tests/test_execution.py`.
3. Affected fill-test, RDP lifecycle and predecessor execution regressions.
4. Full suite when practical.

Record exact commands and outcomes. Inspect status, untracked files, diff stat and `git diff --check`; preserve unrelated assets.

## Observability Requirements

Retain existing placement journaling and acknowledgment hooks.

Incomplete/unknown exceptions must expose:

- attributable acknowledged orders;
- deduplicated observed usable order IDs;
- a reason distinguishing known partial acceptance from malformed or unknown outcome.

Do not log credentials or complete signed payloads. No telemetry subsystem is required.

## Live Validation

No live POST or cancellation is authorized.

The eventual supervised single-market runner must record both request positions, acknowledged IDs and incomplete/unknown outcomes, then use authoritative reads and the existing close barrier before further action. This iteration proves acknowledgment handling using controlled fixtures; it does not prove live batch execution or RDP readiness.

## Explicit Non-Goals

- Per-token dynamic signing metadata.
- CLI or runner integration.
- NORMAL entry orchestration, FillWindow timers or state transitions.
- Automatic partial-batch cleanup.
- Placement retry or recovery discovery.
- Changes to cancellation, reconciliation or confirmed-fill processing.
- Changes to capital gates, approved $5 reserve, Book Filter, prices or sizes.
- Support for arbitrary batch sizes or additional SDK response formats.
- FL/IM strategy changes.
- Real-money trading.

## Risks

A batch is not atomic. Even an explicitly rejected companion leaves an acknowledged order requiring later scoped close/reconciliation.

Response attribution relies on the official indexed batch result envelope. Malformed cardinality must fail closed rather than guessing.

Observed IDs are evidence, not ownership certification. Future recovery must verify authoritative account/order state.

## Deferred Findings

- Gateway signing currently uses one `MarketMeta.tick_size` for the whole batch; RDP per-token current-tick signing remains a separate prerequisite.
- A later controller must consume incomplete/unknown evidence without admitting new risk before reconciliation.
- Remaining runtime integration and required metrics are not delivered by this acknowledgment iteration.

## Human Approval Required

NO.

This is a bounded extension of the existing official execution gateway, with explicit shared-core authorization in this contract and unchanged default FL behavior. It requires no strategy decision, new runtime interface or real-money authorization.
