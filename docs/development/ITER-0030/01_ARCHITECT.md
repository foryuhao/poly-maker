# ITER-0030 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

Stable Gap ID: **DG-002 — Current Capital and Inventory Across Successive Orders**.

One delivery behavior: **the existing retained single-market execution path preserves its cumulative confirmed inventory and acquisition costs across successive NORMAL and REBALANCE orders, accounts for current commitments once, and permits each actual submission only when both the unchanged $30 capital model and current official account evidence support it.**

This is a **verification-first, production-code-unchanged** contract. Existing operating components appear sufficient. The iteration closes DG-002 only if the complete integrated acceptance below passes independent review.

A defect blocking this acceptance cannot be deferred or hidden by weakening the scenario. Because production changes are not authorized by this initial contract, a required production defect must return to Architect for an additive revision.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Latest archived implementation/receipt HEAD at recovery: `1440a30520d29520904ac1e2fee6eb57b701d3ee`.
- Current initialization HEAD: `48a947ebd354a35e0e743639ab185f350c0a3ccd`.
- The initialization commit changes only `ITER-0030/00_INITIALIZATION.md` and `ITERATION_INDEX.md`; production code and tests remain the archived ITER-0029 implementation.
- Current read-only inspection reports no tracked or staged modifications. Pre-existing untracked `research_outputs/` remains protected and outside iteration scope.
- The parent Orchestrator verified the recovery HEAD against the direct remote branch ref. The initialization commit is a subsequent local documentation stage, not an unexpected production divergence.
- Latest completed iteration: ITER-0029, CODE_READY, final independent `09_REVIEWER_REREVIEW_03.md` PASS; archive receipt exists.
- CLOSED: DG-001, DG-003, DG-004, DG-006.
- OPEN: DG-002, DG-005, DG-007–DG-012.
- MICRO_LIVE_READY: NO.
- Real-money and main-merge authorization: NOT_GRANTED.

Authorities and relevant evidence inspected:

- `AGENTS.md`.
- `.codex/config.toml` and `.codex/agents/architect.toml`.
- `docs/strategy/RDP_PHASE0_FROZEN.md`, particularly §§2–11, 13–18, 23–27 and 35.
- `BASELINE.md`, `ITERATION_INDEX.md`, `DELIVERY_GAPS.md`.
- `ITER-0030/00_INITIALIZATION.md`.
- ITER-0029 RESULT and final Reviewer09.
- ITER-0027 Architect contract and its registered partial-prerequisite boundary.
- Approved ITER-0005 $5 NORMAL repair-reserve decision.
- Existing ITER-0026 blocker-before-import test runner.

Actual relevant source and integration tests were inspected. Historical test totals remain historical evidence; **Architect tests executed: NOT_RUN**.

## Problem

DG-002 remains OPEN despite the completion of its next-action-admission prerequisite and the actual NORMAL/REBALANCE operating paths.

The remaining problem is an **integrated acceptance evidence gap**, rather than demonstrated absence of another trading component:

> The complete successive NORMAL → REBALANCE → resumed NORMAL path has not independently established, against account evidence generated independently of the local processor, that cumulative quantities, costs, current reservations, physical spendability and the original $30 allocation agree before every actual new-risk submission.

Existing focused tests demonstrate valuable pieces. Their presence or prior PASS does not close the complete DG-002 condition.

The smallest defensible next step is to exercise the existing production path with an independent scripted fake venue/account oracle and retain its evidence. Implementing another ledger, capital gate or runner would duplicate existing capabilities.

## Evidence / Current Behavior

1. `RDPInputSession.post_cycle_next_action_admission()` already obtains current market/account evidence, validates retained close/registry provenance, projects the full cumulative run and builds the correct mode-specific candidate.

2. `_run_post_cycle_next_action_admission()` calls:
   - `project_rdp_capital()` with the cumulative run scope and retained close-proof union;
   - `evaluate_rdp_capital_gate()` for NORMAL or REBALANCE;
   - `evaluate_current_rdp_backing()` with current account evidence.
   
   It retains the account/admission and repeats freshness/backing checks after asynchronous reads.

3. `_capture_continuation_normal_intent()` and `_capture_continuation_rebalance_intent()` already reproject cumulative economics at existing SDK guards. They reject changed scope, stale account provenance, changed inventory/capital, changed quote/tick evidence and unsupported current backing.

4. `continue_normal()` operates successive actual gateway submissions and close/reconcile cycles. `continue_rebalance()` consumes retained initial or later handoffs, operates the deficient-side repair and invokes the existing NORMAL continuation after proved recovery.

5. `_monitor_rebalance_attempt()` reads current account evidence while a repair rests. It projects actual remaining orders, matches the active owned scope, evaluates model capital and current physical backing, and closes/reconciles before replacement or recovery.

6. `project_rdp_capital()` already:
   - accepts only registered run orders and confirmed economic fills;
   - validates remaining-size checksums;
   - sums cumulative confirmed acquisition costs;
   - reserves remaining order quantity at its submitted price;
   - requires current, nonoverlapping matching close proofs for absent orders;
   - computes `FreeCash = 30 − InventoryCost − ReservedCash`.

7. `evaluate_current_rdp_backing()` already:
   - reuses strict initial evidence validation;
   - matches selected account quantities against confirmed run inventory;
   - distinguishes current active orders from reappearing closed orders;
   - subtracts account-wide BUY commitments;
   - checks balance and required-spender allowance independently;
   - preserves the $5 NORMAL reserve without resetting model capital.

8. Existing integration tests include:
   - `test_one_normal_continuation_call_operates_real_fake_sdk_cycles_cumulatively`;
   - `test_rebalance_accepts_later_normal_continuation_handoff`;
   - `test_rebalance_fractional_repair_fill_keeps_confirmed_cost_through_recovery`;
   - existing repricing, cancellation-race, current-account rejection and stale-proof tests.

9. The common `_prepare_repeated_continuation()` fixture currently constructs account quantities and cash from `snapshot_inventory(scope.processor, ...)`. This is useful for operating-path tests, but cannot independently establish agreement between external account evidence and the local ledger.

10. Existing cross-mode tests assert selected inventory/cost and send behavior. They do not constitute the complete independently generated account/model/commitment acceptance required by DG-002.

No new strategy-semantic decision is necessary to resolve this evidence gap.

## Existing Components Reused

Production components remain unchanged:

- `RDPInputSession.initial_normal_admission()` and `submit_initial_normal()`.
- `complete_initial_normal_cycle()`.
- `continue_normal()` and `continue_rebalance()`.
- Existing admission, final SDK guards, retained submission/cycle results and terminal evidence.
- `RDPPlacementOwner`, cumulative registered scope and close-proof union.
- `UserEventProcessor`, confirmed fill ledger, replay deduplication and quarantine checks.
- `snapshot_inventory()`.
- `project_rdp_capital()`.
- `evaluate_rdp_capital_gate()`.
- `evaluate_initial_rdp_backing()` and `evaluate_current_rdp_backing()`.
- `_capture_account_with_gateway()` and `RDPAccountSnapshot`.
- Existing `ExecutionGateway` placement, strict exact account-order reads, official positions path, collateral/spender reads, cancellation and trade/order reconciliation.
- Existing market-data service, dynamic ticks, status checks, Book Filter, input ownership and task draining.
- Existing structured logs and immutable result objects.

Test components to reuse:

- `placement_scope` and `blocked_network`.
- `RepeatedNormalSDKClient` or its compatible local test extension.
- Existing fake trade/order representations, feed event helpers and gateway setup.
- Existing real-executor fake-gateway pattern.
- Existing structured logging capture that records actual dict log records.
- The archived pre-import DNS/socket blocker.

No manually assembled projection or direct owner mutation may replace the actual production operating path in the new primary acceptance scenario.

## Existing Components Modified

**Production components: none.**

Authorized test changes:

- `tests/test_rdp_placement.py`: add the necessary integrated DG-002 scenarios and a small local extension of existing fake-SDK/account fixtures.
- `tests/test_rdp_capital.py`: only if an additional narrow boundary assertion is necessary to explain a result from the integrated scenarios. Such assertions supplement, rather than replace, actual-path acceptance.

Preserve existing test functions, signatures, assertions and previously reviewed positive/negative behavior. Prefer adding focused tests over rewriting existing fixtures globally.

Worker may create new immutable Worker evidence beneath `docs/development/ITER-0030/worker_evidence/`. Orchestrator owns specialist artifact persistence, current-state documents and Gap closure.

`src/`, runtime configuration, dependency files, Frozen SOP, historical artifacts and research data are outside this initial implementation scope.

## New Components Required

No new production component, internal runtime interface, gateway, capital ledger, execution owner, CLI path or external dependency.

A **small test-only independent fake venue/account extension** is required because the current common integration account fixture derives its answer from the production processor.

This extension must:

- retain scripted accepted orders and confirmed venue fills independently of the processor;
- derive fake physical quantities, remaining commitments, collateral and allowance from that independent script/state;
- deliver the corresponding existing-format user/REST evidence to the unchanged production path;
- allow bounded fault injection into account/read evidence;
- record order, read, cancel, reconcile and POST chronology.

The fake account oracle must not call `snapshot_inventory()`, `project_rdp_capital()` or the backing evaluators to generate expected account state or expected acceptance. Production projections may be inspected as **actual outputs**.

Reuse the current fake client and test representations where practical. Do not build a general fake exchange framework.

## Desired Behavior

### Observable start condition

One existing retained selected-market session begins with an empty selected-market run, valid fake official inputs, independently generated sufficient account backing and explicit **test-only** execution authorization.

The existing initial admission/submission establishes actual attributed order ownership. No previously fabricated completed cycle is used as the starting evidence of the main full-path scenario.

### Observable end condition

The unchanged production APIs have actually submitted and reconciled:

1. an initial NORMAL pair;
2. at least one subsequent NORMAL pair;
3. a deficient-side REBALANCE repair, including a partial resting interval;
4. a resumed NORMAL pair after proved recovery.

At each relevant submission and resting/closure boundary, retained actual evidence agrees with the independent confirmed-fill and commitment oracle.

A deterministic legitimate handoff, time-bound end or safety rejection bounds the test. Do not add production attempt limits or manufacture a zero-fill-duration STOP.

Unsafe account/model inputs cause no additional new-risk POST while unsafe. Unreliable state cannot release stale capital certification or adopt foreign inventory/orders.

## Minimal Scope

1. Add a local independent fake venue/account oracle using existing fake-SDK machinery.
2. Exercise the unchanged APIs from initial send through subsequent NORMAL, repair and actual NORMAL resumption.
3. Assert cumulative inventory/cost, active reservations, proof scope, model capital and physical backing throughout.
4. Add focused negative integrated scenarios at subsequent NORMAL and REBALANCE boundaries.
5. Retain exact commands, results, safe chronological evidence and reviewed file hashes.
6. Obtain independent actual-diff review and explicit complete DG-002 acceptance.

The normal API handoff between `continue_normal()` and `continue_rebalance()` is permitted. It is the existing current interface; do not introduce a new orchestrating production function to conceal that boundary.

If existing behavior fails a required criterion, preserve the failing evidence and return **WORKER_BLOCKED** for Architect revision. Do not silently implement production fixes or reduce acceptance scope.

## Invariants

- Frozen BUY-only, MAKER-only and post-only behavior.
- One selected market and one current execution cycle.
- `Delta = QY − QN`; NORMAL strictly `<5`, REBALANCE `>=5`.
- Existing NORMAL pricing/skew and deficient-side five-share REBALANCE pricing.
- Dynamic per-token ticks and minimum-size handling.
- One cumulative confirmed economic authority; only confirmed fills contribute quantities/cost.
- No cash reset at mode transitions or replacement.
- `Capital = FreeCash + ReservedCash + InventoryCost = $30` for the unchanged no-settlement model.
- Resting quantity reserves cash; resting orders do not become acquisition cost.
- NORMAL retains the approved $5 repair reserve.
- Model capital and physical account spendability remain distinct.
- Account-wide BUY commitments are subtracted once from physical backing.
- Closed-order reservations release only after valid scoped reconciliation evidence.
- No foreign inventory adoption or speculative foreign-order cancellation.
- Cancel → Reconcile → current inventory/capital → next action.
- Stale, contradictory, incomplete or unreliable evidence fails closed.
- Existing authorization, safety, drain and terminal behavior remain intact.
- No real network account/order action is allowed by these tests.

## Acceptance Criteria

1. **Complete actual cross-mode path.**  
   A deterministic fake-SDK scenario starts at actual initial submission and traverses subsequent NORMAL → REBALANCE → actual resumed NORMAL submission using the same session, processor and placement owner. Both deficiency signs are covered.

2. **Independent cumulative economics.**  
   The expected QY/QN/CY/CN and total confirmed cost are computed from independently scripted venue fills and prices. They agree with retained production snapshots after every relevant cycle. Fractional fills across modes and replayed confirmations do not reset or double-count quantities/cost. MATCHED or other unconfirmed evidence alone does not increase acquisition cost.

3. **Partial resting reservations and release.**  
   At least one partial repair remains in REBALANCE long enough for the production monitor to consume current account/order evidence. Actual projection reserves only its unfilled remaining quantity, alongside all prior confirmed costs. A reprice or equivalent replacement boundary proves cancel/reconcile precedes the next POST. No overlap, stale reservation or hypothetical cancel credit is used.

4. **Model versus physical capital.**  
   Actual subsequent submission cases separately prove:
   - adequate physical cash/allowance cannot override the unchanged $30 model or NORMAL reserve;
   - model headroom cannot override insufficient physical cash;
   - sufficient cash cannot override insufficient allowance;
   - NORMAL includes the approved $5 reserve;
   - REBALANCE can use that reserve and retains the existing exact-sufficient-cash behavior.
   
   Boundary expectations use independent exact Decimal arithmetic. No new headroom policy is introduced.

5. **Account-wide commitments without adoption.**  
   A foreign-token BUY commitment reduces cash and allowance support once and can prevent a subsequent POST. Matching active owned repair commitments agree with model reservations without duplicate subtraction. Selected-market foreign orders or unexplained selected positions prevent new risk; foreign scope is neither adopted nor cancelled.

6. **Unsafe evidence at both operating boundaries.**  
   Integrated cases exercise stale account receipts, selected-position contradiction, closed-order resurrection, and inconsistent active remaining-size/order evidence at subsequent NORMAL or REBALANCE admission/monitoring. Each produces an appropriate existing factual rejection/safety outcome and no unsafe next POST. Coverage must include both modes; one mode’s helper tests are insufficient.

7. **Late fill and proof validity.**  
   A confirmed fill delivered during cancellation/reconciliation is incorporated before replacement or recovery. Replayed evidence does not inflate cost. Valid new evidence that invalidates an old close certification cannot be masked by its historical PASS or old snapshot; current scope/economics remain truthful and no stale certification grants a new order.

8. **Final guards and output honesty.**  
   A captured subsequent account admission that becomes stale before the actual final SDK guard causes no POST. Terminal or denied results preserve known cumulative inventory and distinguish unsupported capital/completeness. No uncertain account outcome is represented as clean closure or full capital certainty.

9. **Reuse, compatibility and evidence.**  
   Production files/configuration remain unchanged. Existing closed-Gap tests and the affected regression suite pass. The new oracle is test-only and does not reproduce the production strategy/accounting implementation. All valid offline runs report zero attempted network access. Required commands, failures, logs and final tree hashes are retained.

Complete independent PASS on these criteria closes **DG-002 only**. Other OPEN IDs remain OPEN.

## Required Tests

Worker runs in increasing scope:

1. New DG-002 integrated acceptance tests.
2. Existing cumulative NORMAL and cross-mode repair tests, including fractional repair, repricing, cancellation-race and late-proof cases.
3. Affected integration/regression tests:
   - `tests/test_rdp_placement.py`;
   - `tests/test_rdp_capital.py`;
   - `tests/test_rdp_runtime.py`;
   - `tests/test_rdp_lifecycle.py`;
   - `tests/test_rdp_account.py`;
   - `tests/test_execution.py`;
   - accounting and quote tests as relevant.
4. Full existing suite, practical at the currently recorded runtime.

Use existing `.venv/bin/python` and the archived blocker-before-import runner:

`docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py`

Set `PYTHONDONTWRITEBYTECODE=1`, appropriate `PYTHONPATH=src:tests`, and a newly created writable ITER-0030 directory under `/var/tmp` for `TMPDIR` and raw logs. The runner disables pytest cache. Preserve exact command/environment, exit status, observed test total, failure names and blocker footer.

At least one main positive cross-mode scenario must use the real existing gateway executor with a fake SDK. Inline execution may support additional narrow cases but cannot be the sole evidence.

Use synchronization/events for held partial-fill/read/close boundaries. Do not rely on unexplained arbitrary sleeps or fake a successful production predicate.

Reviewer independently executes the new integrated acceptance and appropriate affected regression; validates the full-suite evidence against exact unchanged production and current test hashes; and inspects relevant untracked evidence.

Historical `/tmp` quota-contaminated runs remain preserved. New quota/output failure is environmental evidence, not PASS. Do not delete old scratch, audit or research data to obtain space.

Before Worker completion and review, inspect:

- `git status --short`;
- `git ls-files --others --exclude-standard`;
- `git diff --stat`;
- `git diff --check`;
- staged changes;
- protected-file and production-source state.

Record **NOT_RUN** wherever a required validation was not executed.

## Observability Requirements

No new production logging or metrics collector.

Use existing immutable admissions, intent validations, cycle/close results, owner proofs and structured records to retain a safe test chronology showing:

- market and attempt identity;
- NORMAL/REBALANCE handoffs;
- actual signed/submitted token, quantity, price and tick;
- independent confirmed quantities/cost;
- actual model InventoryCost, ReservedCash and FreeCash;
- physical balance, allowance, account-wide commitments and backing decision;
- partial remaining quantity;
- cancel/read/reconcile/POST ordering;
- factual rejection and terminal reason;
- final known inventory and capital-certification/completeness status.

Keep the evidence reconstructable without credentials or raw exception secrets. This bounded acceptance chronology is not the complete DG-009 required metrics deliverable.

## Live Validation

No live validation, real order creation/modification/cancellation or real account mutation in this iteration.

After MICRO_LIVE_READY and separate human authorization, the corresponding 1–3 BTC5m-market evidence should compare confirmed fills/cost, partial remaining commitments, fresh cash/allowance support and mode-transition capital continuity against this acceptance.

Fake-SDK success establishes implementation behavior, not venue execution, actual fill reliability, profitability or readiness by itself.

## Explicit Non-Goals

- Production-code modification under this initial contract.
- Reimplementing or reopening DG-001, DG-003, DG-004 or DG-006.
- A second inventory ledger, capital controller or execution path.
- A general fake exchange/testing framework.
- Unified CLI, market-end owner or official resolution acquisition.
- Complete reporting/metrics, drawdown or unresolved metric-definition decisions.
- New strategy states, filters, stop conditions, attempt limits or optimization.
- Runtime external-interface changes, RPC or new dependencies.
- Real orders/cancels, MICRO_LIVE activation or main merge.
- Declaring MICRO_LIVE_READY.
- Altering historical reports, research data or prior failure evidence.

## Risks

- An oracle generated from the same production projection creates circular evidence. Independent script/state generation is mandatory.
- Snapshot assertions alone can miss what actually occurred at POST. Record and correlate real fake-SDK submission chronology with retained production guard/admission evidence.
- Existing NORMAL capital rejection may wait safely for new evidence rather than terminate immediately. Tests must verify no unsafe POST and bound the run through legitimate existing events; they must not impose a new STOP policy.
- Inline SDK fixtures can hide executor/async races. At least one complete positive run must retain the real executor.
- Fractional serializer tolerances are existing compatibility behavior. Do not alter them or accept contradictory positions by broadening tolerance.
- A newly discovered required production defect blocks closure and requires Architect revision; it cannot be deferred for convergence.
- Historical full-suite counts and historical PASS hashes cannot be presented as this iteration’s current results.
- `/tmp` quota problems can corrupt evidence output. Use `/var/tmp`, retain errors and confirm complete logs.

## Deferred Findings

The following existing requirements remain under their stable OPEN IDs and receive no implementation in this iteration:

- DG-005: complete STOP and operational-time acceptance.
- DG-007: complete selected-market termination and final scoped evidence.
- DG-008: official resolution and final market accounting.
- DG-009: complete durable metrics and remaining definition checks.
- DG-010: integrated single-market CLI.
- DG-011: final runtime-path and official read-only connection evidence.
- DG-012: final independent readiness and verified archive.

ITER-0029 records an earlier unforced NORMAL failure with unproven historical causality and passing current reviewed regressions. If it recurs, retain attempt/proof/guard diagnostics and treat it as a required regression concern. Do not assign an unsupported cause or dismiss it as merely historical.

No DG-002 acceptance or safety defect discovered during this work may be placed in Deferred Findings.

## Human Approval Required

No additional human approval is required for this verification-first contract within the explicit resumed development authorization.

The Frozen SOP, approved $5 reserve and existing model/backing behavior supply the authority needed for DG-002 acceptance. No new strategy decision is introduced.

If testing exposes a behaviorally meaningful ambiguity in capital, risk or accounting that existing authority cannot resolve, return to Architect and record **HUMAN_DECISION_REQUIRED** with the exact conflicting evidence and smallest necessary decision.

Real-money execution and main merge remain separately unauthorized.
