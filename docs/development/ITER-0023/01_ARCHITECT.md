# ITER-0023 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Canonical root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `5b8f569148b3a68e491832a019a0639e11204631`, matching the local remote-tracking reference.
- Latest archived PASS: ITER-0022, `0491c17d1b0c46f3f3200e1a776fcb05799605f3`.
- Inspected `AGENTS.md`, Frozen SOP, baseline, current index, ITER-0022 Architect/RESULT/final Reviewer/archive receipt and relevant runtime, strategy and placement tests.
- Working tree contains only preserved untracked `research_outputs/`.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **NO**.

FL Behavior Impact: **NONE**.

IM Dependency: **NONE**.

## Problem

The initial acknowledged pair has separate APIs for pre-fill validity, started FillWindow completion and scoped closure. No owner connects these APIs into the completion of one current NORMAL cycle.

Implement exactly one prerequisite: an explicitly activated initial-cycle completion owner.

## Evidence / Current Behavior

Frozen SOP §§18–21 requires:

- Valid pre-fill quotes continue resting.
- Invalid pre-fill quotes pass through cancellation and reconciliation.
- Accepted confirmation starts the retained FillWindow.
- Window expiry or a system exception closes the cycle.
- Final authoritative inventory precedes any next action.

Actual `RDPInputSession` provides:

- `evaluate_initial_prefill()`, returning REST, FILL_WINDOW or CLOSE.
- `complete_initial_fill_window()`, retaining the original receipt deadline and closing once.
- `close_initial_normal_placement()`, using the existing cancellation/reconciliation barrier and capital proof.
- Retained processor ownership and replacement blocking.
- Cancellation/drain and teardown mechanisms.

These APIs currently require a caller to coordinate each action separately. The read-only CLI does not invoke them automatically.

## Existing Components Reused

- `RDPPlacementOwner` and complete initial-pair acknowledgment scope.
- Existing registry, system-health, generation and freshness checks.
- `evaluate_initial_prefill()` and its official Gamma/authenticated order reads.
- `initial_fill_window()` and `complete_initial_fill_window()`.
- `close_initial_normal_placement()`, `close_rdp_cycle()` and `_StrictCloseGateway`.
- Existing processor, confirmed-fill inventory, capital projection and close-proof release.
- Existing injected clock, sleep, polling interval, waiter cleanup and shield/drain mechanisms.

## Existing Components Modified

- `src/polymaker/rdp_runtime.py`: explicit initial-cycle completion API, retained owner/result, delegation guards and teardown coordination.
- `tests/test_rdp_placement.py`: composed cycle tests.
- `tests/test_rdp_runtime.py` only where necessary for session teardown and default-mode compatibility.

Small RDP-local helper extraction is permitted for reuse.

The RDP-local close implementation may consult the retained cycle interruption context after the barrier and before capital certification/release. This is necessary to prevent cancellation arriving during closure from certifying a subsequent action.

No shared gateway, Gamma, processor, strategy, capital formula, configuration or CLI changes are authorized.

## New Components Required

One immutable initial-cycle completion result and one retained asynchronous owner inside `RDPInputSession`.

No new transport, ledger, trading state or general multi-cycle controller.

## Desired Behavior

An explicit API completes the current fully acknowledged initial NORMAL pair:

1. Evaluate pre-fill validity.
2. On REST, wait using the existing polling interval and evaluate again.
3. On FILL_WINDOW, delegate to the existing started-window coordinator.
4. On CLOSE, delegate to the existing scoped close barrier.
5. Return one immutable completion result containing final closure evidence.

The API never submits, requotes, arms another attempt or executes the reported next mode.

## Minimal Scope

1. Accept the exact current complete initial-pair attempt and a finite positive operational close budget.
2. Reject invalid scope, armed/partial/unknown placement, registry contradiction, previously active competing coordination or invalid budget before taking an order action.
3. Retain one cycle task from activation through final cleanup. Concurrent activation must not start another owner.
4. Reuse the pre-fill API for decisions; do not duplicate its reads or validity calculations.
5. REST has no elapsed-time or zero-fill stop. Poll with the existing interval, bounded by remaining market time, and support interruption wakeup.
6. Before acting after an await, recheck scope, system reliability and current accepted-fill evidence. Healthy confirmation takes precedence over ordinary quote invalidation. An existing read/state failure remains a failure.
7. Delegate FILL_WINDOW once to the existing coordinator, preserving its original deadline and failure semantics.
8. Delegate pre-fill CLOSE once to the existing close API:
   - Ordinary PriceGate, ready Book Filter rejection or valid quote-price departure may use the existing healthy close proof.
   - Unreliable reads/evidence, health loss, scope contradiction, cancellation, teardown or market end must retain the existing unsafe-continuation restriction.
   - Do not classify an arbitrary reason string as healthy. Require the corresponding reliable decision evidence and current checks.
9. Preserve exclusive ownership across nested pre-fill, window and close tasks. Permit only the owner's legitimate delegates; competing external calls must fail closed throughout its lifetime.
10. Caller cancellation or teardown signals the owner, wakes waits and drains actual HTTP/SDK work before cleanup. Do not detach reads, cancel blocking executor work or initiate duplicate closure.
11. Recheck retained interruption context after awaited closure and before certification/release. Interruption during cancellation/reconciliation must not produce a healthy next-action proof.
12. Retain the final result. Repeated access for the same completed scope must not repeat cancellation or reconciliation.

## Invariants

- Exactly one current cycle owns its acknowledged order scope.
- Inventory changes only through the existing accepted confirmed-fill authority.
- Cancel → Reconcile → Update Inventory remains mandatory.
- Foreign/manual orders are neither adopted nor canceled.
- The original FillWindow deadline never moves.
- Ordinary book movement cannot reprice a started window.
- Lack of fill alone cannot cause closure.
- Operational close budget is not a resting-duration limit.
- Failed or incomplete closure retains replacement blocking.
- Unsafe continuation preserves available confirmed inventory but cannot certify capital, report an eligible next mode or release ownership.
- A next-mode result is descriptive; it grants no execution authority.
- Execution authorization remains fixed false.
- Existing read-only modes gain no order-action authority.

## Acceptance Criteria

1. A valid zero-fill pair survives repeated REST evaluations without cancellation or a newly invented timeout.
2. PriceGate failure, ready filter rejection and quote-price departure lead to one scoped close using existing barrier evidence.
3. Accepted confirmation before activation, during reads or during resting hands off to the existing original-deadline window.
4. Confirmation combined with a genuine read/state failure leads to unsafe closure rather than healthy handoff.
5. Started-window ordinary movement does not trigger pre-fill reads, repricing or another cycle.
6. Healthy completed closure exposes authoritative inventory and the existing NORMAL/REBALANCE classification at `abs(Delta) = 5`.
7. Staleness, generation changes, pending/unreliable fills, untradability, market end or scope contradiction cannot permit continued healthy resting or release.
8. Partial fills and cancel/fill races are incorporated through the existing reconciliation authority.
9. Concurrent cycle activation and competing pre-fill/window/close calls cannot overlap delegates or cancel twice.
10. First/repeated caller cancellation and teardown drain retained work; interruption during closure prevents capital certification and replacement release.
11. Failed waits and delegated-operation failures produce sanitized retained evidence without unhandled task exceptions.
12. Completion performs zero order-create calls and never arms another attempt.
13. Existing explicit APIs and default CLI behavior remain compatible when the new owner is inactive.
14. Required targeted, integration, predecessor regression and practical full-suite checks pass.

## Required Tests

Use existing fake official HTTP/WebSocket/SDK infrastructure and block/count real DNS/socket access.

- Multiple valid REST iterations followed by ordinary invalidation; prove no zero-fill timeout.
- PriceGate, each ready Book Filter rejection and either-token quote movement.
- Accepted confirmation before activation, during delayed reads and while resting.
- Confirmation combined with failed reads or unreliable evidence.
- Configured and default FillWindow deadlines, ordinary movement and natural partial/full completion.
- Healthy closure with final Delta below, at and above the five-share boundary.
- Confirmed fills arriving during cancellation/reconciliation.
- Stale status/order/public evidence, public/user generation changes, market end and registry mutation.
- Concurrent activation and external pre-fill/window/close overlap while resting and during each delegate.
- First/repeated cancellation and teardown during resting, actual mocked Gamma HTTP, blocked actual gateway executor reads, FillWindow waiting and each close SDK operation.
- Cancellation arriving after closure starts but before certification/release.
- Failed sleeper/delegate outcomes; no unhandled task failures or raw sensitive exception text.
- Cached completion access; no second cancellation or reconciliation.
- Zero creates, no new attempt, foreign orders untouched and inactive/default-mode regressions.

Run narrow cycle tests, affected runtime/placement/lifecycle/accounting/capital/filter tests, FL regressions and the full suite when practical. Preserve exact commands and outcomes.

## Observability Requirements

Use an immutable result and existing structured logging to expose:

- Market/condition, attempt and acknowledged scope.
- Completion trigger and last relevant pre-fill decision.
- Original window deadline when applicable.
- Existing close/barrier result, final inventory, capital certification, replacement blocking and descriptive next mode.
- Execution authorization fixed false.

Record activation and completion. Reuse existing decision/window/close logs; do not introduce broad telemetry infrastructure.

## Live Validation

No live network reads, order creation or cancellation are permitted during this iteration.

After complete delivery and explicit human authorization, verify one actual cycle follows resting → accepted confirmation or invalidation → scoped cancellation → reconciliation → final inventory. Compare the original receipt deadline and final confirmed quantities with durable output.

Fake validation establishes implementation behavior, not live execution or strategy economics.

## Explicit Non-Goals

- Production submission or live-send authorization.
- CLI execution activation.
- Final submit guards or new capital reservations.
- Requoting or another NORMAL cycle.
- REBALANCE execution.
- Unknown-placement recovery.
- New timing policy, filters or stop conditions.
- Full delivery metrics or strategy optimization.
- Shared-core changes or new external interfaces.

## Risks

Nested ownership can incorrectly exclude legitimate delegates or permit competing callers between delegates. Retained task provenance must cover the whole cycle lifetime.

Cancellation can arrive during a blocking read or an already-started close. Ownership must drain the operation and preserve the interruption restriction through certification.

An ordinary invalidation must not conceal unreliable evidence or override a genuine read failure merely because confirmation also arrived.

## Deferred Findings

Gated submission, final submit checks, subsequent NORMAL/REBALANCE operation, unknown-placement recovery and complete required metrics remain necessary.

This iteration completes one already acknowledged initial cycle; it does not establish MICRO_LIVE_READY.

## Human Approval Required

**NO.**

The contract connects existing implementations of Frozen §§18–21 without changing strategy semantics, approved capital/depth decisions or external interfaces.

Real-money authorization remains NOT_GRANTED.
