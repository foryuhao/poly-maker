# RDP-SM M2-H01 — Pagination Fix Independent Review

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- RDP-SM SOP v1.8 FROZEN; FL-first / Thin SM Orchestrator; MicroLive First. MicroLive authorization: **NO**.
- Entry status: preserved prior 22-line M1 CLI addition and untracked SM modules, tests and development reports; staged diff empty. Untracked files were included in review. No checkout/reset/cleanup or historical RDP branch inspection.
- Independent reviewer: separate existing agent `/root/m2_independent`, who did not implement the pagination fix. It independently inspected current source/tests, executed the selected suites and reproduced the original schedules under the new code, concluding **PASS**. Root implemented the preceding fix, repeated checks and assembled this report; root self-check is not the sole independent review provenance.
- Captured SHA256 hashes for 164 existing files under src/tests/docs/config/livecfg at review entry. Final comparison confirms no existing file changed and only this Review Markdown was created. No real credentials were inspected or used.

## 2. Review Scope

Read `RDP_SM_M2_H01_COMMITMENT_FIX.md`, `RDP_SM_M2_H01_INDEPENDENT_REVIEW.md`, `RDP_SM_M2_H01_PAGINATION_FIX.md`; inspected the actual SM pagination adapter, commitment/release/close paths, new pagination tests, necessary existing M2/M1/FL tests and actual FL Gateway/installed SDK interfaces.

Only assess failed-page evidence retention, persistent account uncertainty, subsequent empty listings, safe release and direct regressions. No complete M2 architecture re-audit, Type2 development, general CapitalGate, recovery framework, new database, M3, live interfaces, RPC or fixes during review. Fail-Closed after an API exception is an acceptable result; automatic recovery is not required.

Worker claims are hypotheses checked against actual code, independent executions and assertions:

| Claim | Independently verified evidence | Verdict |
|---|---|---|
| Open Orders pagination safe | Per-page retention plus original schedule: no actual placement/POST before or after later empty reads | CONFIRMED |
| Trades pagination safe | Relevant partial leg retained; failure cannot authorize zero-fill release; no placement or premature inventory | CONFIRMED |
| Cross-snapshot / later empty safe | Independent successful empty Gateway queries followed by close and new-window attempts stay blocked | CONFIRMED |
| Capital admission safe for these failures | $6 / $4.50 / $2.45 examples make zero placements; complete sufficient-funds paths still work | CONFIRMED for scoped schedules |
| 438 tests passed | Separate reviewer: 92 + 346 passed, one justified deselection; root repeat agrees | CONFIRMED |
| Shared core unchanged | Baseline diff quiet for Gateway/Tracker/FillTruth/Journal/config/Engine/FL strategy; entry hash comparison | CONFIRMED |

## 3. Open Orders Pagination Verification

Actual production call chain:

```text
SMWindowExecutor.execute_window -> _snapshot -> M1 _account / Gateway.open_orders
  -> ExecutionClobClient.get_open_orders
  -> official SDK get_open_orders -> ExecutionClobClient._get(/data/orders)
  -> inherited read-only official transport validation -> _observe_order_page
  -> SDK requests the next page
```

Source evidence: `src/polymaker/rdp_sm_execution.py:148-160,203-231`; existing Gateway `src/polymaker/execution/gateway.py:595-604`; installed SDK `.venv/lib/python3.12/site-packages/py_clob_client_v2/client.py:534-559`.

The SDK normally returns only the complete accumulated list. The new SM observation occurs before returning each valid page to that SDK loop. First economics are retained by actual exchange ID with `setdefault`; positive cumulative/associate evidence is sticky. On collection failure, the exception path latches `OPEN_ORDERS_PAGINATION_INCOMPLETE` and re-raises. Neither the completed-list field nor a subsequent successful empty return overwrites the persistent observation map or clears the failure.

The separate reviewer adapted the prior report's exact offline P01 diagnostic, using its real executor/SDK/Gateway and Fake HTTP:

```text
Balance = $6.00
External LIVE BUY5 @ $0.90 = $4.50
Order page1 valid; page2 timeout
observed_open_rows includes external after failure
initial window = BLOCKED
successful subsequent Gateway.open_orders() = []
verify_close() = false
next window = BLOCKED
Gateway.place calls = 0; SDK POSTs = 0
external actual venue order remains LIVE
```

Submitted test `tests/test_rdp_sm_m2_h01_pagination.py:38` also asserts retained price/quantity, zero delegated Gateway calls/POST/cancel, initial/next BLOCKED and failure reason. Its three variants cover timeout, cursor cycle and missing cursor. After a successful empty read, even a later cancelled zero-fill row cannot clear the failed-history restriction. These are actual execution assertions, not merely checking an internal bool.

**OPEN ORDERS: PASS.** The former $6.95 exposure admission does not occur.

## 4. Trades Pagination Verification

Actual path:

```text
_external_no_fill_terminal / production FillTruth
  -> production Gateway.get_trades
  -> ExecutionClobClient.get_trades
  -> official SDK get_trades -> _get(/data/trades)
  -> shared parse_trade -> retain relevant page legs before requesting next page
```

Source evidence: `rdp_sm_execution.py:166-200`; Gateway `execution/gateway.py:733-750`; installed SDK `client.py:577-607`.

Relevant maker legs are retained by `(trade_id, order_id)` when the ID is an observed external order or known owned SM order. Positive external fill evidence is latched immediately. The SDK worker only observes risk dictionaries; it does not write StateStore, fabricate Tracker fills or apply inventory. Actual Tracker/FillTruth remain responsible for owned confirmed economics.

Separate reviewer diagnostic result:

```text
Previously retained external order; actual MATCHED quantity = 5
Cancelled order replica reports matched0 / associates[]
Valid trade page1; page2 timeout
retained fill marker = true; relevant partial leg retained
initial window = BLOCKED
successful subsequent Gateway.get_trades(...) = []
verify_close() = false; next window = BLOCKED
Gateway.place calls = 0; SDK POSTs = 0
SM inventory unchanged
```

`test_p02_trade_page_retains_risk_without_inventory_or_automatic_release` (`:82`) executes MATCHED, MINED and CONFIRMED variants. It checks the actual retained leg size/price/state, sticky failure, later empty reads, both window outcomes and zero Gateway/POST calls. External legs never enter SM inventory, including external CONFIRMED.

`test_incomplete_owned_trade_read_cannot_release_seen_fill_or_repeat_order` (`:210`) verifies the owned variant: one actual BUY, fill during cancel, valid MATCHED prefix followed by failure, lagging matched0 terminal replica and later empty trades. The maximum $2.45 remains; only one placement/POST occurs; later windows and close stay blocked. Further cancellations are limited to that owned identity.

**TRADES: PASS.** Full-return-only `last_trades` still exists as completed-set evidence, but it is no longer the sole record of already observed relevant legs or account uncertainty.

## 5. Cross-Snapshot Evidence Retention

The following actual enforcement points refer to the same persistent failure restriction:

| Boundary | Source | Effect |
|---|---|---|
| Failed collection | `rdp_sm_execution.py:193-210` | Preserve first failure reason; re-raise instead of pretending an empty complete list |
| Next-window eligibility | `:468-478` | `safe_to_continue` requires no failed account-pagination history |
| Account snapshot | `:510-515,669-671` | Reject before reading and after awaited reads, including failures caught conservatively by an external proof helper |
| Final pre-intent check | `:1079-1081` | No intent/new Gateway.place using failed history |
| Actual signed POST permission | `:233-241` | Last transport defense before sending an order |
| External release | `:683-695` | Failed history prevents no-fill release |
| Close barrier | `:906-958` | Necessary cleanup/reconciliation allowed, but no SAFE_TO_CONTINUE from failed history |

No successful query clears `pagination_failure`; no automatic reset/recovery API is provided. Evidence maps retain first economics, and historical rows remain part of the existing identity-deduplicated budget calculation on normal complete-read paths. A missing listing entry does not delete the row.

Prior complete-snapshot retention/UNKNOWN tests also pass. A permanently blocked executor is deliberate containment, not proof that every outstanding exposure is closed.

**CROSS-SNAPSHOT RETENTION: PASS.**

## 6. Later Empty Snapshot

Both submitted P01/P02 tests and the independent extended diagnostics perform an actual successful empty Gateway read after failure. They then call close verification and try another real window. The failure reason remains, close verification returns false, new placement counts remain zero and no result becomes SAFE_TO_CONTINUE.

The restriction operates before capital admission and again at intent/POST/close; it is not dependent on interpreting later empty responses as a new risk set. Current list absence, official terminal evidence and released commitment are distinct concepts in code.

**LATER EMPTY SNAPSHOT: PASS.** No automatic release or unsafe order was demonstrated.

## 7. Commitment Release

Normal complete-read behavior remains usable:

| Property | Actual tests/assertions | Result |
|---|---|---|
| Complete multi-page orders / sufficient capital | New `:128`: raw $7.40, external $2.45; two actual Maker POSTs and safe close; external remains LIVE | PASS |
| Reliable zero-fill terminal/trade evidence | New `:156`: complete trade pages and matching cancelled matched0/associates[] order reads; one legitimate POST, zero inventory/cost after own close | PASS |
| Duplicate trade observations | New `:180`: two complete repeated page sets leave one retained economic key, $4.50 reservation/$1.50 spendable, no POST/inventory | PASS |
| Duplicate order observations | Existing H01 `test_duplicate_observations_and_own_proofs_do_not_double_count`: same ID charged once through reads/windows; four legitimate POSTs at sufficient funds | PASS |
| Partial fill / confirmed cost | Original M2 `test_e04_partial_fill_cancel_race_and_duplicate_confirmations`: owned confirmed quantity3, two trade identities, committed $1.47 retained through duplicate events | PASS |
| Cancel ACK not terminal | Original M2 `test_e04_cancel_ack_does_not_prove_terminal_or_release_capital`: LIVE after ACK blocks, retains $4.85 and adds no next-window POST | PASS |
| Pending evidence cannot release | New owned-prefix case plus original MATCHED/MINED and existing H01 unresolved-fill variants | PASS |
| $30 hard limit | Original cap/exact-$30/confirmed-cost-next-window tests: no excess POST, owned confirmed cost remains charged | PASS |

Actual release helper (`rdp_sm_execution.py:683-735`) requires matching identity/economics/market, cancelled state, matched0, explicit empty associates, a full trade query with no relevant leg, a second matching official order read and a journaled proof. Any prior positive fill/event evidence prevents its narrow zero-fill release. Failed pagination now prevents release even if later replies are empty or apparently zero-fill terminal.

Owned proof acceptance (`:764-821`) also requires current official reconciliation to include all previously observed page-leg economics. MATCHED can advance to CONFIRMED; it cannot silently disappear. Remaining confirmed costs and maximum unresolved placement exposure use the existing SM ledger.

Account budget merges historical/fresh orders and local unresolved attempts by identity (`:579-655`). It subtracts reservations from raw official balance/allowance, not from an already reduced M1 budget. If live balance already nets some reservation, this may conservatively under-admit; it does not authorize known-risk release. Exact live netting remains NOT_VERIFIED and is not this review's failure condition.

**CAPITAL SAFETY / COMMITMENT RELEASE: PASS for the scoped code and tested schedules.** This is not a claim of universal live exchange completeness.

## 8. Independent Test Results

Separate reviewer exact commands:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

**92 passed in 14.30s**, 0 failed/skipped: 10 pagination + 20 preceding H01 + 14 preceding remediation + 48 original M2 cases. Root repeat: **92 passed in 15.23s**.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

**346 passed, 1 deselected in 5.88s**: 64 M1/F01 + 282 selected existing FL. Root repeat: **346 passed, 1 deselected in 6.65s**.

Distinct total: **438 executed / 438 passed / 0 failed / 0 skipped; 1 deselected**. Repeat runs are not added. Independent adapted stdin diagnostics of the prior P01/P02 schedules additionally passed safety assertions, including actual successful empty reads, close attempts and another window; no project test files were created/changed for them.

Both reviewers' suites and the separate diagnostic reported:

```text
0 network/DNS attempts; workspace credentials denied
```

Environment: existing Python 3.12.14 venv. Before production imports the unchanged runner clears inherited credentials, denies socket/DNS and workspace credential reads, uses a temporary CWD, disables plugin autoload/cache and fails on any network attempt. `-B` disables bytecode writes. Existing fixtures use public dummy-key signing and local Fake HTTP, denying forbidden RPC/trading operations. Real SM/Gateway/SDK/Tracker/FillTruth/Journal execute; the placement spy delegates to the actual Gateway and never fabricates an outcome.

Existing exclusion `tests/test_config_aliases.py::test_cli_conflict_stops_before_settlement_preflight`: **JUSTIFIED**, unchanged. Source expects ConfigAliasError, while CLI dotenv discovery reaches protected workspace credentials first under this offline guard. Protection was not relaxed and real credentials were never enabled. Deselected is not skipped/passed.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_pagination.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_pagination.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

Independent reviewer and root: all exit0, Ruff passes / files formatted. The existing tracked CLI addition is confined to the prior independent read-only command and was not modified here. Shared execution/state/storage/FL behavior source matches baseline.

## 9. M2-H02 / M0 Regression

| Protection | Actual assertion evidence | Verdict |
|---|---|---|
| M2-H02 | `test_h02_optional_slow_reads_never_precede_deadline_cancellation`: actual CANCEL at mono3 precedes 5-second/six-page trades. Held second-leg read test uses real monotonic/event loop, DELETE by T0+3..3.4 while read is blocked; worker remains owned, jobs drain; LIVE terminal failure blocks. Cutoff tests have zero POST. | **PASS** |
| M0-H01 | `test_e07_h01_terminal_then_live_blocks_even_old_official_terminal` and official LIVE/list variants: real processor invalidates terminal proof, restores exposure, close/new window blocked and no additional POST | **CONTAINED** |
| M0-H02 | `test_e06_h02_late_confirmed_invalidates_complete_and_requires_official_set` plus status-only/cross-order cases: late fills invalidate old proof, inventory deduplicates, old empty REST cannot explain them; no new POST until current official set agrees | **CONTAINED** |
| M0-H03 | First/second UNKNOWN, duplicate-ID and lookup-failure cases retain maxima, log unresolved risk and prevent retry/new-window POST; known-order cleanup remains available | **CONTAINED** |
| M1/F01 / FL | Selected 346 tests pass, shared core source unchanged | **PASS for selected scope** |

No original M0 harness, historical audit report, timer architecture, Type2 account behavior or shared component was changed during review. These regression results preserve the containment dispositions; they do not claim automatic recovery or universal venue finality.

## 10. MicroLive Impact Assessment

No new reproducible targeted safety finding: **NONE**.

| Issue / limitation | Evidence / actual MicroLive impact | Blocker | Decision |
|---|---|---|---|
| P01/P02 observed exposure lost after failed pagination | Independent original schedules now retain evidence, remain blocked and make zero placements before/after empty reads | NO after this scoped fix | FIX NOW — verified implemented |
| Same executor stops permanently after incomplete account pagination | Explicit failure restriction with no reset; avoids uncertainty being used to spend funds | NO code-safety blocker; manual handling instead of automatic recovery is acceptable | DEFER automatic recovery; no additional implementation required |
| Current Type2 account unsupported by SM | Previously established account limitation; EOA-only execution restrictions remain. Current authority not newly verified here | YES for MicroLive, unchanged; not a pagination-review failure | Separate previously required minimal account task; no adaptation this round |
| Real venue completeness/netting and physical storage | Offline evidence only; existing restrictions remain | Authorized preparation prerequisite, not a new scoped code failure | DEFER to authorized preparation; no live requests |

Do not weaken the failure restriction to improve availability. This PASS neither supports Type2 nor grants MicroLive readiness/authorization.

## 11. Final Reviewer Decision

**PASS**

The separate reviewer independently reproduced both original failed-pagination schedules under current production code and found zero unsafe placements, retained order/trade evidence and persistent blocking through later empty replies. Complete safe-query/release paths work; pending/confirmed capital semantics, $30 tests, M2-H02 and M0 containments regressions pass; selected FL/M1 behavior and shared core remain unchanged. All selected tests independently reproduce offline.

No remaining concrete false release, unsafe order or false SAFE_TO_CONTINUE was demonstrated in this targeted scope. There is no requirement to add automatic recovery or extend this review to other architecture work.

## 12. Change Manifest

Only review-created file:

`docs/development/RDP_SM_M2_H01_PAGINATION_INDEPENDENT_REVIEW.md`

No production source, tests, configuration, SOP or historical report modified. No files removed. Entry/final SHA256 comparison for 164 existing files confirms only this new report was added. Shared core changes: **NO**.

Final Git status (prior uncommitted artifacts preserved; no staged change):

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
?? tests/test_rdp_sm_m2_h01_pagination.py
?? tests/test_rdp_sm_m2_remediation.py
```

## 13. Next Minimal Action

**Architect Acceptance** of this targeted pagination fix.
