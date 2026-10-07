# ITER-0008 — Worker Implementation

## Status
IMPLEMENTED

## Architect Contract
Implemented the approved optional per-token signing tick handoff in the existing execution gateway. Preserved legacy signing behavior when the option is omitted.

## Base Repository State
Branch: `rdp-phase0-dev`  
HEAD: `cb80a59c2234d7380c91ef234b588163e8597e3a`

The worktree initially contained only preserved, untracked `research_outputs/`.

## Existing Components Reused
Reused the existing `ExecutionGateway.place()` flow, SDK signing and `PartialCreateOrderOptions`, minimum-size checks, pre-submit and final-submit guards, GTC/post-only submission, strict pair parser, and fake execution client.

The installed SDK’s `TickSize` type supplies the accepted tick values. The gateway passes the matching supported string to the SDK and leaves its cached-tick validation intact.

## Files Changed
- `src/polymaker/execution/gateway.py`
- `tests/test_execution.py`

## Implementation Summary
Added optional `token_ticks` input to `place()`. When supplied, the gateway copies the quote list and mapping before its first await, then validates the opt-in guard/placement flags, quote shape, exact token-key set, SDK-supported positive finite ticks, and finite on-grid prices strictly inside `(0, 1)`. Signing options are constructed per quote with that token’s tick and existing `meta.neg_risk`.

No tick fallback, repricing, private SDK cache mutation, or caller/controller wiring was added. Without `token_ticks`, the existing market-level signing option path remains.

## Behavior Before
Every quote used one signing tick from `MarketMeta.tick_size`, even when the two market outcomes had different current ticks.

## Behavior After
Opt-in single or pair placement signs each quote using its copied per-token tick. Invalid requests fail before signing or posting. Existing guards still block submission if state changes during signing.

## Tests Added / Changed
Added coverage for distinct YES/NO ticks, single-outcome YES and NO placement, successive tick changes, invalid mapping keys and values, unsupported SDK ticks, invalid/off-grid prices, invalid flags/guards/shapes, mutation during the rate-limit await, guard denial before and after signing, SDK signing rejection, minimum-size denial, paper-mode validation, GTC/post-only behavior, and partial pair acknowledgment.

## Tests Executed
- `uv run pytest -q tests/test_execution.py -k per_token`
- `uv run pytest -q tests/test_execution.py`
- `uv run pytest -q tests/test_rdp_quotes.py tests/test_rdp_lifecycle.py tests/test_gateway_positions_v2.py tests/test_marketdata.py tests/test_orderbook.py tests/test_engine.py tests/test_favorite_live.py`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/execution/gateway.py tests/test_execution.py`
- `uv run ruff check src/polymaker/execution/gateway.py`
- `git diff --check`

## Test Results
Final results:

- Per-token tests: **38 passed**
- Execution tests: **110 passed**
- Affected RDP, market-data, orderbook, engine, gateway-position, and FL live tests: **216 passed**
- Full suite: **953 passed, 2 skipped**
- Gateway Ruff check: **passed**
- `git diff --check`: **passed**

During test authoring, initial focused runs exposed test-fixture assertion mismatches: two failures (`AttributeError` reading the fake signed payload and an invalid single-order response fixture), followed by one (`TypeError` indexing `OrderArgsV2`). The test assertions/fixture were corrected, and the final focused and execution runs passed. The full suite ran before a final test-only formatting cleanup; the execution module was rerun afterward and passed.

Ruff across both changed files still reports three existing findings in a separate legacy test region: one local import-order issue and two assigned lambdas. The newly introduced Ruff finding was fixed.

## Regression Results
The affected regression set and full suite passed as listed above. No regressions were observed in the tested RDP or FL paths.

## Git Diff Summary
Two tracked files changed: `gateway.py` and `test_execution.py` (332 insertions, 2 deletions). The 748 untracked files under `research_outputs/` were preserved and not modified. No Git history operations were performed.

## Safety Checks
All placement tests used fake clients. No live network requests, orders, or cancellations were made. The implementation retains forced post-only and strict placement requirements for the new mode and performs no POST after validation failure, guard denial, or signing failure.

## Known Limitations
No production caller is wired to provide per-token ticks in this iteration. SDK cached-minimum disagreements safely reject placement; cache refresh policy is deferred.

## Deferred Findings
Runtime composition must provide authoritative per-token ticks and a submit guard that verifies the captured market state and connection generation.

## Reviewer Attention
Verify that invalid inputs fail before signing, quote and tick inputs remain stable across awaits, and the final guard prevents POST after a tick change during signing.

## Human Live Trading Performed
NO
