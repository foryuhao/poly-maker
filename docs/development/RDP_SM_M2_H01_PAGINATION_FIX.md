# RDP-SM M2-H01 — Pagination Evidence Safety Fix

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD / original FL baseline: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- Frozen SOP: RDP-SM v1.8; architecture: FL-first / Thin SM Orchestrator; principle: MicroLive First. No SOP modification.
- M0: CLOSED_WITH_FINDINGS; M1/F01: accepted/closed; preceding H01 independent review: FAIL due to partial-pagination evidence loss. This is a Worker remediation, not an independent review.
- Entry status: existing 22-line CLI addition, untracked SM source/tests and development reports. Staged diff empty. Preserved all existing files; no checkout, reset, cleanup or historical RDP branch access.
- Read the preceding H01 commitment-fix and independent-review reports and inspected the real SM, FL Gateway, SDK pagination and necessary existing tests. No credentials inspected or used. MicroLive authorization: **NO**.

## 2. Original Reviewer Findings

| Finding | Executed schedule from the prior review | Required correction |
|---|---|---|
| P01 | Authenticated open-order page1 contains external LIVE BUY5 at $0.90; page2 fails. A later empty listing loses the $4.50 observation and permits a new $2.45 BUY against $6 collateral. | Preserve page evidence or maintain unresolved-account blocking; empty listings cannot prove release. |
| P02 | A previously observed external order has a relevant MATCHED maker fill on trade page1; page2 fails. A lagging cancelled/matched0 replica plus later empty trades erases positive fill evidence and releases $4.50. | Retain relevant partial trade evidence and prevent use of incomplete history as a release proof. |

Potential account commitment in both examples is **$4.50 + $2.45 = $6.95**, exceeding the supplied $6 collateral condition. These are liquid-funds admission failures even though the SM-only $30 cap is not exceeded. The previous reports remain unchanged.

## 3. Actual Root Cause

Real call chains:

```text
SMWindowExecutor._snapshot -> M1 _account / Gateway.open_orders
  -> ExecutionClobClient.get_open_orders
  -> official SDK get_open_orders -> _get(/data/orders), page by page

SMWindowExecutor._external_no_fill_terminal / FillTruth
  -> Gateway.get_trades
  -> ExecutionClobClient.get_trades
  -> official SDK get_trades -> _get(/data/trades), page by page
```

FL Gateway readers remain at `src/polymaker/execution/gateway.py:604,733`; installed SDK loops are at `.venv/lib/python3.12/site-packages/py_clob_client_v2/client.py:534,577`. They return a collection only after all requested pages complete. A later exception prevents that collection from returning.

The former SM observation loop ran only after `super().get_open_orders()` returned. Relevant external fill detection ran only after the full `Gateway.get_trades()` call returned. Thus a valid page received earlier could be discarded when a later page failed. The immediate failure did not authorize a placement, but it left no persistent completeness restriction; a later complete empty response could reuse a zero-fill replica to release the risk.

The fix observes pages before the SDK requests the next one and makes a failed collection a persistent account-evidence restriction. It does not replace SDK pagination or copy Gateway/Tracker/FillTruth logic.

## 4. Minimal Implementation Change

Only existing `src/polymaker/rdp_sm_execution.py` changes:

| Change | Actual source | Effect |
|---|---|---|
| Observe authenticated order pages | `ExecutionClobClient._get:148`, `_observe_order_page:212` | Retain first order economics by exchange ID and monotonically retain positive cumulative/associate evidence. |
| Observe relevant trade legs on each page | `_get:173-184` | Use shared `parse_trade`; retain first economic leg by `(trade_id, order_id)` and mark external positive fill observations. |
| Latch failed full reads | `get_trades:193`, `get_open_orders:203` | Preserve the first `TRADES_PAGINATION_INCOMPLETE` or `OPEN_ORDERS_PAGINATION_INCOMPLETE` reason. Successful later reads do not clear it. |
| Enforce the evidence restriction | `safe_to_continue:468`, `_snapshot:510,669`, pre-intent check `:1079`, SDK `_post:239` | Prevent a new window, intent or actual order POST using incomplete account history. |
| Prevent false release/close | `_external_no_fill_terminal:683`, `_verify_close:906,955` | No external release on failed history; close still attempts necessary known-order cleanup/reconciliation, then remains BLOCKED. |
| Preserve observed owned-fill economics | `_verify_order:764,792` | Current official fill set must include every previously observed owned page leg with matching token/side/price/quantity before accepting a proof. Status may legitimately advance to CONFIRMED. |

New evidence dictionaries/latch are initialized in the existing SM account adapter at `:294-295`. There is no reset API, database, new external endpoint, recovery service or capital framework. No M2-H02 timer/cancellation scheduling logic was changed.

## 5. Open Orders Pagination Evidence

After inherited M1 transport validates the official endpoint and page schema/token identities, `_get` records that page before returning it to SDK pagination. Existing first-seen economics remain keyed by order ID; repeated observations are not summed. Positive `size_matched` or associated-trade evidence remains sticky.

If any collection read fails, including a timeout, cursor cycle or invalid page, the SM SDK retains a failure reason. A malformed page need not yield usable economics: the persistent restriction still prevents using its uncertainty as absence evidence.

P01 tests execute a real window with $6 balance, a valid first-page external $4.50 LIVE order and failed second page. They assert retained quantity/price, **zero actual Gateway.place calls**, zero SDK POSTs, BLOCKED, then perform a successful empty official listing and attempt another window. That window remains BLOCKED with zero new placements. Even a subsequent cancelled zero-fill row cannot clear this failed-history restriction.

## 6. Trades Pagination Evidence

The actual shared parser extracts maker legs from each official trade page. Relevant legs for known SM IDs or already observed external order IDs are retained by `(trade_id, order_id)`. External positive evidence marks the observed order before a later page can fail.

This SDK cache is **risk evidence, not inventory**. It does not write StateStore or mutate Tracker from the SDK worker. MATCHED/MINED are not applied as Confirmed Inventory. CONFIRMED external legs are also excluded from SM inventory. Real production Tracker/FillTruth continue to project owned confirmed economics during reconciliation.

P02 tests cover MATCHED, MINED and CONFIRMED first-page states, followed by a second-page timeout and later complete empty trades. They verify the retained leg/economics, sticky account restriction, zero Gateway.place/POST calls and zero SM inventory.

An additional actual owned-order case places one BUY, receives a MATCHED maker leg during cancellation, and then loses the second trade page. Later empty trades and a lagging matched0 cancelled order cannot remove its maximum $2.45 SM commitment or permit a second order. Necessary known-ID cancellation retries are allowed; only one Gateway.place/POST occurs.

## 7. Cross-Snapshot Retention

- Failed pagination never clears previously observed rows/legs. First economic observations remain in the existing SM SDK evidence cache.
- A later successful empty listing is available for diagnosis but cannot reset `pagination_failure`.
- The same executor remains Fail-Closed after incomplete account pagination. There is no automatic clearance even if a later response appears complete. This conservative restriction is intentional and within scope.
- Snapshot admission checks both before and after awaited reads; final intent and POST checks also reject a latched failure.
- Close proof cannot turn SAFE_TO_CONTINUE while the account-evidence restriction exists. Actual known-order cancellation and official reconciliation remain available for safety/diagnosis.
- Existing journal/manual-restart restrictions remain unchanged; a new executor is not an automatic recovery mechanism.

This fixes the disappearing-evidence schedules without claiming that an event version, a fixed wait, or a later empty response establishes exchange completeness.

## 8. Commitment Release Conditions

In a session with no failed-pagination history, the existing external zero-fill release verifier still requires matching official order identity/economics/market, CANCELED/CANCELLED, matched0, explicit empty associates, a complete trade read containing no relevant maker leg, a second matching official order read and a recorded proof. Missing listing entries and cancel ACK are not terminal proof.

After failed pagination, this narrow release path returns false. Even fresh zero-fill replies do not automatically resolve the incomplete history; persistent blocking is permitted instead of implementing recovery.

Owned orders still require actual terminal, confirmed trade and complete reconciliation evidence. Their current official set must also explain previously observed page-leg economics. Pending or omitted observed legs retain the maximum placement commitment; already reconciled confirmed owned costs use the existing ledger. External observations never become owned SM fills.

Complete multi-page empty trade queries plus reliable zero-fill cancelled-order evidence still permit safe release in the new positive-path test. Complete observations of MATCHED/MINED/CONFIRMED or partial fills continue to prohibit false zero-fill release in existing regressions.

## 9. Regression Tests

New file: `tests/test_rdp_sm_m2_h01_pagination.py`, **10 cases**. The fixture opens the production SMWindowExecutor and uses the real Gateway, SDK, Tracker/StateStore, FillTruth and Journal. Only HTTP transport, public dummy account environment, time and external event ordering are faked. Placement-count wrappers delegate to the actual Gateway method.

| Required scenario | Test / meaningful assertions | Result |
|---|---|---|
| T01/P01; T07 original funds example | `test_p01_order_page_survives_failure_and_later_complete_empty` (timeout/cursor-cycle/missing-cursor): retain $4.50 economics; $6 balance; requested $2.45; Gateway.place=0/POST=0; initial and next window BLOCKED | PASS, 3 cases |
| T02/P02 | `test_p02_trade_page_retains_risk_without_inventory_or_automatic_release`: page1 relevant leg survives page2 failure; sticky fill evidence; no placements or premature inventory | PASS, 3 states |
| T03 later empty | Both P01/P02 explicitly perform a successful empty listing after failure, then try another window/close; no automatic release or SAFE result | PASS |
| T04 complete orders | `test_complete_multi_page_orders_deduplicate_and_allow_sufficient_funds`: all pages complete, one external $2.45 reservation, $7.40 raw funds; two actual Maker POSTs close safely | PASS |
| T05 verified release | `test_complete_multi_page_trade_proof_allows_verified_zero_fill_release`: complete pages, official cancelled zero-fill evidence; one actual BUY safely closes; confirmed inventory/cost zero | PASS |
| T06 duplicate legs | `test_duplicate_completed_trade_pages_do_not_sum_commitment_or_inventory`: repeated same `(trade,order)` leaves one evidence key; $4.50 reserved/$1.50 spendable; no POST/inventory | PASS |
| Owned partial collection | `test_incomplete_owned_trade_read_cannot_release_seen_fill_or_repeat_order`: one POST then cancel/fill/page failure, later empty replica; retain $2.45, no second POST, BLOCKED | PASS |
| T08 existing safety | Unmodified H01/M2 remediation/execution, M1/F01 and selected FL tests below | PASS |

Exact commands from project root:

```bash
.venv/bin/ruff format --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_pagination.py
.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py tests/test_rdp_sm_m2_h01_pagination.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_h01_pagination.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short

git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

Final results:

```text
Focused: 10 passed in 0.96s
M2 combined: 92 passed in 15.12s
M1/FL: 346 passed, 1 deselected in 6.30s
Each run: M0 offline guard: 0 network/DNS attempts; workspace credentials denied
Ruff: All checks passed; formatting unchanged on final run
Git checks: exit 0
```

Distinct final total: **438 executed / 438 passed / 0 failed / 0 skipped; 1 deselected**. The focused repeat is included in the combined count, not added again. Combined M2 comprises 10 new + 20 preceding H01 + 14 preceding remediation + 48 original M2 cases; M1/F01 are 64 and selected existing FL are 282.

During test authoring, one new test initially over-specified exactly one cancellation request. The existing safety cleanup correctly retried known-ID cancellation on the next blocked window. Corrected the new fixture to emit its fill once and assert all cancels remain scoped to the owned ID; actual placement count remains exactly one. No existing tests or scheduling behavior were changed to obtain PASS.

Existing deselection remains justified: `test_cli_conflict_stops_before_settlement_preflight` reaches protected workspace dotenv before its expected ConfigAliasError. No credential protection was relaxed. Deselected is not skipped/passed.

Environment: existing Python 3.12.14 venv. Unchanged offline runner clears inherited environment before imports, denies socket/DNS and workspace credential reads, runs from a temporary CWD and disables plugin autoload/pytest cache; `-B` disables bytecode writes. Fake HTTP handles official SDK requests locally. No external API, RPC, authenticated live request or real order occurred. Fake evidence proves local control/economics for these schedules; actual live venue completeness, funds netting and storage behavior remain NOT_VERIFIED.

### Existing protection results

| Protection | Actual selected assertions | Outcome |
|---|---|---|
| M2-H02 | Slow multi-page reads follow DELETE; deadline cancellation overtakes held second-leg reads; worker/tasks drain; cutoff prevents new POST | PASS; scheduling unchanged |
| M0-H01 | Terminal then contradictory LIVE invalidates proof and prevents new POST | CONTAINED |
| M0-H02 | Late/status-only/cross-order confirmed facts invalidate old COMPLETE; current official set and inventory dedup required | CONTAINED |
| M0-H03 | First/second UNKNOWN retains maxima, prevents retries/next window; cleanup recovers known fills | CONTAINED |
| $30 / pending cost | Existing confirmed-cost, exact-$30, UNKNOWN and partial-cancel cases | PASS |
| M1/FL | All selected tests pass; shared core unchanged | PASS for selected scope |

## 10. MicroLive Impact

| Issue | Evidence / impact | Blocker | Decision |
|---|---|---|---|
| P01 disappearing partial LIVE observation | New real-path tests retain the row and prevent all placements through later empty reads | Remediated in Worker code; independent acceptance pending | FIX NOW, implemented |
| P02 disappearing partial MATCHED evidence | New real-path tests retain leg/economic evidence, remain blocked and never release via empty replicas | Remediated in Worker code; independent acceptance pending | FIX NOW, implemented |
| Incomplete collection stops the same executor even after later successful reads | Persistent conservative latch prevents account-risk reuse; no automatic recovery promised | No new funds-safety blocker; manual handling required | DEFER automatic clearance/recovery |
| Live official completeness/netting/storage evidence unavailable | Offline tests verify local behavior; live verification remains future preparation, not claimed here | Must be addressed in authorized MicroLive preparation | DEFER; no live testing this round |

No new funds-safety finding was observed in these selected tests. No broader account/capital architecture was introduced.

## 11. Remaining Type 2 Blocker

Unchanged preceding audit disposition: selected configuration Type2; signer/funder different; FL has Type2 SDK capability, but current SM is EOA-only and current account authority remains NOT_VERIFIED. The actual Type2 account remains **UNSUPPORTED** by SM and is a MicroLive compatibility blocker. No account adaptation, shared signing change, secret inspection or fresh account verification in this task.

Automatic recovery, general Proxy support and previously deferred unrelated work retain their dispositions. This pagination fix grants no MicroLive authorization and does not start M3.

## 12. Change Manifest

- Modified existing untracked production SM adapter: `src/polymaker/rdp_sm_execution.py`.
- Created focused tests: `tests/test_rdp_sm_m2_h01_pagination.py`.
- Created this report: `docs/development/RDP_SM_M2_H01_PAGINATION_FIX.md`.
- **Shared production core changes: NO.** Gateway, Tracker, FillTruth, Journal, Engine, FL strategy/signing and M1 source/CLI unchanged during this task.
- Entry hashes of 162 files under src/tests/docs/config/livecfg were compared. Only the SM execution source changed; only the new test and this report were created. No files deleted. Prior Worker/reviewer reports and all existing tests preserved.
- Final status (existing CLI modification predates this task; docs directory includes preserved prior untracked artifacts):

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

## 13. Final Worker Result

**CODE_READY**

Both failed-pagination counterexamples are eliminated in actual SM/Gateway execution tests. Already observed order/trade evidence persists, later empty replies cannot authorize risk release, incomplete account evidence blocks new placement, normal complete-query/release paths remain usable and selected safety/FL regressions pass. This is Worker evidence, not Independent Reviewer PASS or MicroLive readiness.

Next action: **Independent Targeted Review**.
