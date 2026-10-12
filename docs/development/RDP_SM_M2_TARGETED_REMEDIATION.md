# RDP-SM M2 — Targeted Remediation

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- Frozen strategy: RDP-SM SOP v1.8; FL-first / Thin SM Orchestrator; MicroLive First. No landed SOP v1.8 file; the frozen user execution contract remains authoritative. No strategy rule was changed.
- M0: CLOSED_WITH_FINDINGS. M1: ACCEPTED. M1-F01: CLOSED. Previous M2 independent decision: **FAIL**, with M2-H01/H02 requiring targeted remediation. M0-H01/H02/H03 remain CONTAINED in the actual SM execution path.
- Entry workspace: pre-existing additive CLI diff and untracked M0/M1/F01/M2 source, tests and audit reports. Staged diff empty; branch and HEAD correct. Existing files were preserved, including the original M2 Worker/Reviewer reports. No checkout/reset, historical RDP branch access or commits.
- This is a Worker implementation/self-check, **not independent acceptance**. MicroLive authorization: **NO**.

## 2. Objective

Audit the actual configured account locally and fix only the two confirmed M2 safety defects in the production `SMWindowExecutor`: omitted liquid collateral reservations and discretionary reads delaying cancellation. Preserve the existing $30 ledger, Maker-only execution, official-interface boundary and M0 containments.

No M3 strategy, shared execution/signing changes, new wallet client, Proxy framework, RPC, new API, database or automatic recovery. No authenticated live request, real order or real cancellation. The authorized account audit reads existing local configuration/credentials and historical FL logs; tests separately forbid live credentials.

## 3. Account Compatibility Audit

### Actual selected configuration

The user explicitly confirmed **`config` (Type 2)** as the actual FL launch configuration. `config/config.toml:17` specifies `signature_type = 2`. The alternative `livecfg/config.toml:4` specifies Type 3 and is **not** used to infer the selected account's mode. Rabby login alone was not used as evidence.

Local `Config.load('config')` and `Account.from_key(cfg.secrets.pk)` produced only masked identity output:

```text
Configured Signature Type: 2
Signer: [REDACTED_ACCOUNT]
Funder: [REDACTED_ACCOUNT]
Signer/Funder: DIFFERENT
Network/DNS attempts during local audit: 0
```

Canonical `POLYMARKET_PRIVATE_KEY` supplies the signer key; canonical `POLYMARKET_WALLET_ADDRESS` supplies `Secrets.browser_address`. No credential values were printed, written to this report, placed in fixtures or sent to any service. No SDK connection or API credential derivation was performed with those real credentials.

### Actual FL call chain and account semantics

| Question | Actual source / evidence | Conclusion |
|---|---|---|
| What is the signer? | `config.py:275` loads configuration; local `eth_account.Account.from_key` derives the EOA | Locally derived signer above; not the configured funder |
| What does WALLET_ADDRESS mean? | `execution/gateway.py:115,134-155` passes `sec.browser_address` as SDK `funder`, then sets Gateway `_funder`; `positions:653-671` queries it as `user` | Funds/positions address, not the signing EOA |
| Does FL separate signer and funder? | `Gateway.connect` passes key, configured signature type and funder separately to the existing official `ClobClient` | **VERIFIED_BY_CODE**; no new signing implementation needed |
| Type 2 behavior? | Installed `py_clob_client_v2/order_builder/builder.py:45-66,190-198`: maker is funder, Type 2 signer is key-derived EOA; configured mode is included in signed order | SDK supports Safe-mode signing; SDK support alone does not establish current account control |
| Balance / allowance identity? | `gateway.py:751`; SDK `client.py:687-696` uses signer/API-key L2 authentication and configured `signature_type`; no explicit funder query | Relies on official account/signature-mode binding; not locally provable from a constructed builder |
| Positions identity? | `Gateway.positions` uses full configured funder in official Data API `user` | Explicit configured funder |
| Open-order identity? | `gateway.py:595`; SDK `client.py:534` uses authenticated L2 reads | Signer/API-key account; no explicit funder query |
| Why SM blocks it? | `rdp_sm_execution.py:365-367,479-482` requires mode 0 and signer=funder on startup and each submission snapshot | Current SM **does not support** selected Type 2 account; no silent fallback to Type 0 |
| Existing control-relation verifier? | `settlement_sweep.py:270-277` requires deployed Safe, `getOwners` and threshold 1 via RPC | Only read source; did **not** initialize or execute this RPC/settlement path |

### Available historical live evidence

Read only the existing current-workspace `logs/favorite-first-live.jsonl`. A socket/DNS-denied diagnostic aggregated results without printing raw records or full identities:

- 8 non-paper Gateway connection records have signer/funder prefixes consistent with the local configured identities. These are truncated-prefix matches, not full historical signer proof.
- 118 historical positions requests contain the exact configured funder as `user`.
- 11 historical `/balance-allowance` HTTP 200 records explicitly use `signature_type=2`.
- 8 historical POST `/orders` HTTP 200 records and 6 FL fill records exist in that log.

These establish **VERIFIED_BY_PREVIOUS_LIVE_EVIDENCE** for historical FL Type 2 reads/trading activity consistent with this configuration. HTTP 200 alone is not an individual order acceptance proof, and these logs do not record the full signer or current Safe owners/threshold. They are not a fresh current-control verification or a new live test.

### Disposition

- Configured mode: **2 / CONFIGURED**; separated FL SDK identities: **VERIFIED_BY_CODE**.
- Current Safe control relationship and current official balance/order-to-funder binding: **NOT_VERIFIED** offline.
- Actual compatibility with the **current SM executor: UNSUPPORTED** because its executable EOA-only guard rejects Type 2. This is an **ACCOUNT_COMPATIBILITY_BLOCKER** for the selected account, not proof that FL Type 2 signing is defective.
- No account/signing adaptation implemented. Existing evidence cannot satisfy the current explicit identity proof contract without assuming control from configuration/log prefixes or invoking prohibited RPC. Do not change mode to 0, guess a funder or introduce a new interface to bypass the block. An approved account-evidence/compatibility decision is required before that account can use SM.
- The account limitation does not prevent independent delivery of the H01/H02 fixes. **MICROLIVE_READY = NO**.

## 4. M2-H01 Root Cause

Original independent review, section 8: M1's `_account` subtracts the remaining notional from its first open-order snapshot; the original M2 snapshot retained that budget after reading another order list and subtracted only matched quantities from that later list. It did not reserve a locally acknowledged LIVE order absent from a lagging list, or the resting portion of a newly observed other-market order.

Counterexamples preserved from the Reviewer report:

1. Balance $3; first Maker $2.45 ACK absent from open-order listings; second $2.40 incorrectly POSTed. Combined $4.85 commitment exceeds observed collateral.
2. Balance $6; newer list reveals other-market BUY5 at $0.90; original code still permits a $2.45 POST despite only $1.50 remaining.

The $30 session exposure ledger was not the defect; liquid balance/allowance admission was. Existing `collateral_readiness` (`favorite_live.py:183`) converts raw collateral micro-units and resolves applicable allowance but does not reconcile local unlisted placements. Gateway returns official raw balance/allowance; neither its implementation nor the inspected SDK establishes that those fields have already deducted all resting-order liabilities.

## 5. M2-H01 Minimal Fix

Changed only SM `_snapshot` (`rdp_sm_execution.py:477,509-573`):

1. Preserve the raw SDK rows from the first M1 account read, then merge them with the second official open-order snapshot.
2. Key commitments by exchange order ID. Validate canonical token, side, original price/quantity and owned-order economics. Duplicate IDs within a list or conflicting economics across lists block.
3. Reserve the full original BUY limit notional for each unresolved observed order, conservatively covering remaining and possibly unsettled matched quantity. Other markets' BUY liabilities also reserve collateral. Two snapshots of the same ID count once.
4. Merge local ACK/INTENT/UNKNOWN maxima using the same known ID, or the unique unknown attempt ID, taking the maximum rather than adding it to that same official liability. A lagging list cannot erase a known commitment. Independently terminal, reconciled orders may release the unfilled envelope; confirmed costs remain in the separate $30 economic ledger.
5. Rebuild the **submission** budget from raw official balance and applicable allowance, rather than subtracting again from M1's previously adjusted budget:

```text
reserved = sum(unique unresolved BUY envelopes)
AvailableSpendable = max(0, min(30,
                               official_balance - reserved,
                               applicable_allowance - reserved))
new_quote_notional <= AvailableSpendable
TotalSMCommitment + new_quote_notional <= 30
```

The second quote always obtains a new snapshot and includes the first quote's local commitment. Insufficient funds stops the second request before intent/POST, while the first is still tracked, cancelled and reconciled. A capital refusal is a pre-submit safety refusal, not a fabricated venue rejection.

The same order's liability is not duplicated between local attempts and the two lists. Unresolved envelopes deliberately retain their conservative maximum until the terminal/confirmation evidence allows release. Live collateral reservation/settlement semantics remain **NOT_VERIFIED**; if raw collateral already excludes a reservation, this policy can under-admit orders. It cannot use that uncertainty to authorize more funds. No claim of exact live spendable liquidity is made.

| Required case | Actual test / assertion | Result |
|---|---|---|
| C01 $3 versus $4.85 combined; C02 sequential ACK | `test_h01_ack_missing_from_listing_cannot_spend_first_commitment_twice` (balance and allowance): exactly 1 POST, commitment at POST $2.45; second blocked, first owned/cancelled/terminal, release only after proof | PASS |
| C03 known/newly visible resting order | `test_h01_either_official_snapshot_reserves_newly_observed_order` (each list): $6-$4.50 insufficient; exactly 0 POSTs | PASS |
| C04 UNKNOWN | Original `test_e03_h03_unknown_retains_commitment_no_retry_or_next_window`: unknown maximum retained, no retry/new-window POST | PASS |
| C05 cancel ACK without terminal | Original `test_e04_cancel_ack_does_not_prove_terminal_or_release_capital`: $4.85 remains committed, BLOCKED, no further POST | PASS |
| C06 clearly sufficient | `test_h01_official_and_local_identity_reserve_once_with_sufficient_funds`: $7.40 collateral versus $7.30 including external order; exactly 2 SM POSTs and SAFE; external order left intact | PASS |
| Pending partial / conflicting snapshots | `test_h01_partial_pending_fill_does_not_release_first_order_budget`; `test_h01_conflicting_snapshot_economics_fail_closed`: pending $2.45 retained / conflicting evidence produces 0 POST | PASS |

## 6. M2-H02 Root Cause

Original independent review, section 11: the fill-loop awaited a complete `reconcile_owned_order` before reaching cancellation. Official SDK trade pagination may perform many blocking worker-thread requests. Loop expiry checks did not interrupt that await or independently initiate known-ID cancellation.

The preserved counterexample starts a 3-second window at T+220 and returns six trade pages taking 4 seconds each. Cancellation did not start until mono24/T+244. A held necessary GET likewise kept the event loop responsive without initiating cancellation at the deadline. Cancellation initiation, rather than final reconciliation duration, was defective.

Cancelling an asyncio awaiter cannot terminate the existing SDK's running thread. This fix does not claim otherwise and does not abandon such workers as detached state-mutating tasks.

## 7. M2-H02 Minimal Fix

Actual scheduling changes are in `rdp_sm_execution.py:702-755,835-850,989-992,1033-1072`:

- **DROP optional active REST polling** during the 3-second wait. Existing event processing still records delivered WS events; actual production FillTruth recovers REST-only fills after cancellation. Optional market/trade reads cannot justify postponing closeout.
- After first ACK ownership and durable ACK recording, schedule an event-loop timer against the **original** T0 deadline. It is not restarted at ACK. When ACK arrives after expiry, delay is zero and known cleanup begins immediately.
- The timer initiates real `Gateway.cancel` independently of any necessary second-leg snapshot/SDK read. Gateway's existing eight-worker pool permits cancellation while the sequential read path is waiting on a worker; no new pool/infrastructure was introduced.
- `_cancel_known` owns and awaits one cancellation task per known-ID set, shared by timer and foreground closeout. New known IDs require their own cleanup scope. Cancel ACK continues to carry no terminal proof.
- `_await_owned` shields and drains the same snapshot/verification coroutine through caller cancellation, retaining ownership of its SDK reads. Abnormal exit latches BLOCKED. Timer/SDK/cancellation tasks are awaited before result/teardown; no timed-out worker is assumed dead.
- On expiry: no further placement; known cancellation takes priority; then terminal verification and actual confirmed reconciliation. Final reconciliation may exceed 3 seconds and T+240. Failed terminal evidence stays BLOCKED with commitment retained.
- Existing T+15/T+240 submit guards and 15-second closeout buffer are unchanged. With the default window, a start at T+222 is refused. Timer scope is acknowledged identities; an in-flight request without a known ID cannot be selectively cancelled until its identity becomes available, and remains governed by UNKNOWN/intent risk containment.

Guarantee demonstrated: discretionary polling and a slow required second-leg worker read no longer postpone known-ID cancellation initiation until read completion. This is **not** a hard real-time OS/venue cancellation-completion guarantee. The event loop must run and the venue must accept/process its request. A permanently blocked necessary read can still keep the overall executor blocked; it does not allow another placement or a premature result.

| Required case | Actual test / assertion | Result |
|---|---|---|
| T01 5-second active read / original six-page schedule | `test_h02_optional_slow_reads_never_precede_deadline_cancellation`, 1/6 pages: first event is actual DELETE at mono3; trade GETs occur afterward; final reconciliation duration may reach T+247 | PASS |
| T01 necessary SDK read held through expiry | `test_h02_deadline_cancel_overtakes_blocked_second_leg_read_and_drains`: real monotonic/event loop; DELETE at T0+3..3.4, before read release and its possible 5-second hold; exactly 1 POST, 1 cancellation, no early result | PASS |
| T02 near cutoff; T03 cutoff | `test_h02_cutoff_still_prevents_all_new_posts`, elapsed222/240: 0 POSTs; `MARKET_SUBMISSION_CUTOFF` | PASS |
| T04 cancellation leaves LIVE | Held-read test LIVE variant plus original cancel-ACK test: BLOCKED, maximum retained, next-window POST count unchanged | PASS |
| T05 late CONFIRMED | Original `test_e06_h02_late_confirmed_invalidates_complete_and_requires_official_set`: old proof invalidated; no extra POST until current official evidence; exactly-once inventory | PASS |
| T06 normal / abnormal | Original 48-case suite plus held-read terminal and repeated-caller-cancellation variants: normal SAFE; abnormal BLOCKED and all worker/cancellation owners drained | PASS |

## 8. M0 Regression

No M0 harness/shared caches were modified or re-audited. Existing real-executor integration cases were rerun:

| Finding | Current regression evidence | Disposition |
|---|---|---|
| M0-H01 terminal then contradictory LIVE | `test_e07_h01_terminal_then_live_blocks_even_old_official_terminal`, official LIVE/open-list contradiction tests; invalidate old proof, BLOCKED, next POST count unchanged | **CONTAINED** |
| M0-H02 late CONFIRMED / stale COMPLETE | `test_e06_h02_late_confirmed_invalidates_complete_and_requires_official_set`, status-only and cross-order fill-set tests; confirmed inventory updated once, current official set required | **CONTAINED** |
| M0-H03 UNKNOWN / unsafe retry | `test_e03_h03_unknown_retains_commitment_no_retry_or_next_window`, lookup failure and duplicate placement identity tests; no retry/next POST, unknown maximum retained | **CONTAINED** |

These are containments in the actual SM production path, not claims that UNKNOWN has automatically resolved or that the original M0 FAIL evidence is obsolete.

## 9. Test Evidence

Environment: repository `.venv`, Python 3.12.14, existing pytest/pytest-asyncio and installed official SDK; no dependency installation. Tests exercise actual SM executor, SDK signing/transport parsing, Gateway, Tracker, FillTruth and real Journal files. New fixtures reuse the existing M2 external HTTP fake. No FakeGateway or FakeFillTruth.

Final commands and output:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

```text
62 passed in 13.01s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

**14 new targeted cases** (7 capital / 7 timing) plus **48 unchanged M2 cases**. 0 failed, 0 skipped.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

```text
346 passed, 1 deselected in 5.64s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

64 unchanged M1/F01 cases plus 282 selected FL regressions. Combined **408 executed / 408 passed / 0 failed / 0 skipped; 1 deselected**. Repeated development runs are not added to the distinct count.

The existing `tests/test_config_aliases.py:105::test_cli_conflict_stops_before_settlement_preflight` exclusion remains JUSTIFIED: unchanged CLI dotenv discovery reaches protected workspace `.env` before the expected ConfigAliasError. This is deselected, not passed/skipped. No protection was disabled or real credential enabled for tests.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_remediation.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_remediation.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

All exit 0; Ruff reports `All checks passed!`, `2 files already formatted`. Original M1 source/CLI, M2 tests and original M2 reports retain their entry SHA-256 hashes. Selected FL regression: **PASS**.

Network isolation is executable: before pytest imports, the unchanged offline runner clears inherited credentials, denies workspace `.env`, switches to a temporary CWD, blocks socket connection/send and DNS operations, disables plugin autoload/cache, and rejects any recorded attempt. `-B` avoids bytecode writes. HTTP is replaced at the SDK/httpx boundary; clock/event ordering and filesystem faults are the other fake boundaries. Account audit is a separate explicitly authorized local diagnostic with socket/DNS denied; it never connects a real credential-bearing SDK. Real interface/storage smoke: **NOT_VERIFIED**.

## 10. MicroLive Impact

| Issue | Evidence | MicroLive impact | Blocker | Decision |
|---|---|---|---|---|
| M2-H01 omitted liquid liabilities | Both original adverse read schedules now stop the unsafe POST; sufficient $7.40 case still accepts $7.30 including other-market order | Prevents reuse of locally known or newly observed occupied funds | NO after fix; independent acceptance pending | **FIX NOW — implemented** |
| M2-H02 late cancellation initiation | Actual DELETE at original 3-second deadline before slow page/read completion; required tasks drained | Prevents discretionary reads carrying acknowledged quotes into cutoff | NO after fix; independent acceptance pending | **FIX NOW — implemented** |
| Selected account Type 2 versus SM EOA-only guard | User-selected config2, signer/funder DIFFERENT; executable mode0-only guard unchanged | Selected account cannot execute SM; removing the guard without adequate control/account evidence would violate identity safety | **YES for selected-account MicroLive** | **FIX NOW — account adaptation stopped; evidence/Architect decision needed** |
| Live collateral/account/interface/storage semantics | Only local code, historical logs and Fake HTTP were verified | Must establish real supported account, collateral binding and operational persistence before live authorization | Pre-live prerequisite; not an H01/H02 code-delivery blocker | **DEFER to authorized live preparation** |

No new known unsafe execution path was accepted to achieve passing tests. MicroLive remains unauthorized independently of these fixes.

## 11. Deferred Issues

- Current selected Type 2 account control/official binding is unverified and its SM compatibility is UNSUPPORTED. Do not mistake theoretical SDK support or historical log prefixes for a completed adaptation.
- Real current official interface/finality and collateral reservation semantics, physical storage durability and operational journal location must be checked during separately authorized preparation. This patch deliberately reserves conservative maxima.
- Existing no-auto-restart/manual-history-review boundary remains; no recovery framework developed. Necessary SDK reads are drained and may extend the blocked cycle.
- Existing M0 findings remain CONTAINED. Prior RFQ capability-boundary limitation and future M4 reporting needs retain their accepted dispositions; no scope expansion. Submitted-price grid validation remains in the existing M2 executor and was not relaxed.

## 12. Change Manifest

Modified this iteration:

- `src/polymaker/rdp_sm_execution.py`: only SM collateral composition, deadline cancellation ownership and managed-read cleanup.

Created this iteration:

- `tests/test_rdp_sm_m2_remediation.py`: 14 targeted production-path offline cases.
- `docs/development/RDP_SM_M2_TARGETED_REMEDIATION.md`: this report.

**Shared production core changes: NO.** M1 source/CLI, original M2 tests, original audit reports, Gateway, Tracker, FillTruth, Journal core, config, signing core, Engine and FL strategy unchanged. No project configuration or credential file was edited.

Final Git status (untracked directory expanded by the explicit manifest above):

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

The CLI status is the preserved earlier M1 addition, not an edit in this remediation. Prior untracked artifacts are also preserved, not newly authored here. Staged diff empty.

## 13. Final Result

**CODE_READY**

M2-H01 and M2-H02 are fixed in the production execution path with adverse schedules, actual POST/DELETE counts, real cancellation timing/order, inventory/commitment assertions and 408 passing selected cases. M0 containments remain valid in the existing tested path; shared core unchanged; no real trading/network requests. This is Worker self-check, not independent Reviewer PASS.

Separate account compatibility: **UNSUPPORTED** for the selected Type 2 account in current SM. Its current control relationship is **NOT_VERIFIED**. No account support is claimed from SDK capability alone.

**MICROLIVE_READY = NO. MICROLIVE AUTHORIZATION = NO.** CODE_READY concerns the independently deliverable H01/H02 code fixes, not account clearance or live authorization.

## 14. Next Action

**Independent Targeted Review.**
