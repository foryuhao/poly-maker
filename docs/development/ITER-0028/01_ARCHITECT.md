# ITER-0028 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

Stable Gap ID: **DG-003 — Repeated NORMAL Operation in One Market**.

One required delivery behavior: **after a successfully reconciled NORMAL cycle, the retained selected-market runtime automatically admits, submits and completes successive NORMAL cycles using cumulative confirmed inventory, current capital evidence and current dynamic ticks**.

This contract targets **full DG-003 closure**, not another helper-only prerequisite.

DG-002 remains OPEN until operating REBALANCE submissions also consume cumulative inventory, costs, commitments and current account backing. DG-004 remains OPEN; this iteration provides an honest REBALANCE handoff without placing a REBALANCE order.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Verified predecessor HEAD: `a1061aac2d0a3b40b6f5a705977f3314c57f356d`.
- Current initialization HEAD: `a438998688ddea54ebfd24ca9f2ecf45d6b265f3`.
- The initialization commit changes development documents only. Production implementation remains the independently reviewed ITER-0027 tree.
- Working tree: no tracked or staged changes; existing untracked `research_outputs/` remains preserved.
- ITER-0027: CODE_READY, archived at implementation commit `7df8722cfec7f2a4b16468124ff2ae6b8382b8e2`, with independent Reviewer04 PASS, RESULT and receipt05.
- Current archived regression evidence: 1683 passed, 2 skipped; independent additional integrated probes: 22 passed. These are predecessor evidence, not validation of future changes.
- DG-001 and DG-006: CLOSED.
- DG-002–DG-005 and DG-007–DG-012: OPEN.
- MICRO_LIVE_READY: NO.
- Real-money and main merge authorization: NOT_GRANTED.

Inspected authorities and relevant durable evidence:

- AGENTS.md, Frozen SOP, BASELINE.md, ITERATION_INDEX.md and DELIVERY_GAPS.md.
- ITER-0027 Architect01, Worker03, Reviewer04, RESULT and archive receipt05.
- ITER-0028/00_INITIALIZATION.md.
- Approved $5 reserve and Book Filter decisions referenced by the delivery register.

Inspected actual runtime, placement owner, capital projection/backing, gateway, lifecycle, market-data tick handling, quote/accounting implementations and relevant tests.

## Problem

The runtime now produces a factual post-cycle candidate, but it cannot operate the next NORMAL cycle.

The existing execution composition remains tied to initial empty inventory and one acknowledged pair. Repeated NORMAL operation therefore still requires manual code intervention or unsafe resetting of initial state.

The observable missing behavior is:

> A reconciled cycle leaves `abs(Delta)<5`, but the same runtime does not automatically quote again using the accumulated confirmed inventory and remaining capital.

This cannot be solved by simply calling initial submission again: initial capture requires empty scope and exact five-share quotes, submission is once-only, cycle/window completion is cached for one attempt, and capital closure currently consumes one scoped proof.

The minimum defensible delivery change is a retained NORMAL continuation that extends those existing components compatibly and consumes ITER-0027 admission through actual guarded pair placement and lifecycle.

## Evidence / Current Behavior

1. `post_cycle_next_action_admission()` builds current NORMAL or REBALANCE candidates and validates physical backing, but:
   - derives run IDs from the current pair only;
   - requires `_initial_cycle_result`;
   - grants no execution authority.

2. `RDPPlacementOwner.arm()`:
   - requires `_initial_scope_empty()`;
   - `_valid_initial_pair()` requires exactly five shares per leg.

   This cannot capture Frozen inventory-skewed subsequent quotes.

3. `submit_initial_normal()` rejects reuse through `_initial_submission_invoked`. Its guard, executor-drain and typed outcome handling are valuable existing safety components.

4. `complete_initial_normal_cycle()` and FillWindow completion retain one-shot results. A later attempt cannot consume the original attempt’s cached result.

5. `project_rdp_capital()` supports confirmed costs and resting reservations across explicit IDs, but its close-evidence argument represents one scoped barrier. Successive cycles require all previously closed scope to remain supported without inventing an aggregate reconciliation result.

6. Placement `record_close()` currently compares current-pair capital and inventory. Subsequent release must additionally preserve cumulative run evidence while validating the current cycle’s own closure.

7. Existing `RDPMarketDataService` validates official selected-market tick events and records `WS_TICK_SIZE_CHANGE`/`WS_BOOK_SNAPSHOT` provenance. Bootstrap ticks are initial metadata. The post-cycle admission currently rejects a current WS tick that differs from bootstrap metadata, so it cannot satisfy DG-003’s changed-tick acceptance through actual feed events.

8. Existing gateway strict pair placement already accepts inventory-skewed quantities and per-token signing ticks. No gateway replacement or new placement interface is necessary.

9. Existing pre-fill, FillWindow, scoped close and terminal-failure code already handles resting quotes, first-confirmation timing, cancel/fill races and uncertainty. Their implementation must be reused.

## Existing Components Reused

- Retained `RDPInputSession` and existing public/authenticated input owners.
- Initial submission, owner-loop SDK guard bridge, absolute operational deadline checks, typed gateway evidence and executor draining.
- Existing `ExecutionGateway.place()` with forced post-only, strict opted-in pair acknowledgments and token signing ticks.
- `RDPPlacementOwner`, pre-ack processor capture and registry validation.
- `UserEventProcessor` and `StateStore` as authoritative owned-order/fill state.
- Existing NORMAL quote builder, Book Filter and inventory accounting.
- ITER-0027 current factual admission and account backing validation.
- Existing initial pre-fill, FillWindow, cycle-close and terminal-failure implementation.
- `close_rdp_cycle()` and filltruth reconciliation.
- Existing capital projection and model CapitalGate.
- Official market-status/account reads.
- Validated retained market-data tick evidence.
- Existing fake SDK/gateway/account/market fixtures and pre-import network-blocking test runner.

## Existing Components Modified

1. **`src/polymaker/rdp_runtime.py`**
   - Add retained NORMAL continuation from a successfully completed initial cycle.
   - Reuse the existing submission, pre-fill, FillWindow and closure implementation for subsequent attempts.
   - Preserve attempt-specific provenance and results.
   - Make post-cycle admission and final subsequent-send validation consume cumulative run scope and complete closure evidence.
   - Preserve cumulative inventory in NORMAL continuation, handoff and terminal results.
   - Extend serialization and teardown ownership to the continuation.

2. **`src/polymaker/execution/rdp_placement.py`**
   - Extend the existing owner to capture a subsequent NORMAL pair only after attributable prior closure.
   - Validate Frozen inventory skew instead of treating arbitrary larger quantities as admissible.
   - Retain acknowledged run scope and validate current-cycle release against cumulative capital evidence.
   - Keep initial empty five/five capture behavior compatible.

3. **`src/polymaker/risk/rdp_capital.py`**
   - Make the existing projector consume multiple retained scoped close proofs through a compatible local extension.
   - Preserve all existing proof validation.
   - Continue using existing model gate and current backing.

4. Relevant tests:
   - `tests/test_rdp_placement.py`.
   - `tests/test_rdp_capital.py`.
   - `tests/test_rdp_runtime.py`.
   - `tests/test_rdp_marketdata.py` only if necessary to verify actual retained tick evidence used by the runtime.

No gateway, lifecycle, shared tracker/store, quote-builder, CLI, configuration or dependency changes are authorized by this contract.

## New Components Required

No new subsystem, execution gateway, inventory/capital ledger, parallel trading path or external runtime interface.

Necessary local additions:

- One retained NORMAL-continuation operation and immutable result/history within the existing runtime.
- Attempt-specific references to acknowledged IDs, issued admission, submission result and scoped close proof.
- A compatible projector input for the collection of retained scoped close proofs.

These are necessary now because actual successive orders must preserve prior economic evidence while separately proving closure of each current cycle. They record evidence owned by existing components; they must not become another trading state machine.

Factor common private operations only where needed to reuse existing implementation. Do not copy the initial submission or lifecycle pipeline into a separate runner.

## Desired Behavior

### Observable start condition

The selected-market session retains a completed, authoritative initial NORMAL cycle. Its current placement owner, registry, close proof and cumulative confirmed inventory agree. No operation is in flight and STOP is not latched.

One explicit NORMAL-continuation call is made. Authorization defaults to denied; tests deliberately authorize only fake execution.

### Observable end condition

That single call automatically operates successive NORMAL cycles while reliable current evidence permits NORMAL action.

It eventually returns retained evidence for:

- a reconciled REBALANCE handoff when cumulative `abs(Delta)>=5`; or
- an honest terminal result on safety failure, insufficient remaining operational time, expiry or interruption.

During its lifetime, no caller manually arms/resolves orders, selects mode, supplies inventory, closes each cycle or invokes the next-cycle submission.

The continuation selects no other market and places no REBALANCE order.

### Cumulative scope and capital

Run scope is derived only from attributable acknowledgments registered through the existing owner. It includes all prior and current acknowledged orders.

Preserve all prior confirmed quantities and actual acquisition costs. Never reset the processor, remove owned history or recreate an empty $30 projection.

Retain each authentic scoped close result. Revalidate those proofs against current processor/store evidence before allowing new risk. An absent current account order is not a close proof.

The capital projector must support the union of proven closed scope and current resting scope, without:

- summing capital allocations;
- double-counting fills or reservations;
- manufacturing a merged `RDPCloseResult`;
- treating a prior close as proof of later orders;
- accepting contradictory overlapping proofs.

The current cycle’s barrier may describe its own inventory scope. Next mode and run capital use cumulative inventory. That distinction must be explicit in retained evidence and release checks.

### Subsequent admission and final send

After complete closure:

1. Obtain fresh current admission through the existing official reads and retained feeds.
2. Derive NORMAL or REBALANCE from cumulative confirmed Delta.
3. If NORMAL, build current Frozen skewed pair quotes and evaluate existing Price Gate, Book Filter, model CapitalGate and physical backing.
4. Capture pending ownership before placement.
5. Submit through the existing strict pair gateway path with both current SDK guards.
6. Preserve typed outcome evidence, register only attributable acknowledgments and retain unresolved evidence.
7. Operate the acknowledged pair through existing resting/FillWindow/cancel/reconcile behavior.
8. Update cumulative evidence and repeat only after authoritative closure.

A cached factual admission is never sufficient final-send authority.

Both guard points must revalidate the issued intent against current:

- exact market and attempt provenance;
- cumulative prior inventory/cost and closure evidence;
- clean current pre-ack capture;
- account/status freshness and backing;
- books, dynamic ticks, metadata legality and filter;
- authorization, STOP/interruption and operational deadline.

Guard failure must withhold POST. Unreliable or ambiguous exposure follows existing terminal failure handling rather than automatic retry.

### Dynamic ticks

Use current tick values backed by the existing validated selected-market feed and current generation.

A valid current WS tick change can supersede the bootstrap tick for subsequent quotes. Do not mutate bootstrap facts merely to hide disagreement.

Use the same captured per-token tick evidence for candidate calculation, signing options and final validation. A further change before POST invalidates that intent.

Bootstrap minimum-size and market identity/neg-risk validation remain mandatory. Malformed or unproven tick evidence rejects; no hard-coded tick or unexplained metadata mismatch waiver is allowed.

This requires no new market-data endpoint or service.

### Cycle lifetime and safety

- Only one cycle is current.
- Prior confirmed fills cannot start the next cycle’s FillWindow.
- The first accepted confirmation for the current attempt uses its original retained receipt and configured window.
- Ordinary movement during a started FillWindow does not reprice.
- A valid zero-fill quote continues resting.
- Healthy pre-fill invalidation closes and reconciles before requoting.
- No quote quota, cycle quota, zero-fill timeout or rebalance-duration stop is introduced.

Entry/filter/price rejection without unreliable exposure does not invent a terminal strategy condition. With no orders outstanding, the continuation may wait and re-evaluate using existing bounded polling/wakeup behavior.

A NORMAL CapitalGate rejection with reliable evidence means no admissible new NORMAL risk; it must not silently reset capital, waive the reserve or relabel balanced inventory as REBALANCE. No strategy stop rule is added merely because an entry gate rejected.

Reuse the existing positive order/close operational budgets and strict remaining-market-time check. Insufficient Order → Cancel → Reconcile time prevents new risk and ends the continuation safely with TIME_EXHAUSTED. No new numeric strategy cutoff is required.

On cumulative REBALANCE classification, close the current NORMAL cycle completely and return a REBALANCE handoff with current inventory/capital evidence. This is not STOP or an invented trading mode.

### Compatibility

Keep initial public operations once-only and preserve their retained initial results. Subsequent attempts must not make an old initial call resend or return another attempt’s cached completion.

An internal common implementation may serve both initial and subsequent attempts, but public initial safety semantics remain unchanged.

Default-denied authorization and explicit operation ownership remain mandatory.

## Minimal Scope

One integrated NORMAL continuation, with only the owner, cumulative proof and guard/lifecycle extensions necessary to make repeated NORMAL delivery work.

No separate prerequisite iteration for merely collecting IDs or constructing another candidate.

A concrete implementation may choose private helper names and small immutable records. It may not replace working gateway/lifecycle machinery, erase original results or broadly reorganize unrelated runtime code.

If the existing implementation cannot meet this scope without modifying an excluded component or changing strategy meaning, return WORKER_BLOCKED with the exact required change.

## Invariants

- Frozen SOP and approved human decisions remain unchanged.
- Only official Polymarket HTTP/WebSocket runtime interfaces.
- One selected market and one current cycle.
- Confirmed fills alone establish inventory and acquisition cost.
- `Delta = QY - QN`; mode uses cumulative inventory.
- The model allocation remains exactly $30.
- Prior fills are never forgotten between attempts.
- Resting reservations are separate from confirmed InventoryCost.
- NORMAL preserves the approved $5 repair reserve in model and current backing.
- Foreign account state cannot augment run inventory or capital.
- Complete attributable reconciliation precedes replacement.
- SDK guards, post-only, strict acknowledgments and fail-closed uncertainty remain active.
- STOP prevents all later new risk.
- Initial public operations retain their reviewed once-only behavior.
- No autonomous real-money execution, main merge or market rotation.

## Acceptance Criteria

1. **Integrated repeated delivery**
   - Starting from an actual completed initial fake cycle, one continuation call automatically submits and completes at least two additional NORMAL cycles.
   - No test manually arms, resolves, closes or starts those subsequent cycles.
   - Successive requests use the existing gateway’s actual fake SDK path.

2. **Fractional skew and cumulative accounting**
   - A fractional residual after the first cycle produces the correct skewed next pair.
   - A third cycle demonstrates retention of both earlier cycles’ quantities and actual confirmed costs.
   - Every accepted subsequent send records the cumulative projection, model gate and current backing that supported it.

3. **Multiple closure proofs**
   - Prior completed proofs and a later current resting pair project correctly.
   - Partial current fills count confirmed cost plus remaining posted reservations exactly once.
   - Incomplete, stale, forged/mismatched, contradictory or insufficient proof collections cannot certify capital or release replacement.

4. **Actual changed dynamic tick**
   - Feed an official-format selected-market tick-size-change message through existing market-data processing.
   - A later NORMAL candidate/signing request uses that tick without manually altering bootstrap metadata.
   - A tick change between admission/signing/POST denies the stale intent.
   - Invalid metadata/minimum-size scenarios remain fail-closed.

5. **Ordering**
   - Second and third POSTs occur only after the corresponding prior cancellation/reconciliation completes.
   - Held reconciliation, late confirmed fills and cancel/fill races do not permit early replacement.
   - Existing close behavior never cancels foreign or previously closed orders as new current scope.

6. **Per-attempt resting/window behavior**
   - Valid zero-fill orders rest beyond the configured FillWindow duration without that duration becoming a zero-fill timeout.
   - Healthy invalidation closes before a fresh requote.
   - Old cycle confirmation receipts do not start the next window.
   - New confirmations retain the current attempt’s original receipt deadline.
   - Ordinary movement during a started window causes no premature repricing.

7. **Current gate enforcement**
   - Stale/contradictory account, order or fill evidence, changed generations, backlog and stale books deny new risk.
   - Low actual cash or allowance denies despite sufficient model capital.
   - Higher observed cash never replenishes the $30 model.
   - NORMAL reserve rejection never bypasses the reserve.

8. **Strict outcome/STOP safety**
   - A subsequent partial, unknown, error, unresolved owner or interrupted complete response latches terminal protection.
   - There is no retry of an ambiguous POST.
   - Cleanup is limited to registered attributable current scope.
   - Prior confirmed inventory remains included in final known run evidence.
   - Unprovable exposure and incomplete cleanup remain explicit; no false capital release.

9. **REBALANCE handoff**
   - Both exact `Delta=+5` and `Delta=-5` cumulative boundaries end NORMAL continuation after current closure.
   - Zero dual NORMAL POSTs occur after that boundary.
   - Result identifies REBALANCE handoff and full cumulative inventory.
   - No REBALANCE POST or invented STOP is performed.

10. **Time, concurrency and drain**
    - Default or non-boolean authorization produces zero signing/POST/cancel.
    - Concurrent continuation or conflicting manual operations cannot create two current cycles.
    - Existing order/close budget boundary prevents late new risk.
    - Cancellation, teardown and expiry drain in-flight gateway/read work and preserve honest scoped cleanup results.
    - No SDK operation is detached and no new-risk POST occurs after STOP.

11. **Compatibility**
    - Initial once-only submission, original result caching, DG-001 and DG-006 regression cases still pass.
    - FL/IM/shared infrastructure remains unchanged.
    - Source/test/configuration scope and relevant untracked files are independently reviewed.

Reviewer PASS must explicitly confirm **full DG-003 delivery closure** through integrated repeated-cycle evidence. DG-002 remains OPEN for required REBALANCE successive-order coverage, and DG-004 remains OPEN for its operating loop.

## Required Tests

Use the existing archived pre-import socket/DNS-blocking runner. Do not connect to live create/cancel endpoints.

Worker runs increasing scope and preserves exact commands/results.

1. Focused new continuation and multi-proof acceptance tests, then:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_capital.py
   ```

2. Runtime/placement integration:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_rdp_placement.py tests/test_rdp_runtime.py
   ```

3. Affected gateway, lifecycle, account, feed, quote and accounting regressions:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_execution.py tests/test_rdp_lifecycle.py tests/test_rdp_account.py tests/test_rdp_marketdata.py tests/test_rdp_userstream.py tests/test_rdp_quotes.py tests/test_rdp_accounting.py tests/test_rdp_book_filter.py
   ```

4. FL compatibility:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q tests/test_favorite_live.py
   ```

5. Full suite:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python docs/development/ITER-0026/review_evidence/iter0026_review_pytest.py -q
   ```

Meaningful independent review must probe cumulative scope after more than one close, subsequent SDK guards, changed ticks through actual service processing, uncertainty and cancellation races.

Before Worker completion:

```bash
git status --short
git ls-files --others --exclude-standard
git diff --stat
git diff --check
```

Report zero attempted network connections. Preserve existing research outputs and immutable evidence.

## Observability Requirements

Retain attempt-linked evidence for:

- initial and subsequent admissions/submissions;
- current versus cumulative run/closed/resting scope;
- confirmed QY/QN/Delta, acquisition costs and capital projection;
- new candidate, current signing ticks and tick provenance;
- account/status receipt evidence and physical backing;
- pre/post-sign guard outcomes and typed POST evidence;
- original FillWindow receipts/deadlines;
- current scoped close proofs;
- cumulative next-mode selection;
- REBALANCE handoff or terminal reason;
- honest unresolved exposure and cleanup completeness.

Earlier evidence must survive later attempts. A final result cannot overwrite the only copy of an earlier acknowledged submission or reconciliation outcome.

Use existing sanitized structured logging. No credentials or raw SDK errors.

This supplies execution evidence needed by later metrics work; it does not implement DG-009’s full metrics collector.

## Live Validation

None during development.

All new placement/cancellation testing uses fakes, with network blocked before imports.

After final readiness and explicit human authorization, supervised validation can compare consecutive NORMAL quotes against retained confirmed inventory, verify fractional size skew and dynamic ticks, and inspect cancellation/reconciliation before each replacement.

Development evidence does not establish live execution correctness or strategy edge.

## Explicit Non-Goals

- Operating REBALANCE, deficient-side placement or repricing.
- Full project readiness and all DG-002 closure.
- Final complete market-owner termination across future REBALANCE phases.
- Official resolution/final realized accounting.
- Full metrics definitions or collection.
- CLI activation or live validation.
- Automatic market rotation or a 288-market scheduler.
- Gateway/lifecycle rewrites, new execution subsystem or generic strategy framework.
- Optional refactoring and unrelated FL/IM changes.
- Quote/cycle quotas, zero-fill stops, hard rebalance-duration limits or strategy revisions.
- Real orders, real cancellations and main merge.

## Risks

- Cumulative run evidence and current-cycle evidence have different scopes. Comparing only one or relabeling a scoped barrier as cumulative can release capital incorrectly.
- Resetting initial fields for reuse can corrupt original audit provenance or resend an initial operation.
- Reusing prior confirmation receipts can start a subsequent FillWindow immediately.
- Final SDK guard checks must distinguish permitted current ARMED capture from unchanged proven prior inventory.
- Sequential account observations can lag confirmed fills. Disagreement remains ineligible; do not adopt or heuristically repair it.
- Bootstrap ticks must not override validated current WS ticks, but real identity/generation inconsistencies must still reject.
- Uncertainty after a later POST must preserve all earlier confirmed inventory while retaining current unresolved exposure.
- The continuation must not turn ordinary gate rejection into an unauthorized strategy stop condition.

Any defect blocking this delivery or its safety remains a required fix or Architect return; it cannot be deferred merely for convergence.

## Deferred Findings

- DG-002: required REBALANCE sends still need cumulative current-capital consumption and independent acceptance.
- DG-004: actual deficient-side operating loop and recovery remain unimplemented.
- DG-005/DG-007: final integrated policy/termination across all operating phases remains required.
- DG-008–DG-012: resolution, complete metrics, CLI, official read-only validation and final readiness/archive remain required.
- DG-009’s existing definition questions remain for its authority check. This contract makes no new metric definitions.

No additional Gap IDs or speculative roadmap items are introduced.

The index’s “Latest archived implementation HEAD” and delivery register’s introductory “Latest reviewed implementation” still mention ITER-0026, while their current iteration paragraphs and ITER-0027 artifacts record the newer reviewed tree. This is document-pointer drift; Orchestrator should correct those current pointers during normal index/register maintenance. Actual code, Git and the newer immutable review govern this contract.

## Human Approval Required

None for this contract.

Repeated NORMAL operation, inventory skew, current capital gating, dynamic ticks and lifecycle ordering are already specified by the Frozen SOP. Operational budgets reuse existing explicitly selected positive engineering budgets and remaining-time checks; this contract invents no numeric strategy cutoff.

The user’s automatic continuation authorization covers implementation, ordinary repair/re-review and archival.

A newly discovered strategy ambiguity, excluded-component necessity or material architecture change must return to Architect. Genuine missing strategy authority requires the smallest human decision.

Real-money trading, main merge and new external runtime interfaces remain unauthorized.
