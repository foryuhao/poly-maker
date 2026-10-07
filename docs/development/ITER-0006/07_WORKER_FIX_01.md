# ITER-0006 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repaired MF-01 through MF-04 from `docs/development/ITER-0006/06_REVIEWER.md` under approved revision `04_ARCHITECT_REVISION_01.md`. No strategy thresholds or semantics changed.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `10e94b5f6e519eca3c2618efeaaa524d3a333711`
- Preserved untracked `research_outputs/` was not modified.

## Existing Components Reused

- Existing `MarketMeta`, `OrderBook`, and parsed `TradePrint`.
- Existing per-market filter and synthetic market-data callback tests.

## Files Changed

- `src/polymaker/strategy/rdp_book_filter.py`
- `tests/test_rdp_book_filter.py`

## Implementation Summary

Unready results now explicitly reject and retain their readiness reason. Receipt and evaluation times are converted to Decimal values before window-boundary arithmetic. Decreasing evaluation times reset evidence and require fresh coverage; equal-time repeated evaluations remain valid.

Depth addition, cumulative variation, time subtraction, excursion, and threshold multiplication fail closed on inexact precision. The Depth Extreme decision uses exact cross-multiplication, so reported ratio division cannot round the strict predicate. Exact zero-neighbor depth avoids unnecessary addition.

## Behavior Before

Unready evidence could report `rejected=False`; float cutoff subtraction could retain exactly expired events; decreasing evaluation times could reuse evidence; and limited-precision addition could change a strict Depth Extreme decision.

## Behavior After

Unready inputs reject. Exact boundary events expire according to `(now - window, now]`. Evaluation time must be monotonic, with equal timestamps permitted. Arithmetic that cannot be represented exactly within supported precision fails closed with an explicit reason.

## Tests Added / Changed

Added or strengthened tests for unready rejection, exact `.3 + 2` and integer timestamp boundaries, just-inside/outside trade and depth events, exact one-second anchor selection, decreasing versus repeated evaluations, and inexact depth addition and cumulative variation.

## Tests Executed

- `uv run pytest -q tests/test_rdp_book_filter.py`
- `uv run pytest -q tests/test_marketdata.py tests/test_orderbook.py tests/test_estimators.py tests/test_rdp_quotes.py tests/test_rdp_capital.py`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/strategy/rdp_book_filter.py tests/test_rdp_book_filter.py`
- `git diff --check`
- No-index whitespace checks for both new files

## Test Results

- Book Filter tests: **45 passed**.
- Affected integration/regression tests: **138 passed**.
- Full suite on the final tree: **880 passed, 2 skipped**.
- Ruff and whitespace checks: **Passed**.

## Regression Results

The affected market-data, orderbook, estimator, quote, and capital regression set passed. The full suite had no failures or exclusions.

## Git Diff Summary

Worker changes remain limited to the two authorized new files. The working tree also contains Orchestrator-owned ITER-0006 artifacts and index changes, plus preserved `research_outputs/`; none were modified by this Worker.

## Safety Checks

No shared-core or external-interface changes, I/O, network activity, trading, or Git history operations were performed.

## Known Limitations

Production callback wiring remains deferred to a later iteration.

## Deferred Findings

No additional findings were addressed.

## Reviewer Attention

Please verify exact timestamp cutoffs, decreasing-evaluation recovery, unready rejection, and the inexact arithmetic fail-closed cases against the archived MF-01 through MF-04 reproductions.

## Human Live Trading Performed

NO
