# ITER-0028 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 1. MUST_FIX = 2.

The actual repeated NORMAL happy path operates through the existing SDK/lifecycle machinery, and the repository regression suite passes. Independent subsequent-attempt probes nevertheless reproduce three ordinary implementation defects: a reported terminal clock failure permits later new risk; final continuation inventory/cost can be stale or omitted; and a reliable no-exposure quote invalidation invents STOP.

**Full DG-003 acceptance is not closed. DG-003 remains OPEN and ITER-0028 is NOT_READY.** DG-002 and DG-004 remain OPEN. MICRO_LIVE_READY remains NO. No real-money or main-merge authorization is granted. These findings require Worker correction within the approved contract, not strategy or architecture redesign.

## Reviewed Inputs

- Actual AGENTS.md, Frozen RDP_PHASE0_FROZEN.md, BASELINE.md, ITERATION_INDEX.md and DELIVERY_GAPS.md.
- ITER-0028/00_INITIALIZATION.md, frozen01_ARCHITECT.md and immutable02_WORKER.md.
- Relevant ITER-0027 Architect/Worker/Reviewer04/RESULT/archive evidence; its PASS closes only the factual-admission prerequisite.
- Actual five-file source/test diff and relevant existing initial admission, submission/SDK guards, placement ownership, pre-fill, FillWindow, cancellation/reconciliation, terminal cleanup, capital/accounting, quote builders, official account parser/reads and market-data tick provenance.
- Current tracked, staged, untracked and ignored evidence scope. Worker_evidence contains 18 preserved logs; the Worker discloses its exploratory failures and unrecoverable earliest 31-failure node-ID/traceback evidence. Its final suite is independently rerun below.

Direct input SHA256:

| Input | SHA256 |
|---|---|
| AGENTS.md | `0dedaab0b6653bd030d01c494d42d10619727f0e9f78bed9703002816538aa25` |
| Frozen SOP | `92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d` |
| ITER-0028 Architect01 | `6e02585e44c31502264b5bc657ac69cc2abfec60b5cadc5bac4b8df580aba066` |
| ITER-0028 Worker02 | `13dfdcd624e596c2803a16b36c49166132c04ec9937ae4feef7ad10838a92c01` |

## Repository / Diff Reviewed

Repository `/home/ubuntu/poly-maker`; branch `rdp-phase0-dev`; base/HEAD `ec6c3af559095fa911a14a7008d9a8ebeab70487`. Current HEAD is the frozen ITER-0028 contract, with the implementation uncommitted. Recent graph and current branch/status were inspected directly.

Five authorized implementation/test files: **1150 additions / 53 deletions**.

| File | Additions / deletions | Exact reviewed SHA256 |
|---|---|---|
| src/polymaker/rdp_runtime.py | 681 / 37 | `74ef8ce114026672a50af67f7766fed752a3fc6e396b8c579f808d385f7e12fe` |
| src/polymaker/execution/rdp_placement.py | 138 / 10 | `b5fb6bb7fcdfa2ecc041c1e61bf9933438da95e6801e5109f1239312a2e6db82` |
| src/polymaker/risk/rdp_capital.py | 14 / 6 | `cb93eff1b4f4c534f48f169303ad893ba69b9e96b1be02ca0b623b79e1984316` |
| tests/test_rdp_capital.py | 32 / 0 | `685929358f99e5e7a44bbc98e730070e8472370d3429abb2f7b4340f1bb42566` |
| tests/test_rdp_placement.py | 285 / 0 | `0d7de5080b9d8a996228e73cdc8ae06bb6bca75cb131d4dadbf0da47e45796b7` |

Hashes remained unchanged after independent verification. Additional tracked changes are Orchestrator-only index/gap REVIEWER-stage narrative (four added/four removed lines), without a closure claim. Untracked scope consists of Worker02 and the preserved 748-file research_outputs tree. The 18 ignored worker_evidence logs are audit evidence, not runtime code. No unexpected staged, protected, configuration, dependency or implementation change was found. `git diff --check` passed.

Reviewer did not edit repository code, tests, documents or Git. All additional probes/logs and this report were created under `/tmp`; older specialist artifacts remain unchanged.

## Acceptance Criteria Review

| Contract criterion | Review conclusion |
|---|---|
| 1. Integrated repeated delivery | Supported on happy path. One call operates three subsequent NORMAL fake-SDK cycles in repository integration tests; subsequent attempts are not manually armed/resolved/closed by the caller. |
| 2. Fractional skew/cumulative accounting | Supported for eligible sends/completed cycles. Fractional skew, third-cycle cumulative inventory/cost, admissions, model gates and current backing are recorded. Final failure evidence is deficient under M1. |
| 3. Multiple closure proofs | Supported by actual-diff inspection, full-suite projection tests and runtime proof retention. Every proof passes the existing validator independently; overlap, incomplete proofs, stale inventory, local resurrection and insufficient covered absence reject. Current resting reservation plus confirmed partial cost is independently exercised by the new projector test. Release reprojects the complete cumulative scope while separately checking the current barrier's inventory. No aggregate close result is manufactured. |
| 4. Actual dynamic tick | Supported. Repository integration sends an official-format selected-market tick change through existing processing, warms the required filter, signs subsequent orders with .005 while retaining bootstrap facts, and denies a further post-sign tick change. Existing invalid tick/metadata/minimum behavior and independent later minimum mutation deny POST. M2 still affects the clean no-send recovery semantics. |
| 5. Ordering | Supported. Independent held later cancellation blocks the next POST; a confirmed fill arriving during held cancellation is retained before replacement. Only current registered order IDs are canceled, with no prior/foreign scope. Repository tests establish cancellation/trade reconciliation between every successive POST. |
| 6. Per-attempt resting/window | Supported by direct independent subsequent-path tests. A zero-fill pair rests beyond four virtual seconds with no cancel and no started window despite old-cycle confirmations; healthy one-tick invalidation closes before automatic requote. A new confirmation starts its own original-receipt three-second deadline, and ordinary movement during that started window causes no premature cancel/reprice. |
| 7. Current gates | Supported for new-risk denial/accounting. Independent changes after signing to cash, allowance, positions, timestamp, generation, backlog, stale book and minimum all deny POST. Physical cash/allowance100 does not replenish model30 or waive reserve5. The full suite covers projector/backing reserve and freshness boundaries. Reliable no-send rejection is incorrectly turned into STOP under M2. |
| 8. Strict outcome/STOP | **Not fully satisfied.** Independent subsequent typed partial and unknown/error outcomes latch protection, avoid retries/speculative cancellation, preserve uncertainty and retain cumulative inventory in terminal cleanup. Real-executor complete-ack deadline interruption drains and closes. Their outer continuation evidence can still forget confirmed quantities/cost under M1. |
| 9. Both REBALANCE handoffs | Supported on completed reliable paths. Actual cumulative +5 and -5 end after current closure with full cumulative inventory and no additional dual NORMAL or REBALANCE POST. Independent late-cancel fill scenario also completes at -5. DG-004 operating execution remains absent and OPEN. |
| 10. Time/concurrency/drain | **Not fully satisfied.** Non-boolean/default authorization has zero I/O; held-close conflict/manual calls, cancellation, teardown, deadline/drain are directly checked. B1 permits later new risk after a reported terminal clock failure. M2 incorrectly makes ordinary reliable intent invalidation terminal. |
| 11. Compatibility/scope | Regression and scope checks pass. Initial cached results, DG-001/DG-006, FL and shared tests pass on the actual full tree. No excluded subsystem change. This does not waive the independently reproduced defects. |

This contract seeks full DG-003 delivery closure, not a helper prerequisite. Happy-path delivery plus a passing inherited suite is insufficient while B1/M1/M2 remain. Required repairs and re-review must close those conditions before PASS or CODE_READY.

## Existing Codebase Compatibility

The implementation extends the existing owner with attempt history and independently retained close proofs; it reuses the same gateway placement/guard/executor path and the same pre-fill/window/close pipeline for subsequent attempts. The projector extension applies its existing close validator to each proof and rejects overlapping scope. No gateway, lifecycle, store/tracker, quote builder, CLI, configuration or dependency was replaced.

Worker did not create a duplicate execution subsystem or separate inventory/capital ledger. Original initial submission/cycle/window caches remain distinct from subsequent maps. Regression evidence supports compatibility. The defects below are in the new continuation's termination/result/recovery composition and require local correction rather than rebuilding reused components.

## Findings

### BLOCKER

**B1 — Reported terminal clock failure does not latch STOP and allows subsequent new-risk POSTs.**

Location: `src/polymaker/rdp_runtime.py:1140–1148` in `_run_normal_continuation`; `continue_normal` checks the STOP latch before a later call.

The clock/arithmetic exception branch returns `outcome="TERMINAL"`, `reason="OPERATION_CLOCK_FAILED:RuntimeError"`, but never invokes the existing terminal latch. The completed initial owner stays released and a later continuation can enter again.

Independent actual-runtime reproducer starts from a completed acknowledged initial fake cycle, installs a clock that raises RuntimeError, calls the authorized continuation and observes TERMINAL with zero POSTs and an unset latch. It then restores the clock and calls continuation again. The second call performs **two new SDK POSTs** and returns REBALANCE_HANDOFF. No fabricated caller inventory/close proof or manual later-cycle operation is used.

This violates the frozen terminal/no-later-new-risk invariant and the approved honest-terminal failure behavior. Fix terminal clock/evidence failure through the existing immutable STOP protection before returning. Keep a genuinely insufficient valid operational-time budget distinct from unreliable clock evidence; do not turn malformed/nonfinite clock evidence into a resumable safety outcome. No speculative cleanup is necessary when all attributable prior orders are already proven closed.

Mandatory regression: the failing clock-terminal probe, with recovery of the clock still producing zero later signing/POST and no owner release/new-risk re-entry after terminal failure. Preserve the sanitized primary clock cause.

### MUST_FIX

**M1 — Final continuation evidence reuses stale completed-cycle inventory/cost or drops known inventory when capital cannot certify.**

Location: `src/polymaker/rdp_runtime.py:1256–1271`, `_continuation_completion`; interrupted-COMPLETE flow at `1219–1222` and generic exception fallback at `1248–1253`.

The result selects the last completed cycle's capital projection, then reports that snapshot next to the current cumulative registered run IDs. It does not capture the current confirmed run evidence. If the latest cycle cannot certify capital, it reports no inventory even though terminal cleanup retained known confirmed inventory. Known confirmed economic evidence and complete capital certification are different authorities.

Three independently reproduced cases:

1. Subsequent strict two-row **partial** response registers one YES order and accepts its one-share confirmed fill. Scoped cleanup correctly cancels only `cycle-1-yes-token`; terminal confirmed QY is **2.25** including prior1.25. Outer continuation reports QY **1.25**, InventoryCost **.6875**, omitting the new .55 cost. Current confirmed cost is **1.2375**.
2. A late .25 YES fill on the prior closed order arrives during signing. The final guard correctly denies POST with **STALE_CLOSE_EVIDENCE** and processor QY is **1.5**, confirmed cost **.825**. Outer continuation reports QY **1.25**, cost **.6875** from the invalidated prior close snapshot.
3. A subsequent real-executor POST returns COMPLETE after its .08-second operation deadline. The SDK operation is drained and the registered current pair is safely closed. Terminal confirmed inventory is QY **1.25**, QN **1**, cost **1.0975**. Outer continuation reports QN **0**, cost **.6875**, and its reason becomes `CONTINUATION_FAILED:RDPPlacementError` after trying to start a cycle despite the already latched deadline termination.

Fix final known-run evidence using the existing authoritative confirmed processor/terminal inventory facilities, preserving attributable confirmed quantities and actual acquisition cost. Retain explicit known scope/uncertainty where complete evidence is unavailable. Do not adopt unregistered observations or account positions. Do not certify FreeCash/replacement or reuse a prior certified projection as current authority when close evidence is stale/unreliable. Historical immutable cycle projections may remain historical evidence. Preserve the primary terminal/interruption cause instead of generating an avoidable secondary cycle exception.

Mandatory regressions: the partial current-fill, late-prior-fill final-guard, interrupted-COMPLETE examples and unreliable close/teardown evidence, checking current quantities/cost, known scope and absence of false capital certification/release.

**M2 — Reliable no-exposure quote invalidation creates an invented STATE_UNRELIABLE STOP.**

Location: `src/polymaker/rdp_runtime.py:1202–1218`, subsequent submission result handling.

All non-time DENIED/NO_SEND/FAILURE_PRE_POST outcomes are made terminal. Independent probe changes the current YES bid by one tick after signing, with current reliable feed/closure/account evidence. Both SDK guards work: the second returns **CONTINUATION_QUOTE_INTENT_CHANGED**, POST remains unstarted, owner resolves clean **EMPTY**, unresolved=false, pending capture is discarded, and no orders/cancellations occurred. Nevertheless continuation latches **STATE_UNRELIABLE STOP**.

Frozen NORMAL behavior and the approved Desired Behavior explicitly disallow inventing a terminal strategy condition for reliable entry/filter/price rejection with no unreliable exposure. Such a changed intent is withheld, then current admission can be refreshed/waited/requoted. This is not authority to retry an ambiguous POST.

Fix the result/owner composition to distinguish authoritative clean no-risk rejection from uncertainty. Preserve typed no-send attempt history and prior authentic close provenance while re-admitting an eligible fresh NORMAL intent; do not reset the initial once-only state or erase cumulative history. Keep STOP for partial/unknown/unresolved/captured anomalies, actual interrupted exposure, scope corruption, stale/unreliable state and other genuine terminal failures. Do not decide retry solely from the outer wrapper string: a NO_SEND/EMPTY response can still have an independently unresolved captured owner anomaly and must remain terminal then. A validated dynamic-tick/quote/filter change may be a clean stale-intent rejection; unproven metadata/state is not.

Mandatory regression: the reliable one-tick post-sign quote movement must produce no stale POST, no invented STOP and eventually a fresh guarded continuation/honest handoff or valid time exhaustion. Also preserve the existing unresolved-owner/captured-fill NO_SEND failure matrix to prove that ambiguous exposure is never retried.

### NOTE

1. Worker openly records absent direct later-attempt fault/window/cancellation/held-close tests. Independent probes above fill the review evidence for this tree but do not replace permanent meaningful repair regressions.
2. Worker’s earliest exploratory `512 passed, 31 failed` output was not durably retained and its exact failure node IDs are unavailable. This evidence limitation remains disclosed; final independent current-tree regression is not inferred from that history. Preserve the 18 existing logs and do not rewrite Worker02.
3. Scratch harness development initially used incorrect result-field names, an incorrect partial response cardinality, a wrong SDK read interception, missing `apply_delta` arguments/Side type and a teardown cancellation-only expectation. Separate versions preserve those attempts. Only the corrected final probe files/results below establish findings; harness failures are not repository defects.

## Frozen SOP Compliance

The implemented successful path retains cumulative confirmed inventory, exact Frozen skew, NORMAL reserve, dynamic maker ticks, strict post-only, Cancel → Reconcile → next-action ordering and deficient-side handoff boundaries. It adds no REBALANCE POST, hard rebalance stop, quote/cycle quota or zero-fill timeout.

Compliance is incomplete: B1 allows new risk after a reported terminal failure; M2 invents STOP for a reliable no-exposure NORMAL intent change; M1 emits incorrect known inventory/cost evidence. Correcting these applies the existing Frozen semantics and approved contract. Frozen SOP and approved reserve/filter decisions remain unchanged.

## External Interface Review

No new external runtime interface or production dependency. Existing official HTTP/WS account/status/book/tick paths are reused. No blockchain/RPC/third-party runtime path was added. Every independent Python/project execution used the archived pre-import DNS/socket blocker. **All completed guarded commands reported `REVIEW_NETWORK_ATTEMPTS []`.** Signing, POST and cancellation counters refer exclusively to fake SDK clients; no real network/order/cancel occurred.

## Trading Safety Review

Subsequent partial/unknown and interrupted complete acknowledgments independently demonstrate the existing terminal pipeline’s registered-only cleanup, executor drain, no speculative zero-ID cancellation, no retry and continued replacement blocking. Held cancellation and teardown do not allow early replacement. Conflict calls cannot create another current cycle. Current guards reject late fill/current account/book/generation/metadata changes.

B1 remains a terminal re-entry defect. M2 remains an inappropriate STOP/recovery defect; correcting it must preserve the stricter independent unresolved-owner gate. All immutable factual/continuation results retain execution_authorized=false; execution authority is the deliberately authorized operation plus existing guards, never the admission object itself.

## Inventory / Capital Accounting Review

Successful sends preserve the same $30 allocation across prior and current IDs, confirmed cost and separately attributed resting reservations. Each retained proof validates its own covered inventory; cumulative mode/capital uses the union. The current cycle’s barrier is not misrepresented as an aggregate economic snapshot. Account BUY commitments are subtracted once from physical cash/allowance; higher observed cash100 neither replenishes model30 nor reduces reserve5. Selected account positions remain backing checks, not adoption.

M1 is a concrete accounting-evidence defect on failure outcomes. The safe terminal facility already preserves current cumulative confirmed inventory in the tested registered scope, while the outer result contradicts it. Repair must preserve exact confirmed acquisition cost without manufacturing capital reliability from stale/unresolved proof. No InventoryCost/LockedPnL/WorstSettlementPnL/MarketPnL definition is revised.

## State Machine Review

Successful NORMAL cycles use cumulative Delta, with both exact ±5 closed handoffs and no operating REBALANCE or extra dual POST. New attempt-specific windows/caches prevent old confirmation receipts from starting the next window. Valid zero-fill resting and ordinary started-window movement were exercised directly; healthy pre-fill invalidation closes/reconciles before automatic requote.

Terminal/recovery composition is not yet correct under B1/M2. The loop must distinguish reliable clean stale-intent rejection, valid insufficient operational time and genuine unreliable/ambiguous terminal state without inventing strategy transitions or allowing terminal re-entry.

## Test Review

Independent full exact-tree command, from `/home/ubuntu/poly-maker`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q > /tmp/iter0028_reviewer_full.log 2>&1
```

**1687 passed, 2 skipped in 90.16s**, zero network attempts. The two existing live-market-data tests are disabled by their default live gate; no offline implementation test was excluded. This run covers capital76, placement/runtime470, affected433, FL48 and the remaining repository regressions. Broad duplicated runs were unnecessary after exact-tree success.

Independent scratch commands use the same pre-import runner, prefix and `-q --tb=short --show-capture=no` unless specified:

| Scratch file / command arguments | Exact result |
|---|---|
| `/tmp/iter0028_reviewer_probes_v3.py` with `-q -s --tb=short` | **2 failed, 1 passed in 1.72s**; failures are M1 partial current-fill and late-prior-fill. Unknown POST exception safely passes. |
| `/tmp/iter0028_reviewer_lifecycle_v3.py -k 'held_subsequent_close or later_zero_fill or reliable_no_send or subsequent_complete'` | **2 failed, 4 passed, 3 deselected in 1.96s**; failures are M2 reliable no-send movement and M1 complete-ack deadline. Held late-fill/cancel/teardown and zero-fill/requote pass. |
| `/tmp/iter0028_reviewer_window_v2.py -k 'later_window or nonboolean or subsequent_guard_current'` | **13 passed, 9 deselected in 1.81s**; new receipt/ordinary movement, nonboolean authorization4 and post-sign current evidence mutations8. |
| `/tmp/iter0028_reviewer_terminal.py -k 'clock_failure_terminal or higher_current_cash'` | **1 failed, 1 passed, 22 deselected in 1.75s**; B1 clock failure/re-entry fails; high physical cash/model reserve passes. |
| `/tmp/iter0028_reviewer_conflicts.py` | **1 passed in 1.64s**; concurrent continuation/manual close/manual cycle/manual arming during held later close are blocked. |

There are **25 distinct corrected independent scenarios: 20 passed and 5 failed**, with the five failures mapping to B1 once, M1 three times and M2 once. Every completed command has zero network attempts. Isolated scratch runs emit the existing websockets legacy deprecation warning, unrelated to findings.

Original valid failing probes/logs are retained unchanged. The script/log pairs named below are the decisive reproduction evidence; scratch helper imports require their companion files. The full report path is `/tmp/ITER-0028-REVIEWER.md`; Orchestrator may copy files verbatim for durable evidence.

| Evidence | SHA256 |
|---|---|
| /tmp/iter0028_reviewer_probes_v3.py | `194500b1060c3c8a03ff245c8c6407b5012f32e5efc9dd6538f8bd803c644dac` |
| /tmp/iter0028_reviewer_probes_v3.log | `dfd1a5745fdc2cc70915ee83cda1b4cf82d50825dc557b0ea331026bfb900876` |
| /tmp/iter0028_reviewer_lifecycle_v3.py | `9c44f0aafe08b3327640ab1d9c4e6328837c5c2cea042a07a8315bd1907cfa67` |
| /tmp/iter0028_reviewer_lifecycle_v3.log | `bb525ba1f6cf17f134fa1eb7bb4e9a0d2fa2d8591d37a9063a3ad6463a3fd9ab` |
| /tmp/iter0028_reviewer_window_v2.py | `8a7c0b984fd557cdec8475291d4edb68cc5781203edc35f66daf7e40f6d7ea60` |
| /tmp/iter0028_reviewer_window_v2.log | `2c2f5c433b3b6cb1dbd175dee2852884a7c673bb1acf6bb79ffe3421e2fa8884` |
| /tmp/iter0028_reviewer_terminal.py | `098b19e4cde73e3ccb4b76cc1dd718f1625c7283538742e41458b90f33420c9b` |
| /tmp/iter0028_reviewer_terminal.log | `03894f0ce6b42b31b849856e899823d7778314d62ebc7f8d2e05d029bdcee3ec` |
| /tmp/iter0028_reviewer_conflicts.py | `98e7d568dd9bc4e95bc5109d0dfb1ecb98fe607fa5fdaf254359231c461f1b6f` |
| /tmp/iter0028_reviewer_conflicts.log | `fdf09feddbce457e64aaccc4f138a57bf001105ce1678f7d6595e24153977c3b` |
| /tmp/iter0028_reviewer_full.log | `8d42e1463c65fbc482f46a2d1e6550c616d3febd9e3d78b511ad684d5fab005d` |

Runner: `docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py`. It installs socket/DNS blockers before importing pytest/project and disables pytest cache writes. Scratch development-history files also remain available: `/tmp/iter0028_reviewer_probes.py`, `_probes_v2.py`, `_lifecycle.py`, `_lifecycle_v2.py`, `_window.py` and corresponding `.log` files. These earlier harness failures are disclosed as NOTE3 and do not establish additional implementation findings.

Tree checks: current status/branch/HEAD/graph, complete actual diff/numstat/name scope, relevant untracked and ignored evidence, direct SHA256 before/after verification and `git diff --check`. No current-tree source/test hash changed during review.

## Scope Review

The five implementation/test files are expressly authorized; no excluded gateway/lifecycle/tracker/store/quote/config/CLI/dependency change. Dynamic ticks are read from existing validated selected-market feed provenance rather than by rewriting bootstrap metadata. NORMAL continuation alone is implemented; no market rotation or operating REBALANCE. Necessary owner/proof/history additions stay local and reuse the existing pipeline. Prior audit artifacts and unrelated research data are preserved.

All three findings classify against existing Frozen/contract requirements and are repairable within current scope. No Architect revision, new external interface or human strategy decision is needed.

## Residual Risks

No live API or real-money behavior was tested or authorized. Sequential account/status reads may lag; guards must remain fail-closed. Full operating REBALANCE, complete STOP/time policy, market termination/resolution, integrated CLI and final readiness remain in existing OPEN gaps. This review does not close those gaps or permit deployment/trading. The direct subsequent-path failure regressions must become durable tests during repair; inherited initial-path successes alone are insufficient.

## Final Decision

RETURN_TO_WORKER for **B1, M1 and M2**. Preserve this report, original failing probes/logs and Worker02 unchanged. Implement additive Worker repair and independent re-review against the same frozen contract.

Do not mark ITER-0028 CODE_READY, close DG-003, create final PASS archive or continue the next iteration until the three findings and required regression scenarios close. DG-002 and DG-004 remain OPEN. Real orders/cancels and main merge remain unauthorized.
