# RDP-SM M3-H01 — Preflight Risk Evidence Handoff Fix

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- SOP: RDP-SM v1.8 FROZEN; FL-first / Thin SM Orchestrator; MicroLive First.
- M1/M2 accepted; M2-H01/H02 closed; M0-H01/H02/H03 contained per Architect disposition. M3 Independent Reviewer FAIL, M3-H01 HIGH / FIX NOW; other principal M3 functions passed review.
- Read the existing M3 Worker/Independent Review and M2 pagination-independent-review reports and actual M1/M2/M3 code. Preserve those reports unchanged.
- Entry: pre-existing tracked CLI addition; untracked SM modules/tests/development reports. Staged diff empty. Original baseline remains correct; working-tree delivery files are included.
- Captured SHA256 hashes for 171 pre-existing files under src/tests/docs/config/livecfg. No checkout/reset/cleanup or historical RDP branch inspection.
- MicroLive authorization **NO**. No real credentials, external HTTP/WS/RPC, real orders/cancels, M4 or independent Reviewer launched during this Worker task.

## 2. Reviewer Original Counterexample

The original independent review reproduced a real production-path loss of financial evidence:

```text
Official account balance: $6.00
Initial M1 preflight: external LIVE BUY5 @ .90 = $4.50
Subsequent listing: external order omitted
M3 NORMAL YES BUY5 @ .49 = $2.45
Old behavior: Gateway.place called, account potential commitment $6.95
Second order: total potential commitment $9.345
Old M3 run: COMPLETED
```

The new test returns that external order only on the first official order-list response. All subsequent listings are empty; targeted official order reads still say LIVE. It runs actual M1/M3/M2 with Type2 dummy-key SDK signing configured, not Fake M2/Gateway.

Observed after this fix, both with initial T+60 and with T+0 startup/waiting: **zero Gateway.place calls, zero SDK POSTs, zero cancels**. Execution snapshots reserve $4.50 and report $1.50 spendable; the requested $2.45 is denied. The attempted window is BLOCKED and the run is BLOCKED with `OFFICIAL_CAPITAL_INSUFFICIENT`, never COMPLETED.

## 3. Actual Root Cause

M1 `_account` reads account-wide open orders and capital (`src/polymaker/rdp_sm.py:338,367`), producing a basic preflight result. That structured result reports aggregate commitment, not the full historical order/trade identity set. READY is a limited current check, not authorization for arbitrary quote amounts.

The SM SDK adapter already retains each returned order page by exchange order ID, sticky fill observations and relevant trade legs (`src/polymaker/rdp_sm_execution.py:148,203,214`). However, each fresh `_ExecutionReads.connect_account` initially constructs empty observation maps (`:297`). Previously:

1. M3 created a new reader for every pre-T+15 preflight iteration and discarded its predecessor's observations.
2. M3 passed no evidence into `SMWindowExecutor.open`.
3. M2 retained only observations from its own later startup preflight in its execution client.

An omitted listing therefore erased risk at the **reader/session handoff**, even though M2 correctly retains risk once present in its own observation maps. The later M3 COMPLETED result followed an admission made without the earlier commitment. This fix changes handoff, not M2 capital mathematics.

## 4. M1 → M3 → M2 Data Flow

Actual updated path:

```text
M3 initial _ExecutionReads -> real M1 run_preflight
  -> per-page exchange-ID order observations / sticky fill flags
  -> relevant trade-leg observations / persistent pagination failure if any
  -> M1 closes HTTP pool; observations remain available
  -> next M3 waiting reader imports the preceding reader's evidence
     after verifying source and destination signer/funder/signature mode
  -> real SMWindowExecutor.open(risk_reads = last M3 reader)
     -> fresh M1 preflight seeded with all prior observations
     -> fresh execution reader seeded with that accumulated evidence
     -> production M2 _snapshot / existing reservation and release logic
     -> production Gateway / Tracker / FillTruth / Journal / Close Barrier
```

Only observation maps/failure state cross the handoff. READY, a cached balance, SM order ownership, confirmed inventory, terminal proofs or previous reconciliation permits are not imported. M2 continues to obtain fresh metadata, books, official capital/account facts and placement-time checks.

## 5. Minimal Code Change

| File / reference | Change |
|---|---|
| `src/polymaker/rdp_sm_execution.py:280,306` | Optional prior risk reader. Reuse `_execution_identity` on both readers and require matching SDK signer, funder and signature type before copying existing order maps, trade-leg maps and pagination failure. Release the predecessor reference after copying. |
| `src/polymaker/rdp_sm_execution.py:464` | Optional `risk_reads` input to the actual M2 factory. Carry M3 evidence into M2's fresh preflight and then its execution reader, replacing the narrower order-only transfer. |
| `src/polymaker/rdp_sm_execution.py:487` | Add initial observed order/trade IDs and account-incomplete reason to the existing durable session journal record for provenance. |
| `src/polymaker/rdp_sm_loop.py:240,273` | Preserve observations across all waiting iterations and explicitly pass the last reader into real M2. |
| `src/polymaker/rdp_sm_loop.py:260` | At cutoff before any executor was opened, retained/unverified observations cannot take the old unconditional no-execution COMPLETED shortcut. Return BLOCKED with `PREFLIGHT_RISK_UNRESOLVED_AT_CUTOFF`. |

No new capital ledger, CapitalGate, database, external interface or background task. No changes to NORMAL/REBALANCE formulas, M2 scheduling/cancel deadlines, shared FL signing/execution/state or M1 CLI behavior. All preceding reader operations are awaited; M1 still closes its HTTP pool normally.

## 6. Preflight Risk Evidence Retention

The identity-bound handoff copies the **existing** SDK evidence by order ID and `(trade_id, order_id)`, including sticky `_sm_fill_observed` flags and the persistent incomplete-pagination reason. It neither creates SM ownership for external orders nor credits external/pending evidence as confirmed inventory.

Actual M1 currently reads positions/orders/collateral; it does not independently scan all trades. The fix does not claim a new complete M1 trade discovery capability. It preserves the order-associated fill evidence M1 actually observes and any relevant trade observations/failure already present in the SM reader. Actual trade reconciliation remains M2's responsibility.

Tests cover an initial partially matched external order followed by a misleading zero-fill cancelled replica: the sticky flag survives the handoff, full $4.50 remains reserved and no placement or confirmed SM inventory occurs. Incomplete M1 order pagination retains the first page, returns BLOCKED, opens no executor and makes no placement. Account-identity mutation during waiting also blocks before any window.

## 7. Cross-Snapshot Handling

M3 waiting may involve many fresh M1 reads. Every new reader begins with its predecessor's retained evidence; later empty listings cannot replace it. The T+0→T+15 and T+0→T+60 clock schedules both retain the external order through every reader, including the M2 factory's preflight and execution clients.

Inside M2 the existing identity-based reservation merge is unchanged (`src/polymaker/rdp_sm_execution.py:632`). Historical observations, fresh first/second listings and local unresolved attempts are merged by order ID. Fresh absence is not terminal proof. Additional M2 observations augment the initial M1 evidence; they do not replace it.

Unknown official status retains the commitment. Incomplete pagination retains observations and its blocking latch; no automatic reset/recovery was added. Retention is for this single running composition, not a general cross-process account-history recovery service.

## 8. Capital Safety

All quotes still pass real M2 capital checks. The frozen $30 SM commitment limit, official balance/allowance checks, local ACK/UNKNOWN envelopes and verified release logic are unchanged.

| Tested evidence | Actual M2 result |
|---|---|
| M1 external $4.50; later omitted; balance $6 | Reserved $4.50, spendable $1.50; no $2.45 placement |
| Same external repeated in M1/M2, $2.45; balance $7.40 | Count once; initial spendable $4.95 |
| First SM BUY ACK for $2.45 plus external $2.45 | Reserved $4.90; second $2.395 quote fits $2.50 remaining; two legitimate POSTs |
| Initial external $4.50 plus additional M2 external $.50; balance $6 | Both retained; reserved $5, spendable $1; zero placements |
| Previous external LIVE, freshly verified cancelled with zero fills and complete trade evidence | Existing M2 proof releases that reservation; balance $6 supports both legitimate quotes |

The release test uses actual targeted official-order reads, complete official trade-list reads, a final matching cancelled-order read and existing durable `sm_external_no_fill_terminal` records (`src/polymaker/rdp_sm_execution.py:733`). A cancel request, omission, read timeout or lagging zero-fill replica alone does not release risk. No external order is cancelled by SM.

`WindowResult.capital_committed` is the existing **SM-owned** commitment ledger, not the external-account reservation total. In the blocked original case it remains zero because no SM order was submitted; the $4.50 external envelope is proved separately by actual `_snapshot.execution_reserved_usd` and retained exchange-ID rows. No claim that external exposure is SM inventory/cost.

## 9. Run Status Correctness

The actual unsafe quote now produces a BLOCKED window and `execution_status=BLOCKED`, preserving `OFFICIAL_CAPITAL_INSUFFICIENT`. It cannot reach another quote window or COMPLETED. M2 may subsequently verify the empty set of SM-owned orders and report its final own-order cleanup barrier SAFE_TO_CONTINUE; that does **not** clear external risk or override M3's BLOCKED capital stop.

If waiting jumps to T+240 before M2 startup, retained observations have no verified release proof. The added conservative cutoff guard returns BLOCKED/BLOCKED rather than COMPLETED. Clean no-risk cutoff behavior remains covered by the unchanged M3 tests. A previously cancelled but unverified external observation at this early-return branch can conservatively block; improving its availability is not necessary for this safety fix.

## 10. Regression Tests

New file: `tests/test_rdp_sm_m3_h01_handoff.py`, **11 cases**. Fake responses/time only; actual M1, M3, M2 factory/executor, Gateway, SDK, Tracker, FillTruth and Journal run. Delegating instrumentation captures real Gateway calls, reader maps and real M2 account snapshots; it supplies no synthetic execution outcome.

| Requirement | Actual test / assertions | Result |
|---|---|---|
| T01 original reviewer example | `test_original_preflight_risk_survives_waiting_and_missing_listings` (T+60): zero Gateway calls/POSTs, reserve4.50/budget1.50, BLOCKED quote/run | PASS |
| T02 risk across waiting | Same test (T+0→15 and T+0→60), Type2: every constructed reader retains exchange ID; no unsafe order | PASS |
| T03 incomplete preflight | `test_incomplete_preflight_retains_page_and_never_opens_executor`: first-page row and failure retained, zero windows/executors/POSTs | PASS |
| T04 verified release | `test_verified_no_fill_release_uses_existing_m2_proof`: fresh official proof, reservation0, two real Maker POSTs, safe close | PASS |
| T05 duplicate evidence | `test_duplicate_evidence_and_sequential_sm_commitment_count_once`: external reserve2.45, later4.90 with first ACK, two accepted orders | PASS |
| T06 updated exposure | `test_m2_additional_commitment_does_not_replace_m1_evidence`: reserve5, budget1, both IDs retained, zero POSTs | PASS |
| T07 run status | Original schedules BLOCKED; `test_waiting_until_cutoff_does_not_report_completed_with_retained_risk` BLOCKED with exact cutoff risk reason | PASS |
| Pending/unknown/account mismatch | Three focused real-path tests: sticky fill risk, failed targeted order read and dummy-key mutation remain blocked | PASS |
| T08 trading loop | Existing full Type2 M1/M2/M3 market test: NORMAL→REBALANCE→NORMAL, cumulative confirmed5/5, 69 windows, 3 ActiveCycles, last POST T+219, stop T+240 | PASS |
| T09 M2-H01/H02 | All92 execution/remediation/commitment/pagination cases, including original capital examples and deadline cancellation ordering | PASS |
| T09 M0-H01 | `test_e07_h01_terminal_then_live_blocks_even_old_official_terminal` and official LIVE contradiction cases | CONTAINED |
| T09 M0-H02 | `test_e06_h02_late_confirmed_invalidates_complete_and_requires_official_set` and interleaved trade-change cases | CONTAINED |
| T09 M0-H03 | `test_e03_h03_unknown_retains_commitment_no_retry_or_next_window` variants | CONTAINED |
| T09 Type2/FL | Seven existing Type2 cases and selected M1/FL regressions | PASS |

Exact commands from `/home/ubuntu/poly-maker`:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m3_h01_handoff.py -q --tb=short
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m3_loop.py -q --tb=short
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py src/polymaker/rdp_sm_loop.py tests/test_rdp_sm_m3_h01_handoff.py
.venv/bin/ruff format --no-cache --check src/polymaker/rdp_sm_execution.py src/polymaker/rdp_sm_loop.py tests/test_rdp_sm_m3_h01_handoff.py
```

Actual final output:

```text
Targeted: 11 passed in 1.61s
M3: 22 passed in 95.71s
Type2/M2: 99 passed in 18.40s
M1/FL: 346 passed, 1 deselected in 8.16s
Every suite: M0 offline guard: 0 network/DNS attempts; workspace credentials denied
Ruff: All checks passed; 3 files already formatted
```

Distinct final total **478 executed / 478 passed / 0 failed / 0 skipped; 1 deselected**. Initial new-test run had two incorrect instrumentation assertions (querying closed SQLite after runner cleanup; expecting a second order to appear in an open listing before immediate cancellation). Corrected to retained actual ownership IDs/observations and actual durable proof records; initial failed run is not added to the final distinct-case total.

The unchanged exclusion `test_cli_conflict_stops_before_settlement_preflight` remains justified: guarded CLI dotenv discovery encounters protected workspace credentials before the expected ConfigAliasError. No credentials enabled, guard relaxed or test changed. Deselected is not passed/skipped.

Environment: repository venv, Python3.12.14. Existing runner clears inherited trading environment before imports, uses a temporary cwd, denies workspace `.env`/`.aws`, socket connect/send and DNS, disables pytest cache/plugin auto-loading and bytecode writes. All HTTP transports are MockTransport; dummy key is public deterministic fixture material. No real API/storage/live-authority verification was performed.

## 11. Change Manifest

Modified in this task:

- `src/polymaker/rdp_sm_execution.py` — reader evidence transfer and M2 factory handoff/provenance.
- `src/polymaker/rdp_sm_loop.py` — waiting/startup handoff and unresolved-risk cutoff result guard.

Created:

- `tests/test_rdp_sm_m3_h01_handoff.py`.
- `docs/development/RDP_SM_M3_H01_HANDOFF_FIX.md`.

Shared FL production-core changes: **NO**. SHA256 entry/final comparison identifies only the above two existing files changed and the above two new files; no deletion. M1 source, original M3 tests, historical reports, Type2 identity logic, M2 capital/terminal/reconciliation/deadline code, CLI/config and shared Gateway/Tracker/FillTruth/Journal/Engine/FL strategy preserved. Baseline `git diff --quiet` for shared-core paths exits0.

Final Git status (SM files were already untracked; listing is not a claim that this task created all of them):

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
?? tests/test_rdp_sm_m3_h01_handoff.py
?? tests/test_rdp_sm_m3_loop.py
?? tests/test_rdp_sm_type2_account.py
```

## 12. Remaining MicroLive Blockers

| Issue | Evidence / impact | Blocker | Decision |
|---|---|---|---|
| M3-H01 evidence loss | Original schedule now preserves4.50 through waiting/M2; zero placement and BLOCKED result | Resolved in Worker-tested scope; independent acceptance pending | FIX NOW completed |
| Current Type2 Safe live signing authority | Existing marker remains NOT_VERIFIED_FOR_LIVE; local derivation/offline SDK success does not prove current Safe permission | YES before real trading | DEFER to live preparation; not bypassed |
| Real official interface/storage verification and explicit live startup authorization | Offline mocks prove tested composition only; live verification not performed and default runner still blocks live use | Required before live use; no authorization granted | DEFER to preparation |
| Conservative cutoff refusal with retained external observations | No targeted M2 release proof exists at pre-executor cutoff, so BLOCKED even if a future proof might allow release | NO safety blocker | DEFER availability optimization |

No new general CapitalGate/recovery/account-management requirements. Automatic restart recovery, Merge/Redeem/settlement and M4 reporting remain outside scope.

## 13. Final Worker Result

**CODE_READY**

The actual M1→M3→M2 path carries identity-bound risk observations across startup/waiting and into first/subsequent M2 capital checks. Original counterexamples produce zero placement; complete reliable release and sufficient-funds duplicate/sequential paths still execute. 478 final tests pass offline; M2/M0/Type2/FL regressions preserved; no shared-core change or real trading.

This is Worker self-check evidence, not Independent Reviewer PASS, MicroLive readiness/authorization or live authority verification.

Next action: **Independent M3-H01 Targeted Review**.
