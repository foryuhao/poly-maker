# RDP-SM M2-H01 — Independent Review

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- Frozen SOP v1.8; FL-first / Thin SM Orchestrator; MicroLive First. MicroLive authorization: **NO**.
- Entry Git status: preserved 22-line M1 CLI addition and untracked M0/M1/F01/M2 source/tests/reports. Staged diff empty. Untracked files were included, not ignored. No checkout/reset/cleanup, old RDP branch access or SOP changes.
- Independent reviewer: separate agent `/root/m2_independent`, who did not implement this fix. It independently inspected source, executed 428 selected tests and reproduced the incomplete-pagination failures, concluding **FAIL**. Root authored the Worker fix, assembled this report and separately repeated tests/diagnostics; root self-check is not the sole independent reviewer provenance.
- The 161 entry files under src/tests/docs/config/livecfg were hashed before review. Final comparison confirms only this new Review Markdown was created. No real secrets were inspected or used.

## 2. Scope

Read all five required M2 reports, actual SM account/Window Executor code, new H01 tests, related Gateway account readers, necessary Tracker/FillTruth/Journal and existing regressions.

Only assess cross-snapshot liability retention, release evidence, conservative balance admission and targeted regressions. No complete M2 re-audit, Type2 adaptation, general external-order management, CapitalGate, recovery framework, shared-core modifications, M3, live requests or RPC.

Actual production chain remains SMWindowExecutor -> real ExecutionGateway/SDK -> real Tracker/StateStore/FillTruth/Journal -> close barrier. Fake HTTP, time/event ordering and filesystem faults are at external/test boundaries. Call-count wrappers delegate to the actual Gateway.place and fabricate no outcome.

## 3. Original Counterexample

**PASS for the original complete-snapshot read1/read2 schedules.**

The separate reviewer reran the $6 balance / external LIVE $4.50 / requested $2.45 schedules with the external venue order remaining LIVE and no external writer. Both returned:

```text
Gateway.place calls=0
SDK POSTs=0
barrier=BLOCKED
reason=OFFICIAL_CAPITAL_INSUFFICIENT
external commitment/identity retained
```

`tests/test_rdp_sm_m2_h01_commitment.py:22` verifies the actual delegated Gateway call count, no POST/cancel, unchanged external LIVE state and a second attempted window after more omissions. Startup-only observation (`:54`) also blocks correctly.

These improvements do not prove that observations from an incomplete paginated response are retained. The counterexamples in sections 5/6 expose that remaining gap.

## 4. Root Cause Verification

The former per-snapshot-only reservation map is supplemented by `ExecutionClobClient.observed_open_rows`:

- `rdp_sm_execution.py:186-203` retains first completed-list observations by exchange ID and latches observed cumulative/associate evidence.
- Startup observations survive M1 reader teardown and seed execution at `:386-401`, using the optional private reader argument in `rdp_sm.py:397`. Default M1/CLI behavior remains unchanged.
- `:544-615` merges historical external observations, both fresh lists and local unresolved attempts. Historical owned rows are excluded from external reservation while actual owned terminal/fill proofs control release.
- Relevant delivered external events invalidate the admission revision (`:285-310,508,632`); the verified capital revision remains bound through the final awaited spender check before intent.
- The narrow external zero-fill verifier (`:643-690`) uses actual official order/trade reads without assigning SM ownership/cancel permissions.

However, observations are committed **after the SDK returns the entire collection**:

```python
# Open orders :188,197
self.last_open_rows = super().get_open_orders(*args, **kwargs)
observed = self.observed_open_rows.setdefault(oid, dict(row))

# Trades :182,183
rows = super().get_trades(*args, **kwargs)
self.last_trades = rows

# Release proof :677-680
trades = await self.gateway.get_trades(market=normalize_order(row).market_id)
if any(leg.order_id == oid for raw in trades for leg in parse_trade(raw).maker_legs):
    observed["_sm_fill_observed"] = True
```

If an earlier valid authenticated page contains a LIVE order or relevant fill but a later page fails, these retention statements never execute for that evidence. The transport `_get :148-178` checks page shape/cursors but does not retain per-page economic observations.

## 5. Cross-Snapshot Retention

### Positive evidence

| Property | Actual assertion / source | Result |
|---|---|---|
| Completed LIVE observation survives repeated empty lists | New `:22,54,83`; actual post count and retained $4.50 reservation | PASS |
| Previously retained ID survives a failed targeted order lookup | `:83` injects ReadTimeout after full observation; full maximum retained and no POST | PASS |
| Same ID repeated across snapshots/windows is not multiplied | `:159`: reserve $2.45 once, budget $4.95, four valid POSTs over two windows with $7.40 funds | PASS |
| Reappearance associates with original identity | First economics retained; conflicting fresh economics block; fresh LIVE restores reservation in `:104` | PASS on reviewed paths |
| Owned UNKNOWN maximum remains | Original M2 `:258` plus duplicate-ID/lookup variants; no retry/next POST | PASS |

### M2-H01-P01 — Valid evidence is lost when pagination later fails

- Severity: **HIGH**.
- Scope: one remaining H01 evidence-retention finding with two paths.
- Actual MicroLive impact: permits already obligated collateral to fund a new order; cannot depend on venue rejection.
- Effective containment: the current failed read blocks or reserves temporarily, but a later empty response permits the unsafe POST.
- Blocker: **YES**. Decision: **FIX NOW**.

**Path A: incomplete open-order collection.** Independently reproduced by the separate reviewer and root:

1. Official balance $6; other-market LIVE BUY5 at $0.90 remains active throughout.
2. /data/orders page1 returns this valid order and next_cursor=partial.
3. Page2 times out.
4. First window correctly blocks, but observation cache is **empty**.
5. A subsequent window receives complete empty listings, while the actual external order is still LIVE.
6. Actual Gateway.place=1, POST=1 for $2.45; known account exposure at POST is **$6.95**. Own order later closes and result is SAFE_TO_CONTINUE.

Root observed:

```text
first=BLOCKED / open orders snapshot failed
cache_after_first=[]
retry Gateway.place=1, POST=1
combined_at_post=[6.95]
retry=SAFE_TO_CONTINUE
external=LIVE
network/DNS attempts=0; credentials denied
```

No concurrent writer, real cancellation, time change, fake Gateway or altered capital logic is involved. The failure is loss of valid evidence before whole-list completion, not a demand to trade using an incomplete snapshot.

**Retention verdict: FAIL overall.** Complete returned lists are retained; already received valid pages of a failed collection are not.

**Smallest repair:** retain authenticated valid economic observations as pages arrive in the SM transport/read adapter, or latch a conservative unresolved state after incomplete collection reads. Later empty snapshots must not erase that evidence/uncertainty. No shared Gateway change, DB or generic external-order manager is required.

## 6. Commitment Release

The completed-read release path checks matching ID/token/side/price/original quantity/market, CANCELED/CANCELLED, matched0, explicit empty associates, no relevant returned maker trade, a second official order read, durable proof record and unchanged admission evidence.

The new tests establish:

- `:104`: valid current zero-fill proof releases the budget; later LIVE restores reservation; a subsequent legitimate own BUY closes safely.
- `:130` (six variants): complete MATCHED/MINED/CONFIRMED evidence, late cumulative changes, missing associations and market contradiction do not release.
- `:174` (three sources): fully observed list/order/trade fill evidence remains sticky through later zero replicas.
- `:233,279`: delivered external LIVE/CONFIRMED during time/final SDK checks invalidates the release budget before intent/POST.
- Original M2 partial/cancel tests preserve confirmed owned fill cost; cancel ACK cannot prove terminality.

These distinguish missing order, verified terminal state, unfilled release and confirmed inventory cost. External orders are not cancelled or put into SM inventory.

**Path B of M2-H01-P01: incomplete trade collection loses positive fill evidence.** Independently reproduced with actual production reads:

1. Fully retain the external $4.50 LIVE observation.
2. Venue has a relevant MATCHED maker fill of 5 at $0.90. The cancelled order replica lags with matched0 and associates[].
3. Official trade page1 returns that valid relevant MATCHED fill; page2 times out.
4. The current snapshot reserves $4.50, but `_sm_fill_observed` remains **false** because processing awaits the whole SDK trade collection.
5. The next pre-submit snapshot gets lagging zero-fill cancelled order replies and a complete empty trade response.
6. The zero-fill verifier releases the reservation and the **same execute_window** submits $2.45. Relevant pending commitment $4.50 plus new $2.45 is $6.95 against $6 collateral.

Root observed:

```text
trade pages=[MA==,partial,MA==,MA==]
actual external matched quantity=5.0; trade status=MATCHED
cached fill_observed=false
Gateway.place=1; POST=1; combined_at_post=[6.95]
barrier=SAFE_TO_CONTINUE
network/DNS attempts=0; credentials denied
```

The initial read's immediate conservative reservation works, but its known positive fill evidence disappears on the subsequent read. A later empty reply cannot explain away a relevant fill already returned by an authenticated page.

**Commitment release verdict: FAIL overall.** This is premature release after discarded partial evidence, not a claim that every complete zero-fill proof is invalid. Both partial paths are the same finding and can be addressed by the same SM evidence-retention/fail-closed contract. Do not broaden to automatic recovery.

## 7. Balance Accounting

- Existing **$30 SM economic ledger: PASS** in selected assertions. Confirmed costs, unresolved owned maxima and UNKNOWN are counted by actual owned economic identities; partial cancel does not discard actual fills.
- Complete historical/fresh/local observations deduplicate correctly. Raw official balance/allowance is used rather than deducting from M1's already adjusted budget again.
- Sequential test `:198` permits only the first $2.45 against $7 less external $4.50, then blocks the second $2.40 even when both external/local rows disappear; first safely closes.
- Clearly sufficient funds remain usable: $7.40 with external $2.45 permits the $4.85 SM pair, including subsequent own windows.
- Raw balance already net of some venue reservation can cause additional conservative admission refusal. `:219` proves this ambiguity cannot be interpreted as a liability release. Exact live netting semantics remain NOT_VERIFIED; conservative under-utilization is not this review's failure reason.
- The partial-pagination diagnostics do **not** exceed the $30 session cap. They fail the separate official liquid-funds constraint by discarding observed external obligations.

**BALANCE ACCOUNTING: FAIL overall** because the two executed schedules authorize $6.95 account obligations against the specified $6 gross collateral condition. No unsupported claim of live account loss or a broken $30 ledger is made.

## 8. Regression

| Area | Actual independent assertions | Verdict |
|---|---|---|
| M2-H02 | Original deadline, six-page/5-second queries after DELETE, held required read, repeated caller cancellation, task drain, cutoff | **PASS** |
| M0-H01 | Owned terminal then contradictory LIVE and official open-list/terminal contradictions invalidate proof; next-window POST count unchanged | **CONTAINED** |
| M0-H02 | Owned late/status-only/cross-order confirmed facts require current official economics/set and deduplicate inventory | **CONTAINED** |
| M0-H03 | First/second UNKNOWN, duplicate identity and lookup failure preserve maxima and stop retries/next POST | **CONTAINED** |
| FL / M1 | 282 existing selected FL plus 64 M1/F01 independently pass; core unchanged | **PASS for selected scope** |

The new findings concern external capital observations from incomplete pagination. They do not reopen the historical M0 harness or claim full universal venue finality. No deadline task orphan or scheduling regression was found in the selected tests.

## 9. Independent Test Results

Environment: existing venv, Python 3.12.14, pytest/pytest-asyncio and official SDK. No installation. The unchanged runner clears environment before imports, denies workspace credentials/socket/DNS, uses temporary CWD, disables plugin autoload/cache and fails on any network attempt. -B avoids bytecode files.

Separate reviewer commands/results:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

**82 passed in 14.10s** = 20 new H01 + 14 preceding remediation + 48 original M2. Root repeat: **82 passed in 14.87s**.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

**346 passed, 1 deselected in 5.10s**. Root repeat: **346 passed, 1 deselected in 6.09s**.

Distinct pytest total: **428 executed / 428 passed / 0 failed / 0 skipped; 1 deselected**. Both independent/root runs report zero network/DNS attempts and denied workspace credentials. Duplicated runs are not added.

Additional diagnostics reproduce **two safety-violating schedules**. They are not passing pytest cases. Submitted tests cover completed observations/failed targeted reads, but not a valid first collection page followed by pagination failure. No tests were modified to obtain PASS.

Existing exclusion `tests/test_config_aliases.py:105::test_cli_conflict_stops_before_settlement_preflight`: **JUSTIFIED**, unchanged. CLI dotenv discovery hits protected workspace .env before the expected ConfigAliasError; no real credentials were enabled. Deselected is not skipped/passed.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm.py src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_commitment.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm.py src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_commitment.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

All exit0. Shared core matches baseline. Original reports/tests/code remain unchanged during review.

### Exact rerunnable offline diagnostic

Run from project directory. Actual SDK/Gateway execute under Fake HTTP with a public dummy key. All fixture artifacts are in temporary directories. The assertions deliberately confirm the unsafe result; exit0 means reproduced, **not safety PASS**.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import os,sys,socket,tempfile,runpy,asyncio,json
from pathlib import Path
from decimal import Decimal
from copy import deepcopy
root=Path.cwd()
keep={k:os.environ[k] for k in ('PATH','LANG') if k in os.environ}
os.environ.clear(); os.environ.update(keep); network=[]
def deny(*a,**kw):
    network.append(1); raise AssertionError('network forbidden')
def audit(event,args):
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        p=Path(os.fsdecode(args[0])).absolute()
        if p==root/'.env' or root/'.aws' in p.parents:
            raise AssertionError('credentials forbidden')
sys.addaudithook(audit)
for name in ('connect','connect_ex','sendto','sendmsg'):
    if hasattr(socket.socket,name): setattr(socket.socket,name,deny)
for name in ('create_connection','getaddrinfo','gethostbyname','gethostbyname_ex'):
    setattr(socket,name,deny)
sys.path[:0]=[str(root/'tests'),str(root/'src')]
import httpx,pytest
ns=runpy.run_path(str(root/'tests/test_rdp_sm_m2_execution.py'))
async def case(mode):
    with tempfile.TemporaryDirectory(prefix='m2-h01-review-') as tmp,pytest.MonkeyPatch.context() as mp:
        fixture=ns['env'].__wrapped__(mp,Path(tmp)); env=next(fixture)
        try:
            v,e=await ns['open_executor'](env)
            v.capital['balance']='6000000'
            external={'id':'external','asset_id':'3','market':'other',
                      'side':'BUY','price':'.9','original_size':'5',
                      'size_matched':'0','status':'LIVE','associate_trades':[]}
            v.rows['external']=external
            if mode=='trades':
                await e._snapshot()
                trade=v.fill('external','pending',5,'MATCHED')
                trade['market']='other'
                v.rows['external']['status']='CANCELED'
                v.open_rows_override=[]
            original=v.response; initial=True; pages=[]; calls=[]; combined=[]
            real_place=e.gateway.place
            async def counted(*a,**kw):
                calls.append(1); return await real_place(*a,**kw)
            mp.setattr(e.gateway,'place',counted)
            def response(request):
                nonlocal initial
                path=request.url.path
                if mode=='trades' and path=='/data/order/external':
                    row=deepcopy(v.rows['external'])
                    row.update(size_matched='0',associate_trades=[])
                    return httpx.Response(200,json=row,request=request)
                if path==('/data/orders' if mode=='orders' else '/data/trades'):
                    cursor=request.url.params.get('next_cursor'); pages.append(cursor)
                    if initial and cursor=='MA==':
                        return httpx.Response(200,json={
                            'data':[external if mode=='orders' else trade],
                            'next_cursor':'partial'},request=request)
                    if initial:
                        initial=False
                        raise httpx.ReadTimeout('offline page2 fails after valid evidence')
                    return httpx.Response(200,json={'data':[],'next_cursor':'LTE='},request=request)
                if request.method=='POST':
                    combined.append(float(e.committed()+Decimal('4.5')))
                return original(request)
            with httpx.Client(transport=httpx.MockTransport(response)) as client:
                mp.setattr('py_clob_client_v2.http_helpers.helpers._http_client',client)
                first=await e.execute_window('w1',[ns['quotes']()[0]])
                cache_after_first=list(e.gateway._client.observed_open_rows)
                final=first if mode=='trades' else await e.execute_window('w2',[ns['quotes']('w2')[0]])
            print(json.dumps({'mode':mode,'first':first.close_barrier,
                'first_reason':first.block_reason,'cache_after_first':cache_after_first,
                'pages':pages,'gateway_place_calls':len(calls),'posts':len(v.posts),
                'combined_at_post':combined,'final':final.close_barrier,
                'external_state':v.rows['external']['status'],
                'external_matched':v.rows['external']['size_matched'],
                'fill_observed':e.gateway._client.observed_open_rows.get('external',{}).get('_sm_fill_observed')}))
            assert len(calls)==len(v.posts)==1 and combined==[6.95]
            if mode=='orders':
                assert first.close_barrier=='BLOCKED' and not cache_after_first
            else:
                assert not e.gateway._client.observed_open_rows['external']['_sm_fill_observed']
        finally:
            try: next(fixture)
            except StopIteration: pass
async def main():
    await case('orders'); await case('trades')
asyncio.run(main()); assert not network
print('network/DNS attempts=0; workspace credentials denied')
PY
```

Real official interface finality/netting, Type2 authority and physical storage durability remain **NOT_VERIFIED**. No live smoke or credential use occurred.

## 10. MicroLive Impact

| Issue | Actual evidence | First $30 MicroLive impact / protection | Blocker | Decision |
|---|---|---|---|---|
| M2-H01-P01 partial order/fill evidence discarded — HIGH | Two independent actual POSTs at $6.95 total account obligations against $6; earlier valid authenticated page forgotten after later-page failure | No effective cross-read containment; first failure temporarily blocks/reserves, later omission permits unsafe admission | **YES** | **FIX NOW**: retain valid per-page evidence or latch unresolved pagination state in SM; regress both paths |
| Conservative over-reservation / external partial-fill release excluded | Full envelope retained for fully captured uncertain facts; adequate-funds positive tests still pass | Can reduce trading opportunities; no demonstrated unsafe relaxation required | NO | **DEFER** efficiency/automatic release |
| Selected Type2 account unsupported | Existing executable EOA-only gate and prior account disposition unchanged | Separate actual selected-account MicroLive blocker; refusal contains authority uncertainty | **YES before MicroLive**, outside fix review | Separate minimal evidence-backed task; no adaptation here |
| Actual interface/netting/storage and recovery framework | Offline evidence only; no new contradictory result on prior disposition | Authorized preparation still needed; no demand for full CapitalGate/restart framework | Not this targeted acceptance reason | **DEFER** existing prerequisites/exclusions |

Only the executed partial-evidence funding failure determines this fix's FAIL. The absence of Type2 support, live smoke or automatic recovery does not independently fail this targeted review.

## 11. Final Reviewer Decision

**FAIL**

- Original complete read1/read2 counterexamples: **PASS**.
- Cross-snapshot retention: **FAIL overall**, because valid partial order pages are not retained after later-page failure.
- Commitment release: **FAIL overall**, because a positive relevant partial trade page can be forgotten and a later zero replica releases its obligation.
- Balance accounting: **FAIL overall** for available collateral; $30 SM ledger and reviewed identity deduplication remain effective.
- M2-H02: **PASS**; M0-H01/H02/H03 remain **CONTAINED** in selected execution regressions.
- Selected FL regression and shared-core preservation: PASS; 428 submitted cases independently pass, but two supplemental safety diagnoses expose the missing coverage.

The minimum repair is one SM evidence-retention/fail-closed gap covering both paginated collections. No shared-core modification, Type2 development or M3 is required to fix it. This review does not authorize MicroLive.

## 12. Change Manifest

Only created:

- `docs/development/RDP_SM_M2_H01_INDEPENDENT_REVIEW.md`.

The separate reviewer edited no files. All 161 entry files hashed before review remain unchanged; only this report was added. No production/test/config edits, commits, staged changes, cleanup or overwritten historical evidence.

Final Git status:

```text
 M src/polymaker/cli.py
?? docs/development/
?? src/polymaker/rdp_sm.py
?? src/polymaker/rdp_sm_execution.py
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
?? tests/test_rdp_sm_m1.py
?? tests/test_rdp_sm_m1_f01_token_identity.py
?? tests/test_rdp_sm_m2_execution.py
?? tests/test_rdp_sm_m2_h01_commitment.py
?? tests/test_rdp_sm_m2_remediation.py
```

CLI and existing untracked files were present at entry. The directory listing includes this new report and preserved older reports. Staged diff empty.

## 13. Next Minimal Action

**Worker: preserve valid order/fill evidence across failed pagination, or conservatively block future admission until that incomplete risk is resolved; add the two reproduced HTTP-page failure regressions.**

---

### Repeat review addendum — 2026-10-12, Asia/Shanghai

The user repeated the same review request. The original thirteen-section audit above is preserved; this append-only addendum records new checks without replacing prior evidence. Entry report SHA-256: `736c742692fb51159ca3bab47ff1bdfa58629b1a99f74d37b163b011db4bc0ea`.

- Branch/HEAD remain `fl-restored-97ab0f9` / `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`. Git status and staged diff are unchanged.
- All 161 files from the prior review's protected entry manifest remain byte-for-byte identical. No new Worker fix was present. The existing Review Markdown accounts for the repeat entry's 162nd file.
- Separate reviewer `/root/m2_independent` independently recomputed both SM source hashes, the three M2 test hashes and Worker report hash, confirming unchanged artifacts. It reread and reran the exact saved diagnostic in section 9, reproducing both unsafe paths and concluding **FAIL** again. It made no changes or new independent full-suite run. Its previous independent full-suite results still apply to identical files; fresh full-suite runs below were performed by root and are not misrepresented as separate-agent executions.

Fresh root commands were identical to section 9:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

**82 passed in 14.85s.**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

**346 passed, 1 deselected in 6.85s.** Both ended with `0 network/DNS attempts; workspace credentials denied`. Distinct repeat cases: **428 executed / 428 passed / 0 failed / 0 skipped; 1 justified deselection**. Previous runs are not added to this count. Ruff, format, whitespace and unchanged shared-core checks also passed.

Root executed the unchanged diagnostic by extracting the saved bash block:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
from pathlib import Path
import subprocess
report=Path('docs/development/RDP_SM_M2_H01_INDEPENDENT_REVIEW.md').read_text()
section=report.split('### Exact rerunnable offline diagnostic',1)[1]
command=section.split('```bash\n',1)[1].split('\n```',1)[0]
raise SystemExit(subprocess.run(command,shell=True,executable='/bin/bash').returncode)
PY
```

Results repeated both earlier failures:

```text
orders: first BLOCKED; cache empty; subsequent Gateway.place=1 / POST=1;
        combined commitment=6.95 against 6; final SAFE; external remains LIVE
trades: external matched=5; fill_observed=false; Gateway.place=1 / POST=1;
        combined commitment=6.95 against 6; final SAFE
network/DNS attempts=0; workspace credentials denied
```

Diagnostic exit0 means those unsafe results were reproduced, not that the safety properties passed. The two diagnostic failures are separate from passing submitted pytest cases.

**Repeat verdict: FAIL.** Original complete-snapshot counterexample PASS; cross-snapshot retention, commitment release and available-balance accounting FAIL for incomplete pagination. M2-H02 PASS, M0-H01/H02/H03 CONTAINED and selected FL regression PASS remain unchanged. M2-H01-P01 remains HIGH / FIX NOW. Type2 remains unsupported, and MicroLive remains unauthorized; neither was implemented or tested live.

Repeat change manifest: **modified only this existing Review Markdown by appending this addendum**; no files created or removed, no other project file modified, no staging/commit. Final Git status remains the status in section 12.

Next minimal action remains the SM-only incomplete-pagination evidence retention/fail-closed fix, with both reproduced regressions. No M3 or Type2 work.
