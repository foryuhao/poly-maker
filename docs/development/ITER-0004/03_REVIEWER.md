# ITER-0004 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`
- Frozen SOP, particularly sections 18, 21 and 25–26
- ITER-0004 `01_ARCHITECT.md` and `02_WORKER.md`
- Actual lifecycle source and complete tests
- Existing gateway, filltruth, processor, order normalization and accounting APIs
- Actual Git state and relevant untracked files

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `f4b085c8bfdd86eafbf2ae76c05623b47866ed0a`
- New implementation: `src/polymaker/execution/rdp_lifecycle.py`
- New tests: `tests/test_rdp_lifecycle.py`
- Both untracked files were inspected directly.
- Other changes are Worker evidence and Orchestrator index maintenance.
- Preserved unrelated `research_outputs/` remains outside scope.

Reviewed SHA-256:

```text
rdp_lifecycle.py:
84fe321481f92261be8b17dd5909560806ba7cbc2e9c24e74b501dc79a9de1f5

test_rdp_lifecycle.py:
4873d0baaa8deeacbb581dfc39434488b9fa541d8d4fb6dfb4810b9a29d882d3
```

`git diff --check` passed. Reviewer performed no edits, Git mutations or trading.

## Acceptance Criteria Review

Basic cancellation/recovery sequencing, confirmed partial-fill retention, fully filled orders, explicit scope, foreign-order preservation, pending confirmation, checksum race detection and cancellation propagation are implemented.

Acceptance criteria 9–10 are not fully satisfied: current order-state changes after terminal observation are not revalidated, and expired deadlines can allow gateway operations or successful completion.

Required test evidence is also incomplete as detailed below.

## Existing Codebase Compatibility

The component reuses existing cancellation, exact REST recovery, normalization, processor order updates and accounting.

No replacement gateway, parser, ledger or reconciliation engine was created. No shared-core or FL/IM behavior changed, and no existing safety control was bypassed.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Explicitly enforce the operational deadline at execution boundaries.**

Locations: `rdp_lifecycle.py:87`, `:122`, `:136`, and `:206`.

`asyncio.timeout_at()` schedules cancellation through the event loop. It does not prevent an immediately completing await from continuing when the deadline is already expired.

Independent reproduction with the existing fake gateway:

```text
deadline = loop.time() - 1
run={"o1"}, closing={"o1"}

calls:
open_orders → cancel → open_orders → get_order → open_orders

result:
OPERATIONAL_DEADLINE
```

The attempt invokes cancellation after its deadline. With empty run/closing scopes and empty open-order reads, the same expired deadline returns `complete=True`.

Required repair:

- Validate a finite usable deadline before gateway work.
- Check expiry before every gateway operation and authoritative-state mutation.
- Recheck immediately after awaited operations and before final inventory/completion.
- Preserve timeout cancellation and external task-cancellation propagation.
- Add immediate/non-yielding gateway tests proving an expired deadline performs no calls, empty scope cannot complete after expiry, and expiry during the final read cannot release closure.

These are operational bounds, not strategy timers or StopReasons.

**MF-02 — Revalidate current order state after the final await.**

Location: `rdp_lifecycle.py:206`.

The final synchronous checks revalidate fill quantities and confirmation but do not inspect current local order state.

Independent reproduction:

1. Reconcile a zero-fill CANCELED order and apply its terminal observation.
2. Block the final `open_orders()` read.
3. Deliver a valid owned LIVE `OrderEvent` through `processor.on_order()`.
4. Release the final read, which returns no remote open orders.

Observed result:

```text
complete=True
reason=None
processor store still contains open order "o1"
```

The newer contradictory order observation leaves order state unreliable, yet the barrier exposes actionable inventory.

Required repair:

- After the final await, recheck current market-token order state before completion.
- A run order reintroduced locally, or another conflicting market-token order observation, must block closure pending renewed reconciliation.
- Do not erase contradictory evidence to force completion.
- Add the late-LIVE event reproduction and a repeated-attempt recovery case.
- Keep final validation and accounting free of further awaits.

**MF-03 — Complete the explicit contract test matrix.**

Location: `tests/test_rdp_lifecycle.py`.

Add these missing required scenarios:

- Partial terminal orders with both `CANCELED` and `CANCELLED`.
- COMPLETE reconciliation with MINED and RETRYING fills, proving no final inventory until confirmation.
- Cancellation returns while the order remains authoritatively open; closure remains incomplete.
- A registered foreign-condition scope fails before gateway calls.
- Seed a local resting order, then verify a validated terminal processor observation removes it. The current zero-fill test starts with no local order.
- An unadmitted CONFIRMED-only record first blocks closure, then existing REST recovery admits it and a subsequent attempt completes.
- Uncertain cancellation with recovered confirmed fills preserves those fills while returning no final inventory.

Existing tests establish related paths, but do not demonstrate these expressly required cases.

### NOTE

None.

## Frozen SOP Compliance

The intended sequence matches Cancel → Reconcile → Update Inventory → Next Action.

No new quoting policy, strategy timer, state or economic filter was introduced.

MF-02 currently permits completion with contradictory order state. MF-01 leaves the operational bound incomplete. Both repairs fit the existing Frozen SOP and contract.

## External Interface Review

The component calls existing official-interface gateway methods only.

No external runtime interface, RPC, dependency, merger or settlement path was added.

Tests and review probes used local fake gateways.

## Trading Safety Review

Only explicit registered closing IDs are cancellation targets. Foreign/manual orders are preserved. The component never places orders.

Cancellation uncertainty remains a sticky failure while reconciliation can preserve recovered truth.

Final order-state reliability and explicit deadline enforcement require the repairs above.

No real-money trading or cancellation occurred.

## Inventory / Capital Accounting Review

Confirmed partial fills and duplicate REST/WS evidence reuse the authoritative processor and reviewed accounting.

Incomplete results expose no inventory. Pending or unadmitted evidence blocks completion.

No alternate inventory/capital ledger or CapitalGate policy was introduced.

## State Machine Review

Fresh normalized observations establish terminal cancellation or complete MATCHED filling; absence from open orders alone is insufficient.

Fill checksums are rechecked after the final await. Current order-state changes need the additional final check in MF-02.

The component introduces no NORMAL/REBALANCE/STOP controller.

## Test Review

Independent results:

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_rdp_lifecycle.py` | 29 passed in 0.37s |
| `uv run pytest -q tests/test_filltruth.py tests/test_state.py tests/test_execution.py tests/test_filltest.py tests/test_rdp_accounting.py tests/test_rdp_quotes.py` | 245 passed in 5.64s |
| `uv run pytest -q` | 781 passed, 2 skipped in 9.73s |
| `git diff --check` | Passed |

No exclusions or test failures occurred. Separate local probes reproduced MF-01 and MF-02.

## Scope Review

Current implementation stays within the approved two new files.

All requested repairs are ordinary implementation/test corrections within those files. No architecture change, shared-core modification or human decision is required.

## Residual Risks

The caller must serialize trading decisions, construct complete ownership scope and prevent submissions while closing.

A successful closure remains an observation requiring fresh safety checks before later execution.

Integrated runtime, CapitalGate, Book Filter, metrics and readiness remain outstanding.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 3

Repair the deadline and final order-state checks, complete the specified tests, preserve existing artifacts, and return the actual repaired tree for independent re-review.

No human or Architect decision is required.
