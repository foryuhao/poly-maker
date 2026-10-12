# RDP-SM M2 — Execution Integrity

## 1. Baseline

- Date: 2026-10-11 (provided project date).
- Project: `/home/ubuntu/poly-maker`; branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`. The original FL baseline is an ancestor and is still HEAD; accepted M0/M1/F01 artifacts are present as workspace additions.
- Strategy: RDP-SM SOP v1.8 FROZEN; FL-first / Thin SM Orchestrator; MicroLive First.
- M0: CLOSED_WITH_FINDINGS, independent reviewer FAIL, Architect waived harness rework. M1: ACCEPTED. F01: CLOSED, independent review PASS. M2: authorized implementation. MicroLive: NOT AUTHORIZED.
- Read all six required M0/M1/F01 reports. Their historical conclusions remain intact.
- No actual SOP v1.8 file was found in the current checkout. The existing `docs/strategy/RDP_PHASE0_FROZEN.md` is an older Phase0 document, not SOP v1.8. Its different timing/strategy rules were not adopted. This implementation uses the Architect's frozen execution contract in the M2 prompt; no SOP file was changed.
- Entry Git status: pre-existing 22-line additive M1 CLI diff, plus untracked M0/M1/F01 source, tests and reports. Staged diff empty. No unrelated conflicting changes, checkout/reset/cleanup, historical RDP branch access or commits.
- Final status is reproduced in section 16. M1 source, CLI, tests and original reports retain their previously recorded SHA-256 hashes.

## 2. Objective

Implement the **actual reusable SM Window Executor** that future M3 can call with one or two already determined BUY Maker quotes. The production library handles pre-submit validation, commitment, sequential submission, the fill deadline, cancellation, terminal verification, confirmed fill projection, reconciliation and a traceable close barrier.

Scope includes only SM orchestration, its necessary SDK/journal adapters and offline integration tests. No NORMAL/REBALANCE decision, size skew, continuous trading loop, multi-market scheduling, automatic recovery, CapitalGate framework, new persistent database, merge/redeem/settlement, shared execution refactor or live authorization. No CLI command capable of live M2 execution was introduced.

## 3. FL Component Reuse

| Component | Existing source | Actual interface | SM usage | Evidence |
|---|---|---|---|---|
| Gateway | `src/polymaker/execution/gateway.py:172`, `:305`, `:595`, `:653`, `:711`, `:733`, `:751`, `:770` | `place`, `cancel`, **`open_orders`** (not an assumed `get_open_orders` Gateway method), `positions`, `get_order`, `get_trades`, `balance_allowance`, `required_collateral_spenders` | Real non-paper Gateway, its executor/rate limiter, SDK signing, strict response parsing and reads; one quote per placement | New integration tests exercise actual SDK HTTP transport; signed maker payload asserted |
| Tracker | `state/tracker.py:87`, `:171`, `:193`, `:328` | Pending capture, registry, `on_trade`, `on_order`, economic fills, quarantine | `_SMProcessor` calls production lifecycle logic; adds only proof invalidation and journal observations | Cancel/WS race, REST recovery, late/status-only confirmation, duplicate and shared-trade-ID tests |
| StateStore | `state/store.py:54`, `:135`, `:225` | Existing SQLite registry, fills and order state | Real `StateStore(':memory:')`; optimistic FL positions are not SM confirmed inventory | Actual owned records/applied keys asserted; no new persistent DB |
| FillTruth | `execution/filltruth.py:69`, `:211` | `reconcile_owned_order(..., retry_delays=())` | Real official order/trade pagination and ownership/economics/checksum reconciliation | Used during the window, before subsequent submissions and at closure; COMPLETE complemented by separate terminal/confirmation checks |
| Journal | `journal.py:16`, `:25` | Append-only `Journal.write/close` | `RiskJournal` subclasses actual Journal; SM-only exclusive lock, flush/fsync and restart refusal | Real files; intent observed at HTTP POST; injected filesystem fsync errors; lock/history tests |
| M1 / account | `rdp_sm.py:84`, `:95`, `:167`, `:331`, `:397`; `favorite_live.py:183` | `run_preflight`, `_Reads`, `_account`, `_token_id`, collateral readiness | Startup M1 READY plus fresh subsequent reads; own inventory may persist; F01 canonical identities retained everywhere | Unchanged 64 M1/F01 cases; owned inventory next-window case |
| Market data | `catalog/gamma.py:62`, `:134`; `marketdata/parse.py:78`; `marketdata/orderbook.py:77`; M1 `_market/_book` | Official metadata, real parser, OrderBook and SDK dynamic tick | Exact BTC5m market/outcomes; fresh books/tick/time on every submission | Invalid grid/token/price/size and official tick change tests |
| SDK / Config | Installed `py_clob_client_v2/client.py`; `config.py`; `btc5m.py:70` | Real SDK constructor, derive existing credentials, V2 GTC signing, authenticated GET/POST/DELETE; existing configuration | Restricted SM SDK subclass, existing hosts/spenders; same 15-second conservative buffer as BTC5m runner | Real local dummy-key signing and actual SDK requests under Fake HTTP |

No Gateway, Tracker, FillTruth, StateStore, Journal core, Generic Engine, config, FL strategy or existing CLI behavior was modified.

## 4. Final Architecture

Production source: `src/polymaker/rdp_sm_execution.py`.

```text
SMWindowExecutor.open(cfg, selected_slug, session_id)
  -> unchanged M1 run_preflight -> READY required
  -> _ExecutionReads -> unchanged M1 account connection / existing-key derivation
  -> real ExecutionGateway + ExecutionClobClient (actual installed SDK)
  -> RiskJournal (existing FL JSONL format, sync/lock adapter)
  -> real StateStore(':memory:') + _SMProcessor(UserEventProcessor)

await executor.execute_window(window_id, list[WindowQuote], fill_window_sec=3)
  -> fresh previous close proof, if any
  -> Gamma / M1 metadata, OrderBook, fresh tick, account, official time
  -> BUY/identity/grid/size/capital checks
  -> durable sm_placement_intent
  -> Gateway.place([Quote], token-specific MarketMeta,
                   force_post_only=True, strict_placement=True, submit_guard=...)
  -> actual SDK signing and single-quote GTC post_orders -> ACK/REJECTED/UNKNOWN
  -> Tracker register_order / buffered event replay
  -> fill-window REST reconcile_owned_order -> Gateway.get_order/get_trades
  -> Gateway.cancel(known IDs)
  -> FillTruth + fresh final order + official trade-set/confirmation validation
  -> confirmed-only projection + commitment + close barrier + WindowResult

await executor.verify_close()   # explicit serialized read-only recheck
executor.close()                # after active work is awaited; never resumes trading
```

`WindowQuote` contains market, token, outcome, BUY side, exact price/size and window identity. `WindowResult.to_dict()` exposes session/window/market, wall-clock start/end, requested quotes, attempt outcomes, known IDs, confirmed fills, YES/NO/Delta, committed capital, barrier/reason, journal reference and T0/deadline. Inventory/IDs include the session's owned orders, not unrelated account orders. Each window lists its own new placement attempts.

There is no separate demonstration executor. M3 must call this library path, not rebuild placement or closure. The existing UserStream can deliver normalized events to `executor.processor`; M2 does not start a WS daemon or a trading runtime. Its standalone window path also recovers events via actual official REST reads.

External paths are existing official Gamma `/markets`, Data API `/v2/positions`, CLOB `/auth/derive-api-key`, `/version`, `/balance-allowance`, `/data/orders`, `/book`, `/tick-size`, `/time`, `/data/order/{known-id}`, `/data/trades`, and CLOB `/orders` POST/DELETE. No RPC, third-party service, new API service or settlement path. Tests substitute HTTP transport; no external requests occurred.

## 5. Order Placement

- Each quote uses an independent one-order Gateway call, preserving non-atomic placement. The existing SDK's `post_orders` method is used with a singleton; no new batch interface was added.
- BUY only, post-only GTC. No taker/SELL or automatic price adjustment. Submitted price must be on the freshly read supported tick grid and inside `[tick, 1-tick]`, strictly below best ask. Quantity must meet official Gamma venue minimum and SDK's two-decimal size precision. The signed token, side, signer/funder and exact maker/taker amounts must match the input before HTTP POST.
- ACK requires the Gateway-extracted opaque ID, unique within the entire session. Tracker owns that ID and retains pending-event capture until ACK association. ACK identity is journaled before another submission.
- Gateway `[]` is not blindly classified as rejection: a transport observer distinguishes no POST (ABORTED) from literal `success:false` without an order ID (REJECTED). Contradictory rejection/ID, timeout, ambiguous/missing ACK or conflicting identities are UNKNOWN.
- Second REJECTED stops placement and closes the first immediately; confirmed first-leg fills remain inventory. First/second UNKNOWN never retries or starts another window. Known IDs are still cancelled/reconciled. An official open-order lookup is recorded without pretending it authoritatively correlates an unknown request.
- T0 is monotonic immediately before the first Gateway request is scheduled, **after intent fsync and before ACK**. Deadline is T0 + configured fill interval, default 3 seconds, minimum 3. It is never reset by ACK or fill. A first request finishing after deadline prevents second submission.
- The same request task is shielded and awaited through repeated caller cancellation. No cancellation-driven resubmission or abandoned SDK worker. Process death is covered by the durable intent/restart latch, not automatic recovery.

## 6. Fill Window

The executor waits against the original monotonic deadline, using existing FillTruth/official reads with short sleeps. It does not chase prices or inspect ordinary book movement to cancel early. Partial fills are accepted. An unresolved read/ownership/journal safety condition can stop the window early.

Every submission re-reads market tradability, book/tick, identity, account and official time. A worker-thread submit guard repeats deadline, freshness, earlier-window proof and state checks immediately before POST, without reading SQLite from that thread.

Frozen time is preserved: submission cannot occur before T+15 or at/after T+240. A window must also leave its fill time **plus the existing 15-second fixed buffer** strictly before T+240. With default fill time, a start at T+222 is refused. This buffer is scheduling headroom, not a claim that exchange/network cancellation latency is bounded by 15 seconds.

At expiry/rejection/failure, cancel known IDs, then verify terminal and reconcile. Cancellation/reconciliation may overrun the fill interval and continue after T+240. The delayed-first-ACK test explicitly proves that only cleanup occurs after cutoff; no second BUY is posted. SAFE_TO_CONTINUE is an execution-fact result, never a cached timing/authorization permit.

## 7. Confirmed Inventory

The existing Tracker applies optimistic MATCHED fills to its internal store. SM never uses those positions as confirmed strategy inventory. It projects only applied, non-quarantined economic events whose status is CONFIRMED and whose maker order is owned by this session.

YES and NO are sums by canonical official token; Delta = YES - NO. Deduplication is the production `(trade_id, maker_order_id)` identity, including two owned maker legs sharing a single trade ID. Partial fills, duplicate WS/REST confirmations and REST-only confirmed fills are tested.

MATCHED/MINED/RETRYING, failed/quarantined fills and unapplied status-only confirmations remain unresolved. A status-only CONFIRMED cannot create inventory without recoverable maker economics. Late confirmation invalidates the affected proof. Local confirmed inventory may be visible while BLOCKED, but continuation needs a fresh official confirmed trade set and matching cumulative quantities/associate IDs.

## 8. Close Barrier

Production functions: `safe_to_continue`, `_verify_order`, `_official_fills`, `_verify_close` and the final submit guard in `rdp_sm_execution.py`.

Continuation requires all of the following:

1. Every recorded attempt is ACK, explicit REJECTED or a proven no-POST ABORTED; no INTENT/UNKNOWN can disappear.
2. Every ACK has unique ownership and a current verified proof.
3. Fresh official order economics/market match the registry. CANCELED/CANCELLED, or fully MATCHED with zero remaining, establishes **ORDER_TERMINAL**; cancel ACK/local removal does not.
4. Actual FillTruth returns COMPLETE **and** a fresh final order cumulative quantity equals recovered maker quantity.
5. A complete paginated official trade response agrees with local maker economics and every economic leg is **TRADE_CONFIRMED**. Associated trade IDs equal that exact set; missing associate evidence blocks even a nominal zero-fill result.
6. No unapplied/quarantined fill, contradictory state, new ingress during verification, cross-order trade-set change, fatal journal error or UNKNOWN remains.
7. All known-order proofs exist, global close revision is current, and affected per-order proofs have not been invalidated.
8. The next window re-verifies all old orders and then checks that subsequent market/account reads did not invalidate the previous barrier. The final POST guard enforces the earlier-window proof again.

These separately establish ORDER_TERMINAL, TRADE_CONFIRMED and **RECONCILIATION_COMPLETE**. No fixed sleep is used to infer that future confirmations cannot occur. Proof comes from current official terminal/cumulative/associate information, full returned trade pages and observed lifecycle consistency; fresh evidence is required for subsequent execution.

BLOCKED prohibits new placement. Explicit `verify_close` can resolve a transient confirmation/read gap using new official facts; it is serialized with active execution and does not place. UNKNOWN, unexplained terminal/LIVE contradiction, abnormal exit and journal failure latch fatal state and cannot auto-clear. A BLOCKED result with unknown exposure is **not** proof that all orders closed. Known closed orders can release their independently proven unfilled exposure while unknown maximum exposure remains reserved.

## 9. M0 Findings Closure

| Finding | Original counterexample | Actual code fix / containment | Executable evidence | Result |
|---|---|---|---|---|
| M0-H01 | Cached terminal survives later LIVE; new POST allowed | `_SMProcessor.on_order` invalidates affected proof, records contradiction and latches fatal. Fresh official LIVE, including presence in official open orders, also contradicts previous terminal. Known orders are queried again; even old official CANCELED cannot explain an untimestamped contradictory WS LIVE automatically | `test_e07_h01_terminal_then_live_blocks_even_old_official_terminal`; `test_official_live_after_terminal_also_blocks_next_post`; official open-order contradiction test: real second-window call, no additional POST, retained risk until re-query, BLOCKED | **CONTAINED** in actual SM path; shared production semantics unchanged |
| M0-H02 | Late CONFIRMED reuses old COMPLETE and misses inventory | Real processor event changes invalidate proof; confirmed-only inventory updates/dedupes. Official set/cumulative/associate matching is mandatory, including status-only and cross-order/next-snapshot races | `test_e06_h02_late_confirmed_invalidates_complete_and_requires_official_set`, status-only and two race tests: no next POST with stale official data; later matching official facts recover exactly two shares once; owned inventory permits a subsequent legitimate window | **CONTAINED**; no claim of universal venue atomic snapshots or future settlement finality |
| M0-H03 | Unknown attempt is omitted and retry allowed | Append/durable intent before POST, distinct attempt outcome and maximum commitment, singleton POST permission, fatal UNKNOWN checked at window entry and worker guard; official lookup never treats absence as rejection | Three `test_e03_h03_unknown_retains_commitment_no_retry_or_next_window` cases: hidden venue order/timeout or missing ID; real new-window calls make no new POST; unknown $2.45/$2.40 remains committed; known first order closes | **CONTAINED**; UNKNOWN cannot be automatically resolved by current interfaces |

No M0 report/harness was repaired. Containment claims concern this production executor and the tested event schedules, not a retrofit to FL or a universal event-ordering implementation.

## 10. Capital Safety

Hard session limit: exactly **30 USDC**, using Decimal arithmetic on quote/fill economics. Each exposure appears once:

- Known active, incomplete, contradictory or unknown placement: full submitted limit-price notional. This is a conservative envelope for matched commitment plus unfilled maximum, not their double-counted sum.
- Order with valid independent terminal/confirmed reconciliation proof: actual confirmed fill cost; only then release unfilled notional.
- Explicit rejection/no-POST abort: zero exposure.
- UNKNOWN without reliable ID: full maximum remains committed permanently in this executor.

Existing closed-order cost proofs remain independently valid when a *different* new placement begins; a new event invalidates the affected order proof. This avoids both stale release and counting historical cancelled original size as new economic exposure. An evidence contradiction can increase the conservative recorded maximum; the executor blocks rather than submitting against that uncertainty.

All quote commitments are checked together before the first attempt and again before each order. Actual HTTP test boundary additionally asserts current commitment <=30. Exact $30 is allowed; over-limit quotes and later windows consuming confirmed-cost budget are refused before POST.

Official available balance and applicable allowance are re-read per submission. M1 account reader reserves account-wide open BUY remaining notional; M2 conservatively also reserves matched notional potentially awaiting settlement, including other-market open orders. Known pending closed-order fills are reserved too. The liquid-budget check may be conservative when official balance already reflects a match; this does not double-count the $30 economic ledger.

Account authority is **contained to signature_type=0 EOA with actual SDK signer=funder**. The default Config signature type is 2 and is therefore deliberately BLOCKED by this M2 path. No RPC/proxy-owner guess was added. SDK signer, builder funder, configured funder and signature type are rechecked before submission. Proxy-authority support remains deferred, not implicitly authorized by M1 READY. Current SDK order version must be V2; unsupported/unknown versions block.

Startup requires M1 clean target market. Later account holdings must be explained by owned economic fills, and target open orders must have owned IDs; own inventory is not re-rejected merely for being nonzero. Other-market inventory is represented by official collateral already spent; other-market open orders reserve collateral. The account must be operated without another concurrent trading writer; observable external target orders/unexplained holdings block.

## 11. Journal & Crash Safety

`RiskJournal` uses the actual FL append-only JSONL implementation and format. It adds `flock(LOCK_EX|LOCK_NB)`, flush + file fsync on every record, and directory-entry fsync at initialization. Intent is durably recorded before Gateway scheduling; ACK/order ID and risk outcome are recorded before any next submission. Quotes, attempt UUID, request-body SHA-256 after transport starts, outcomes, owned identities, events, official reconciliation responses, barrier and window result provide M4 traceable facts. No private key/API secret is written.

The ledger is a fixed filename per funder in **the configured existing journal directory**, independent of session/window/market UUID. Any nonempty historical ledger refuses new startup, even after a prior SAFE result; there is no automatic restart or automatic risk-cleared inference. Concurrent writers are refused. An operator must verify/archive historical risk outside this executor before another session; moving/deleting/changing the configured ledger directory is not an automatic recovery procedure.

Journal failure before intent sync produces no POST. Failure after ACK stops additional submission but still attempts known cancellation and reconciles actual fills. Fatal logging failure cannot permit a new window. Event/cleanup logging failure is latched while best-effort safety reads continue; no swallowed write failure becomes SAFE.

Tests prove ordering, fsync-failure handling, readable intent/risk records, lock and startup refusal. Physical power-loss/storage durability and an actual killed process mid-network exchange are **NOT_VERIFIED**. The design relies on normal local POSIX filesystem fsync/flock guarantees; it does not recover state automatically, depend on a new DB or promise orders are closed after process death.

## 12. Test Results

All new tests are in `tests/test_rdp_sm_m2_execution.py`. The actual SM executor, Gateway, installed SDK signing/auth/pagination, Tracker, StateStore, FillTruth and Journal execute. Only official HTTP responses, event timing, clocks and filesystem-error boundary are faked. No FakeGateway, FakeTracker or FakeFillTruth replaces a evaluated component.

| Scenario | Test evidence | Expected | Observed | Result |
|---|---|---|---|---|
| E01 Both ACK | `test_e01_e08_both_ack_no_fill_verified_close_and_next_window`; shared-trade-ID two-leg test | Two IDs/owners, actual terminal and confirmed reconciliation, next-window gate | Both owned, cancelled/read; two confirmed legs with common trade ID retained; legitimate next window uses same executor | PASS |
| E02 ACK / REJECT | `test_e02_first_ack_second_explicit_postonly_reject_closes_fills` | Stop submission, close first, count actual fills | Immediate close, first two confirmed shares/$0.98, second explicit reject | PASS |
| E03 UNKNOWN / H03 | Three first/second timeout/missing-ID cases; failed unknown-lookup case | No retry/next order; maximum risk retained | Hidden possible order remains unresolved; new-window call posts nothing; known first cancelled; even failed unknown lookup preserves known first's two confirmed shares; durable unknown/lookup record | PASS |
| E04 Partial/cancel race | Partial/duplicate test; actual WS-during-cancel test; cancel-ACK/LIVE test | No lost/doubled fill; ACK not terminal | Exactly three shares across early/cancel fills, duplicate replay unchanged; WS MATCHED then REST CONFIRMED; LIVE keeps full commitment and blocks next window | PASS |
| E05 MATCHED/MINED | Two pending-status cases | No confirmed inventory or false COMPLETE | Tracker tracks two pending shares, SM inventory zero, barrier/next POST blocked | PASS |
| E06 Late confirmation / H02 | Late, status-only, cross-order and next-pre-submit-snapshot tests | Stale COMPLETE invalid; official proof needed | Late inventory counted once; old official zero set blocks; updated set permits explicit recheck; races block | PASS |
| E07 Terminal/LIVE / H01 | WS and official-LIVE tests | Invalidate old terminal, no new POST | Fatal contradiction recorded, official re-query performed, all next attempts blocked | PASS |
| E08 No fill/cancel | E01/E08 test | Verified terminal, unchanged inventory, safe continuation | SAFE_TO_CONTINUE; zero inventory/commitment; next window works | PASS |
| E09 Capital | Over-limit, exact-$30, confirmed-cost-next-window, UNKNOWN, account-wide reservation tests | <=30 per POST; no early release; official funds/allowance | No over-limit POST, old cost retained, unknown maxima retained, other-market reservation blocks insufficient budget | PASS |
| E10 Cutoff/exit | Five cutoff cases; delayed ACK past deadline/cutoff; abnormal exit, repeated in-flight task cancellation, journal history tests | No new POST after cutoff; durable risk; no restart | No forbidden POST; late ACK only cleaned; same in-flight request awaited and known ID cancelled; historical ledger refuses new session | PASS |
| Focused input/safety | Nine invalid quotes, changing official tick, one-order path, pre-intent/after-ACK fsync failure, account mismatch/proxy, contradictory rejection ID | Invalid input/identity/logging cannot trade; legitimate input remains executable | Correct refusal; no new interface or quote adjustment; EOA/V2 valid path remains usable | PASS |

Environment: repository `.venv`, Python 3.12.14, installed pinned SDK and existing pytest/pytest-asyncio. No installs. Preserved offline runner clears inherited environment before imports, denies workspace `.env`/credential access, socket/DNS calls, uses temporary CWD, disables cache/plugin autoload; `-B` prevents bytecode. M2 fixture additionally denies Gateway live connection, market/taker orders, broad cancellation and RPC token-balance paths. A public deterministic dummy private key is used only with Fake HTTP local signing.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

**48 passed / 0 failed / 0 skipped**. Guard: `0 network/DNS attempts; workspace credentials denied`.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

**346 passed / 0 failed / 0 skipped; 1 deselected** = unchanged 64 M1/F01 + 282 existing selected FL regressions. Zero network/DNS attempts.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_execution.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

Combined selected suite: **394 passed / 0 failed / 0 skipped; 1 deselected**, zero network/DNS attempts. Duplicate runs are not added to this distinct-case count.

The unchanged exclusion is `test_cli_conflict_stops_before_settlement_preflight`: existing dotenv discovery attempts protected workspace credentials before the expected alias error. Its prior independent JUSTIFIED disposition is preserved; no credentials/protection/test were changed and it is not counted as a pass or skip.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_execution.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_execution.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/engine.py src/polymaker/config.py src/polymaker/favorite_live.py
```

Passed. Worker performed source/call-chain self-checks and adverse event scheduling tests. **Independent M2 Review: NOT_RUN**; Architect invokes it separately.

Real official read-only smoke, real authenticated requests, real orders/cancellation, external WS/RPC and actual account availability: **NOT_VERIFIED / NOT_EXECUTED**. Fake HTTP establishes executable composition and safety assertions under supplied official facts; it cannot prove actual service schema availability, auth/finality or physical storage/network behavior.

## 13. MicroLive Impact Assessment

| Issue | Evidence | MicroLive impact | Blocker | Decision |
|---|---|---|---|---|
| Gateway empty list hides abort versus reject | Actual Gateway source plus observed SDK POST/response | Must not free uncertain submission risk | YES if uncontained; adapter now contains | FIX NOW implemented |
| Shared COMPLETE does not imply terminal/confirmed | Real MATCHED/MINED tests and cancel-ACK/LIVE | Premature inventory/capital/next window risk | YES if uncontained; explicit barrier implemented | FIX NOW implemented |
| Buffered Journal lacks durability/lock/restart policy | Actual Journal source and fsync failure/history tests | Lost intent after crash could permit duplicate exposure | YES if uncontained; SM sync adapter implemented | FIX NOW implemented |
| SDK indefinitely caches dynamic tick | Actual SDK cache and changed tick test | Wrong signed price grid | YES if uncontained; cache invalidated/read afresh | FIX NOW implemented |
| State changes between close verification and subsequent POST | Actual late/cross-order/next-snapshot tests | Old proof cannot authorize a new window | YES if uncontained; invalidation and final gate implemented | FIX NOW implemented |
| Proxy signer/funder authority lacks proof | Default signature type 2; explicit proxy test BLOCKED | Proxy execution is unavailable safely | YES for proxy use; NO for supported EOA delivery | DEFER proxy authority; EOA-only containment now |
| Live API/service/storage behavior not demonstrated | All tests intentionally offline | Offline CODE_READY is not MicroLive readiness | Requires separately authorized preparation; no live claim | DEFER; no live test in M2 |

No current uncontained execution defect is claimed solved merely by test count. UNKNOWN and contradictory states intentionally remain blocked with risk retained; operator resolution is a prerequisite for any subsequent session.

## 14. Deferred Issues

- Signer/funder authoritative association for proxy accounts stays deferred; M2 contains it by refusing signature types other than 0 rather than guessing ownership or using RPC.
- Broader SDK RFQ subclient boundary remains outside this path. No RFQ/settlement call exists here; the restricted transport is not claimed as a universal sandbox for every SDK subclient.
- M1 raw book-level grid checking remains deferred; **actual M2 submitted price grid** is now checked. F01 canonical-token constraints remain unchanged, including the previously disclosed zero-share-row filtering limitation.
- No automatic UNKNOWN correlation/recovery, journal restart recovery, multi-market loop or new DB. All historical ledgers require manual risk review; normal completed sessions also cannot auto-resume.
- Actual official schema/finality/authentication, latency relative to the 15-second buffer and physical crash durability remain NOT_VERIFIED. Missing/contradictory current evidence blocks rather than a timed wait manufacturing completion.
- Full timeline/report presentation belongs to M4. M2 records raw input, request identity, ownership, lifecycle, official evidence and close results for reuse.
- M0-H01/H02/H03 have production SM containment described above; original M0 audit findings/reports are not rewritten or declared universally repaired in FL.

## 15. Final Worker Result

**M2_CODE_READY**

The importable production executor handles one/two independently posted Maker BUYs with real FL components; all required E01–E10 scenarios and focused cases have executable offline evidence. Confirmed projection, $30 limit, durable intent, UNKNOWN maximum reservation, current close evidence and next-window gating are demonstrated. Shared core/FL behavior remains unchanged and the selected regressions pass.

This result is scoped to the supported EOA/V2 official interface contract and the described conservative operational constraints. It is not Independent Reviewer PASS, MICROLIVE_READY, LIVE_PASS or live authorization. No M3 work was started.

## 16. Change Manifest

Created in M2:

1. `src/polymaker/rdp_sm_execution.py` — actual production executor and necessary thin adapters/results.
2. `tests/test_rdp_sm_m2_execution.py` — 48 offline real-component integration cases.
3. `docs/development/RDP_SM_M2_EXECUTION_INTEGRITY.md` — this report.

Modified existing files in M2: **NONE**. Shared production core changes: **NO**. Pre-existing CLI/M0/M1/F01 artifacts are preserved, not M2 modifications. Tracked diff remains only the prior 22-line M1 CLI addition; staged diff empty.

Final `git status --short --untracked-files=all`:

```text
 M src/polymaker/cli.py
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? docs/development/RDP_SM_M0_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_F01_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_F01_TOKEN_IDENTITY.md
?? docs/development/RDP_SM_M1_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_SAFE_SESSION.md
?? docs/development/RDP_SM_M2_EXECUTION_INTEGRITY.md
?? src/polymaker/rdp_sm.py
?? src/polymaker/rdp_sm_execution.py
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
?? tests/test_rdp_sm_m1.py
?? tests/test_rdp_sm_m1_f01_token_identity.py
?? tests/test_rdp_sm_m2_execution.py
```

## 17. Next Action

**Independent M2 Review**
