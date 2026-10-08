# ITER-0022 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented `docs/development/ITER-0022/01_ARCHITECT.md`: add one read-only API that decides whether the retained, acknowledged initial pair may REST, must hand off to FILL_WINDOW, or requires CLOSE. No order actions, controller, requoting, CLI activation, shared-core changes, or strategy changes were added.

## Base Repository State

Canonical root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
Checkout HEAD before implementation: `ae8d7168f55793cc7257bc807e346aaa2400acbb`  
The approved contract records source baseline `63f72b0d9d7f9731a2494df94580a1510372d74a`; the intervening checkout commit persisted the contract. Before implementation, the working tree had no tracked changes and retained untracked `research_outputs/`.

## Existing Components Reused

Reused the retained placement owner and registry, official Gamma status path, strict authenticated order-read gateway, existing fill processor and FillWindow, inventory and capital projection, dynamic books, Book Filter, NORMAL quote builder, and existing read ownership/teardown patterns.

## Files Changed

- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_placement.py`

## Implementation Summary

Added an immutable initial pre-fill decision result and an evaluation API on the retained input session. It validates attempt ownership and acknowledged scope, reads fresh market status and exact open orders through existing components, checks order identity and Decimal quantities, projects current inventory/capital evidence, and compares the acknowledged quotes with current valid NORMAL candidates.

Accepted confirmed-fill evidence hands off to the existing FillWindow, including when ordinary quote movement occurs during healthy reads. Actual read or system-state failures still return CLOSE. Overlapping evaluation/close coordination is excluded, and cancellation or teardown retains ownership until in-flight reads drain. The API cannot create, cancel, reconcile, or replace orders; execution remains disabled.

## Behavior Before

The initial pair had placement ownership and post-fill window closure, but no API to assess its validity before the first accepted confirmed fill.

## Behavior After

The API returns REST only when fresh authoritative evidence confirms an unchanged, valid pair. It returns FILL_WINDOW for accepted confirmation, preserving the original receipt deadline. Missing or foreign selected orders, duplicate or contradictory orders, exact quantity reductions, invalid quotes, stale/unreliable evidence, market expiry, or read failure cannot certify REST.

## Tests Added / Changed

Added pre-fill decision tests for unchanged pairs, pricing/filter invalidation, dynamic movement, exact tiny Decimal reductions, order identity failures, fill/read races, stale or unreliable evidence, ownership changes, overlap/cancellation/teardown, and zero order actions. Added composed coverage using the official Gamma HTTP client and a blocked actual gateway SDK executor.

## Tests Executed

All pytest invocations used a `uv run python` wrapper that replaced DNS resolution and socket connection functions with blockers before importing pytest. Each run reported zero network attempts.

- Targeted: pytest arguments `["-q", "tests/test_rdp_placement.py", "-k", "prefill"]`.
- Affected integration: pytest arguments `["-q", "tests/test_rdp_placement.py", "tests/test_rdp_runtime.py", "tests/test_rdp_accounting.py", "tests/test_rdp_capital.py", "tests/test_rdp_lifecycle.py", "tests/test_rdp_book_filter.py", "tests/test_rdp_quotes.py", "tests/test_rdp_account.py", "tests/test_rdp_live.py", "tests/test_rdp_marketdata.py", "tests/test_rdp_userstream.py", "tests/test_execution.py"]`.
- FL regression: pytest arguments `["-q", "tests/test_favorite_first.py", "tests/test_favorite_first_review.py", "tests/test_favorite_live.py"]`.
- Full suite: pytest arguments `["-q"]`.
- Quality checks: `uv run ruff check src/polymaker/rdp_runtime.py tests/test_rdp_placement.py`; `uv run python -m compileall -q src/polymaker/rdp_runtime.py tests/test_rdp_placement.py`; `git diff --check`.

## Test Results

- Targeted: 28 passed, 168 deselected in 0.59s; zero network attempts.
- Affected integration: 850 passed in 68.83s; zero network attempts.
- FL regression: 92 passed in 0.61s; zero network attempts.
- Full suite: 1,470 passed, 2 skipped in 78.03s; zero network attempts.
- Ruff, compileall, and `git diff --check`: passed.

During test construction, several assertions or fixtures initially failed: an order-age test assumed zero age, one quote-movement fixture did not cross its intended boundary, and a `1e-30` Decimal decrement rounded under the active context. The actual Gamma/SDK cancellation test also had an incorrect context-lifetime expectation. These test setup issues were corrected; the final targeted, affected, regression, and full runs passed.

## Regression Results

The affected RDP/execution integration group and the FL regression group passed. The full suite passed on the final implementation tree.

## Git Diff Summary

`src/polymaker/rdp_runtime.py`: 727 changed lines.  
`tests/test_rdp_placement.py`: 614 changed lines.  
Overall: 1,339 insertions, 2 deletions.

Final SHA-256:

- `src/polymaker/rdp_runtime.py`: `b0fb7c7b12da40cfd6cecb2c900a1c6b559f729b32c71f41a071568312a38c8c`
- `tests/test_rdp_placement.py`: `03ff4740947da925443be2f0f7a544800924f2209a4cdc70d82d728325b180e3`

Final status shows only those two modified tracked files and the preserved untracked `research_outputs/` directory. No Git history operations were performed.

## Safety Checks

- No real-money or live API calls; DNS and socket access were blocked for every pytest run.
- Tests verify the API issues no create/cancel calls and cannot enable execution or replacement.
- Exact order quantity validation remains Decimal-based.
- No shared-core or Frozen SOP changes.
- `git diff --check` passed.

## Known Limitations

This API reports a decision and evidence only. It does not implement polling, automatic closure/reconciliation, replacement quoting, subsequent NORMAL/REBALANCE execution, or MICRO_LIVE_READY functionality.

## Deferred Findings

As listed in the contract: gated submission, automatic pre-fill action, subsequent NORMAL/REBALANCE operation, continuous reliability, unknown-placement recovery, and complete metrics remain outside this iteration.

## Reviewer Attention

Please inspect fill-confirmation precedence against read/system failures, exact Decimal quantity checks, order and registry ownership validation, read freshness, and cancellation/teardown ownership. Confirm the scope remains limited to the retained initial pre-fill decision API.

## Human Live Trading Performed

NO
