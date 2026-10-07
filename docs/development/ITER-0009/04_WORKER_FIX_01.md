# ITER-0009 — Worker Implementation

## Status
IMPLEMENTED

## Architect Contract
Repair MF-01–03 from `docs/development/ITER-0009/03_REVIEWER.md` within the approved bootstrap and test scope.

## Base Repository State
Branch: `rdp-phase0-dev`  
HEAD: `f8ce0378e1028e3796640ba53318f754882b0fb2`

## Existing Components Reused
Retained the existing exact Gamma lookup, `parse_market()`, `MarketMeta`, public CLOB GET path, Typer CLI, and structured logging.

## Files Changed
This repair changed only:

- `src/polymaker/rdp_live.py`
- `tests/test_rdp_live.py`

## Implementation Summary
- Enforced the positional `Up`, then `Down` outcome mapping before any CLOB book reads.
- Parse CLOB JSON numeric literals as `Decimal` before validation. The bootstrap now rejects values whose conversion to the returned float loses precision, while accepting ordinary numeric strings.
- Copy and validate Gamma/CLOB endpoint strings before the first await, then use those snapshots throughout bootstrap.

## Behavior Before
Reversed token outcomes could pass validation; lossy float conversion could lower a venue minimum or move a tick to `1.0`; and configuration mutation during Gamma lookup could redirect subsequent CLOB requests.

## Behavior After
Reversed outcomes fail before book reads. Precision-losing metadata fails explicitly. Mutating configuration after lookup does not change the already validated request origins.

## Tests Added / Changed
Added regression cases for reversed outcomes, string and raw JSON precision loss, and configuration mutation during Gamma lookup. The mutation test verifies that the unapproved host receives no request.

## Tests Executed
- `uv run pytest -q tests/test_rdp_live.py`
- `uv run pytest -q tests/test_rdp_live.py tests/test_btc5m.py tests/test_catalog.py tests/test_config_aliases.py tests/test_favorite_live.py tests/test_engine.py`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/rdp_live.py src/polymaker/cli.py tests/test_rdp_live.py`
- `git diff --check`

## Test Results
- Focused bootstrap suite: **52 passed**
- Affected regressions: **142 passed**
- Full suite: **1,007 passed, 2 skipped**
- Ruff and diff whitespace checks: **passed**

## Regression Results
The full suite passed on the repaired tree.

## Git Diff Summary
Only the approved bootstrap module and its tests changed in this repair. Orchestrator-owned audit/index artifacts and preserved `research_outputs/` remain untouched. No Git history operations were performed.

## Safety Checks
No live HTTP reads, account authentication, Engine startup, gateway actions, orders, or cancellations were performed. Validation used mocked HTTP requests.

## Known Limitations
The bootstrap remains a public metadata preflight; it does not establish trading readiness.

## Deferred Findings
WebSocket tick provenance, account readiness, execution integration, and lifecycle orchestration remain deferred.

## Reviewer Attention
Verify reversed outcome order fails before book requests, precision-loss cases fail without rounding, and configuration mutation cannot redirect CLOB requests.

## Human Live Trading Performed
NO
