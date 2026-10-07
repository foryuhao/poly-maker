# ITER-0012 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the repairs approved in `docs/development/ITER-0012/04_ARCHITECT_REVISION_01.md`: add opt-in exact-decimal order reads while preserving legacy gateway behavior, fix exact freshness comparisons, and validate required spender identities.

## Base Repository State

Repository: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
Actual base HEAD: `03d5e376fba19c1cd48f15fb42fe717080dbfeec`  
The revised Architect Contract records its inspection checkpoint as `b5245503067ec7114d514e3f7f87ab7590795383`.

## Existing Components Reused

Reused the existing gateway request and executor, strict account reader, immutable account evidence, startup backing evaluator, fixed capital constant, preflight, and CLI report.

## Files Changed

- `src/polymaker/execution/gateway.py`
- `src/polymaker/rdp_account.py`
- `src/polymaker/risk/rdp_capital.py`
- `src/polymaker/rdp_live.py`
- `src/polymaker/cli.py`
- `tests/test_rdp_account.py`
- `tests/test_rdp_capital.py`
- `tests/test_rdp_live.py`

## Implementation Summary

Added immutable `ExactOpenOrder` records and an opt-in `open_orders(strict_account=True, exact_decimal=True)` path. It parses supported strings, `Decimal` values, and integers before subtraction, calculates remaining quantity exactly, and rejects float source fields and malformed cumulative values. The account reader now requests and retains these exact records. Default gateway reads continue to return the existing float `OpenOrder` objects.

Freshness checks now compare exact decimal representations of supported timestamps and the age limit, so an exact boundary passes while an actual excess fails. The backing evaluator also rejects missing, malformed, or duplicate-normalized spender identities using local address validation.

## Behavior Before

Account backing used exact arithmetic on remaining quantities that had already undergone float subtraction. This could turn a valid `$30` backing result into a shortfall. Float-based timestamp subtraction could also reject an exact freshness boundary, and the evaluator could accept a non-address spender string.

## Behavior After

Exact account reads preserve authoritative decimal quantities through parsing, account capture, and backing evaluation. The reviewed `1.1 − 0.2` partial fill at price `0.5` produces remaining quantity `0.9`, commitment `$0.45`, and `$30` backing from `$30.45`. Genuine cash or allowance shortfalls remain ineligible. Invalid spender identities fail closed.

## Tests Added / Changed

Added actual gateway parser-to-account-to-backing tests for fractional and long-decimal partial fills, exact `$30` backing, and independent micro-dollar cash and allowance shortfalls. Added exact-mode rejection cases for missing cumulative fields, floats, booleans, nonfinite values, negative quantities, and matched quantity exceeding original quantity. Verified default float `OpenOrder` behavior remains unchanged.

Added exact freshness-boundary and immediately-over-boundary cases, spender identity validation cases, and retained startup backing, preflight, and CLI outcome coverage.

## Tests Executed

- `uv run pytest -q tests/test_rdp_account.py tests/test_rdp_capital.py tests/test_rdp_live.py` — **181 passed**
- `uv run pytest -q tests/test_execution.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py tests/test_settlement_sweep.py` — **254 passed**
- `uv run pytest -q` — **1127 passed, 2 skipped**
- Ruff across all eight changed source and test files — **passed**
- `uv run polymaker rdp-live --help` — **passed**
- `git diff --check` — **passed**

## Test Results

All targeted, affected, and full-suite checks passed on the final tree.

## Regression Results

Legacy gateway return behavior and existing capital projection/admission behavior passed. Account, preflight, CLI, execution, gateway, and predecessor regression tests passed.

## Git Diff Summary

Eight files changed: **833 insertions, 38 deletions**. No Git history operations were performed. Preserved untracked `research_outputs/` remained untouched.

## Safety Checks

No live reads, account writes, orders, cancellations, signing, RPC, or real-money trading were performed. Exact-mode float fields fail closed; the default gateway path remains unchanged.

## Known Limitations

This verifies exact processing of controlled SDK-shaped responses. It does not establish live account-read compatibility or spendability.

## Deferred Findings

Trading-time cash/order revalidation, authenticated-stream reliability, controller startup baseline consumption, position-reader precision, and final tick/minimum-size integration remain deferred as recorded in the Architect Contract.

## Reviewer Attention

Please verify exact producer-to-backing partial-fill behavior, unchanged legacy gateway results, strict rejection of unsupported exact-mode source values, and exact freshness and spender-validation boundaries.

## Human Live Trading Performed

NO
