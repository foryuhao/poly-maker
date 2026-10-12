# RDP-SM M3 — Trading Loop

## 1. Baseline

- Date: 2026-10-12, Asia/Shanghai.
- Project: `/home/ubuntu/poly-maker`.
- Branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- SOP: RDP-SM v1.8 FROZEN. No landed v1.8 document found; the complete frozen strategy/execution contract in this M3 prompt is authoritative. No older strategy document substituted.
- M1/F01 and M2 accepted per Architect instructions; H01/H02 closed; M0-H01/H02/H03 contained. Historical account report remains unchanged; this prompt explicitly permits Type2 offline development with live authority unverified.
- Entry Git status: preserved prior CLI addition, untracked SM sources/tests/reports; staged diff empty. No checkout/reset/cleanup, historical RDP branch inspection or SOP changes.
- Read the required M1, M2, pagination independent review and Type2 account reports and inspected actual shared readers/execution/test conventions.
- MicroLive authorization: **NO**. No real credentials read, real account request, order/cancel or RPC occurred in this task.

## 2. Objective

Deliver a real single-market M1 → M3 → M2 execution composition for NORMAL/REBALANCE, confirmed-inventory size skew, continuous safe windows, cutoff closeout and minimal run summary, including the specifically authorized local Type2 compatibility adaptation.

Excluded: new Gateway/executor/FillTruth/capital ledger, generic Engine, new external interfaces, multi-market scheduling, recovery framework, SELL/Taker, Merge/Redeem/settlement, prediction/filtering, M4 dashboard/reporting and real trading. No independent Reviewer started by Worker.

## 3. Type 2 Minimal Adaptation

`src/polymaker/rdp_sm_execution.py:310` adds the local `_execution_identity` check, invoked at executor startup (`:458`) and every execution snapshot (`:580`), and reused by M3's preflight admission.

- Derive signer from the **current configured key**, not an assumed wallet-address alias.
- Require configured chain137, SDK signer = Gateway signer = key-derived signer, configured funder = SDK builder funder = Gateway funder, SDK signature type = configured signature type.
- Type0 retains signer=funder and reports `LOCAL_EOA_KEY_VERIFIED`.
- Type2 requires distinct signer/funder and exact equality between configured funder and the installed official SDK's deterministic Safe derivation using its chain factory config. This is a pure local SDK operation, not a relayer/RPC request.
- Other modes and mismatches fail closed. The actual signed POST additionally checks `signatureType` agrees with the builder; existing maker/signer/token/side/exact-economics checks remain.
- Session records and M3 results mark Type2 **NOT_VERIFIED_FOR_LIVE**. Neither deterministic derivation nor a dummy-key offline signature establishes current Safe ownership/threshold.

Existing FL SDK construction, configured signer/funder separation, balance/allowance signature-mode query, funder positions and authenticated orders are reused unchanged. No shared signing/Gateway core modification. New tests assert actual Type2 signed `maker=derived Safe`, `signer=key address`, `signatureType=2`, official-read mode/funder parameters, Type0 execution and blocking after key/funder/mode mismatch.

The previous [REDACTED_ACCOUNT_AMOUNT] USDC, zero-order and zero-position read is **historical**, not reused as fresh readiness. This round did not query the real account. The previous TYPE2_BLOCKED report is historical evidence, preserved; the new prompt changes the offline-development disposition only.

## 4. Architecture

Actual production path:

```text
SMTradingLoop.run (default BLOCKED; explicit offline boundary required)
  -> run_preflight with existing _ExecutionReads
     -> M1 official metadata / books / tick / account / clean market / time
     -> local Type0 or derived-Safe identity check
     -> wait before T+15 without orders
  -> real SMWindowExecutor.open (M1 startup recheck, Journal ownership)
  -> loop:
     -> real M2.verify_close + managed _snapshot
     -> production M2.confirmed inventory -> NORMAL / REBALANCE -> make_quotes
     -> real M2.execute_window with quote-evidence revision
        -> existing Gateway.place(strict, postOnly) -> real SDK
        -> M2 deadline / cancellation / order terminal / FillTruth / Tracker
        -> M2 confirmed inventory / capital / Close Barrier
     -> SAFE permits next window; BLOCKED stops
  -> real M2.verify_close(cancel=True)
  -> summary + existing Journal -> close resources -> STOP
```

New strategy code: `src/polymaker/rdp_sm_loop.py`; interfaces `make_quotes`, `RunResult`, `SMTradingLoop.run`. Only two strategy labels: NORMAL and REBALANCE. Execution statuses are separate result facts.

Uses existing GammaClient/market parser, M1 book/OrderBook/tick readers and account composition, M2 executor/Gateway/Tracker/StateStore/FillTruth/Journal. No direct M3 placement/cancellation/trade-reconciliation implementation. Existing official HTTP paths/hosts unchanged; no new WS/API/RPC/data source.

Entry example: `await SMTradingLoop(cfg, slug, session_id).run()` returns BLOCKED with `M3_LIVE_EXECUTION_NOT_AUTHORIZED` before reads/initialization. This task's executable entry is `run(offline=True)`, which also requires actual `httpx.MockTransport` on **both** SDK and async HTTP clients. A boolean alone cannot enable real HTTP. No new CLI or real-mode option was added; existing FL/M1 CLI remains unchanged.

Future authorized MicroLive can reuse the same loop/executor composition after a separately approved live authorization gate. Current code does not offer a live bypass. Type2 live authority remains a preparation prerequisite.

## 5. NORMAL

- Condition: `abs(Delta) < 5`, with Decimal sums of actual M2 confirmed shares.
- YES price = latest official YES BestBid − YES Tick; NO similarly, without complement inference or hardcoded $0.01.
- Sizes: YES = 5 + max(−Delta,0); NO = 5 + max(Delta,0).
- Both quotes are Maker BUY for the bound market/window/tokens.
- Decimal arithmetic preserves literal tick/size calculations; do not round price or increase quantity to bypass restrictions. Enforce legal price range/grid, uncrossed ask, official minimum and the existing M2/SDK size precision .01; M2 revalidates with fresh data before actual placement.
- Known illegal quote: record a skipped quote and wait/re-read; no M2 placement call. Account/execution uncertainty is not downgraded to a skip.
- No pair-cost≤1 rule. A test submits a legal pair with sum-price above1 through actual M2.

## 6. REBALANCE

- Condition: `abs(Delta) >= 5`.
- Positive Delta: NO BUY only; negative Delta: YES BUY only.
- Size exactly abs(Delta), without upward rounding. Price is the latest legal, noncrossing BestBid, not BestBid−Tick.
- Off-grid/minimum/precision violations are recorded and skipped. Unknown state or M2 BLOCKED stops the run.
- After a no-fill **safe** window, read the latest book and quote again; no intra-window chasing, MaxRebalanceSec or STUCK state.
- At cutoff retain and report residual; no SELL/Taker or new rebalance after T+240.

## 7. Trading Loop

M1 startup before T+15 is permitted to wait only when all non-time checks passed. Dirty/unknown/account-insufficient checks cannot be ignored as an early-market wait. No executor/order state is opened before the eligible startup check.

For each candidate window, the loop requires the previous M2 barrier, performs current M2 official close/snapshot reads, computes state/quotes from confirmed inventory, validates them and calls the production executor. Quote IDs are unique within the single session. SDK account/time/tick/capital checks still happen inside M2 before each independent POST.

The accepted M2 revision also binds the M3 quote calculation: an optional `quote_revision` check at `rdp_sm_execution.py:1113` rejects a first new intent if official confirmation/evidence changes during M2's pre-submit recheck. Existing M2 callers default to None and retain their behavior. This is a small SM admission constraint, not a new executor, close barrier or capital system.

The regression injects a previously unseen official confirmed fill after M3 generates its second NORMAL quote but before real M2 reconciliation. M2 updates actual inventory and blocks the old quote (`STRATEGY_QUOTE_EVIDENCE_CHANGED`); only the original two POSTs exist. M3 stops instead of recomputing/retrying through a blocked result.

FillWindowSec default3/minimum3 remains controlled by M2's original T0/deadline. M3 uses the existing 15-second `CANCEL_BUFFER_S`: no candidate starts when elapsed+fill time+buffer≥240. Near cutoff it waits and re-reads until cutoff, without another quote. Cancellation/reconciliation can overrun the fill interval. No active-cycle hard target or next-market lookup.

## 8. Inventory

Only `executor.confirmed()` supplies the strategy shares/costs: actual applied, non-quarantined, owned confirmed economic legs. M3 does not use submitted/ACK/MATCHED/MINED quantities or maintain an independent fill ledger.

Every summary is a **projection of the cumulative M2 set**, not an increment of cumulative window quantities. Trade/order identity dedup stays in production Tracker/FillTruth. Late confirmation may update the summary while the run remains blocked; it cannot authorize another window using old proof/quotes.

ActiveCycles counts distinct original quote-window IDs with at least one confirmed owned fill, derived from M2 placement ownership. Multiple fills in one window count once; a late-confirmed old order is assigned to its original window, not the window that first observed it. Zero-fill windows count zero.

CompletedPairs=min(YES,NO), ResidualShares=abs(Delta). Each average cost uses confirmed quantity-weighted actual prices. PairPnL=CompletedPairs×(1−AvgYES−AvgNO), exactly the specified theoretical formula. With no pairs it is zero; absent reliable costs on actual paired inventory yield NOT_VERIFIED. It is not realized PnL, cash-plus-face-value profit, or settlement proceeds.

## 9. Capital Safety

M3 owns no capital ledger and never releases commitments. It submits fixed strategy requests only through M2. The existing executor checks liquid balance/allowance, retained historical/partial-page external observations, owned pending/UNKNOWN and $30 economic commitment before each submission.

In the $3-funds integration case, first $2.40 BUY is accepted, second is blocked by remaining spendable capital, first safely closes, and M3 stops. It does not turn M2 BLOCKED into a quote skip or submit another window even if final cleanup later succeeds.

## 10. Market Closeout

At cutoff or a blocked/error exit, call actual M2 `verify_close(cancel=True)`. No independent cancel list, terminal cache or FillTruth is created in M3. Required M2 operations retain their managed task/drain behavior before resources close.

- COMPLETED: required final closeout/reconciliation verified, normal cutoff reached; profitability is irrelevant.
- BLOCKED: a safety/admission failure stopped the run, but final known execution cleanup verifies safe. The original blocked window remains BLOCKED in raw results and never triggers retry.
- INCOMPLETE: final closeout cannot be proved, including pending trades, UNKNOWN, LIVE after cancel, fatal execution/journal state. Final barrier BLOCKED; no false SUCCESS.

A clean, time-ineligible startup at/after240 has no SM attempts and returns COMPLETED with `MARKET_CUTOFF_NO_EXECUTION`, only after full M1 non-time checks and local account mapping succeed. Default authorization failure does not initialize a session.

RunResult stores market/session/start/stop/reason, window/state/active/fill/block counts, cumulative confirmed shares/costs/pairs/residual/theoretical PnL, final barrier/status, live authority, actual window results and quote-skip reasons. M3 strategy decisions/quote results and final summary are written through the existing M2 durable Journal; M4 can consume these facts. No automatic recovery or market restart.

## 11. Full Offline Market Test

`test_m3_15_full_market_type2_real_execution_timeline` uses a public dummy Type2 signer and official-SDK-derived distinct Safe. M1, M2, M3, Gateway, SDK signing, Tracker/FillTruth/StateStore and Journal all execute. Only official HTTP/time/fill ordering is faked; no Fake M2.

The bound market has start0/end300. Observed, assertion-verified timeline:

| Market elapsed | Actual behavior | Confirmed YES/NO / Delta |
|---|---|---|
| 0–14 | M1 clean/readable but time-ineligible; no POST | 0 / 0 / 0 |
| 15 | NORMAL: YES5@.48, NO5@.479, two Maker POSTs | 0 / 0 / 0 at quote |
| 18 | Window1 cancel/official reconciliation confirms YES5 | 5 / 0 / +5 |
| 18 | REBALANCE: NO5@.48 only | 5 / 0 / +5 at quote |
| 21 | Window2 confirms NO3; returns to NORMAL | 5 / 3 / +2 |
| 21 | NORMAL size skew: YES5@.48, NO7@.479 | 5 / 3 / +2 at quote |
| 24 | Window3 confirms NO2; NORMAL resumes | 5 / 5 / 0 |
| 24–219 | Safe NORMAL windows continue; zero additional fills | 5 / 5 / 0 |
| 219 | Last new quotes, window69 | 5 / 5 / 0 |
| 222–239 | Existing fill/close buffer prevents new window; fresh reads only | 5 / 5 / 0 |
| 240 | Final M2 close/reconciliation; STOP | 5 / 5 / 0 |

Actual totals checked by passing assertions: **69 windows = 68 NORMAL + 1 REBALANCE; 137 Maker POSTs, all between T+15 and T+219; 3 ActiveCycles / 3 confirmed economic fills; all known orders CANCELED; final SAFE_TO_CONTINUE / COMPLETED.**

Confirmed YES cost $2.40 (avg.48); NO cost $2.398 (avg.4796). CompletedPairs5; residual0; theoretical PairPnL **0.202 USDC**. These are Fake-boundary evidence, not a real market profit claim. The market's final60 seconds contain no new trading; execution stops at240 rather than waiting for settlement at300.

## 12. Test Results

| Requirement | Actual test evidence | Result |
|---|---|---|
| M3-01/02 | Five quote-state cases: Delta0,+2,−3,+5,−5; exact bid/tick prices and quantities | PASS |
| M3-03/04 | Full real-path timeline NORMAL→REBALANCE→NORMAL on actual confirmed fills, including +2 skew | PASS |
| M3-05 | No-fill REBALANCE safely closes; next bid changes .48→.50; four total POSTs across three windows, no chase | PASS |
| M3-06 | MATCHED/MINED do not become inventory; one blocked window and no subsequent POST | PASS |
| M3-07 | Actual capital/pending/UNKNOWN/stale-quote results block the loop, no Fake M2 return values | PASS |
| M3-08 | T+14 waits to15; at240 no POST; full last quote219; M2 cutoff/delayed-ACK regressions | PASS |
| M3-09 | $3 liquid funds admits only first BUY; second/new windows blocked; existing $30 tests | PASS |
| M3-10 | Zero-fill A, one-fill B, multiple-fill C → ActiveCycles2; cumulative quantities2/2 | PASS |
| M3-11 | Full verified cutoff close; LIVE despite cancellation → INCOMPLETE; no false COMPLETE | PASS |
| M3-12 | Same-run concurrent invocation blocked without interfering with owner; metadata change stops after first window; all reads remain bound to one slug; existing account Journal lock/history tests | PASS |
| M3-13 | Official tick changes .01→.001; next NORMAL YES price .48→.489 | PASS |
| M3-14 | Actual Type2 signed maker/signer/mode and read parameters; wrong funder/key/builder/type blocked; Type0 regression | PASS |
| M3-15 | Complete start0/end300 market, trading cutoff240, 69 real executor windows and final inventory/statistics | PASS |
| M3-16 | M2-H01/H02 and M0 containments plus selected M1/FL | PASS |
| Extra safety | Known invalid quote skipped without rounding; pair sum>1 allowed; default/nonmock transport blocked before initialization; stale official inventory blocks old strategy quote | PASS |

Exact final commands:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m3_loop.py -q -s --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py -q --tb=short

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B tests/rdp_sm_m0_offline.py tests/test_rdp_sm_m1.py tests/test_rdp_sm_m1_f01_token_identity.py tests/test_execution.py tests/test_filltruth.py tests/test_state.py tests/test_userstream_parse.py tests/test_btc5m.py tests/test_catalog.py tests/test_orderbook.py tests/test_marketdata.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py -q -k 'not test_cli_conflict_stops_before_settlement_preflight' --tb=short

.venv/bin/ruff check --no-cache src/polymaker/rdp_sm_execution.py src/polymaker/rdp_sm_loop.py tests/test_rdp_sm_m3_loop.py tests/test_rdp_sm_type2_account.py
.venv/bin/ruff format --check --no-cache src/polymaker/rdp_sm_execution.py src/polymaker/rdp_sm_loop.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m3_loop.py
git diff --check
git diff --quiet -- src/polymaker/execution src/polymaker/state src/polymaker/journal.py src/polymaker/config.py src/polymaker/engine.py src/polymaker/favorite_live.py
```

Final outputs:

```text
M3: 22 passed in 98.70s
Type2/M2: 99 passed in 19.43s
M1/FL: 346 passed, 1 deselected in 9.18s
Each offline run: 0 network/DNS attempts; workspace credentials denied
Ruff/format/shared-core/whitespace checks: PASS
```

Distinct total **467 executed / 467 passed / 0 failed / 0 skipped; 1 deselected**. Type2/M2 includes 7 Type2 + 92 existing M2/remediation/commitment/pagination cases. Intermediate repeated runs are not added. Initial formatting/import lint issues were corrected; no production safety failure was bypassed and no tests were weakened to manufacture PASS.

Environment: existing Python3.12.14 venv and unchanged guarded offline runner. It clears inherited credentials before imports, denies actual socket/DNS/workspace credential reads, disables plugin autoload/cache, uses temporary CWD and `-B`. Only public dummy keys and MockTransport are used. Full `-s` run emits substantial existing reconciliation diagnostics; the table above condenses assertion-verified results. No external HTTP/WS/RPC, installation or real key/account operation this round.

## 13. FL Regression

Selected M1/F01: 64 cases; selected existing FL: 282 cases; all pass. Original shared Gateway, Tracker, FillTruth, Journal, Config, Engine and FL strategy source match baseline. CLI unchanged this round.

The unchanged exclusion `test_cli_conflict_stops_before_settlement_preflight` remains justified: guarded CLI dotenv discovery reaches protected workspace credentials before the expected ConfigAliasError. No protection relaxed or live credentials enabled; deselected is not skipped/passed.

M2-H01/H02 regressions retain all snapshot/pagination commitments, UNKNOWN maxima and deadline/cancellation priority. M0-H01/H02/H03 remain **CONTAINED**, not relabelled universally closed. No original M0 harness or historical report modified.

## 14. MicroLive Impact Assessment

| Issue | Actual evidence / impact | Decision |
|---|---|---|
| EOA-only constraint prevented configured Type2 composition | Verified local key/SDK/funder/mode/Safe derivation and real dummy Type2 signed execution now pass | FIX NOW — SM-only compatibility implemented |
| New confirmed facts between quote generation and M2 submit can invalidate size/state | Actual official late-fill integration test blocks old quote before new intent/POST; inventory retained | FIX NOW — optional existing-revision quote binding implemented |
| Current Type2 Safe permissions not live-verified | No current owner/threshold evidence; marked NOT_VERIFIED_FOR_LIVE, not forged or inferred from historical balance | DEFER to authorized MicroLive preparation; not an offline M3 blocker |
| Real execution entry must stay unauthorized | Default blocked before initialization; offline flag also requires Fake transports | FIX NOW — enforced; live gate still requires later authorization |

No remaining reproducible M3 code-safety blocker in the selected tests. No proposed generic framework or speculative optimization is required for acceptance.

## 15. Deferred Issues

- Current Type2 live authority and fresh real official account/interface verification; old balance/inventory cannot authorize a future launch.
- Explicit MicroLive authorization/live-entry gate remains absent by design. M3 CODE_READY is not MicroLive readiness.
- Existing live venue completeness/netting and physical storage verification limits retain their disposition.
- Merge/Redeem/settlement, realized PnL and M4 full reporting remain excluded.
- Automatic restart recovery/general Proxy/multi-market behavior remain excluded; no new execution path is promised for them.

## 16. Change Manifest

Created:

- `src/polymaker/rdp_sm_loop.py`.
- `tests/test_rdp_sm_m3_loop.py`.
- `docs/development/RDP_SM_M3_TRADING_LOOP.md`.

Modified existing SM files:

- `src/polymaker/rdp_sm_execution.py`: replace EOA-only checks with strict Type0/derived-Safe identity validation; check actual signed mode; optional quote-revision admission check. Existing placement, capital ledger, deadline, cancel, reconciliation and close architecture remain.
- `tests/test_rdp_sm_type2_account.py`: update the prior authority-blocked expectation to the explicitly authorized offline Type2 policy; verify real Type2 signed path and wrong-identity blocking; add three current-configuration/SDK mutation cases.

Shared core changes: **NO**. Protected entry hashes for 167 files confirm only the two listed existing SM files changed and the three listed files were created; no file deleted. All M0/M1/M2/Type2 historical reports, prior M2 tests, M1 source, config and CLI preserved. No commit or staged change.

Final Git status:

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

The tracked CLI addition predates this task; SM files remain untracked from prior milestones and must not be overlooked in review.

## 17. Worker Decision

**M3_CODE_READY**

Real M1/M2/M3 composition and strict offline Type2 compatibility work; frozen strategy, confirmed inventory/state/active-cycle/cost semantics, cutoff, closeout and capital are tested through actual production execution components. Full market and related safety/FL regressions pass. No real order or unauthorized shared-core change.

This is Worker evidence/self-check, not an Independent Reviewer PASS, MicroLive readiness or profitability validation.

## 18. Next Action

**Independent M3 Review.** Worker has not started that review, M4 or MicroLive.
