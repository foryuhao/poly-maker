# RDP-SM M2 — Targeted Independent Review

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- RDP-SM SOP v1.8 FROZEN; FL-first / Thin SM Orchestrator; MicroLive First. MicroLive authorization: **NO**.
- Entry Git status: the preserved 22-line M1 CLI addition and untracked M0/M1/F01/M2 code, tests and audit reports. Staged diff empty. Relevant untracked files were included in review. No conflicting baseline, checkout/reset/cleanup, historical RDP branch access or SOP modification.
- Independent reviewer: separate agent `/root/m2_independent`, author of the original M2 adverse schedules, who did not implement this remediation. It independently inspected the current source, ran the selected tests and additional diagnostics, and concluded **FAIL**. Root previously implemented remediation; root assembled this report and separately repeated checks/reproductions. Root self-check is not the sole independent review provenance.
- All 158 entry files under `src`, `tests`, `docs`, `config` and `livecfg` were hashed before review, excluding bytecode. Final comparison confirms only this new Review Markdown was added. No real credentials were read or used in this review.

## 2. Scope

Read the original M2 Worker report, original independent FAIL report, targeted remediation report, actual executor/tests and directly relevant FL Gateway, Tracker, FillTruth, Journal, account adapter/config and installed SDK implementation.

Only verify M2-H01 available collateral admission, M2-H02 cancellation initiation, existing M0-H01/H02/H03 regressions and Type 2 reuse feasibility/authority evidence. No original M0 re-audit, account implementation, production/test fixes, shared signing change, M3, RPC, external API access or live trading. Historical reports remain intact.

The production path actually executes:

```text
SMWindowExecutor.open -> unchanged M1 read composition
 -> actual ExecutionGateway + installed SDK ExecutionClobClient
 -> actual Tracker/StateStore + RiskJournal(Journal)
execute_window -> snapshots / admission / durable intent
 -> Gateway.place(single quote, strict_placement=True, force_post_only=True)
 -> managed deadline cancellation -> Gateway.cancel(known IDs)
 -> actual FillTruth + fresh order/trade proof -> confirmed projection / barrier
```

Fake objects supply HTTP/time/event/filesystem boundaries, not Gateway, Tracker or FillTruth implementations. The selected executor is production-callable; no alternate feasibility harness was substituted.

## 3. M2-H01 Verification

### Original cases and valid improvements

| Case | Actual assertion / independently observed result | Assessment |
|---|---|---|
| Balance or allowance $3; first $2.45 ACK absent from listing; second $2.40 | `test_rdp_sm_m2_remediation.py:44`: exactly 1 POST, second stops before submission, first remains owned and closes with positive terminal evidence | Original counterexample fixed |
| Balance $6; latest pre-submit list exposes $4.50 external BUY | `:64`, visibility on reads3/4: exactly 0 POSTs, OFFICIAL_CAPITAL_INSUFFICIENT | Original newer-list counterexample fixed |
| Same ID in two lists and local ACK | `:87`: $7.40 covers external $2.45 plus own $4.85; 2 POSTs and SAFE; external order not cancelled | No same-ID duplicate reservation in this schedule |
| Pending partial fill / conflicting list economics | `:98,116`: unresolved maximum retained and no next POST; contradictory economics block with 0 POSTs | Useful fail-closed checks |
| UNKNOWN / cancel ACK / confirmed costs / $30 cap | Original M2 tests `:258,301,364,375` assert actual POST counts and retained capital, including later-window confirmed costs | Existing SM economic ledger and tested uncertainty barriers preserved |

Code merges two raw official lists by ID (`rdp_sm_execution.py:477,509-549`), then merges local unresolved placement maxima (`:550-557`). It builds spendable funds from raw balance/allowance minus those reservations (`:558-569`). This avoids subtracting the already adjusted M1 budget again. If live balance already excludes a reservation, the conservative policy can under-admit; real venue reservation semantics remain unverified. That uncertainty is not the cause of the failing reproduction below, which uses the same gross-balance semantics as the original accepted Reviewer counterexample.

### Remaining HIGH defect: an observed external liability disappears between snapshots

**Finding: M2-H01 remains OPEN in a narrower read-lag schedule. Severity: HIGH. Blocker: YES. Decision: FIX NOW.**

The reservation and identity dictionaries are newly initialized inside each `_snapshot` at `:509-510`. `execute_window` first obtains a snapshot at `:868`, then obtains another immediately before placement at `:887`. Only the latter budget is used at `:906-908`. An external BUY observed in the first invocation is not retained anywhere in the SM liability state when the next invocation's two listings omit it. Local SM attempts are retained; already observed external orders are not.

Minimal reproduction, independently run by the separate reviewer and repeated by root:

1. Official balance = $6; allowance sufficient.
2. Other-market BUY5 at $0.90 remains LIVE in the fake venue throughout. Its $4.50 liability is visible only on order-list read1 **or** read2 of the initial window snapshot.
3. Reads3/4 in the pre-POST snapshot omit that order because the replica lags. No order is filled/cancelled/modified externally; no concurrent external writer is needed.
4. Request one target-market Maker BUY5 at $0.49 ($2.45).
5. The executor sends 1 actual SDK POST despite the already observed available funds being $6-$4.50 = $1.50. Account commitments at POST total **$6.95**. It later cancels only its own order, correctly verifies that own closure, and reports SAFE_TO_CONTINUE. The external order remains LIVE.

Root's exact observed output:

```text
visible_only_read=1, order_reads=[1,2,3,4], balance=6,
external_commitment=4.5, new_commitment=2.45,
combined_at_post=[6.95], posts=1, cancel_ids=[["o-1"]],
external_final_state=LIVE, result=SAFE_TO_CONTINUE, reason=null

visible_only_read=2: same unsafe POST and final result
network/DNS attempts=0; workspace credentials denied
```

The new test at `test_rdp_sm_m2_remediation.py:63` parameterizes visibility only on **reads3/4**. Its assertions are valid, but do not test loss of a prior observation across `_snapshot` calls. The Worker conclusion that known observed liability omission is completely fixed exceeds this coverage.

**Impact:** permits occupied collateral to be reused and relies on the venue to reject an unfunded order. A $30 SM ledger does not cure this separate available-funds failure. The resulting SAFE describes actual closure of its owned order; it does not retroactively make the earlier unsafe admission acceptable. No false own-order terminal proof is alleged.

**Smallest required fix:** SM-only retention of already observed unresolved external BUY envelopes across snapshots, deduplicated by ID/economics. Listing absence must not release a known liability. The smallest conservative containment can retain that envelope for the SM session rather than develop automatic external-order recovery; release requires positive applicable evidence. Add read1/read2 visibility regressions and ensure later windows cannot erase the observation. Do not modify Gateway, Tracker, FillTruth or build a CapitalGate framework.

### Rerunnable read-only reproduction

Run from the project directory. It denies credentials/network before imports, uses only the existing dummy-key HTTP fixture, and writes only temporary fixture artifacts:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import os, sys, socket, tempfile, runpy, asyncio, json
from pathlib import Path
from decimal import Decimal
root = Path.cwd()
retained = {k: os.environ[k] for k in ('PATH','LANG') if k in os.environ}
os.environ.clear(); os.environ.update(retained)
network = []
def deny(*a, **kw):
    network.append(1)
    raise AssertionError('offline network forbidden')
def audit(event,args):
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        p=Path(os.fsdecode(args[0])).absolute()
        if p==root/'.env' or root/'.aws' in p.parents:
            raise AssertionError('workspace credentials forbidden')
sys.addaudithook(audit)
for name in ('connect','connect_ex','sendto','sendmsg'):
    if hasattr(socket.socket,name): setattr(socket.socket,name,deny)
for name in ('create_connection','getaddrinfo','gethostbyname','gethostbyname_ex'):
    setattr(socket,name,deny)
sys.path[:0]=[str(root/'tests'),str(root/'src')]
import pytest,httpx
ns=runpy.run_path(str(root/'tests/test_rdp_sm_m2_execution.py'))
async def case(visible_read):
    with tempfile.TemporaryDirectory(prefix='m2-targeted-review-') as tmp, pytest.MonkeyPatch.context() as mp:
        fixture=ns['env'].__wrapped__(mp,Path(tmp)); env=next(fixture)
        try:
            v,e=await ns['open_executor'](env)
            v.capital['balance']='6000000'
            external={'id':'external','asset_id':'3','market':'other','side':'BUY','price':'.9','original_size':'5','size_matched':'0','status':'LIVE','associate_trades':[]}
            v.rows['external']=external
            original=v.response; reads=[]; at_post=[]
            def response(request):
                if request.url.path=='/data/orders':
                    reads.append(len(reads)+1)
                    v.open_rows_override=[external] if len(reads)==visible_read else []
                if request.method=='POST':
                    at_post.append(float(e.committed()+Decimal('4.5')))
                return original(request)
            with httpx.Client(transport=httpx.MockTransport(response)) as client:
                mp.setattr('py_clob_client_v2.http_helpers.helpers._http_client',client)
                r=await e.execute_window('w1',[ns['quotes']()[0]])
            print(json.dumps({'visible_only_read':visible_read,'order_reads':reads,'balance':6,'external_commitment':4.5,'new_commitment':2.45,'combined_at_post':at_post,'posts':len(v.posts),'cancel_ids':v.cancels,'external_final_state':v.rows['external']['status'],'result':r.close_barrier,'reason':r.block_reason}))
            assert len(v.posts)==1 and at_post==[6.95] and v.rows['external']['status']=='LIVE'
        finally:
            try: next(fixture)
            except StopIteration: pass
async def main():
    await case(1); await case(2)
asyncio.run(main())
assert not network
print('network/DNS attempts=0; workspace credentials denied')
PY
```

The diagnostic exits 0 because it asserts reproduction of the defect. This is **not** a passing safety test. Two adverse schedules violate the required admission property; they are additional diagnostics, not counted as two passing pytest cases.

**M2-H01 / overall CAPITAL SAFETY: FAIL.** The originally tested two paths improve and the $30 ledger remains effective, but observed external liabilities can still be lost.

## 4. M2-H02 Verification

**Verdict: PASS for the targeted cancellation-initiation defect.**

The original six-page query schedule now initiates DELETE at **mono3/T+223**, before trade pages at mono3/7/11/15/19/23. Actual final reconciliation may extend to T+247; this is allowed because mandatory cancellation already began. This result was independently reproduced by the separate reviewer, not inferred from an internal deadline flag.

| Safety property | Actual source/test evidence | Observed |
|---|---|---|
| T0 is request start, monotonic, not ACK | `rdp_sm_execution.py:923-927`; original `test_first_post_delay_does_not_reset_deadline_or_submit_second` | T0=0, deadline3, delayed ACK causes no second POST |
| Optional active polling cannot postpone closure | `:1033-1042` removes REST polling from the waiting loop; `test_rdp_sm_m2_remediation.py:136`, pages1/6 | DELETE at original mono3 before trade GETs; zero active trade reads before cancel |
| Necessary second-leg SDK read cannot postpone known-ID cancel | `:989-991` uses remaining original deadline; `:724` uses real Gateway cancel; test `:192` holds worker read | Actual DELETE at T0+3..3.4 before GET release; executor still awaits that GET; exactly 1 POST |
| No BUY at/after cutoff or insufficient closing headroom | `:897-900,930-944`; new `:248`, original `:388,642` | Zero POSTs at T+222/T+240; delayed first ACK permits only cleanup |
| Cancel ACK is not terminal | `_verify_order :614`, `_verify_close :753`; held-read LIVE variant and original `:301` | BLOCKED, full unresolved commitment retained, next-window POST count unchanged |
| Late confirmation requires new official evidence | Original `:326` | Confirmed quantities counted once; stale proof cannot permit next POST |

### Task ownership and ordering

- `_await_owned` (`:702-722`) shields the same snapshot/verification task and drains it after caller cancellation. It does not claim that cancelling an await kills the SDK thread.
- `_cancel_known` (`:724-751`) owns one cancellation job per known-ID set, shared by foreground and timer. It has no trade/terminal inference from ACK.
- The timer can issue cancellation on Gateway's existing eight-worker pool while a required sequential SDK read is waiting. The held-read and repeated-caller-cancellation variants verify no premature result, no second POST, and completed cancellation jobs.
- `execute_window` finally (`:1055-1067`) cancels the timer handle and drains any fired timer task before result/teardown. The awaited read's production reconciliation may finish during/after cancellation, but cannot outlive the returned window or authorize a post-deadline BUY in the tested path.
- Normal mandatory closeout awaits `_cancel_known`, then reads order/trade facts through actual FillTruth and a fresh final order. Terminal, confirmation and completeness remain distinct. The old read already in flight before expiry may overlap cancellation; its owner is retained and final verification follows cleanup.

No unmanaged timed-out query task or cancellation-as-terminal proof was found in the reviewed schedules. This is not a promise of hard real-time OS/storage scheduling or venue cancellation completion within three seconds. An in-flight placement lacking an ID cannot be selectively cancelled until known; UNKNOWN remains blocked and durably accounted for. A necessary indefinitely blocked SDK read can prolong the blocked cycle, but no longer has exclusive control of known-ID cancellation initiation.

## 5. M0 Findings Regression

| Finding | Actual production regression / assertions | Result |
|---|---|---|
| M0-H01 terminal followed by LIVE | Original M2 `test_e07_h01_terminal_then_live_blocks_even_old_official_terminal` (`:353`), official LIVE/open-list contradictions (`:531,746`); old proof invalidated, fresh query attempted, real next-window POST count unchanged | **CONTAINED** |
| M0-H02 late CONFIRMED / old COMPLETE | `test_e06_h02_late_confirmed_invalidates_complete_and_requires_official_set` (`:326`) and status-only/cross-order variants; exactly-once inventory, fresh official economic set required, no extra POST while unresolved | **CONTAINED** |
| M0-H03 UNKNOWN / retry | `test_e03_h03_unknown_retains_commitment_no_retry_or_next_window` (`:258`, three cases), lookup/duplicate-ID variants; unknown $2.45/$2.40 maxima retained, known first cleanup, no retry/new-window POST | **CONTAINED** |

Neither the remaining external-collateral defect nor the corrected deadline path reopens the historical M0 harness review. These are current SM containments, not automatic resolution or shared-cache fixes.

## 6. Type 2 Account Compatibility

**TYPE2_REUSE_FEASIBILITY: YES, conditional on accepted current authority evidence. ACCOUNT_AUTHORITY: NOT_VERIFIED. Current SM compatibility: UNSUPPORTED.**

- User confirms actual FL configuration is `config`; independent source inspection finds `config/config.toml:17` mode2. Rabby login is not used as proof.
- `Gateway.connect` (`execution/gateway.py:134-155`) supplies existing official SDK key, signature type and funder separately. No signing-core change is needed to expose the SDK's Type 2 path.
- Installed SDK `order_builder/builder.py:63-66,191-198` constructs maker=funder, Type 2 signer=key-derived EOA, and configured signatureType. Type2 is the installed SDK's POLY_GNOSIS_SAFE mode; Type3 uses a different signer rule and is not part of this proposal.
- Existing M1 `_Reads.connect_account` (`rdp_sm.py:174-213`) already constructs signer/funder separately and derives existing API credentials. `_ExecutionReads` (`rdp_sm_execution.py:234-251`) preserves those configured SDK values. Do not switch to Gateway.connect's create-or-derive startup or settlement/RPC paths merely to enable Type 2.
- M2 startup (`:364-367`) and each submission snapshot (`:479-482`) explicitly require mode0 and signer=funder. These executable SM restrictions are the immediate compatibility gap. The signed POST guard (`:205-213`) already compares maker to builder funder and signer to SDK EOA separately.
- `Gateway.positions` (`:653-671`) queries configured funder. SDK balance/allowance (`client.py:687-696`) uses L2 authentication plus signature type, without explicit funder; open orders (`:534-558`) use the L2 account. Thus positions and trade-funds reads must be shown to refer to the same actual intended funded account, not merely successful independent reads.

Only existing historical log metadata was independently counted: 8 non-paper connection records, 11 Type2 balance HTTP200 records, 8 order POST HTTP200 records and 6 fills in `logs/favorite-first-live.jsonl`. No raw records/credentials were printed. These support historical FL capability, not current authority. The Worker's local masked signer/funder result is retained as prior evidence; this review does not rederive it using the forbidden real key.

### Minimal required adaptation, not implemented

1. Replace only SM's mode0/signer=funder admission predicates with an account-specific proof check that supports the existing EOA path and an explicitly verified Type2 signer/funder pair. Retain independent SDK signer, funder, configured mode and official account-scope checks on startup/every submission.
2. Continue using FL SDK Type2 signing and existing Gateway reads. Validate signed maker, signer, configured signatureType and exact economics. No Safe signing implementation, wallet client, new RPC, general Proxy framework or shared Gateway mutation.
3. Add real-SDK/Fake-HTTP Type2 regressions for correct maker/signer/mode, funder positions, authenticated collateral/order scope and incorrect/unproven identity producing zero POSTs.

### Minimum evidence required before enabling that pair

Provide a current, trustworthy binding of the exact configured EOA signer to the exact funded Safe and its permitted signing authority, together with the relevant official authenticated collateral/open-order account binding. If Safe owner/threshold facts form the authority proof, they must establish that the signer can authorize the intended path; a constructed SDK builder, a historical successful order, a wallet login or a local signature alone does not establish current Safe control.

Existing `settlement_sweep.py:270-277` reads deployed Safe owners/threshold via RPC. It was only inspected, **not executed**, and is not an approved SM evidence path. This review does not prescribe introducing RPC. The Architect must identify/accept a current authoritative evidence source within the permitted official/offline scope; absent such evidence, keep Type2 blocked. No new external interface is approved by this report.

The selected account's compatibility is an actual MicroLive blocker, but a separately scoped next task may address it. Lack of Type2 implementation is not the reason for this review's FAIL; the remaining M2-H01 counterexample is.

## 7. Test Results

Environment: existing repository venv, Python 3.12.14, pytest/pytest-asyncio and installed official SDK. No installation. The unchanged offline runner clears credentials before imports, denies workspace `.env`/credential access and socket/DNS, uses temporary CWD, disables plugin autoload/cache, and fails on any network attempt. `-B` avoids bytecode. Fixtures replace external HTTP/time/event ordering/filesystem errors and deny forbidden Gateway/RPC/runtime paths.

Separate reviewer commands/results:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_execution.py tests/test_rdp_sm_m2_remediation.py -q --tb=short
```

**62 passed in 13.43s** = 48 original M2 + 14 targeted cases (7 capital, 7 timing). Root repeated the same files in reverse file order: **62 passed in 13.18s**.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

**346 passed, 1 deselected in 4.88s** = 64 M1/F01 + 282 selected FL regressions. Root repeat: **346 passed, 1 deselected in 5.88s**.

Distinct pytest total: **408 executed / 408 passed / 0 failed / 0 skipped; 1 deselected**. Duplicate runs are not added. Both reviewer/root runs report:

```text
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

Additionally, read-only diagnostics reproduce **two safety-violating M2-H01 schedules** (section 3). Thus 408 passing submitted cases does not imply successful acceptance. Original fixed schedules and the six-page deadline case were also independently repeated.

Existing exclusion `tests/test_config_aliases.py:105::test_cli_conflict_stops_before_settlement_preflight`: **JUSTIFIED**, unchanged. CLI Config.load dotenv discovery hits the protected workspace `.env` before the expected ConfigAliasError; no real credentials were enabled to run it. It is deselected, not passed or skipped. Source/hashes and existing disposition are unchanged.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_remediation.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_remediation.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

All exit0. Real authenticated interfaces, current Safe authority, balance reservation semantics and physical storage guarantees remain **NOT_VERIFIED**. No live smoke was performed or required for the targeted offline decision.

## 8. FL Regression

**PASS for selected regression scope:** 282 existing FL cases and 64 M1/F01 cases pass independently. Existing Gateway/Tracker/FillTruth/Journal/Config/Engine/FL behavior sources match HEAD; only the earlier additive M1 CLI command is tracked as modified. Its existing command bodies are unchanged. No code/test/config file changed during review.

No new external service, trading-capable CLI, settlement path, RPC initialization or changes to FL signing semantics were introduced by remediation. Root/agent checks confirm Fake HTTP only at the external boundary and real execution components underneath. This is selected regression evidence, not a claim of exhaustive repository/live validation.

## 9. MicroLive Impact

| Issue | Evidence | Actual MicroLive impact / containment | Blocker | Decision |
|---|---|---|---|---|
| M2-H01 observations discarded across snapshots — HIGH | $6 collateral, observed LIVE $4.50 external BUY, replica omission, actual new $2.45 POST | Reuses occupied collateral; no effective local admission containment for this schedule; venue rejection cannot be the safety mechanism | **YES** | **FIX NOW**: retain observed unresolved BUY envelopes in SM; read1/2 regressions |
| M2-H02 prior active-read delay | Actual DELETE at mono3 before slow pagination and held required read release; managed task drain verified | Targeted defect corrected; final venue/terminal reconciliation may legitimately overrun | NO | **FIX NOW completed**, accept targeted fix |
| Selected Type2 account unsupported / authority unverified | Config2; executable EOA-only guard; FL SDK supports separated identities | Actual intended account cannot currently execute SM; current refusal effectively contains identity uncertainty | **YES for MicroLive**, separate task | **FIX NOW before live** through minimum evidence-backed SM adaptation; not general Proxy development |
| Real interface/collateral/storage verification | Offline evidence only | Authorized preparation prerequisite; no new contrary evidence changes previous disposition | Not targeted code acceptance blocker | **DEFER** to approved preparation |
| Automatic recovery / general Proxy / full storage framework | Existing no-restart/history refusal; first-market scope | Existing conservative boundary retained; no demonstrated need for framework expansion here | NO | **DEFER** existing disposition |

No unrelated findings or new architecture development are requested. M0 containments remain accepted. HIGH severity alone did not drive FAIL; the executed collateral-overcommitment schedule does.

## 10. Final Reviewer Decision

**FAIL**

- M2-H01: **FAIL / partially repaired**. Original cases no longer over-submit, but a previously observed LIVE external obligation can still disappear across snapshots and cause an unsafe actual POST.
- M2-H02: **PASS**. Deadline initiation is independent of discretionary and tested slow mandatory reads, with owned tasks drained and current terminal/confirmation requirements retained.
- M0-H01/H02/H03: **CONTAINED / CONTAINED / CONTAINED**.
- $30 SM ledger: tested PASS; overall available-capital safety: FAIL.
- FL selected regression and shared-core preservation: PASS. Submitted 408 tests independently pass, with a valid existing deselection; supplementary adverse diagnostics expose the remaining defect.
- Type2 thin-adapter reuse feasibility: YES conditional on accepted evidence; current authority NOT_VERIFIED; current account unsupported. No adaptation performed.

This review is not MicroLive authorization or live interface verification. The smallest mandatory repair is the remaining SM liability-retention gap; there is no need to reopen M0, rework shared execution, or start M3.

## 11. Change Manifest

Only created:

- `docs/development/RDP_SM_M2_TARGETED_INDEPENDENT_REVIEW.md`.

The separate reviewer modified **no files**. All 158 entry project files hashed for comparison remain byte-for-byte unchanged; only this report is new. Temporary fake-account journals/state/test fixtures were outside the project workspace. No secrets were inspected, source/test fixes made, staged changes created, commits made or existing evidence overwritten.

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
?? tests/test_rdp_sm_m2_remediation.py
```

The directory status includes this new report and preserved previous reports. CLI and untracked source/tests are pre-existing, not review edits. Staged diff empty.

## 12. Next Minimal Action

**Return M2-H01 to Worker for a targeted SM-only fix retaining observed external BUY commitments across snapshots, with read1/read2 lag regressions.**
