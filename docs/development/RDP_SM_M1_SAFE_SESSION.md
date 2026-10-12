# RDP-SM M1 — Safe Session

## 1. Baseline

- Date: 2026-10-11 (Asia/Shanghai).
- Branch: `fl-restored-97ab0f9`.
- HEAD and original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`; identical, no ancestry ambiguity.
- Strategy: RDP-SM SOP v1.8 FROZEN. No trading rules changed.
- Architecture: FL-first / Thin SM Orchestrator.
- M0: CLOSED_WITH_FINDINGS, independent Reviewer FAIL retained; Architect authorized M1, waived M0 rework, deferred H01/H02/H03 to M2. MicroLive NOT AUTHORIZED.
- Initial working tree: existing untracked M0 feasibility report, independent review report, offline runner and feasibility tests. Preserved. No initial tracked production changes or staged changes.
- Final working tree: section 12. No commit, reset, checkout or historical RDP branch inspection performed.

## 2. Objective

Deliver M1-01, M1-02 and M1-03 together: one runnable, independent, read-only preflight for an exact BTC Up/Down 5m market, reporting SESSION_READY, SESSION_REJECTED or SESSION_BLOCKED.

Scope: official metadata, two token books and dynamic ticks, frozen trading-time conditions, account identity, selected-market cleanliness, account-wide open BUY commitment, collateral balance and applicable allowance, structured CLI output and offline evidence.

Excluded: orders/cancellation, Engine/FL strategy startup, RPC, settlement/MERGE/REDEEM, Close Barrier, trading inventory state machine, CapitalGate/reservation, database, scheduler/recovery, NORMAL/REBALANCE, M2 implementation and MicroLive. M0 tests and reports are not repaired.

## 3. FL Component Reuse

Line references refer to this working tree; all listed shared components are unchanged except the additive CLI entry.

| Component | Existing Source | Reused Interface | SM Adapter | Evidence |
|---|---|---|---|---|
| BTC5m identity | `src/polymaker/btc5m.py:23`, `:34` | `PERIOD_S`, `slug_for_epoch`; epoch utility in fixtures | Exact slug epoch, alignment and official endDate verification | M1 test `test_t01_t02_t03_t12_integrated_ready_identity_mapping_tick` |
| Official metadata | `src/polymaker/catalog/gamma.py:62`, `:134` | `GammaClient.market_by_slug`, `parse_market` | Strict raw flags/identity/duration/minimum; pair explicit Up/Down outcomes to tokens and reorder YES/NO | Reversed Down/Up fixture; missing/malformed metadata tests |
| Market data | `src/polymaker/marketdata/parse.py:78`; `orderbook.py:59`, `:77`, `:167` | `parse_book`, real `OrderBook.apply_snapshot/view` | Validate raw identity and levels before permissive FL parser; separate YES and NO books | Dynamic .01/.001 ticks and direct best prices asserted |
| SDK official reads | Installed `py_clob_client_v2/client.py:279`, `:331`, `:346`, `:499`, `:534`, `:687` | Server time, book, tick, derive existing API key, paginated authenticated orders, collateral reads | `ReadOnlyClobClient` subclasses pinned SDK; allowlisted GET, denied POST/DELETE and mutating GET; strict version/order-page validation | Real SDK transport receives Fake HTTP; write-boundary and read-failure tests |
| Account reads | `src/polymaker/execution/gateway.py:595`, `:653`, `:751`, `:770` | `open_orders`, `positions`, `balance_allowance`, `required_collateral_spenders` | Real Gateway, ephemeral SDK binding; strict capital and order-identity checks | Inventory, target orders, cross-market commitment, proxy funder, malformed rows, read failure tests |
| Blocking I/O and teardown | `gateway.py:97`, `:100` | Real `_io`, `close` | Await reads; close pool in finally, no background loop | Repeated preflight, no persistent artifacts |
| Collateral units/allowance | `src/polymaker/favorite_live.py:183` | Pure `collateral_readiness` | Reject bool/NaN/absent/fractional micro-unit amounts before helper | T11; no FL live preflight invocation |
| Config/logging/CLI | `src/polymaker/config.py:275`; `logging.py:14`; `cli.py:41` | `Config.load`, stderr `configure`, existing Typer app | One independent command; JSON and exit codes | T13/T14 plus REJECTED/BLOCKED CLI tests |

Deliberately not reused: `resolve_btc5m_market` writes CatalogStore; `Gateway.connect` may create an API key; FL live account baseline can read token balances via RPC. SM bypasses these initialization paths. Gateway/account/client logic is not copied.

## 4. Architecture

```text
existing polymaker Typer CLI: rdp-sm-preflight
  -> Config.load + logging.configure(stderr only)
  -> rdp_sm.run_preflight
     -> existing GammaClient.market_by_slug -> existing parse_market + SM validation
     -> real ExecutionGateway + same installed SDK, read-only subclass
        -> local signer/configured funder -> derive EXISTING API key (GET only)
        -> SDK get_order_book/get_tick_size, separately for both tokens
           -> existing parse_book -> existing OrderBook.view
        -> Gateway.positions/open_orders/required_collateral_spenders/balance_allowance
           -> existing collateral_readiness
        -> SDK get_server_time -> freshness and frozen time-window checks
     -> structured PreflightResult -> close Gateway pool
  -> JSON stdout; exit 0 READY / 1 REJECTED / 2 BLOCKED
```

External interface inventory is limited to existing official HTTP reads:

| Host | GET paths | Purpose |
|---|---|---|
| `gamma-api.polymarket.com` | `/markets?slug=...&limit=1` | Exact official market metadata |
| `clob.polymarket.com` | `/auth/derive-api-key`, `/book`, `/tick-size`, `/data/orders`, `/version`, `/balance-allowance`, `/time` | Existing credentials, market/account reads, applicable exchange version and time |
| `data-api.polymarket.com` | `/v2/positions?user=<funder>` with pagination | Existing FL position reader |

Chain must be 137 and configured hosts must match these official hosts. No new external interface, WS, RPC or third-party service is introduced. SDK contract configuration is local data, not an RPC request. No external requests were actually sent during development/testing.

The SM-side adapter is `src/polymaker/rdp_sm.py`: transport boundary at :84, ephemeral account composition at :153, market validation at :217, book validation at :276, account checks at :317, integrated preflight at :377. It has no persisted execution state.

## 5. Market Preflight

- Identity: exact `btc-updown-5m-<epoch>`, aligned positive epoch, official returned slug and 32-byte condition ID. Official endDate must equal epoch + 300 seconds. If eventStartTime is supplied it must agree. Gamma startDate may be publication time and is not treated as the session start.
- Mapping: official parallel `outcomes` and `clobTokenIds` arrays, exactly Up and Down, unique numeric uint256 tokens. Up maps to YES; Down maps to NO. Titles and array position do not determine YES/NO.
- Tradability: explicit boolean active/closed/acceptingOrders/enableOrderBook/negRisk; known nontradable -> REJECTED, missing or malformed -> BLOCKED.
- Books: official CLOB snapshot for each actual token; asset and condition must agree. Nonempty, finite valid levels, uncrossed best prices. NO prices are not synthesized from YES.
- Tick: fresh per-preflight SDK token tick read must agree with its snapshot tick and installed SDK-supported rounding configuration. Mismatch -> BLOCKED.
- Freshness: existing FL `risk.ws_stale_halt_s` (default 10 seconds). Compare each book timestamp with official CLOB time; maximum future tolerance 2 seconds. Entire preflight must finish within that age budget. Slow or stale reads -> BLOCKED.
- Window: `15 <= elapsed < 240`. 0 and 14.999 rejected; 15 and 239.999 eligible; 240 and 300 rejected. Negative/out-of-session elapsed also cannot be READY.
- READY is a time-stamped snapshot. No order window, cancellation scheduler or authorization lease is created. M2 must recheck actual execution time and keep execution/cancellation outside the final 60-second exclusion.

## 6. Account Preflight

- Identity: locally derived signer, valid nonzero configured SDK funder, supported signature type, successfully derived existing CLOB credentials, SDK signer/funder consistency. EOA signature type requires signer=funder. Proxy positions are queried by funder, proven with differing signer/funder fixture.
- Identity provenance is explicit in output. This does not independently prove proxy ownership on-chain; it follows existing FL configured identity plus authenticated CLOB access. No blockchain verification or RPC is claimed.
- Positions: real Gateway paginates official v2 positions and validates fields/cursors. Target YES and NO inventory must each be exactly zero. Read failure/missing size is BLOCKED; positive target inventory is REJECTED. Other markets' inventory alone does not reject.
- Orders: actual authenticated SDK pagination and Gateway remaining-size calculation. Any target-token pre-existing open order rejects; the session creates none, so no SM-owned/external ambiguity is introduced.
- Missing order cumulative sizes, duplicate IDs, malformed commitment or cycling pagination block. All account-wide remaining BUY notional is subtracted from reported collateral and applicable allowance. Existing non-target orders can exhaust funds even when the target is clean.
- Capital: official COLLATERAL six-decimal balance and allowance for actual locally configured exchange spender(s), selected by authoritative official version and negRisk metadata. Failed, missing, NaN/bool or invalid-unit values -> BLOCKED; known insufficient balance/allowance -> REJECTED.
- Output distinguishes official balance, allowance, existing BUY commitment and conservative available balance. If official balance already excludes reservations, subtracting again is conservative; its live semantics were not independently verified.
- `TestCapitalLimit=30` caps the reported basic budget: `min(30, balance - existing BUY commitment, allowance - existing BUY commitment)`, each available amount floored at zero. It does not require a $30 balance; the $10 fixture is READY.
- Basic eligibility uses only the necessary lower bound for two venue-minimum buys at minimum legal tick prices: `orderMinSize * (yes_tick + no_tick)`. This is not a strategy quote, assurance that an actual chosen pair is affordable, or reservation. M2 must check actual commitments and `CapitalUsed + NewCommitment <= 30` immediately before placement. No USDC/pUSD conversion or settlement is executed.

## 7. CLI & Safety

```bash
.venv/bin/polymaker rdp-sm-preflight btc-updown-5m-<start_epoch> --config-dir config
```

Use the exact official slug for the selected market. Default and only mode is READ_ONLY. Existing FL config/credential conventions apply during authorized real reads; tests use deterministic dummy credentials and never read live credentials.

Read-only enforcement is structural: SDK `_post` and `_delete` always raise, `_get` requires an approved read path, and `/balance-allowance/update` is denied. API key creation, order submission, cancellation and heartbeat cannot reach this client's transport. No `Gateway.connect`, Engine, CatalogStore, StateStore, FL strategy or settlement runner is started. The real Gateway constructor's rate buckets/pool are transient dependencies; no execution ownership/session database is created.

Failures produce BLOCKED with sanitized exception type, rather than assuming empty inventory/orders. READY/REJECTED/BLOCKED JSON is separate from stderr logs; exit codes 0/1/2 are tested. Repeated calls recreate readers and reload official state; READY is never cached.

Observed fixture output fields (asserted through actual preflight and CLI):

```json
{
  "status": "SESSION_READY",
  "read_only": true,
  "trading_authorized": false,
  "test_capital_limit_usdc": 30.0,
  "checked_at": 1800000060.0,
  "time_source": "OFFICIAL_CLOB_TIME"
}
```

Full output also contains market/account dictionaries and named checks: market, account_reads, clean_market, capital, freshness, trading_window. Fixture market YES=1/NO=2, ticks=.01/.001, elapsed=60; capital budget=30. This is offline evidence, not a live market/account result.

## 8. Test Results

Environment: Linux, repository `.venv`, Python 3.12.14, installed pinned `py-clob-client-v2==1.0.2`, pytest/pytest-asyncio. No installation performed.

New file `tests/test_rdp_sm_m1.py`: 46 collected test cases after parametrization. Tests execute real SM, CLI, Gateway readers, SDK pagination/auth/read logic, GammaClient/parser, book parser and OrderBook. Only official HTTP transport/responses are faked, plus explicit forbidden-path sentinels and deterministic time advancement. CLI fixture supplies dummy config and restores logging globals after each case.

| Requirement | Actual evidence | Result |
|---|---|---|
| T01 Market identity | integrated test :158 asserts condition, slug-derived start and official end | PASS |
| T02 Mapping | same test asserts reversed Down/Up input gives YES=1 / NO=2 | PASS |
| T03 Dynamic tick | same test asserts independent .01/.001; tick mismatch blocks | PASS |
| T04 Missing/invalid data | :222, eight missing/malformed/stale cases assert BLOCKED and correct failing stage | PASS |
| T05 Early time | :200 at 0 and 14.999 | PASS |
| T06 Eligible time | :200 at 15 and 239.999 | PASS |
| T07 Late time | :200 at 240 and 300 | PASS |
| T08 Inventory | :260, YES and NO positions independently reject | PASS |
| T09 External target orders | :276, target count and rejection | PASS |
| T10 Failed reads | :305, six paths assert failed endpoint actually reached, BLOCKED and correct stage | PASS |
| T11 Capital | :331, seven known/unknown capital cases; :282 tests cross-market commitment | PASS |
| T12 Structure | :158 JSON serialization and all check names/outcomes | PASS |
| T13 CLI isolation | :354 forbidden Engine/DB constructors; actual app produces READY | PASS |
| T14 No writes | fixture denies Gateway trading/RPC paths; HTTP method/host assertions; :371 denies writes/mutating GET | PASS |
| T15 Repeated reads | :383 subsequent external order changes READY to REJECTED; fresh key/tick reads; no artifacts | PASS |

Additional checks: proxy/funder identity and EOA mismatch (:184), malformed position/order quantities and duplicate identities (:395), cyclic order pagination (:409), CLI rejected/blocked JSON/exit codes (:439), expired total snapshot (:452). Tests don't replace Gateway readers with fake successes.

Final command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight'
```

```text
164 passed, 1 deselected in 1.54s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

Breakdown: 46 M1 + 118 existing regression cases; 0 failures, 0 skipped, 1 explicitly deselected regression. The unchanged M0 runner clears inherited credentials before imports, uses a temporary CWD, denies socket/DNS operations, blocks workspace `.env`/`.aws` reads, disables pytest auto-plugins/cache. M1 adds MockTransport and forbidden write/RPC/runtime sentinels. No denied network attempts occurred.

Earlier combined run, before adding the final proxy test, used the same command without `-k`: **163 passed / 1 failed**. Existing `test_config_aliases.py::test_cli_conflict_stops_before_settlement_preflight` invokes `Config.load` with dotenv search, which attempted workspace `.env`; the credential guard raised before its expected ConfigAliasError. This is an offline isolation incompatibility, retained as evidence. It was deselected without changing the test, runner or credential protection. It is not counted as a passing regression. Intermediate configuration-field and clock-fixture defects found during development were corrected before the final run.

```bash
.venv/bin/ruff check src/polymaker/rdp_sm.py src/polymaker/cli.py tests/test_rdp_sm_m1.py
git diff --check
```

Both passed. Worker self-check only; no independent M1 Reviewer PASS is claimed.

Real API smoke: **NOT_VERIFIED**. No live credentials, official endpoints, real funds or RPC were accessed. Fake evidence establishes composition and safety under the tested schemas; it does not establish current endpoint availability, live authentication/funder ownership, exact live balance reservation semantics or atomicity across market/account reads.

## 9. MicroLive Impact

| Issue | Impact | Blocker | Decision |
|---|---|---|---|
| Normal Gateway connection may create API key; FL account path may use RPC | Unsafe for M1 read-only scope | YES for M1, contained in delivered path | FIX NOW: derive-only SDK binding and explicit transport boundary |
| Permissive SDK/version and order-size defaults | Could report known capital from unknown evidence | YES for M1, contained in adapter | FIX NOW: strict version/page/quantity requirements |
| Slow/stale snapshots and changed token ticks | READY could describe expired/inconsistent reads | YES for M1, contained by checks | FIX NOW: whole-preflight/book age checks and separate token tick agreement |
| M0-H01/H02/H03 | Unsafe terminal/reconciliation/UNKNOWN handling in future execution | YES before MicroLive, NO for read-only M1 | DEFER to M2 per Architect |
| Actual chosen order commitment/time checks absent | M1 snapshot cannot authorize execution | YES before MicroLive, NO for M1 | DEFER: explicitly required in M2 |
| Real official read availability/authentication not smoke-tested | Offline evidence cannot establish live readiness | NO for offline M1 delivery | DEFER: optional authorized read-only verification |
| One unrelated settlement CLI regression reads workspace dotenv | Prevents that regression under strict credential guard | NO for M1 | DEFER; preserve test/guard, record deselection |
| Generic session framework, persistence, market scoring | No demonstrated M1 need | NO | DROP |

## 10. Deferred Issues

Architect-mandated M2 ownership, with original reports unchanged:

- **M0-H01:** terminal cache not invalidated by later LIVE.
- **M0-H02:** late CONFIRMED can reuse stale COMPLETE.
- **M0-H03:** UNKNOWN placement not accounted for; retry may be incorrectly allowed.

See `RDP_SM_M0_EXECUTION_FEASIBILITY.md` and `RDP_SM_M0_INDEPENDENT_REVIEW.md` (independent review identifiers M0-IR-01/02/03). No assertion that these are repaired or contained in actual SM execution is made. All three require resolution or demonstrated containment against M2's actual implementation before MicroLive authorization.

Other material limitations: M2 must refresh time, capital, account and execution evidence immediately before submitting; M1 has no reservation or cross-read atomicity. Offline tests do not prove live official API schema/availability. Proxy ownership provenance remains configured identity plus authenticated access, not on-chain proof. No automatic API-key creation/recovery is provided; missing existing credentials block.

## 11. Final Result

**CODE_READY**

All M1 functionality is delivered in one thin adapter and one additive command. Forty-six offline M1 cases and 118 associated regressions pass under executable network/credential isolation. Market and account preflight, structured statuses, frozen time boundaries, absence of write/runtime paths and repeated read-only execution are tested. One unrelated credential-guard-incompatible regression remains explicitly deselected.

This is Worker evidence for independent review, not formal M1 acceptance, live SESSION_READY, M2 completion or MicroLive authorization. The Architect's M0 closure/disposition is retained.

## 12. Change Manifest

M1 changes only:

1. Created `src/polymaker/rdp_sm.py` — read-only preflight and thin SDK/account adapter.
2. Modified `src/polymaker/cli.py` — additive `rdp-sm-preflight` command (22 lines), existing commands unchanged.
3. Created `tests/test_rdp_sm_m1.py` — offline integrated tests.
4. Created this report, `docs/development/RDP_SM_M1_SAFE_SESSION.md`.

Shared production file change: **YES, CLI integration only**. Shared Gateway, Tracker, FillTruth, Engine, FL strategy/runtime, configuration and market/account components: **unchanged**. No high-risk shared-core change. No M0 file change, SOP edit or other project-file modification. Original M0 report/runner/test SHA-256 values remain:

```text
07150ef03996de612b93cf0f23a9d38e919fc8e000b5a0f5d6ca96829391194f  RDP_SM_M0_EXECUTION_FEASIBILITY.md
c820947c5bf634eadf215fb7e7d75af5e47a47222cd842b209ae4e8dbf3caa9d  rdp_sm_m0_offline.py
1b43018383aa3a1ea4b3968bb1c74b1e5d000cf1255ea8abca59915fbae91750  test_rdp_sm_m0_execution.py
```

Final `git status --short --untracked-files=all` (includes preserved M0 inputs):

```text
 M src/polymaker/cli.py
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? docs/development/RDP_SM_M0_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_SAFE_SESSION.md
?? src/polymaker/rdp_sm.py
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
?? tests/test_rdp_sm_m1.py
```

Staged diff empty. HEAD remains original FL baseline. No commit made.

## 13. Next Minimal Action

Independent M1 Review.
