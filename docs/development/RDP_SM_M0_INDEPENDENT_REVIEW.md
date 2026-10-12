# RDP-SM M0 — Independent Review

## 1. Review Metadata

- Date: 2026-10-11, Asia/Shanghai.
- Reviewer: fresh-context independent agent /root/m0_fresh_review, with separate read-only replication by /root and report assembly by /root. The fresh reviewer received no Worker conversation history and was instructed not to trust the previous PASS. The root previously authored the Worker artifacts; its checks alone are not represented as independent review.
- Branch: fl-restored-97ab0f9.
- HEAD: c9d52ad8efc5a49a7d36dd05958bf250cef802ce.
- Frozen SOP: RDP-SM SOP v1.8, as specified by the Architect/user; no trading rules were changed or reinterpreted.
- Scope: current M0 report, both untracked M0 Python files, current production Gateway/Tracker/StateStore/FillTruth and necessary parsers/configuration/FL finalization, actual Git changes and offline execution.
- Exclusions: historical RDP implementations/branches, fixes, production/test edits, M1, real credentials, HTTP/WS/RPC, external APIs and actual trading.
- Pre-review pwd: /home/ubuntu/poly-maker.
- Pre-review git diff and git diff --cached: empty, including full-content checks.
- Pre-review git status --short --untracked-files=all:

~~~text
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
~~~

All three untracked files were reviewed. No unrelated change or shared production modification was present. No applicable workspace/ancestor AGENTS.md was available. Review did not checkout/reset/clean/commit anything.

## 2. Worker Claims

| Worker claim | Independent verification | Result |
|---|---|---|
| FEASIBILITY_PASS | The checked-in scripts run, but three read-only counterexamples permit real Gateway next-window POSTs despite stale or unknown execution state | NOT ACCEPTED; Reviewer FAIL |
| 254 passed / 0 failed | Root independently ran the exact eight-file suite: 254 passed; no failures/errors/skips reported | CONFIRMED as pytest results, not a safety verdict |
| Six mandatory scenarios PASS | Actual assertions and six scripted traces pass. Their bounded event schedules omit three continuation failures | CONFIRMED for scripted traces only; full close-barrier coverage insufficient |
| Gateway/Tracker/FillTruth directly reused | Production classes/functions, real executor/rate limiter/SQLite and production parsers/reconciliation execute; SDK boundary alone is replaced for new M0 tests | CONFIRMED |
| Shared production changes NO | Branch/HEAD match; tracked/staged diffs empty; only three original M0 artifacts were untracked before review | CONFIRMED |
| Zero network/DNS | Executed runner and isolated diagnostics forbid socket/DNS access and report zero attempts; credentials are isolated before production imports | CONFIRMED for reviewed executions |
| UNKNOWN always Fail-Closed | Initial two-placement submit path latches UNKNOWN. Next-window helper bypasses that ledger/latch and permits a retry after UNKNOWN | FALSE as a composition-wide claim; finding M0-IR-03 |
| Automatic UNKNOWN recovery not implemented | No authoritative request correlation/recovery mechanism is implemented | CONFIRMED; retry in M0-IR-03 is unsafe continuation, not recovery |
| Prior Reviewer PASS | Worker report contains that historical conclusion. A fresh independent reviewer reproduced three defects and concluded FAIL | Prior PASS does not establish this review's result |

The report's statement that all seven close-contract clauses hold and next-window POSTs are prevented while unresolved exceeds the executed evidence (Worker report lines 101, 177 and 192).

## 3. Production Component Verification

Paths and lines refer to the unchanged current baseline or original Worker test file.

| Component | Actual Source | Test Coverage | Evidence | Verdict |
|---|---|---|---|---|
| ExecutionGateway placement | src/polymaker/execution/gateway.py:172, :207, :248, :263 | Production place, strict exceptions, force_post_only, V2 GTC args/options, canonical orderID extraction, dict/singleton-list ACK, rejection and ambiguous POST | Probe constructs ExecutionGateway with paper=False at tests/test_rdp_sm_m0_execution.py:151; only _client replaced at :153; no replacement _io/place/parser. S1–S3 execute it | Reuse CONFIRMED |
| Gateway cancel/read | gateway.py:305, :347, :595, :711, :733 | Actual cancel and get_order/get_trades/open_orders, exception wrapping and scoped cancel parameters | Cancel race holds actual executor call; body-level failure test; official snapshots parsed by real Gateway | Reuse CONFIRMED; cancel boolean is insufficient terminal evidence |
| Tracker | src/polymaker/state/tracker.py:171, :193, :269, :294, :328 | Ownership, optimistic MATCHED, partial remainder, status progression, late events, exact-key dedupe/quarantine | New tests use real UserEventProcessor at test :155; diagnostics use real on_trade/on_order | Reuse CONFIRMED; event changes do not automatically invalidate Probe's cached close evidence |
| StateStore | src/polymaker/state/store.py:78, :88, :135, :225 | Real memory SQLite registry, fill dedupe and economic positions/order map | Test :154; quantity/key/remainder assertions; baseline state tests | Reuse CONFIRMED; positions remain optimistic |
| FillTruth | src/polymaker/execution/filltruth.py:69, :78, :129, :169, :211 | Actual order/trade reads, maker economics, associate-trade/cumulative checksum, status recovery, unresolved failures | Probe.verify calls production reconcile_owned_order at test :209; D2 fresh reconciliation returns UNRESOLVED | Reuse CONFIRMED; cached COMPLETE can become stale |
| Parsers | src/polymaker/userstream/parse.py:171, :200 | Production normalization and distinct MATCHED/MINED/CONFIRMED/RETRYING | ws helper test :290; Gateway/FillTruth normalize official replies | Direct execution CONFIRMED |
| Unknown cleanup | src/polymaker/filltest.py:497 | Actual helper invokes scoped cancellation/read and always returns False | S3 calls _unknown_cleanup on real Gateway | Initial unknown refusal CONFIRMED |

Existing baseline FillTruth unit tests include FakeGateway stubs. They validate processor/reconciliation behavior but independently do not prove Gateway reuse. The 24 new M0 cases provide the combined real-Gateway evidence; the 230 older passes cannot substitute for them.

No defect here establishes that shared production interfaces must change. The demonstrated failures are in the test-only caller composition/proof.

## 4. Six Scenario Review

All test names below belong to tests/test_rdp_sm_m0_execution.py. PASS in this table refers to the actual scripted scenario, not the global close-barrier contract.

| Scenario | Test | Actual Assertions | Observed | Coverage Gaps | Verdict |
|---|---|---|---|---|---|
| 1. Both placements ACK | test_s1_both_acknowledged_and_confirmed_closure (:321) | pair asserts yes-1/no-1, two ownership records and both tracked; after cancel, ID remains tracked and real guarded next POST blocked; final canceled reads plus confirmed 2-share fills each, no open orders and predicate True | Passed; distinct identities and confirmed quantities established | No later order/fill event between successful verification and next POST; D1/D2 invalidate the global continuation claim | PASS, bounded trace |
| 2. First ACK / second reject | test_s2_first_ack_second_explicit_rejection (:339) | Fake SDK success=False yields NO_ACK rather than UNKNOWN; first ID remains known; guarded replacement blocked before closure; first's late 3 confirmed shares recovered through real REST path; predicate only becomes True afterward | Passed; no zero-fill assumption for first order | NO_ACK public return does not expose rejection vs local abort; fixture proves explicit rejection in this case. Post-close invalidation omitted | PASS, bounded trace |
| 3. First ACK / second UNKNOWN | test_s3_unknown_submission_stays_fail_closed_after_scoped_cancel (:358), 3 parameters | Timeout, success missing ID and None raise PlacementUnknown; ledger ACK/UNKNOWN and fail_closed=True; hidden SDK order unowned; cleanup False; empty open-orders/trades do not clear latch; pending remains; blocked POST; repeat submit raises; total posts stays 2 | All 3 passed | Covers initial submit path only. Next-window POST bypasses ledger/latch; D3 proves UNKNOWN does not always remain Fail-Closed | PASS for required initial failure; composition-wide UNKNOWN claim FAIL |
| 4. Partial fill / cancel race | test_s4_partial_fill_cancel_race_reconciles_and_deduplicates (:386) | Real MATCHED 2 changes remainder to 8/$1.60; second MATCHED 3 arrives while SDK cancel blocked in real executor, remainder 5/$1.00; ACK still blocks; official canceled rows plus REST recover missed 1; repeated REST/WS leave 3 keys and confirmed quantity 6 | Passed; actual asynchronous overlap and dedupe verified | New late MATCHED/CONFIRMED after final verification is omitted; D2 demonstrates stale checksum eligibility | PASS, bounded race |
| 5. MATCHED not CONFIRMED | test_s5_terminal_order_with_unconfirmed_trade_blocks_continuation (:431), 3 statuses; test_owned_status_before_matched_cannot_disappear_at_close (:574), 3 statuses | Canceled terminal set plus raw COMPLETE still pending; optimistic size=2, confirmed delta=0 for MATCHED/MINED/RETRYING; guard blocks; official CONFIRMED yields delta=2. Status-before-MATCHED keys remain unapplied/pending until REST recovery | All 6 relevant cases passed; statuses separated | Confirmation of a new fill observed after previous reconciliation clears pending without invalidating old COMPLETE; D2 | PASS for specified transitions; extended reconciliation coverage insufficient |
| 6. No fill / successful cancel | test_s6_no_fill_cancel_and_verified_final_states (:453); positive guard test (:540) | ACK plus LIVE/COMPLETE blocks; canceled snapshots release; no open/local orders; confirmed and optimistic inventory remain zero; positive test actually posts yes-2 through both guard checks and tracks/recloses it | Passed; actual final reads and guarded success path | Later LIVE update not checked against cached terminal set (D1); positive helper tracks ACK only after success and fails to account UNKNOWN (D3) | PASS at verified snapshot; durable continuation proof insufficient |

The six scripted scenario tests constitute 10 parametrized pytest cases. There are 14 further M0 cases, totaling 24; the combined suite adds 230 existing regression cases.

## 5. Close Barrier Audit

| Area | Independent finding | Verdict |
|---|---|---|
| Placement | Initial submit records PENDING before await, uses strict/post-only and registers ACKs. The next-window helper omits attempt registration and UNKNOWN latch | FAIL: M0-IR-03 |
| Cancellation | True reflects lack of SDK exception; body ignored. Tests correctly require subsequent official reads and keep commitment before verification | PASS for exercised paths |
| Order terminal state | verify reads after FillTruth and checks explicit terminal statuses. Cached terminal membership is not revoked by later real Tracker LIVE events | FAIL: M0-IR-01 |
| Trade confirmation | Confirmed projection requires CONFIRMED applied economic fills; all owned unapplied/unconfirmed/quarantined keys block. Existing dedupe assertions are effective | PASS for quantity/status separation |
| Reconciliation | Real FillTruth performs checksum/associate-trade checks. Probe caches COMPLETE and does not invalidate it when later fills change its underlying quantities | FAIL: M0-IR-02 |
| Unknown handling | First/second initial placement UNKNOWN correctly stays fail-closed even after empty snapshots. UNKNOWN during next-window submit does not latch and a second POST succeeds | FAIL: M0-IR-03 |
| Next-window eligibility | Actual Gateway guard runs on loop and worker thread. Both invocations accept the same stale predicate in D1/D2/D3 and authorize fake-SDK POSTs | FAIL |

ORDER_TERMINAL, TRADE_CONFIRMED and RECONCILIATION_COMPLETE are correctly separated conceptually and in initial assertions. Their conjunction must remain valid for the current evidence generation. A confirmed late fill does not make an earlier zero-size checksum current. A newer LIVE event makes terminality unresolved even if the prior REST read said canceled. These are reproducible same-process event-handling deficiencies, not assertions about whether a live exchange actually reactivates canceled orders.

SAFE_TO_CONTINUE requires current, accounted execution evidence for every placement. FAIL_CLOSED_WITH_UNRESOLVED_STATE merely prevents actions while evidence is missing; it is not successful closure. The initial S3 latch proves safe refusal, but D3 demonstrates that this behavior does not cover all submission entry points.

Probe creates UserEventProcessor without on_change/on_reconcile callbacks (test :155). Default callbacks do not update its terminal/outcome caches. This explains the stale evidence in D1/D2. No inspected code ties cached close evidence to a generation of order/trade observations.

## 6. Test Quality Audit

- Real production components: verified. New tests do not replace Gateway methods, Tracker, SQLite or FillTruth with fake equivalents.
- Fake boundaries: only external SDK signing/post/cancel/read responses are fabricated for M0. Synthetic venue rows are valid inputs, not simulated processor outcomes; real parsers and state transitions run.
- Assertions: substantial safety checks include no POST-count growth, exact ownership/key/quantity/remainder values, pending status and raw COMPLETE versus terminal/confirmed separation. They are not empty or call-only assertions.
- Mock limits: sdk.rows mutations establish synthetic official replies and do not certify venue status monotonicity, read consistency or confirmation authority. The test-only barrier is itself under review, not a production orchestrator.
- Async/race: cancellation is awaited in finally; threading.Event synchronizes actual worker cancellation with main-loop fill handling. No unawaited test-created cancellation task was found. Five-second waits are bounded safety timeouts, not assumptions about market latency.
- Independence: each Probe owns a fresh SDK, memory SQLite store, processor and executor. Fixture restores patches; network-attempt counters compare before/after each case. No order-dependent state sharing was found by inspection. Randomized/reversed execution order was not tested.
- Exception coverage: initial strict UNKNOWN, cancel timeout/body failure, official read failures, missing associated trade, FAILED fill and unknown status are covered. Next-window UNKNOWN was omitted; its omission is material.
- Evidence freshness: cached state is insufficiently challenged after close. The existing final-read regression changes status within verify, not after it; it does not catch D1.
- Inventory distinction: correct confirmed-only calculation. D2 is an invalidation/reconciliation error, not a double-count or MATCHED-as-confirmed error.
- Counts: 24 new M0 tests plus 230 existing tests. All 254 passing does not imply all event schedules are safe.

Review executed only read-only diagnostics, not new tests or fixes. Counterexample assertions deliberately confirm the unsafe outcomes; their process exit 0 indicates successful reproduction, not a passing safety property.

## 7. Independent Test Results

Working environment: Linux workspace /home/ubuntu/poly-maker; Python 3.12.14; pytest 9.1.1; pytest-asyncio 1.4.0; py-clob-client-v2 1.0.2. Existing local venv used; no package installation.

Root independently executed:

~~~bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m0_execution.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_gateway_positions_v2.py tests/test_favorite_first.py tests/test_favorite_first_review.py -q
~~~

~~~text
254 passed in 4.33s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
~~~

Counts: Executed 254; Passed 254; Failed 0; Skipped 0; Errors 0.

Fresh independent reviewer separately executed:

~~~bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m0_execution.py -q
# 24 passed in 0.37s

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m0_execution.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_gateway_positions_v2.py -q
# 210 passed in 3.84s
~~~

Both runs reported zero network/DNS attempts; no failures/errors/skips reported. These overlap the 254 cases; they are not additional distinct coverage.

~~~bash
.venv/bin/ruff check --no-cache tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m0_execution.py
# All checks passed!
~~~

Offline protections inspected before execution: runner clears inherited settings before pytest imports, retains only PATH/LANG, disables plugin autoload, explicitly loads pytest-asyncio, changes to empty temporary CWD, denies socket connect/connect_ex/sendto/sendmsg and DNS resolution, and denies workspace .env/.aws open paths. Runner records attempted networking even if Gateway catches the exception; new tests also check their own counter. New Probe supplies Secrets.model_construct, never constructs/authenticates a live SDK. Existing get_order/get_trades requests execute Fake SDK; existing positions tests use FakeHttp. Existing wallet-connect test fails on empty credentials before any live SDK creation. Python bytecode writing was disabled for review runs and pytest cacheprovider is disabled by the runner.

Three unsafe traces were independently reproduced both by the fresh reviewer and by root, using only in-memory SDK responses and actual production components. Root's exact combined stdin command, with no diagnostic file written:

~~~bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
import os
os.environ.clear()
import asyncio
import socket
import sys
import tempfile
from pathlib import Path
root = Path.cwd()
sys.path[:0] = [str(root), str(root / 'src')]
network_attempts = []
def deny(*args, **kwargs):
    network_attempts.append(True)
    raise AssertionError('review diagnostic forbids network')
for name in ('connect', 'connect_ex', 'sendto', 'sendmsg'):
    if hasattr(socket.socket, name): setattr(socket.socket, name, deny)
for name in ('create_connection', 'getaddrinfo', 'gethostbyname', 'gethostbyname_ex'):
    setattr(socket, name, deny)
def protect(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).absolute()
        if path == root / '.env' or root / '.aws' in path.parents:
            raise AssertionError('review diagnostic forbids credential reads')
sys.addaudithook(protect)
from tests.conftest import meta
from tests.test_rdp_sm_m0_execution import Probe, pair, trade, ws
from polymaker.userstream.parse import normalize_order
from polymaker.execution.gateway import PlacementUnknown
from polymaker.execution.filltruth import reconcile_owned_order, ReconciliationOutcome

async def closed_probe():
    p = Probe(meta.__wrapped__())
    await pair(p)
    await p.gw.cancel(sorted(p.known))
    for oid in sorted(p.known):
        p.sdk.rows[oid]['status'] = 'CANCELED'
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert p.safe_to_continue()
    return p

async def main():
    p = await closed_probe()
    try:
        p.sdk.rows['yes-1']['status'] = 'LIVE'
        p.proc.on_order(normalize_order(p.sdk.rows['yes-1']), p.meta.condition_id)
        assert 'yes-1' in p.store.orders and p.safe_to_continue()
        p.sdk.responses.append({'success': True, 'orderID': 'yes-2'})
        assert [o.order_id for o in await p.try_next_window()] == ['yes-2']
        assert len(p.sdk.posts) == 3
        print('D1 REPRODUCED: prior order tracked LIVE; barrier=True; next POST=yes-2')
    finally: p.close()

    p = await closed_probe()
    try:
        ws(p, trade(p, 'yes-1', 'late-new', 2, 'MATCHED'))
        assert not p.safe_to_continue()
        ws(p, trade(p, 'yes-1', 'late-new', 2, 'CONFIRMED'))
        assert p.safe_to_continue() and p.confirmed_qty(p.meta.yes.token_id) == 2
        assert p.sdk.rows['yes-1']['size_matched'] == '0'
        p.sdk.responses.append({'success': True, 'orderID': 'yes-2'})
        assert [o.order_id for o in await p.try_next_window()] == ['yes-2']
        assert await reconcile_owned_order('yes-1', p.gw, p.proc,
                    retry_delays=()) is ReconciliationOutcome.UNRESOLVED
        print('D2 REPRODUCED: late confirmed=2; venue matched=0; next POST=yes-2; fresh reconciliation=UNRESOLVED')
    finally: p.close()

    p = await closed_probe()
    try:
        p.sdk.responses.extend([TimeoutError('lost next POST response'),
                                {'success': True, 'orderID': 'yes-3'}])
        try: await p.try_next_window()
        except PlacementUnknown: pass
        else: raise AssertionError('expected UNKNOWN')
        assert p.attempts == ['ACK', 'ACK'] and not p.fail_closed and not p.proc._pending
        assert p.sdk.rows['hidden-no']['status'] == 'LIVE' and p.safe_to_continue()
        assert [o.order_id for o in await p.try_next_window()] == ['yes-3']
        assert len(p.sdk.posts) == 4
        print('D3 REPRODUCED: next POST UNKNOWN; no attempt/latch; hidden LIVE; retry POST=yes-3')
    finally: p.close()

with tempfile.TemporaryDirectory(prefix='m0-review-diagnostic-') as cwd:
    os.chdir(cwd)
    asyncio.run(main())
assert not network_attempts
print('3/3 unsafe counterexamples reproduced; network/DNS attempts=0; no files written')
PY
~~~

Selected actual output:

~~~text
D1 REPRODUCED: prior order tracked LIVE; barrier=True; next POST=yes-2
D2 REPRODUCED: late confirmed=2; venue matched=0; next POST=yes-2; fresh reconciliation=UNRESOLVED
D3 REPRODUCED: next POST UNKNOWN; no attempt/latch; hidden LIVE; retry POST=yes-3
3/3 unsafe counterexamples reproduced; network/DNS attempts=0; no files written
~~~

All POSTs in this report are real Gateway execution paths ending at the FakeSDK, not external orders. No credentials, market data, RPC, HTTP or WebSocket service was used.

## 8. Findings

### M0-IR-01 — Later LIVE order evidence does not invalidate terminal cache

- Severity: HIGH.
- Evidence: tests/test_rdp_sm_m0_execution.py:155, :205, :243; src/polymaker/state/tracker.py:328 and :358. D1 closes/reconciles both orders, then sends a valid later LIVE zero-fill event through the real processor. Tracker restores yes-1 to store.orders; no quantity gap exists, so no quarantine occurs. Probe retains both terminal IDs/COMPLETE outcomes, predicate returns True and real Gateway posts yes-2.
- Impact: next-window submission is authorized while the previous order has current observed nonterminal state. Even an out-of-order LIVE event requires reconciliation or fail-closed handling; ignoring the conflict is not proven safe.
- Required Action: return to Worker to invalidate terminal/close evidence on relevant later order observations and add an offline regression that asserts no next POST until fresh consistent terminal evidence resolves the conflict. No fix was implemented here.

### M0-IR-02 — Late CONFIRMED fill revives stale COMPLETE checksum

- Severity: HIGH.
- Evidence: test :224, :243; Tracker :269, :294; FillTruth :143 and :169. D2 starts from official canceled orders and reconciled quantity zero. A new owned MATCHED two-share event blocks; CONFIRMED for that key clears pending. Stored outcome remains COMPLETE although the last official size_matched is zero and tracked quantity is two. Predicate True permits yes-2. An immediate fresh production FillTruth reconciliation returns UNRESOLVED.
- Impact: TRADE_CONFIRMED is treated as sufficient to restore eligibility without re-establishing current RECONCILIATION_COMPLETE. Inventory count is accurate, but the claimed official checksum/close barrier is stale.
- Required Action: return to Worker to invalidate prior reconciliation evidence when a new relevant fill/economic observation changes its basis; require a fresh official reconciliation before continuation. Add a post-closure late MATCHED→CONFIRMED regression. No shared change or fix was implemented.

### M0-IR-03 — Next-window UNKNOWN is unaccounted and permits blind retry

- Severity: HIGH.
- Evidence: test submit :163 versus try_next_window :260; positive continuation test :540. D3 scripts a lost next-window POST response after verified closure. Production Gateway raises PlacementUnknown and FakeSDK retains a hidden LIVE order. The helper does not record PENDING/UNKNOWN or latch fail_closed: attempts still ACK/ACK, pending empty, safe=True. A second invocation posts yes-3, total POST count four.
- Impact: all-attempt accounting and UNKNOWN-always-Fail-Closed claims are false for the continuation path used as positive evidence. Strict Gateway semantics alone do not enforce caller-level UNKNOWN handling.
- Required Action: return to Worker to make the next-window feasibility submission follow the same before-await attempt accounting, capture and strict UNKNOWN refusal as initial placements, and add no-retry/no-next-window assertions for its lost-response case. This is an M0 proof correction, not authorization to implement M1.

Counts: CRITICAL 0; HIGH 3; MEDIUM 0; LOW 0. Worker report must be revised after corrections and independent rerun. Its current global PASS and blanket guard/UNKNOWN assertions cannot be accepted unchanged.

## 9. Evidence Limitations

| Classification | Evidence |
|---|---|
| PROVEN | Correct required branch/HEAD; no tracked/staged/shared production changes; only allowed original artifacts plus this review report |
| PROVEN | 24 M0 cases and 230 existing cases pass independently offline; actual Gateway/Tracker/StateStore/FillTruth run |
| PROVEN | Initial UNKNOWN refusal, explicit reject distinction, cancel ACK limitation, partial-fill/cancel overlap, fill dedupe and confirmed-only inventory |
| PROVEN | D1/D2/D3 permit next fake-SDK POSTs through real Gateway with stale or unknown state; fresh reviewer and root both reproduce them |
| INFERRED | Existing components may support a corrected thin caller using event-driven invalidation/accounting; callback hooks and record access exist, but the corrected proof has not been implemented or tested |
| NOT_VERIFIED | A repaired close predicate; production SM runtime; crash/restart/coroutine-cancel recovery; live signing/authentication/venue finality or read completeness; nonzero opening inventory; full-size MATCHED terminal branch |
| NOT_VERIFIED | Random-order test execution and exhaustive concurrent interleavings; existing passing schedules do not establish these |

No failure in this review proves that the FL baseline itself is unsafe or that its rules need changing. The findings concern the proposed M0 safety composition and overstated feasibility evidence.

## 10. Final Reviewer Decision

FAIL

The six scripted scenarios pass and production reuse is confirmed, but three executable same-process counterexamples violate current close-barrier evidence freshness and complete placement accounting. All demonstrated failures occur with current real production components and an external FakeSDK; no historical branch or external service is required.

The Worker result FEASIBILITY_PASS and the previous reviewer PASS are not accepted as a global execution-feasibility conclusion. Return the test-only composition/proof and Worker report for correction, then independently rerun M0. Do not begin M1.

## 11. Architect Attention

No new Architect decision is required to address these M0 findings. They are defects in the caller proof/accounting, and no evidence currently requires shared production changes. Keep M1 gated on a corrected M0 and subsequent independent review. Any proposed shared change remains subject to explicit human approval; none was made or requested here.

## 12. Change Manifest

Created by this review only:

- docs/development/RDP_SM_M0_INDEPENDENT_REVIEW.md

No other project file was modified by review. Worker report/tests were preserved, including their untracked status. No production, existing tests, configuration, frozen SOP or Git history changes; no fixes; no M1.

Preserved original artifact SHA-256 values, checked before and after review:

~~~text
07150ef03996de612b93cf0f23a9d38e919fc8e000b5a0f5d6ca96829391194f  docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
c820947c5bf634eadf215fb7e7d75af5e47a47222cd842b209ae4e8dbf3caa9d  tests/rdp_sm_m0_offline.py
1b43018383aa3a1ea4b3968bb1c74b1e5d000cf1255ea8abca59915fbae91750  tests/test_rdp_sm_m0_execution.py
~~~

Final git status --short --untracked-files=all:

~~~text
?? docs/development/RDP_SM_M0_EXECUTION_FEASIBILITY.md
?? docs/development/RDP_SM_M0_INDEPENDENT_REVIEW.md
?? tests/rdp_sm_m0_offline.py
?? tests/test_rdp_sm_m0_execution.py
~~~

Final tracked and staged diffs remain empty. Branch/HEAD remain fl-restored-97ab0f9 / c9d52ad8efc5a49a7d36dd05958bf250cef802ce.
