# RDP-SM M3 — Independent Review

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- SOP v1.8 FROZEN; FL-first / Thin SM Orchestrator; MicroLive First. MicroLive authorization **NO**.
- Entry: pre-existing 22-line CLI addition and untracked SM source/tests/reports. Staged diff empty. Untracked delivery files included in review, not ignored because HEAD is unchanged.
- Independent reviewer: separate existing agent `/root/m2_independent`, who did not implement M3. It read current code, independently ran 467 selected cases and independently reproduced the capital-handoff failure with actual Type2 execution, concluding **FAIL**. Root authored M3, repeated checks/diagnostics and assembled this report; root self-check is not the sole independent review provenance.
- Captured hashes for 170 existing files under src/tests/docs/config/livecfg; final comparison confirms only this new Review Markdown was created. No source/test/config/history/SOP changes, cleanup, checkout or historical RDP branch access. No real credentials, external requests, RPC, real orders/cancels, fixes or M4 work.

## 2. Review Scope

Read the required M3 Worker, historical Type2 account, M2 execution and pagination-independent-review reports. Inspect current M3/Type2 production changes, actual M1/M2/Gateway/Tracker/FillTruth/Journal call chains, test assertions and necessary regressions.

Only assess frozen strategy correctness, single-market integration, orders/capital/timing/close boundaries and minimal summary. No accepted M2 architecture re-audit, strategy-profit optimization, CapitalGate/recovery framework, new APIs, Merge/Redeem or multi-market feature work.

| Worker claim | Actual independent evidence | Verdict |
|---|---|---|
| Real M1/M2/M3 execution | Production imports and actual SDK/Gateway calls; delegating instrumentation, no Fake M2 | CONFIRMED |
| Correct financial integration | Initial M3 account observations discarded before real M2 owner; unsafe POST independently reproduced | **FAIL** |
| NORMAL / REBALANCE / transition / confirmed stock | Actual formulas plus full positive/negative-Delta real executions | PASS in tested paths |
| Timing / close / ActiveCycles / summary | Measured full69-window trace and failure-path assertions | PASS in tested paths |
| Type2 local compatibility | Actual SDK mode2/maker/signer, derived Safe, mutation rejection, Type0 regression | PASS for offline configuration scope |
| Capital Safety | Ordinary local-capital blocks pass; cross-reader known obligation lost | **FAIL overall** |
| 467 passed / zero network | Separate reviewer and root independently reproduce counts/guards | CONFIRMED; insufficient coverage of handoff |
| Shared core unchanged | Source baseline diff and review-entry/final hashes | CONFIRMED |

## 3. Production Architecture

```text
SMTradingLoop.run
  -> initial M1 run_preflight(_reads = a new _ExecutionReads)
     -> FL Gamma / market parser / OrderBook / dynamic tick / Gateway account reads
  -> SMWindowExecutor.open
     -> another M1 startup preflight with another _ExecutionReads
     -> production M2 client / Tracker / StateStore / durable Journal
  -> real M2.verify_close + managed _snapshot
  -> M2 confirmed economic fills -> M3 make_quotes
  -> real M2.execute_window(..., quote_revision=...)
     -> production Gateway + official SDK signing/postOnly/strict placement
     -> M2 deadline/cancel/terminal/production FillTruth/Tracker/barrier
  -> M3 next window only after SAFE; otherwise STOP
  -> M2 final verify_close(cancel=True) -> summary -> resource close
```

M3 does not call Gateway.place/cancel directly, implement a separate fill truth, own a second capital ledger or run Generic Engine/FL strategy. The runner is a one-market opt-in library; no extra CLI/live entry. All interfaces remain existing official HTTP paths; deterministic Safe derivation is local SDK code, not RPC/relayer service access.

Actual component reuse is confirmed. **Initial financial-evidence integration is defective**: `rdp_sm_loop.py:241-267` neither accumulates earlier preflight observations across wait iterations nor passes them into M2. M2 preserves only the observations from its own later preflight (`rdp_sm_execution.py:451-462`). Section14 documents the executed failure.

## 4. NORMAL

`rdp_sm_loop.py:30-58` uses Decimal confirmed YES/NO quantities:

```text
abs(YES - NO) < 5
YES price = current YES BestBid - current YES tick
NO price  = current NO BestBid  - current NO tick
YES size  = 5 + max(-Delta, 0)
NO size   = 5 + max( Delta, 0)
```

Actual five parameterized quote cases verify Delta0 →5/5, +2 →5/7, −3 →8/5 and the ±5 threshold. Market tokens come from actual M1 Up/Down mapping, not title guessing.

Price range/grid, noncrossing ask, official minimum and SDK size precision are checked without altering a quote to bypass restrictions; illegal requests are skipped without placement. M2 independently refreshes/validates before sending. Actual dynamic-tick test changes YES tick .01→.001 and next NORMAL price .48→.489. A real-path pair-sum>1 test makes two legal Maker POSTs, proving no fixed pair-cost≤1 filter. SDK transport asserts BUY/GTC/postOnly and exact signed economics.

**NORMAL: PASS** for frozen formula/legality behavior. Funds admission remains subject to the separate failing handoff.

## 5. REBALANCE

For abs(Delta)≥5, code requests exactly one weaker-side BUY: Delta>0 buys NO; Delta<0 buys YES. Size exactly abs(Delta), price current legal BestBid. No BestBid−Tick, upward quantity rounding, SELL/Taker, STUCK label or MaxRebalanceSec.

The actual no-fill requote test produces NORMAL, REBALANCE, REBALANCE, with weaker-side NO bid .48 then .50 and one POST per rebalance window. It waits for real M2 closure; no intra-window replacement.

Independent additional negative-side execution observes:

```text
NORMAL -> REBALANCE -> NORMAL
Confirmed inventory: 0/5 -> 3/5 -> 5/5
REBALANCE: YES5@.49
next NORMAL: YES7@.48, NO5@.479
5 actual POSTs; 3 ActiveCycles
average YES/NO costs .486/.479; theoretical pair PnL .175
```

Unrepresentable rebalancing size5.001 is rejected rather than rounded up. **REBALANCE: PASS** in these paths.

## 6. State Transitions

Only production M2 `confirmed()` supplies M3 shares/costs. The strategy projects the cumulative set each time; it never adds cumulative window totals again or resets stock. Production `(trade,owned order)` dedup/ownership remains the truth source.

The full test confirms 5/0 (+5) → REBALANCE, then 5/3 (+2) → NORMAL, then 5/5 (0). The mirrored diagnostic confirms the negative case. Later windows retain own inventory without rerunning clean-zero M1 checks.

MATCHED/MINED integration variants report zero confirmed inventory/ActiveCycles and stop after one blocked window. Actual official confirmation arriving between quote calculation and M2 admission increments the existing evidence revision, updates confirmed inventory and rejects the old quote before a new intent (`STRATEGY_QUOTE_EVIDENCE_CHANGED`, execution source `:1113-1117`). Two original POSTs remain; no blind retry.

M3 requires previous M2 safety, re-reads current official books/identity/ticks and waits for M2 closure before next quotes. It does not derive stock from ACK/submitted quantities. **STATE TRANSITIONS / CONFIRMED INVENTORY: PASS** for reviewed assertions.

## 7. Execution Safety

- Production M2 is used for all sends, funds checks, deadlines, known-ID cancellation, terminal verification, confirmed economics and barrier proofs.
- M3 stops on a blocked window; final cleanup cannot change that raw window to SAFE or restart the loop. A later successful final cleanup may yield final SAFE with run BLOCKED, which is distinct from COMPLETED.
- $3-capital test sends only the first affordable BUY, blocks the second, closes the first and adds no next window. UNKNOWN test makes one POST, retains its $2.40 maximum, reports INCOMPLETE/BLOCKED and never retries.
- Cancel ACK with LIVE state and unresolved MATCHED/MINED both return INCOMPLETE; owned terminal/trade/complete distinctions are preserved.
- Default `run()` blocks before reads/initialization. `offline=True` additionally requires MockTransport on both SDK/async clients. No CLI omission enables live execution. No live-mode option was introduced.

**M2 owned-order barrier/control integration: PASS. Overall capital safety: FAIL** because M3 loses an already observed external obligation before M2's admission logic can account for it. This is a new caller handoff failure, not proof that M2's accepted ledger/barrier internals stopped working.

## 8. Market Timing

M1 validates exact bound BTC5m identity, market duration300 and official start/end. Before15 only a clean preflight whose sole failing condition is time may wait; account/market unknown cannot be ignored.

M3 uses current M2 official time and its existing 15-second close buffer. Default fill interval3 is M2's original monotonic T0/deadline; no candidate starts when elapsed+fill time+buffer≥240. M2 repeats cutoff checks at each send and handles in-flight operations/cleanup beyond3.

Full trace facts are measured at the HTTP POST boundary and computed run fields: first POST15, last219, stop240. No post before15/after240; actual known states verified cancelled. Separate T14/T240 and slow/held SDK read/delayed-ACK regressions pass. Normal book movement causes no intra-window chasing.

T240 is trading cutoff/final execution close, **not T300 market settlement**. No settlement/merge/redeem is performed or claimed. **MARKET TIMING / END-OF-MARKET owned-order cleanup: PASS** in the reviewed traces; this does not cure incorrect prior capital admission.

## 9. Type 2 Integration

`rdp_sm_execution.py:310-340` validates current-key-derived signer, configured/SDK/Gateway funder, chain137 and mode consistency. Type0 requires signer=funder. Type2 requires distinct identities and exact official-SDK-derived Safe; unsupported modes/mismatch block. Actual signed POST mode is checked at `:253` alongside existing maker/signer/economics.

Seven Type2/Type0 cases independently pass: actual mode2 signed maker/funder vs signer, correct balance signature_type parameter, funder positions, valid configuration execution, wrong funder rejection, Type0 execution and key/builder/type mutation blocking. Authenticated order listing uses the existing SDK identity; no new wallet/signing logic, shared-core changes or RPC.

Current Type2 Safe permission remains **NOT_VERIFIED_FOR_LIVE**, explicitly recorded and not inferred from historical balance or fake signatures. This is a pre-live prerequisite, not this review's FAIL reason. No real current account/credentials were queried. **TYPE2 INTEGRATION: PASS for the authorized offline scope.**

## 10. Full Offline Market

The separate reviewer independently ran the central test through real M1/M2/M3/Gateway/SDK/Tracker/FillTruth/Journal. The factory instrumentation delegates to the actual classmethod and sets the test venue's reference; it does not fabricate an executor. Fake callbacks create official response trade facts attached to actual ACK IDs, not strategy labels or a pre-set RunResult.

| Observed elapsed | Real loop action under Fake official/time boundaries | Confirmed YES/NO / Delta |
|---|---|---|
| 0–14 | Clean M1 time-ineligible waits; zero POST | 0/0 /0 |
| 15 | NORMAL two BUYs, YES5@.48 and NO5@.479 | 0/0 /0 at quote |
| 18 | Official confirmed YES5 after close; next REBALANCE NO5@.48 | 5/0 /+5 |
| 21 | Official confirmed NO3; next NORMAL YES5/NO7 | 5/3 /+2 |
| 24 | Official confirmed NO2; NORMAL resumes | 5/5 /0 |
| 24–219 | Actual safe window calls continue, no new fills | 5/5 /0 |
| 219 | Last actual HTTP order submission, window69 | 5/5 /0 |
| 222–239 | Closing headroom prevents new window; reads only | 5/5 /0 |
| 240 | Actual M2 final cancel/verification/reconciliation; STOP | 5/5 /0 |

Confirmed claim: **69 windows =68 NORMAL+1 REBALANCE, 137 Maker POSTs, 3 ActiveCycles/3 confirmed legs, all owned rows cancelled, final SAFE_TO_CONTINUE/COMPLETED.** Counts come from actual loop iterations and owned confirmed facts; time fields come from fake official clock and HTTP boundary, then asserted against expectations. They are not directly assigned69/3/219/240 by the runner or fake executor.

The central fixture contains no external account commitment disappearing at admission. Its valid positive result therefore does not refute section14's separate unsafe schedule. **FULL OFFLINE MARKET: PASS for that nominal scenario.**

## 11. Run Result

`RunResult` contains required IDs/start/stop/reason, window/state/active/fill/block counts, stock/delta/pairs/residual/weighted costs/theoretical PnL, barrier/status, live-authority and raw windows/skips. Journal records permit later M4 reuse; no extra analytics framework.

Actual nominal output: YES5, NO5, Delta0, CompletedPairs5, Residual0, average costs .48/.4796, theoretical pair PnL .202. The formula uses confirmed purchase cost, not book prices/cash/face value. Zero-fill / one-fill / multiple-fill windows produce ActiveCycles2 across three windows. Late trade ownership maps to its original quote window. Unknown costs are not replaced by estimates.

ExecutionStatus COMPLETED means required own execution closeout verified, not profitable/settled/live-authorized. BLOCKED preserves admission failure despite successful known cleanup; INCOMPLETE preserves unresolved close. Those definitions work in tested normal/failure paths. The handoff counterexample still incorrectly reports a normal complete run after unsafe account-capital admission; its owned zero-fill orders happen to close correctly.

## 12. Independent Test Results

Separate reviewer exact commands/results:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m3_loop.py -q --tb=short
```

**22 passed in 96.10s**. Root repeat **22 passed in 96.85s**.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

**99 passed in 17.81s**. Root repeat **99 passed in 18.35s**. Includes 7 Type2/Type0 and 92 M2 safety cases.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

**346 passed, 1 deselected in 8.51s**. Root repeat **346 passed, 1 deselected in 8.25s**.

Distinct submitted pytest total: **467 executed /467 passed /0 failed /0 skipped; 1 deselected**. Repeat runs are not added. **One additional safety defect was independently reproduced**, including T0 and T60 schedules; these are diagnostic failures of safety, not passing safety pytest cases and not included in467.

All guarded suites/diagnostics: **zero network/DNS attempts; workspace credentials denied**. Existing Python3.12.14 venv; environment cleared before imports, socket/DNS/open guards, temporary CWD, no plugin autoload/cache, `-B`; public dummy keys and MockTransport only. No package installation or actual authenticated live request.

The unchanged credential-protection exclusion `test_cli_conflict_stops_before_settlement_preflight` remains **JUSTIFIED**: CLI dotenv discovery hits protected workspace credentials before the expected ConfigAliasError. No protection relaxed. Deselected is not skipped/passed.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py src/polymaker/rdp_sm_loop.py tests/test_rdp_sm_m3_loop.py tests/test_rdp_sm_type2_account.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm_execution.py src/polymaker/rdp_sm_loop.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m3_loop.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

All exit0. No tests changed to obtain PASS. See section14 for exact rerunnable diagnosis, whose exit0 proves the **unsafe result reproduced**, not safety PASS.

## 13. FL Regression

64 M1/F01 and282 selected existing FL cases pass independently. Shared Gateway/Tracker/FillTruth/Journal/config/Engine/FL strategy source unchanged. Current CLI diff is the preserved earlier read-only command addition, not a M3 live command.

M2-H01/H02 **component regression tests PASS**. M0-H01/H02/H03 remain **CONTAINED** for actual owned-executor cache/late-confirmation/UNKNOWN cases. Do not infer end-to-end M3 capital safety from component-only retention tests: M3-H01 discards evidence before that component can retain it.

## 14. Findings

| Finding ID | Severity | Evidence | MicroLive Impact | FIX NOW / DEFER / DROP |
|---|---|---|---|---|
| **M3-H01** | **HIGH** | M3 initial/wait preflight cache is not handed to M2. $6 funds + retained-then-discarded external $4.50 + new YES$2.45 produces2 actual Gateway.place/POSTs; obligations at POST $6.95 then$9.345; external still LIVE; final COMPLETED/SAFE | Known occupied collateral can be spent again in the actual single-market Type2 path. No effective fail-closed restriction survives reader replacement. **Blocker YES** | **FIX NOW** |

### Exact code path / root cause

1. `rdp_sm_loop.py:241-242` constructs a new observer for every initial/wait preflight. The first sees the external order and M1 correctly computes its reserved amount.
2. READY only means basic preflight eligibility, not quote affordability. $1.50 remaining is sufficient for M1's minimum lower-bound check but insufficient for the forthcoming $2.45 quote.
3. `:265-267` calls M2.open without transferring earlier observations; the pre-T15 loop also discards prior reader observations on its next iteration.
4. `rdp_sm_execution.py:451-462` preserves only the **new M2 startup** reader's map. If its listing omits the external ID, the actual execution client receives no record of it.
5. `_snapshot:606-667` correctly reserves historical/fresh/local identities that it knows, but cannot reserve the missing external observation. Fresh raw balance6 is treated as available; the two Maker orders pass M2 admission.

### Independent reproduction / observed vs expected

Separate reviewer used actual Type2 SDK/Gateway and counted delegated actual Gateway calls. External remains LIVE throughout; later lists omit only external while own SM rows remain normally visible. No concurrent writer, actual cancellation/fill/release of that external obligation, fake M2 or code patch.

```text
Balance $6; external LIVE BUY5@.90 = $4.50
First M3 listing contains external; every later listing omits it
New NORMAL YES5@.49 = $2.45; NO5@.479 = $2.395
Start T+60: POST times [60,60]
Start T+0:  POST times [15,15]
Both: Gateway.place calls=2, POSTs=2
Account obligations at POST=[6.95,9.345]
External still LIVE; actual M2 cache lacks external
Window and final barrier SAFE_TO_CONTINUE; run COMPLETED
Network/DNS attempts=0; credentials denied
```

Root separately confirmed the exact $6/$4.50/$2.45 schedule: initial M3 cache `['external']`, M2 startup cache `[]`, 12 list reads, two POSTs and the same obligations/completed result. The below script recreates that T60 case without editing project files.

**Expected:** preserve external $4.50 across M3 reader replacements and pass it to the actual M2 admission owner before any placement, or remain fail-closed. New $2.45 exceeds conservatively available$1.50, so zero unsafe Gateway.place/POST calls. A later empty listing is not terminal proof.

**Smallest fix:** retain all startup/pre-T15 observed unresolved evidence across M3 readers, validate conflicts and hand the retained risk context to the existing M2 owner before its first intent. Preserve first economics/sticky risk and existing identity dedup/release semantics. Add real-path T0/T60 omission regressions. No shared Gateway/signing/Tracker/FillTruth change, capital framework, new DB/API or recovery feature is required.

### Exact guarded read-only reproducer

From project root; public dummy fixture keys only. Assertions deliberately establish the current unsafe behavior. Exit0 means reproduced, **not safety PASS**.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import os, sys, socket, tempfile, asyncio, json, io, contextlib
from pathlib import Path
from decimal import Decimal
from copy import deepcopy
root = Path.cwd()
keep = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
os.environ.clear(); os.environ.update(keep)
network = []
def deny(*a, **kw):
    network.append(1); raise AssertionError('NETWORK_FORBIDDEN')
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        p = Path(os.fsdecode(args[0])).absolute()
        if p == root / '.env' or root / '.aws' in p.parents:
            raise AssertionError('CREDENTIALS_FORBIDDEN')
sys.addaudithook(audit)
for name in ('connect', 'connect_ex', 'sendto', 'sendmsg'):
    if hasattr(socket.socket, name): setattr(socket.socket, name, deny)
for name in ('create_connection', 'getaddrinfo', 'gethostbyname', 'gethostbyname_ex'):
    setattr(socket, name, deny)
sys.path[:0] = [str(root / 'tests'), str(root / 'src')]
import pytest, httpx
from py_clob_client_v2.http_helpers import helpers
from polymaker.rdp_sm_execution import _ExecutionReads
from test_rdp_sm_m3_loop import env, runner, on_windows
from test_rdp_sm_m2_remediation import external_order
async def check(tmp):
    mp = pytest.MonkeyPatch()
    gen = env.__wrapped__(mp, Path(tmp)); v, cfg = next(gen)
    v.capital['balance'] = '6000000'
    v.rows['external'] = external_order()
    v.books['1']['bids'][0]['price'] = '.50'
    on_windows(v, lambda *_: None, stop_after=1)
    initial_response = v.response
    reads, exposures, clients = [], [], []
    real_connect = _ExecutionReads.connect_account
    async def observe_connect(self):
        await real_connect(self); clients.append(self.gateway._client)
    mp.setattr(_ExecutionReads, 'connect_account', observe_connect)
    def response(request):
        if request.url.path == '/data/orders':
            reads.append(len(reads) + 1)
            rows = ([v.rows['external']] if len(reads) == 1 else
                    [r for oid, r in v.rows.items()
                     if oid != 'external' and r['status'] == 'LIVE'])
            return httpx.Response(200, json={'data': deepcopy(rows), 'next_cursor': 'LTE='},
                                  request=request)
        if request.method == 'POST':
            body = json.loads(request.content)
            pending = sum((Decimal(r['price']) * Decimal(r['original_size'])
                           for r in v.rows.values() if r['status'] == 'LIVE'), Decimal(0))
            new = Decimal(body[0]['order']['makerAmount']) / 1_000_000
            exposures.append(float(pending + new))
        return initial_response(request)
    try:
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            mp.setattr(helpers, '_http_client', client)
            result = await runner((v, cfg)).run(offline=True)
        out = {'list_reads': len(reads),
               'initial_M3_observed': list(clients[0].observed_open_rows),
               'M2_startup_observed': list(clients[1].observed_open_rows),
               'actual_POSTs': len(v.posts), 'account_commitment_at_POST': exposures,
               'external_status': v.rows['external']['status'],
               'window_barrier': result.windows[0]['result']['close_barrier'],
               'final_barrier': result.final_close_barrier,
               'status': result.execution_status, 'windows': result.total_windows,
               'network_attempts': len(network)}
        assert out['initial_M3_observed'] == ['external']
        assert exposures == [6.95, 9.345] and len(v.posts) == 2
        assert out['external_status'] == 'LIVE' and result.execution_status == 'COMPLETED'
        return out
    finally:
        try: next(gen)
        except StopIteration: pass
        mp.undo()
with tempfile.TemporaryDirectory(prefix='rdp-sm-m3-review-') as tmp:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = asyncio.run(check(tmp))
assert not network
print(json.dumps(result))
PY
```

No other reproducible blocking finding established. Existing live-authority/interface/storage/recovery limitations remain DEFER under their prior dispositions; they are not the basis for FAIL.

## 15. Final Reviewer Decision

**FAIL**

One concrete HIGH/FIX NOW M3 financial-evidence integration defect admits known obligations above available collateral in actual Type2 execution. Separate reviewer reproduced it at T0/T60; root independently reproduced the exact $6.95 case. Current fail-closed controls do not contain this handoff loss. Passing nominal/full/regression tests and correct own-order closeout do not establish safe account-capital admission.

Frozen strategy, actual component reuse, nominal69-window timeline, Type2 offline mapping and selected regressions otherwise pass reviewed assertions. Do not reinterpret FAIL as requiring a broad M2 refactor or current Safe-owner proof before offline development. No fix implemented in review.

## 16. Change Manifest

Created only:

`docs/development/RDP_SM_M3_INDEPENDENT_REVIEW.md`

170 existing-file hashes remain unchanged; no production/test/config/SOP/historical report edits or deletions. No new diagnostic/test project files. Shared core changes **NO**. Staged diff empty.

Final status:

```text
 M src/polymaker/cli.py
?? docs/development/
?? src/polymaker/rdp_sm.py
?? src/polymaker/rdp_sm_execution.py
?? src/polymaker/rdp_sm_loop.py
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
?? tests/test_rdp_sm_m1.py
?? tests/test_rdp_sm_m1_f01_token_identity.py
?? tests/test_rdp_sm_m2_execution.py
?? tests/test_rdp_sm_m2_h01_commitment.py
?? tests/test_rdp_sm_m2_h01_pagination.py
?? tests/test_rdp_sm_m2_remediation.py
?? tests/test_rdp_sm_m3_loop.py
?? tests/test_rdp_sm_type2_account.py
```

## 17. Next Minimal Action

**Smallest Required Fix:** preserve and hand off M3 startup/wait risk observations to M2 before placement, with the T0/T60 omission counterexamples as targeted regressions.
