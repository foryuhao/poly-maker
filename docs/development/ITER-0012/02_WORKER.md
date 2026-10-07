# ITER-0012 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented `docs/development/ITER-0012/01_ARCHITECT.md`: retain complete immutable account-order evidence and selected condition identity; evaluate read-only backing for an initially empty, fixed-$30 RDP run; report backing separately in account preflight and CLI.

## Base Repository State

Repository: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
Actual base HEAD: `b5245503067ec7114d514e3f7f87ab7590795383`  
The Architect artifact records its inspection checkpoint as `220d964b37839cd3b7a196309549cacd7a330fb7`.

Preserved untracked `research_outputs/` was present and left untouched.

## Existing Components Reused

Reused the authenticated account snapshot and official gateway readers, `RDPAccountSnapshot`, existing `risk/rdp_capital.py` fixed capital and admission gate, and existing `rdp-live --account-check` reporting path.

## Files Changed

- `src/polymaker/rdp_account.py`
- `src/polymaker/risk/rdp_capital.py`
- `src/polymaker/rdp_live.py`
- `src/polymaker/cli.py`
- `tests/test_rdp_account.py`
- `tests/test_rdp_capital.py`
- `tests/test_rdp_live.py`

## Implementation Summary

Account snapshots now retain every validated open order and the selected market condition ID, while preserving selected-order and order-count fields. Older snapshots without complete evidence remain usable for existing callers but cannot certify startup backing.

Added an immutable startup backing result and synchronous evaluator. It validates order collection completeness, count and unique IDs, selected-order consistency, exact selected-market token/outcome mapping, empty selected positions and orders, required-spender evidence, and ordered fresh timestamps before market expiry. It calculates all-account BUY commitments from remaining quantity, excludes SELL orders from cash commitments, and evaluates observed cash and allowance independently against the fixed `CAPITAL = $30.00`.

Commitment and backing arithmetic uses exact Decimal coefficient/exponent operations. The preflight reports eligibility and reason separately from account-read success; CLI output shows the fixed capital, commitment, backing amounts, and read age.

## Behavior Before

Account evidence contained only selected-market orders and an order count, so foreign BUY commitments could not be included in startup backing. Account preflight had no separate result for whether observed cash and allowance supported an initially empty $30 run.

## Behavior After

A complete, fresh account snapshot can certify an empty selected-market baseline and independently show cash and allowance backing after all observed BUY commitments. Existing selected inventory or orders, incomplete or inconsistent evidence, stale reads, or backing below $30 produce explicit ineligible results. The existing capital projection and NORMAL/REBALANCE gate remain unchanged.

## Tests Added / Changed

Added coverage for exact $30 and one-micro-dollar shortfalls; fixed capital despite higher wallet cash; multiple BUYs, SELL exclusion, and remaining-quantity commitments; one-sided and balanced selected positions; selected orders; missing or inconsistent order collections, duplicates, malformed economics and scope; timestamp ordering, freshness boundaries and market expiry; Decimal precision; immutability; actual account-reader retention; account preflight outcomes; and CLI eligible/ineligible reporting.

Existing capital projection and NORMAL admission interoperability is checked using the real processor fixtures.

## Tests Executed

- `uv run pytest -q tests/test_rdp_capital.py tests/test_rdp_account.py tests/test_rdp_live.py` — **161 passed**
- `uv run pytest -q tests/test_execution.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py tests/test_settlement_sweep.py` — **254 passed**
- `uv run pytest -q` — **1107 passed, 2 skipped**
- Ruff across all seven changed source and test files — **passed**
- `uv run polymaker rdp-live --help` — **passed**
- `git diff --check` — **passed**

## Test Results

All targeted, affected, and full-suite checks passed on the final tree.

## Regression Results

Existing capital projection and admission behavior passed unchanged. Account-read, RDP CLI, gateway, execution, and predecessor regression tests passed.

## Git Diff Summary

Seven files changed: **583 insertions, 9 deletions**. No Git history operations were performed. The only untracked assets outside iteration artifacts were preserved `research_outputs/`.

## Safety Checks

No gateway changes, trading-time controller changes, network requests, orders, cancellations, account writes, or real-money trading were performed. The startup backing result grants no send authority and does not adopt existing account state.

## Known Limitations

Account reads are sequential, so eligibility describes the captured startup evidence and does not replace later trading-time revalidation.

## Deferred Findings

Continuous cash, allowance, and order revalidation; authenticated-stream freshness invalidation; controller startup baseline consumption; and per-token minimum-size/tick-provenance integration remain deferred as recorded in the Architect Contract.

## Reviewer Attention

Please verify exact backing arithmetic at boundary values, complete-order evidence and selected-subset consistency, timestamp freshness/expiry checks, and the separation between account-read success and backing eligibility.

## Human Live Trading Performed

NO
