# ITER-0025 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 0  
MUST_FIX = 2

DG-001 remains **OPEN**. Actual initial submission is connected, but its absolute deadline can expire before a permitted POST, and an exception before ownership resolution discards the already received acknowledgment evidence. These are ordinary implementation defects within the approved contract.

## Reviewed Inputs

- `AGENTS.md`, including Delivery Convergence.
- `docs/strategy/RDP_PHASE0_FROZEN.md`.
- `docs/development/BASELINE.md`.
- `docs/development/DELIVERY_GAPS.md`, specifically DG-001 and its closure conditions.
- `docs/development/ITERATION_INDEX.md`.
- `docs/development/ITER-0025/01_ARCHITECT.md`.
- `docs/development/ITER-0025/02_WORKER.md`.
- Actual runtime, gateway, placement owner, lifecycle integration and modified tests.

The approved contract permits the bounded, default-off shared gateway change. It requires one guarded initial submission, actual SDK drain, retained typed outcomes, and exactly one ownership resolution. It does not authorize CLI activation, subsequent cycles, strategy changes or real-money execution.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `d8cddca33fb595d7f6be1874af16a07fb3c70890`

Reviewed implementation hashes:

```text
01321c410a842adef8ad825d1be4f6ad1e03a647d62cbf43cbf89f3548b4e0ef  src/polymaker/execution/gateway.py
64f025846274cbfbcbac92175147027b85b3db022b17daf9728bd38be1c93564  src/polymaker/rdp_runtime.py
24609cacf882ec70daf6cdb540cbfdde943eec880a4cc4b4ab28c8aa0f066cda  tests/test_execution.py
4067a2aff24de7683c87b19cbaa2e22b8b581f2dfe68c0a69ac0d9286760de0f  tests/test_rdp_placement.py
```

The implementation diff contains four files, 1,291 insertions and 24 deletions. The separately modified iteration index belongs to Orchestrator workflow administration.

Untracked inspection found 748 preserved `research_outputs/` files and the current Worker report and Reviewer handoff. Those research files were not modified or adopted into review scope.

I made no repository edits, Git history changes or live requests.

## Acceptance Criteria Review

| Contract condition | Assessment |
|---|---|
| Default and invalid authorization deny before arming/signing/POST | Verified by actual branch and tests |
| Current session-issued admission and operation exclusivity | Implemented; existing provenance and concurrent-action tests pass |
| Finite positive budgets and strict initial market-time comparison | Implemented and tested |
| Both actual gateway guard locations enforce operational time | **Not satisfied: MF-002 permits POST after the absolute deadline** |
| One strict post-only pair using captured ticks and Frozen economics | Implemented through the existing gateway; fake integration passes |
| Pre-ack confirmation replay and confirmed inventory | Implemented through the existing processor; relevant tests pass |
| Complete/empty/partial/unknown/error evidence survives resolution failure | **Not satisfied: MF-001 loses actual acknowledgment evidence before owner update** |
| Categorical opted-in failures and raw exception isolation | Reviewed and covered by real failing-handler test |
| Cancellation, repeated cancellation, teardown and deadline drain | Normal tested paths pass; MF-001 also requires resolution-error teardown coverage |
| No retry, automatic cycle or CLI authority expansion | Main submission path complies; teardown must preserve the exactly-once resolution invariant |
| Legacy and FL compatibility | Independent affected regression passes |
| Tests prove complete DG-001 closure | **Not satisfied: current tests miss or accept both defects** |

This iteration advances the real runtime behavior from admission-only helpers to a default-denied, retained initial placement operation using the existing gateway. That progress does not yet meet DG-001’s full closure condition.

## Existing Codebase Compatibility

Worker reused the existing gateway, strict positional pair parser, per-token signing options, processor pending capture, placement owner and shield/drain mechanism.

I found no duplicate gateway, separate application, unnecessary replacement subsystem or imported IM dependency.

The gateway’s new options default off. The legacy generic failure regression preserves its historical empty return and diagnostic behavior. Independent tests include Favorite First/live and Engine.

`on_post_start` has a current necessity: distinguish an aborted submission from a potentially accepted POST when cancellation, timeout or an exception occurs. Its existing runtime callback is a `threading.Event.set`; the extension does not replace placement or parsing.

## Findings

### BLOCKER

None classified as BLOCKER.

The two MUST_FIX findings prevent PASS and DG-001 closure.

### MUST_FIX

#### MF-001 — Actual Gateway evidence is lost when resolution raises before updating ownership

Locations:

- `src/polymaker/rdp_runtime.py:1355`
- `src/polymaker/rdp_runtime.py:1398`
- `src/polymaker/rdp_runtime.py:1422`
- `src/polymaker/rdp_runtime.py:3577`
- `test_submission_keeps_actual_owner_state_when_resolve_raises`

The SDK outcome exists only in the local `gateway_outcome`. The retained result contains the original attempt and the placement owner’s current state, but no independent sanitized snapshot of the actual Gateway acknowledgment.

If `resolve()` raises before updating the owner:

1. A real fake-SDK POST has already returned accepted `yes-order` and `no-order`.
2. The owner remains `ARMED`, with empty acknowledgment and observed-ID fields.
3. The result becomes `RESOLUTION_ERROR`, retaining that same empty `ARMED` state.
4. Neither the result, owner nor processor retains the returned IDs.
5. The local Gateway outcome disappears when the operation returns.

The existing before-update test explicitly accepts this state without checking preservation of the actual Gateway evidence.

Independent offline reproduction produced:

```text
PROBE_ACTUAL_ACCEPTED_IDS ['yes-order', 'no-order']
PROBE_RETAINED_IDS ()
PROBE_OWNER_OUTCOME ARMED
PROBE_NO_TYPED_OUTCOME_FIELD True
PROBE_LOCAL_OWNED_IDS ()
PROBE_BLOCKED_NETWORK []
```

The result log also reported empty acknowledgment and observed IDs despite the accepted response.

This violates the contract’s retained outcome and reconstructability requirements. Replacement remains blocked, which is appropriately conservative, but blocking does not substitute for retaining known exchange evidence.

There is a related teardown concern: when this failed resolution leaves an `ARMED` owner after submission completion, `aclose()` calls `placement_owner.resolve()` again with `CancelledError`. That retries resolution and replaces the available evidence with interruption evidence, contrary to this operation’s exactly-once resolution invariant.

**Required correction**

Retain a sanitized, immutable representation of the actual typed Gateway outcome before attempting ownership resolution. Preserve the distinction between Gateway-attributed acknowledgments, observed IDs and successfully registered authoritative ownership.

Do not bypass the existing resolver, automatically register unresolved evidence, release replacement blocking, infer confirmed inventory, or authorize trading because the acknowledgment has been retained.

A resolution exception must preserve the actual owner and the separate Gateway evidence. Teardown must not silently perform a second resolution for the same submission.

**Required verification**

Exercise complete, partial and observed-unknown outcomes with resolver failure before and after owner update. Assert:

- Actual Gateway evidence remains reconstructable.
- Attributed and observed IDs remain distinct.
- Existing owner state is preserved.
- Unproven registration is not treated as authoritative ownership.
- Resolution is attempted once, including teardown.
- No retry, foreign adoption, automatic cancellation or new risk occurs.
- Evidence survives caller interruption and sanitized logging.

#### MF-002 — A delayed bridge publication lets the final guard permit POST after the absolute deadline

Locations:

- `src/polymaker/rdp_runtime.py:995`
- `src/polymaker/rdp_runtime.py:1079`
- `src/polymaker/rdp_runtime.py:1080`
- `src/polymaker/rdp_runtime.py:1082`
- `src/polymaker/rdp_runtime.py:1450`
- `src/polymaker/execution/gateway.py:433`

The operation deadline is checked during owner-loop factual capture. The result is subsequently retained and synchronously logged before the bridge completion event wakes the SDK thread.

If that retention/logging step stalls, the deadline can expire after the check and before the worker receives permission to POST. The timeout-owner coroutine cannot revoke permission while the owning event loop is blocked.

I reproduced this with the actual gateway executor and fake SDK by adding a 0.3-second delay to the second `_retain_intent_validation()` call, using a 0.2-second order budget. No factual data or strategy value was changed.

All three runs posted after the absolute deadline:

```text
deadline=2035197.429606525
POST=2035197.609645375
guard_reasons=(None, None)
interruption_reason=ORDER_OPERATION_DEADLINE

deadline=2035197.816575383
POST=2035197.918241313
guard_reasons=(None, None)
interruption_reason=None

deadline=2035198.124768369
POST=2035198.226510297
guard_reasons=(None, None)
interruption_reason=None

PROBE_BLOCKED_NETWORK []
```

The late POSTs were approximately 0.180, 0.102 and 0.102 seconds after the deadline. In the latter two runs, the result did not even record deadline interruption.

This violates the approved absolute deadline and pre-POST guard requirements. A timeout coroutine is not a sufficient gate when its event loop can be delayed.

**Required correction**

Keep mutable factual capture on the owning event loop. Recheck operational permission at the completed guard boundary immediately before the SDK can POST, using the explicit absolute deadline and reliable time evidence. Logging or bridge publication must not extend previously checked permission.

Do not weaken freshness checks, move mutable processor/book reads into the SDK thread, cancel blocking SDK work, or reinterpret this budget as a strategy timeout.

**Required verification**

Use the actual executor path and delay result retention/publication across the operation deadline, particularly at the second guard. Assert zero POST after expiry and correct denial evidence.

Cover applicable first-guard behavior, exact equality, invalid clocks and sufficient-market-time checks. Preserve the existing late-ack tests where POST genuinely began before expiry: those must still drain and retain the actual outcome.

### NOTE

- The implementation preserves conservative blocking for generic pre-POST failure by resolving `PlacementNotAttempted` as uncertainty. This does not falsely prove a clean empty result.
- The two skips in Worker’s full suite are existing skips, not new exclusions.
- DG-002–DG-012 remain outside this iteration. Neither current finding is an optional deferred feature.

## Frozen SOP Compliance

The changes preserve the existing Frozen NORMAL quote builder, dynamic tick inputs, minimum-size validation, Book Filter, $30 capital model and approved $5 future repair reserve.

No REBALANCE pricing, accounting formula, inventory threshold, strategy filter or hard rebalance duration was added.

The two findings concern existing safety and lifecycle requirements. Fixing them requires no new strategy semantics or human decision.

## External Interface Review

No external runtime interface or dependency was added.

Placement continues through the existing official Polymarket SDK gateway. Tests and probes used fake clients with network access blocked before imports.

No blockchain RPC, third-party runtime service or IM dependency was introduced by this diff.

## Trading Safety Review

Positive findings:

- Authorization must be exactly `True`; default is denied.
- Factual evidence objects remain `execution_authorized=False`.
- Initial capture is armed before delegation.
- Both existing gateway guard sites remain present.
- Submission is exclusive against admission, arm, resolve, pre-fill, window, cycle and close actions.
- Cancellation and teardown retain the child task rather than canceling blocking SDK work.
- Opted-in error diagnostics are categorical and stripped of raw SDK chains.
- The real failing `StreamHandler` test preserves typed POST uncertainty.
- No CLI send activation was introduced.
- No real-money order or cancellation occurred.

MF-002 allows expired permission to initiate new risk. MF-001 loses known outcome evidence needed for safe reconciliation. Both must be repaired before PASS.

## Inventory / Capital Accounting Review

The diff does not alter inventory or capital formulas.

Normal successful ownership resolution continues to replay processor capture and use confirmed fills for economic inventory. The pre-ack test injects MATCHED followed by CONFIRMED and asserts one economic fill.

MF-001 must not be repaired by turning retained Gateway acknowledgments into confirmed inventory or by claiming registration succeeded. The retained outcome evidence and authoritative accounting state must remain distinct.

No new capital reservation subsystem or subsequent-order capital behavior was introduced.

## State Machine Review

The submission operation stops at retained placement evidence. It does not automatically activate FillWindow completion, cancellation, another NORMAL cycle or REBALANCE.

Existing cycle methods are excluded while the submission owner is active.

The requested state-machine scope is therefore bounded correctly, but resolution-error teardown and expired final-guard permission remain defective as described above.

## Test Review

All independent pytest checks and probes installed a DNS/socket guard before importing pytest or project modules. The inspected `/tmp/rdp_phase0_network_guard.py` matches the source preserved in Worker02.

Independent current-tree regression:

```bash
uv run python /tmp/rdp_phase0_network_guard.py -q \
  tests/test_rdp_placement.py tests/test_execution.py \
  tests/test_rdp_runtime.py tests/test_rdp_lifecycle.py \
  tests/test_rdp_account.py tests/test_rdp_capital.py \
  tests/test_favorite_first.py tests/test_favorite_live.py \
  tests/test_engine.py
```

Result: **770 passed in 68.42 seconds**, exit 0, zero blocked network attempts.

Worker reports **1615 passed, 2 skipped** for:

```bash
uv run python /tmp/rdp_phase0_network_guard.py -q
```

The four reported source/test hashes match the independently inspected tree. I did not rerun the full suite after finding reproducible correctness failures; repair requires fresh appropriate checks.

Independent lint:

```bash
uv run ruff check src/polymaker/execution/gateway.py \
  src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
```

Result: passed.

I independently checked both current and HEAD versions of `tests/test_execution.py`:

```bash
uv run ruff check tests/test_execution.py --output-format concise
git show HEAD:tests/test_execution.py | \
  uv run ruff check --stdin-filename tests/test_execution.py - \
  --output-format concise
```

Each reports the same existing I001 and two E731 findings. Current line numbers are exactly 17 lines later, matching the inserted legacy regression test. The new test does not introduce those findings.

```bash
git diff --check
```

Result: passed.

Existing tests cover many composed outcome and interruption paths, but passing tests do not negate the two independent failures.

### Independent Probe Method

Both probes used the existing fixture and actual gateway executor. The common setup below was executed in standalone `uv run python - <<'PY'` processes, with the specified per-probe body.

```python
import socket
blocked = []

def deny(*args, **kwargs):
    blocked.append("network")
    raise AssertionError("network denied")

socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.getaddrinfo = deny
socket.create_connection = deny

import asyncio
import importlib.util
import tempfile
import time
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    "probe_fixture", "tests/test_rdp_placement.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
```

For MF-001, create `placement_scope.__wrapped__(Path(temp_directory), monkeypatch)`, warm it with `_prepare_real_admission()`, obtain actual session admission, and attach `ExecutionGateway(Config(), paper=False)` with `FakeBatchClient(_accepted_rows())` and `_derive_only=True`. Replace `scope.owner.resolve` with:

```python
def fail_before(*args):
    raise RuntimeError("synthetic resolve failure")
```

Then execute:

```python
result = await scope.session.submit_initial_normal(
    admission, authorization=True,
    order_budget_s=2, close_budget_s=2,
)
```

Inspect the client response, retained result, owner and processor. The accepted IDs were absent from every retained authoritative/result location, as recorded above. Drain the gateway, finalize the fixture and undo the monkeypatch.

For MF-002, use a fresh identical scope and actual admission, then install:

```python
retain = scope.session._retain_intent_validation
calls = [0]
post_times = []
post = client.post_orders

def slowed(result):
    calls[0] += 1
    if calls[0] == 2:
        time.sleep(.3)
    return retain(result)

def timed_post(*args, **kwargs):
    post_times.append(time.monotonic())
    return post(*args, **kwargs)

monkeypatch.setattr(
    scope.session, "_retain_intent_validation", slowed
)
client.post_orders = timed_post

result = await scope.session.submit_initial_normal(
    admission, authorization=True,
    order_budget_s=.2, close_budget_s=2,
)
```

Compare each `post_times` entry to `result.order_deadline_monotonic`; inspect `guard_reasons` and `interruption_reason`. Repeat with three fresh scopes, draining each gateway and restoring monkeypatches. All three actual fake POSTs occurred after their deadline, with zero network attempts.

These probes changed no repository files.

## Scope Review

The actual diff stays within the approved files and initial submission behavior.

The gateway option and POST-start callback have current integration purposes. No speculative platform, duplicate subsystem, optional strategy feature, CLI activation, configuration change or dataset duplication was found.

Both required corrections fit the current Architect Contract. Return to Worker is appropriate; architecture revision or human approval is unnecessary.

## Residual Risks

- Until MF-002 is repaired, an event-loop stall can permit a not-yet-started POST after the order-operation deadline.
- Until MF-001 is repaired, resolver failure can leave known accepted orders without reconstructable retained acknowledgment evidence.
- Teardown after a pre-update resolution failure must not retry resolution or erase that evidence.
- Offline tests do not prove real execution. Real-money authorization remains ungranted.
- DG-001 closure does not establish integrated single-market readiness; DG-002–DG-012 remain OPEN.

## Final Decision

**RETURN_TO_WORKER.**

Repair MF-001 and MF-002 within the existing contract, add meaningful regressions for both failure mechanisms, run appropriate current-tree checks, and request independent re-review.

Keep **DG-001 OPEN**. Its remaining acceptance conditions are preservation of actual typed Gateway evidence across resolution failure and prevention of expired permission from initiating POST after bridge/diagnostic delay.

MICRO_LIVE_READY: **NO**  
Real-money trading performed: **NO**  
Additional human decision required: **NO**
