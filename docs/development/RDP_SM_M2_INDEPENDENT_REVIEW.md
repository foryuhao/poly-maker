# RDP-SM M2 — Independent Review

## 1. Review Metadata

- Date: 2026-10-12, Asia/Shanghai (review continued from 2026-10-11).
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- Frozen strategy: RDP-SM SOP v1.8; FL-first / Thin SM Orchestrator; MicroLive First.
- Scope: actual M2 executor, directly related components, account/placement/closure/capital/timing/journal evidence, selected offline regressions.
- Reviewer provenance: separate fresh-context agent `/root/m2_independent` independently inspected the implementation, ran 48 M2 and 346 selected M1/FL tests, reproduced adverse schedules, and concluded **FAIL**. Root previously authored M2, assembled this report and separately reran the tests and diagnostics. Root's repeat checks are not presented as the sole source of independent review. The agent's initial turn encountered a usage-limit interruption; its resumed turn completed the independent review.
- Review-entry Git status: prior 22-line additive M1 CLI change and untracked M0/M1/F01/M2 artifacts. All relevant untracked source/tests/reports were included. Staged diff empty. Baseline branch/HEAD correct.
- Read the M2 Worker report, original M0 review findings, accepted M1 report and F01 independent review. Historical conclusions were preserved; M0 was not re-audited.
- No SOP v1.8 file is present in this checkout. The older Phase0 document is not substituted for v1.8. Execution requirements were reviewed against the frozen user contract.
- No historical RDP branch access, code/test fixes, live requests, real credentials, RPC or M3 work.

## 2. Worker Claims Verification

| Claim | Independently checked evidence | Verdict |
|---|---|---|
| Actual reusable production Window Executor exists | `src/polymaker/rdp_sm_execution.py:306`, `:346`, `:733`; importable library with structured quotes/results | CONFIRMED |
| Production Gateway/Tracker/FillTruth/Journal reused | Actual call chain in section 3; SDK signing and HTTP-boundary tests | CONFIRMED |
| Placement PASS | Strict singleton post-only BUY and UNKNOWN handling pass; official funds checks omit known/observed obligations in supplementary diagnostics | **NOT CONFIRMED overall** |
| Close Barrier PASS | Terminal, confirmation, ownership, official fill-set and fresh-proof checks execute; supplied unresolved-state cases block | CONFIRMED for reviewed closure-evidence paths; not a waiver of pre-submit/timing defects |
| Confirmed Inventory PASS | MATCHED/MINED excluded; partial, duplicate, late/status-only and shared-trade-ID assertions | CONFIRMED within tested official facts |
| Capital Limit PASS | Decimal $30 ledger works in tested schedules; official available collateral can be overcommitted | $30 ledger CONFIRMED; **capital safety claim incomplete** |
| M0-H01/H02/H03 CONTAINED | Real next-window calls, POST counts, fills and commitments checked | CONFIRMED in SM path, section 6 |
| 394 passed / 48 M2 cases | 48 M2 + 346 selected M1/FL; independently reproduced | CONFIRMED; excludes supplemental counterexamples |
| FL Regression PASS | 282 selected FL + 64 M1/F01 tests; shared core unchanged | CONFIRMED for selected coverage |
| Shared Core Changes NO | Tracked diff remains only pre-existing CLI addition; execution/state/journal/engine/config/FL files match HEAD | CONFIRMED |
| Network/DNS attempts ZERO | Executable runner and diagnostic guards; actual output zero | CONFIRMED for review executions |
| EOA-only / proxy blocked / no automatic recovery | Factory, repeated identity checks, signed-payload guard and nonempty-history refusal | CONFIRMED |
| CODE_READY / ready to enter M3 | Two reproducible M2 safety defects remain | **NOT ACCEPTED** |

Passing the submitted tests does not prove the untested reservation/read-latency schedules safe.

## 3. Actual Execution Architecture

```text
SMWindowExecutor.open
  -> unchanged M1 run_preflight
  -> _ExecutionReads -> existing M1 account-reader connection
  -> actual ExecutionGateway + actual SDK ExecutionClobClient
  -> RiskJournal(Journal), StateStore, _SMProcessor(UserEventProcessor)

execute_window
  -> official metadata/book/tick/account/time snapshot
  -> validation + capital checks + durable intent
  -> Gateway.place([Quote], force_post_only=True, strict_placement=True)
  -> SDK signing -> singleton GTC POST
  -> production Tracker registration / pending-event replay
  -> production FillTruth official order/trade reconciliation
  -> Gateway.cancel(known IDs)
  -> fresh official terminal + confirmed fill-set checks
  -> inventory / commitments / close barrier / WindowResult
```

| Component | Actual source/interface | SM behavior / evidence | Verdict |
|---|---|---|---|
| Gateway | `execution/gateway.py:172` place, `:305` cancel, `:595` open_orders, `:653` positions, `:711` get_order, `:733` get_trades | Real non-paper Gateway, real worker pool/rate limits, response parser and actual SDK signing | REUSE CONFIRMED |
| Tracker / Store | `state/tracker.py:52`, `:87`, `:171`, `:193`, `:328`; `state/store.py` | Owned order registry, buffered events, economic-fill keys, quarantine; real in-memory StateStore | REUSE CONFIRMED |
| FillTruth | `execution/filltruth.py:69`, `:211` | Real official reads, trade parsing, ownership/economics and cumulative checksum; SM adds terminal/confirmation requirements | REUSE CONFIRMED; active deadline interface omitted |
| Journal | `journal.py:16`, `:25`; SM `:105-137` | Existing append-only JSONL plus SM lock/fsync/restart refusal | REUSE CONFIRMED |
| M1 / MarketData | `rdp_sm.py:84`, `:167`, `:231`, `:293`, `:331`; Gamma/parser/OrderBook | Canonical token validation, official pairing, books/tick, account and collateral reads | REUSE CONFIRMED; mixed account snapshots cause finding |
| SDK transport | SM `:140-258` | Existing official SDK; POST observer/permissions and known-ID cancellation | REAL EXTERNAL BOUNDARY |

The production code is callable by M3; no FakeGateway/FakeFillTruth drives its logic. Tests replace official HTTP responses, event delivery/time and filesystem faults. Local signing uses a public deterministic dummy key.

Official endpoints remain Gamma markets, Data API positions and CLOB existing auth/version/balance/orders/book/tick/time/order/trades paths. No RPC, third-party service, merge/redeem/settlement or Engine/trading-loop initialization was introduced. SM adapters add proof/risk bookkeeping rather than copy the shared execution implementations.

## 4. Placement Review

- SM `:546-574` validates exact approved market/window/outcome/token, BUY enum, finite numbers, official tick grid/range, venue minimum, SDK size precision and price below ask.
- `:837-843` uses two sequential independent singleton calls with both strict placement and forced post-only.
- SDK adapter `:189-219` checks singleton post-only GTC, signed BUY/token/signer/funder/exact amounts, and permits at most one POST per attempt.
- ACK ownership is registered before ACK journal write (`:858-878`), retaining known IDs when persistence fails.
- Gateway empty response is separated into proven no-POST ABORTED versus literal REJECTED. Ambiguous responses, timeout, contradictory IDs or missing identity retain UNKNOWN.
- First UNKNOWN prevents the second order. Second UNKNOWN does not retry. Known first exposure is still cancelled/reconciled. Both first and second UNKNOWN stop future windows.
- First ACK / second explicit post-only REJECT closes immediately and accounts for the first order's actual fills.
- T0 is taken before scheduling the first placement request, after intent fsync. It is not reset at ACK/fill. A delayed first ACK prevents second submission past the original deadline.

**Placement verdict: FAIL**, because `:800-802` can authorize a quote with insufficient reliably available collateral, as demonstrated in section 8. The strict Maker/identity/UNKNOWN portions pass.

## 5. Close Barrier Review

The three facts are separately tested and implemented:

1. **ORDER_TERMINAL:** fresh official order is CANCELED/CANCELLED or fully MATCHED with zero remaining; cancel ACK alone is insufficient (`:576-600`).
2. **TRADE_CONFIRMED:** recovered owned economic events and official maker legs match, and every relevant leg is CONFIRMED (`:601-617`). MATCHED/MINED remain pending.
3. **RECONCILIATION_COMPLETE:** real FillTruth COMPLETE plus final order cumulative quantity/associated IDs, exact official/local maker-set equality, applied/unquarantined keys, all known proofs, no ingress during verification and no unresolved attempt/fatal state (`:618-731`).

The last paginated trade set must explain all prior owned proofs; local event version alone is not treated as official completeness. Subsequent windows require current safe state, fresh previous-order verification, another snapshot check and a final POST guard.

UNKNOWN, journal failure, abnormal exit and terminal/LIVE contradiction latch fatal state. Read-only re-verification can resolve transient trade/read gaps but cannot silently clear those fatal conditions. BLOCKED with unresolved exposure is not successful closure.

**Close Barrier evidence verdict: PASS for the reviewed state/fill schedules.** In the supplemental diagnostics SAFE_TO_CONTINUE is returned only after the fake orders are actually cancelled and reconciled. Their defects are earlier capital admission and delayed cancellation initiation; they do not demonstrate a false terminal/confirmed fill proof. Overall execution remains FAIL.

Limitations: official responses are separate reads, not an atomic venue snapshot or proof about all future events. Current evidence and re-verification are required before subsequent placement. Real service completeness/finality was not verified.

## 6. M0 Finding Verification

| Finding | Actual execution / test evidence | Observed safety assertions | Verdict |
|---|---|---|---|
| M0-H01 terminal followed by LIVE | Processor `:283-292`, official verification `:596-599`, open-list contradiction `:485-490`; tests `:353`, `:531`, `:746` | Previous proof invalidated; fatal contradiction; real next-window call adds zero POSTs; official re-query occurs | **CONTAINED** |
| M0-H02 late CONFIRMED / stale COMPLETE | Processor `:268-281`, proof checks `:601-625`, final cross-order comparison `:694-700`; tests `:326`, `:516`, `:540`, `:563` | Late confirmation cannot reuse old proof; exactly two shares recovered once when official facts agree; no next POST while unresolved; status-only confirmation remains unapplied until economics recovered | **CONTAINED** |
| M0-H03 UNKNOWN permits retry | Intent/POST guard/attempt outcomes `:811-909`; entry gate `:747`; UNKNOWN barrier `:665-678`, `:701-705`; tests `:258`, `:650`, `:726` | Actual first/second UNKNOWN POST counts stop; next-window calls add none; unknown $2.45/$2.40 maximum retained; known first fills recovered even if unknown lookup fails | **CONTAINED** |

All test line references in this table are `tests/test_rdp_sm_m2_execution.py`.

Containment means the actual supported SM path refuses further execution under these conditions. It does not mean shared FL caches were repaired, every uncertain order was resolved, or historical M0 became PASS. Known independently verified terminal exposure can reduce to actual fill cost; UNKNOWN maximum cannot disappear without reliable identity/resolution. Original M0 reports and disposition remain unchanged.

## 7. Confirmed Inventory

Production projection `:414-422` uses actual owned, applied, non-quarantined economic fills and includes only CONFIRMED. YES/NO/Delta derive from these fills at result assembly, never submitted size.

Independent assertions establish:

- Partial window fill1 plus cancel-race fill2 yields YES3, two economic keys and cost $1.47.
- Duplicate confirmations do not add shares/cost.
- MATCHED and MINED yield zero confirmed inventory and BLOCKED.
- Status-only late CONFIRMED does not invent unapplied economics.
- Official late confirmation recovers two shares once and permits a later legitimate window with its own inventory.
- Two owned maker legs sharing one trade ID yield YES2/NO2, Delta0 and two distinct `(trade_id, maker_order_id)` keys.
- Foreign maker legs do not affect SM inventory; the supplementary paginated diagnostic contains only foreign trades and still has zero SM fills.

**Verdict: PASS** under reviewed facts. Current live endpoint behavior remains NOT_VERIFIED.

## 8. Capital Safety

The **$30 economic ledger itself passes** the reviewed cap assertions. `:424-442` uses one conservative envelope per unresolved order/UNKNOWN attempt, reduces independently terminal/confirmed proof to actual fills, and releases rejected/no-POST attempts. Quotes are checked together and again individually (`:767-774`, `:796-797`). Cancellation ACK does not free live risk.

However, session cap and official spendable collateral are different constraints. The latter fails.

### M2-H01 — Official capital guard omits known or newly observed order commitments

- Severity: **HIGH**.
- Evidence:
  - M1 `rdp_sm.py:338,367-392` computes reserved funds from its first open-order read.
  - M2 `rdp_sm_execution.py:474` retains that account budget, then `:484` reads open orders again.
  - `:506-524` subtracts only the **matched** portion from this newer list.
  - `:800-802` compares the new order alone with that budget; known live orders absent from the listing are not reserved against liquid funds.
- Counterexample A, no concurrent external writer: official balance $3; open-order list lags and omits this executor's newly ACKed first order, while real get_order reports LIVE. Both $2.45 and $2.40 orders are POSTed. Actual commitment at POST is **[2.45, 4.85]**. There is insufficient collateral for the second order under the observed gross balance. Final closure is SAFE after cancellation.
- Counterexample B: official balance $6; final pre-POST second open-order read newly exposes other-market BUY5 at $0.90, matched0. Reliable available funds are $1.50, but a **$2.45 POST** occurs. The earlier empty read's budget survives the newer observed $4.50 reservation.
- MicroLive impact: local admission can authorize unfunded commitments or cause preventable partial placement failure. Safety must not rely on the venue rejecting the offending order. This does **not** demonstrate the SM $30 ledger exceeding $30; it demonstrates omission in the separate mandatory official balance/allowance admission check.
- Blocker: **YES**. Decision: **FIX NOW**.
- Smallest repair scope: SM account snapshot/admission only. Account for known unresolved liabilities missing from official listings and the full latest observed BUY commitment, without double counting the same order. Block inconsistent evidence if it cannot be safely combined. Add both adverse read schedules as regressions. No shared Gateway/Tracker/FillTruth refactor or CapitalGate framework is required.

**Capital safety verdict: FAIL**, despite the correctly enforced $30 session ledger.

## 9. Journal & Crash Safety

- Real Journal append format is retained; RiskJournal adds nonblocking exclusive file lock, flush/file fsync and initialization directory fsync.
- Intent is recorded before placement scheduling. At the HTTP boundary tests assert the intent already exists on disk.
- ACK identity is tracked in memory before ACK persistence; ACK write failure stops the second order yet closes/reconciles known first fills.
- UNKNOWN intent/result and conservative maximum remain traceable.
- Pre-placement fsync failure produces zero POSTs and BLOCKED.
- Nonempty account journal refuses automatic restart, regardless of session UUID; competing writer lock is rejected.
- Abnormal caller cancellation latches fatal, retains history and performs known cleanup. Repeated cancellation of an in-flight POST waits for the same task rather than resubmitting.

**Verdict: PASS for the current minimum operating-system guarantee and conservative no-restart boundary.** Physical power-loss/storage semantics and SIGKILL mid-request were not independently proven. Keep a stable configured journal location and manually review history before any operator-led new session. No automatic recovery framework is required or requested.

## 10. Account Compatibility

The EOA restriction is executable, not just documentation:

- M1 `rdp_sm.py:174-213` derives actual signer from the key, checks configured funder and derives existing official API credentials without Gateway.connect/key creation.
- M2 `:363-366` requires signature_type0 and actual signer=funder.
- Fresh snapshot `:475-482` rechecks SDK/builder/config/Gateway identity.
- Signed POST `:205-213` checks signer/maker and submitted amounts.
- Tests `:443` and `:672` exercise identity mismatch and proxy refusal with zero POSTs.

Supported scope: **verified EOA signer=funder, SDK V2**. Proxy/deposit signature types are BLOCKED. Default Config signature_type2 is not silently supported.

Actual intended MicroLive account type: **NOT_VERIFIED**; no live account or credential was inspected. No ACCOUNT_COMPATIBILITY_BLOCKER is asserted without evidence about that account. Proxy support remains DEFER; choosing a compatible verified EOA is a pre-live prerequisite, not a demand to add new account interfaces.

## 11. Trading Window

Submission guards correctly require elapsed >=15 and enough time for the configured fill window plus 15-second buffer before T+240. They recheck deadline/freshness immediately before actual POST. Delayed first ACK cannot reset T0 or permit another BUY past deadline/cutoff.

### M2-H02 — Active reconciliation postpones cancellation past deadline/cutoff

- Severity: **HIGH**.
- Source: `rdp_sm_execution.py:919-936`. The while condition checks expiry before a complete reconciliation loop. Its active `reconcile_owned_order(..., retry_delays=())` passes no deadline; cancellation occurs only afterward.
- Existing production capability: `execution/filltruth.py:211-241` already accepts an overall deadline and bounds the attempt with timeout_at; SDK `client.py:577-607` can read arbitrarily many valid pages before returning.
- Reproduction: one Maker BUY starts at **T+220**, T0=0, fill deadline3. The active official trade read returns six valid pages; each advances fake time by **4 seconds**, below the normal five-second per-operation HTTP timeout. Pages contain only unrelated maker legs. No DELETE/network failure is simulated.
- Observed: pages finish at mono4/8/12/16/20/24; cancellation is **first initiated at mono24 / T+244**, then terminal verification succeeds and result is SAFE. Exactly one POST occurred.
- A separate held-GET diagnostic also showed the event loop responsive, order LIVE and cancel_requests=[] after the original deadline; releasing only the GET allowed cancellation to begin. The paginated case avoids reliance on a single long request.
- MicroLive impact: acknowledged orders remain intentionally active beyond the chosen fill window and can remain live into the final60-second no-trading interval, even though cancellation could have been initiated earlier. Read failure/latency does not trigger deadline cleanup while it is awaited.
- Blocker: **YES**. Decision: **FIX NOW**.
- Smallest repair scope: bound active reconciliation by the original remaining window/cutoff and initiate known-order cleanup at expiry independently of long reads/pages. Reuse the existing deadline capability where appropriate; ensure any unfinished SDK read cannot later authorize stale closure or silently mutate evidence. Keep final post-cancel reconciliation separate. No shared-core semantics change is requested.

This finding concerns **when cancellation begins**, not a guarantee that a venue will complete cancellation within a fixed number of seconds. The explicitly permitted post-expiry cancel/reconciliation overrun does not justify waiting for optional active polling before starting cancellation.

**Trading-window verdict: FAIL** for active-read expiry; submission cutoff checks pass.

## 12. Test Results

Environment: repository venv, Python **3.12.14**, existing pytest/pytest-asyncio and installed official SDK. No packages installed.

The preserved offline runner clears environment before imports, disables plugin autoload/cache, uses temporary CWD and denies workspace credentials plus socket/DNS. `-B` prevents bytecode files. M2 fixtures additionally deny forbidden Gateway/RPC paths and replace only HTTP transport/time/event scheduling/filesystem faults.

Exact root repeat commands (the separate reviewer independently executed the same selections):

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

**48 passed in 3.96s; 0 failed, 0 skipped.**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

**346 passed in 5.98s; 0 failed, 0 skipped; 1 deselected.** Split: 64 M1/F01 + 282 related FL regressions.

Total distinct selected suite cases: **394 executed / 394 passed / 0 failed / 0 skipped**, plus **1 deselected**. Separate reviewer also reported 394 passed; duplicate runs are not added to the total.

Both root runs ended with:

```text
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

The name of the existing guard does not mean M0 was re-audited or its harness modified.

### Core scenario assertions and gaps

All tests are in `tests/test_rdp_sm_m2_execution.py`.

| Scenario | Test / line | Actual assertion and observed result | Review verdict |
|---|---|---|---|
| E01 both ACK | `test_e01_e08_both_ack_no_fill_verified_close_and_next_window` :219 | Two distinct owned IDs, terminal cancellation, trade reads, zero inventory/cost; next window adds two orders | PASS |
| E02 first ACK / second REJECT | `test_e02_first_ack_second_explicit_postonly_reject_closes_fills` :234 | ACK/REJECTED distinct; immediate cleanup, confirmed YES2, cost0.98 | PASS |
| E03 UNKNOWN | `test_e03_h03_unknown_retains_commitment_no_retry_or_next_window` :258, three cases | First/second timeout/missing ID; next POST count unchanged; maximum retained | PASS |
| E04 partial/cancel race | :276, :301, :489 | YES3 once; WS during cancellation processed; cancel ACK with LIVE keeps4.85 and blocks next POST | PASS |
| E05 MATCHED/MINED | :313, two cases | Both pending statuses inventory0, quantity tracked, maximum retained, no next POST | PASS |
| E06 late CONFIRMED | :326, :516, :540, :563 | Stale proof invalid; no next POST; official correction recovers once; cross-order/snapshot races block | PASS |
| E07 terminal then LIVE | :353, :531, :746 | WS/fresh-order/open-list contradiction invalidates proof and stops next POST | PASS |
| E08 no fill cancellation | :219 | Actual terminal verification, inventory/cost0, safe continuation | PASS |
| E09 capital | :366, :375, :470, :698 | Over$30 refused; prior confirmed cost included; exact$30 accepted; consistent external-order reserve checked | **FAIL coverage/composition**: independent M2-H01 reproductions bypass official liquid-funds check |
| E10 cutoff/exit | :388, :451, :609, :621, :642 | Early/late starts rejected; delayed POST no second; crash/cancellation/history guarded | **FAIL active-read schedule**: M2-H02 permits late cancellation initiation |
| Focused validation/persistence/identity | :419, :425, :443, :650, :663, :672, :705, :726 | Invalid quotes stop before POST; real SDK tick refresh; failures retain identity/risk; proxy blocked | PASS for supplied schedules |

The 48 cases contain actual safety assertions, not merely mock-call checks. Async POST cancellation and WS/cancel interleaving are exercised. Deterministic fake time and temporary journals isolate tests. Missing coverage was non-atomic account-list visibility and time spent inside active paginated reconciliation.

### Existing exclusion

`tests/test_config_aliases.py:105::test_cli_conflict_stops_before_settlement_preflight` remains the previously justified deselection. Unchanged CLI Config.load's dotenv discovery reaches protected workspace .env before the expected ConfigAliasError. Source and hashes match accepted earlier reports; M2 does not change Config, CLI or this test. No real credentials were enabled. It is **deselected, not passed or skipped**. Exclusion: **JUSTIFIED** for this guarded review.

### Read-only supplemental reproducer

This exact command was run from project root. It creates only temporary files, exercises real executor/Gateway/Tracker/FillTruth/Journal, and changes only external HTTP/time boundaries. Witness assertions intentionally establish the defective behavior; they are not counted as passing safety tests.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import os, sys, socket, tempfile, runpy, asyncio, logging
from pathlib import Path
root = Path.cwd()
kept = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
os.environ.clear()
os.environ.update(kept)
network = []
def deny(*a, **kw):
    network.append(True)
    raise AssertionError('offline network forbidden')
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        p = Path(os.fsdecode(args[0])).absolute()
        if p == root / '.env' or root / '.aws' in p.parents:
            raise AssertionError('credentials forbidden')
sys.addaudithook(audit)
for name in ('connect', 'connect_ex', 'sendto', 'sendmsg'):
    if hasattr(socket.socket, name):
        setattr(socket.socket, name, deny)
for name in ('create_connection', 'getaddrinfo', 'gethostbyname', 'gethostbyname_ex'):
    setattr(socket, name, deny)
sys.path[:0] = [str(root / 'tests'), str(root / 'src')]
import pytest, httpx, structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
ns = runpy.run_path(str(root / 'tests/test_rdp_sm_m2_execution.py'))
async def case(name):
    with tempfile.TemporaryDirectory(prefix='m2-review-') as tmp, pytest.MonkeyPatch.context() as mp:
        fixture = ns['env'].__wrapped__(mp, Path(tmp))
        env = next(fixture)
        client = None
        try:
            v, e = await ns['open_executor'](env)
            original = v.response
            commitments, pages, order_reads, cancel_times = [], [], [], []
            if name == 'own-ACK-lag':
                v.capital['balance'] = '3000000'
                v.open_rows_override = []
            elif name == 'newer-order-list':
                v.capital['balance'] = '6000000'
            else:
                v.time = ns['START'] + 220
            def response(request):
                if request.method == 'POST':
                    commitments.append(float(e.committed()))
                if name == 'newer-order-list' and request.url.path == '/data/orders':
                    order_reads.append(len(order_reads) + 1)
                    v.open_rows_override = [{
                        'id': 'external', 'asset_id': '3', 'market': 'other',
                        'side': 'BUY', 'price': '.9', 'original_size': '5',
                        'size_matched': '0', 'status': 'LIVE', 'associate_trades': [],
                    }] if len(order_reads) == 4 else []
                if name == 'paginated-active-read' and request.url.path == '/data/trades' and not v.cancels:
                    cursor = request.url.params.get('next_cursor')
                    i = 0 if cursor == 'MA==' else int(cursor[1:])
                    v.mono += 4
                    v.time += 4
                    pages.append((i, v.mono))
                    foreign = {
                        'id': 'foreign-' + str(i), 'status': 'CONFIRMED',
                        'market': ns['CID'], 'maker_orders': [{
                            'order_id': 'foreign-order-' + str(i), 'asset_id': '3',
                            'side': 'BUY', 'price': '.5', 'matched_amount': '1',
                        }],
                    }
                    return httpx.Response(200, json={
                        'data': [foreign],
                        'next_cursor': 'LTE=' if i == 5 else 'p' + str(i + 1),
                    }, request=request)
                return original(request)
            client = httpx.Client(transport=httpx.MockTransport(response))
            mp.setattr('py_clob_client_v2.http_helpers.helpers._http_client', client)
            v.on_cancel = lambda venue: cancel_times.append(
                (venue.mono, venue.time - ns['START'])
            )
            r = await e.execute_window(
                'w1', ns['quotes']() if name == 'own-ACK-lag' else [ns['quotes']()[0]]
            )
            print(name, 'barrier=', r.close_barrier, 'posts=', len(v.posts),
                  'commitments=', commitments, 'deadline=', r.fill_deadline_monotonic,
                  'cancel=', cancel_times, 'pages=', pages)
            # Witness assertions demonstrate the defects; these are NOT safety PASS assertions.
            assert r.close_barrier == 'SAFE_TO_CONTINUE'
            if name == 'own-ACK-lag':
                assert len(v.posts) == 2 and commitments == [2.45, 4.85]
            elif name == 'newer-order-list':
                assert len(v.posts) == 1 and len(order_reads) == 4
            else:
                assert pages == [(i, 4.0 * (i + 1)) for i in range(6)]
                assert cancel_times == [(24.0, 244)]
        finally:
            if client:
                client.close()
            try:
                next(fixture)
            except StopIteration:
                pass
async def main():
    for name in ('own-ACK-lag', 'newer-order-list', 'paginated-active-read'):
        await case(name)
asyncio.run(main())
print('network/DNS attempts=', len(network))
assert not network
PY
```

Observed output:

```text
own-ACK-lag barrier= SAFE_TO_CONTINUE posts= 2 commitments= [2.45, 4.85] deadline= 3.0 cancel= [(3.0, 63.0)] pages= []
newer-order-list barrier= SAFE_TO_CONTINUE posts= 1 commitments= [2.45] deadline= 3.0 cancel= [(3.0, 63.0)] pages= []
paginated-active-read barrier= SAFE_TO_CONTINUE posts= 1 commitments= [2.45] deadline= 3.0 cancel= [(24.0, 244)] pages= [(0, 4.0), (1, 8.0), (2, 12.0), (3, 16.0), (4, 20.0), (5, 24.0)]
network/DNS attempts= 0
```

Both funding witnesses and the pagination witness were independently reproduced by the separate reviewer and root. Existing suite failures0 do not erase these three violated safety expectations.

Additional unchanged-core/whitespace commands:

```bash
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/engine.py src/polymaker/config.py src/polymaker/favorite_live.py
```

Both exit0. Live official interface/storage smoke remains **NOT_VERIFIED / NOT_EXECUTED**.

## 13. MicroLive Impact Assessment

| Issue | Evidence | MicroLive impact | Blocker | FIX NOW / DEFER / DROP |
|---|---|---|---|---|
| **M2-H01, HIGH:** missing liquid-funds reservations | Real POSTs4.85 against balance3; newer observed reserve4.50 ignored against balance6 | Can authorize unfunded orders/partial failure despite locally known exposure | **YES** | **FIX NOW**: SM snapshot/admission plus two regressions |
| **M2-H02, HIGH:** active read delays expiry cleanup | Six4s official pages; cancellation begins T+244 with deadline T+223 | Known orders can fill beyond intended window and into final60s | **YES** | **FIX NOW**: deadline-governed active polling/cleanup plus regression |
| EOA-only/proxy compatibility | Runtime signature0/funder checks; proxy test blocks; current account unverified | Proxy execution unavailable; supported EOA safety is constrained | NO for supported M2 scope; compatibility needs proof before live | DEFER proxy authority; do not guess actual account type |
| Official interface/storage/finality/latency verification | Intentionally fake HTTP/time; no physical crash/live service validation | Pre-live preparation evidence still required | NO for offline M2 review by itself | DEFER to authorized preparation |
| No automatic recovery / full M4 report | Nonempty ledger blocks; raw execution facts recorded | Manual risk review and later report presentation required | NO | DEFER unchanged |

No generic architecture expansion, full CapitalGate, proxy implementation or recovery framework is requested. Two actual safety blockers were identified; neither is justified by previously approved deferred dispositions.

## 14. Deferred Issues

Preserve approved dispositions:

- Proxy signer/funder authoritative linkage; verified EOA-only remains the supported scope.
- Broader unused SDK RFQ subclient boundary.
- M1 raw book-level grid validation; actual submitted M2 quote grid is already checked.
- F01 disclosed zero-share raw-row filtering limitation.
- Live official authentication/schema/completeness/finality, actual account compatibility and physical storage guarantees: NOT_VERIFIED.
- Automatic UNKNOWN correlation/restart recovery excluded; historical journal requires manual review.
- Full timeline/report presentation belongs to M4; M2 records underlying facts.

M0-H01/H02/H03 remain **CONTAINED in actual M2**, with original audit findings preserved. The new capital and active-deadline findings are **not deferred**.

PROVEN: reviewed executable suite results, production reuse, adverse POST/cancel schedules, supported EOA refusal, current unchanged-core comparison and zero attempted external network operations.

INFERRED: the demonstrated admission and cleanup schedules can occur with delayed official listings and paginated/network-latency responses; this implication does not assume a concurrent account writer.

NOT_VERIFIED: live venue acceptance of an unfunded request, actual deployment account type, endpoint availability/finality, physical crash durability or all possible event interleavings. Review failure does not require claiming those unverified outcomes occurred.

## 15. Final Reviewer Decision

**FAIL**

The real executor and its M0 containment exist, selected regressions pass, and confirmed inventory/closure/journal evidence is substantial. However, official liquid-funds admission omits observed/known commitments and active reconciliation can postpone cancellation beyond the fill deadline and market cutoff. Both are reproduced through actual production components with Fake external boundaries and zero network attempts.

The separate reviewer independently concluded FAIL with these two HIGH / FIX NOW findings. Root reproduced them without fixing implementation or tests. Passing394 submitted cases is insufficient to accept current M2 as the future execution kernel.

Smallest required next action: **targeted SM-only repair of M2-H01 and M2-H02 with their adverse schedules, followed by independent re-review**. No shared-core architecture decision is necessary for the proposed scope. Architect acceptance of current known risks would be an explicit disposition decision; this review does not grant it. Do not begin M3 or MicroLive.

## 16. Change Manifest

Review-created file only:

- `docs/development/RDP_SM_M2_INDEPENDENT_REVIEW.md`.

Neither root nor the independent agent changed source, tests, Worker reports, earlier reviews, shared core or SOP. Temporary diagnostic journals were outside the project and removed by TemporaryDirectory.

Input SHA-256 anchors verified at review entry and completion:

```text
dd8044c6a66fc1fd54484d81c1263ad4741c8bfcce147650ef469b1100cf1cb7  src/polymaker/rdp_sm_execution.py
ceeb487c31690096655dd0f81367022b74271195ca64164ab10e9ec089c3a771  tests/test_rdp_sm_m2_execution.py
7d87b96a932028a76995545b19703162337d5b8f0edae59b1cde7f21a7e5849c  docs/development/RDP_SM_M2_EXECUTION_INTEGRITY.md
97acb95c0f5c0f5048e46499bfa49efcb90f3453cc19c6c616c1ff159cbab50f  src/polymaker/rdp_sm.py
00133cf7024d7b2d7bedb5f4d5a717015844146b73d987f3cfb6c209f07468e8  src/polymaker/cli.py
7713ddcd71b356617cb595f24cc1417706068bdd27edc815af050cb563867356  tests/test_rdp_sm_m1.py
07d5b0f5f0233d437f08b63a9c8041105857fd50912d221c8b749f6acd8a381e  tests/test_rdp_sm_m1_f01_token_identity.py
c820947c5bf634eadf215fb7e7d75af5e47a47222cd842b209ae4e8dbf3caa9d  tests/rdp_sm_m0_offline.py
```

All pre-existing development reports also retained their review-entry hashes. Final Git status (`--short --untracked-files=all`):

```text
 M src/polymaker/cli.py
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? docs/development/RDP_SM_M0_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_F01_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_F01_TOKEN_IDENTITY.md
?? docs/development/RDP_SM_M1_INDEPENDENT_REVIEW.md
?? docs/development/RDP_SM_M1_SAFE_SESSION.md
?? docs/development/RDP_SM_M2_EXECUTION_INTEGRITY.md
?? docs/development/RDP_SM_M2_INDEPENDENT_REVIEW.md
?? src/polymaker/rdp_sm.py
?? src/polymaker/rdp_sm_execution.py
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
?? tests/test_rdp_sm_m1.py
?? tests/test_rdp_sm_m1_f01_token_identity.py
?? tests/test_rdp_sm_m2_execution.py
```

Branch/HEAD unchanged; staged diff empty; tracked CLI diff unchanged from review entry. Shared-core changes: **NO**. No commit, fixes or real trading performed.
