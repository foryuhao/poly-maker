# ITER-0028 — Reviewer Report

## Verdict

RETURN_TO_WORKER

BLOCKER = 0. MUST_FIX = 1.

Fix 01 closes the original B1 terminal re-entry reproduction, the original M1 omitted/stale confirmed-inventory and interruption-cause reproductions, and original M2 clean no-send recovery. Independent full regression passes. A new independent accounting-report reproduction nevertheless shows that the added `capital_certified` flag certifies an older closed scope when current run scope/reservations or close validity have changed. This is an ordinary implementation defect within the frozen contract.

**Full DG-003 acceptance remains OPEN; ITER-0028 is NOT_READY.** DG-002 and DG-004 remain OPEN. MICRO_LIVE_READY remains NO. No real-money execution or main merge was performed or authorized.

## Reviewed Inputs

Read actual AGENTS.md, Frozen SOP, BASELINE.md, index/delivery register, frozen ITER-0028 Architect01, original Worker02, immutable Reviewer03, Worker Fix04 and Worker Evidence Addendum05. Reviewed the actual five-file source/test diff, current code/tests, relevant original initial admission/submission guards, owner registry, lifecycle/window/close pipeline, terminal inventory capture, cumulative projector/backing, tracker/store evidence and market-data tick provenance. Relevant ITER-0027 evidence was retained as predecessor context, not proof of this tree.

| Authority/artifact | Actual SHA256 |
|---|---|
| AGENTS.md | `0dedaab0b6653bd030d01c494d42d10619727f0e9f78bed9703002816538aa25` |
| Frozen SOP | `92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d` |
| Architect01 | `6e02585e44c31502264b5bc657ac69cc2abfec60b5cadc5bac4b8df580aba066` |
| Worker02 | `13dfdcd624e596c2803a16b36c49166132c04ec9937ae4feef7ad10838a92c01` |
| Reviewer03 | `00f55bc0de51647e833380d25f145ce6cb3a75cde0acc49b8e489059ebd61b19` |
| Worker Fix04 | `c2a52808a971ca5e5e946cabdf58d3a30416f3e6286a2df05ca230c09cc92538` |
| Worker Addendum05 | `a6555c0b090c37ce33fc89bc3b76199a091c9b973e5d527c9b2515f0615a01a9` |

The persisted Fix04 hash and separate Addendum05 are the reviewed inputs; an earlier communicated Worker hash is not substituted for either immutable document.

## Repository / Diff Reviewed

Repository `/home/ubuntu/poly-maker`, branch `rdp-phase0-dev`, base/current HEAD `ec6c3af559095fa911a14a7008d9a8ebeab70487`. The inspected local graph shows origin/rdp-phase0-dev at this contract commit. No history operation was performed by Reviewer.

Actual authorized implementation diff: five files, **1685 insertions / 54 deletions**.

| File | Added/deleted | Reviewed SHA256 |
|---|---|---|
| src/polymaker/rdp_runtime.py | 790/37 | `f7d1bd9e568dab2fd19db6dd1882e82919110196c1bfb327ceb3c730faa24a0d` |
| src/polymaker/execution/rdp_placement.py | 169/11 | `79f80cf4e025f6732277eae766d50f3d8f17de6eeb2e909ab1fd5574475f93a5` |
| src/polymaker/risk/rdp_capital.py | 14/6 | `cb93eff1b4f4c534f48f169303ad893ba69b9e96b1be02ca0b623b79e1984316` |
| tests/test_rdp_placement.py | 680/0 | `f37d5759dc41a01cf45a34b6e148d55af64ca4ba63e5424d03d788e254fd8c39` |
| tests/test_rdp_capital.py | 32/0 | `685929358f99e5e7a44bbc98e730070e8472370d3429abb2f7b4340f1bb42566` |

Additional tracked changes are the Orchestrator-owned index/gap stage narrative, four insertions/four deletions. Untracked scope comprises current immutable specialist reports, copied Reviewer evidence and preserved pre-existing research_outputs. Ignored worker evidence is audit material. Staged diff is empty; `git diff --check` passes; AGENTS/Frozen diff is empty. No configuration, dependency, CLI, gateway, lifecycle, tracker/store or unrelated strategy edit is present. All Reviewer-created probes/logs/report are under `/tmp`; no repository file or older audit artifact was changed.

## Acceptance Criteria Review

| Criterion | Actual-tree conclusion |
|---|---|
| 1. Integrated repeated delivery | Satisfied on reliable paths. Actual retained initial cycle precedes automatic subsequent SDK pairs under one continuation call; repository integrated tests execute multiple later cycles without caller arm/resolve/close interventions. Independent adapted clean recovery sends two freshly admitted later pairs. |
| 2. Fractional skew/cumulative accounting | Subsequent eligible sends preserve earlier quantities/cost and fractional skew with current backing/model gates. Original M1 failure cases now return known cumulative current inventory separately from uncertified capital. New M3 prevents complete result-accounting acceptance. |
| 3. Multiple closure proofs | Existing projector and owner release correctly validate cumulative scope, per-proof evidence, overlap, current partial cost/reservations and stale proof failures. **Result certification is not fully satisfied:** M3 bypasses these current validations when reporting `capital_certified`. |
| 4. Actual changed dynamic tick | Supported by actual-format selected-market tick processing and SDK signing tests on the reviewed tree. Bootstrap metadata is not rewritten. Further post-sign tick change prevents stale POST; invalid minimum/book evidence also denies. Corrected test refreshes timestamp so its first guard measures tick rather than incidental stale book. Later stale feed may legitimately STOP; initial clean tick denial alone may re-admit. |
| 5. Ordering | Independent held later cancellation, late fill, caller cancellation and teardown scenarios pass. No next POST precedes current cancellation/reconciliation; cancellation is confined to registered current IDs, excluding old closed/foreign IDs. |
| 6. Per-attempt resting/window | Independent later zero-fill rests beyond window duration, closes on healthy invalidation, then requotes. Old confirmations do not start the new window. New confirmation uses its original receipt; ordinary movement during the started window does not reprice. |
| 7. Current gates | Independent post-sign cash, allowance, position, generation, backlog, stale book, timestamp and minimum mutations deny new risk. Higher actual cash does not replenish model30/reserve5. Current send/owner guards remain fail-closed; M3 concerns the new completion's certification claim. |
| 8. Strict outcome/STOP | Original partial, unknown, late-prior fill and real-executor interrupted COMPLETE probes now preserve current known inventory and terminal protection. Primary ORDER_OPERATION_DEADLINE is retained by the durable test. No ambiguous POST retry or speculative unregistered cleanup. M3 still permits false result certification under changed evidence. |
| 9. Both REBALANCE boundaries | Full-tree integrated +/-5 boundary tests pass. Current NORMAL closure precedes handoff; cumulative quantities are retained. Independent -5 late-cancel-fill scenario passes. No later dual NORMAL or REBALANCE POST and no invented boundary STOP. |
| 10. Time/concurrency/drain | B1 clock exception/malformed clock now latches STOP; restoring clock cannot send again. Independent nonboolean/default denial, manual conflicts, later held cancellation/teardown and SDK deadline drain pass. Concurrent call creates no second cycle, but M3 makes its capital report stale. |
| 11. Compatibility/scope | Independent full1700/2 passes, including initial once-only caches, existing DG-001/DG-006, FL and shared components. Authorized diff and protected-file checks pass. Full DG-003 closure remains unavailable while M3 exists. |

Original Reviewer03 B1/M1/M2 reproductions are closed in the specified senses above. M3 is a newly reproduced defect in the new certification reporting, related to the same requirement that known inventory and complete current capital authority must remain distinct. No strategy or architecture revision is required.

## Existing Codebase Compatibility

The implementation continues to reuse the existing SDK placement/guard/executor path, processor registry, tracker/store, pre-fill/window lifecycle, cancellation/reconciliation, capital projector/backing and market-data service. Attempt maps/close-proof retention locally extend the existing owner/runtime. Clean EMPTY attempts remain immutable history while restoring the proven closed source as the admission anchor; acknowledged proof coverage is preserved independently of harmless EMPTY history.

No duplicated gateway, execution pipeline, capital/inventory ledger or parallel strategy state machine was introduced. Initial once-only operations remain compatible. The correct existing capital projector already supplies the validations omitted by the new report helper; fixing M3 should reuse that authority rather than introduce another validator.

## Findings

### BLOCKER

None. Original B1 is closed: the exception/nonfinite/malformed clock branch latches the existing terminal cause before return, and subsequent entry denies new risk.

### MUST_FIX

**M3 — New continuation completion falsely certifies retained capital by inventory equality alone, without current scope/reservations/close validity.**

Location: `src/polymaker/rdp_runtime.py:1338`, `_continuation_completion`, particularly `capital_certified` at line1353 and `unresolved_exposure` at line1361.

The helper chooses the latest supplied cycle projection, or falls back to the initial cycle projection on denial. It accepts that retained projection when its inventory equals current confirmed inventory and STOP has not latched. It does not verify projection run IDs, current resting reservations, current registry/close-proof validity or the current owner's complete closure. Equal confirmed quantities do not prove equal capital or valid order absence.

Independent valid reproductions use an actual retained acknowledged initial cycle, the real continuation/owner/processor and existing projector:

1. Hold the first subsequent zero-fill pair during its lifecycle. A concurrent authorized continuation is safely DENIED. Its result nevertheless reports `capital_certified=True`, `unresolved_exposure=True` and all four run IDs. The retained initial projection covers only two IDs with reserved_cash0; the current owner is replacement_blocked with no closed IDs. A fresh existing projector over the four current IDs and live rows certifies **reserved_cash5.3125**, demonstrating the omitted current commitments. No second POST occurs during the competing call and no owner is released.
2. After the initial close, deliver an accepted authoritative local LIVE observation for the owned previously closed `no-order`. Inventory stays unchanged. A default-denied continuation reports `capital_certified=True`, `unresolved_exposure=False`. The existing projector with retained close proofs rejects **CLOSED_ORDER_LOCAL_OPEN**. The live order remains in the store; there is no POST/cancel by this denied operation.

Exact observations:

```text
CURRENT_VS_OLD True True (four run IDs) () True (two initial IDs) 0 True 5.3125
RESURRECTED DENIED True False False CLOSED_ORDER_LOCAL_OPEN 1
```

The failing tests are `/tmp/iter0028_rereview01_certification_v2.py::test_concurrent_denial_does_not_certify_prior_scope` and `::test_resurrected_order_denial_not_capital_certified`; both fail only on the final false-certification assertion after proving the underlying scope/commitment/invalid-close facts. This is observed reporting dishonesty, not evidence of a send-gate bypass or actual capital release. It violates current cumulative capital/proof accounting and honest final/current evidence requirements and must not be deferred to achieve PASS.

Correction within scope: certification must consume a valid current cumulative projection/closure authority for the reported run scope, including current reservations and current proof/registry evidence. A retained closed projection must not certify a live expanded or contradicted scope just because inventory matches. Denied/report-only calls should remain zero-I/O; they may conservatively report uncertified evidence when current authority is unavailable. Preserve current known inventory even when certification fails. Ensure uncertainty remains honest where newer order evidence contradicts retained closure. Do not simply exclude one outcome label or these two test conditions; address the authority mismatch generally. Reuse existing projector/proof validation, keep owner release separate, and retain both reproductions as durable regressions.

### NOTE

- The archived original M2 probe's `c.posts==0` assertion is intentionally obsolete after clean re-admission. It remains immutable and its failed rerun is disclosed. New probe proves zero stale-intent POST, six signatures (first pair abandoned), two fresh POSTs, retained EMPTY history, changed next price .54 versus stale .55, and three actual close proofs. No STOP is invented.
- Known-only fractional terminal snapshots retain the existing float accounting convention; independent three additional .1 fills at .55 report QY1.5500000000000003/cost0.8525000000000001 with `capital_certified=False`. This is disclosed representation precision, not a new ledger or proof of certified capital. Certified paths retain the existing exact Decimal projector cost. No unrelated accounting rewrite is requested.
- Worker original exploratory failures and repair failures remain visible in archived evidence. Full passing regressions do not override independently failing current-tree probes.

## Frozen SOP Compliance

Strategy authority is unchanged. NORMAL uses cumulative absDelta<5, BestBid-minus-current-tick dual makers and exact inventory skew; +/-5 hands off only after closure. Model30 and NORMAL reserve5 remain intact; confirmed acquisitions are separate from live reservations and settlement metrics. No cycle quota, zero-fill timeout, hard REBALANCE duration stop or new runtime mode was added. Clean entry/tick/price/filter invalidation may wait and freshly re-admit; unreliable captured evidence and ambiguous outcomes still terminal. B1/M2 repair is consistent with these distinctions. M3's report certification violates honest current capital/proof evidence and blocks full acceptance.

## External Interface Review

No dependency or external runtime interface change. Runtime continues to use approved official Polymarket HTTP/WebSocket and existing account/gateway paths. No RPC, third-party runtime API, alternate execution path, real network or real trading was used in review. Internal result/history fields remain local implementation evidence with `execution_authorized=False`.

## Trading Safety Review

Actual SDK path remains post-only, strict typed pair acknowledgments, current final guards, explicit true authorization and positive operation budgets. STOP protects later risk; clock repair passes re-entry tests. Subsequent partial/unknown/late-fill/interrupted COMPLETE paths retain uncertainty, drain SDK work, clean only registered attributable current IDs and do not retry ambiguity. Pending captured unknown trades do not qualify for clean EMPTY recovery. Concurrent/manual operations are excluded; held cancellation and teardown drain. No extra POST, release or unregistered cancellation was observed in M3 probes. Nonetheless a false capital certification is material safety/accounting evidence and prevents readiness.

## Inventory / Capital Accounting Review

Known current confirmed inventory is captured from explicit registered clean scope and remains available even when capital is uncertifiable. Original partial QY2.25, late-prior QY1.5 and interrupted COMPLETE QY1.25/QN1 now appear in the outer continuation result. Scope/exposure flags remain explicit for terminal uncertainty. Foreign/unregistered fills are not adopted.

The actual projector correctly handles cumulative prior proofs, current partial confirmed cost plus remaining reservation exactly once, overlap/incomplete/stale proof failure and local order resurrection. Current backing cannot reset model30 or bypass reserve5. M3 is isolated to report certification's stale projection selection/comparison; both independent cases verify the reused projector already distinguishes the omitted facts.

## State Machine Review

Clean EMPTY recovery restores only the prior COMPLETE released source with full retained close proof and no acknowledgments/observations/pending anomalies in the EMPTY attempt. Fresh admission and both send guards are required again; stale signed intent is discarded. Uncertain evidence does not regain that path. Window receipt and close history remain attempt-specific, while confirmed inventory stays cumulative. Reliable exact +/-5 handoff creates no REBALANCE order. Initial results/caches stay once-only. M3 concerns current report authority during denied/no-new-risk calls; it does not authorize a second state machine or redesign.

## Test Review

All independent pytest commands use the archived pre-import DNS/socket blocker, with cache disabled and bytecode writing disabled. Runner SHA256 `bac8efb151d4e69c33bad9ef345bb9ad1c6d4e412aea38d0ec6d2a7e80fe9641`. Every completed command reports `REVIEW_NETWORK_ATTEMPTS []`.

Exact independent commands from repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no > /tmp/iter0028_rereview01_full.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no docs/development/ITER-0028/review_evidence/iter0028_reviewer_probes_v3.py docs/development/ITER-0028/review_evidence/iter0028_reviewer_terminal.py docs/development/ITER-0028/review_evidence/iter0028_reviewer_conflicts.py > /tmp/iter0028_rereview01_original.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/iter0028_rereview01_probes_v2.py > /tmp/iter0028_rereview01_probes_v2.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/iter0028_rereview01_certification_v2.py > /tmp/iter0028_rereview01_certification_v2.log 2>&1
```

Results:

- Full exact tree: **1700 passed, 2 skipped in98.67s**.
- Immutable original probes across three selected modules: **27 passed, 1 failed in3.24s**. Sole failure is obsolete M2 total POST assertion; terminal.py intentionally contains inherited lifecycle/window/gate probes, so this run directly rechecks their complete matrix as well as B1, original inventory failures and conflicts.
- New adapted/evidence probes_v2: **2 passed, 1 failed in1.76s**. Clean recovery and known fractional inventory pass; active-cycle stale certification fails.
- New precise certification_v2: **2 failed in1.67s**, both M3 reproductions, after current projector/scope facts pass.
- Static: current branch/HEAD/status/graph, staged/untracked scope, `git diff --check`, protected-file diff and SHA256 checks completed.

Exploratory Reviewer probe errors are preserved: probes.py first ran with two incorrect test-only attribute names (`submission.authorization`, `attempt.released`) and produced2 failed/1 passed; new probes_v2 corrects those names without overwriting the original. certification.py initially imported OrderEvent from the wrong module and produced a collection error; new certification_v2 imports the existing tracker class. These are harness errors, not product findings. The initial SHA command also used three nonexistent path aliases; the actual five paths listed above were then hashed directly. No test/repository modification masked these outcomes.

Scratch evidence for exact archival follows; v2 probe imports immutable `/tmp/iter0028_reviewer_probes_v3.py` (same archived helper counterpart in ITER-0028/review_evidence). That helper must accompany replay through PYTHONPATH. No archived probe or report was overwritten.

| Scratch evidence path | SHA256 |
|---|---|
| `/tmp/iter0028_rereview01_full.log` | `d4b6965840a976b2587d2480d28bad3ff782d2ef5cb56b84cfd0cd323dfb813c` |
| `/tmp/iter0028_rereview01_original.log` | `577985333d7e62d2842f7f9078a59ebf523e5f31eef5509a518e0c7cea60a8b4` |
| `/tmp/iter0028_rereview01_probes.py` | `d2e460028d58bbac01546d8b519b0a329ce0983bdfbf1cf6c495a1c31bfd3107` |
| `/tmp/iter0028_rereview01_probes.log` | `4b94cfa103c84a96598892539b9ec3fa51421769e96390c64009596ee860e56b` |
| `/tmp/iter0028_rereview01_probes_v2.py` | `af985cf0fcbfdb81b1a4a7abad66d8d8dc622e417abcc9b38b28585dc467dcb2` |
| `/tmp/iter0028_rereview01_probes_v2.log` | `a11c408d4d1028ba0b1d7185e6738dc01b8a51319fbff307dcc96d4090d2051f` |
| `/tmp/iter0028_rereview01_certification.py` | `2dd843381324424d605fe514fc38ffa1ac2cd15c17c57879b2f0b585c85844af` |
| `/tmp/iter0028_rereview01_certification.log` | `d1fef91d396d2b58796a779c32483d7ab15e9ce48c174ff632ea81c9106db47b` |
| `/tmp/iter0028_rereview01_certification_v2.py` | `ef2a381e519cb14289e5408e7757e30a0b109ec7d90bc5bd50c5a9e094a8c90a` |
| `/tmp/iter0028_rereview01_certification_v2.log` | `aae796c333dfe6a0d2d53022cb8359a5b36c92f3a223775d332f7cbec3080ab5` |
| `/tmp/iter0028_reviewer_probes_v3.py` | `194500b1060c3c8a03ff245c8c6407b5012f32e5efc9dd6538f8bd803c644dac` |

The exploratory commands used the identical blocked runner:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/iter0028_rereview01_probes.py > /tmp/iter0028_rereview01_probes.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/iter0028_rereview01_certification.py > /tmp/iter0028_rereview01_certification.log 2>&1
```

## Scope Review

All five changed implementation/test paths remain expressly authorized. No excluded gateway/lifecycle/shared-ledger/CLI/config/dependency change. Repairs address current repeated NORMAL delivery and its existing safety/evidence constraints. No ITER-0025 reimplementation, DG-002/DG-004 execution, strategy redesign, unrelated fix, main merge or live trading. The remaining M3 should return to Worker under the same frozen contract; no Architect/human semantic decision is necessary.

## Residual Risks

All execution/reconciliation evidence is offline fake-SDK evidence; it cannot establish live exchange behavior or confer trading authority. DG-002/004 and other open delivery/readiness conditions remain unclosed. DG-003 has reliable-path delivery evidence but still lacks honest capital certification under the independently reproduced changed-scope/contradicted-close scenarios. The representation precision disclosed above remains the reused accounting convention and must not be mistaken for exact certified capital. Current acceptance cannot be claimed from the passing full suite alone.

## Final Decision

RETURN_TO_WORKER.

Persist this report verbatim as the next independent re-review artifact (ITER-0028/06_REVIEWER_REREVIEW_01.md), preserve exact scratch evidence and old failing artifacts, then route M3 for local repair and independent actual-tree re-review. Original B1/M1 known-inventory/interruption/M2 recovery reproductions are closed, but full DG-003 closure and CODE_READY are withheld until current capital/report authority is corrected. DG-002 and DG-004 remain OPEN; MICRO_LIVE_READY remains NO. No new implementation iteration is authorized by this report.
