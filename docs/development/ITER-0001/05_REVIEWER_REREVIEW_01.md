# ITER-0001 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`
- Frozen SOP, particularly inventory/accounting sections 2–12 and 35
- `docs/development/ITER-0001/01_ARCHITECT.md`
- Original Worker and Reviewer artifacts
- `docs/development/ITER-0001/04_WORKER_FIX_01.md`
- Actual accounting source and complete repaired test file
- Relevant existing processor/store interfaces and mutable domain objects
- Current Git state and tracked/untracked iteration changes

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `29cdc603cebf305457f665b1aee2d0eb705406a3`
- Accounting source remains unchanged from the initial reviewed implementation.
- Repairs expand only `tests/test_rdp_accounting.py`.
- New source/tests and Worker/Reviewer artifacts remain untracked and were inspected directly.
- Tracked working-tree modification is the Orchestrator’s iteration index.
- Preserved unrelated `research_outputs/` remains outside iteration scope.

`git diff --check` passed. No existing production subsystem was changed. Reviewer performed no edits, Git mutations or trading.

## Acceptance Criteria Review

- **MF-01: CLOSED.** Added profitable balanced inventory, mirrored two-sided residual NO, and real-processor MINED/RETRYING exclusion tests with independent numerical expectations.
- **MF-02: CLOSED.** Added zero/negative fill-price and boolean-quantity cases that explicitly establish tracker admission without quarantine before projection rejection.
- **MF-03: PARTIALLY CLOSED.** Positions, registry records, fill evidence, evidence sets and all four callbacks now have before/after assertions. Open-order snapshots still share mutable objects, leaving that part of the requested independent state comparison ineffective.
- All other original acceptance assessments remain satisfied.
- Independent required suites pass.

Acceptance criterion 11 and the corresponding required read-only test remain incompletely demonstrated.

## Existing Codebase Compatibility

The implementation continues to reuse existing ownership, accepted-fill evidence, lifecycle advancement, deduplication and quarantine.

No working subsystem was duplicated or unnecessarily replaced. No existing safety mechanism was bypassed.

FL, IM and shared-core behavior remain unchanged.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-03 remains open — Capture independent open-order values.**

Location: `tests/test_rdp_accounting.py:315`.

The state capture uses:

```python
"orders": dict(store.orders)
```

`OpenOrder` is a mutable dataclass (`src/polymaker/domain.py:131`). Copying the dictionary preserves references to those same objects. If a snapshot read changed an order’s `size`, `price` or another field in place, both saved and current captures would see the changed object and the equality assertion would still pass.

This falls short of the requested independent authoritative-state snapshots.

Required repair: capture every open-order field as independent values, using `dataclasses.asdict`, `deepcopy`, or immutable field tuples. Retain the valid-read and rejected-read comparisons. No production or architecture change is required.

### NOTE

None.

## Frozen SOP Compliance

Production accounting remains compliant with the reviewed Frozen formulas. Confirmed fills alone supply quantities and actual acquisition costs; balanced/residual attribution, locked economics, settlement bounds and explicit winner calculations remain correct.

Negative LockedPnL remains representable. No Frozen SOP change occurred.

## External Interface Review

No external interface or runtime dependency was added. Fake-gateway recovery tests remain local.

## Trading Safety Review

The component has no placement, cancellation or live-send authority. Existing safety gates remain untouched.

Scoped unreliable evidence rejects explicitly. Unadmitted CONFIRMED-only evidence is not synthesized into inventory.

No real-money trading occurred.

## Inventory / Capital Accounting Review

Formula coverage now includes both residual directions and profitable/negative balanced economics.

InventoryCost remains actual confirmed-fill acquisition cost. Capital cash/reservation policy is outside this accounting contract.

No production accounting defect was identified during this re-review.

## State Machine Review

New lifecycle tests demonstrate that accepted MINED and RETRYING evidence contributes no confirmed inventory or cost before confirmation.

Tracker-owned replay, maker-fill identity and REST recovery remain intact.

NORMAL, REBALANCE, STOP and runtime orchestration remain outside this iteration.

## Test Review

Independent results:

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_rdp_accounting.py` | 23 passed in 0.13s |
| `uv run pytest -q tests/test_state.py tests/test_filltruth.py tests/test_favorite_first.py tests/test_favorite_first_review.py` | 118 passed in 0.89s |
| `uv run pytest -q` | 679 passed, 2 skipped in 9.20s |
| `git diff --check` | Passed |

No exclusions or failures occurred.

The remaining finding concerns assertion independence, not a failing test or identified production mutation.

## Scope Review

Repair stays within the approved test file. No shared-core, FL/IM, configuration, dependency, dataset, strategy or runtime-interface change occurred.

The remaining repair is an ordinary test correction within current authority.

## Residual Risks

The projection still requires complete explicit run order scope and separate reconciliation/pending-exposure safety gates.

It does not establish integrated runtime readiness or authorize live trading.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 1

Complete the independent open-order capture in MF-03, preserve this report and previous artifacts unchanged, and return the repaired tree for re-review. No human decision is required.
