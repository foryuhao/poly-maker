# RDP-SM M2-H01 — Cross-Snapshot Commitment Fix

## 1. Baseline / Branch / HEAD

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- RDP-SM SOP v1.8 FROZEN; FL-first / Thin SM Orchestrator; MicroLive First. No strategy rule changed. No landed v1.8 document is substituted with older strategy rules.
- Read the original M2 Worker, Independent Review, Targeted Remediation and Targeted Independent Review reports. Latest independent decision remains historical **FAIL**; this Worker fix awaits a new independent review.
- Entry Git status: preserved prior 22-line CLI addition and untracked M0/M1/F01/M2 artifacts/reports. Staged diff empty. Branch/HEAD correct; no checkout/reset/cleanup, historical RDP branch access or overwriting audit evidence.
- MicroLive authorization: **NO**. No live credentials, external HTTP/WS/RPC, real orders or real cancellations were used.

## 2. Original Reviewer Counterexample

The targeted independent review reproduced both schedules:

```text
Official account balance: $6.00
External LIVE BUY: price $0.90, original quantity 5 -> $4.50 commitment
Initial window snapshot: visible only on list read1 or read2
Later pre-submit snapshot: both listings omit that order
Requested SM BUY: price $0.49, quantity 5 -> $2.45 commitment
Old behavior: 1 actual POST; total account commitments at POST $6.95
External order remains LIVE; own order later closes and result is SAFE
```

Only replica visibility changed; no external writer, fill, cancellation or genuine liability release occurred. A correctly enforced $30 SM cap did not protect the separate $1.50 available collateral budget.

The new production-path regressions preserve that venue row as LIVE and assert **zero Gateway.place calls**, zero SDK POSTs and zero cancellations for both initial visibility schedules, including another attempted window after further omissions.

## 3. Actual Root Cause

Previously `SMWindowExecutor._snapshot` rebuilt `reservations` and `identities` on each invocation. Its merge included only that invocation's two returned lists and this session's placement attempts. `execute_window` read a preliminary snapshot and another before placement; an external order observed in the first was not in `self.attempts`, so disappearance from the second's lists erased its reservation.

No explicit CANCELLED state was assigned to the missing order. The financial effect nevertheless treated an absent row as liability release. A local owned ACK was retained correctly by the preceding fix; external observations were not.

Startup M1 also reads open orders. Discarding that raw identity evidence before constructing the executor would leave the same omission gap at session startup. The fix therefore preserves that observation too, rather than fixing only read1/read2 inside `execute_window`.

## 4. Minimal Code Change

Production changes are limited to the existing SM adapters:

| Source | Change | Purpose |
|---|---|---|
| `src/polymaker/rdp_sm_execution.py:181-204` | `ExecutionClobClient.get_open_orders` retains first observed raw order economics by exchange ID and latches any observed fill/associate evidence | Survive later list omissions and account-read failures without replacing Gateway |
| `:264,386-401` | Allocate account observation map; preserve startup observations in the execution client | Avoid forgetting liabilities between M1 preflight and the first execution snapshot |
| `src/polymaker/rdp_sm.py:397-400` | Optional private `_reads` parameter, default still the existing `_Reads(cfg)` | M2 supplies its real observer-enabled reader to the unchanged read-only preflight call chain; ordinary M1/CLI behavior and result structure remain unchanged |
| `rdp_sm_execution.py:544-615` | Merge retained external observations, both fresh lists and owned attempts by identity; exclude historical owned rows from external envelopes | Retain external obligations while using existing fresh owned terminal/fill proofs; avoid duplicate capital |
| `:642-690` | Narrow read-only no-fill terminal verifier | Release only when current official order/trade evidence establishes cancellation with no fills |
| `:285-310,508,632` | Relevant delivered external events invalidate admission evidence; snapshot rejects a changed evidence revision | A late external LIVE/CONFIRMED cannot preserve an old zero-fill spendable budget |
| `:149-155,1071-1075` | Known observed IDs may be read; ACK cannot reuse an observed external identity | Reuse the existing official get_order endpoint without granting external cancellation/ownership |

The SDK observation map is in-memory account evidence within the existing SM client, not a new DB, CapitalGate or generic external order manager. No trading algorithm, Type 2 support or shared execution/signing code changed.

Read permissions include observed exchange identities. DELETE remains restricted to `known_ids` owned by SM. External orders are not registered in the SM Tracker, cancelled, or projected into SM inventory.

## 5. Commitment Retention Contract

- An observed external BUY retains its full original limit notional across snapshots and windows. Repeated empty lists cannot erase it.
- First observed economics remain the comparison basis; incompatible later price/original-size/token/side data blocks admission instead of replacing the liability with a smaller one.
- Identity-keyed merging counts the same order once across history, fresh lists and local attempts. Owned unresolved ACK/INTENT/UNKNOWN maxima continue using the existing local ledger.
- Historical owned LIVE rows do not override fresh owned terminal/fill proofs. Current official LIVE rows still trigger the existing terminal contradiction containment.
- Observed positive cumulative size, associated trade IDs, relevant official trade legs, or delivered relevant external events prevent the narrow zero-fill release. A later zero-fill replica cannot erase that evidence.
- Failed/unknown order/trade reads preserve the maximum reservation. A lookup failure is never interpreted as absence or rejection.
- Changes during the account/proof/time read sequence invalidate that admission snapshot. Its evidence revision is also checked after the last awaited spender read and before creating the placement intent; it cannot be silently refreshed to the revision of a later event. Existing final submission guards still reject subsequent changed revision, stale time and cutoff.

No automatic external-order recovery was added. External orders with any fill or unresolved delivered event retain their conservative envelope for this session. They may reduce liquidity; they cannot fund a new SM order through guessed release.

## 6. Release Conditions

The only new external release case is **verified zero-fill cancellation**. For an omitted external BUY, `_external_no_fill_terminal` requires:

1. No previously observed fill/associate evidence or delivered unresolved external event.
2. Real `Gateway.get_order` succeeds with matching ID, canonical token, BUY side, original price/quantity and the originally observed market identity.
3. Status is CANCELED/CANCELLED; cumulative matched quantity is zero; `associate_trades` is explicitly present and empty.
4. Real `Gateway.get_trades` completes its actual SDK pagination; production trade parsing identifies **no maker leg for that order**. Any relevant leg, including MATCHED/MINED/CONFIRMED, prevents release. Malformed or failed trade information also prevents release.
5. A second official get_order after the trade read still proves the same zero-fill cancelled state and identity.
6. The proof is recorded using the existing durable RiskJournal write before admission uses the released budget.

Release reduces only that snapshot's reservation. **History is never deleted** and there is no cached external COMPLETE flag. Every subsequent snapshot must obtain fresh proof again. LIVE, missing/error responses, new fills or contradiction restore the maximum; a new relevant event invalidates the current read sequence/submit guard.

This does not implement partial-filled external capital settlement or automatic release of UNKNOWN exposure. The existing owned-order close barrier remains responsible for SM orders; cancellation ACK alone still releases nothing. Current official facts are evidence for the current admission, not a proof about all future venue events.

## 7. Balance Semantics

The shared `collateral_readiness` and Gateway/SDK account readers remain unchanged. They expose raw official collateral micro-unit balance and applicable allowance; source inspection alone does not prove that the venue has already netted all reservations.

The conservative SM admission remains:

```text
Reserved = sum(unique unresolved BUY envelopes)
Spendable = max(0, min(30,
                      raw_official_balance - Reserved,
                      applicable_allowance - Reserved))
NewOrderCommitment <= Spendable
TotalSMCommitment + NewOrderCommitment <= 30
```

It does **not** deduct from M1's already reservation-adjusted budget a second time. Same economic order identity is charged once. For example, $7.40 raw collateral with one $2.45 external order yields $4.95 budget, even after repeated observations, and permits the legitimate $4.85 SM pair. Sequential admission then includes the first local ACK even if its list entry is missing.

If raw balance already includes an exchange reservation deduction, this conservative policy can under-admit. It never infers that the known liability has disappeared or guesses that a lower balance is uncommitted money. Real venue reservation/settlement semantics remain **NOT_VERIFIED** without authorized interface verification. This report claims identity-deduplicated accounting and conservative offline safety, not exact live liquidity utilization.

Confirmed owned inventory costs remain in the unchanged $30 economic ledger. Cancel requests and unknown responses cannot free that ledger prematurely. Other-market inventory/trades never enter SM confirmed inventory.

## 8. Regression Test Evidence

New file: `tests/test_rdp_sm_m2_h01_commitment.py`, **20 focused cases**, calling the real production SM executor/Gateway/SDK/Tracker/FillTruth/Journal. External HTTP, clock/events and environment are faked using existing fixtures. The original-counterexample test wraps Gateway.place only to log calls and delegates to its unchanged implementation; it returns no fabricated result.

| Required scenario | Test / actual assertion | Result |
|---|---|---|
| T01 initial read1/read2 liability disappears | `test_reviewer_initial_observation_survives_presubmit_omission`, two schedules: zero actual Gateway.place calls/POSTs, LIVE venue order unchanged, next attempted window also blocked | PASS |
| Startup variation | `test_startup_preflight_observation_is_retained_too`: only M1 startup list contains the order; later four lists omit it; no POST | PASS |
| T02 repeated omissions | `test_repeated_omissions_and_live_or_unknown_state_retain_maximum`: three omitted snapshots still reserve $4.50, then timeout preserves it and blocks submit | PASS |
| T03 terminal proof / fresh recheck | `test_only_fresh_no_fill_terminal_proof_releases_budget`: actual cancelled order + trade reads release to $6; later LIVE restores $4.50; new verified cancellation permits one actual Maker POST, only own order cancelled | PASS |
| T04 UNKNOWN / unresolved fill | Above timeout case plus `test_cancellation_with_unresolved_or_new_fill_evidence_cannot_release`, six variants: MATCHED/MINED/CONFIRMED legs, late cumulative update, absent associate evidence, market conflict; no unsafe POST | PASS |
| Historical fill omission | `test_seen_external_fill_cannot_be_erased_by_later_zero_fill_replicas`, three sources: list/order/trade evidence persists through later zero-fill/empty replies; no POST | PASS |
| T05 duplicates; T06 sufficient funds | `test_duplicate_observations_and_own_proofs_do_not_double_count`: reserve $2.45 once through repeated reads; budget $4.95; two legitimate two-order windows make 4 POSTs and SAFE; historical owned rows do not retain cancelled notional | PASS |
| T07 sequential allocation | `test_sequential_submit_reserves_omitted_external_and_local_ack`: $7-$4.50 permits first $2.45; second $2.40 blocked after first ACK, first safely closed, external remains LIVE | PASS |
| T08 ambiguous balance basis | `test_ambiguous_balance_reservation_semantics_never_infer_release`: raw balance $3 does not erase $4.50 known commitment; budget clamps to zero and no POST | PASS, conservative policy; live netting NOT_VERIFIED |
| Late delivered external facts | `test_late_external_ws_fact_invalidates_zero_fill_budget_before_submit`, LIVE/CONFIRMED: real processor invalidates a just-computed release budget during official time read, zero POSTs; next read retains envelope; SM inventory remains zero | PASS |
| Final awaited SDK read | `test_external_fact_during_final_sdk_spender_check_cannot_reuse_release`: SDK resolver delegates to the real implementation while delivering an external LIVE event; old budget revision rejected before intent, zero attempts/POSTs | PASS |
| T09 existing constraints | Original 62 M2/remediation tests plus selected 346 M1/FL cases | PASS |

Final execution command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short
```

```text
82 passed in 13.56s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

20 new + 14 preceding remediation + 48 original M2 cases; 0 failed, 0 skipped. Intermediate runs are not added to the distinct-case count.

Related regression command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short
```

```text
346 passed, 1 deselected in 5.75s
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

64 existing M1/F01 plus 282 selected FL cases. Total **428 executed / 428 passed / 0 failed / 0 skipped; 1 deselected**. The unchanged exclusion `test_cli_conflict_stops_before_settlement_preflight` remains justified: CLI dotenv discovery reaches protected workspace `.env` before the expected ConfigAliasError. It is deselected, not passed/skipped; no live credential protection was disabled.

```bash
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm.py src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_commitment.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm.py src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_commitment.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

All exit0; Ruff reports all checks passed / three files formatted. Tests clear inherited credentials, deny workspace credential reads and socket/DNS, use temporary CWD and local dummy-key signing under Fake HTTP. No package installations or live smoke. CLI/config/previous test files and all historical reports retain their entry hashes.

## 9. M2-H02 Regression

**PASS.** No deadline/cancel scheduling redesign. The existing timer, original T0, managed `_await_owned`/`_cancel_known` operations, deadline wait and timer drain remain in place.

All preceding slow-query tests pass: 5-second/six-page responses are read after actual cancellation at mono3; a held second-leg SDK read does not delay actual DELETE at T0+3..3.4; repeated caller cancellation drains the same reads/jobs; LIVE after cancel stays BLOCKED with capital retained. T+222/T+240 tests still produce zero BUY POSTs.

New external proof reads are part of the existing managed snapshot. If they are slow during second-leg validation, the same independent deadline cancellation remains responsible for known SM orders. They do not create a separate polling task, restart the timer or change FillWindowSec.

## 10. M0 Findings Regression

| Finding | Existing actual-executor evidence | Status |
|---|---|---|
| M0-H01 | Terminal-then-LIVE, official LIVE and open-list contradiction cases invalidate old proof; next-window POST count unchanged | **CONTAINED** |
| M0-H02 | Late/status-only/cross-order confirmation tests require current official trade set, update confirmed inventory once and prevent stale COMPLETE admission | **CONTAINED** |
| M0-H03 | First/second UNKNOWN and lookup/duplicate-ID cases retain maximum commitment, never retry and never add next-window POSTs | **CONTAINED** |

Original M0 harness and historical conclusions remain untouched. Shared Tracker/FillTruth semantics and owned-order reconciliation remain unchanged. New external observations are collateral evidence only.

## 11. MicroLive Impact

| Issue | Impact | Blocker | Decision |
|---|---|---|---|
| Observed LIVE liability lost across snapshots/startup | Formerly could reuse $4.50 against $6 and submit $2.45; now both original schedules make zero Gateway.place calls | NO after Worker fix; independent acceptance pending | **FIX NOW — implemented** |
| Unsafe release through later zero-fill/incomplete replies | Narrow release requires fresh zero-fill terminal/trade proof; historical fill/event evidence retained; concurrent relevant state change invalidates budget | NO within tested contract | **FIX NOW — necessary retention/release protection implemented** |
| Partial-filled/external-event envelope remains conservative | Can block otherwise affordable orders; never releases uncertain exposure through guessed settlement | Not a safety blocker to code delivery | **DEFER** automatic external release/recovery; not needed for this fix |
| Exact live balance semantics / physical storage / interface behavior | Offline evidence only; conservative local policy and existing no-restart boundary remain | Authorized preparation prerequisite | **DEFER**, previous disposition unchanged |
| Selected Type2 account unsupported | Current chosen account cannot run SM until separately verified/adapted | **YES for MicroLive** | Separate targeted task; not implemented here |

No MicroLive readiness/authorization claimed, no M3 work. This is Worker self-check, not Independent Reviewer PASS.

## 12. Remaining Type 2 Blocker

**ACCOUNT TYPE 2: UNSUPPORTED — unchanged.** User-selected `config` remains Type2; prior signer/funder control evidence remains NOT_VERIFIED. EOA-only startup and per-submit identity restrictions remain executable. SDK signer/funder settings, shared Gateway signing core and actual account configuration were not changed or accessed with real credentials.

The preceding review's conditional Type2 thin-adapter reuse recommendation remains a separate task. Historical FL capability does not verify current authority. This fix neither removes identity checks nor authorizes an account adaptation.

## 13. Change Manifest

Modified this iteration:

- `src/polymaker/rdp_sm_execution.py`: SM account observation retention, identity-deduplicated budget, narrow fresh zero-fill terminal verification and admission evidence invalidation.
- `src/polymaker/rdp_sm.py`: private optional read-composition injection so M2 can preserve startup observations; default M1/CLI path unchanged.

Created:

- `tests/test_rdp_sm_m2_h01_commitment.py`: 20 focused offline cases.
- `docs/development/RDP_SM_M2_H01_COMMITMENT_FIX.md`: this report.

**Shared production core changes: NO.** Gateway, Tracker, FillTruth, Journal core, signing core, Engine, FL strategy, configuration, previous tests and original reports unchanged. Only the two existing SM modules changed. No new DB or external service.

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

The CLI change is the preserved earlier M1 addition. Untracked prior files were not cleaned or overwritten. Staged diff empty; no commits.

## 14. Final Worker Result

**CODE_READY**

The actual Window Executor no longer loses an observed external BUY envelope on list omission. The Reviewer read1/read2 schedules and startup variation stop before Gateway.place; repeated/unknown/contradictory observations preserve risk, duplicate identities are not double-counted, and current zero-fill cancellation evidence can release liquidity without erasing history. Valid sufficient-funds/sequential paths remain available.

428 selected tests pass with zero network/DNS attempts and no live credentials. M2-H02 remains PASS; M0-H01/H02/H03 remain CONTAINED; selected FL behavior and shared core unchanged. Real interface/reservation semantics remain NOT_VERIFIED; Type2 remains UNSUPPORTED; MicroLive remains unauthorized.

Next action: **Independent Targeted Review.**
