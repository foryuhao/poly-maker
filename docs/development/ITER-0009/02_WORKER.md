# ITER-0009 — Worker Implementation

## Status
IMPLEMENTED

## Architect Contract
Implemented the approved read-only, single-market BTC5m metadata bootstrap and `rdp-live --market <slug> --preflight-only` CLI command. No trading mode was added.

## Base Repository State
Branch: `rdp-phase0-dev`  
HEAD: `f8ce0378e1028e3796640ba53318f754882b0fb2`

## Existing Components Reused
Reused the existing Typer application, `Config`, structured logging, `GammaClient.market_by_slug()`, `parse_market()`, BTC5m interval helpers, `MarketMeta`/`TokenMeta`, and the existing `httpx` dependency.

## Files Changed
- `src/polymaker/cli.py`
- `src/polymaker/rdp_live.py` — new
- `tests/test_rdp_live.py` — new

## Implementation Summary
Added a bootstrap that validates the canonical aligned BTC5m slug, exact official Gamma/CLOB origins, explicit Gamma tradability and market identity, Up/Down token mapping, and timezone-aware interval end. It performs an exact Gamma lookup and separate bounded public `/book` GETs for both tokens, validating book identity, `neg_risk`, tick size, and minimum order size.

The immutable result retains the parsed market and each token’s venue metadata separately, without using Gamma defaults for book ticks or minimum sizes. Successes and categorized failures are logged to `rdp-live-preflight.jsonl`.

The CLI requires `--preflight-only`, exits before network access if it is omitted, and does not construct an Engine or connect to the execution gateway.

## Behavior Before
The application had no RDP command for explicitly selecting one BTC5m market and validating its public per-token CLOB metadata.

## Behavior After
A user can run `uv run polymaker rdp-live --market <btc-updown-5m-epoch> --preflight-only` to validate one exact market using official public GETs. The command records structured evidence and reports that no real-money trading was performed.

## Tests Added / Changed
Added mock HTTP and CLI tests for exact market selection, distinct token metadata, malformed or contradictory responses, origin restrictions, timestamps and expiration, bounded failure behavior, durable CLI evidence, exit codes, and forbidden Engine/gateway paths.

## Tests Executed
- `uv run pytest -q tests/test_rdp_live.py`
- `uv run pytest -q tests/test_rdp_live.py tests/test_btc5m.py tests/test_catalog.py tests/test_config_aliases.py tests/test_favorite_live.py tests/test_engine.py`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/rdp_live.py src/polymaker/cli.py tests/test_rdp_live.py`
- `uv run polymaker rdp-live --help`
- `git diff --check`

## Test Results
Final focused bootstrap suite: **47 passed**.  
Affected regression set: **135 passed** before the final malformed-Gamma-envelope cases were added.  
Final full suite, including those cases: **1,002 passed, 2 skipped**.  
Ruff and `git diff --check`: **passed**.  
CLI help displayed the required `--preflight-only` option and read-only scope.

Early focused runs exposed test harness issues involving RESPX’s uncalled-route default, query-parameter comparison, and the reused Gamma client’s exact-slug filtering. The test expectations were corrected; the final focused suite passes.

## Regression Results
The final full suite passed. No existing FL or catalog behavior was changed.

## Git Diff Summary
The tracked diff adds only CLI registration. The two new authorized module/test files are untracked pending Orchestrator archival. Existing untracked `research_outputs/` assets were preserved. No Git history operations were performed.

## Safety Checks
The bootstrap uses only official Gamma and CLOB HTTPS origins and GET requests, with bounded HTTP timeouts and connection limits. CLI tests patch Engine construction and gateway connect/place/cancel methods to fail if invoked. No account authentication, order actions, or live network validation were performed.

## Known Limitations
This preflight validates public market selection and metadata only. It does not establish WebSocket readiness, account state, CapitalGate, quote eligibility, or trading readiness.

## Deferred Findings
Runtime tick provenance and refresh, account readiness, per-token minimum-size integration, and strategy lifecycle orchestration remain deferred.

## Reviewer Attention
Verify origin rejection occurs before requests, result/failure evidence is durably logged, and the command cannot enter Engine or execution paths.

## Human Live Trading Performed
NO
