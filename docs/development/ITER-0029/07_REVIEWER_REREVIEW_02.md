# ITER-0029 — Reviewer Report

## Verdict

RETURN_TO_WORKER

Independent exact-tree Fix02 review finds **1 BLOCKER and 2 MUST_FIX findings**, supported by four failing new integrated fault cases. The prior corrected independent matrices are green (30 passed / 1 deselected), and the current independent full suite is green (1745 passed / 2 skipped). They do not cover the demonstrated final-POST clock barrier omission, primary-interruption/secondary-clock combination, or broken failure-only NORMAL diagnostic.

**DG-004 remains OPEN.** No full acceptance, helper PASS, CODE_READY or MICRO_LIVE_READY is declared. The defects are ordinary local runtime/test implementation defects within the unchanged frozen contract. No strategy-semantic, external-interface or architecture change is required by the observed evidence.

## Reviewed Inputs

Actual repository AGENTS.md, Frozen SOP, BASELINE, current ITERATION_INDEX/DELIVERY_GAPS; ITER-0029 frozen Architect01; original Worker02/Reviewer03, WorkerFix01/Reviewer05; immutable new `06_WORKER_FIX_02.md`; relevant Worker original/Fix01/Fix02 evidence; current seven-file source/test diff; relevant existing submission guards/thread bridge/SDK drain, placement owner, typed close proof, current fill/account/capital/inventory components and retained NORMAL tests. Prior ITER-0028 components are reused, not reimplemented.

Input SHA256 values:

| File | SHA256 |
|---|---|
| `AGENTS.md` | `0dedaab0b6653bd030d01c494d42d10619727f0e9f78bed9703002816538aa25` |
| `docs/strategy/RDP_PHASE0_FROZEN.md` | `92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d` |
| `docs/development/BASELINE.md` | `8fbf83198838af4fe9a46daee803a3fe4379139c429d65b7278f7ae29106bfe7` |
| `docs/development/ITERATION_INDEX.md` | `79808a2bd59bfd2f3704f509a1fa9b72a9a4e2de169be1d1317ac58d3f89aaf7` |
| `docs/development/DELIVERY_GAPS.md` | `6e3ca663128bb1cab932ee8a4d60c3fc958d8df790af92dd039df5fa1963156e` |
| `docs/development/ITER-0029/01_ARCHITECT.md` | `fa2c9fab2346b94de58353c5564e9dbe6cc85bc6a6d34d01a0859c4ceb62423c` |
| `docs/development/ITER-0029/02_WORKER.md` | `7cb7ecbc1590787697c38a8ef146ab9bba2ec493d5524f3d56fca8ff2ecfad31` |
| `docs/development/ITER-0029/03_REVIEWER.md` | `c1b2cf39f5e81dad7c9c71b1875af17b5a2bbc757ad09d1f7236c424f38d6bf8` |
| `docs/development/ITER-0029/04_WORKER_FIX_01.md` | `c16cabb0bcca6eb0f927aac7fd367caeaf59bc5b8fdcb5ca96771b04f086d9ac` |
| `docs/development/ITER-0029/05_REVIEWER_REREVIEW_01.md` | `109deef077489b71a1522b629cefeec0ebd0345198a2194f3d5e2157d5218922` |
| `docs/development/ITER-0029/06_WORKER_FIX_02.md` | `97417b7dcaafd9fe833d03ea776c6639881a433454c40bb677531aa13d711d06` |

Worker06 reports implementation of B1-R/M2-R/M4, green exact-node/full tests and failure-only M5 diagnostics, while acknowledging no historical root-cause proof. This review independently verifies current behavior and explicitly disposes of M5 below; the Worker’s description of it as historical/unrelated is not authority to waive a required regression concern.

## Repository / Diff Reviewed

Repository `/home/ubuntu/poly-maker`; branch `rdp-phase0-dev`; base/current HEAD `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`, unchanged. Implementation remains uncommitted; no staged diff. Exact reviewed source/test hashes:

| File | SHA256 |
|---|---|
| `src/polymaker/execution/gateway.py` | `c1d809cb09d2c697b02cad7e939fc6dc568a28ba2db5750dfbc70df8e2a47da8` |
| `src/polymaker/execution/rdp_placement.py` | `5d02f872f28e0e83f31695c5eeafa63ed7f4ffe1be3ba8150c8f987da99eadc6` |
| `src/polymaker/rdp_runtime.py` | `65a4bc22eb993afeb8e7d757d4fa0b4710efad93862355e45cfffaf1a10b4223` |
| `src/polymaker/risk/rdp_capital.py` | `d3208986ba14d0e57402c9b22bdd5d486a7c2d5bbb742443c8ee1e0432e1dbd6` |
| `tests/test_execution.py` | `df6f2ea4ccfd4f1c4550d0cb7c5a243f4dfe3be6843fb01fe90bb9d58ddefb2a` |
| `tests/test_rdp_capital.py` | `687de05bd028554081f632c206d5b435cffea219d065fd8fcdd8e1c8b1344218` |
| `tests/test_rdp_placement.py` | `8efced276a85587c50d73e4b2705dd7c8960b7b821ea32abc421d9cd1e97c967` |

The authorized seven implementation/test files have **2548 insertions / 64 deletions** against base. Including two Orchestrator current-stage documents, tracked diff is nine files, **2553 insertions / 69 deletions**. Fix02 changes runtime and placement tests; original gateway/owner/capital and other test hashes remain unchanged. `git diff --check` passes with no output.

Relevant untracked/additive iteration reports/evidence/interruption/environment observations were considered; ignored raw logs are inspected directly. Pre-existing untracked `research_outputs/` is inventoried and preserved outside this implementation scope. Reviewer did not edit repository source/tests/docs/configuration, modify old evidence or perform any Git history operation. All new probes/logs/report are scratch-only under `/tmp`.

Final actual Git command results are preserved in `/tmp/ITER-0029-rereview-02-evidence/final_repository_checks.json`: `git status --short --branch`, `git rev-parse HEAD`, `git log --graph --oneline --decorate -15`, `git diff --stat`, `git diff --numstat`, `git diff --check`, `git diff --cached --name-status`, `git ls-files --others --exclude-standard`, `git diff --name-only`. Hashes and actual runtime/placement-test patches are preserved alongside it.

## Acceptance Criteria Review

All eleven frozen delivery criteria were assessed against actual tree and appropriate tests. Supporting evidence is not acceptance closure while mandatory failures remain.

| Criterion | Actual current independent evidence | Disposition |
|---|---|---|
| 1. Genuine initial/later retained handoff, one authorized operating call, actual resumed NORMAL dual POST, compatibility/default denial | Both signs/later handoff and actual healthy fake-SDK operating tests remain green in exact full suite. Previous plain interruption cases pass. A secondary clock failure now suppresses the primary deadline/teardown result. | Not closed: M1-C |
| 2. ±5/larger/strict `<5`, deficient BUY5 at BestAsk-currentTick, post-only | Actual quote/gateway/owner mode/cardinality changes inspected; existing integrated tests pass. No healthy opposite-side repair or unproved overlapping NORMAL send. | Supported healthy behavior |
| 3. Strict opted-in canonical single ack, clean rejection versus uncertain malformed/conflicting/multiple, observed versus adopted IDs, legacy/pair compatibility | Actual parser and ownership tests remain green. Original before/after registration injections pass; unknown/unregistered IDs are not adopted/cancelled speculatively. | Supporting evidence |
| 4. Fractional/multiple repair/NORMAL cumulative inventory/cost, replay and late evidence | Original known-subset and held current/prior fill probes pass independently; full fractional/replay/current proof tests green. Current code retains actual registered cumulative scope and uncertainty. | Original M3 trigger closed |
| 5. Cancel/reconcile before ask/tick reprice; late cancel and SDK worker drain | Existing dynamic tick/target tests pass. Plain held deadline/cancel/teardown probes pass. Combined held deadline/teardown plus post-drain clock fault still drains/cleans safely, but primary result is overwritten. | Not closed: M1-C |
| 6. Recovery closes remainder, rechecks actual cumulative inventory, real current-skew NORMAL | Original held current fill yields actual NORMAL 5/6 skew; valid late prior fill blocks stale close certification/new pair. Exact full healthy recovery tests pass. | Supporting race evidence |
| 7. Model30, physical cash/allowance/account commitments, current active backing, no resurrection/foreign/contradictory/overlap proof | Existing active-current scope and contradiction tests remain green; exact cash and current reservation semantics retained, no extra reserve/headroom. Initial capital failure now stops. | Supporting gate evidence |
| 8. All actual CanSafelyContinue predicates/categorical STOP, no further new risk, truthful scoped cleanup | Old monitor category, backward-poll and complete-sum-budget matrix now green. New between-sign/POST clock regression passes both ordinary SDK guards and makes actual POST before outer STOP. Primary cause can be replaced by later clock failure. | FAIL B1-F/M1-C |
| 9. Dynamic official tick/freshness/metadata/min/account/fill/time barriers through final POST mutation | Existing pre-sign/pre-POST and post-await mutations pass. New operation-local clock guard is not consumed by final submission permission; fresh older receipts can mask a within-market regression and permit POST. | FAIL B1-F |
| 10. Safe unchanged no-fill rests, no hard duration/quota/no-fill stop, natural sufficient-time boundary | Full no-fill and new exact/below/just-above sum-budget tests pass; no numeric strategy horizon added. Monitor checks full caller budget at the added read/wait edges. | Original M4 trigger closed |
| 11. Structured current proof/economics/category/cause, required compatible regression evidence/protected scope | Previous current-cleanup/economic/category probes green. Exact full now passes; historical NORMAL required concern remains unproven and the new failure diagnostic itself raises AttributeError for ordinary typed close proofs, defeating intended evidence. Primary/final cause coherence also fails combined cases. | FAIL M5-R/M1-C |

DG-004 remains OPEN; DG-002 and other registered gaps remain OPEN. Aggregate DG-009 report assembly and other future required gaps are not introduced into this local repair. The eleven criteria cannot be closed by passing only the previous matrices or full suite.

## Existing Codebase Compatibility

The implementation continues to reuse the official input/account infrastructure, existing gateway/guards/drain, owner, registered fill processor, store, cumulative inventory/projector/backing, quote builders, reconciliation and close proof, retained NORMAL operation and existing logging path. Strict-single acknowledgement is opt-in; legacy single and pair behavior is covered by the exact full suite. No parallel gateway, order registry, inventory ledger or NORMAL execution loop was introduced.

Fix02’s operation-local clock callback and `rebalance=True` terminal-classification opt-in are compatible local additions. The shared terminal default for existing NORMAL callers remains unchanged. Monitoring now receives the validated clock and complete operation budget. However submission permission still reads the untracked raw session clock; a post-return check cannot replace the last guard before a risk-bearing POST.

The diagnostics modify an existing positive test’s failure branch while keeping healthy assertions. That branch is not compatible with the actual typed `RDPCloseResult` interface because it assumes nonexistent `attempt_id`. Neither a passing healthy branch nor a fake helper result proves failure diagnostics usable.

## Findings

### BLOCKER

**B1-F — Operation-local backward-clock guard is absent at the final risk-bearing SDK permission edge; a detected regression can still POST first.**

Production locations: `src/polymaker/rdp_runtime.py:1243` local `read_operation_clock` state; `:1376` `_submit_normal_continuation` call without that guard state; `:1381` only-after-submission clock validation; shared `_initial_submission_permission_failure` at `:2992` reads raw `self._now()` and checks type/finiteness/deadline/remaining time without comparing to the prior accepted operation sample. Guard intent capture similarly uses the retained raw-clock path.

Minimal deterministic integrated trigger:

1. Genuine fully closed/released initial NORMAL +5 handoff, fresh legal books/tick/metadata/account/capital, canonical single repair ack. Start operation clock at base+3.
2. Official-style status/account receipts are legitimately 1.5 seconds old within configured freshness bounds. They stay nonfuture and fresh after a one-second clock regression; no unrelated malformed state is fabricated.
3. Existing fake SDK `on_create` callback, between signing and final POST guard, moves operation clock to base+2 (both finite times strictly inside selected market).
4. Both actual guard receipts report factual_valid=True and no reason. **An actual fake-SDK POST happens once**; resolved attempt is COMPLETE, post_started=True, guard_reasons=(None,None). Returning to the outer repair body finally observes OPERATION_CLOCK_REGRESSED, STOPs and cancels the current registered repair.

Output:

```text
FINAL_CLOCK TERMINAL STATE_UNRELIABLE 1 [('cycle-1-no-token',)] OPERATION_CLOCK_REGRESSED [('COMPLETE', None, True, (None, None))]
```

The guard logs show status age1.5 before signing/0.5 after regression, account age1.502 and legal books; all are still accepted. Negative-age protection can happen to catch some regressions, but is not equivalent to tracking the operation clock at the actual send edge. The demonstrated later cleanup is positive evidence of attribution/drain; it does not make the intervening risk-bearing POST authorized under reliable time.

Expected: the same accepted operation-time history must guard relevant admission/await/signing/final POST boundaries with safe synchronization through the existing submission/worker guard bridge. At the first regressed sample, revoke send, latch STATE_UNRELIABLE and emit no new POST. If SDK already started for an independent earlier reason, retain drain and exact cleanup; do not bypass other safety guards or introduce another execution path. Required regression must verify fresh older receipts rather than accidentally relying on a future-timestamp rejection.

Evidence: `/tmp/ITER-0029-rereview-02-evidence/final_guards_and_diagnostics.py::test_clock_regression_between_sign_and_post_denies_new_risk`; command N below, fails (expected posts0, observed1). Existing monitor regression/shape tests still pass. This is the remaining full B1 clock-coverage acceptance defect, not a request for new time strategy semantics.

### MUST_FIX

**M1-C — Secondary post-drain clock invalidity replaces the already-retained primary deadline/teardown result.**

Production location: `rdp_runtime.py:1380` reads/classifies clock immediately after awaited submission **before** its terminal/interruption consumption at `:1394`. On error, `_latch_rebalance_stop` replaces the session cause/detail and the branch returns generic STATE_UNRELIABLE. Existing cached terminal result retains the earlier cause; repeated `_ensure_terminal_failure` returns that cached result rather than recreating it.

Minimal integrated trigger: genuine +5 handoff; real gateway `_io` worker holds fake SDK canonical single ack plus confirmed NO fill1. While held, order budget expires (deadline) or session `aclose()` begins (teardown). Both continuation/teardown correctly stay pending and no cancel occurs before worker release. Just before held worker returns, set session wall clock to NaN. Shared submission drains, records original cause and reconciles/cancels exact `cycle-1-no-token`; outer post-await clock check then substitutes OPERATION_CLOCK_INVALID.

Observed outcomes:

```text
PRIMARY_AND_CLOCK deadline None STATE_UNRELIABLE ORDER_OPERATION_DEADLINE OPERATION_CLOCK_INVALID [('cycle-1-no-token',)]
PRIMARY_AND_CLOCK teardown None STATE_UNRELIABLE SESSION_TEARDOWN OPERATION_CLOCK_INVALID [('cycle-1-no-token',)]
```

One POST and one exact cancel occur in each; primary cached `terminal_failure_result.cause` remains ORDER_OPERATION_DEADLINE or SESSION_TEARDOWN. Final session/repair receipt cause becomes OPERATION_CLOCK_INVALID. Deadline caller receives STATE_UNRELIABLE instead of primary deadline completion. Teardown caller returns a normal terminal completion instead of its documented CancelledError; `aclose()` itself still returns normally after drain. No early SDK drain/cleanup failure or improper unknown-ID cancellation is claimed.

Expected: when awaited submission has already terminally latched an attributable primary interruption, consume that result/cancellation semantics before a later clock observation can overwrite it. Secondary invalid-clock observation may be retained separately without changing the primary operation cause, frozen category or cached/final coherence. Preserve no-new-risk latch and current scope/economics; do not monitor/retry a terminal attempt. For a healthy nonterminal submission, the validated clock still must gate risk, including B1-F’s final POST.

Evidence: `final_guards_and_diagnostics.py::test_primary_interruption_survives_postdrain_clock_failure[deadline/teardown]`, N, both fail. Original simple held deadline/cancel/teardown probes pass in O; this combination is within explicitly required repair SDK interruption/clock safety. Add durable combined regression assertions for primary caller result, cached result, cause/category logs, drain and exact cleanup/confirmed QY5/QN1.

**M5-R — Required NORMAL diagnostic enhancement fails on the actual close-proof type, so green runs cannot waive the unresolved required regression concern.**

Test production location: `tests/test_rdp_placement.py:10479` failure-only diagnostics use `proof.attempt_id for proof in placement_owner.close_proofs`. Actual reused type `src/polymaker/execution/rdp_lifecycle.py:32` has only complete, reason, affected_order_ids, reconciliation_outcomes and inventory. `RDPCloseResult` has **no attempt_id**; the owner’s keyed association is distinct from its public tuple of typed barriers.

Controlled diagnostic-only trigger: invoke the actual existing positive integrated NORMAL test with its usual fills/book filter/official feed/fake SDK. Let existing `record_close` record valid proof, then return a blocked replacement result for attempt2. This creates the same class of abnormal owner-release/terminal outcome the new failure-only branch is intended to diagnose, without claiming to reproduce the root cause of the historical intermittent production behavior. The test reaches its actual failure diagnostic with nonempty genuine typed close proofs.

Observed:

```text
FAILURE_DIAGNOSTIC AttributeError 'RDPCloseResult' object has no attribute 'attempt_id'
```

Expected pytest failure should contain `NORMAL positive path diagnostic:` plus actual retained reason/owner/close/capital evidence. Instead the diagnostic crashes before it can emit that dictionary. The controlled rejection is a harness sufficiency test only; it is not evidence that forced rejection caused either earlier unforced full-suite failure.

M5 disposition: current exact-tree independent full1745/2 passes, including the original positive case. Original assertions remain operative, and those successful runs are preserved. But prior exact-tree required positive failures remain unexplained; Worker06 does not supply root-cause proof, and its proposed bounded future diagnostic is itself broken under the real close proof interface. **M5 is not closed or waived as historical/unrelated merely because a healthy full run passes.** There is a precise ordinary required test defect to repair now.

Expected: use actual existing typed proof/scope and truthful owner association in the failure-only diagnostic, without inventing fields/IDs, weakening the positive assertions or making speculative production changes. Verify that the diagnostic itself survives an intentionally abnormal retained result. Then perform bounded actual positive-node/appropriate exact-tree verification with usable reason/proof/guard evidence; if the unforced failure recurs, retain its actual cause and perform the smallest justified runtime/owner/test fix. If no unforced recurrence is obtained, report that limit explicitly for independent disposition rather than claiming a proved historical cause. A genuine out-of-scope need must be returned precisely; none is shown by this diagnostic-field bug.

Evidence: `final_guards_and_diagnostics.py::test_failure_only_normal_diagnostic_survives_proved_close_results`, N, fails. No float, timing, historical-process or speculative accounting explanation is asserted. The repair lies entirely within approved placement tests and existing runtime/owner compatibility review.

### NOTE

**N1 — Prior concrete repairs independently verified.**

Unmodified corrected original P2+E3 plus previous A2 matrix run together: **30 passed, 1 deselected**. Monitor backward clock, complete monitor sum budget, cached terminal category agreement for all four normal safety predicates, malformed entry/live shapes, pending/malformed registered fill cleanup, initial capital STOP, held SDK drain/plain interruption semantics, known subset inventory/current cleanup proof and held close/fill race cases all pass. Original M3 remains closed; previous M2-R/M4 concrete triggers are closed. B1’s full coverage and M1’s combined primary-cause preservation still fail as above.

**N2 — Exact independent regression is now green; it is not the failure-only diagnostic test.**

Full1745/2 independently substantiates general compatibility on current hashes and does not erase the prior full1fail1738/2 or original Worker failure. It does not execute the newly added failure-only diagnostic unless the positive case fails. Required M5 diagnostic sufficiency is therefore independently tested by N rather than inferred from F.

**N3 — No additional historical/environment hypothesis work.**

All earlier report/probe/log versions remain immutable. Historical idle-process observations are not assigned causal significance and no processes were killed. There was no unbounded seed search, historical replay, broad rerun to hide a failure or unsupported float hypothesis during this review.

## Frozen SOP Compliance

Healthy code preserves frozen absDelta≥5 REBALANCE, strict `<5` NORMAL, deficient-only BUY5 highest noncrossing maker/post-only, close-before-replace/recovery, confirmed cumulative acquisition-cost economics and no hard rebalance duration/no-fill stop. Current capital uses actual commitments/reservations and preserves exact available-cash admission without extra protected headroom.

B1-F violates required reliable time at the last risk-bearing boundary. M1-C violates primary terminal result/cause coherence under the required interruption lifecycle. M5-R blocks valid required failure/regression evidence. These are implementation defects; Frozen text and approved architecture are unchanged.

## External Interface Review

Only approved official Polymarket HTTP/WebSocket and existing SDK runtime integration remain. No RPC, unofficial/third-party runtime service, new external interface, CLI/live command, dependency or configuration change was introduced. The operation-local callback and repair category opt-in are internal extensions.

All independent pytest imports run behind the archived DNS/socket blocker. Fake SDK sends/cancels are local fixtures; each log ends `REVIEW_NETWORK_ATTEMPTS []`. Reviewer performed no real connection/account action/order/cancel and no main merge.

## Trading Safety Review

Explicit authorization/default denial, existing guard bridge, captured ownership before POST, post-only, strict single certainty, adopted-versus-observed IDs, live gates and current capital checks remain in place. Unknown/unregistered IDs are not speculative cancel targets. SDK worker drain and exact cleanup remain safe in both failing M1-C combinations: no early return/cancel before release, then a single registered scope cancel.

B1-F demonstrates actual fake-SDK POST despite a regressed operation clock while every ordinary age/quote/capital guard is accepted. Outer-after-await cleanup correctly limits further exposure but cannot replace prevention at the final send edge. Primary deadline/teardown safety latch/cleanup remain present in M1-C; the defect is caller outcome/primary cause substitution, not an observed extra POST or lost known fill.

Current proof/capital reporting remains conservative and cannot release owner/capital through a diagnostic. Healthy no-fill/close/recovery safety behavior must remain intact through local fixes.

## Inventory / Capital Accounting Review

Actual registered-run filtering and clean-subset terminal capture preserve known QY5/QN0/cost2.75 when repair registration fails before ownership. Unknown ack remains unresolved and uncancelled; after-registration known repair fill is attributable/reconciled. Current STOP cleanup proof is associated with matching repair/terminal result rather than earlier initial cycle.

Original held current repair fill still updates actual resumed NORMAL skew; valid late prior NO fill invalidates stale close certification/no pair. Current model30/physical cash/allowance/commitments and active reserve remain separately validated; no hypothetical cancellation credit/extra reserve introduced. Full fractional/replay/scope contradiction tests pass.

In new combined interruption probes, cached cleanup retains current confirmed inventory QY5/QN1 and exact closed repair ID; final completion does not certify capital or release blocked owner. Primary event/cause inconsistency must be repaired while preserving that known inventory/uncertainty. No actual stale capital grant is observed in the new probes, and no approximate-accounting remedy is justified for M5.

## State Machine Review

Repair entry consumes a genuine retained released handoff, validates caller budgets/authorization/conflicts and operates through existing owner/task barriers. Monitor uses tracked clock and full required operation sum at added entry/read/wait boundaries. Category classification is supplied before cached terminal retention for regular monitor safety exits, and previous four-category agreement matrix passes.

The same accepted clock state is not integrated into the final shared submission guard, so body-level validation occurs too late for B1-F. Post-submission body clock validation also runs before consuming an already-terminal interruption, producing M1-C’s contradictory final cause/caller result. The local fix must preserve terminal priority and use existing guard synchronization rather than add another execution state machine.

Failure diagnostic must inspect retained actual owner/proof state using its existing types. A helper-supplied attempt ID or invented mapping is not evidence of actual close association.

## Test Review

All commands below run from `/home/ubuntu/poly-maker` with bytecode disabled and pre-import runner `docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py` (SHA256 `bac8efb151d4e69c33bad9ef345bb9ad1c6d4e412aea38d0ec6d2a7e80fe9641`); runner disables cache. Every test log ends with an empty network-attempt list. No source/test edits by Reviewer.

**F — Exact current full suite with failure capture enabled**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=all > /tmp/ITER-0029-rereview-02-evidence/full.log 2>&1
```

**1745 passed, 2 skipped in101.64s**, exit0, zero network. This includes Fix02 durable clock/budget/category changes and all existing affected/adjacent/FL regressions, including the previously failing positive NORMAL node. There is no claim of historical root-cause proof from a passing run.

**O — All unmodified corrected earlier independent matrices**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=all /tmp/ITER-0029-review-evidence/probes_v2.py /tmp/ITER-0029-review-evidence/extra_v3.py /tmp/ITER-0029-rereview-01-evidence/additional_probes_v2.py -k 'not normal_proof_order_variation' > /tmp/ITER-0029-rereview-02-evidence/original_matrices.log 2>&1
```

**30 passed, 1 deselected in3.35s**, exit0, zero network. Byte-identical copies of source files are included in current evidence for durable reproduction; original sources/logs/artifacts are untouched.

**N — New final-send clock, combined interruption priority and actual-type diagnostic sufficiency**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=all /tmp/ITER-0029-rereview-02-evidence/final_guards_and_diagnostics.py > /tmp/ITER-0029-rereview-02-evidence/final_guards_and_diagnostics.log 2>&1
```

**4 failed in4.16s**, exit1, zero network. All four valid tests correspond directly to B1-F, M5-R and M1-C deadline/teardown. No scratch import/harness correction occurred in this new file; the diagnostic test deliberately induces an abnormal retained close result solely to verify that the production test’s failure diagnostic works. It does not claim a natural production root cause.

Raw guard receipts, current ack/POST/cancel events, cached primary cause and final replaced cause, diagnostic AttributeError and pytest failure assertions are preserved in the exact log. No further historical probe/search or broad repetition is needed to establish this RETURN_TO_WORKER. The next Worker changes need direct durable regressions and appropriate renewed exact-tree checks.

Evidence root `/tmp/ITER-0029-rereview-02-evidence/`; exact SHA256 manifest:

```text
ff3bef4271e21d16fcd2b5256304374da350361ba06a9e8fcedbb38041c83c8b  /tmp/ITER-0029-rereview-02-evidence/actual_placement_test_diff.patch
a82253a8482e737f39681f28b28a71bec1872335090c61074895ed9eb69f496f  /tmp/ITER-0029-rereview-02-evidence/actual_runtime_diff.patch
2b1b5a2dc71ae7123882b55f18f45ceb6ec44cd6803cfecb9ae73ba8a0d5531b  /tmp/ITER-0029-rereview-02-evidence/additional_probes_v2.py
a6ffb74f4e165cf824af61dfe92269bc1be98a48d2b1753017d40755fb6ae63e  /tmp/ITER-0029-rereview-02-evidence/extra_v3.py
3f6dc117525b5f5d3032b75b0db7f25ffcd763ad80219f82a356756851970660  /tmp/ITER-0029-rereview-02-evidence/final_guards_and_diagnostics.log
23b34277a4e6dc4d6cef46675c0d96dadbfc3699576f2ebb552f3e07baa30daf  /tmp/ITER-0029-rereview-02-evidence/final_guards_and_diagnostics.py
31779b60849ff7549ce5204d6e45385f7c174010d4aeb4a9f97dfa05575369f9  /tmp/ITER-0029-rereview-02-evidence/final_repository_checks.json
8209b67db860d1525498c16dd036392086e01661ea1e6695d3c630a737310843  /tmp/ITER-0029-rereview-02-evidence/full.log
5bdaa441a8ed2d26416d4df9e8367890f6996ecbdfad23550aa4a4049ea7b8b8  /tmp/ITER-0029-rereview-02-evidence/original_matrices.log
8935d756aaab1f1d8cc2dd6d2b76e876155691ba38a2ad8f9041d9e2fd34467a  /tmp/ITER-0029-rereview-02-evidence/probes_v2.py
0dbaec06f1b27c9a1b7d9938a1b5ccac2d74e2190b82e19c945134a2d567493e  /tmp/ITER-0029-rereview-02-evidence/reviewed_hashes.json
```

Manifest file: `/tmp/ITER-0029-rereview-02-evidence/SHA256SUMS`. Parent may copy these bytes verbatim for durable07 evidence. No earlier report/probe/log was overwritten. Repository sources/tests remain at the reviewed hashes.

## Scope Review

Production/test changes remain within authorized seven-file ITER-0029 scope, with Fix02 confined to runtime and placement tests; no Frozen SOP, external dependency/interface, CLI/live execution, shared-core or unrelated FL/IM source refactor. Existing working components are extended locally rather than replaced.

B1-F/M1-C/M5-R are necessary required delivery/safety/compatibility defects, not optional refactoring or a speculative new iteration. The failure-only diagnostic is now an observed ordinary test bug; it cannot be dismissed as historical merely because its healthy branch passed. No proven architecture/strategy conflict requires RETURN_TO_ARCHITECT. If root-cause evidence later demonstrates a real scope/interface/semantic need, preserve it and return that precise conflict rather than silently expanding scope.

## Residual Risks

- Current B1-F/M1-C/M5-R are unwaived failures. Full green and earlier matrices do not establish acceptance for uncovered combinations.
- Original/unforced NORMAL intermittent failure cause remains unknown. Current full passes, while new diagnostic crashes under actual typed close proof; M5 remains required until usable bounded diagnostic/current verification is independently disposed of. No float/process/timing explanation is assumed.
- Controlled diagnostic failure is a test of evidence sufficiency, not proof of the historical production cause. Preserve that distinction.
- Fake official inputs/SDK tests establish neither live venue behavior nor economic performance/readiness. No real trading authorization is inferred.
- Current known inventory/uncertainty and close-proof reporting must remain read-only and cannot grant trading authority or release capital. Other delivery/readiness/live requirements remain separate open gaps.

## Final Decision

**RETURN_TO_WORKER. BLOCKER=1; MUST_FIX=2.**

Repair B1-F’s final SDK clock barrier and M1-C’s primary-terminal priority locally in existing retained submission/runtime interfaces. Repair M5-R’s actual-type failure diagnostic in approved placement tests and retain bounded real positive-node/current-tree evidence for independent M5 disposition. Preserve the now-green prior monitor clock/sum-budget/category matrix, SDK drain, exact per-ID ownership/cleanup, cumulative known subset/cost, current matching close proof, held fill-race/recovery and healthy NORMAL behavior.

The current exact-tree full1745/2 is preserved as passing evidence; it does not waive the four independently demonstrated failures or close DG-004. No historical root cause is claimed or specialist audit rewritten. Independent re-review must verify the full eleven-condition acceptance on the exact repaired tree before closure.

DG-004 remains OPEN; DG-002 and other recorded gaps remain OPEN. This iteration is not CODE_READY or MICRO_LIVE_READY. No strategy redesign, external-runtime change, real order/cancel, main merge or next implementation is approved by this artifact.
