# ITER-0023 — Reviewer Report

## Verdict

PASS

BLOCKER = 0  
MUST_FIX = 0

Both findings from `04_REVIEWER.md` are resolved in the reviewed tree.

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`, especially §§18–21
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- `docs/development/ITER-0023/01_ARCHITECT.md`
- `docs/development/ITER-0023/02_WORKER.md`
- `docs/development/ITER-0023/03_REVIEW_HANDOFF.md`
- `docs/development/ITER-0023/04_REVIEWER.md`
- `docs/development/ITER-0023/05_WORKER_FIX_01.md`
- Actual cumulative source/test diff
- Existing placement ownership, pre-fill evaluation, FillWindow coordination, scoped lifecycle barrier, confirmed-fill processing, accounting and capital projection

This is an independent implementation re-review, not acceptance of the Worker report alone.

## Repository / Diff Reviewed

Canonical root: `/home/ubuntu/poly-maker`

Branch: `rdp-phase0-dev`

HEAD: `ac27ff657bd08a2e0785b053bde2783b329f8a7a`

Reviewed implementation remains uncommitted.

| File | Cumulative change | SHA-256 |
|---|---|---|
| `src/polymaker/rdp_runtime.py` | 483 insertions | `b6ce58aa38b6853345059125dc67ae8f67aab42236cbd6162f631640628f6698` |
| `tests/test_rdp_placement.py` | 1004 insertions | `bd6f505efa20d9fae0623fb3e88a71c60f2619ee108c6bc0323e342ae9deb4f1` |

Hashes matched the Worker artifact and remained unchanged after verification.

Index and iteration-document changes are Orchestrator workflow artifacts. Historical untracked `research_outputs/` remain preserved.

Reviewer made no repository source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1: Valid zero-fill REST without invented timeout | Satisfied. Existing polling is reused; close budget does not limit resting duration. |
| AC2: Ordinary invalidation uses one scoped close | Satisfied. PriceGate, typed ready filter rejection and valid quote-price departure are the allowed healthy classes. |
| AC3: Confirmation preserves original FillWindow | Satisfied, including confirmation between real REST evaluation completion and owner resumption. |
| AC4: Confirmation with genuine read/state failure | Satisfied. Failure remains primary and continuation remains unsafe. |
| AC5: Started-window movement | Satisfied. Existing window coordination ignores ordinary repricing and performs no new-cycle admission. |
| AC6: Authoritative final inventory and mode | Satisfied through the existing close proof and five-share boundary classification. |
| AC7: Unreliable state cannot grant healthy continuation | Satisfied. Unsupported candidate rejections retain blocking and cannot certify a next action. |
| AC8: Partial fills and cancel/fill races | Satisfied through reused reconciliation/confirmed-fill authority and affected tests. |
| AC9: Exclusive owner/delegates | Satisfied. Competing activation and pre-fill/window/close calls are rejected. |
| AC10: Cancellation/teardown drain and restrict release | Satisfied. Retained interruption remains checked after closure and before release. |
| AC11: Sanitized retained failures | Satisfied. Waiter outcomes are retrieved; failure evidence is categorical. |
| AC12: No creates/new attempt | Satisfied. Completion does not submit, sign, requote or arm another attempt. |
| AC13: Inactive/default compatibility | Satisfied. Default read-only CLI authority is unchanged; existing explicit APIs remain compatible when this owner is inactive. |
| AC14: Required checks | Satisfied. Independent affected verification passed; Worker recorded final targeted, affected, FL and full-suite results for matching hashes. |

## Existing Codebase Compatibility

The implementation composes existing infrastructure rather than replacing it.

It reuses:

- Current placement ownership and registry checks.
- Existing official market/account reads.
- The pre-fill evaluator.
- Original-receipt FillWindow evaluation and completion.
- Scoped cancellation/reconciliation.
- Confirmed-fill inventory and capital projection.
- Existing shield/drain and structured logging mechanisms.

No duplicate transport, ledger, cancellation implementation, reconciliation implementation or general cycle controller was introduced.

Shared-core production files, CLI, configuration and dependencies remain unchanged.

Cached completion retains the acknowledged scope, submitted economics and generations. Matching access does not repeat cancellation or reconciliation. Nested delegation and final interruption checks remain intact after the repair.

## Findings

### BLOCKER

None.

### MUST_FIX

None.

### NOTE

**Prior MUST_FIX-01 — CLOSED.**

The owner now handles a reliable started window discovered after a successful REST decision by delegating to the existing FillWindow coordinator.

Independent reproduction used the real evaluator and queued the accepted confirmation with `loop.call_soon()` immediately before its wrapper returned. The event therefore arrived after real REST evaluation and before owner resumption.

Observed:

```text
REPAIR_PROBE REST_RACE
TRIGGER WINDOW_EXPIRED
WINDOW 903000000000
CANCEL_AT [903000000000]
CAPITAL_CERTIFIED True
BLOCKED False
```

Receipt time was `900000000000`. Cancellation occurred at the original default three-second deadline, with no early close.

**Prior MUST_FIX-02 — CLOSED.**

The generic candidate-rejection equality predicate has been removed. Healthy classification now permits only the contract’s named classes with corresponding evidence.

Independent replay of a real `MIN_ORDER_SIZE` rejection observed:

```text
REPAIR_PROBE MIN_ORDER_SIZE
TRIGGER MIN_ORDER_SIZE
WINDOW None
CANCEL_AT [900000000000]
CAPITAL_CERTIFIED False
BLOCKED True
```

The affected suite also passed the real-builder `MIN_ORDER_SIZE` and `ILLEGAL_QUOTE_PRICE` cases with confirmed-fill reconciliation. Those tests assert preserved inventory, no certified continuation, no next mode and retained replacement blocking.

An additional independent structured-filter probe accepted valid typed evidence and rejected:

- Aggregate flag mismatch.
- Outcome-side flag mismatch.
- Unsupported rejection reason.
- Missing filter evidence.

No further actionable findings arose.

## Frozen SOP Compliance

The completed initial-cycle owner follows Frozen §§18–21:

```text
Rest / accepted fills
→ original FillWindow or permitted invalidation
→ Cancel
→ Reconcile
→ confirmed inventory
→ descriptive NORMAL / REBALANCE result
```

Healthy confirmation now preserves its original receipt deadline. Genuine read/state failures and interruption still require unsafe closure.

No Frozen document, inventory/capital formula, approved $5 future repair reserve or approved depth-change definition changed.

No no-fill stop, resting-duration stop or additional strategy state was introduced.

## External Interface Review

No external runtime interface or dependency was added.

The implementation continues to use existing official Gamma/CLOB and retained WebSocket/account infrastructure. No blockchain RPC, third-party service or IM dependency was introduced.

Independent pytest and reproduction runs blocked and counted DNS/socket operations from before imports. Recorded network attempts: zero.

## Trading Safety Review

Execution authorization remains fixed false.

The new completion API creates no orders and grants no signing, submission, requoting or subsequent-cycle authority. Existing read-only CLI modes gain no order-action authority.

The retained owner remains responsible through reads, waiting and closure. Delegation guards exclude competing callers. Cancellation/teardown wake waits and drain retained HTTP/SDK work.

Post-barrier interruption checks prevent capital certification and replacement release. Unsupported candidate invalidation now preserves those restrictions.

Real-money trading performed: NO.

Live order/cancellation actions performed: NO.

## Inventory / Capital Accounting Review

Inventory and acquisition costs continue to derive from existing accepted confirmed fills and reconciliation.

The new owner does not mutate inventory directly or substitute different accounting formulas.

Supported healthy completion uses the existing capital proof and classification:

```text
abs(Delta) < 5  → NORMAL
abs(Delta) >= 5 → REBALANCE
```

Unsupported invalidation still closes/reconciles attributable orders and preserves confirmed inventory, while withholding capital certification, next-mode eligibility and replacement release.

A reported next mode remains descriptive and does not execute an order.

## State Machine Review

One explicit activation owns the current complete acknowledged pair.

REST reuses existing polling without a zero-fill timeout. Accepted confirmation hands off once to the retained original-deadline window. Ordinary movement during that window does not reprice or admit another cycle.

Scoped closure remains serialized through the existing barrier. Competing calls cannot overlap legitimate delegates.

Completion is immutable and cached. Failure or interruption retains replacement blocking.

The repair addresses the two previously defective transitions without changing approved architecture or strategy semantics.

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
    'tests/test_rdp_lifecycle.py',
    'tests/test_rdp_accounting.py',
    'tests/test_rdp_capital.py',
    'tests/test_rdp_book_filter.py',
    'tests/test_rdp_quotes.py',
    'tests/test_rdp_account.py',
    'tests/test_rdp_live.py',
    'tests/test_rdp_marketdata.py',
    'tests/test_rdp_userstream.py',
    'tests/test_execution.py',
])
print('NETWORK_ATTEMPTS', attempts)
raise SystemExit(result if not attempts else 2)
PY
```

Result:

```text
913 passed in 69.37s
NETWORK_ATTEMPTS []
Exit code 0
```

This includes the 38 current cycle cases, repaired real-evaluator/real-builder regressions, read-failure confirmation handling, nested coordination, cached completion and interruption at each real-executor SDK closure operation.

Independent static checks:

```bash
uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
git diff --check
sha256sum src/polymaker/rdp_runtime.py tests/test_rdp_placement.py
```

Ruff and whitespace checks passed; hashes matched the table above.

Independent targeted reproductions were executed with:

```text
uv run python - <<'PY'
```

Both installed the same four DNS/socket guards before imports and loaded the existing fixture module through `importlib.util.spec_from_file_location()`.

The original-finding replay used the same fixture/evaluator composition documented in `04_REVIEWER.md`, with these repair-specific checks:

```python
async def real_rest_then_confirm(a):
    decision = await original(a)
    assert decision.action == 'REST'

    def accepted_after_return():
        t._send_matched_then_confirmed(
            scope, 'yes-order', receipt_ns=clock.read(),
            price=yes.price, trade_id='review-rest-race',
        )
        client.rows['yes-order']['size_matched'] = '1'
        client.trades = [t._rest_trade(
            'yes-order', 'yes-token', str(yes.price), '1',
            trade_id='review-rest-race',
        )]

    asyncio.get_running_loop().call_soon(accepted_after_return)
    return decision
```

After completion:

```python
assert result.last_prefill_decision.action == 'REST'
assert result.fill_window_completion.window_deadline_monotonic_ns == 903_000_000_000
assert canceled_at == [903_000_000_000]
assert result.close_result.capital.certified
assert not result.close_result.replacement_blocked
```

The minimum-size replay changed valid retained minimum metadata to six shares after five-share acknowledgment, then asserted:

```python
assert result.trigger == 'MIN_ORDER_SIZE'
assert result.close_result.capital is not None
assert not result.close_result.capital.certified
assert result.close_result.next_mode is None
assert result.close_result.replacement_blocked
```

Replay result: both passed; exit code 0; network attempts zero.

The separate structured-filter probe used a real pre-fill decision with typed `INACTIVE_YES` evidence and verified:

```python
assert check(decision) is True
assert check(replace(decision, book_filter=replace(valid, inactive=False))) is False
assert check(replace(decision, book_filter=replace(valid, sides=baseline.sides))) is False
assert check(replace(
    decision, book_filter=replace(valid, reasons=('MIN_ORDER_SIZE',)),
)) is False
assert check(replace(decision, book_filter=None)) is False
```

Result: passed; exit code 0; network attempts zero.

Worker’s final matching-tree verification, preserved in `05_WORKER_FIX_01.md`:

- Focused repair tests: 3 passed.
- Cycle tests: 38 passed.
- Affected suite: 913 passed.
- FL regression: 92 passed.
- Full suite: 1533 passed, 2 skipped in 78.11s.
- Every guarded run: zero network attempts.

The final full-suite count is Worker-executed evidence. I independently reran the affected suite and targeted reproductions; no new concern justified another full-suite repetition.

## Scope Review

The repair remains within the existing Architect Contract and the two authorized files.

It makes local changes to handoff and healthy-invalidation classification, plus necessary regression fixtures/tests.

No Architect revision, human strategy decision, shared-core change, external-interface change or scope expansion was required.

## Residual Risks

This PASS applies to the exact hashes and initial acknowledged-cycle scope reviewed above.

It does not establish project-level MICRO_LIVE_READY, live execution correctness or strategy economics.

Submission guards, subsequent NORMAL cycles, REBALANCE execution, unknown-placement recovery and complete required metrics remain deferred prerequisites.

Real-money authorization remains NOT_GRANTED.

## Final Decision

PASS

The Orchestrator may archive ITER-0023 as CODE_READY after persisting this report and completing the required RESULT/index/Git/remote archive gate.

Continue with the next minimal prerequisite. This is not a final project readiness review.
