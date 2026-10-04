# M4A Real Market #2 Polygon Token-Balance RPC Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `m4a-a1-pass` (`97ab0f9`), with the existing unreviewed first-live startup repair still present in the working tree.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`).
Architecture reference: `docs/architecture/M4A_ARCHITECTURE.md` (AE-1 one-market controlled-live plan); `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md` remains unchanged.

## Scope implemented

Real Market #2 reached Engine startup, market/user WebSockets, heartbeat, and orders/positions REST, then aborted before trading because funder token balances could not be verified. A later manual `token_balances(...)` returned valid zero YES/NO balances. The observed fault is intermittent public Polygon RPC read reliability combined with long per-endpoint failover latency; the evidence does not establish that zero balances caused the abort.

Set the `token_balances()` HTTPProvider timeout from 20 to 5 seconds per endpoint. On each failed endpoint, log `token_balance_rpc_failed` with only the parsed hostname and exception class name. Existing endpoint order and deduplication remain intact. After all endpoints fail, the function still returns `None`.

## Files added

- `tests/test_gateway_token_balances.py`: no-network failover, all-fail, zero-balance, safe-log, and preflight distinction regressions.
- This report.

## Files modified

- `src/polymaker/execution/gateway.py`: timeout and safe per-endpoint diagnostic.
- `docs/CURRENT_STATUS.md`: current blocker and handoff.

The pre-existing startup repair in `livecfg/strategy.toml`, `tests/test_favorite_live.py`, and `docs/reports/M4A_A1_STARTUP_IMPLEMENTATION_REPORT.md` was preserved; this task did not edit those files.

## Important implementation decisions

The log emits `host` and `error_type` only. It does not pass the exception message or RPC URL to the logger; a configured URL with user info, path, and query therefore does not expose those fields. Malformed URL parsing yields `host=unknown` without changing failover. No extra retry layer was added.

## Deviations from architecture

- NONE. This is a bounded gateway read-reliability repair; it changes neither Research schema nor live decision authority.

## Tests added

- First configured RPC fails, second returns actual nonzero YES/NO balances; asserts 5-second timeout and safe logging for a URL containing a test credential.
- All three distinct RPC hosts fail; asserts `None`, three safe failure logs, and 5-second timeout.
- Zero YES/NO balances are returned as `0.0`, with no failover.
- FavoriteFirst `_account_baseline` distinguishes `None` from a verified zero balance.

## Tests executed

- `.venv/bin/pytest tests/test_gateway_token_balances.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py -q -k 'not test_required_spender_follows_client_order_version_and_market_risk'`: **74 passed, 1 deselected**. The excluded unchanged spender test is a previously documented environment hang.
- `.venv/bin/ruff check src/polymaker/execution/gateway.py tests/test_gateway_token_balances.py`: passed.
- `git diff --check`: passed.

## Test results

The named gateway and FavoriteFirst preflight regressions passed locally. This is a Worker test result, not an independent Review PASS. No RPC network traffic or live trading was run.

## Known limitations / unresolved issues

The 5-second timeout applies to each HTTP request, not to the entire multi-token batch or full endpoint sequence. An intermittent public endpoint can still fail; all-endpoint failure remains a preflight abort. No real-market retry or M4A acceptance claim is made. The startup repair remains unreviewed in this working tree.

## Safety / behavior impact

Balance safety semantics changed: **NO**. `None` remains unverified and fail-closed; verified zero remains valid. FavoriteFirst trading semantics changed: **NO**. Research/A1 changed: **NO**. No changes to collateral, order logic, Fill Truth, DP, or MERGE.

## Git diff summary

One production source file modified, one focused test file added, this report added, and current status updated on top of the pre-existing unreviewed startup repair. No commit or PASS tag was created.

## Handoff to Reviewer

Review the complete tracked and untracked working tree against `m4a-a1-pass`, distinguishing the pre-existing startup repair from this RPC repair. Verify timeout scope, hostname-only logging including a configured sensitive URL, failover result, zero-balance validity, and all-fail `None` behavior. Both repairs require independent review before any further live attempt. No live run is part of this task.
