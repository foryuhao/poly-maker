# ITER-0024 — Reviewer Report

## Verdict

PASS

BLOCKER = 0  
MUST_FIX = 0

The bridge deadline finding from `04_REVIEWER.md` is resolved in the reviewed tree.

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- `docs/development/ITER-0024/01_ARCHITECT.md`
- `docs/development/ITER-0024/02_WORKER.md`
- `docs/development/ITER-0024/03_PAUSE_AFTER_ARCHIVE_INSTRUCTION.md`
- `docs/development/ITER-0024/04_REVIEWER.md`
- `docs/development/ITER-0024/05_WORKER_FIX_01.md`
- Actual cumulative source/test diff
- Existing gateway guard locations, placement ownership, pending capture, freshness, backing, capital, filter and quote infrastructure

The pause instruction requires successful archival of this iteration, then a development pause. It does not authorize ITER-0025.

## Repository / Diff Reviewed

Canonical root: `/home/ubuntu/poly-maker`

Branch: `rdp-phase0-dev`

HEAD: `322fa5be51ec904ae6b9d1394cbc505c683876fa`

Reviewed implementation remains uncommitted.

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `86a59e7359e64c35163f042cb5a4a185939bce72df34a4cb521e08ce747100ce` |
| `tests/test_rdp_placement.py` | `82edf84329ac1afc816e892d87747091ca3fc730fef481d77e57d451cc848ffb` |

Cumulative source/test diff: 1,384 insertions, 3 deletions.

Hashes matched the Worker repair artifact and remained unchanged after independent checks.

Workflow/index artifacts and historical untracked `research_outputs/` remain preserved.

Reviewer made no repository source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1–2: Actual admission, exact clean ARMED attempt and provenance | Satisfied. Fabricated/copied/foreign scope rejects. |
| AC3–4: Immutable intent, metadata, PriceGate and Book Filter | Satisfied. Current conditions are recomputed; request economics and signing ticks are not silently changed. |
| AC5–6: Freshness, health, generations, backlog, expiry and closure | Satisfied using retained evidence and existing boundary semantics. |
| AC7: Exact clean pre-ack capture | Satisfied. Expected keys/count are required; buffered economic evidence and contaminated scope reject. |
| AC8: Backing and CapitalGate | Satisfied. Existing $30 backing and approved $5 reserve remain active. |
| AC9–10: Both gateway guards and zero unauthorized POSTs | Satisfied through actual gateway/fake SDK integration. |
| AC11: Bounded bridge and cleanup | Satisfied after repair. Expired queued capture and late positive publication reject. |
| AC12–13: No production trading authority and compatibility | Satisfied. No production placement invocation or read-only CLI activation was introduced. |
| AC14: Required checks | Satisfied. Independent affected verification and targeted reproductions passed; matching-tree Worker regression/full-suite evidence is durable. |

## Existing Codebase Compatibility

The implementation reuses the existing admission evidence, account backing, capital calculations, Book Filter, quote builder, processor pending capture and gateway guard locations.

No transport, ledger, execution gateway or reconciliation subsystem was duplicated or unnecessarily replaced.

The repair adds request-local deadline/publication coordination inside the RDP callback adapter. Shared-core production files, CLI, configuration, dependencies and strategy modules remain unchanged.

Existing cancellation/teardown behavior, placement capture and cycle completion remain compatible in the independently executed affected suite.

## Findings

### BLOCKER

None.

### MUST_FIX

None.

### NOTE

**Prior MUST_FIX-01 — CLOSED.**

Each worker-thread bridge request now retains an absolute monotonic deadline derived from the existing runtime limits.

The owning-loop callback checks the deadline before mutable-state capture and again before publishing its result. Request-local locking coordinates publication and abandonment. A false `Event.wait()` result unconditionally returns timeout, regardless of a subsequently completed event/result.

Independent replay of the exact prior scheduling interleaving observed:

```text
REPAIRED_TIMEOUT_RACE elapsed 0.051
wait_expired True
captures_after_expiry 0
factual_valid False
reason VALIDATION_BRIDGE_TIMEOUT
NETWORK_ATTEMPTS []
```

The queued callback was delivered after the real 50 ms wait expired but before the worker handled that outcome. It performed no mutable-state capture. The request registry cleared and all retained outcomes remained negative.

A second independent probe isolated the post-capture deadline check: the real capture initially produced valid evidence, its return was delayed beyond the request deadline, and worker timeout handling was held until publication completed.

Observed:

```text
POST_CAPTURE_DEADLINE:
underlying real capture valid;
late publication rejected;
retained outcomes negative;
registry empty
NETWORK_ATTEMPTS []
```

No further actionable findings arose.

## Frozen SOP Compliance

The validator continues to recompute current PriceGate, Book Filter, dynamic TickSize, Frozen sizing, backing and CapitalGate without changing the send intent.

The $30 capital assumption and approved $5 NORMAL repair reserve remain unchanged.

The repaired deadline is an operational bridge bound derived from existing limits. It adds no strategy timer, state, filter or risk policy.

Factual validity remains distinct from trading authorization.

## External Interface Review

No external runtime interface or dependency was added.

Revalidation performs no HTTP, WebSocket or SDK reads. It evaluates retained authoritative evidence and current stream state through existing components.

No RPC, third-party service or IM dependency was introduced.

Independent pytest and reproduction runs installed DNS/socket guards before imports and recorded zero network attempts.

## Trading Safety Review

Validation results keep `execution_authorized=False` and outstanding execution gates visible.

No production order-create, cancel or `gateway.place()` invocation was introduced.

Affected tests verify:

- First-guard denial produces zero signatures and zero POSTs.
- Signing-time invalidation produces zero POSTs.
- Final guard originates in the real SDK executor but captures mutable evidence on the owning loop.
- Bridge timeout at the actual final guard produces zero POSTs.
- Late callback delivery cannot publish positive evidence.
- Existing unavailable-loop, abandonment, teardown and repeated-cancellation behavior remains fail-closed.

Real-money trading performed: NO.

Live order/cancellation actions performed: NO.

## Inventory / Capital Accounting Review

The validator does not adopt inventory, resolve acknowledgments, create reservations or release replacement blocking.

It permits only the exact clean pending capture associated with the current ARMED attempt. Buffered fills, malformed IDs, overflow, owned scope and quarantine reject.

Existing initial backing, capital projection and CapitalGate remain the authorities. No accounting formula or approved reserve behavior changed.

## State Machine Review

The validator binds the session-issued eligible admission to the exact current ARMED attempt and retained generations.

It reports current factual evidence without advancing the placement/cycle state or executing a next action.

The event loop owns mutable-state evaluation; the SDK worker uses the bounded bridge.

Both pre-capture expiry and late-result publication now fail closed. Timeout handling cannot revive eligibility.

## Test Review

Independent affected-suite command:

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
result = pytest.main([
    '-q',
    'tests/test_rdp_placement.py',
    'tests/test_rdp_runtime.py',
    'tests/test_execution.py',
    'tests/test_rdp_capital.py',
    'tests/test_rdp_book_filter.py',
])
print('NETWORK_ATTEMPTS', attempts)
raise SystemExit(result if not attempts else 2)
PY
```

Result:

```text
601 passed in 65.88s
NETWORK_ATTEMPTS []
Exit code 0
```

This includes the new expiry/publication/gateway regressions, both guard locations, first denial, signing-time invalidation, and existing teardown/cancellation tests.

Independent static checks:

```bash
uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
git diff --check
sha256sum src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
```

Ruff and whitespace checks passed. Hashes matched the reviewed table.

Both independent probes ran through `uv run python - <<'PY'`, with the same four network guards before imports and the disposable fixture setup documented in `04_REVIEWER.md`.

The prior-race replay retained its real-wait interleaving:

```python
class ExpiryInterleavingEvent(threading.Event):
    def wait(self, timeout=None):
        observed = super().wait(timeout)
        if timeout is not None and not observed:
            expired.set()
            assert resume_worker.wait(2)
        return observed
```

The request factory forwarded all arguments, including the new retained deadline. After the real wait expired, books were refreshed and the queued callback ran before worker resumption. Final assertions were:

```python
assert result.factual_valid is False
assert result.reason == 'VALIDATION_BRIDGE_TIMEOUT'
assert not captured
assert not scope.session._intent_bridge_requests
assert all(
    not item.factual_valid
    for item in scope.session.initial_intent_validation_results
)
```

Result: passed; exit code 0; zero network attempts.

The post-capture probe independently held worker wait handling and delayed a real positive capture:

```python
class DeferredWait(threading.Event):
    def wait(self, timeout=None):
        wait_entered.set()
        assert resume.wait(2)
        return super().wait(timeout)

def valid_then_finish_late(*args):
    result = original_capture(*args)
    assert result.factual_valid
    captured.append(result)
    time.sleep(.08)
    return result
```

The request bound was 50 ms. After callback completion, worker handling resumed. Assertions verified that the underlying real capture was valid, the returned result was `VALIDATION_BRIDGE_TIMEOUT`, no positive result was retained, and the request registry was empty.

Result: passed; exit code 0; zero network attempts. This delay was confined to a disposable offline probe; no production change occurred.

Worker’s matching-tree evidence in `05_WORKER_FIX_01.md`:

- Focused matrix: 50 passed.
- Actual gateway timeout regression: 1 passed.
- Affected integration: 601 passed.
- FL regression: 106 passed.
- Full suite: 1,584 passed, 2 skipped.
- All guarded runs: zero network attempts.

The final full-suite count is Worker-executed evidence. I independently reran the affected suite and adversarial deadline probes. No remaining concern justified repeating the entire full suite.

## Scope Review

The repair remains within the approved two-file implementation/test scope.

The request deadline, publication coordination and three regressions directly address the prior finding.

No Architect revision, human strategy decision, shared-core change, external-interface change or scope expansion was required.

## Residual Risks

This PASS applies only to the exact hashes and initial-intent revalidation scope reviewed above.

It does not establish MICRO_LIVE_READY, production submission authority, live execution correctness or strategy economics.

Outstanding submission gates, subsequent NORMAL/REBALANCE operation, unknown-placement recovery and complete metrics remain deferred.

Real-money authorization remains NOT_GRANTED.

The human pause instruction applies after this iteration is successfully archived and remotely verified.

## Final Decision

PASS

The Orchestrator may persist this report, complete RESULT/index archival, commit, push and verify the reviewed ITER-0024 state.

Then pause development as instructed:

```text
ITER-0024 已归档，开发暂停，待收敛调整
```

Do not initialize ITER-0025 or dispatch another Architect.
