# ITER-0027 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

Stable Gap ID: **DG-002 — Current Capital and Inventory Across Successive Orders**.

One delivery behavior: **the retained selected-market runtime produces a fresh, factual next-action admission from its own reconciled nonempty inventory, current official account evidence and current books, preserving the original $30 capital allocation**.

This is a necessary partial prerequisite. **DG-002 remains OPEN** after this iteration. Its complete acceptance condition still requires successive NORMAL and REBALANCE submissions to consume current cumulative run evidence before each send. This contract does not authorize those submissions or claim operating-loop completion.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- Inspected implementation/archive HEAD: `e4ab1532043f47af9e416e9e8ef632440bb8608a`.
- Current initialization HEAD: `396a96973f203c5986aa6e9978a1b4d595dd1a0d`.
- The initialization commit changes only the delivery register, iteration index and `ITER-0027/00_INITIALIZATION.md`; source and tests remain the archived ITER-0026 implementation.
- Tracked working tree is clean. Existing untracked `research_outputs/` is preserved outside this iteration.
- ITER-0026 is CODE_READY and archived. Its final independent report is `12_REVIEWER_REREVIEW_03.md`; its RESULT and `13_ARCHIVE_RECEIPT.md` exist.
- DG-001 and DG-006 are CLOSED. Their implementation must not be repeated.
- MICRO_LIVE_READY: NO.
- Real-money trading authorization: NOT_GRANTED.
- Main merge authorization: NOT_GRANTED.

Authorities inspected:

- `AGENTS.md`.
- `docs/strategy/RDP_PHASE0_FROZEN.md`.
- `docs/development/BASELINE.md`.
- `docs/development/ITERATION_INDEX.md`.
- `docs/development/DELIVERY_GAPS.md`.
- `docs/development/ITER-0026/RESULT.md`.
- `docs/development/ITER-0026/13_ARCHIVE_RECEIPT.md`.
- `docs/development/ITER-0027/00_INITIALIZATION.md`.
- Approved human decisions in ITER-0005 and ITER-0006.

Relevant existing source and tests were inspected directly. Historical test reports are context, not proof that a subsequently changed tree passes.

## Problem

After a reconciled initial NORMAL cycle, the runtime can classify the resulting inventory as NORMAL or REBALANCE but cannot produce a fresh next-action admission using that inventory and actual current spendability.

The initial admission and final initial-intent validation are intentionally empty-run operations. Reusing them for a later action would either reject retained inventory or incorrectly recreate a $30 empty-run capital projection.

The observable problem is therefore:

> A completed cycle leaves confirmed inventory and acquisition costs in the existing processor, but the runtime has no integrated factual admission for the next candidate that preserves those costs and verifies current account support.

This prerequisite is necessary now because repeated NORMAL operation and operating REBALANCE must not be implemented against an empty-run admission.

Completing all DG-002 acceptance in this iteration would also require adapting one-shot placement capture, submission ownership, final SDK guards and cycle coordination for actual successive orders, including deficient-side execution. That would bundle the operating behaviors registered under DG-003 and DG-004. This iteration establishes their shared factual admission boundary without introducing another send path.

## Evidence / Current Behavior

1. `RDPInputSession.initial_normal_admission()`:
   - checks `_initial_processor_is_empty`;
   - evaluates `evaluate_initial_rdp_backing`;
   - calls `project_rdp_capital` with an empty order set and empty open orders;
   - builds a NORMAL candidate from empty inventory.

2. `_capture_initial_normal_intent()` similarly certifies an empty initial scope. Its retained admission and ARMED-attempt provenance protect initial submission and must remain intact.

3. `evaluate_initial_rdp_backing()` correctly requires an empty selected-market account state and $30 initial cash/allowance backing. It is not a current-run backing evaluator.

4. `project_rdp_capital()` already:
   - obtains confirmed inventory from the existing processor;
   - preserves actual confirmed acquisition costs;
   - computes remaining resting reservations;
   - validates owned identity and fill/order consistency;
   - checks close evidence before treating absent orders as closed;
   - computes `FreeCash = 30 - InventoryCost - ReservedCash`.

5. `evaluate_rdp_capital_gate()` already enforces:
   - capital mode versus inventory;
   - new notional within model FreeCash;
   - the $30 allocation;
   - the approved $5 future repair reserve in NORMAL.

6. `_close_initial_normal_placement()` already produces the attributable close barrier and capital projection. `complete_initial_normal_cycle()` retains the resulting completion on the session. Confirmed fills remain in the processor after close.

7. Existing tests exercise fractional inventory, both exact-five boundaries, confirmed fills during cancellation, complete close proofs and unreliable closure.

8. Existing `build_normal_quotes()` and `build_rebalance_quote()` already implement the required formulas. Neither needs replacement.

9. No existing RDP next-action admission was found. The current once-only submission and empty initial capture are deliberate limitations, not authorization to reset inventory or weaken their gates.

## Existing Components Reused

- `RDPInputSession`, its retained public/user owners and its completed-cycle result.
- `RDPPlacementOwner.current` and existing exact registry validation.
- `UserEventProcessor`, its owned records, confirmed economic fills, deduplication and quarantine checks.
- Existing `RDPInitialCycleCompletion` and `RDPInitialPlacementClose` evidence.
- `project_rdp_capital()` and its close-proof, order-checksum and confirmed-cost validation.
- `evaluate_rdp_capital_gate()`.
- `build_normal_quotes()` and `build_rebalance_quote()`.
- The retained `RDPMarketDataService`, current generation, books, dynamic ticks and Book Filter.
- `read_rdp_market_status()` and current exact-market status/freshness validation.
- `_capture_account_with_gateway()` using the already connected authenticated gateway.
- `RDPAccountSnapshot`, exact Decimal collateral/order evidence and existing account identity.
- Existing task serialization, cancellation/drain and session teardown patterns.
- Existing fake gateway, account, market-status and completed-cycle integration fixtures.

The execution gateway, lifecycle barrier, filltruth reconciliation and initial live-send controls remain the existing implementations.

## Existing Components Modified

1. **`src/polymaker/rdp_runtime.py`**
   - Add one factual next-action admission operation to the existing retained session.
   - Derive input scope from the session’s own completed cycle and current placement owner.
   - Refresh official status/account evidence.
   - Reproject current nonempty inventory/capital and build the appropriate current candidate.
   - Retain an immutable admission or rejection result.
   - Serialize and drain its read work within existing session ownership.

2. **`src/polymaker/risk/rdp_capital.py`**
   - Add current-run account backing validation alongside existing initial backing and model CapitalGate.
   - Reuse existing Decimal, timestamp, account-order and spender validation wherever compatible.
   - If common validation requires extraction, keep it local and minimal; preserve all existing initial-backing acceptance and rejection behavior.
   - Do not rewrite the projector or weaken its close-proof requirements.

3. Relevant tests:
   - `tests/test_rdp_capital.py`.
   - `tests/test_rdp_placement.py`.
   - `tests/test_rdp_runtime.py` only where necessary for retained-session concurrency or teardown coverage.

## New Components Required

No new subsystem, gateway, execution path, account reader, inventory ledger or external runtime interface.

A small immutable factual next-action result type inside `rdp_runtime.py` is required. The initial admission type explicitly describes empty-run backing and initial placement provenance; reinterpreting it as post-cycle authority would obscure those constraints.

A local current-run backing result/evaluator in `rdp_capital.py` is necessary because initial backing rejects all selected inventory. It must consume the existing account and projection rather than maintain separate capital state.

Do not introduce cumulative history interfaces or multiple-close-proof abstractions in anticipation of future loops. This iteration uses the authoritative completed initial cycle already retained by the runtime.

## Desired Behavior

### Observable start condition

The existing selected-market input session has completed its acknowledged initial NORMAL cycle through existing cancellation/reconciliation. Its retained completion, current placement owner and exact owned registry agree. Confirmed inventory may be zero, fractional or at either REBALANCE boundary.

### Observable end condition

One explicit next-action admission call returns and retains an immutable, current factual decision containing:

- market and completed-attempt identity;
- authoritative run order scope;
- NORMAL or REBALANCE classification from confirmed inventory;
- freshly captured account/status receipt evidence;
- current confirmed inventory and capital projection;
- current candidate quotes and dynamic token metadata;
- applicable Book Filter evidence;
- model CapitalGate result;
- account cash/allowance backing result;
- eligibility or a precise rejection reason;
- remaining execution gates;
- `execution_authorized = false`.

The operation does not submit, sign, arm pending capture, cancel, reconcile again or release ownership.

### Scope and provenance

The operation takes no caller-supplied replacement inventory, arbitrary order set, claimed close proof or requested trading mode.

It derives scope from the retained successful completed-cycle close and matching current owner. It must reject:

- absent or foreign completion;
- mismatched attempt/market/registry;
- unresolved or replacement-blocked ownership;
- incomplete or stale close proof;
- active submission, cycle, close, pre-fill or other conflicting admission work;
- unrelated local owned scope or pending capture that prevents establishing the same run.

The existing processor remains authoritative. Account positions cannot add inventory or replace confirmed acquisition costs.

### Current factual capture

Refresh market status and account evidence through existing official reads. After the awaits, synchronously revalidate current scope, close proof, processor evidence, session health, generations, time, book freshness/backlog and account/status freshness before issuing an eligible result.

New processor evidence observed during reads must be included in the final projection or cause rejection. A previously retained close proof whose inventory no longer matches current confirmed evidence must fail closed.

### Current capital and physical support

Keep the model allocation at exactly $30. Reuse the existing projector to preserve confirmed acquisition costs and release only proven closed reservations.

Separately compute actual account availability:

- subtract outstanding account-wide BUY commitments from observed collateral balance;
- subtract those commitments from observed allowance;
- retain the existing distinction between owned selected scope and foreign commitments;
- never add foreign orders, positions or costs to the RDP inventory model.

For an eligible candidate:

- existing model CapitalGate must pass;
- actual cash and allowance after commitments must cover its new notional;
- NORMAL must retain the already approved $5 repair capacity in both the model and current cash/allowance backing;
- REBALANCE may use that reserve.

Do not subtract prior confirmed acquisition cost again from observed account balance: the official balance is current account evidence, while the $30 model independently retains confirmed cost.

Selected-market positions must support the same confirmed quantities; malformed, stale or contradictory position/order evidence rejects admission. Use existing numerical reconciliation tolerances where float-based processor quantities require them, document the tolerance, and do not use position average price as acquisition-cost authority.

### Candidate construction

For `abs(Delta) < 5`:

- use existing NORMAL quote construction;
- apply current inventory size skew;
- require current Price Gate and Book Filter;
- use current per-token ticks and legal minimum sizes.

For `abs(Delta) >= 5`:

- use existing REBALANCE quote construction;
- select only the deficient side;
- use five shares at current BestAsk minus current tick;
- require post-only legality and applicable current capital/state/market evidence;
- do not apply NORMAL’s Book Filter or $5 protected reserve as a new REBALANCE restriction.

The factual result must still list operational time sufficiency, final submission revalidation and deliberate execution authorization as outstanding. It must not claim full `CanSafelyContinue` or ready-to-send authority.

## Minimal Scope

Only:

1. Current-run account backing validation.
2. One integrated post-cycle factual admission on the retained session.
3. Its immutable evidence and structured logging.
4. Necessary tests and specialist reports.

Prefer private/local helpers and compatible additions to the existing runtime.

Do not change initial admission/placement semantics to make this new operation available. Do not reset existing once-only flags or initial cycle caches.

Do not change `gateway.py`, `rdp_placement.py`, `rdp_lifecycle.py`, shared tracker/store, quote builders, CLI, configuration or production dependencies under this contract. If implementation cannot meet acceptance without such a change, return WORKER_BLOCKED with the concrete necessity.

## Invariants

- Frozen SOP remains unchanged.
- Official Polymarket HTTP/WebSocket are the only runtime interfaces.
- Confirmed fills alone establish inventory and acquisition cost.
- No account-position adoption and no independent inventory state machine.
- `Delta = QY - QN`.
- `$30 = FreeCash + ReservedCash + InventoryCost` within the existing no-settlement projection.
- Account balance does not reset or replenish the strategy allocation.
- NORMAL preserves the approved $5 repair reserve.
- REBALANCE pricing, clip and deficiency semantics remain Frozen.
- Existing stale-data, order-state, fill-state and reconciliation checks remain fail-closed.
- Existing terminal STOP latch is respected.
- A next-action factual admission grants no execution authority.
- DG-001 and DG-006 behavior remains compatible.
- FL/IM behavior and shared data remain unchanged.
- No real-money orders or cancellations occur.

## Acceptance Criteria

1. **Actual integrated starting state**
   - Tests complete an acknowledged pair through the existing session cycle owner and close barrier.
   - The new operation consumes the session’s retained result, not a fabricated caller close argument.

2. **Nonempty NORMAL admission**
   - A completed cycle with fractional residual inventory produces the correct inventory-skewed next NORMAL candidate.
   - Prior QY/QN, CY/CN and confirmed acquisition cost remain present.
   - Model FreeCash is $30 minus retained cost and attributable current reservations.
   - Fresh account evidence can support this nonempty state without invoking empty-start rejection.

3. **REBALANCE boundary admission**
   - Both `Delta = +5` and `Delta = -5` select the correct deficient-side five-share maker candidate.
   - Current ticks, asks and minimum-order constraints govern legality.
   - This remains candidate admission, not an operating REBALANCE loop.

4. **Current physical backing**
   - Cash, allowance and foreign BUY commitments are reflected exactly once.
   - Increasing observed balance cannot reset model capital.
   - Low current cash or allowance rejects despite sufficient model FreeCash.
   - NORMAL $5 protection and REBALANCE reserve availability are tested independently.

5. **Scope and close safety**
   - No eligible result before complete authoritative closure.
   - Unresolved/partial/unknown placement, STOP latch, mutated registry, resurrected order, stale close inventory, unrelated owned scope and pending/FAILED/quarantined fills reject.
   - Closed orders reported as open in fresh official evidence reject; absence alone never replaces close proof.

6. **Freshness and coherent capture**
   - Stale/future/malformed account timestamps, market expiry, stale/contradictory status, changed generations, stale books, backlog and selected-position/order contradictions reject.
   - Injected changes during status/account awaits cannot escape final validation.
   - Dynamic tick changes are used coherently or reject incompatible metadata; cached initial ticks cannot silently determine the next candidate.

7. **Serialization and drain**
   - Concurrent conflicting operations cannot produce overlapping eligible admissions.
   - Caller cancellation and session teardown drain owned read work.
   - No read operation remains detached or publishes eligibility after teardown/STOP.

8. **No new trading authority**
   - Fake signing, placement and cancellation counters remain zero during this new operation.
   - It never arms processor pending capture or changes placement-owner release state.
   - Eligible and rejected results both retain `execution_authorized=false`.
   - Outstanding send/time gates remain explicit.

9. **Compatibility and evidence**
   - Required targeted, affected, FL and full regression checks pass.
   - Complete diff and relevant untracked files are independently reviewed.
   - No protected or out-of-scope file changes.

Reviewer PASS must identify this necessary **post-reconciliation next-action admission prerequisite** and explicitly leave **DG-002 OPEN**. Helper-only unit coverage is insufficient without criteria 1–3 exercised through the actual retained runtime.

## Required Tests

Worker runs increasing scope and records exact commands, results and failures.

1. Capital-focused tests:

   ```bash
   .venv/bin/python -m pytest -q tests/test_rdp_capital.py
   ```

2. Actual runtime/cycle admission integration:

   ```bash
   .venv/bin/python -m pytest -q tests/test_rdp_placement.py tests/test_rdp_runtime.py
   ```

3. Affected prerequisites:

   ```bash
   .venv/bin/python -m pytest -q tests/test_rdp_account.py tests/test_rdp_quotes.py tests/test_rdp_accounting.py tests/test_rdp_lifecycle.py tests/test_rdp_marketdata.py tests/test_rdp_userstream.py tests/test_rdp_book_filter.py tests/test_execution.py
   ```

4. FL compatibility:

   ```bash
   .venv/bin/python -m pytest -q tests/test_favorite_live.py
   ```

5. Full suite:

   ```bash
   .venv/bin/python -m pytest -q
   ```

Add meaningful scenarios covering the acceptance criteria, including fractional inventory, both deficiency signs, physical versus model backing, evidence mutation across awaits, cancellation and teardown.

All create/cancel integration must use fakes. New integration tests must block network access and verify no attempted live connection.

Before Worker completion inspect:

```bash
git status --short
git ls-files --others --exclude-standard
git diff --stat
git diff --check
```

Preserve existing unrelated research outputs.

## Observability Requirements

Retain and log a sanitized decision with:

- market/attempt identity and captured timestamp;
- run/closed/resting order scope;
- mode and reason;
- QY/QN/Delta and confirmed inventory cost;
- model FreeCash/ReservedCash and candidate notional;
- account-wide BUY commitments and current cash/allowance backing;
- account/status receipt ages;
- feed generations and dynamic tick/minimum evidence;
- applicable filter/gate outcomes;
- outstanding execution gates;
- `execution_authorized=false`.

Never log credentials or raw SDK exception text.

This is factual decision evidence within the existing runtime. It is not the complete DG-009 metrics collector.

## Live Validation

None during this iteration.

No real create, cancel, signing or live order lifecycle validation is authorized.

After eventual MICRO_LIVE_READY and explicit human authorization, a supervised one-market run can verify that a reconciled initial cycle retains confirmed cost and fractional inventory when evaluating the next NORMAL or REBALANCE action. Such evidence belongs to the authorized integrated runner, not this development prerequisite.

## Explicit Non-Goals

- Actual second NORMAL submission.
- Automatic repeated NORMAL operation.
- Operating REBALANCE, repricing or recovery loop.
- New placement capture or final SDK guard path.
- Cumulative cycle history framework or multiple-close-proof interface.
- Full CanSafelyContinue and runtime STOP policy.
- Market-end/resolution handling.
- Complete metrics/reporting.
- CLI execution activation.
- Quote quotas, no-fill timeout or hard rebalance-duration stop.
- Any Frozen strategy change, new external runtime service or FL/IM redesign.
- Real-money trading and main merge.

## Risks

- Account reads are sequential observations. Final consistency/freshness checks must reject disagreement rather than invent an atomic snapshot.
- Official positions may lag confirmed fills. Such disagreement remains ineligible; do not silently adopt positions, waive reconciliation or introduce heuristic recovery.
- A retained successful close can become stale when later order/fill evidence arrives. Reprojection must validate present processor state.
- Initial admission’s empty-state assumptions cannot be reused by merely removing their guard.
- Physical spendability and the strategy’s $30 accounting allocation are distinct checks; conflating them can reset the budget or double-count reservations.
- New asynchronous reads must join existing serialization and drain ownership.
- Passing this prerequisite does not prove actual successive-order safety or close DG-002.

A defect blocking these criteria must be fixed within scope or returned to Architect; it cannot be deferred to obtain PASS.

## Deferred Findings

Required remaining delivery behaviors remain in their existing stable Gap IDs:

- **DG-002**: actual successive sends consume cumulative current run inventory, cost, reservations and fresh account evidence before every order.
- **DG-003**: automatic repeated NORMAL cycle operation using current factual admission and complete closure ordering.
- **DG-004**: operating deficient-side REBALANCE and recovery.
- **DG-005/DG-007**: full terminal/time policy and selected-market termination.
- **DG-008–DG-012**: official resolution, metrics, integrated CLI, final runtime evidence and independent readiness/archive.

These are open required deliveries, not new speculative requirements. This contract does not prescribe their future architecture.

DG-009’s documented metric-definition questions remain for its eventual authority check; this iteration makes no such definitions.

## Human Approval Required

None for the approved scope.

The user explicitly resumed automatic serial development and archival through MICRO_LIVE_READY. This contract implements existing Frozen accounting and next-action factual validation without revising strategy semantics or external runtime interfaces.

Any newly discovered behaviorally meaningful ambiguity, required out-of-scope architecture change or new external interface must return to Architect. A genuine missing strategy definition requires the smallest explicit human decision.

Real-money trading and main merge remain unauthorized.
