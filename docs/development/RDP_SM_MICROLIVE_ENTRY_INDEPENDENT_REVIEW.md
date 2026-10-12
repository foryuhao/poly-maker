# RDP-SM MicroLive Entry Independent Review

## 1. Reviewer decision

**INDEPENDENT_REVIEW = PASS** for the final dedicated entry and its narrowly scoped production composition.

The first review found a reproducible market-ambiguity blocker and returned FAIL to the Worker. The Worker supplied a minimal fix. Independent re-execution of the original counterexample and the final targeted suite verified that the blocker is closed. No remaining safety blocker was found in this review scope.

This is a pre-live source and offline integration review. It does not authorize trading, establish actual Safe signing permissions, or attest current live account balances, positions, order acceptance or latency. The separate Worker readiness assessment must supply current official account evidence. No live CLI or online account diagnostic was run by the Reviewer.

## 2. Baseline and independence

- Date: 2026-10-12.
- Branch: `fl-restored-97ab0f9`.
- HEAD: `c9d52ad8efc5a49a7d36dd05958bf250cef802ce`.
- SOP: RDP-SM v1.8, frozen; FL-first / thin SM orchestration.
- Existing tracked CLI modifications and untracked accepted SM source/tests/reports were preserved.
- The Reviewer ran in a separate review agent, inspected source and executed its own tests and counterexample. Worker test claims were not substituted for independent evidence.
- Scope: current market selection, time window, dedicated CLI integration, identity handoff, capital and close-barrier preservation, historical MATCHED policy, single-market exit and default command safety.
- No broad redevelopment or renewed whole-engine audit of accepted M1/M2/M3 was performed. No history branches, real credentials, RPC or third-party interfaces were accessed.
- Reviewer modifications: only this new report. The Worker independently made the remediation; the Reviewer did not modify production code or tests.

## 3. Initial FAIL: dynamic market ambiguity

Initial discovery used `exact_market(..., limit=2)` and rejected multiple results. However, subsequent production M1 and M2 snapshots called the shared `GammaClient.market_by_slug`, which requests `limit=1` and selects the first matching row. The expected market check compared only that selected row's condition/token identity.

Independent minimal reproduction:

1. Use the actual production entry, M1/M3/M2, Gateway, Tracker and FillTruth with the existing public dummy-key HTTP fixture and offline network/credential guards.
2. Return one valid current-market row on the first Gamma read.
3. On every subsequent Gamma read, return `[original_row, conflicting_row]`; the second row has the same slug and a different condition ID. Keep all other fixture responses unchanged.
4. Run `run_current_market` with fixture clocks; finish after one quote window.

Observed before remediation:

```text
INDEPENDENT CONFLICT DIAGNOSTIC:
COMPLETED MARKET_CUTOFF mock placements=2 Gamma reads=8
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

This violated the required exit-without-trading behavior for conflicting official market responses. The Reviewer reported FAIL and requested a minimal SM-side read hook, without modifying code.

## 4. Worker remediation and independent re-review

The final `_Reads.market` method preserves the existing Gamma behavior for ordinary M1/offline composition (`rdp_sm.py:216`). Production `_ExecutionReads.market` uses the existing strict `exact_market` function when the dedicated production account policy is present (`rdp_sm_execution.py:331`). M1 and M2 now use that reader method (`rdp_sm.py:424`, `rdp_sm_execution.py:613`). The account-policy reference is retained across startup and executor handoff.

This leaves the shared FL Gamma implementation unchanged and checks uniqueness during discovery, production preflight, loop snapshots and each M2 pre-submit snapshot.

Independent re-execution of the same original counterexample on the final source:

```text
INDEPENDENT CONFLICT DIAGNOSTIC:
BLOCKED M1_SESSION_NOT_READY mock placements=0 Gamma reads=2
M0 offline guard: 0 network/DNS attempts; workspace credentials denied
```

The final suite independently passed all three added dynamic-ambiguity cases: conflict on read 2 (M1), read 4 (loop snapshot), and read 7 (before second BUY). The first two have zero mock placements; the last retains and cancels the first known order, blocks the second BUY and reports BLOCKED. No live order remains in the fixture.

**Initial ambiguity finding: CLOSED.**

## 5. Targeted findings

| Required boundary | Evidence | Verdict |
|---|---|---|
| Current market identity | Official CLOB time determines the current 300-second epoch; exact Gamma result, strict slug/type/end/mapping/token/tradability checks and independent dynamic ticks; no next-epoch selector | PASS |
| Reliable startup window | Entry checks `15 <= elapsed < 240`; T+10/T+250 exit without waits, authentication or placement; freshness/epoch contradiction fails closed | PASS |
| Production call chain | `cli.py:62` → `run_current_market` → production M1/M3 → `SMWindowExecutor.open`/`execute_window` → existing Gateway | PASS |
| No execution bypass | Entry and CLI never call Gateway.place; real submission remains solely inside existing M2, with Maker/Post-only BUY, signed economics and pre-submit guard | PASS |
| Type 2 | `_execution_identity` checks key/SDK/Gateway signer, configured/SDK/Gateway funder, signature mode and deterministic Safe mapping; mismatch blocks; signed mock payload preserves mode 2 and Safe maker | PASS |
| Capital and observed risk | Existing $30 committed-capital guards and retained order/trade observations remain; historical reserve is also subtracted in each production snapshot | PASS |
| Close barrier | M3 requires M2 safe-to-continue and rechecks close before another window; cutoff and abnormal paths perform necessary cancel/reconciliation; unresolved closure remains INCOMPLETE | PASS |
| Cutoff / one market | Near T+240 cannot open an unsafe window; orders have the existing 3-second window plus shutdown margin; fixed slug/session and one-shot loop; no next-market scheduling or restart | PASS |
| Default command isolation | Help/import do not initiate reads or writes; read-only preflight cannot POST/DELETE; ordinary `run()` remains offline/default-blocked | PASS |

The unchanged NORMAL/REBALANCE quote logic was inspected only to verify integration preservation. No authorization check was added inside the 3-second fill window, no new execution engine or capital gate was introduced, and no automatic background trading task was found.

## 6. Historical MATCHED policy

The policy is conservative and does not treat MATCHED as confirmed inventory or redeemed funds (`rdp_sm_entry.py:85`). Non-owned pending evidence must have valid trade/market/timestamp/account BUY attribution, a uniquely identified unrelated market officially closed/resolved/not accepting orders, an old end time, and distinct verified token mapping. Current-market pending records and unresolved other-market records block.

Related present positions, live orders or retained observed-order evidence also block. Existing sticky pagination failures and observed order/fill risk still enter the accepted M2 mechanism. An omitted later trade snapshot does not remove the pending record. Even a subsequent status update does not release the already retained pending cost.

The complete known pending BUY cost is reserved under `historical_pending_cost_reserved_usd` in M1 and M2 (`rdp_sm.py:372`, `rdp_sm_execution.py:721`). The original trade status is retained and `redeem=NOT_VERIFIED` is recorded. No state is rewritten to CONFIRMED. Unreadable old related orders are not claimed terminal or redeemed: the reserve remains, and any independently observed order commitment remains subject to M2's existing stricter rules.

This bounds the known matched cash risk without demanding label-based rejection. If attribution, economics, market resolution, funds, pagination or existing observed commitments cannot be trusted, the path fails closed. Insufficient collateral after the retained reserve is independently tested and denies all BUYs.

**Historical policy integration: PASS.** Actual current account observations belong to the separate readiness diagnostic, not this offline verdict.

## 7. Journal additions

The additions are records in the existing journal: starting spendable balance/test limit, preserved historical pending cost, capital committed at placement intent, cancel request time/duration and window close monotonic time. Existing confirmed-fill identity/economics/time records and final result remain available.

The final targeted R14 test independently reconstructed capital utilization, order fill rate, active cycles, quote lifetime, close/reconciliation timing, confirmed inventory, multi-window residual exposure duration, completed pairs and theoretical pair economics. Pending submissions do not become confirmed inventory. The test's exposure lasts six seconds across three quote windows; it is not assumed to last only the three-second quote window.

No reporting framework, database, dashboard, settlement calculation or strategy decision was added. These records supplement existing intent/cancel/result journaling.

## 8. Independent tests

Every test command used this bootstrap, which denies real sockets/DNS and workspace credential reads, clears inherited credentials, disables plugin autoload/cache and runs in an isolated temporary directory. Only HTTP/clocks/dummy configuration boundaries were replaced.

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -c "import tempfile,runpy,sys,os; tempfile.tempdir='/dev/shm'; sys.path.insert(0,os.path.abspath('tests')); runpy.run_path('tests/rdp_sm_m0_offline.py',run_name='__main__')" <tests below> -q --tb=short
```

| Suite arguments | Executed | Passed | Failed | Skipped | Deselected | Result |
|---|---:|---:|---:|---:|---:|---|
| Final `tests/test_rdp_sm_entry.py tests/test_rdp_sm_type2_account.py tests/test_rdp_sm_m3_h01_handoff.py` | 43 | 43 | 0 | 0 | 0 | 45.16s |
| `tests/test_rdp_sm_m2_h01_commitment.py tests/test_rdp_sm_m2_h01_pagination.py tests/test_rdp_sm_m2_remediation.py tests/test_rdp_sm_m2_execution.py` | 92 | 92 | 0 | 0 | 0 | 13.98s |
| `tests/test_rdp_sm_m3_loop.py` | 22 | 22 | 0 | 0 | 0 | 71.88s |
| **Distinct independently executed cases** | **157** | **157** | **0** | **0** | **0** | |

The initial 40-case targeted run passed in 37.70s before the ambiguity remediation. Its cases overlap the final 43-case run and are not counted twice. The existing M2/M3 regression runs preceded the final hook verification; the final 43 cases exercise the changed production hook, identity/risk handoff, real CLI, capital, close barrier and default command isolation. No M2/M3 execution or strategy logic changed during remediation.

The two independent stdin diagnostics (before/after remediation) are separate from pytest counts. The first deliberately reproduced the unsafe behavior; the second asserted BLOCKED and zero mock placements.

All runs explicitly reported **0 network/DNS attempts; workspace credentials denied**. Real order POSTs, real cancels and external network calls: **0**.

Additional independent checks: scoped Ruff check **PASS**, `git diff --check` **PASS**, quiet diff of shared Gamma/Gateway/execution/Tracker/state/Journal/config/Engine/favorite_live against HEAD **PASS**. Broader FL regression results are supplied in the Worker readiness report; they are not presented here as independently re-executed.

## 9. Reviewed-source identity

SHA256 of the final reviewed production entry files and targeted test:

```text
fca8399b042376e11a93c0bce514b161b6c6732c29271ab7912c77c86a9ce0d5  src/polymaker/cli.py
6427958e877b1279b6c3eb7a13e7a18f0d0eb0673a846783b93208fb9cb2b3bb  src/polymaker/rdp_sm.py
887fbd568c4d79dba2c6e31d7eecb0c12b8cafa74876b9d5de0f60039d16db4e  src/polymaker/rdp_sm_entry.py
dfc2bc6793cbef2ca69be002a23d429847d34ce2f76ac7aab19cd4ab408f9fc8  src/polymaker/rdp_sm_execution.py
f37bdeeee4463e08e4410396a2f18055c2559f12e2049027ba27a1404e0a2ce2  src/polymaker/rdp_sm_loop.py
1c3825215449446c7704a1f664ebc076bef503d2b9f9914434887ed854d6c403  tests/test_rdp_sm_entry.py
```

## 10. Next action

Worker may complete the separate current-account readiness assessment and release-content sanitization. GitHub push remains conditional on `MICROLIVE_READY=YES` as well as this targeted review PASS. No trading was authorized or executed by this review.
