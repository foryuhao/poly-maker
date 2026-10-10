# ITER-0028 — Reviewer Report

## Verdict

PASS

BLOCKER = 0. MUST_FIX = 0.

The exact reviewed tree satisfies all 11 frozen ITER-0028 acceptance criteria for **DG-003 — Repeated NORMAL Operation in One Market**. This is full integrated DG-003 acceptance closure, not helper completion. The retained runtime operates actual fake-SDK subsequent NORMAL cycles from a real acknowledged/reconciled initial cycle, preserving cumulative confirmed inventory/cost, current guards, attempt-specific lifecycle and honest terminal/handoff evidence.

Original B1, M1, M2 and subsequent M3 findings are closed. Independent exact-tree full regression and additional original/new probes pass. **DG-002 and DG-004 remain OPEN**, including cumulative capital consumption by operating REBALANCE submissions and actual REBALANCE operation/recovery. Other readiness gaps are unaffected; MICRO_LIVE_READY remains NO. PASS grants neither real-money trading nor main merge authority.

## Reviewed Inputs

Read actual AGENTS.md, Frozen SOP, BASELINE.md, ITERATION_INDEX.md and DELIVERY_GAPS.md; frozen ITER-0028 Architect01, Worker02, Reviewer03, WorkerFix04, EvidenceAddendum05, Reviewer06, Interruption07 and current WorkerFix08. Relevant ITER-0027 predecessor evidence was used only as context. Reviewed actual source/test diff and relevant existing admission, SDK guards/executor draining, placement owner, processor/store, pre-fill/window/close/terminal handling, capital projector/backing, confirmed accounting and official market-data tick processing.

Directly verified authority/artifact SHA256:

| Input | SHA256 |
|---|---|
| AGENTS.md | `0dedaab0b6653bd030d01c494d42d10619727f0e9f78bed9703002816538aa25` |
| Frozen SOP | `92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d` |
| Architect01 | `6e02585e44c31502264b5bc657ac69cc2abfec60b5cadc5bac4b8df580aba066` |
| Reviewer06 | `52104f1f6274680321795fa24737f8506ad062728ecfc12482adbc34d7883e3f` |
| Interruption07 | `b4131240a2d2e6a04157ff335028d753eaa7940908502266fbda4707f4a3b4dc` |
| WorkerFix08 | `3a67e753ffe4624c81c5fc8aa7d81dc0376e5fed317712fa2a5527ef03230606` |

Other immutable input hashes were verified in Reviewer06 and its preceding review; those files remain retained unchanged. Reports were not used in place of reviewing or testing current implementation.

## Repository / Diff Reviewed

Repository `/home/ubuntu/poly-maker`; branch `rdp-phase0-dev`; current/base HEAD `ec6c3af559095fa911a14a7008d9a8ebeab70487`. Inspected branch/status, actual HEAD, current diff/stat/check and protected/staged/untracked scope. Origin ref remains at the frozen contract base. Reviewer performed no Git history or repository mutation.

The cumulative authorized implementation/test diff is **five files, 1818 additions / 55 deletions**. Fix02 changed runtime completion reporting and placement tests only; owner/capital/capital tests match the independently reviewed Fix01 hashes.

| File | Added/deleted | Exact reviewed SHA256 |
|---|---|---|
| src/polymaker/rdp_runtime.py | 819/37 | `57971e1ab71d17768bd47e86a9e7296e79633709be7b61b9d9c23c372be3b654` |
| src/polymaker/execution/rdp_placement.py | 169/11 | `79f80cf4e025f6732277eae766d50f3d8f17de6eeb2e909ab1fd5574475f93a5` |
| src/polymaker/risk/rdp_capital.py | 14/6 | `cb93eff1b4f4c534f48f169303ad893ba69b9e96b1be02ca0b623b79e1984316` |
| tests/test_rdp_placement.py | 784/1 | `ef0aba929d4a429b9bac6c82e4b273030997de1e17d2c3534941458c869fb1c7` |
| tests/test_rdp_capital.py | 32/0 | `685929358f99e5e7a44bbc98e730070e8472370d3429abb2f7b4340f1bb42566` |

All five hashes were rechecked unchanged after independent tests. Additional tracked changes are Orchestrator-owned index/gap REVIEWER-stage narrative (four insertions/four deletions). Staged diff is empty; `git diff --check` passes; AGENTS/Frozen diff is empty. The 769 untracked files comprise preserved research_outputs (748), current specialist artifacts and copied review evidence. Ignored worker logs remain audit evidence. No unrelated source/test/configuration/dependency/CLI change is present. Reviewer wrote scratch probes/logs/report under `/tmp` only; no old probe or audit artifact was overwritten.

## Acceptance Criteria Review

| Frozen criterion | Independent acceptance evidence/conclusion |
|---|---|
| 1. Integrated repeated delivery | **Satisfied.** `test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively` executes the retained initial SDK cycle plus three automatically owned later NORMAL cycles in one call; later cycles are not manually armed/resolved/closed by the caller. The independent current-scope probe additionally executes two actual subsequent SDK pairs through one continuation before inspecting the six-order result. |
| 2. Fractional skew/cumulative accounting | **Satisfied.** Subsequent admissions/SDK quantities apply cumulative fractional residual skew; third-cycle assertions preserve both previous cycles' Q/C, Decimal confirmed cost, model30, reserve5 and physical backing. Independent original partial/late-prior/interrupted-COMPLETE cases retain current known inventory. New completion reprojects complete current scope; original M1 and M3 are closed. |
| 3. Multiple closure proofs | **Satisfied.** Existing projector validates every retained proof independently, rejects overlap/incomplete/stale/mismatched/insufficient coverage, and counts current confirmed partial cost plus remaining reservations once. New independent completion matrix rejects missing/incomplete/wrong-scope/overlapping/stale proofs, invalid registry economics/identity and unproved registered IDs. An actual closed six-ID run reprojects all three scoped proofs and remains certified. Current live expanded scope stays uncertified and unreleased. |
| 4. Actual changed dynamic tick | **Satisfied.** Exact-tree integrated tests process an official-format selected-market tick change through the existing service, retain bootstrap facts, warm the filter and sign later SDK orders with .005. A further post-sign tick change denies the stale intent; invalid metadata/minimum and fresh guards remain active. The previously corrected timestamp ensures the tick assertion measures tick evidence. Clean no-exposure tick/quote denial may freshly re-admit; later real stale-feed evidence still rejects. |
| 5. Ordering | **Satisfied.** SDK event sequence establishes CANCEL and TRADES between successive POSTs. Direct independent held subsequent close, late confirmed fill during cancellation, caller cancellation and teardown pass; no early replacement or prior/foreign cancellation. |
| 6. Per-attempt resting/window | **Satisfied.** Independent later zero-fill pair rests beyond the configured duration without starting a window or timing out. Healthy invalidation closes before automatic fresh requote. Prior-cycle receipt does not start the next window; a new confirmation retains its own original-receipt deadline; ordinary movement during that started window does not prematurely cancel/reprice. |
| 7. Current gates | **Satisfied.** Direct independent post-sign cash, allowance, positions, generation, backlog, stale book, timestamp and minimum mutations deny POST. Higher cash/allowance100 does not replenish model30 or waive reserve5. Existing projector/backing/filter tests cover reserve and fresh evidence boundaries. Completion certification now validates current scope/close evidence without claiming current physical spendability or execution authority. |
| 8. Strict outcomes/STOP | **Satisfied.** Subsequent partial/unknown/error, pending captured anomaly, stale prior fill and real-executor interrupted COMPLETE paths fail closed, retain known cumulative inventory, preserve primary cause, drain work and clean only attributable registered current IDs. No ambiguous POST retry. Terminal/uncertifiable reports preserve scope/uncertainty and do not release capital or ownership. |
| 9. Both REBALANCE boundaries | **Satisfied.** Exact-tree +5 and -5 integrated cases close current NORMAL completely and hand off cumulative inventory/capital. No subsequent dual NORMAL or REBALANCE POST; no invented boundary STOP. Independent late-cancel-fill and six-ID current-scope scenarios also finish at -5. DG-004 operation remains absent and OPEN. |
| 10. Time/concurrency/drain | **Satisfied.** Clock exception/malformed values latch STOP; restored clock cannot re-enter new risk. Default/nonboolean calls cause no execution I/O. Held-close manual conflicts/current owner serialization, operational budget boundary, caller cancellation, teardown and real SDK deadline drain pass. Concurrent denial during a live later pair is now uncertified/unresolved with zero extra POST/cancel. |
| 11. Compatibility/scope | **Satisfied.** Exact-tree full1703/2 passes, including initial once-only/cached results, existing DG-001/DG-006, FL and shared components. Protected/staged/untracked checks pass. No excluded subsystem rewrite or external interface change. |

These conditions jointly close the approved integrated repeated NORMAL behavior. This PASS does not close DG-002's still-missing operating REBALANCE capital consumption, DG-004's execution/recovery, or the remaining project readiness requirements.

## Existing Codebase Compatibility

The original gateway's post-only strict pair placement, guard bridge and executor drain remain the execution path. The existing owner/processor/store retain authoritative registration/fill state. Existing pre-fill/window/close reconciliation and terminal cleanup are reused for attempt-specific cycles. The small multiple-proof extension applies the existing projector validator rather than inventing aggregate close authority or replacing its ledger.

Fix02 reuses that projector synchronously from the report helper. It performs no account read, SDK call, cancellation or owner release. Initial once-only API/cache behavior remains covered by full regression. No duplicated gateway, independent execution path, parallel inventory/capital ledger, unrelated state machine or replacement of a working subsystem was found.

## Findings

### BLOCKER

None.

Original B1 is closed: malformed/exceptional operation clock latches the existing STOP cause before return, and independent restored-clock re-entry produces no new SDK POST.

### MUST_FIX

None.

- **M1 closed:** current registered confirmed inventory is captured independently from capital certainty, including later partial fills, late evidence on earlier orders and interrupted COMPLETE responses. Primary deadline/cancellation protection remains authoritative. Unprovable scope is explicit and capital stays uncertified.
- **M2 closed:** clean EMPTY pre-POST quote/tick/filter invalidation retains immutable attempt history and restores only a fully proven closed source. Fresh admission and final guards produce fresh signatures/POST; no stale intent is retried and no STOP is invented from this clean rejection. Pending captured anomalies remain terminal.
- **M3 closed:** `_continuation_completion` requires a nonempty registered run and complete/released current owner with retained proof count for acknowledged attempts, then reprojects full current scope through the existing validator. Matching inventory alone no longer certifies an old projection. Current open pair, newer local LIVE resurrection, missing/invalid proof and adverse current registry/fill evidence report uncertified/unresolved without releasing owner or performing I/O. Known inventory remains available where attributable clean scope can be projected.

### NOTE

- Completion capital certification is scoped confirmed-run/closed-order accounting evidence. It is not fresh physical cash/allowance backing, an eligible future quote or execution permission. Those separate admission/guard requirements remain mandatory before every actual send; `execution_authorized` stays false.
- The independent unrelated foreign registry case remains outside reported run IDs/inventory/cost. It cannot replenish run capital. Denied report-only certification of the explicit closed run does not approve account-wide spending or adopt foreign holdings.
- The obsolete original M2 total-POST-zero assertion remains immutable and is explicitly deselected in the prior matrix. The separately rerun adapted probe verifies the proper zero-stale-POST/fresh-re-admission behavior.
- Known-only inventory retains the existing float snapshot convention while certified confirmed cost is accumulated by the existing Decimal projector. No unrelated accounting rewrite is needed. Historical exploratory failures, Worker import mistake and interruption evidence remain preserved.

## Frozen SOP Compliance

Frozen SOP and approved human decisions are unchanged. NORMAL uses cumulative absDelta<5, current BestBid-minus-one-tick dual makers and Frozen size skew. Reconciliation precedes replacement. Model30, confirmed acquisition cost, resting reservations, physical backing and NORMAL reserve5 remain separate. Exact +/-5 handoff stops dual NORMAL quoting without placing a REBALANCE order or inventing STOP.

No quote/cycle quota, zero-fill timeout, hard REBALANCE-duration stop, account inventory adoption or new strategy state was introduced. Valid zero-fill quotes rest; healthy invalidation closes/requotes. Clean reliable entry/intent rejection can await fresh evidence, whereas unreliable state/ambiguous outcome remains terminal. Accounting concepts are not replaced by settlement assumptions.

## External Interface Review

Runtime continues to depend on approved official Polymarket HTTP/WebSocket and existing gateway/account/market-data components. No RPC, unofficial service, third-party runtime API, dependency or external runtime interface was added. New continuation/result/proof records are local evidence inside the existing runtime; they confer no execution authority. All review calls were offline and blocked before imports.

## Trading Safety Review

Explicit authorization remains `is True`; default and nonboolean inputs deny. Actual SDK orders retain forced post-only, strict opted-in pair acknowledgments, current tick signing and both current final guards. Market/time/order/metadata/account/fill/capital uncertainty denies risk. STOP remains immutable against later entry.

Subsequent ambiguous/partial/unknown/interrupted outcomes are not retried. Retained authoritative owner scope limits cleanup; foreign/unregistered IDs are not speculatively canceled. SDK/read work drains during cancellation/teardown. Independent held close proves no early replacement, and the current-authority matrix proves report generation changes no order/owner/fill evidence or gateway behavior. No safety control was weakened and no real-money order/cancellation occurred.

## Inventory / Capital Accounting Review

Cumulative registered run IDs retain prior confirmed fills across all attempts; current subset/unavailable scope is explicit when needed. Original M1 probes now observe partial current QY2.25, late-prior QY1.5 and interrupted COMPLETE QY1.25/QN1 rather than stale initial-only totals. Unknown or quarantined evidence does not become certified capital; valid known fills are retained separately.

Existing projector proof validation remains decisive: missing coverage, local resurrection, stale inventory, incomplete/overlapping/wrong-scope proof, bad registration/economics, pending/unadmitted/failed/quarantined fill cannot certify. The fresh scoped multi-cycle result matches all six IDs/three proofs and exact current cost. Current resting commitments are not mistaken for zero by an old closed snapshot; the original M3 live-pair probe independently establishes the correct 5.3125 reservation and now sees uncertified/unresolved reporting.

The projector has no owner-release side effect. Only the existing complete close path may release replacement. Current physical cash/allowance and model/reserve are still checked separately before new risk. DG-002's complete NORMAL-plus-operating-REBALANCE requirement remains OPEN.

## State Machine Review

One current cycle and retained attempt-specific submission/window/close results preserve original initial once-only caches. Prior confirmations do not start later windows. SDK acknowledgments adopt only validated registered scope. Clean EMPTY history creates no fake close proof; proof coverage remains tied to acknowledged attempts while the proven source serves fresh admission.

Actual current-close completion produces honest NORMAL continuation or REBALANCE handoff. Clock/unreliable/post uncertainty latches terminal protection and records known inventory. Concurrent/manual operations cannot create a second current cycle. Reporting recomputes current capital in memory and conservatively marks unresolved when it cannot certify; it neither mutates/reopens owner nor invents another execution state machine.

## Test Review

All independent pytest commands used the pre-import DNS/socket blocker `docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py`, SHA256 `bac8efb151d4e69c33bad9ef345bb9ad1c6d4e412aea38d0ec6d2a7e80fe9641`; bytecode/cache writing was disabled. Every run completed with **REVIEW_NETWORK_ATTEMPTS []**.

Exact commands from repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:docs/development/ITER-0028/review_evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no docs/development/ITER-0028/rereview_01_evidence/iter0028_rereview01_certification_v2.py > /tmp/iter0028_rereview02_original_m3.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no > /tmp/iter0028_rereview02_full.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:docs/development/ITER-0028/review_evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/iter0028_rereview02_authority.py > /tmp/iter0028_rereview02_authority.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:docs/development/ITER-0028/review_evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0028/review_evidence/iter0028_reviewer_probes_v3.py docs/development/ITER-0028/review_evidence/iter0028_reviewer_terminal.py docs/development/ITER-0028/review_evidence/iter0028_reviewer_conflicts.py -k 'not reliable_no_send_quote_movement_is_not_stop' > /tmp/iter0028_rereview02_prior_matrix.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:docs/development/ITER-0028/review_evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no docs/development/ITER-0028/rereview_01_evidence/iter0028_rereview01_probes_v2.py > /tmp/iter0028_rereview02_adapted.log 2>&1
```

Independent results on the exact reviewed tree:

| Check | Result |
|---|---|
| Original M3 two failing reproductions | **2 passed in2.48s** |
| Full suite | **1703 passed, 2 skipped in99.70s** |
| New current-authority matrix | **13 passed in1.98s**, one existing websockets deprecation warning |
| Prior terminal/lifecycle/window/current-gate/conflict matrix | **27 passed, 1 deliberately deselected in3.36s** |
| Adapted clean recovery/current inventory/active-cycle probes | **3 passed in2.62s** |
| Static/protected scope | `git diff --check` passes; staged/protected diff empty; exact hashes unchanged |

The additional commands yield 45 passing probe executions; some inherited helper scenarios appear in more than one module and are not claimed as 45 unique behaviors. The full suite includes all three new durable M3 regressions, previous B1/M1/M2 tests, both-sign integrated SDK/tick tests, multiple-proof capital tests, initial caches, FL and shared regressions.

The new independent authority matrix explicitly tests missing/incomplete/wrong-scope/overlapping proof, late confirmed evidence, pending fill, order/fill quarantine, foreign/invalid registry economics, unproved registered ID, unrelated foreign registration and a clean actual multi-cycle six-ID scope. It intercepts current projector calls, forbids runtime/account/gateway I/O and compares owner/current/attempt/proof/store/fill state before and after reporting. All pass; no release/mutation occurred. Healthy closed scope remains certified; adverse current authority remains uncertified/unresolved with known inventory where provable.

No independent pytest failures occurred in this re-review. One exploratory read used the nonexistent ITER-0028-local index path; the actual `docs/development/ITERATION_INDEX.md` was subsequently read directly. No repository change or test modification was used to conceal an outcome. Worker08's disclosed first test-import failure and earlier failing reports remain immutable historical evidence.

New scratch evidence for exact archival follows. The archived original/adapted probe modules import the unchanged helper in `docs/development/ITER-0028/review_evidence`; retain that replay PYTHONPATH and companion module. No old audit evidence was overwritten.

| Scratch evidence path | SHA256 |
|---|---|
| `/tmp/iter0028_rereview02_original_m3.log` | `0860735e085d0abd1a5d493d1b6dcae7cbe5590e3337d2875fcd324312f2c21b` |
| `/tmp/iter0028_rereview02_full.log` | `907898845507d3aab23e941436b5d383943bda8d2bea2cf69e3c136bf7a62752` |
| `/tmp/iter0028_rereview02_authority.py` | `ceffca8791bb294d6eaac74112facc6f3d822e423fcf06cee4ef3a70c6bf8501` |
| `/tmp/iter0028_rereview02_authority.log` | `a3ffa61376be7499a00ba2d5778d014e34a0eb934fa43abddb65e323df5cb8c8` |
| `/tmp/iter0028_rereview02_prior_matrix.log` | `76bc1fb01b76daf69105cb1c1fd58a89ecc71e4c19ddb0c7ee7f37c7087e46bd` |
| `/tmp/iter0028_rereview02_adapted.log` | `2aeb98be82ddb8ee8937378f8a3e51eded560384d74c07e42b2035fd1904f10f` |

## Scope Review

One approved delivery behavior, DG-003 repeated NORMAL operation, remains the implementation scope. Actual source/test changes are limited to runtime, existing placement owner, existing capital projector and their permitted tests. Gateway/lifecycle/store/tracker/quote builders/CLI/configuration/dependencies were not rewritten. Private history/proof/continuation additions are necessary for actual successive cycles and match the frozen contract.

No ITER-0025/0026 reimplementation, unrelated defect fix, architecture redesign, new runtime interface, speculative feature, live trading or main merge. Fix02 repairs report authority using existing validation rather than expanding scope. No Architect or human strategy decision is required for this PASS.

## Residual Risks

Execution evidence is offline fake-SDK/reconciliation evidence; it is not real-market proof. Full project readiness remains incomplete. DG-002 and DG-004 remain OPEN for actual operating REBALANCE capital/execution/recovery; other finite register gaps remain unchanged. This iteration closes integrated repeated NORMAL behavior only, not a complete trading CLI/market runner or permission to send live orders.

Capital certification here is current scoped closed-run economic evidence, not fresh account spendability or authorization. Required account/market/order freshness and operational budgets remain independently enforced before actual risk. Existing float representation applies to known-only inventory metrics; certified monetary projection retains Decimal accumulation. These limits do not leave an unclosed criterion in this frozen DG-003 contract.

## Final Decision

PASS.

**All 11 ITER-0028 acceptance criteria are independently satisfied on the exact reviewed five-file tree; full DG-003 delivery acceptance is closed.** B1/M1/M2/M3 are closed, with BLOCKER0 and MUST_FIX0. No helper-only or partial-prerequisite claim is substituted for integrated delivery.

Orchestrator should persist this report verbatim as `09_REVIEWER_REREVIEW_02.md`, preserve exact scratch evidence, create RESULT/update the stable DG-003/index status, and archive the reviewed iteration through the authorized development branch workflow. This PASS remains tied to the recorded hashes/base/state. Code or test changes invalidate its exact-tree applicability and require appropriate re-review.

DG-002 and DG-004 remain OPEN; MICRO_LIVE_READY remains NO. No real-money trade/cancellation or main merge was performed or authorized.
