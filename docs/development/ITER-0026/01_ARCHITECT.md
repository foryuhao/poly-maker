# ITER-0026 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `071e1bef3b686e39019d2ffb50689a4ba3145e3a`, matching `origin/rdp-phase0-dev`.
- Latest archived implementation: ITER-0025, `b0de5de89d23441b2fe6f7531ab092874678db24`.
- Final ITER-0025 Reviewer06 PASS explicitly verifies DG-001 closure.
- Current iteration: ITER-0026, ARCHITECT.
- Tracked tree clean; existing `research_outputs/` preserved.
- Inspected current contract, Frozen SOP, baseline, index, delivery register, prior RESULT/Reviewer/archive receipt and actual submission, ownership, lifecycle and tests.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

Shared-Core Change: **NO**.

FL Behavior Impact: **NONE**.

IM Dependency: **NONE**.

## Delivery Gap / Closure Target

**DG-006 — Partial/Unknown Placement and Unreliable-State Termination.**

Implement the missing runtime connection:

**Start:** The current initial submission finishes with partial/unknown/error evidence, or the operating initial cycle finishes with unreliable state.

**End:** New risk is latched off, safely registered attributable scope receives existing scoped cleanup, and one retained STOP result explicitly reports reconciliation completeness and unresolved exposure.

Target DG-006’s full fail-closed closure condition for the currently implemented runtime. Reviewer must verify the entire required failure matrix. If a condition remains unmet, keep DG-006 OPEN and identify it precisely.

Successful subsequent NORMAL/REBALANCE operation, market-wide termination and CLI composition remain separate open requirements.

## Problem

The runtime now submits and retains actual typed outcomes, but failure outcomes do not automatically lead to scoped risk closure and an actionable terminal result.

Connect those outcomes to existing cancellation/reconciliation without promoting uncertain evidence into ownership or enabling replacement.

## Evidence / Current Behavior

- `submit_initial_normal()` retains complete/partial/unknown/error evidence after SDK drain.
- Retained gateway acknowledgments are explicitly evidence, not registered ownership.
- Resolver failure can leave the owner ARMED while the result preserves accepted exchange IDs.
- `close_initial_normal_placement()` can close registered attributable partial scope.
- `close_rdp_cycle()` requires registered ownership and refuses to infer it from an observed ID.
- Initial cycle failure closes through the existing barrier, but does not produce the complete terminal no-new-risk result.
- No retained terminal latch/result consumes these outcomes automatically.

Frozen §§18, 21, 26–27 and DG-006 require safe failure handling. Unprovable state must remain blocked and reported.

## Existing Components Reused

- Retained `RDPInitialSubmissionResult` and independent gateway evidence.
- Current `RDPPlacementOwner`, registry validation and replacement blocking.
- Existing initial cycle completion and unsafe-continuation evidence.
- `close_initial_normal_placement()`, `close_rdp_cycle()` and `_StrictCloseGateway`.
- Existing processor confirmed-fill accounting and reconciliation.
- Existing shield/drain, delegation guards and session cleanup.
- Existing categorical logging.

## Existing Components Modified

- `src/polymaker/rdp_runtime.py`: terminal risk latch/result, automatic failure connection, retained task ownership, legitimate delegation and teardown.
- `tests/test_rdp_placement.py`: actual submission-to-terminal and cycle-failure integration.
- `tests/test_rdp_runtime.py` only for necessary teardown and inactive-mode compatibility.

Small RDP-local helper extraction is permitted.

No gateway, processor, strategy, capital formula, CLI, configuration or dependency changes are authorized.

## New Components Required

One immutable terminal-failure result and one retained terminal cleanup owner inside the existing runtime.

Their current necessity is the missing typed outcome → scoped closure → honest STOP connection.

No recovery subsystem, separate ledger or additional trading state.

## Desired Behavior

A partial, unknown or unresolved initial outcome automatically latches **STOP / STATE_UNRELIABLE** before cleanup.

Only IDs whose actual registered ownership matches the retained attempt may enter the existing barrier.

Merely observed IDs and retained acknowledgments that were not successfully registered remain explicit unresolved evidence. They are not adopted, registered or canceled by this iteration.

The result distinguishes:

- Safely registered scope and its cleanup evidence.
- Closed/reconciled IDs.
- Unregistered acknowledgment IDs.
- Merely observed IDs.
- Available confirmed inventory with its scope.
- Remaining uncertainty and required human attention.

A successful scoped cleanup does not prove an unknown placement outcome is fully resolved.

## Minimal Scope

1. Retain a terminal no-new-risk latch and immutable result for the current selected-market scope.
2. Connect it automatically after actual submission work drains when retained evidence is partial, unknown, resolution-error or otherwise leaves unprovable placement/fill ownership.
3. Also connect the existing initial-cycle failure result when unreliable state prevents valid continuation.
4. Preserve default denial, clean no-send/authoritative-empty and healthy successful behavior. They must not gain cancellation side effects merely from this addition.
5. Derive failure from actual retained results and current authoritative evidence. Do not accept fabricated caller evidence or arbitrary stop-reason strings.
6. Latch new-risk denial before awaiting cleanup. Existing admission, arming and submission guards must observe the latch.
7. Determine cleanup scope from current registered ownership and matching attempt economics. Preserve all other IDs separately.
8. Reuse the existing scoped close path with unsafe continuation. No capital certification, next-mode eligibility or replacement release follows terminal failure.
9. If scope is absent, invalid or still ARMED without registered attribution, perform no speculative cancellation. Return honest unresolved STOP evidence.
10. If an initial-cycle failure already attempted scoped closure, retain that evidence rather than automatically repeating cancellation/reconciliation.
11. For submission failure requiring closure, run one bounded close using the existing operational close budget. Closure timeout, uncertain cancellation, pending/failed fills or failed reads remain incomplete.
12. Preserve accepted confirmed inventory available from the existing processor. Distinguish a scoped confirmed snapshot from certified final whole-market inventory.
13. Retain one terminal owner/result. Repeated access must not repeat cleanup.
14. Integrate legitimate nested delegation while rejecting competing admission, arm, submit, cycle and close actions.
15. First/repeated cancellation and teardown drain terminal HTTP/SDK work before gateway/store cleanup. Do not detach tasks or resolve placement again.
16. Preserve original immutable submission/cycle evidence; terminal output links to it and records subsequent cleanup separately.

## Invariants

- STOP means no new risk.
- This failure path uses the existing permitted `STATE_UNRELIABLE` reason; original categorical causes remain separate details.
- No new strategy stop reason or trading state.
- Registered ownership remains the boundary for cancellation/reconciliation.
- Retained acknowledgment evidence cannot silently become registered ownership.
- Observed, foreign or manual orders are not adopted or canceled.
- No retry POST, replacement or heuristic unknown-ID recovery.
- Confirmed fills remain the sole inventory/cost authority.
- Unknown exposure is never represented as flat inventory or free capital.
- Incomplete closure cannot certify capital, release replacement or execute a next mode.
- Cleanly closed known scope does not erase unresolved placement evidence.
- Default read-only sessions gain no order-action authority.

## Acceptance Criteria

1. Actual partial/unknown submission automatically reaches retained STOP handling after SDK drain.
2. Safely registered accepted legs receive one existing scoped close; observed/unregistered/foreign IDs receive none.
3. Resolver failure before or after owner update preserves gateway evidence without a second resolve or invented ownership.
4. Unknown zero-ID outcomes perform no speculative cancellation and clearly report unresolved exposure.
5. Cancel/fill races and partial confirmed fills remain accounted once through existing reconciliation.
6. Uncertain cancel, stale/malformed reads, pending/FAILED/quarantined fills and incomplete barriers produce honest incomplete terminal output.
7. Existing unreliable cycle closure becomes terminal without duplicate cleanup.
8. All subsequent new-risk APIs/guards reject after the latch; terminal handling never retries POST.
9. Terminal result retains scope, cause, registered cleanup, unregistered acknowledgments, observed IDs, completeness and available confirmed inventory.
10. First/repeated cancellation and teardown retain and drain actual fake SDK/HTTP cleanup work.
11. Repeated terminal access is idempotent; competing runtime actions cannot overlap.
12. Healthy success, clean no-send/default denial and inactive CLI/observer behavior remain compatible.
13. Required targeted, affected, predecessor and practical full-suite checks pass.

DG-006 may be marked CLOSED only when all its failure-handling conditions are independently verified. An incomplete terminal result is expected for genuinely unprovable exchange state; concealing that uncertainty is a defect.

## Required Tests

Block/count real DNS/socket access before imports. Use existing real gateway/executor paths with fake SDK clients.

- Actual initial submit → one-leg acceptance → registered scoped cancellation/reconciliation → STOP.
- Accepted-plus-unknown response with observed IDs remaining unowned and uncanceled.
- Unknown zero-ID POST failure.
- Resolver exception before and after ownership update; preserved unregistered acknowledgments and zero second resolve.
- Complete acknowledgment combined with interrupted/unreliable continuation.
- Confirmed fills before acknowledgment, during cancel and through REST recovery; deduplication.
- Pending, FAILED, quarantined and unadmitted fill evidence.
- Uncertain cancel and stale/malformed/failing open-order/order/trade reads.
- Registry/economics contradiction and foreign selected-market orders.
- Existing initial-cycle unsafe closure reused without another cancel.
- Terminal latch denial of every new-risk entry/guard.
- First/repeated cancellation and teardown during each actual fake SDK cleanup operation.
- Cached terminal access, conflicting calls and zero retry POST.
- Healthy completion, clean empty/no-send, default denial and read-only CLI compatibility.

Run narrow terminal tests, affected runtime/placement/lifecycle/execution/accounting/capital tests, FL regressions and full suite when practical. Preserve exact commands and outcomes.

## Observability Requirements

Retain and log:

- Market, attempt and input/result provenance.
- STOP action, permitted StopReason and original failure category.
- Registered cleanup IDs, reconciled/closed IDs.
- Unregistered acknowledgment IDs and observed IDs separately.
- Barrier completeness/reason and remaining uncertainty.
- Available confirmed inventory and the exact scope/certification level.
- No-new-risk latch and replacement blocking.

Do not emit raw payloads or sensitive exception text. Do not label unresolved termination as successful reconciliation or realized final accounting.

## Live Validation

No live calls, orders or cancellations during this iteration.

After full delivery and explicit human authorization, unexpected placement/state failure must visibly stop new risk and preserve actionable evidence. Development verification is entirely fake-client integration; it does not establish live execution correctness.

## Explicit Non-Goals

- Registering retained but unregistered gateway acknowledgments.
- Heuristic unknown-order adoption or rediscovery.
- Subsequent NORMAL/REBALANCE execution.
- Full CanSafelyContinue/STOP timing policy.
- Market-end orchestration, resolution accounting or complete metrics.
- CLI execution activation.
- New shared-core/external interfaces or real-money validation.

## Risks

Registered ownership and retained exchange acknowledgment evidence differ after resolver failure. Confusing them could cancel foreign scope or manufacture inventory.

A barrier may close known legs while unknown exposure remains. Terminal output must keep those facts separate.

Nested cleanup and teardown can duplicate cancellation or close the gateway before executor work drains. Retained ownership and cached evidence must span the entire operation.

## Deferred Findings

DG-002–DG-005 and DG-007–DG-012 remain OPEN.

Unregistered acknowledgments may require later supervised reconciliation; this iteration preserves and reports them. It does not guess ownership.

Loss of evidence, cancellation outside registered scope, detached SDK work, false inventory/capital certification or continued new risk are current blockers and cannot be deferred.

## Human Approval Required

**NO additional architecture or strategy decision.**

This connects existing typed outcomes and registered scoped closure according to Frozen failure safety. It introduces no new strategy semantics or runtime interface.

Real-money authorization remains **NOT_GRANTED**.
