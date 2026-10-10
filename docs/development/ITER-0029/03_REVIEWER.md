# ITER-0029 — Reviewer Report

## Verdict

RETURN_TO_WORKER

Independent actual-tree review finds **1 BLOCKER family and 3 MUST_FIX findings**. The new REBALANCE happy path operates through real fake-SDK submissions and can resume the existing NORMAL continuation, but required fail-closed composition, safety-predicate termination, interruption output and current cumulative reporting are defective. **DG-004 remains OPEN.** This is neither a helper PASS nor a partial-prerequisite PASS. Full delivery acceptance has not closed.

The defects are ordinary implementation defects within the frozen contract. They require Worker repair and independent re-review, not a strategy redesign, external-interface change or additional human architecture decision. No live trading or cancellation was performed.

## Reviewed Inputs

Repository: `/home/ubuntu/poly-maker`. Review date: 2026-10-10. This review follows the actual repository AGENTS.md, Frozen SOP and ITER-0029 contract rather than session claims.

Read and inspected:

- `AGENTS.md`, `docs/strategy/RDP_PHASE0_FROZEN.md`, `docs/development/BASELINE.md`, current `ITERATION_INDEX.md` and `DELIVERY_GAPS.md`.
- Frozen `docs/development/ITER-0029/01_ARCHITECT.md`, including its eleven acceptance criteria, required fault-injection tests, observability and non-goals.
- Immutable `02_WORKER.md`, `WORKER_EVIDENCE_ARCHIVE.md`, Worker evidence manifest and relevant successful/failing logs. The Worker explicitly acknowledges missing repair-specific held SDK POST and registration-failure injections; those combinations were independently exercised here.
- Actual seven-file implementation/test diff, modified tests, relevant existing retained NORMAL continuation, gateway guard/capture/SDK drain, placement owner, account parser/backing, projector/close proof, registered fill processing, inventory accounting, quote builders and lifecycle/reconciliation components.
- Relevant already-reviewed retained-cycle/cumulative-scope behavior from ITER-0028; it is reused rather than reimplemented.

Authority/provenance SHA256 values:

| File | SHA256 |
|---|---|
| `AGENTS.md` | `0dedaab0b6653bd030d01c494d42d10619727f0e9f78bed9703002816538aa25` |
| `docs/strategy/RDP_PHASE0_FROZEN.md` | `92e455097c7567007ed8688faae81241588d77af2f614cd3f8a663486856d32d` |
| `docs/development/BASELINE.md` | `8fbf83198838af4fe9a46daee803a3fe4379139c429d65b7278f7ae29106bfe7` |
| `docs/development/ITER-0029/01_ARCHITECT.md` | `fa2c9fab2346b94de58353c5564e9dbe6cc85bc6a6d34d01a0859c4ceb62423c` |
| `docs/development/ITER-0029/02_WORKER.md` | `7cb7ecbc1590787697c38a8ef146ab9bba2ec493d5524f3d56fca8ff2ecfad31` |
| `docs/development/ITER-0029/WORKER_EVIDENCE_ARCHIVE.md` | `8b5d9f12ae0a9fd0e411e22b40678736b43699c1faffdeaac27e5a3d059ac657` |

Worker test claims were treated as evidence to inspect, not authority for the current tree. The independent full-suite run below substantiates the exact reviewed implementation tree.

## Repository / Diff Reviewed

- Branch: `rdp-phase0-dev`.
- Base and current HEAD: `3286e56ac4e8ac3f72ccf0edda7722d9dcbc3827`.
- No source or tests were edited during this review; no Git history operation was performed.
- Actual authorized implementation diff: seven files, **1841 insertions / 25 deletions**. Source/test SHA256 values at final inspection are below.
- Additional Orchestrator stage documentation changes: `ITERATION_INDEX.md` and `DELIVERY_GAPS.md`; complete ordinary diff is nine files, **1846 insertions / 30 deletions**.
- Staged diff is empty. `git diff --check` exits 0 with no output.
- Relevant untracked files are Worker02, Worker evidence archive/manifest and its lossless compressed log. Ignored Worker logs were inspected directly. Pre-existing untracked `research_outputs/` was explicitly inventoried and preserved; it is outside implementation/review scope. No cleanup, stash or overwrite occurred.

| File | SHA256 |
|---|---|
| `src/polymaker/execution/gateway.py` | `c1d809cb09d2c697b02cad7e939fc6dc568a28ba2db5750dfbc70df8e2a47da8` |
| `src/polymaker/execution/rdp_placement.py` | `5d02f872f28e0e83f31695c5eeafa63ed7f4ffe1be3ba8150c8f987da99eadc6` |
| `src/polymaker/rdp_runtime.py` | `c81e5b4d35603ff5e86086bca1e598a4af2efd8b0643d4babd016976ccc4d862` |
| `src/polymaker/risk/rdp_capital.py` | `d3208986ba14d0e57402c9b22bdd5d486a7c2d5bbb742443c8ee1e0432e1dbd6` |
| `tests/test_execution.py` | `df6f2ea4ccfd4f1c4550d0cb7c5a243f4dfe3be6843fb01fe90bb9d58ddefb2a` |
| `tests/test_rdp_capital.py` | `687de05bd028554081f632c206d5b435cffea219d065fd8fcdd8e1c8b1344218` |
| `tests/test_rdp_placement.py` | `75eb72cbda33f0b5172ee77a6956f7f4dcfb470435d6e1b72d60789d841fb20c` |

Read-only final Git commands and full stdout/stderr are preserved in `/tmp/ITER-0029-review-evidence/final_repository_checks.json`: `git status --short --branch`, `git rev-parse HEAD`, `git log --graph --oneline --decorate -15`, `git diff --stat`, `git diff --numstat`, `git diff --check`, `git diff --cached --name-status`, `git ls-files --others --exclude-standard`, and `git diff --name-only`. Hashes of authorities and the exact implementation are in `reviewed_hashes.json`.

## Acceptance Criteria Review

Status refers to this exact tree, not a future repair or Worker assertion. A passing happy-path test does not discharge a required fault path.

| Frozen criterion | Independent evidence and result | Status |
|---|---|---|
| 1. Retained initial/later handoff, one operating call, actual NORMAL dual POST, default denial/API compatibility | Actual integrated tests exercise initial and later handoffs and real fake-SDK NORMAL resumption; explicit `authorization is True` and shared conflict gate are present. The full suite passes. However interruption and unreliable-state paths do not satisfy the operating call's terminal contract. | Partial; B1/M1 block full acceptance |
| 2. Both signs, exact ±5/larger and strict `<5`, five-share deficient BUY at BestAsk-currentTick, post-only | Mode-aware arm/registration/close checks, quote builder and strict-single gateway are inspected. Integrated both-sign/larger-delta and recovery tests pass in the independent full suite. No opposite-side repair or overlapping NORMAL POST is introduced on the healthy path. | Supported healthy path |
| 3. Strict opted-in single ack; clean rejection versus malformed/nonboolean/multiple/conflicting uncertainty; legacy/pair compatibility | Actual parser and gateway diff inspected. Canonical strict parsing and observed/acknowledged distinction are tested. Existing legacy/pair tests remain passing. Per-ID registration before/after injection verifies uncertainty does not authorize unknown-ID cleanup or a retry. | Supported ack/ownership safety; reporting defect M3 |
| 4. Fractional confirmed fills, cumulative cost/inventory across repair and resumed NORMAL, replay and late evidence | Actual projector/owner/processor composition and integrated fractional tests pass. Independent held current repair fill updates the actual resumed NORMAL pair to current 5/6 skew; a valid late prior NO fill invalidates prior certification and stops without a new pair. Registration-before failure drops known prior QY=5 in both returned inventory views. | FAIL M3 |
| 5. Ask/tick reprice only after real close; late cancel/SDK drain ordering | Actual dynamic-tick/ask tests pass. Independent held SDK POST confirms no return, cleanup or replacement before worker drain for deadline/cancel/teardown. Deadline and teardown then enter an already-terminated attempt and raise a secondary placement error. | Partial; FAIL M1 |
| 6. Recovery cancel remainder, barrier and current inventory recheck before actual skewed NORMAL | Independent held-close current-fill and valid prior-fill mutations pass: no early POST, updated NORMAL skew for current fill, stale prior close never grants new risk. Healthy recovery tests pass. Required interruption handling remains defective. | Supported race behavior; M1 blocks complete lifecycle acceptance |
| 7. Model30, physical cash, allowance/account commitments at admission/final guard/monitor; active versus closed evidence | Actual backing/projector diff preserves active-owned matching rather than treating it as closed resurrection, counts commitments and retains current-cost projection. Existing contradiction/close-scope regressions pass. Initial capital-failure handling silently borrows NORMAL retry semantics. | FAIL M2 |
| 8. All four actual CanSafelyContinue predicates, categorical StopReason, scoped cleanup/no re-entry, exact-cash compatibility | Cash=0 admission loops then returns an unlatching TIME_EXHAUSTED completion. Unreliable outer clock allows later POST; invalid monitor clock leaves live repair uncancelled; quarantined inventory escapes without STOP. Categorical stop logs also disagree with the actual predicate. | FAIL B1/M2 |
| 9. Official tick, freshness, cross/metadata/account/fill barriers through final POST mutation | Healthy pre-sign/pre-POST and post-account-await guard tests pass. Pending/quarantined state cannot be allowed to escape the terminal lifecycle after an accepted repair; independent quarantine and invalid-clock injections fail. | FAIL B1 |
| 10. Safe unchanged no-fill order rests; no hard rebalance duration/quota/no-fill stop | Inspected monitor retains unchanged safe repair, and no-fill tests pass. There is no introduced MaxRebalanceSec, quota or no-fill stop. Natural time insufficiency and invalid time evidence still require the frozen terminal behavior. | Healthy no-fill supported; M2/B1 block terminal edge |
| 11. Structured entry/attempt/reprice/recovery/STOP economics, current proof/certificate, regressions/protected scope | Entry/recovery timestamps and economic differences, actual fill prices/sizes and contemporary book/tick observations are present for later DG-009 assembly. Full suite passes 1731/2. STOP receipt combines the new repair ID with an earlier cycle's close proof; known cumulative inventory can disappear. | FAIL M3; categorical output M2 |

The acceptance matrix therefore cannot close DG-004. DG-002 and other open delivery gaps remain OPEN; this review does not claim MICRO_LIVE_READY, CODE_READY or real-market validity. Remaining combinations should be independently rechecked after the ordinary repairs rather than expanding this already-failing review into unrelated implementation work.

## Existing Codebase Compatibility

Worker appropriately reuses the official input/account path, gateway, final guards, live-send gates, captured ack resolution, owned fill processor, local order store, projector, reconcile/close barrier, quote builders and retained NORMAL loop. Strict single ack is an opt-in mode in the existing gateway; legacy single and strict pair behavior remains covered. Mode/cardinality is propagated through the existing owner and close proof instead of adding another order registry or inventory store.

Recovered NORMAL execution calls the established `_run_normal_continuation`; there is no parallel NORMAL execution engine. A separate repair monitor is justified by the frozen no-duration/no-fill and changing highest-maker target semantics. No working subsystem is unnecessarily replaced, and no new external gateway is introduced.

The defects arise at composition boundaries: NORMAL retry policy is used during REBALANCE safety failure; the shared submission result is consumed after it has already latched STOP; and existing cumulative capture assumes acknowledged history is entirely registered. Reuse does not by itself prove those new combinations safe. Repair should extend these existing boundaries locally, preserving reviewed NORMAL compatibility and exact ownership rules.

## Findings

### BLOCKER

**B1 — Unreliable REBALANCE evidence can escape without terminal latch or leave the accepted repair live without scoped cleanup.**

Production locations: `src/polymaker/rdp_runtime.py:1168` public shield/cancellation handling; `:1210` outer `_now()`/time arithmetic; `:1328` monitor result handling; `:1436` early monitor clock failure; `:1454` unguarded cumulative `snapshot_inventory`. The public wrapper catches cancellation, but does not compose arbitrary unreliable clock/inventory failure into retained terminal cleanup.

Minimal independent triggers and observations:

1. Start with a genuinely completed/released initial NORMAL cycle containing confirmed QY=5, QN=0. Make `_now()` raise `RuntimeError` just before authorized `continue_rebalance`. The exception escapes; `_terminal_stop_latched` stays false. Restore the clock and call again: **two actual fake-SDK POST calls occur**, ending `REBALANCE_HANDOFF`, latch still false. Output: `CLOCK_REENTRY RuntimeError REBALANCE_HANDOFF 2 False`. This is concrete later new-risk admission after an unhandled unreliable-state failure.
2. After a canonical single ack registers a live zero-fill repair, make clock return NaN. Monitor returns `(None, STATE_UNRELIABLE)` and the outer loop latches, but never routes through scoped cleanup. Returned completion is TERMINAL; **cancel count is zero and the selected registered order remains LIVE**. A log additionally falsely uses the previous cycle's completed close, addressed in M3.
3. Quarantine the accepted current repair during a monitor poll. Its next inventory snapshot raises `ValueError`; continuation escapes with **latch false, cancel count zero and current repair LIVE**. This is an ordinary realistic unreliability signal from the existing fill processor, not a speculative external exception.

Expected: unreliable time/inventory evidence must latch the existing no-new-risk STOP, drain any started SDK operation, and attempt only exact currently registered-owned cleanup with the caller's close budget. If reconciliation cannot prove closure, preserve explicit incomplete/unresolved exposure and known clean cumulative fills; do not release an owner, certify capital or admit a later operation. Invalid clock must not prevent cleanup from using the retained operational monotonic deadline. Later calls must remain gated.

Evidence: `probes_v2.py::test_repair_outer_clock_failure_latches` and `::test_repair_monitor_unreliable_evidence_cleans[clock/quarantine]`; exact reproduction command P2 below; all three fail on this tree. `probes_v2.log` contains accepted LIVE rows, no cancels, re-entry POST count and no network attempts. Repair requires durable integrated clock exception/type/nonfinite/backward and post-ack quarantined/pending-invalid evidence regressions, asserting terminal latch, exact cleanup/incomplete output and no later risk. Use existing terminal/drain infrastructure; no new architecture is needed.

### MUST_FIX

**M1 — Interrupted COMPLETE single acknowledgement is monitored after STOP and cleanup, replacing the primary operation outcome with a secondary `RDPPlacementError`.**

Production locations: `_run_rebalance_continuation` at `rdp_runtime.py:1277` submission await and its subsequent COMPLETE branch, compared with the retained NORMAL flow's post-submission terminal check. The shared submission machinery correctly drains the SDK worker, preserves the original interruption cause, registers attributable ack/fills and performs exact cleanup. The REBALANCE consumer fails to honor that already-terminal result before entering its monitor.

Minimal trigger: start genuine +5 handoff; use the real existing `ExecutionGateway._io` thread wrapper with a fake SDK `post_orders` held after it creates canonical ack and a confirmed NO fill of 1. Expire order budget while held, or invoke session teardown while held. Both the continuation and teardown correctly stay pending until worker release; no cancellation occurs before release. After release, **one POST and one exact cancel of `cycle-1-no-token`** occur; terminal inventory is QY=5/QN=1, and cause remains `ORDER_OPERATION_DEADLINE` or `SESSION_TEARDOWN`. Nevertheless the caller receives **`RDPPlacementError`** instead of the primary deadline terminal completion or teardown cancellation. The caller-cancellation branch passes and raises `CancelledError`; `aclose()` itself returns normally after drain.

Expected: consume STOP immediately after the awaited submission, retain primary cause/terminal proof and output, and do not start an ordinary repair monitor/close on an already-cleaned terminal attempt. Deadline should return its attributable terminal completion; teardown should retain documented continuation-cancellation semantics. Do not weaken drain ordering or adopt/cancel observed-but-unregistered IDs.

Evidence: `probes_v2.py::test_repair_held_sdk_post_drains[deadline/cancel/teardown]`, P2. Deadline and teardown fail; cancel passes. This directly supplements the Worker's acknowledged lack of repair-specific held POST evidence. Preserve the passing no-return/no-cancel-before-drain and known-fill assertions in regression tests while fixing terminal result consumption.

**M2 — REBALANCE admission/time safety failures use NORMAL retry/nonterminal semantics and emit incorrect categorical StopReason.**

Production locations: `rdp_runtime.py:1210` time sufficiency return; `:1223` `_normal_continuation_rejection_retryable` branch; retry list `:6371`; STOP receipt `:1928`; `_latch_terminal_stop` at `:3245` hardcodes logged `STATE_UNRELIABLE` regardless of actual predicate.

Minimal trigger: genuine closed +5 handoff, fresh official-form account capture with physical cash=0 and allowance=0. Admission truthfully rejects `INSUFFICIENT_CURRENT_CASH`. Instead of CAPITAL_INSUFFICIENT STOP, repair waits as a NORMAL retry would. A deterministic sleep advances clock to insufficient remaining order+cancel+reconcile time. Completion becomes `TIME_EXHAUSTED` with reason `INSUFFICIENT_ORDER_CANCEL_RECONCILE_TIME`, **terminal latch false**, and no SDK POST. Output: `ENTRY_CAPITAL TIME_EXHAUSTED INSUFFICIENT_ORDER_CANCEL_RECONCILE_TIME False ['INSUFFICIENT_CURRENT_CASH']`. The absence of a live current repair at this start does not excuse an unlatching safety failure; residual inventory still needs the frozen terminal action. A fresh later call is not prohibited by a STOP latch.

Separately, nonretryable detailed admission reasons are fed directly into `rdp_rebalance_stopped.stop_reason`, while `rdp_terminal_stop_latched.stop_reason` always says STATE_UNRELIABLE. Thus time, capital or market predicates can produce a detailed noncanonical stop value or two conflicting categorical fields. Frozen allowed categories are TIME_EXHAUSTED, CAPITAL_INSUFFICIENT, MARKET_NOT_TRADABLE and STATE_UNRELIABLE; diagnostics belong in a separate cause/reason.

Expected: apply the actual four frozen REBALANCE continuation predicates; valid insufficient operational time or current capital must terminally latch the appropriate category and preserve precise diagnostic cause. Use current exact cash/allowance/model/account commitments; do not invent reserve/headroom or release active commitments. Clean stale quote/tick/filter pre-POST no-send re-admission remains legitimate where fresh evidence can still satisfy all predicates. Do not change existing NORMAL-only retry policy in order to fix the new REBALANCE composition.

Evidence: `probes_v2.py::test_repair_initial_capital_predicate_stops`, P2, fails. Log includes truthful admission rejection followed by unlatching time completion. Code inspection and STOP receipts substantiate categorical misreporting. Worker must add integrated entry/time-boundary and each allowed-category assertions, including current active reservation/exact-cash behavior, with no later risk and honest cleanup.

**M3 — Terminal completion loses known prior inventory on unregistered repair ack and associates current repair with an earlier cycle's close proof.**

Production locations: `rdp_runtime.py:3368` `_capture_terminal_inventory`, using `placement_owner.registered_run_order_ids` for continuation without filtering actual registered ownership before `snapshot_inventory`; `:1926` `_continuation_completion` chooses `cycles[-1]` for STOP receipt independently of the latest repair attempt.

Minimal registration trigger: genuine initial QY=5/QN=0 and canonical repair ack; make the single processor registration fail before ownership is installed. Correct safety behavior is already observed: no retry, STOP latch, no cancel of the unregistered ID, UNKNOWN terminal exposure, no capital certification. However ack history includes that unregistered ID; cumulative inventory snapshot over the combined IDs fails as a whole. Actual registered clean inventory independently remains **QY=5/QN=0**, yet both returned completion inventory and terminal confirmed_inventory are **None** with `REGISTERED_ORDERS_UNAVAILABLE`. Output: `KNOWN_PRIOR 5.0 0.0 None None () ('cycle-1-no-token',)`. Known prior acquisition cost 2.75 is not evidence of unknown repair fill and should remain reported as a clean scoped subset, without pretending total exposure is fully resolved.

The registration-after variant correctly reconciles/cancels its actually registered ID and retains QY=5/QN=1. These passing attribution boundaries must remain intact.

Minimal reporting trigger: either before-registration failure or accepted live-repair invalid monitor clock from B1. `rdp_rebalance_stopped` labels `attempt_id=2`, acknowledges `cycle-1-no-token`, yet reports **closed_order_ids=('no-order','yes-order'), close_complete=True, replacement_blocked=False** from initial attempt1. In the NaN case the actual current repair is LIVE with zero cancels. Capital_certified remains false and unresolved_exposure true, so this is not an actual capital grant, but the cleanup proof receipt itself is false/mismatched. Required structured evidence must not promote an older cycle's barrier to current repair completion.

Expected: retain known current registered clean cumulative fills/cost and explicit scoped IDs despite an unregistered/uncertain new ack; report incomplete total exposure and no current capital certification. Terminal cleanup still targets only exact attributable registered current IDs. Derive latest repair cleanup fields from its matching current close/terminal result; if no current barrier exists, report missing/incomplete rather than inheriting the earlier completed cycle. This is read-only evidence derivation, not a new read, owner release or separate state machine.

Evidence: `extra_v3.py::test_registration_before_preserves_prior_inventory`, E3 below, fails; direct before/after ownership injections in P2 otherwise pass. Both logs preserve mismatched STOP receipts. Regression tests must assert known prior fills/cost, unregistered-ID uncertainty, no accidental cancellation/adoption, and attempt/proof-matched STOP receipts for complete, incomplete and absent current cleanup.

### NOTE

**N1 — Preserved intermittent NORMAL `CLOSE_PROOF_NOT_RELEASED` remains unexplained, not claimed fixed.**

Worker archived `affected_rdp_execution_final.log` records 699 passed / 1 failed in `test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively[fills1-5.0-False]`; isolated/affected/full reruns pass. Original log SHA256 is `407c8dbde23265c47ee8004d9629abb06d5d81ddc40cd3063c17d5cbaf4c151e`. Inspection shows a complete/certified reconcile result but a subsequent owner-release proof rejection. This is not sufficient to assert a particular root cause.

Independent full suite passes. A bounded instrumented rerun of that exact positive integrated NORMAL case at PYTHONHASHSEED 0 through 7 also passes all eight times, with no fresh-versus-supplied certified-proof mismatch printed. Strict equality or ordering sensitivity is only a possible hypothesis, not a verified defect. Do not erase the original log, declare it conclusively repaired, or modify unrelated NORMAL code based on this hypothesis. Retain it as diagnostic uncertainty and check NORMAL compatibility again during ordinary repair/re-review. No extra architecture work is requested.

**N2 — Evidence-development corrections are preserved.**

Original `probes.py/log` has two registration diagnostic attribute errors; `probes_v2.py/log` corrects the field to `unregistered_acknowledgment_ids` and refines actual registered reconciliation assertions. Original `extra.py/log` accidentally collects an imported existing parametrized test, producing a large output; `extra_v2.py` aliases that import. Its original prior-fill mutation also exceeded the filled YES order's registered size and therefore tests quarantine, not a valid late prior close-proof mutation. `extra_v3.py/log` uses an unfilled registered prior NO order for a valid late confirmed fill. Old versions are retained unchanged. Only valid corrected cases substantiate findings and positive race conclusions.

## Frozen SOP Compliance

Healthy repair semantics match Frozen §§23–27: absDelta≥5 REBALANCE, strict `<5` NORMAL, five-share deficient-side BUY at current highest noncrossing maker, post-only, and no no-fill/hard-duration stop. Inventory economics remain confirmed acquisition cost rather than mark-to-market/PnL strategy gates.

B1 and M2 conflict with the frozen safety requirement to STOP when CanSafelyContinue becomes false; M1 violates terminal lifecycle/result composition required by the implementation contract; M3 violates truthful current accounting/proof evidence. These are implementation defects, not accepted reinterpretations. No frozen text was modified.

## External Interface Review

Runtime dependencies remain approved official Polymarket HTTP/WebSocket plus existing SDK integration. No blockchain RPC, third-party runtime data/trading service, CLI/live command, dependency/configuration change or unofficial state dependency was added. The retained method and opt-in strict-single flag are internal extensions explicitly authorized by the contract.

Development-only fake SDK and test runner do not become runtime interfaces. Every independent test used network blocking before imports. No external connection, actual account/order mutation, real order or cancel was attempted.

## Trading Safety Review

Default authorization denial, final send guards, POST-only constraint, per-ID ownership, observation-versus-acknowledgement distinction, capture-before-POST, and current capital/freshness checks remain present. Denied/concurrent calls cannot post on healthy reviewed paths. Strict response uncertainty does not become invented ownership.

Independent held SDK tests prove real gateway worker drain ordering for the repair composition: the operation does not finish, cleanup does not start, teardown does not complete, and replacement does not POST while the worker is held. After release, exact registered cleanup occurs; this positive safety distinction is preserved even though M1's caller result fails.

B1 is a confirmed trading-safety blocker: accepted repair can remain live without terminal cleanup and an unreliable pre-operation failure can permit later new risk. Scope-local repair must preserve terminal latch, worker drain, exact cleanup and honest unresolved state under all required failure paths. Reconciliation refusal is never a reason to invent closure or release capital.

## Inventory / Capital Accounting Review

Healthy full-path fractional and multiple-attempt tests use one retained registered run scope, confirmed fill cost, current resting commitments and accumulated proofs. Model budget and actual physical cash/allowance are separate constraints. Active repair backing is matched by current exact registered IDs; closed-ID resurrection/foreign or contradictory evidence is not normalized into a safe state. Existing exact-cash admission remains supported without new hypothetical reserve.

Held current repair close mutation retains additional fills before resumed NORMAL sizing. Held valid prior fill mutation retains QY=5/QN=2/Delta3, invalidates previous close evidence (`STALE_CLOSE_EVIDENCE`), leaves certification false/unresolved true and prevents a resumed pair. This is the required no-stale-proof safety behavior.

Registration-before M3 must retain known prior QY=5/acquisition cost2.75 with explicitly incomplete total scope; it must not claim unknown repair quantity/cost. No failing independent probe observed an improper actual capital grant from the false close log: certification stayed false. Nevertheless false cleanup evidence and lost known inventory block contract completion. M2's absent-capital predicate must stop rather than wait under NORMAL policy.

## State Machine Review

Entry requires a retained released REBALANCE cycle, exact true authorization, valid operation budgets and serialization against conflicting operations. Actual current cumulative recovery can call the existing NORMAL loop and make a real fake-SDK dual POST. Reprice and recovery close before replacing; no unchanged-target repeated POST is present.

B1 leaves unreliable-state exit outside the retained terminal machine. M1 attempts to continue a terminal attempt after shared submission interruption. M2 returns a nonterminal time result and repeats current-capital rejection in REBALANCE. All three require local state-machine composition repair. Cancellation behavior which already passes must remain: continuation caller sees cancellation, teardown itself returns after drain, and no owned remainder is silently released.

## Test Review

All commands below were run from `/home/ubuntu/poly-maker`; exact logs are retained. Network blocker runner SHA256: `bac8efb151d4e69c33bad9ef345bb9ad1c6d4e412aea38d0ec6d2a7e80fe9641`. Environment prefix suppresses bytecode; the runner disables normal pytest cache and blocks DNS/socket use before imports. Every pytest log ends `REVIEW_NETWORK_ATTEMPTS []`.

**F — Independent exact-tree full suite**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q --tb=short --show-capture=no > /tmp/ITER-0029-review-evidence/full.log 2>&1
```

Result: **1731 passed, 2 skipped in 100.82s**, exit0, zero network. This includes all modified test files and existing adjacent/FL regression tests. It substantiates general compatibility, not the previously missing fault combinations.

**P1/P2 — Repair-specific held POST, registration, unreliable clock/inventory and entry capital**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/probes.py > /tmp/ITER-0029-review-evidence/probes.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/probes_v2.py > /tmp/ITER-0029-review-evidence/probes_v2.log 2>&1
```

P1: **8 failed, 1 passed in 3.03s**, exit1; includes two diagnostic harness errors described in N2. P2: **6 failed, 3 passed in 2.22s**, exit1; the six production failures are held deadline, held teardown, outer clock, monitor clock, monitor quarantine and initial capital. Passed cases are held caller cancel, before-registration ownership boundary, after-registration reconciliation/known fill. Both logs have zero network attempts. P2 is the authoritative corrected failure matrix, not P1's raw failure count.

**E1/E3 — Known prior inventory and held close fill mutations**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/extra.py -k 'not normal_proof_order_variation' > /tmp/ITER-0029-review-evidence/extra.log 2>&1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/extra_v3.py -k 'not normal_proof_order_variation' > /tmp/ITER-0029-review-evidence/extra_v3.log 2>&1
```

E1: **1 failed, 5 passed, 1 deselected in 16.10s**, exit1; imported-test collection and original contradictory prior-fill scope are disclosed in N2. E3: **1 failed, 2 passed, 1 deselected in 5.13s**, exit1, zero network. Only failure is loss of known prior inventory. Current accepted repair fill and valid late prior fill during held recovery close both pass. No early replacement occurs while cancel is held.

**N — Bounded original intermittent NORMAL case investigation**

The following exact command was executed independently for each N=0,1,2,3,4,5,6,7, with the corresponding concrete `PYTHONHASHSEED` and log filename (no further unbounded repetition):

```bash
PYTHONHASHSEED=N PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests:/tmp/ITER-0029-review-evidence .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q -s --tb=short --show-capture=no /tmp/ITER-0029-review-evidence/extra_v2.py -k normal_proof_order_variation > /tmp/ITER-0029-review-evidence/proof_seed_N.log 2>&1
```

Each exits0: **1 passed, 3 deselected**, zero network. These eight runs instrument actual owner proof comparison while invoking the exact integrated positive NORMAL case, and do not reproduce the archived failure. They establish only nonreproduction under those eight executions; they do not prove a root cause or cure. Logs preserve individual duration/result.

No unchanged full-suite rerun is needed merely to write/persist this report. Worker repairs alter production/test concerns and require relevant integrated regressions followed by appropriate affected/full verification. Independent re-review must verify all eleven required criteria before DG-004 can close.

All original probe files, successful/failing logs, final tree checks and hashes are preserved under `/tmp/ITER-0029-review-evidence/`. Exact SHA256 evidence manifest follows; no old probe or log was overwritten:

```text
50f2f6a0f796deabe92b36eb2b070523075429a0953a595f8f1ce5f956b5eb3e  /tmp/ITER-0029-review-evidence/extra.log
d279d59964d5846ed78000248224eb4afdeabf1a90b0a02a6b5186f5554c7280  /tmp/ITER-0029-review-evidence/extra.py
266b3725f13602281b15454aa47c8f341f49a5fab60000fa0e30e3efdf113e76  /tmp/ITER-0029-review-evidence/extra_v2.py
cc3a8d07dd5a5a34d6588c2da512ba1b4f23fbe7d92ec049bf2b8b6b090923c2  /tmp/ITER-0029-review-evidence/extra_v3.log
a6ffb74f4e165cf824af61dfe92269bc1be98a48d2b1753017d40755fb6ae63e  /tmp/ITER-0029-review-evidence/extra_v3.py
edc7bd8e10e7c0123a122f74bb1446c732d6906ec119f0acf95fccd221849f1c  /tmp/ITER-0029-review-evidence/final_repository_checks.json
c132aa90390ce977f43e05b5f8ab4011b1ca8131a031562724ba5fa29b2775e7  /tmp/ITER-0029-review-evidence/full.log
2e52497db4c97c89f22a35083a8a7df1be0961a67b1923c4426d1d4f802d47d7  /tmp/ITER-0029-review-evidence/probes.log
17c82abc86678ffa98ec801a87d582a71f881a308a6d897697f26d74f4d00b9f  /tmp/ITER-0029-review-evidence/probes.py
21bf982def7daa3180c05468351857772ada0c9e6123b0c38d914e261f18677b  /tmp/ITER-0029-review-evidence/probes_v2.log
8935d756aaab1f1d8cc2dd6d2b76e876155691ba38a2ad8f9041d9e2fd34467a  /tmp/ITER-0029-review-evidence/probes_v2.py
51f727560eac5b20a1b33eca8eaf2cfc687f7631954fd10e8a8f38ff4a5190a7  /tmp/ITER-0029-review-evidence/proof_seed_0.log
19dd46cc080c2e3ce7592665eff5861664ece19f4b1bf298017ecc6cd8e19fc0  /tmp/ITER-0029-review-evidence/proof_seed_1.log
39a4e7920c3cd68c613eb0948d4c8664f60fa886d9b9aab9b38450139725210a  /tmp/ITER-0029-review-evidence/proof_seed_2.log
e2a6f3f1f9f8c45dd25889fbef2b025a783c0d2fdd5a5c0cf7c7faafc20ec08f  /tmp/ITER-0029-review-evidence/proof_seed_3.log
b9199e070aba5abf57d4e66ce70805f6c0700547ac2bf72fc736a3aafd7a067b  /tmp/ITER-0029-review-evidence/proof_seed_4.log
af74188b48b8ea2665eb30e46ad9df2a08a5665e6b2a3ea8ec92eb13bd081694  /tmp/ITER-0029-review-evidence/proof_seed_5.log
37e6ae22eb1cd0d3954da75edd83657d75a1bb5db95876d5580a79075bff30a7  /tmp/ITER-0029-review-evidence/proof_seed_6.log
67c836222487afe8351e27dfe208ecc3a0f6776231a4985a30337cf64a86727c  /tmp/ITER-0029-review-evidence/proof_seed_7.log
6a6d561783bb9360a80ce1242eb1ea3c056f5e0643ab13a05f6c839cff122eca  /tmp/ITER-0029-review-evidence/reviewed_hashes.json
```

The manifest itself is `/tmp/ITER-0029-review-evidence/SHA256SUMS`. The parent Orchestrator may copy these exact bytes into durable iteration evidence, including lossless compression of the large original `extra.log`; do not silently edit raw evidence. Initial scratch snapshot command using absent `python` exited127, made no file changes, and was rerun successfully using `python3`; this tooling correction did not affect test results or repository state.

## Scope Review

The seven changed implementation/test files are explicitly authorized. No Frozen SOP, external interface, dependency, live command, shared core, FL/IM strategy source, research output or Git history change was introduced by this review. Owner/capital extensions are necessary to represent an attributable single repair within the existing retained cumulative run.

The findings are required DG-004 acceptance defects discovered on its new mode and lifecycle composition. They cannot be deferred to DG-005/007 or hidden by helper completion. No unrelated historical research/architecture work is requested, and this report does not initiate the next iteration. The original intermittent NORMAL history is preserved without asserting an unproven independent repair scope.

## Residual Risks

- Confirmed B1/M1/M2/M3 prevent safe delivery closure; they are not waived residual risks.
- The archived intermittent NORMAL close-proof rejection remains unexplained despite full and bounded isolated passes. Repair/re-review must keep its original evidence and check compatibility; no unsupported root-cause claim is made.
- Successful tests use fake official inputs and fake SDK calls under pre-import network blocking. They do not validate real venue ack timing, fill availability, market economics or live execution.
- Completion output is read-only derived evidence. It must not release orders/capital or replace authoritative current processor/owner/proof state.
- Remaining delivery gaps, aggregate DG-009 metric assembly and final readiness/live authorization remain separate required work. No readiness, main merge or real-money authority is granted.

## Final Decision

**RETURN_TO_WORKER. BLOCKER=1 family; MUST_FIX=3; PASS is prohibited.**

Repair the existing REBALANCE composition for B1/M1/M2/M3 under the unchanged frozen contract. Preserve the passing worker-drain, exact per-ID attribution, strict-single parsing, held-close late-fill recheck, current cumulative proof rejection, healthy no-fill behavior and existing NORMAL compatibility. Add durable regression evidence for each concrete failing trigger and truthful current cleanup/cumulative inventory receipts. Then return the exact repaired tree to independent Reviewer for complete eleven-criterion DG-004 acceptance review.

DG-004 remains OPEN; DG-002 and other gaps remain OPEN as recorded. This iteration is not CODE_READY or MICRO_LIVE_READY. No human strategy redesign is needed for these ordinary defects. No live order/cancel, main merge or next implementation is authorized by this report.
