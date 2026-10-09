# ITER-0023 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the explicitly activated owner for one fully acknowledged initial NORMAL pair. The owner composes the existing pre-fill evaluation, FillWindow coordinator, and scoped close barrier. It adds no trading state, order creation, cancellation implementation, requoting, or CLI activation.

Caller cancellation and session teardown interrupt the owner and wake its waits. If interruption arrives during closure, the close path checks it after the barrier and again before recording close evidence; it cannot certify capital, classify a next mode, or release replacement blocking.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `ac27ff657bd08a2e0785b053bde2783b329f8a7a`
- Architect contract: `docs/development/ITER-0023/01_ARCHITECT.md`
- Working tree before implementation contained preserved untracked `research_outputs/`.
- `MICRO_LIVE_READY`: NO
- Real-money authorization: NOT_GRANTED

## Existing Components Reused

Reused `RDPPlacementOwner` and its registry, `evaluate_initial_prefill()`, the original-deadline `complete_initial_fill_window()` coordinator, `close_initial_normal_placement()` and its Cancel → Reconcile → inventory barrier, existing processor and capital projection, injected clock and sleeper, and existing waiter cleanup and structured logging.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

Final SHA-256:

- `src/polymaker/rdp_runtime.py`: `42fbde5d529a4a56f62855d4ff3cb467d243656afbb0b47b54fd867261d5d328`
- `tests/test_rdp_placement.py`: `04536cee18729da5bb38351d34dca102f3403e11ca1b80b550cce1875f45c365`

## Implementation Summary

Added `complete_initial_normal_cycle()` and an immutable cached completion result. Activation requires the current complete acknowledged pair, consistent registry, finite positive close budget, and no competing coordination. A stable identity containing attempt, acknowledged IDs, quote economics, and generations protects cached access.

The retained owner delegates REST decisions, FillWindow completion, and closure to existing APIs. REST polling is bounded by remaining market time and has no zero-fill timeout. External pre-fill, FillWindow, or close calls are blocked while the owner or its delegates are active.

A healthy close requires fresh matching status and order evidence, fresh books for both tokens and the expected generation, no public backlog, and valid structured evidence for a PriceGate failure, ready Book Filter rejection, or quote-price departure. A reason string alone cannot certify continuation.

Caller cancellation and teardown wake REST or FillWindow waits and drain retained work. After an awaited close, an interruption clears capital certification and next-mode evidence before the placement owner can release replacement blocking.

## Behavior Before

Callers had to coordinate pre-fill checks, FillWindow completion, and scoped closure through separate APIs. Nothing retained ownership of one initial NORMAL cycle across those operations.

## Behavior After

One explicit activation owns the pair through repeated REST evaluations or a delegated FillWindow, then through one scoped close. The result retains the trigger, last pre-fill evidence, optional original FillWindow deadline, and final close evidence. Repeated access for the same attempt, acknowledged scope, quotes, and generations returns the cached result without another close.

## Tests Added / Changed

Added direct cycle tests for:

- Repeated REST followed by quote invalidation, with no zero-fill timeout and cached completion.
- PriceGate, each of the five ready Book Filter rejection categories, and quote movement.
- Confirmation before activation, during resting, and during delayed Gamma reads, with default and configured original deadlines.
- Read failure combined with confirmation, proving unsafe closure rather than healthy FillWindow handoff.
- Started-window book movement, exact five-share REBALANCE classification, and final inventory reconciliation.
- Competing activation and pre-fill, FillWindow, and close calls while resting and during delegated work.
- First/repeated caller cancellation while resting; teardown while awaiting FillWindow.
- Health loss, public/user generation change, and market expiry after a REST wait.
- Cancellation and teardown during each blocked real-executor SDK operation: `open_orders`, `cancel`, `get_order`, and `get_trades`. Each case verifies that closure drains and cannot certify capital or release replacement blocking.
- Caller cancellation and teardown during an actual mocked Gamma HTTP request, including response drain and client cleanup.
- Sanitized failed-sleeper evidence and refusal to trust a forged healthy reason without supporting evidence.

## Tests Executed

All pytest runs used `uv run python - <<'PY'` with DNS and socket guards installed before importing pytest and invoking `pytest.main(...)`. The guard patched `socket.getaddrinfo`, `socket.create_connection`, `socket.socket.connect`, and `socket.socket.connect_ex`.

Exact pytest argument arrays and results:

```python
pytest.main(["-q", "tests/test_rdp_placement.py", "-k", "initial_cycle", "--tb=short"])
# 35 passed, 221 deselected
```

```python
pytest.main([
    "-q",
    "tests/test_rdp_placement.py",
    "tests/test_rdp_runtime.py",
    "tests/test_rdp_lifecycle.py",
    "tests/test_rdp_accounting.py",
    "tests/test_rdp_capital.py",
    "tests/test_rdp_book_filter.py",
    "tests/test_rdp_quotes.py",
    "tests/test_rdp_account.py",
    "tests/test_rdp_live.py",
    "tests/test_rdp_marketdata.py",
    "tests/test_rdp_userstream.py",
    "tests/test_execution.py",
])
# 910 passed
```

```python
pytest.main([
    "-q",
    "tests/test_favorite_first.py",
    "tests/test_favorite_first_review.py",
    "tests/test_favorite_live.py",
])
# 92 passed
```

```python
pytest.main(["-q"])
# 1530 passed, 2 skipped in 77.92s
```

Every guarded run recorded **0 network attempts**.

Additional checks:

- `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py` — passed.
- `uv run python -m compileall -q src/polymaker/rdp_runtime.py tests/test_rdp_placement.py` — passed.
- `git diff --check` — passed.

## Test Results

Early targeted runs exposed and resolved a cache identity check that did not account for the placement owner’s immutable post-close replacement, a PriceGate proof check that was too strict, and a missed final interruption trigger update. Some intermediate assertions also used inconsistent fill prices, remaining quantities, or cancellation expectations; those fixtures were corrected. The final targeted, affected, regression, and full-suite runs all passed.

## Regression Results

Affected RDP/runtime/execution integration: **910 passed**. FL regression: **92 passed**. Full suite: **1530 passed, 2 skipped**. All pytest runs recorded zero DNS/socket attempts.

## Git Diff Summary

Against base HEAD, the assigned files show 418 insertions in `rdp_runtime.py` and 860 insertions in `test_rdp_placement.py`. `git diff --check` passed.

Final status remains on `rdp-phase0-dev` at `ac27ff657bd08a2e0785b053bde2783b329f8a7a`, with only the two assigned files modified and preserved untracked `research_outputs/`. No audit files or Git history were changed.

## Safety Checks

The cycle only delegates the existing read, cancellation, and reconciliation paths; it never creates an order or arms another attempt. Execution authorization remains false. Network calls were blocked and counted during tests; the Gamma HTTP request used a local mock. No live calls or real-money actions occurred.

## Known Limitations

This completes one acknowledged initial NORMAL cycle only. It does not add submission, automatic requoting, another NORMAL cycle, REBALANCE execution, CLI activation, unknown-placement recovery, or full Phase 0 readiness.

## Deferred Findings

Gated submission, final submit checks, subsequent NORMAL/REBALANCE operation, unknown-placement recovery, and complete required metrics remain deferred under the Architect Contract.

## Reviewer Attention

Verify that stable cached identity includes the exact acknowledged scope and submitted economics; that only reliable structured invalidation evidence permits healthy closure; and that interruption at each closure await prevents capital certification and replacement release. Confirm the original FillWindow deadline remains unchanged and that no zero-fill timeout or order-create path was added.

## Human Live Trading Performed

NO
