# ITER-0022 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `63f72b0d9d7f9731a2494df94580a1510372d74a`, matching the local remote-tracking reference.
- Latest archived PASS: ITER-0021, `a243d7ffe83a9085edcea6b027d88be068141650`.
- Inspected prior evidence: ITER-0021 `RESULT.md`, `10_REVIEWER_REREVIEW_02.md` and `11_ARCHIVE_RECEIPT.md`.
- Working tree contains only preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **NO**.

FL Behavior Impact: **NONE**.

IM Dependency: **NONE**.

## Problem

The retained initial pair has placement ownership and post-fill window closure, but no decision determining whether it remains valid before its first accepted confirmed fill.

Implement exactly one prerequisite: current pre-fill quote validity for the initial acknowledged pair.

## Evidence / Current Behavior

Frozen SOP §19 requires resting valid quotes and closing invalid quotes before requoting. Its invalidation conditions are PriceGate, Book Filter, departure from BestBid minus TickSize, stale market data, unreliable order state and market untradability.

Existing components provide:

- Safely attributed initial-pair ownership and immutable submitted quotes.
- Fresh exact-market Gamma status with current freshness validation.
- Dynamic books, Book Filter and pure NORMAL candidate construction.
- Strict exact-decimal authenticated open-order reads through the retained gateway.
- Confirmed-fill inventory, pending/quarantine checks and capital projection.
- Original-receipt FillWindow and explicit scoped close composition.

These components are not composed into a pre-fill validity decision.

## Existing Components Reused

- `RDPPlacementOwner.current` and registry validation.
- Existing fresh Gamma status helper and freshness evaluator.
- Existing strict gateway read adapter, including SDK draining and safe diagnostics.
- `snapshot_inventory`, `project_rdp_capital` and existing processor evidence validation.
- `build_normal_quotes`, dynamic books and `book_filter_result()`.
- `initial_fill_window()` for first-confirmation handoff.
- Existing read ownership, admission serialization and teardown patterns.

## Existing Components Modified

- `src/polymaker/rdp_runtime.py`: decision type, retained evaluation API and task ownership.
- `tests/test_rdp_placement.py`: actual composed read/processor/quote tests.
- `tests/test_rdp_runtime.py` only where needed for teardown and default-mode compatibility.

Small extraction of existing RDP-local read/cleanup helpers is permitted when necessary for reuse. Preserve existing admission and close behavior.

No shared gateway, Gamma, processor, strategy or capital changes are authorized.

## New Components Required

One immutable RDP pre-fill decision type and one evaluation API inside the existing runtime.

No new transport, ledger, polling controller or trading state machine.

## Desired Behavior

For the current fully acknowledged initial NORMAL pair, the API reports one of these internal actions:

- **REST:** authoritative evidence confirms an unchanged, valid pre-fill pair.
- **FILL_WINDOW:** accepted confirmed-fill evidence has started the existing reliable window.
- **CLOSE:** quote invalidation or unreliable evidence requires existing scoped closure before any next action.

These are action recommendations, not additional trading states.

The API performs necessary reads and returns evidence. It does not submit, cancel, reconcile or requote.

## Minimal Scope

1. Validate current attempt identity, complete acknowledgment scope, registered economics and retained replacement blocking.
2. Reject armed, partial/unknown, closed, foreign or already-closing attempts before reads.
3. If a reliable window already started, return its handoff decision without evaluating ordinary pre-fill quote movement.
4. Otherwise obtain fresh Gamma status and strict exact-decimal open-order evidence through existing components.
5. Capture read receipts and use existing market-status and account/order freshness limits.
6. After each await, recheck attempt/registry identity, session health, generations and market expiry.
7. Validate returned selected-market orders against registered ownership. Foreign selected orders, duplicate IDs and contradictory identities/economics cannot certify resting.
8. If healthy accepted confirmation arrived during reads, hand off to the existing window. Genuine read/state failures still require closure.
9. Without accepted confirmation, require both acknowledged orders to remain present at exact submitted price and full submitted quantity. Any unaccounted reduction or absence requires closure/reconciliation.
10. Reuse existing evidence projection, Book Filter and NORMAL builder to validate current pricing. Compare submitted quotes against current candidates using exact decimal identity.
11. Return the action, categorical reason, attempt/scope/generations, current read/status evidence, filter, available inventory/projection and window evidence.
12. Own the evaluation through cancellation and teardown; exclude overlapping evaluation or close coordination.

No periodic loop or automatic action follows the result.

## Invariants

- The existing processor remains fill authority.
- MATCHED-only, MINED, RETRYING, unadmitted, failed or quarantined evidence cannot certify an unchanged pre-fill pair.
- Only accepted confirmed evidence can establish FillWindow handoff.
- Confirmation during an await takes precedence over ordinary PriceGate, filter or quote-movement invalidation.
- System-level read/health failures do not become healthy handoff merely because confirmation arrived.
- Exact authoritative quantity reduction cannot be rounded into “no fill.”
- The current pair is checked; no replacement quote is armed or reserved.
- No fresh CapitalGate admission charges existing resting orders again.
- Gamma metadata cannot replace dynamic ticks or CLOB minimum-size authority.
- Foreign/manual orders are neither adopted nor canceled.
- Evaluation never releases placement blocking or substitutes for close proof.
- Lack of fill alone remains compatible with REST; no new resting timeout is introduced.
- Execution authorization remains fixed false.

## Acceptance Criteria

1. A healthy unchanged full pair with fresh reads and valid Frozen conditions returns REST.
2. All reads reuse the captured official Gamma path and existing authenticated gateway without reconnecting or deriving credentials.
3. PriceGate failure, Book Filter rejection or either quote departing from current BestBid minus its dynamic tick returns CLOSE.
4. Stale/backlogged public inputs, either unhealthy generation, expired market, unavailable/untradable status or unreliable order evidence cannot return REST.
5. Missing owned orders, changed token/side/price, duplicate IDs, foreign selected orders or any exact remaining-quantity reduction returns CLOSE without adopting state.
6. Accepted confirmation before evaluation or during a healthy read returns FILL_WINDOW with the original receipt deadline, even when ordinary quote movement subsequently occurs.
7. CONFIRMED-only unadmitted evidence and pending/quarantined/failed fills do not produce REST or a fabricated handoff.
8. Slow reads cannot certify stale status/order evidence. Final results contain current ages and preserve the primary failure category.
9. Registry/attempt changes during reads fail closed.
10. Overlapping evaluation and close coordination are excluded. Cancellation, repeated cancellation and teardown retain ownership until actual HTTP/SDK work drains.
11. Results perform zero create/cancel calls and cannot enable execution or replacement.
12. Existing initial admission, window/close coordination and read-only CLI behavior remain compatible.
13. Targeted, affected integration, predecessor regression and practical full-suite checks pass.

## Required Tests

Use existing fake official HTTP/WebSocket/SDK infrastructure and block/count real DNS/socket access.

- Valid REST with unchanged exact full pair.
- PriceGate boundary, each Book Filter rejection and movement/tick changes on either token.
- Missing, duplicate, foreign selected, wrong-side/token/price and malformed open-order evidence.
- Tiny exact partial reductions that would disappear through float conversion or tolerance.
- MATCHED-only, CONFIRMED-only, accepted confirmation, quarantine and FAILED evidence.
- Confirmation during delayed Gamma and order reads; ordinary book movement after confirmation.
- Read failure combined with confirmation, proving no false healthy handoff.
- Status/order freshness boundaries, delayed reads, backlog, health loss, generation change and market expiry.
- Attempt/registry mutation during reads.
- Evaluation overlap with evaluation, explicit close and window coordinator.
- First/repeated cancellation and teardown with active HTTP and blocked actual gateway executor reads.
- Zero order-create/cancel calls and existing CLI regressions.

Run narrow validity tests, affected runtime/placement/accounting/capital/lifecycle/book-filter tests, FL regressions and the full suite when practical. Record exact commands and outcomes.

## Observability Requirements

Expose reconstructable decision evidence and categorical reasons through the immutable result and existing structured logging.

Include condition, attempt, acknowledged scope, generations, read receipts/ages, action and relevant quote/filter/window evidence. Avoid raw payloads, SDK exception text and duplicate telemetry infrastructure.

## Live Validation

No live calls or trading actions are permitted during this iteration.

After complete delivery and explicit human authorization, compare pre-fill decisions with actual resting orders and books. Verify that invalidation leads through scoped Cancel → Reconcile before replacement, while accepted confirmation preserves the original FillWindow.

Fake tests establish implementation behavior, not strategy economics.

## Explicit Non-Goals

- Submission, live-send authorization or final submit guards.
- Automatic pre-fill polling, cancellation or requoting.
- Subsequent NORMAL or REBALANCE execution.
- Operational-time policy implementation.
- Unknown-placement recovery.
- New capital reservations, inventory adoption or accounting formulas.
- Shared-core changes, new interfaces or CLI execution activation.

## Risks

Confirmation can race with asynchronous reads and ordinary market movement. Final decision ordering must distinguish healthy window handoff from system failure.

Exact account quantities must remain Decimal throughout validation. Missing orders require reconciliation rather than an assumption of cancellation or fill.

## Deferred Findings

Production gated submission, automatic pre-fill action, subsequent NORMAL/REBALANCE operation, continuous reliability, unknown recovery and complete metrics remain required.

This prerequisite does not establish MICRO_LIVE_READY.

## Human Approval Required

**NO.**

The contract composes Frozen §19 conditions with existing authoritative components and preserves established confirmed-fill authority. It adds no strategy semantics or external interface.

Real-money authorization remains NOT_GRANTED.
