# ITER-0029 — Reviewer Report

## Verdict

RETURN_TO_WORKER

Independent re-review of Worker Fix01 finds **one remaining BLOCKER family and three MUST_FIX findings**. Original concrete B1 exception/nonfinite/quarantine triggers, M1 interruption-consumption triggers and M3 known-subset/current-cleanup triggers now pass independent reproduction. The full clock-safety acceptance remains incomplete for a backward clock within the selected market; M2 categorical output is still inconsistent in the immutable cached terminal result. The monitor also omits the full approved operational-time budget. A required existing NORMAL operating regression fails again on this exact tree.

**DG-004 remains OPEN.** This is not a partial/helper PASS or full delivery closure. The ordinary implementation repair/re-review loop remains appropriate; no demonstrated defect requires changing the Frozen SOP, strategy semantics, approved external interfaces or architecture. A genuinely necessary scope conflict discovered during root-cause work must be explicitly returned rather than improvised.

## Reviewed Inputs

Read the actual repository AGENTS.md authority and Frozen SOP, BASELINE, current ITERATION_INDEX/DELIVERY_GAPS, frozen ITER-0029 contract01, original Worker02 and original independent Reviewer03, new immutable Worker04, Worker original/Fix01 evidence and their manifests, and ENVIRONMENT_OBSERVATION_01. Source-of-truth is the actual current code/tests/diff. Relevant prior ITER-0028 cumulative-owner/proof/accounting behavior and original ITER-0029 positive and failing probes were reused; neither prior completed iteration was reimplemented.

Key input SHA256 values:

| File | SHA256 |
|---|---|
| `AGENTS.md` | `0dedaab0b6653bd030d01c494d42d10619727f0e9f78bed9703002816538aa25` |
| `docs/strategy/RDP_PHASE0_FROZEN.md` | `92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d` |
| `docs/development/BASELINE.md` | `8fbf83198838af4fe9a46daee803a3fe4379139c429d65b7278f7ae29106bfe7` |
| `docs/development/ITERATION_INDEX.md` | `ea311fcd599e4112c66f547df5bb8ee22c62e61552afe6d3e86c1727bbddd099` |
| `docs/development/DELIVERY_GAPS.md` | `79662b571f2c2ed52c1098fbb8b0e41332236c380ece7d66762362e3cd8f6206` |
| `docs/development/ITER-0029/01_ARCHITECT.md` | `fa2c9fab2346b94de58353c5564e9dbe6cc85bc6a6d34d01a0859c4ceb62423c` |
| `docs/development/ITER-0029/02_WORKER.md` | `7cb7ecbc1590787697c38a8ef146ab9bba2ec493d5524f3d56fca8ff2ecfad31` |
| `docs/development/ITER-0029/03_REVIEWER.md` | `c1b2cf39f5e81dad7c9c71b1875af17b5a2bbc757ad09d1f7236c424f38d6bf8` |
| `docs/development/ITER-0029/04_WORKER_FIX_01.md` | `c16cabb0bcca6eb0f927aac7fd367caeaf59bc5b8fdcb5ca96771b04f086d9ac` |
| `docs/development/ITER-0029/ENVIRONMENT_OBSERVATION_01.md` | `9d328c82b419b8109ff4d0e641c702c7369f0209580b9d560e0efc41ed5790a7` |

The original review remains immutable RETURN_TO_WORKER. This artifact records the new exact tree and does not silently amend prior conclusions. Worker04 claims full1739/2 and original corrected probes green; original probes independently pass here, but the independent exact-tree full suite has a current failure and that takes precedence over the reported run.

## Repository / Diff Reviewed

- Repository: `/home/ubuntu/poly-maker`; branch `rdp-phase0-dev`.
- Base/current HEAD: `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`, unchanged.
- Exact seven-file implementation/test diff remains the authorized runtime, gateway, owner, capital and their execution/capital/placement tests. Fix01 production/test changes are confined to runtime and placement tests. All other original seven-file hashes remain unchanged.
- Seven implementation/test files: **2293 insertions / 32 deletions**. Two additional Orchestrator stage documents bring ordinary tracked diff to nine files, **2298 insertions / 37 deletions**. No staged diff; `git diff --check` exits0 with no output.
- Additive untracked iteration reports/evidence/environment observation were inspected as audit artifacts. Ignored raw log archives were inspected directly. The pre-existing untracked `research_outputs/` tree was explicitly inventoried and preserved, outside this source change. No unrelated work was changed or removed.
- Reviewer changed no repository source, tests, docs, configuration or Git history. Only new scratch probes/logs/provenance/report were written under `/tmp`; no earlier audit/probe/log was overwritten.

Exact current implementation hashes:

| File | SHA256 |
|---|---|
| `src/polymaker/execution/gateway.py` | `c1d809cb09d2c697b02cad7e939fc6dc568a28ba2db5750dfbc70df8e2a47da8` |
| `src/polymaker/execution/rdp_placement.py` | `5d02f872f28e0e83f31695c5eeafa63ed7f4ffe1be3ba8150c8f987da99eadc6` |
| `src/polymaker/rdp_runtime.py` | `0b01182d5068fb91610fa5dd5709a726e817833e1fe424638c36f1da2c07363d` |
| `src/polymaker/risk/rdp_capital.py` | `d3208986ba14d0e57402c9b22bdd5d486a7c2d5bbb742443c8ee1e0432e1dbd6` |
| `tests/test_execution.py` | `df6f2ea4ccfd4f1c4550d0cb7c5a243f4dfe3be6843fb01fe90bb9d58ddefb2a` |
| `tests/test_rdp_capital.py` | `687de05bd028554081f632c206d5b435cffea219d065fd8fcdd8e1c8b1344218` |
| `tests/test_rdp_placement.py` | `ccebd1d78ced1250b89d896005bac8769b50b26504bb20674d424992cba6ad61` |

`/tmp/ITER-0029-rereview-01-evidence/final_repository_checks.json` preserves exact read-only command output for `git status --short --branch`, `git rev-parse HEAD`, `git log --graph --oneline --decorate -15`, `git diff --stat`, `git diff --numstat`, `git diff --check`, `git diff --cached --name-status`, `git ls-files --others --exclude-standard`, `git diff --name-only`. `reviewed_hashes.json` records authorities/documents/implementation; current runtime and owner diffs are retained as `.patch` files in the same scratch evidence directory.

## Acceptance Criteria Review

All eleven frozen criteria were independently considered against actual code and tests. Passing rows indicate supporting evidence, not a partial PASS when required failures remain.

| Criterion | Actual evidence / present conclusion | Status |
|---|---|---|
| 1. Initial/later genuine retained REBALANCE handoff through one authorized operating call to actual NORMAL pair, default denial and compatibility | Actual integrated initial/later handoff tests exercise real fake-SDK sends, shared serialization and existing NORMAL resumption. Old interrupted-COMPLETE reproductions pass. The required existing NORMAL positive operating test fails in this exact full suite. | Not closed; M5 |
| 2. Both signs, exact ±5/larger, strict `<5`, deficient five-share BUY at BestAsk-currentTick/post-only | Actual unchanged quote/gateway/mode-owner paths and both-sign/larger-delta integrated tests are covered in exact full run. No opposite-side repair or unproved NORMAL overlap introduced. | Supporting healthy-path evidence |
| 3. Strict opted-in single ack / authoritative rejection / uncertain malformed, nonboolean, multiple or conflicting response, observed versus adopted IDs; pair/legacy compatibility | Parser and owner diff inspected; strict-single/pair/legacy tests pass. Original before/after registration fault tests pass independently, preserving no unknown-ID adoption/cancellation/retry. | Supported; current-scope reporting M3 trigger closed |
| 4. Fractional and cumulative Q/C across repairs and NORMAL, replay, late prior evidence | Healthy integrated fractional/cumulative cases pass; original held current-fill mutation updates real NORMAL skew to 5/6, and valid prior NO fill blocks stale certification. Known prior QY5/cost2.75 survive registration-before failure as a clean scoped subset, with unresolved unknown ack. | Supporting evidence; original M3 concrete trigger closed |
| 5. Actual close-before-ask/tick reprice; late cancel and held SDK drain | Existing integrated dynamic target/tick paths pass. Independent held deadline/cancel/teardown all remain pending until real `_io` worker drain, then exact one-ID cleanup; primary operation outcomes now survive. | Original M1 concrete triggers closed |
| 6. Recovery closes remainder, rechecks inventory before actual current-skew NORMAL | Independent held current and valid prior fill close races pass again with no early replacement and no stale capital certification. Actual positive recovery test coverage retained. Current unresolved operating regression remains relevant to full behavior. | Supporting recovery evidence; M5 blocks full criterion |
| 7. Current model30/physical cash/allowance/account commitments admission/final guards/monitor; active backing versus closed resurrection/foreign/contradictory/overlap evidence | Relevant code and unchanged active backing/projector tests inspected. Initial capital failure now terminally stops; exact-cash admission and current-reservation safety tests remain in actual full suite. No new headroom/reserve or hypothetical release introduced. | Supporting gate behavior; output category M2 still required |
| 8. Four actual CanSafelyContinue predicates, permitted categorical StopReason, truthful scoped cleanup/no later risk | Old capital/no-latch failure repaired. Ten malformed clock shape tests and two pending/malformed fill tests independently pass; current time/capital/market cached terminal reason is wrong, current monitor time budget is incomplete, valid within-market clock regression remains unhandled. | FAIL B1-R/M2-R/M4 |
| 9. Official tick/freshness/metadata/min/account/fill/time guards through final POST mutations | Actual pre-sign/pre-POST and post-account-await tests remain passing. New pending/malformed registered fill and invalid-clock cleanup tests pass without fabricated capital. Current within-market backward clock does not fail closed. | FAIL B1-R |
| 10. Safe unchanged no-fill rests; no hard duration/quota/no-fill STOP; natural-time safety | No-fill/reprice code and tests retain frozen behavior; no numeric strategy horizon introduced. No-fill does not justify resting after the actual approved operation budget predicate becomes false. | FAIL M4 |
| 11. Current structured economics/proof, exact complete/incomplete scopes, required tests/protected scope | Entry/recovery endpoints, economic differences, actual fills/current book inputs retained. Original M3 current-proof mismatch corrected. Public final StopReason is categorized, but earlier immutable terminal result/logs remain STATE_UNRELIABLE for three actual categories. Full run has a required regression failure. | FAIL M2-R/M5 |

No gap closure, CODE_READY, MICRO_LIVE_READY, live authorization or next implementation is implied. DG-002 and other registered gaps remain OPEN; aggregate DG-009 reporting remains outside this required local repair.

## Existing Codebase Compatibility

Actual diff continues to reuse existing official HTTP/WebSocket input/account capture, gateway send guards and SDK drain, placement owner, registered fill processor, local store, quote builders, projector/backing, reconcile/close barrier, retained NORMAL execution and structured evidence path. Strict single response remains opt-in; pair/legacy behavior is preserved by tests. No duplicate gateway, inventory store, order registry or alternative NORMAL loop was introduced.

Fix01 adds an exception boundary around the existing repair body, actual registered run filtering, category/detail fields and attempt-matched STOP receipt. These local extensions are necessary for identified failure combinations and do not replace a working subsystem. The exception boundary uses existing terminal cleanup with operational monotonic deadlines and preserves known clean subsets rather than granting new capital.

The remaining issue in M2-R comes from invoking the shared terminal-result creator before supplying repair-specific classification, then changing the session category afterward. A local consistent classification-before-retention change can preserve legacy defaults. M4/B1-R are local time-validation concerns in the authorized retained repair operation. M5 requires finite diagnosis of the current NORMAL/owner-proof compatibility failure; its root cause is not inferred from a historical note.

## Findings

### BLOCKER

**B1-R — Required backward-clock safety remains incomplete when the clock regresses within the selected market.**

Original B1 exception, malformed shape, nonfinite and quarantined-evidence triggers are repaired and independently green. The remaining frozen clock requirement is different from merely rejecting a timestamp older than market start.

Production locations: `src/polymaker/rdp_runtime.py:1255` repair outer clock check and `:1540` monitor clock checks, repeated after status/account reads and at `:1737`. They reject exceptions/bool/non-number/nonfinite and `now < bootstrap.epoch`, but do not detect a newly sampled timestamp less than a prior accepted sample within the same operation. Freshly captured account/status receipts at the regressed clock do not necessarily expose this regression as a negative age.

Minimal deterministic trigger: genuine released initial +5 handoff, canonical zero-fill repair, otherwise legal/fresh inputs. Use an operation clock at `base+3`, then during the first monitor sleep move it backward to `base+2`; both are valid finite wall times strictly inside the selected market. Official-style status/account receipts use the current supplied clock; books are earlier and still fresh, so no unrelated stale/future input is fabricated. The monitor continues with a live repair and **enters a second ordinary sleep/poll** at the lower timestamp, still unlatching/unclosed. To bound the probe, the next sleep advances to market end-minus0.1, finally yielding TIME_EXHAUSTED/cleanup. Output: `BACKWARD_CLOCK TIME_EXHAUSTED [base+3, base+2] [('cycle-1-no-token',)]`.

Expected: regression of the operation's observed clock is unreliable time evidence; latch STATE_UNRELIABLE at the first regressed sample and perform existing exact registered cleanup before any further ordinary monitor/rest/reprice/recovery behavior. Keep the operational close deadline monotonic; do not add a numeric rebalance duration or suppress safe no-fill. No new POST after the regression is asserted by this probe; the demonstrated defect is continued live resting under unrecognized unreliable time.

Evidence: `additional_probes_v2.py::test_backward_clock_inside_market_is_unreliable`, command A2 below, fails. Full original exception/shape/pending cleanup evidence remains green and must not be regressed. Add a durable within-market backward sample integration test with accurate fresh receipts and explicit no further ordinary polling/risk, exact cleanup and denied re-entry.

### MUST_FIX

**M2-R — Repair-specific category is applied after the immutable terminal result and its finished event have already recorded STATE_UNRELIABLE.**

Production locations: `rdp_runtime.py:1512` `_monitor_rebalance_attempt.close_for`, `:3473` `_ensure_terminal_failure`, `:3648` retained `RDPTerminalFailureResult`, and outer classification at `:1455`; `_latch_rebalance_stop` at `:3416` changes session state but cannot rewrite an already emitted event or frozen cached result.

Minimal triggers are the existing actual integrated monitor safety cases for remaining time, current capital and market tradability. Scoped close/reconcile is real and completes. `close_for(reason)` invokes `_ensure_terminal_failure` before `_latch_rebalance_stop(reason)`. Shared `_latch_terminal_stop` uses its default STATE_UNRELIABLE and `_retain_terminal_failure` caches that category. Later outer classification makes public completion/session say the correct category, but the cached terminal result and its earlier authoritative finished receipt remain wrong.

Independent observations:

| Actual predicate | Final session/public category | Cached terminal_failure_result.stop_reason / finished log |
|---|---|---|
| Time unavailable | TIME_EXHAUSTED | STATE_UNRELIABLE |
| Current cash/allowance insufficient | CAPITAL_INSUFFICIENT | STATE_UNRELIABLE |
| Maker market not accepting orders | MARKET_NOT_TRADABLE | STATE_UNRELIABLE |
| Actual unreliable state | STATE_UNRELIABLE | STATE_UNRELIABLE (passes) |

The logs show initial STATE_UNRELIABLE latch/terminal-finished, then `rdp_terminal_stop_classified`, then correctly categorized final `rdp_rebalance_stopped`. This is not repaired merely by adding a later correction event: the frozen cached public result still misclassifies the real safety condition. Primary detailed cause must also survive interruption classification rather than being overwritten by a secondary diagnosis.

Expected: establish the appropriate allowed repair category and separate cause before terminal result retention/finished emission, preserving existing NORMAL defaults, exact cleanup and first interruption cause. All representations of the same terminal action must agree, including retained result, latch/finished receipts, final repair STOP receipt and public operation completion or diagnostic reason field. No old audit artifact/result history may be rewritten to hide the failure.

Evidence: `additional_probes_v2.py::test_retained_terminal_stop_category_agrees[time/capital/market]` fail; `[state]` passes. The original P2 initial-capital case now passes, so the admission-policy part of original M2 is closed; this finding is the remaining result/event consistency requirement. Add durable assertions for retained terminal category and finished log, not solely `result.reason == session._terminal_stop_reason`. Also verify held-deadline/interruption cause/category consistency while preserving the now-passing primary-output semantics.

**M4 — Repair monitoring checks only the close budget, omitting the approved Order → Cancel → Reconcile sufficiency budget.**

Production locations: `rdp_runtime.py:1547` initial monitor time branch and subsequent post-await/end checks. The outer new-risk admission correctly requires remaining time greater than `order_budget_s + close_budget_s`; the monitor uses `remaining <= close_budget_s` and later only market expiry. Frozen contract Desired Behavior item7 explicitly requires the sufficient remaining approved operation budget both before new risk **and during repair monitoring**. Its Required Tests require exact remaining-budget boundary evidence.

Minimal deterministic trigger: start a canonical zero-fill repair while entry has ample time, then its post callback sets the current clock to market end-minus1.5; caller budgets are order1 and close1. Other official-style inputs remain valid/current and book freshness allowance is explicitly extended for this isolated time test. Remaining time1.5 is less than required2, but greater than close1. The monitor accepts the state and enters an ordinary rest sleep with **latch false, one live POST and no cancel**. The probe bounds it by moving clock to end-minus0.1 during that sleep; only then does it stop/close. Output: `FULL_TIME_BUDGET TIME_EXHAUSTED [(end-1.5, False, 1, 0)] [('cycle-1-no-token',)]`.

Expected: consume the existing positive caller budgets consistently at monitor boundaries, including after awaited official reads, and TIME_EXHAUSTED STOP/close as soon as the actual required budget is insufficient. Do not substitute a hard strategy duration, add extra funding headroom, or treat safe lack of fill as a stop. Preserve exact-cash pure admission and current active-reservation checks.

Evidence: `additional_probes_v2.py::test_monitor_checks_full_order_cancel_reconcile_budget`, A2, fails because `polls` is nonempty. Add durable exact-sum and just-above/just-below boundary tests at entry and while resting/post-await, with no ordinary wait/new risk after predicate failure and correct complete/incomplete scoped result.

**M5 — Required existing NORMAL positive operating regression fails again on the exact delivered tree and remains unresolved.**

Independent exact-tree full suite: **1 failed, 1738 passed, 2 skipped**, exit1, zero network. Failure node:

```text
tests/test_rdp_placement.py::test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively[fills1-5.0-False]
```

At `tests/test_rdp_placement.py:10331`, expected actual operation outcome REBALANCE_HANDOFF is TERMINAL. This same required positive case failed in the archived original Worker affected run, whose internal reason was CLOSE_PROOF_NOT_RELEASED; Worker Fix01 full report and subsequent isolated runs pass. The current independent failure is material acceptance evidence, not a stale historical note that can be deferred to obtain PASS.

This current full command intentionally used `--show-capture=no`, so it preserves the failing node/assertion/result but not its internal terminal reason. **It does not prove the new failure's reason is again CLOSE_PROOF_NOT_RELEASED.** Neither repeated node nor historical similarity establishes root cause. Relevant production boundaries are retained NORMAL close/result composition at `rdp_runtime.py:6090` and owner `record_close` at `execution/rdp_placement.py:447`; exact current cause must be captured before prescribing a particular source fix.

Finite diagnostic work in this review:

- One instrumented exact positive NORMAL case invoking the actual existing integrated test independently **passes** (1 passed/3 deselected); no certified supplied/fresh proof mismatch is printed.
- Read actual owner full-proof equality, snapshot_inventory accumulation and current projector/close/release. Float/set-order sensitivity was considered as a hypothesis only.
- Bounded offline set/float layout searches for hash seeds0–127, once in system Python and once in the actual `.venv` Python3.12, find no discrepancy in the explicitly simulated runtime/owner set construction. Those reduced simulations are not a proof of runtime invariance and do not establish a root cause.
- No historical process was killed; ENVIRONMENT_OBSERVATION_01 establishes no causality. No broad repeat was used to replace/hide the failed full result.

Expected: finite actual diagnostic reproduction retaining internal cause/proof/guard evidence for the failing positive path, followed by the smallest compatible approved runtime/owner/test repair or justified deterministic test synchronization correction if evidence proves a harness problem. Do not weaken freshness, ownership/proof equality, accounting or fail-closed safety merely to force this assertion green; do not assert a float/timing/process explanation without evidence. Required healthy NORMAL execution must remain reliable and appropriate exact-tree regression checks must pass before acceptance closes.

Classification is MUST_FIX for an unresolved required regression concern, not a claim that a particular production defect or Fix01 causal introduction is already established. Current authorization covers required compatibility work in the existing runtime/owner/test boundary. If diagnosis demonstrates that resolution genuinely needs another strategy decision, architecture/interface change or unauthorized scope, stop and return that precise conflict under the existing escalation procedure. No such need has presently been demonstrated; ordinary Worker repair is the next action.

### NOTE

**N1 — Original concrete closures are verified, not merely taken from Worker04.**

The unmodified corrected original P2+E3 independently run together: **12 passed, 1 deselected**, zero network. Held deadline/cancel/teardown drain and primary operation output, outer unreadable clock/re-entry denial, live nonfinite clock/quarantine exact cleanup, initial capital STOP, before/after registration boundaries, prior clean cumulative inventory, held current-fill recovery/current skew and valid late prior fill invalidation all pass. Original M1 and M3 concrete defects are closed on this exact tree; original B1 concrete triggers are closed but full backward-clock acceptance remains B1-R. M2 admission policy is closed but cached category consistency remains M2-R.

**N2 — Scratch harness collection correction is preserved.**

First new `additional_probes.py` imports `TradeState` from nonexistent `polymaker.models`; collection exits2 with one ImportError and zero network attempts, without executing a product test. New `additional_probes_v2.py` corrects that import to `polymaker.domain`; the first source/log are retained unchanged. Only corrected valid test outcomes support findings.

**N3 — Historical environment observation has no demonstrated causal connection.**

Read-only Orchestrator document notes historical idle ep_poll pytest processes. This review does not kill them, reinterpret that observation as failure causality or use it to excuse current acceptance failures. The original historical NORMAL failure remains preserved; M5 separately records current exact-tree evidence and its limits.

## Frozen SOP Compliance

Healthy implementation preserves absDelta≥5 REBALANCE and strict `<5` NORMAL, deficient-only BUY5/highest legal noncrossing maker/post-only, confirmed-only cumulative economics, close-before-replace and no no-fill quota/hard rebalance horizon. Existing exact-cash admission is not replaced by extra reserve/headroom.

Remaining B1-R/M4 violate the required actual time reliability/sufficiency checks for CanSafelyContinue; M2-R violates accurate allowed StopReason observation/result semantics. M5 prevents claiming compatible reliable operating delivery. No change to frozen strategy text or human-approved semantics occurred; the remedy must implement the existing constraints rather than reinterpret them.

## External Interface Review

Runtime still uses approved official Polymarket HTTP/WebSocket and existing SDK gateway. No blockchain RPC, third-party runtime API, unofficial runtime service, new external interface, dependency/configuration/CLI/live-command change is present. Repair method and strict-single opt-in are authorized internal extensions.

All independent test imports run behind the archived pre-import DNS/socket blocker; every pytest log reports `REVIEW_NETWORK_ATTEMPTS []`. Offline layout scripts import no project code and perform only local child Python arithmetic; no connection/order/cancel is possible in those scripts. No real network/trading operation was performed.

## Trading Safety Review

Authorization is explicitly True; default denial and shared conflicts remain gated. Capture-before-POST, final guard checks, post-only SDK arguments, strict-single certainty and observed-versus-adopted ownership remain active. Unknown/unregistered acknowledgements are not speculative cancellation targets.

Held real gateway `_io` worker tests pass: no operation/teardown return or cleanup before release, no replacement before drain, then one actual registered repair-ID cleanup. Deadline retains primary ORDER_OPERATION_DEADLINE output; caller cancel/teardown caller semantics remain cancellation, while aclose itself completes after drain. No secondary normal monitor is started on the already-terminal attempt in those original triggers.

Two fresh pending/malformed registered fill injections independently stop, cancel only the current repair, retain unresolved exposure/clean known cumulative inventory and never certify capital. Ten entry/live malformed clock tests also stop and gate re-entry with current-scope receipts. Those improvements must be preserved. B1-R still keeps active resting exposure after a newly regressed clock, and M4 still rests after a false frozen time sufficiency predicate; neither can be waived.

## Inventory / Capital Accounting Review

Actual registered-run filtering now consults matching processor registry for each retained attempt. An unregistered new ack no longer poisons the whole known cumulative projection: prior QY5/QN0 and cost2.75 survive as CLEAN_REGISTERED_SUBSET_CONFIRMED_FILLS, with unknown ack explicitly unresolved, no speculative cancel and no capital certification. The after-registration fault remains accurately attributable/reconciled with confirmed repair fill included.

STOP receipt derives cleanup from matching current repair terminal/cycle result. Old initial closed IDs are no longer used as current repair proof; missing/incomplete current close has no fabricated proof attempt ID. Independent original and new malformed-clock/pending scope checks pass. Completion reprojection stays read-only and never releases owner/capital; terminal/uncertain scope cannot manufacture current certification.

Model budget, physical cash/allowance, account commitments and active repair reservations remain distinct. Current cost is accumulated across repair and resumed NORMAL; replay/late prior evidence protection remains active. Independent held current repair fill yields actual updated NORMAL 5/6 skew; held valid prior fill produces STALE_CLOSE_EVIDENCE/no new pair. No test observed an improper capital grant. Remaining cached stop-category error is reporting/lifecycle consistency, not an observed premature capital release. No approximate-accounting fix is inferred for M5.

## State Machine Review

Repair entry requires retained released genuine handoff, valid operation budgets, serialization and human authorization. Happy-path actual repair → close/reconcile → current NORMAL resumption and reprice barriers continue to use retained owner/task state. M1's interrupted COMPLETE branch now checks terminal state immediately and preserves cancellation/deadline primary semantics.

Exception cleanup is composed with existing monotonic operational close infrastructure, exact registry scope and terminal failure retention; old B1 escapes are fixed. M2-R shows classification happens too late to keep the retained terminal result coherent. B1-R shows operation-time validation has no within-operation backward-sample barrier. M4 shows resting monitor uses a different sufficiency predicate from new-risk entry. These are local consistency defects, not grounds to invent another state machine.

Current full M5 must remain an unresolved required NORMAL compatibility concern until its actual cause is understood; a passing isolated execution is insufficient to declare the positive operating path reliably complete.

## Test Review

All pytest commands run from `/home/ubuntu/poly-maker`, using `.venv/bin/python` and pre-import blocker `docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py` (SHA256 `bac8efb151d4e69c33bad9ef345bb9ad1c6d4e412aea38d0ec6d2a7e80fe9641`). Bytecode is disabled and runner pytest cache is disabled. Every resulting pytest log ends with an empty network-attempt list.

**F — Independent exact repaired-tree full suite**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no > /tmp/ITER-0029-rereview-01-evidence/full.log 2>&1
```

**1 failed, 1738 passed, 2 skipped in 102.58s**, exit1. Failure: the positive NORMAL case in M5. This exact-tree failure is preserved; Worker04's different full1739/2 run is not erased or treated as proof this run passed.

**O — Unchanged corrected original independent probes**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/probes_v2.py /tmp/ITER-0029-review-evidence/extra_v3.py -k 'not normal_proof_order_variation' > /tmp/ITER-0029-rereview-01-evidence/original_corrected_probes.log 2>&1
```

**12 passed, 1 deselected in 3.47s**, exit0. These source files are unchanged from original review; byte-identical copies are included in the new evidence directory for durable reproducibility.

**A1/A2 — Additional mandatory clock/evidence/category/time combinations**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-rereview-01-evidence/additional_probes.py > /tmp/ITER-0029-rereview-01-evidence/additional_probes.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-rereview-01-evidence/additional_probes_v2.py > /tmp/ITER-0029-rereview-01-evidence/additional_probes_v2.log 2>&1
```

A1: **1 collection error in0.31s**, exit2, incorrect scratch import; no production conclusion. A2: **5 failed, 13 passed in2.25s**, exit1. Exact failures: cached category time/capital/market, full monitor operation budget, within-market backward clock. Passed: state category, ten bool/string/NaN/infinity/pre-market entry/live clock shapes, and pending/malformed registered fill cleanup. Sources/logs are additive and immutable.

**I — Finite actual positive NORMAL proof instrumentation**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/extra_v2.py -k normal_proof_order_variation > /tmp/ITER-0029-rereview-01-evidence/normal_instrumented_once.log 2>&1
```

**1 passed, 3 deselected in3.98s**, exit0. It invokes the exact existing positive integration case and only records a supplied-versus-fresh certified projection mismatch if owner release fails. No mismatch was printed. This isolated pass does not supersede F or establish M5's root cause.

**L — Bounded offline arithmetic/layout hypothesis checks**

```bash
python3 /tmp/ITER-0029-rereview-01-evidence/proof_layout_search.py > /tmp/ITER-0029-rereview-01-evidence/proof_layout_search.log 2>&1
.venv/bin/python /tmp/ITER-0029-rereview-01-evidence/proof_layout_search.py > /tmp/ITER-0029-rereview-01-evidence/proof_layout_search_py312.log 2>&1
```

Both exit0 and say `No differing layouts in seeds0..127`. Script executes local arithmetic-only child Python interpreters, simulating the explicitly specified set construction/cost values; no project import/network. It is a reduced diagnostic, not a trading acceptance test, proof of accounting invariance or verified root cause. A direct arithmetic check confirms float addition order can alter a last bit in general, but the reduced searches do not demonstrate that this caused either real test failure.

The independent full run includes modified durable Fix01 tests and all existing affected/adjacent/FL regressions; no unnecessary separate repeated unchanged broad run was performed. The failures justify ordinary repair and appropriate renewed exact-tree checks. Future re-review must satisfy the complete eleven-criterion matrix, not just O passing.

Evidence root: `/tmp/ITER-0029-rereview-01-evidence/`. Every scratch source/log/final tree snapshot is listed with its exact SHA256 below. Original review evidence under `/tmp/ITER-0029-review-evidence/` and repository immutable03 are untouched. Runner remains archived in ITER-0026. Parent Orchestrator may copy the exact new evidence bytes into durable iteration archival without editing them.

```text
648b57f0cb45d5ed4018a6f8ab6ffd1fb7afc3580ecddce164f8280d31bdd2d6  /tmp/ITER-0029-rereview-01-evidence/actual_owner_diff.patch
9d8b94d8c5cd88a95b6a2065987959f88e6cb603284699fff38bf576ba1caf9f  /tmp/ITER-0029-rereview-01-evidence/actual_runtime_diff.patch
e43271620754c95d2c3e61b99e039b729cf582c2044d621f19b050ecfff75a77  /tmp/ITER-0029-rereview-01-evidence/additional_probes.log
93b7911085c2750b7b90b5a4cc808019cb138c4a44841d742df9472022079bed  /tmp/ITER-0029-rereview-01-evidence/additional_probes.py
b7d3fda81a635d4b9b596b14d10fc53b1a39b1c1bc87d963fccf90d96d042fd1  /tmp/ITER-0029-rereview-01-evidence/additional_probes_v2.log
2b1b5a2dc71ae7123882b55f18f45ceb6ec44cd6803cfecb9ae73ba8a0d5531b  /tmp/ITER-0029-rereview-01-evidence/additional_probes_v2.py
266b3725f13602281b15454aa47c8f341f49a5fab60000fa0e30e3efdf113e76  /tmp/ITER-0029-rereview-01-evidence/extra_v2.py
a6ffb74f4e165cf824af61dfe92269bc1be98a48d2b1753017d40755fb6ae63e  /tmp/ITER-0029-rereview-01-evidence/extra_v3.py
dfd86fedce0e51dc22178cafae35de5766e219adf8cfcf7e04e538ae2ba629d5  /tmp/ITER-0029-rereview-01-evidence/final_repository_checks.json
030ec8dac78bc124eb33ad58ff5fb206846103e9a46287cfb7fb35dcb6949aea  /tmp/ITER-0029-rereview-01-evidence/full.log
8b54db31c9afdd1dcb5b3977dde900950c1656d762cd5fe1710344271cd5da16  /tmp/ITER-0029-rereview-01-evidence/normal_instrumented_once.log
136e360c979dac606fc7f250b31a6692fd1125fb24735ea46f6332fdd96ab372  /tmp/ITER-0029-rereview-01-evidence/original_corrected_probes.log
8935d756aaab1f1d8cc2dd6d2b76e876155691ba38a2ad8f9041d9e2fd34467a  /tmp/ITER-0029-rereview-01-evidence/probes_v2.py
5dd6e0f831e53effe1d2b6cbe7677dac192e44382a2e8c628841febc7ea90479  /tmp/ITER-0029-rereview-01-evidence/proof_layout_search.log
4724cd1964b7c5e298cd8eaab4123c7403fea8fd0b8d63fc1a35eece521076c7  /tmp/ITER-0029-rereview-01-evidence/proof_layout_search.py
5dd6e0f831e53effe1d2b6cbe7677dac192e44382a2e8c628841febc7ea90479  /tmp/ITER-0029-rereview-01-evidence/proof_layout_search_py312.log
1dba4e7b25472c7d7e859c9fce39106de27724b84e6adfacfbb77d897573d1b9  /tmp/ITER-0029-rereview-01-evidence/reviewed_hashes.json
```

Manifest path: `/tmp/ITER-0029-rereview-01-evidence/SHA256SUMS`.

## Scope Review

All production/test edits belong to authorized seven-file ITER-0029 scope, with Fix01 specifically confined to runtime and placement tests. No strategy redesign, unrelated FL/IM refactor, new platform subsystem, external API, dependency, live command, research output or Git operation was introduced. Reviewer is independently read-only and did not implement any finding.

B1-R/M2-R/M4 directly violate explicit current frozen acceptance. M5 is a current required compatibility regression, not a speculative roadmap item or excuse to redesign. These cannot be deferred merely to converge or reassigned to DG-005/007 while claiming DG-004 complete. Worker should diagnose/repair minimally within existing authorized boundaries and escalate only an actually demonstrated scope/semantic conflict. No additional next iteration is started by this report.

## Residual Risks

- Confirmed current B1-R/M2-R/M4 and unresolved current M5 are unwaived acceptance failures; they are not accepted residual risk.
- Exact internal reason/cause of the current NORMAL full-suite failure is not captured by the chosen suppressed-capture full command. Historical CLOSE_PROOF_NOT_RELEASED and reduced float/timing hypotheses must remain distinguishable from present verified assertion evidence. Further finite Worker diagnosis is required.
- Historical idle pytest processes establish no failure causality; none were terminated.
- Synthetic/fake-official SDK tests cannot prove real venue ack/fill timing, economic performance or live readiness. No real-money trading is authorized.
- Known clean subset reporting remains explicitly incomplete when unknown acknowledgements/failed fills exist, with no current capital certification or invented close proof. Repair must preserve that safety distinction.
- Other delivery gaps and final independent readiness review remain required; no main merge or live authorization is granted.

## Final Decision

**RETURN_TO_WORKER. BLOCKER=1 remaining family; MUST_FIX=3.**

Original concrete M1 and M3 triggers are closed; original B1 malformed/exception/quarantine triggers and M2 admission trigger are independently repaired. Full required acceptance still fails B1-R backward-clock handling, M2-R cached terminal classification, M4 complete monitor time predicate, and M5 current exact-tree NORMAL regression concern.

Perform ordinary authorized local repair with durable integrated regressions. Preserve current primary SDK interruption/drain ordering, exact registered cleanup, pending-invalid and malformed-clock fail-closed handling, known cumulative subset inventory/cost, attempt-matched close proof and successful held-close fill rechecks. Preserve the current and historical NORMAL failures; capture actual diagnostic evidence before attributing or repairing their cause. Return the exact repaired tree to independent Reviewer for complete DG-004 acceptance review and appropriate passing required regression checks.

DG-004 and DG-002/other recorded open gaps remain OPEN. This iteration is not CODE_READY or MICRO_LIVE_READY. No Frozen/strategy/external-interface change, real order/cancel, main merge or next implementation is approved by this report.
