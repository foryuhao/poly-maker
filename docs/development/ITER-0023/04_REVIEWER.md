# ITER-0023 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 0  
MUST_FIX = 2

The existing suite passes, but two independently reproduced implementation defects prevent acceptance.

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`, particularly §§18–21
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- `docs/development/ITER-0023/01_ARCHITECT.md`
- `docs/development/ITER-0023/02_WORKER.md`
- `docs/development/ITER-0023/03_REVIEW_HANDOFF.md`
- Actual runtime and test diff
- Reused placement ownership, scoped lifecycle barrier, confirmed-fill processing, capital projection, quote builder, and FillWindow coordination

The incomplete prior review supplied investigation leads. Its observations and test results were not substituted for independent verification.

## Repository / Diff Reviewed

Canonical root: `/home/ubuntu/poly-maker`

Branch: `rdp-phase0-dev`

HEAD: `ac27ff657bd08a2e0785b053bde2783b329f8a7a`

Implementation remains uncommitted.

Reviewed implementation diff:

| File | Change | SHA-256 |
|---|---|---|
| `src/polymaker/rdp_runtime.py` | 418 insertions | `42fbde5d529a4a56f62855d4ff3cb467d243656afbb0b47b54fd867261d5d328` |
| `tests/test_rdp_placement.py` | 860 insertions | `04536cee18729da5bb38351d34dca102f3403e11ca1b80b550cce1875f45c365` |

The index modification and untracked Worker/handoff documents are workflow artifacts. The 748 untracked `research_outputs/` files remain preserved.

I did not edit repository source, tests, audit documents, or Git history. Reproduction injected events and metadata changes into disposable fixture objects only.

## Acceptance Criteria Review

| Criteria | Review |
|---|---|
| AC1: REST without zero-fill timeout | No duration-based stop was added; existing polling continues while valid. |
| AC2: Ordinary invalidation uses scoped closure | PriceGate, ready filter rejection, and quote movement are covered, but healthy classification also admits unauthorized candidate rejection categories. MUST_FIX-02. |
| AC3: Accepted confirmation hands off to original window | Covered before activation, during waiting, and during delayed reads. Confirmation after a real REST decision but before owner resumption instead closes immediately. MUST_FIX-01. |
| AC4: Genuine read/state failure remains unsafe | Implemented and covered by confirmation combined with failed order reads. |
| AC5: Started-window movement does not reprice | Existing window coordinator is reused; ordinary movement does not start another cycle. |
| AC6: Authoritative inventory and mode classification | Existing close proof and confirmed inventory authority remain in use; exact-five classification is exercised. Healthy release requires fixing MUST_FIX-02. |
| AC7: Unreliable inputs cannot permit healthy continuation | Existing scope, generation, health and freshness checks remain; candidate-rejection classification is too broad. |
| AC8: Partial fills and cancel/fill races | Existing reconciliation and confirmed-fill accounting are reused; relevant inherited tests passed. |
| AC9: Exclusive coordination | Retained owner/delegate guards reject competing calls during resting, FillWindow work and closure. |
| AC10: Cancellation/teardown drain and restrict release | Post-barrier interruption checks preserve blocking; actual mocked HTTP and executor-operation tests passed. |
| AC11: Sanitized retained failures | Failed sleeper and read outcomes are categorical; waiter outcomes are retrieved. |
| AC12: No order creation or new attempt | New owner only delegates existing read/window/close operations. |
| AC13: Inactive/default compatibility | Changes are RDP-local; existing explicit APIs remain available when the owner is inactive. |
| AC14: Required checks | Independent full suite and scoped Ruff passed, but passing tests omit the two reproduced cases. |

Acceptance criteria are not fully satisfied.

## Existing Codebase Compatibility

The implementation reuses the existing pre-fill evaluator, FillWindow coordinator, lifecycle barrier, strict gateway adapter, processor, inventory accounting and capital projection.

It does not duplicate or unnecessarily replace a working transport, ledger, cancellation implementation, reconciliation implementation, or strategy subsystem.

No shared-core production file, CLI, configuration or dependency changed. FL tests are included in the independently executed full suite.

Cached completion identity includes attempt, market, acknowledged IDs, submitted quote economics, and generations. Quotes are immutable. Matching cached access returns retained evidence without repeating the close; changed identity is rejected.

## Findings

### BLOCKER

None.

### MUST_FIX

#### MUST_FIX-01 — Accepted confirmation after REST evaluation closes before its FillWindow

Location: `src/polymaker/rdp_runtime.py:943`, particularly the branch at line 980.

After awaiting `evaluate_initial_prefill()`, the owner refreshes local FillWindow evidence. It handles a started window when the returned action is `FILL_WINDOW`, or when a returned `CLOSE` qualifies as ordinary healthy invalidation.

If the returned action is `REST` and accepted confirmation arrives before the owner resumes, refreshed evidence correctly says the window has started. The code instead emits:

```text
UNEXPECTED_FILL_WINDOW_WITHOUT_HANDOFF
```

It immediately delegates unsafe closure.

Independent reproduction used the real pre-fill evaluator to produce `REST`, then admitted a valid MATCHED→CONFIRMED fill before returning control to the cycle owner. The fake SDK exposed the consistent remaining quantity and matching confirmed trade.

Observed result:

```text
ACTION REST
TRIGGER UNEXPECTED_FILL_WINDOW_WITHOUT_HANDOFF
WINDOW None
CLOCK 900000000000
BARRIER True
CERTIFIED False
NEXT None
BLOCKED True
CANCEL [('no-order', 'yes-order')]
```

The confirmation receipt was `900000000000`; its default window should run until `903000000000`. Cancellation occurred without advancing the clock or delegating that window.

This violates Frozen §20 and contract Minimal Scope 6 / AC3. The event is a valid timing interleaving, not unreliable fill evidence.

Required correction:

- Recognize accepted, reliable confirmation discovered after a successful REST evaluation.
- Delegate once to the existing original-receipt FillWindow coordinator.
- Preserve genuine read/state failures and interruption as unsafe closure.
- Add a deterministic regression for this precise evaluator-to-owner handoff interleaving, asserting no early cancellation and the unchanged original deadline.

#### MUST_FIX-02 — Any matching quote-builder rejection can certify healthy continuation

Location: `src/polymaker/rdp_runtime.py:1146`.

The final healthy-invalidation predicate accepts any non-null candidate rejection whose reason equals the decision reason:

```python
decision.current_candidate is not None
and decision.reason == decision.current_candidate.reason
and decision.reason is not None
```

This exceeds the contract’s three ordinary invalidation classes: PriceGate failure, ready Book Filter rejection, or valid quote-price departure.

Independent reproduction:

1. Admit and acknowledge the real initial five-share pair.
2. Change otherwise valid retained minimum-order metadata to six shares.
3. Run the real pre-fill evaluator and real quote builder.
4. Use the existing scoped close against a fake SDK with consistent order evidence.

The real builder returns `MIN_ORDER_SIZE`. The cycle classifies it as healthy and releases replacement blocking:

```text
ACTION CLOSE
TRIGGER MIN_ORDER_SIZE
WINDOW None
BARRIER True
CERTIFIED True
NEXT NORMAL
BLOCKED False
CANCEL [('no-order', 'yes-order')]
```

The same generic predicate also accepts other candidate rejection reasons; a matching reason alone does not establish an authorized ordinary invalidation.

This violates contract Minimal Scope 8 and its unsafe-continuation invariant.

Required correction:

- Restrict healthy closure to the explicitly authorized classes and their corresponding reliable structured evidence.
- Treat minimum-size and other unsupported candidate rejection categories as unsafe continuation.
- Still cancel/reconcile the attributable scope and preserve available confirmed inventory.
- Do not certify capital, expose an eligible next mode, or release replacement blocking.
- Add a real-evaluator regression for valid minimum metadata producing `MIN_ORDER_SIZE`, plus representative unsupported candidate rejection coverage.

Both corrections fit the current approved contract. No Architect revision or human decision is required.

### NOTE

The existing suite’s success does not establish coverage of the evaluator-to-owner confirmation interleaving or candidate rejection classification. The repair should add those regressions before the final suite run.

This iteration does not establish project-level readiness. Submission, subsequent cycles, REBALANCE execution, unknown-placement recovery and complete metrics remain deferred.

## Frozen SOP Compliance

The general composition follows Frozen §§18–21: rest, accepted fills, scoped cancellation, reconciliation, confirmed inventory and descriptive next-mode classification.

No Frozen document, capital formula, approved $5 repair reserve, or approved depth-change definition changed.

MUST_FIX-01 violates the requirement to retain remaining orders through the original FillWindow after accepted confirmation. MUST_FIX-02 violates the approved continuation restriction for this implementation iteration.

There is no zero-fill timeout or hard resting-duration stop.

## External Interface Review

No external runtime interface or dependency was added.

The implementation delegates to existing official Gamma and authenticated CLOB paths. No blockchain RPC, third-party API, or IM implementation was introduced.

All independent tests and reproductions installed DNS/socket guards before importing pytest or project test fixtures. Network attempts: zero.

## Trading Safety Review

The completion result fixes `execution_authorized=False`.

The cycle owner performs no order creation, signing, replacement submission or new attempt arming. Cancellation/reconciliation is reachable only through explicitly activated APIs; default read-only CLI behavior gains no authority.

Ownership is retained across nested pre-fill, window and close delegates. Competing calls are rejected.

Caller cancellation and teardown wake waits and drain retained work. Existing strict gateway operations retain blocking executor ownership. Interruption is checked after the barrier and immediately before release.

MUST_FIX-01 fails closed but incorrectly ends a valid FillWindow. MUST_FIX-02 incorrectly grants a healthy continuation proof.

Real-money trading performed: NO.

Real cancellation/order actions against live funds: NO.

## Inventory / Capital Accounting Review

The existing confirmed-fill processor and reconciliation barrier remain the inventory authority. New code does not directly change quantities or costs.

Final close classification continues to use:

```text
abs(Delta) < 5  → NORMAL
abs(Delta) >= 5 → REBALANCE
```

The descriptive next mode does not itself execute an order.

The close path’s interruption gate prevents capital certification and replacement release after cancellation or teardown. Existing accounting formulas are unchanged.

MUST_FIX-02 must preserve the barrier’s available confirmed inventory while withholding a healthy capital/continuation proof.

## State Machine Review

One retained owner coordinates the current acknowledged pair. REST uses existing polling and remaining market time without a no-fill stopping rule.

Original FillWindow timing and closure are delegated to the established coordinator. Completion is retained and matching access is cached.

The REST→started-window race is mishandled as an unexpected state. This is MUST_FIX-01.

Candidate rejection classification is broader than authorized healthy transition evidence. This is MUST_FIX-02.

No new strategy state or autonomous next-cycle execution was added.

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
1530 passed, 2 skipped in 78.08s
NETWORK_ATTEMPTS []
Exit code 0
```

Independent static checks:

```bash
uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
git diff --check
```

Both passed.

The two reproductions ran with the same four network guards. After the guard setup above, their exact Python body was:

```python
import asyncio, importlib.util, tempfile
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
import pytest
from structlog.testing import capture_logs

spec = importlib.util.spec_from_file_location(
    'review_fixture', 'tests/test_rdp_placement.py',
)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

async def probe(kind):
    with tempfile.TemporaryDirectory() as tmp, pytest.MonkeyPatch.context() as mp:
        fixture = t.placement_scope.__wrapped__(Path(tmp), mp)
        scope = next(fixture)
        gateway = None
        try:
            clock = t._DeterministicMonotonicClock(900_000_000_000)
            scope.session._monotonic_ns = clock.read
            scope.session._sleep = clock.sleep
            attempt, quotes = await t._compose_accepted_initial_pair(scope)
            yes = next(q for q in quotes if q.token_id == 'yes-token')
            gateway, client = t._close_gateway(scope, quotes)

            if kind == 'rest-confirmation-race':
                original = scope.session._run_initial_prefill_evaluation

                async def real_rest_then_confirm(a):
                    decision = await original(a)
                    assert decision.action == 'REST', decision
                    t._send_matched_then_confirmed(
                        scope, 'yes-order', receipt_ns=clock.read(),
                        price=yes.price, trade_id='review-rest-race',
                    )
                    client.rows['yes-order']['size_matched'] = '1'
                    client.trades = [t._rest_trade(
                        'yes-order', 'yes-token', str(yes.price), '1',
                        trade_id='review-rest-race',
                    )]
                    return decision

                mp.setattr(
                    scope.session, '_run_initial_prefill_evaluation',
                    real_rest_then_confirm,
                )
            else:
                changed = replace(
                    scope.session.bootstrap,
                    token_metadata=tuple(
                        replace(x, min_order_size=6)
                        for x in scope.session.bootstrap.token_metadata
                    ),
                )
                scope.session.bootstrap = changed

            result = await scope.session.complete_initial_normal_cycle(
                attempt, close_budget_s=2,
            )
            print(
                'PROBE', kind,
                'ACTION', result.last_prefill_decision.action,
                'TRIGGER', result.trigger,
                'WINDOW', result.fill_window_completion,
                'CLOCK', clock.read(),
                'BARRIER', result.close_result.barrier_complete,
                'CERTIFIED', getattr(result.close_result.capital, 'certified', None),
                'NEXT', getattr(result.close_result.next_mode, 'value', None),
                'BLOCKED', result.close_result.replacement_blocked,
                'CANCEL', client.cancel_calls,
            )

            if kind == 'rest-confirmation-race':
                assert result.trigger == 'UNEXPECTED_FILL_WINDOW_WITHOUT_HANDOFF'
                assert clock.read() == 900_000_000_000
                assert result.fill_window_completion is None
                assert result.close_result.barrier_complete
                assert result.close_result.replacement_blocked
            else:
                assert result.trigger == 'MIN_ORDER_SIZE'
                assert result.close_result.capital.certified
                assert result.close_result.next_mode.value == 'NORMAL'
                assert not result.close_result.replacement_blocked
        finally:
            if gateway is not None:
                gateway.close()
            try:
                next(fixture)
            except StopIteration:
                pass

async def main():
    with capture_logs():
        await probe('rest-confirmation-race')
        await probe('minimum-size-change')

asyncio.run(main())
print('NETWORK_ATTEMPTS', attempts)
assert not attempts
```

Executed through `uv run python - <<'PY'`.

Result: both defective behaviors reproduced; assertions passed; exit code 0; `NETWORK_ATTEMPTS []`.

These assertions intentionally confirm current defects. They are not passing acceptance tests.

## Scope Review

The two changed implementation/test files fall within the Architect Contract.

The immutable result, retained cycle task, delegation guards and RDP-local interruption checks are justified by the approved problem.

No speculative subsystem, separate application, broad metrics infrastructure, shared-core rewrite or new strategy feature was introduced.

The necessary repairs are ordinary implementation changes within existing authority.

## Residual Risks

- The two MUST_FIX findings remain unresolved.
- Test success currently omits their specific failure cases.
- Offline fake HTTP/SDK results do not demonstrate live execution or RDP economics.
- The integrated project remains `MICRO_LIVE_READY: NO`.
- Real-money authorization remains `NOT_GRANTED`.

## Final Decision

RETURN_TO_WORKER

Repair MUST_FIX-01 and MUST_FIX-02 under the existing Architect Contract, add meaningful regressions, run appropriate guarded checks, and return for independent re-review.

Do not archive this implementation as PASS or CODE_READY.
