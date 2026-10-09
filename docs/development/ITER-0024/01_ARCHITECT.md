# ITER-0024 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `1d357fa171de7cbb3cc1d121ec2f98d1b8d821d4`, matching `origin/rdp-phase0-dev`.
- Latest archived PASS: ITER-0023, `f71ce2bc3a069d44ea158596a451d892276669f7`.
- Inspected active development contract, Frozen SOP, baseline, index, ITER-0023 RESULT/final Reviewer/archive receipt and relevant runtime, placement, gateway, capital and tests.
- Working tree contains only preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **NO**.

FL Behavior Impact: **NONE**.

IM Dependency: **NONE**.

## Problem

Initial NORMAL admission captures eligible quotes and account evidence, but no RDP runtime component revalidates that intent at the existing gateway’s pre-sign and final pre-POST guard locations.

Implement exactly one prerequisite: coherent final revalidation of the initial NORMAL intent.

This validates current facts. It does not grant human authorization or constitute the complete live-send gate.

## Evidence / Current Behavior

Frozen §§15–18 requires current PriceGate, Book Filter, dynamic TickSize, Frozen size and CapitalGate before posting.

Actual implementation provides:

- Fresh market/account admission through `evaluate_initial_normal()`.
- Initial pre-ack capture through `arm_initial_normal_placement()`.
- Existing strict pair placement with per-token signing ticks.
- Gateway guard calls after rate-budget waiting and after both signatures.
- Scoped lifecycle and one acknowledged-cycle completion.

The admission explicitly leaves `FINAL_SUBMIT_REVALIDATION` outstanding.

The gateway’s first guard runs on the event loop. Its final guard normally runs inside the SDK executor. Reading mutable books and processor state directly from that worker would not provide a coherent event-loop capture.

## Existing Components Reused

- Existing initial admission and actual captured account/market evidence.
- `RDPPlacementOwner`, initial-pair economics and processor pre-ack capture.
- Existing health, generation, backlog, expiry and freshness checks.
- `evaluate_initial_rdp_backing()`, `project_rdp_capital()` and `evaluate_rdp_capital_gate()`.
- `book_filter_result()`, dynamic books, validated CLOB metadata and `build_normal_quotes()`.
- Existing gateway submit-guard locations, strict pair acknowledgment and per-token tick signing.
- Existing structured logging and fake SDK/executor tests.

## Existing Components Modified

- `src/polymaker/rdp_runtime.py`: retain trusted initial-admission provenance, implement revalidation and the small callback adapter needed for both gateway guard locations.
- `tests/test_rdp_placement.py`: actual admission/arming/revalidation composition.
- `tests/test_execution.py` only for necessary fake-gateway integration proving both guard locations use the RDP validator.
- `tests/test_rdp_runtime.py` only for teardown/default-mode compatibility.

Small extraction of RDP-local admission checks is permitted for reuse.

No production gateway, processor, capital, strategy, CLI, configuration or dependency changes are authorized.

## New Components Required

One immutable RDP initial-intent revalidation result and one session-bound validator/callback adapter.

No new transport, reservation ledger, execution controller or trading state.

## Desired Behavior

For an initial admission actually issued by this retained session and its exact current ARMED attempt, revalidation determines whether the captured quotes and signing ticks remain valid.

Each check obtains a new coherent event-loop capture. It returns a categorical rejection or current validated evidence.

A callback compatible with the existing gateway can expose this factual result from either the event-loop thread or the SDK worker. The eventual send caller must separately enforce outstanding human authorization and operational-time gates.

No production call to `gateway.place()` is introduced.

## Minimal Scope

1. Retain the actual eligible admission and corresponding immutable account/status evidence when the session issues it. Do not accept a fabricated admission merely because its public fields say eligible.
2. Bind the validator to that admission, the exact current ARMED attempt, submitted economics, market identity and captured generations.
3. Verify the initial processor/store remains empty except for this attempt’s exact expected pre-ack capture.
4. Require the expected two BUY pending keys and remaining count. Pending events, malformed IDs, overflow, owned orders, economic fills or quarantine reject.
5. Reuse current session health, generation, backlog, expiry and book-freshness checks.
6. Recompute market-status and all account-read freshness at the current capture, using existing exact boundary semantics.
7. Reuse actual retained account evidence to recheck empty selected state and initial cash/allowance backing.
8. Recompute current Book Filter, dynamic ticks, minimum metadata, NORMAL candidate, capital projection and CapitalGate.
9. Require exact identity with the armed request’s prices, quantities and captured per-token signing ticks. Do not silently update or rebuild the send request.
10. Support both gateway callback threads:
    - Execute directly when already on the owning event loop.
    - Otherwise schedule the same synchronous capture on that loop and obtain its result through a bounded, fail-closed bridge.
    - Derive the bridge’s operational bound from existing runtime timing limits; introduce no strategy timer or configuration feature.
    - Unavailable/closed loop, bridge timeout, cancellation, teardown or evaluation failure rejects.
11. Retain each categorical result for reconstruction. A callback failure cannot fabricate eligibility.
12. Integrate cleanup for retained bridge work without changing inactive session behavior.

The callback validates only this prerequisite. Its positive result must remain explicitly distinct from complete execution authorization.

## Invariants

- Captured admission provenance belongs to the current session.
- Current mutable strategy evidence is evaluated on the owning event loop.
- The SDK worker never independently reads or mutates processor/book state.
- Revalidation performs no HTTP, WebSocket or SDK reads; it checks retained authoritative evidence and current stream state.
- Stale evidence requires a future fresh admission, not an optimistic freshness reset.
- Intent prices, sizes and signing ticks remain immutable.
- Frozen sizes and approved $5 NORMAL repair reserve remain unchanged.
- Pre-ack capture is allowed only for this exact ARMED attempt; pending fill evidence cannot certify sending.
- No inventory adoption, reservation mutation, acknowledgment resolution or replacement release occurs.
- Factual eligibility never grants human authorization.
- Existing admission/attempt/result execution authorization remains fixed false.
- No live order or cancellation action is performed.

## Acceptance Criteria

1. Actual fresh eligible admission plus its exact clean ARMED capture passes factual revalidation.
2. Fabricated, foreign, superseded or differently scoped admission/attempt evidence rejects.
3. Changes to either quote price, size, dynamic tick or validated minimum metadata reject rather than alter the request.
4. Current PriceGate or Book Filter rejection prevents validation.
5. Market/account/book freshness rejects at the exact existing limits; ages and result share the validated capture.
6. Health loss, either generation change, backlog, expiry or session closure rejects.
7. Buffered/pending economic evidence, malformed IDs, overflow, owned scope or quarantine rejects; intentional clean arming alone does not.
8. Existing backing and CapitalGate remain active, including the approved repair reserve.
9. Both real gateway guard locations obtain coherent current results through fake SDK integration.
10. State invalidation during signing produces zero fake POSTs. First-guard denial produces zero signatures and POSTs.
11. Bridge timeout, loop shutdown, repeated cancellation and teardown fail closed without detached mutable-state evaluation or unhandled task failures.
12. Revalidation makes zero production create/cancel calls and cannot enable read-only CLI execution.
13. Existing admission, placement capture, cycle completion and predecessor behavior remain compatible.
14. Required tests pass.

## Required Tests

Use existing fake HTTP/WebSocket/SDK fixtures and block/count real DNS/socket access.

- Actual eligible admission → exact ARMED capture → successful factual validation.
- Forged/equivalent copied admission, foreign session/attempt and registered scope/economics mutation.
- PriceGate boundary, ready/unready Book Filter and movement/tick change on either token.
- Minimum metadata changes, including minimum above Frozen five-share clip.
- Exact account/status/book freshness boundaries and future/invalid timestamps.
- Cash/allowance backing rejection and unchanged $5 reserve behavior.
- Clean intentional pending capture versus buffered MATCHED/CONFIRMED events, malformed IDs, overflow, quarantine and foreign local scope.
- Public/user disconnect or generation change, backlog, expiry and teardown.
- Existing gateway with fake SDK and real executor: first guard accepts, signing changes event-loop state, final guard rejects with zero POSTs.
- Valid per-token signing options and factual callback checks at both locations.
- Worker-thread callback timeout/unavailable loop and teardown while callback work is queued.
- First/repeated cancellation and cleanup; no unhandled exceptions or sensitive diagnostics.
- Zero production actions and default CLI/runtime regressions.

Run narrow revalidation tests, affected runtime/placement/execution/capital/filter tests, FL regressions and the full suite when practical. Preserve exact commands and outcomes.

## Observability Requirements

Expose current capture, market/attempt scope, generations, categorical outcome, account/status/book ages, candidate/tick identity and capital/filter evidence through the immutable result and existing structured logs.

Keep outstanding execution gates visible. Do not label factual validation as permission to trade.

No raw account payloads, credential values, SDK exception text or separate telemetry subsystem.

## Live Validation

No live network or trading actions are permitted during this iteration.

After complete delivery and explicit human authorization, verify that the final send path invokes revalidation after signing and that invalidated intent produces no POST. Correlate submitted prices and SDK signing ticks with the captured intent.

Fake tests establish implementation behavior, not live execution or economics.

## Explicit Non-Goals

- Production placement invocation.
- Human authorization or CLI live-mode activation.
- Operational-time policy completion.
- New capital reservations.
- Automatic acknowledgment handling or cycle activation.
- Subsequent NORMAL or REBALANCE operation.
- Unknown-placement recovery.
- New filters, timers, interfaces or strategy semantics.
- Shared-core changes or broad refactoring.

## Risks

The final gateway callback runs in a different thread. Evaluating mutable state there or waiting indefinitely for the event loop would undermine correctness or cleanup.

Intentional pending capture must be distinguished precisely from unexpected buffered evidence. A general “pending allowed” boolean is insufficient.

A positive factual result can be misused as complete send authority unless remaining gates remain explicit.

## Deferred Findings

Production gated submission still needs to enforce all outstanding execution gates and own the gateway operation/outcome.

Subsequent NORMAL/REBALANCE execution, unknown-placement recovery and complete required metrics remain required.

This iteration does not establish MICRO_LIVE_READY.

## Human Approval Required

**NO.**

The contract implements the already-required final revalidation prerequisite using existing Frozen conditions and gateway guard locations. It introduces no trading-semantic decision or external interface.

Real-money authorization remains NOT_GRANTED.
