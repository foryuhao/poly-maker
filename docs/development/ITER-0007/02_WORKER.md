# ITER-0007 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the opt-in two-order RDP acknowledgment path from `docs/development/ITER-0007/01_ARCHITECT.md`. Default single-order parsing and behavior remain unchanged.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `b44e32cf2c03f2525201b8b4a1793789331e59f1`
- Preserved untracked `research_outputs/` was not modified.

## Existing Components Reused

- Existing `ExecutionGateway.place()` signing, rate limiting, GTC construction, submit guards, forced post-only handling, paper mode, and acknowledgment hooks.
- Existing `OpenOrder`, `Quote`, `MarketMeta`, `PlacementUnknown`, and fake-client tests.

## Files Changed

- `src/polymaker/execution/gateway.py`
- `tests/test_execution.py`

## Implementation Summary

Added the opt-in `allow_two_order_rdp_pair` keyword. It requires exactly two BUY quotes covering the market’s distinct YES and NO tokens, forced post-only, and strict placement; invalid requests fail before signing or POST.

The opt-in parser accepts only a two-row list and maps rows to quotes by request position. It returns both accepted orders, returns an empty list for two explicit rejections, and raises `BatchPlacementIncomplete` for one accepted and one rejected row. Unknown or malformed results raise `PlacementUnknown`. Exceptions preserve independently attributable acknowledgments, deduplicated observed IDs, and a reason. Ambiguous or duplicate IDs are never fabricated into attributed orders.

## Behavior Before

The existing parser rejected multi-quote responses, so a successful batch could not return usable acknowledgments for an RDP NORMAL pair.

## Behavior After

Explicitly opted-in two-order pairs can return correctly attributed acknowledgments. Partial and uncertain outcomes remain exceptions with evidence, and default single-order behavior remains unchanged.

## Tests Added / Changed

Added fake-client tests for successful pairs, two rejections, both partial-acceptance directions, accepted-plus-unknown outcomes in both positions, malformed and wrong-cardinality responses, conflicting and duplicate IDs, POST exceptions, request validation, minimum-size denial, pre-submit and signing-race final guards, forced post-only GTC payloads, and paper pairs. Existing default single-order tests continue to exercise the legacy parser.

## Tests Executed

- `uv run pytest -q tests/test_execution.py`
- `uv run pytest -q tests/test_filltest.py tests/test_rdp_lifecycle.py tests/test_favorite_first.py tests/test_favorite_first_review.py tests/test_gateway_positions_v2.py`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/execution/gateway.py`
- `uv run ruff check --ignore I001,E731 tests/test_execution.py`
- `git diff --check`
- No-index whitespace checks for both changed files

## Test Results

- Execution tests: **70 passed**.
- Fill-test, RDP lifecycle, and predecessor regressions: **147 passed**.
- Full suite: **913 passed, 2 skipped**.
- Gateway Ruff and whitespace checks: **Passed**.

The test file’s unfiltered Ruff check reports three existing findings outside this change: one local import-order finding and two assigned lambdas. The file passes with those existing rule codes ignored.

## Regression Results

Existing single-order gateway behavior and the affected fill-test, RDP lifecycle, predecessor, and full-suite tests passed.

## Git Diff Summary

Two authorized files changed: 142 insertions and 5 deletions in `gateway.py`; 287 insertions in `test_execution.py`. The preserved `research_outputs/` tree remains untracked and untouched.

## Safety Checks

Only fake clients were used. No live network request, order placement, cancellation, retry, or Git history operation was performed. No controller, lifecycle, inventory, or strategy behavior was changed.

## Known Limitations

Batch acknowledgment handling relies on the official two-row positional response. It does not perform cleanup or recovery actions for acknowledged orders.

## Deferred Findings

Per-token signing metadata and controller handling of partial or unknown acknowledgments remain deferred as specified by the contract.

## Reviewer Attention

Please verify that ambiguous IDs never receive token attribution, partial acceptance always raises the specialized exception, and default single-order parsing remains unchanged.

## Human Live Trading Performed

NO
