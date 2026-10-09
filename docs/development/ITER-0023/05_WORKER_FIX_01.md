# ITER-0023 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved ITER-0023 contract and addressed both findings in `04_REVIEWER.md`:

- A reliable confirmation accepted after a successful REST evaluation now hands off to the existing FillWindow coordinator with its original receipt deadline.
- Healthy closure now requires structured evidence for PriceGate failure, a ready Book Filter rejection with matching reason and evidence, or valid quote-price departure. Other quote-builder rejections close and reconcile without certifying continuation.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `ac27ff657bd08a2e0785b053bde2783b329f8a7a`
- Git history was not changed.
- Existing ITER-0023 audit and `research_outputs/` files were preserved.

## Existing Components Reused

The repair uses the existing `evaluate_initial_prefill()`, `initial_fill_window()`, `complete_initial_fill_window()`, `close_initial_normal_placement()`, `RDPQuoteDecision`, `RDPBookFilterResult`, and reconciliation/fill accounting paths.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

No audit documents, Frozen SOP, shared-core files, CLI, configuration, or external interfaces were changed.

## Implementation Summary

After a REST result, the cycle owner checks current reliable FillWindow evidence before waiting or closing. If a confirmed fill arrived during the evaluator-to-owner handoff, it delegates to the existing coordinator and preserves the receipt-time deadline. Genuine read failures, system failures, and interruptions remain unsafe.

Healthy close classification now rejects generic matching reason strings and unsupported candidate rejections. Book Filter closure additionally requires an actual typed result, an authorized rejection reason, readiness, and matching structured side/aggregate evidence.

## Behavior Before

- A confirmation accepted immediately after a successful REST evaluation could be classified as unexpected and closed before its FillWindow.
- A matching quote-builder rejection reason could certify healthy closure, including `MIN_ORDER_SIZE`.

## Behavior After

- The REST-to-owner confirmation race uses the original FillWindow deadline and does not cancel early.
- `MIN_ORDER_SIZE` and `ILLEGAL_QUOTE_PRICE` candidate rejections cancel and reconcile the owned scope, preserve confirmed inventory, and keep capital uncertified, next mode unset, and replacement blocked.
- Genuine read failure paths remain fail-closed.

## Tests Added / Changed

Added a deterministic real-evaluator REST-result/confirmation race test asserting the original receipt deadline and cancellation timing.

Added real quote-builder rejection cases for `MIN_ORDER_SIZE` and `ILLEGAL_QUOTE_PRICE`, including confirmed-fill reconciliation and assertions that unsafe closure cannot certify capital or release replacement blocking.

Updated healthy Book Filter test fixtures to use typed filter results with matching structured evidence.

## Tests Executed

Each pytest run installed guards for `socket.getaddrinfo`, `socket.create_connection`, `socket.socket.connect`, and `socket.socket.connect_ex` before importing pytest.

```python
pytest.main([
    "-q", "tests/test_rdp_placement.py",
    "-k", "rest_result_racing_confirmation or unsupported_candidate_rejection",
])
# 3 passed
```

```python
pytest.main(["-q", "tests/test_rdp_placement.py", "-k", "initial_cycle", "--tb=short"])
# 38 passed, 221 deselected
```

Affected RDP/runtime/execution integration:

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
# 913 passed
```

FL regression:

```python
pytest.main([
    "-q",
    "tests/test_favorite_first.py",
    "tests/test_favorite_first_review.py",
    "tests/test_favorite_live.py",
])
# 92 passed
```

Full suite:

```python
pytest.main(["-q"])
# 1533 passed, 2 skipped in 78.11s
```

## Test Results

All final guarded runs recorded zero DNS or socket attempts.

During test development, an initial focused run had 3 failures because the fake SDK rows exposed matched quantity before the evaluator returned REST. After correcting the fixture timing, the race and minimum-size cases passed. A subsequent run had 1 failure because changing tick size correctly made the Book Filter unready before quote construction; the unsupported-candidate case was changed to exercise `ILLEGAL_QUOTE_PRICE` while retaining ready filter evidence. The final focused, affected, regression, and full runs all passed.

## Regression Results

- Scoped Ruff: `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py` — passed.
- Compile: `uv run python -m compileall -q src/polymaker/rdp_runtime.py tests/test_rdp_placement.py` — passed.
- `git diff --check` — passed.
- FL regression: 92 passed.
- Full suite: 1533 passed, 2 skipped.

## Git Diff Summary

Cumulative uncommitted ITER-0023 Worker diff against HEAD:

- `src/polymaker/rdp_runtime.py`: 483 insertions
- `tests/test_rdp_placement.py`: 1004 insertions
- Total: 1487 insertions

Final SHA-256:

- `src/polymaker/rdp_runtime.py`: `b6ce58aa38b6853345059125dc67ae8f67aab42236cbd6162f631640628f6698`
- `tests/test_rdp_placement.py`: `bd6f505efa20d9fae0623fb3e88a71c60f2619ee108c6bc0323e342ae9deb4f1`

## Safety Checks

- No real-money or live API actions were performed.
- No order-create action was added or invoked.
- Unsupported invalidations preserve confirmed inventory while preventing capital certification, next-mode eligibility, and replacement release.
- DNS/socket guards recorded zero attempts.
- No Git history operation was performed.

## Known Limitations

This iteration implements one acknowledged initial NORMAL cycle only. It does not activate live submission, later NORMAL cycles, or REBALANCE execution.

## Deferred Findings

Submission guards, subsequent-cycle operation, REBALANCE execution, unknown-placement recovery, and full delivery metrics remain outside this contract.

## Reviewer Attention

Please verify both repaired findings: REST-result confirmation handoff with the unchanged receipt deadline, and rejection of unsupported quote-builder results as healthy continuation evidence. The tests use the real evaluator and quote builder for these cases.

## Human Live Trading Performed

NONE.
