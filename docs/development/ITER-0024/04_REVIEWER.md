# ITER-0024 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 0  
MUST_FIX = 1

The full suite passes, but an independently reproduced bridge deadline race violates the contract’s timeout requirement.

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- `docs/development/ITER-0024/01_ARCHITECT.md`
- `docs/development/ITER-0024/02_WORKER.md`
- `docs/development/ITER-0024/03_PAUSE_AFTER_ARCHIVE_INSTRUCTION.md`
- Actual runtime/test diff
- Existing gateway guard locations, placement owner, processor pending capture, account backing, capital projection, Book Filter, quote construction and freshness helpers

The pause instruction requires completing this iteration’s repair/review/archive workflow before pausing. It does not authorize ITER-0025.

## Repository / Diff Reviewed

Canonical root: `/home/ubuntu/poly-maker`

Branch: `rdp-phase0-dev`

HEAD: `322fa5be51ec904ae6b9d1394cbc505c683876fa`

Reviewed implementation remains uncommitted.

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `1938d6726c2c0d756241820f95df687b16ab3690653eb6ba74eadad8f18c697b` |
| `tests/test_rdp_placement.py` | `c20532897072e8f5f10e9d8f4275154e818adf0705b3bc9168627ae988111ee0` |

Diff: 1,187 insertions, 3 deletions across the two implementation/test files.

The index and untracked iteration documents are workflow artifacts. The 748 historical untracked `research_outputs/` files remain preserved.

Reviewer did not modify repository source, tests, audit files or Git history.

## Acceptance Criteria Review

| Criteria | Review |
|---|---|
| AC1: Actual admission and clean ARMED capture | Implemented and tested. |
| AC2: Admission/attempt provenance | Exact object identity and current scope are checked; copies and foreign attempts reject. |
| AC3: Immutable economics/ticks/minimums | Current candidate and metadata are compared against admitted/armed intent rather than silently replacing the request. |
| AC4: Current PriceGate/Book Filter | Recomputed using retained authoritative state. |
| AC5: Freshness and coherent capture | Status, account and book ages use the captured timestamp and existing boundary semantics. |
| AC6: Health/generation/backlog/expiry/closure | Relevant categorical checks remain active. |
| AC7: Clean pre-ack capture | Exact pending keys/count are required; buffered events, malformed IDs, overflow, owned scope and quarantine reject. |
| AC8: Backing and CapitalGate | Existing $30 backing and approved $5 reserve remain active. |
| AC9: Both gateway guard locations | Fake SDK with real executor demonstrates owner-loop evaluation at both locations. |
| AC10: Denial prevents signatures/POST | First denial produces no signatures/POST; signing-time invalidation produces no POST. |
| AC11: Bridge timeout/shutdown/cancellation | **Not satisfied.** A result arriving after the wait expires can be returned as factual-valid; a late queued callback can still capture mutable state. |
| AC12: No production create/cancel authority | No production placement invocation or CLI send activation was added. |
| AC13: Compatibility | Existing runtime/placement/predecessor tests passed independently. |
| AC14: Required checks | Suite and static checks pass, but the deadline race is absent from the current matrix. |

## Existing Codebase Compatibility

The change reuses existing account backing, capital calculations, filter, quote builder, placement capture and gateway guard locations.

It does not duplicate or replace a transport, ledger, gateway, reconciliation subsystem or application.

The local synchronous composition is justified by the requirement to evaluate retained mutable state on the owning event loop without additional reads.

No shared-core production, CLI, configuration, dependency or Frozen SOP change occurred.

## Findings

### BLOCKER

None.

### MUST_FIX

#### MUST_FIX-01 — Bridge can accept a valid result after its operational wait has expired

Locations:

- `src/polymaker/rdp_runtime.py:937`: owner-loop callback checks abandonment/closure, but no operational deadline.
- `src/polymaker/rdp_runtime.py:967`: timeout branch can return `request.validation` after `completed.wait(timeout_s)` returned false.

The timeout branch contains:

```python
if request.completed.is_set() and request.validation is not None:
    return request.validation
```

A thread interleaving can therefore reverse a timeout:

1. The worker’s real bounded event wait expires and returns false.
2. Before the worker processes that outcome, the owning loop runs the queued callback.
3. The callback evaluates current mutable state and publishes a factual-valid result.
4. The worker observes the newly completed event and returns that positive result.

No deadline is retained on the request, so the owner-loop callback cannot independently recognize that its operational time has already expired.

Independent deterministic reproduction used a wrapper around a real `threading.Event.wait(.05)`. After the underlying wait returned false, the wrapper paused the worker to expose the scheduling interleaving. The queued owner-loop callback then ran with freshly received books and otherwise valid real admission/account evidence.

Observed:

```text
TIMEOUT_RACE elapsed 0.095
wait_expired True
captures_after_expiry 1
factual_valid True
reason None
NETWORK_ATTEMPTS []
```

The operational bound was 50 ms. The callback captured mutable state after that bound and the worker accepted the resulting positive evidence.

This violates Minimal Scope 10 and AC11: bridge timeout must reject, and abandoned/expired queued work must not later evaluate mutable state or fabricate eligibility.

Required correction:

- Enforce an actual request deadline derived from the approved existing timing limits.
- Check expiry on the owning loop before starting queued mutable-state capture.
- Prevent late completion/publication from converting an expired request into factual validity.
- Preserve fail-closed teardown, unavailable-loop and abandonment behavior.
- Add deterministic regressions for a callback delivered after the real wait expired but before worker timeout handling, and for late completion publication.
- Preserve zero signatures/POSTs when either gateway guard receives this timeout rejection.

This is an ordinary implementation defect within the existing contract. No Architect revision, strategy change or human decision is required.

### NOTE

The existing timeout test resumes a held callback after the worker has already marked the request abandoned. That verifies one ordering but misses the interleaving above.

The Worker disclosed and repaired eight earlier teardown regressions. Those tests passed in the independent full suite.

No other actionable findings arose in this review.

## Frozen SOP Compliance

Current PriceGate, Book Filter, dynamic TickSize, Frozen sizes, account backing and CapitalGate are recomputed before factual validation.

The approved $5 NORMAL repair reserve remains unchanged.

The callback checks only this prerequisite; factual validity does not grant human trading authorization. No new strategy state, filter, risk rule or timing policy was introduced.

MUST_FIX-01 concerns the approved operational thread bridge, not strategy semantics.

## External Interface Review

No external runtime interface or dependency was added.

Revalidation performs no HTTP, WebSocket or SDK reads. It evaluates retained account/status evidence and current stream state using existing components.

No RPC, third-party runtime API or IM dependency was introduced.

All independent tests/reproductions installed DNS/socket guards before imports. Successful completed runs recorded zero network attempts.

## Trading Safety Review

The immutable validation result fixes `execution_authorized=False` and exposes outstanding execution gates.

No production `gateway.place()` or cancellation invocation was introduced.

Actual gateway integration with a fake SDK verifies:

- First-guard denial: zero signatures and zero POSTs.
- State invalidated during signing: zero POSTs.
- Final SDK-thread callback evaluates mutable state on the owning event loop.
- Per-token signing options remain explicit and post-only placement remains enforced.

The bridge timeout race must be corrected before this prerequisite can be accepted.

Real-money trading performed: NO.

Live order/cancellation actions performed: NO.

## Inventory / Capital Accounting Review

The validator does not adopt inventory, resolve acknowledgments, reserve capital or release replacement blocking.

It requires an empty initial processor/store except for the exact expected armed pending capture.

Existing account backing and capital projection/gate calculations are reused. Pending fills, quarantine and other unexpected local economic evidence reject.

The $30 capital assumption and $5 future repair reserve remain active.

## State Machine Review

The validator binds the actual session-issued eligible admission to the exact current ARMED attempt and captured generations.

It checks current facts without altering intent or advancing the placement/cycle state.

Acknowledgment handling, cycle activation, subsequent NORMAL operation and REBALANCE execution remain outside this iteration.

The owning event loop performs the synchronous capture. The SDK worker uses the bridge rather than independently evaluating books/processor state.

Timeout handling currently permits the late positive result described in MUST_FIX-01.

## Test Review

Independent full-suite command:

```bash
uv run python - <<'PY'
import socket
attempts = []

def deny(name):
    def blocked(*a, **kw):
        attempts.append(name)
        raise AssertionError('NETWORK_BLOCKED:' + name)
    return blocked

socket.getaddrinfo = deny('getaddrinfo')
socket.create_connection = deny('create_connection')
socket.socket.connect = deny('socket.connect')
socket.socket.connect_ex = deny('socket.connect_ex')

import pytest
result = pytest.main(['-q'])
print('NETWORK_ATTEMPTS', attempts)
raise SystemExit(result if not attempts else 2)
PY
```

Result:

```text
1581 passed, 2 skipped in 86.84s
NETWORK_ATTEMPTS []
Exit code 0
```

Independent static/tree checks:

```bash
uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
git diff --check
sha256sum src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
git status --short --branch
git ls-files --others --exclude-standard
```

Ruff and whitespace checks passed. Final hashes matched the reviewed table.

The successful deadline-race reproduction was executed with `uv run python - <<'PY'`, using the same four network guards above before this body:

```python
import asyncio, threading, importlib.util, tempfile, time
from pathlib import Path
import pytest
from structlog.testing import capture_logs
import polymaker.rdp_runtime as runtime

spec = importlib.util.spec_from_file_location(
    'review_fixture', 'tests/test_rdp_placement.py',
)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

async def probe():
    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as mp:
        fixture = t.placement_scope.__wrapped__(Path(tmp), mp)
        scope = next(fixture)
        thread = None
        try:
            t._prepare_real_admission(scope)
            admission = await scope.session.initial_normal_admission()
            attempt = scope.session.arm_initial_normal_placement(admission)
            scope.session._market_data_max_age_s = .05

            base_request = runtime._IntentBridgeRequest
            expired = threading.Event()
            resume_worker = threading.Event()
            queued = []
            captured = []

            class ExpiryInterleavingEvent(threading.Event):
                def wait(self, timeout=None):
                    observed = super().wait(timeout)
                    if timeout is not None and not observed:
                        expired.set()
                        assert resume_worker.wait(2)
                    return observed

            def request_factory(*args):
                request = base_request(*args)
                request.completed = ExpiryInterleavingEvent()
                return request

            mp.setattr(runtime, '_IntentBridgeRequest', request_factory)
            loop = asyncio.get_running_loop()
            original_schedule = loop.call_soon_threadsafe

            def hold(callback, *args):
                if getattr(callback, '__name__', '') == 'capture_on_owner_loop':
                    queued.append((callback, args))
                    return None
                return original_schedule(callback, *args)

            mp.setattr(loop, 'call_soon_threadsafe', hold)
            original_capture = scope.session._capture_initial_normal_intent

            def capture(*args):
                captured.append(time.monotonic())
                return original_capture(*args)

            scope.session._capture_initial_normal_intent = capture
            outcomes = []
            started = time.monotonic()
            thread = threading.Thread(target=lambda: outcomes.append(
                scope.session.revalidate_initial_normal_intent(admission, attempt),
            ))
            thread.start()
            assert await asyncio.to_thread(expired.wait, 1)
            assert queued and not captured

            for token in ('yes-token', 'no-token'):
                scope.session.public_session.service.book(token).local_ts = (
                    scope.session._now()
                )

            queued[0][0](*queued[0][1])
            resume_worker.set()
            await asyncio.to_thread(thread.join, 1)
            result = outcomes[0]

            print(
                'TIMEOUT_RACE',
                'elapsed', round(time.monotonic() - started, 3),
                'wait_expired', expired.is_set(),
                'captures_after_expiry', len(captured),
                'factual_valid', result.factual_valid,
                'reason', result.reason,
            )
            assert result.factual_valid is True and result.reason is None
        finally:
            if thread is not None and thread.is_alive():
                resume_worker.set()
                await asyncio.to_thread(thread.join, 2)
            try:
                next(fixture)
            except StopIteration:
                pass

with capture_logs():
    asyncio.run(probe())
print('NETWORK_ATTEMPTS', attempts)
assert not attempts
```

Result: defect reproduced; exit code 0; network attempts zero. The final assertion intentionally confirms the defective behavior, not acceptance.

An earlier exploratory version used the account freshness limit as the 50 ms bound. Its expected-positive assertion failed because account evidence correctly became stale. The refined reproduction used the market-data bound and refreshed received book timestamps before late capture, isolating bridge deadline behavior. That initial exploratory assertion failure is not hidden or treated as a production regression.

## Scope Review

The two implementation/test files are within the approved contract.

Admission provenance, local synchronous validation and a callback adapter are justified by the stated prerequisite.

No separate application, speculative controller, new reservation ledger, shared-core rewrite or external interface was introduced.

The required deadline repair remains within current authority.

## Residual Risks

- MUST_FIX-01 remains unresolved.
- Passing tests omit this deadline interleaving.
- Fake SDK/offline tests do not demonstrate live execution or strategy economics.
- This iteration is not project-level readiness.
- Real-money authorization remains NOT_GRANTED.
- After successful ITER-0024 archival, the human’s pause instruction applies; no ITER-0025 should begin.

## Final Decision

RETURN_TO_WORKER

Repair MUST_FIX-01 under the current Architect Contract, add deterministic regressions, run appropriate guarded checks and return for independent re-review.

Do not archive the current implementation as PASS or CODE_READY. Complete this iteration’s authorized repair/archive workflow, then pause as instructed.
