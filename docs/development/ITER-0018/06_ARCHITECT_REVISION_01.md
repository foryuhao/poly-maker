# ITER-0018 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

Revision01 supersedes the implementation authority in `01_ARCHITECT.md`. Original reports remain immutable. The problem remains initial-placement close composition.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `977b5739938cfeccd14ef6bfdeaf8ab3901950b7`
- Current stage: ARCHITECT_REVISION.
- Reviewed implementation changes remain in runtime, placement owner and placement tests; audit/index changes are Orchestrator-owned.
- Preserved `research_outputs/` remains untouched.
- Reviewer05: RETURN_TO_ARCHITECT; MF-01 closed, MF-02–04 outstanding.
- MICRO_LIVE_READY: NO.
- Real-money authorization: NOT_GRANTED.

**Shared-Core Change: YES** — narrowly authorize default-off sanitized diagnostics for the existing gateway methods used by close.

**FL Behavior Impact:** NONE intended. Existing method defaults, execution behavior and default diagnostic behavior remain unchanged.

**IM Dependency:** NONE.

## Problem

Complete the existing initial-placement close operation without detaching its SDK calls or duplicating gateway cancellation.

Every delegated operation must remain owned through cancellation/deadline/teardown, and RDP diagnostics must exclude raw exception context even when logging handlers fail.

## Evidence / Current Behavior

Reviewer05 independently established:

- Fresh current capital/registry validation repairs stale-proof release.
- `_StrictCloseGateway.cancel` drains cancellation executor work but duplicates gateway execution internals.
- Its read `_call` still detaches running SDK operations when canceled.
- Its safe warning is emitted while the raw SDK exception remains active. A failing standard logging handler exposes that exception through stderr.

The existing gateway already owns the client, executor, limiter, cancellation and observation events. Its derive-only SDK context suppresses raw SDK HTTP diagnostics. Reuse those mechanisms.

## Existing Components Reused

- Existing scoped close barrier and strict account reads.
- Existing gateway cancellation, executor, limiter and observation events.
- Retained session, processor, store and owned registry.
- Existing capital evaluator and repaired current-proof recording.
- Existing task-drain and cancellation-preservation conventions.

## Existing Components Modified

- `execution/gateway.py`: add a keyword-only, default-off sanitized-diagnostic option to `cancel`, `open_orders`, `get_order` and `get_trades`.
- `rdp_runtime.py`: replace copied cancellation with delegation; retain every delegated method task through interruption.
- `execution/rdp_placement.py`: retain the working fresh-proof repair. Change only if required to preserve this contract.
- Relevant placement/execution tests.

No other shared gateway operation, send gate, processor or strategy behavior is authorized to change.

## New Components Required

NONE.

Reuse the current result types, adapter and gateway. A small shared diagnostic helper and one local method-task drain helper are sufficient.

## Desired Behavior

The existing explicit close operation continues:

```text
known owned IDs
→ existing cancellation/reconciliation barrier
→ confirmed inventory
→ fresh capital/registry proof
→ proof-based closure recording
```

Its adapter delegates all four gateway operations.

For each operation, cancellation or deadline expiry retains ownership until that operation finishes. After draining, interruption propagates and the barrier starts no further operation.

RDP gateway failures produce sanitized categorized evidence. Diagnostic emission occurs after the raw exception handler has exited, with no raw cause/context attached.

## Minimal Scope

1. Remove the local cancellation mirror, including direct client/limiter/observation/executor handling and private executor-set inspection.
2. Add the default-off diagnostic keyword to the four existing gateway methods. The RDP adapter explicitly opts in.
3. Preserve each method’s existing request, limiter, paper/empty handling, observation events and success/failure semantics.
4. In opted-in failure handling, retain only safe category/type information. Emit diagnostics and construct surfaced errors outside the raw exception handler; do not retain raw chained context.
5. A diagnostic failure must not convert uncertain cancellation into success or certify a failed read. Keep the operation fail-closed and outward errors sanitized.
6. Run each delegated async method in one owned child task and await it through shielding.
7. On interruption, drain that child without canceling its underlying await of running executor work. Handle repeated cancellation, retrieve its terminal result/error and propagate interruption after draining.
8. Apply that wrapper equally to open-order reads, cancellation, order reads and trade reads.
9. Keep the close marker and replacement block until all owned operation work drains.
10. Preserve current exact-order adaptation, known-ID scoping, incomplete-result handling, fresh capital/registry validation and additive closure evidence.

Do not implement another gateway or alter `_io` cancellation behavior globally.

## Invariants

- Only safely registered IDs are cancellation targets.
- Observed-only IDs remain unowned.
- Partial/unknown placement uncertainty survives known-subset closure.
- Confirmed fills alone determine inventory/cost.
- Fresh proof validation remains mandatory at recording.
- Capital remains $30; approved reserve semantics remain unchanged.
- No operation task is detached, and no replacement is admitted during draining.
- An expired deadline cannot certify closure after a running operation returns.
- Defaults preserve predecessor behavior.
- Sanitized diagnostics contain no raw exception objects, payloads or chains.
- No global logger/handler replacement or process-wide suppression.
- No submission, live authorization, new external interface or strategy timer.
- Existing read-only commands and ordinary resource cleanup initiate no cancellation.

## Acceptance Criteria

1. Original close-composition scope remains satisfied: actual barrier, known-ID cancellation, confirmed accounting and exact NORMAL/REBALANCE boundary.
2. Current proof/registry checks continue rejecting late MATCHED evidence, reopened orders and changed submitted economics/scope.
3. The RDP adapter calls existing gateway methods; no cancellation execution mirror remains.
4. All four operation types retain actual running SDK work through first and repeated cancellation.
5. Deadline expiry retains ownership until SDK work drains, returns incomplete evidence and starts no subsequent barrier operation.
6. Session teardown waits for active close work before closing its resources.
7. Overlapping closes remain rejected throughout draining.
8. Uncertain/incomplete results retain replacement blocking; partial/unknown outcomes remain unresolved.
9. Opted-in diagnostics are sanitized under normal and failing handlers, including stderr.
10. A standard `StreamHandler` whose stream raises on write cannot expose a credential sentinel from the SDK exception.
11. Outward opted-in errors have no raw SDK cause/context; useful failure categories remain.
12. Cancellation limiter, paper/empty handling, observation events, boolean outcomes and SDK call counts match existing behavior.
13. Legacy default gateway behavior and predecessor tests remain unchanged.
14. Required tests pass with zero attempted network operations or real-money actions.

## Required Tests

Retain original iteration tests and MF-01 regressions.

Use the actual derive-only gateway executor with fake blocking SDK methods. Parameterize across:

- `get_open_orders`
- `cancel_orders`
- `get_order`
- `get_trades`

For each operation cover:

- First caller cancellation.
- Repeated cancellation while draining.
- Operational deadline expiry.
- Session teardown.
- Overlap rejection while the SDK remains blocked.
- Marker/resource ownership until the fake SDK is released.
- No subsequent barrier operation after interruption/deadline.
- Replacement block retained.

Diagnostic tests must exercise opted-in cancellation and failing reads:

- SDK exceptions containing credential sentinels.
- Ordinary diagnostic capture.
- A real standard failing `StreamHandler`, with stderr captured.
- No sentinel in output, records, result or outward exception chain.
- Useful failure category preserved.
- Handler failure cannot produce success.

Do not repair the regression by replacing the failing handler with a functioning one.

Add default-off gateway compatibility tests and delegation assertions. Use fake SDKs only; block DNS/socket connections and record attempted-network count. Run targeted, affected lifecycle/gateway/placement/runtime/capital regressions, predecessor regressions and full suite when practical.

## Observability Requirements

Retain existing attempt-close evidence and safe failure categories.

Opted-in gateway diagnostics may include operation name, exception type and scoped order count/IDs where already appropriate. No raw exception text, payload or traceback chain.

No telemetry expansion is required.

## Live Validation

All validation is fake-only. Exercise the real executor and real logging-handler failure behavior without official account calls.

No development order or cancellation is authorized. Existing CLI paths remain uninvolved.

## Explicit Non-Goals

- Submission or new send authorization.
- Full cycle/FillWindow controller.
- Unknown-ID discovery or ownership adoption.
- Subsequent NORMAL/REBALANCE execution.
- Global executor redesign or global diagnostic policy changes.
- Broad FL changes.
- Complete metrics or readiness declaration.

## Risks

- Shielding alone is insufficient if the owner stops waiting after cancellation.
- Retrieving a child failure must not replace remembered interruption with a stale success.
- Logging from inside a raw exception handler can leak context despite safe message fields.
- Default-off behavior must remain covered independently.
- A running SDK call may extend wall time beyond the operational deadline; ownership must remain explicit and no risk release may occur.

## Deferred Findings

Submission/final gates, full cycle scheduling, unknown-placement recovery, subsequent admission/execution, continuous account reliability and complete metrics remain outstanding.

## Human Approval Required

NO.

The revised boundary is an ordinary implementation decision within existing Frozen semantics and official interfaces. It restores gateway reuse and closes verified ownership/diagnostic defects without expanding trading authority.
