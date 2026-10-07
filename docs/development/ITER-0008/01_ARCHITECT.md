# ITER-0008 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD and remote-tracking branch: `fc78fb17628c64c2129a25ded08d8ad2ae5db977`.
- ITER-0007 PASS archived at `864a39c42af64072d60bdedc2d51dfeaf0d6288b`.
- Inspected AGENTS.md, Frozen SOP, baseline, index, ITER-0007 result, final review, archive receipt and relevant source/tests.
- Working tree contains only preserved untracked `research_outputs/`.
- Previous full-suite evidence: `913 passed, 2 skipped`; subsequent tests-only repair passed all 72 execution tests. These are prior evidence.

## Problem

The gateway signs every quote with one static `MarketMeta.tick_size`. RDP quotes use independently current YES and NO ticks. The existing gateway therefore cannot faithfully sign a NORMAL pair with different token ticks or a REBALANCE quote using an updated deficient-side tick.

Solve only the per-token tick handoff into existing placement.

## Evidence / Current Behavior

- Frozen SOP requires dynamic `TickY` and `TickN`.
- `MarketDataService` already maintains a separate `OrderBook.tick_size` for each token and processes official tick-change events.
- RDP quote builders already receive separate ticks.
- `ExecutionGateway.place()` constructs one `PartialCreateOrderOptions` before looping through quotes, using `meta.tick_size`.
- The installed official SDK uses its selected tick for price rounding and signing precision. It also checks supplied ticks against its cached minimum tick.
- ITER-0007 provides strict two-order acknowledgment handling without changing signing metadata.

## Existing Components Reused

- Existing gateway placement, signing and official batch POST.
- Existing `PartialCreateOrderOptions`, GTC payload construction and SDK signing.
- Existing quote and market domain types.
- Existing pre-submit/final-submit guard.
- Existing strict acknowledgment and incomplete/unknown exceptions.
- Existing fake-client execution tests.
- Existing market-data ticks and RDP builders as the eventual input producers.

## Existing Components Modified

- `src/polymaker/execution/gateway.py`.
- `tests/test_execution.py` or one focused execution test file.

**Shared-Core Change: YES.** Only the opt-in local extension specified here is authorized.

**FL Behavior Impact:** NONE for existing callers; omission of the new option preserves the current signing path and defaults.

**IM Dependency:** NONE.

## New Components Required

A small gateway validation helper for an optional per-token tick mapping. No new subsystem, SDK, dependency or metadata service.

## Desired Behavior

Add an optional keyword argument to `place()` carrying ticks keyed by quote token ID. Its exact name may be chosen locally.

When supplied:

1. Require strict placement, forced post-only and a callable existing submit guard.
2. Accept either one BUY quote for a market outcome or a valid two-order pair using the existing pair opt-in. Reject other shapes before signing.
3. Require mapping keys to match the submitted quote tokens exactly. Values must be finite positive numeric ticks; booleans and strings are invalid.
4. Require each tick to be representable by the installed SDK’s supported signing precision. Unsupported ticks reject explicitly; never substitute another tick.
5. Validate each quote price against its own tick using decimal arithmetic: finite, strictly inside `(0,1)` and exactly on-grid. Reject rather than round.
6. Copy the mapping and quote sequence before the first await so caller mutation cannot change the validated signing request.
7. Construct signing options separately for each quote, using its copied token tick and existing `meta.neg_risk`.
8. Preserve exact input price and size. Do not normalize, clip or reprice a quote.
9. Retain both existing submit-guard checks, including the final check after all signing and immediately before POST.
10. Apply the same validation in paper mode without contacting a client.

The caller’s guard remains responsible for verifying that authoritative market state, tick values and connection generation still match the captured request. This iteration establishes and tests that handoff; it does not certify market-data provenance itself.

SDK cached-minimum disagreement or signing failure must produce no POST. Do not mutate private SDK caches or bypass SDK validation.

## Minimal Scope

Implement the optional per-token signing handoff and its tests.

Retain the legacy market-level signing option when the new mapping is omitted, including existing pair acknowledgment tests. No production caller is wired in this iteration.

Invalid new-mode arguments raise an explicit validation error before signing or posting. Preserve existing handling and observability of SDK signing failures.

## Invariants

- Frozen strategy prices, sizes and gates remain unchanged.
- Different token ticks remain independent.
- No fallback to `meta.tick_size` occurs for a supplied mapping.
- No silent rounding repairs an invalid quote.
- No POST occurs after guard denial or signing failure.
- Strict partial/unknown acknowledgment handling remains intact.
- No automatic retry, cancellation or inventory mutation.
- Existing minimum-size checks remain active.
- Official runtime interfaces and default FL behavior remain unchanged.
- No real-money actions during development.

## Acceptance Criteria

1. A guarded opted-in pair with different YES/NO ticks passes each corresponding tick to SDK signing in request order.
2. A guarded single deficient-side quote uses only that token’s supplied tick.
3. Two successive calls after a tick change use the new supplied tick rather than static market metadata.
4. Invalid mapping keys, numeric values, unsupported ticks, off-grid prices, flags, guard or quote shape fail before signing/POST.
5. Mapping or list mutation during an await cannot alter the captured signing request.
6. A tick change while signing, detected by the final guard, produces zero POSTs.
7. SDK cached-tick disagreement/signing failure produces zero POSTs and does not retry or alter ticks.
8. GTC, forced post-only, minimum-size protection and strict acknowledgment behavior remain operational.
9. Paper mode validates the new inputs and invokes no client.
10. Existing callers and default signing/parser tests pass unchanged.

## Required Tests

Use fake clients and controlled callbacks; no live actions.

- Distinct token ticks with static `meta.tick_size` deliberately different.
- Single YES and single NO placement with supplied token tick.
- Successive tick changes.
- Missing/extra/foreign mapping keys; duplicate/foreign quote tokens.
- Zero, negative, NaN, infinity, boolean, string and unsupported ticks.
- Off-grid and invalid prices.
- Missing strict/post-only flags or submit guard.
- Invalid single/pair shapes.
- Mutation during rate-limit await.
- Tick change during signing and final guard denial.
- SDK signing rejection, including cached-minimum disagreement.
- Minimum-size denial, forced post-only and GTC payload preservation.
- Partial/unknown pair acknowledgment propagation with the new option.
- Paper validation and unchanged default behavior.

Run focused tests, execution tests, affected RDP quote/lifecycle and predecessor regressions, then the full suite when practical. Record exact commands and results. Inspect status, untracked files, diff stat and `git diff --check`.

## Observability Requirements

Retain existing placement journaling and acknowledgment hooks.

Validation failures must identify the offending token or input category without exposing credentials or signed payloads. Existing signing-failure evidence remains visible. No additional telemetry subsystem.

## Live Validation

No live POST or cancellation is authorized.

Later supervised runtime evidence must connect the authoritative per-token tick snapshot, constructed quote, signing option and submit-guard outcome. This iteration verifies the handoff through fixtures; it does not establish integrated readiness.

## Explicit Non-Goals

- CLI/controller integration.
- New market-data or metadata readers.
- Tick provenance tracking or SDK cache-refresh policy.
- Per-token minimum-size metadata.
- Signed quantity/accounting redesign.
- NORMAL/REBALANCE lifecycle policy.
- Changes to approved reserve or Book Filter definitions.
- Private SDK cache manipulation.
- Live trading.

## Risks

An SDK cache disagreement can block an otherwise valid current tick. The safe result is no POST; public refresh/recovery integration remains deferred.

Supplying ticks alone does not establish freshness. The eventual controller must provide a guard that verifies captured authoritative state.

## Deferred Findings

- Runtime composition must source certified per-token metadata and enforce generation/tick consistency.
- A public, official-interface SDK cache refresh may be required if actual tick transitions expose stale cached minima.
- Existing market-level minimum-size metadata and SDK quantity precision require subsequent integration verification.

## Human Approval Required

NO.

This bounded signing handoff uses the existing official gateway and preserves Frozen semantics and default predecessor behavior. No new runtime interface or real-money authority is required.
