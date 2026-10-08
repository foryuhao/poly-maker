# ITER-0020 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md` and `docs/strategy/RDP_PHASE0_FROZEN.md`.
- ITER-0020 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual runtime/placement-test diff.
- Reused FillWindow, placement registry, scoped close barrier, capital projection, processor reconciliation and SDK-drain ownership.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `b257cf036537009394a52e4e3cc77309ac3550f4`

The contract records an earlier planning HEAD; the Worker discloses the actual implementation baseline. Reviewed implementation changes are limited to two approved files. Audit/index changes and preserved `research_outputs/` were distinguished from implementation.

| File | Reviewed SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `fa1433d7c25ad5e6447da19dc5c25f0cfe23cc8acc82dd9081e77386110f90e8` |
| `tests/test_rdp_placement.py` | `c3cdd3486ae3360b90ab37848767ccd7fb903f52d6e71ff4708ae4fb93bb12f3` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1: activation validation | Implemented; rejects invalid budget, unstarted window, mismatched attempt/registry and overlap before gateway actions. |
| AC2–4: deadline, ordinary movement, additional/full fills | Default boundary behavior passes; required configured and consistent natural-fill coverage remains incomplete, MF-03. |
| AC5: prompt failure-triggered closure | Generation, termination and unreliable-window cases covered. Owned sleeper failure is silently ignored, MF-02; direct health-loss coverage missing, MF-03. |
| AC6: separate trigger and fail-closed continuation | Defective: timing contradiction can release replacement and expose NORMAL, MF-01. |
| AC7–8: confirmed inventory, classification and cancel/fill race | Positive/negative-five and confirmed cancel-race paths pass; continuation certification still requires MF-01. |
| AC9: mutual exclusion | Coordinator/coordinator and coordinator/explicit-close tests pass. |
| AC10–12: interruption, teardown, SDK draining and deadline | Existing four-operation draining tests pass; owned wait-task failure requires MF-02. |
| AC13: default read-only behavior | Existing modes remain inactive; no CLI activation or order creation added. |
| AC14: regression | Current affected tests pass with zero network attempts, but required acceptance repairs remain. |

## Existing Codebase Compatibility

The coordinator reuses existing FillWindow decisions, registry checks and scoped close composition. It does not duplicate cancellation or reconciliation.

Gateway, processor, accounting and capital formulas remain unchanged. Existing FL engine/execution regressions pass. No IM dependency was introduced.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — An uncertified timing trigger can release replacement and fabricate continuation mode.**

Locations: `_run_initial_fill_window_completion()`, runtime close certification before `record_close()`.

The coordinator records `WINDOW_UNRELIABLE` but delegates close without carrying that continuation restriction into certification. `_close_state_reliable()` checks input health, not the permanently contradictory window.

Independent reproduction used consistent REST evidence:

1. Accept a YES confirmation at600s and establish deadline603s.
2. Activate the coordinator.
3. Admit another YES fill with original receipt599s, making window timing contradictory.
4. Return authoritative order matched quantity2 and matching confirmed REST trades for both fills.
5. Wake the coordinator.

Observed:

```text
TRIGGER=WINDOW_UNRELIABLE:CONFIRMED_RECEIPT_PRECEDES_ESTABLISHED_WINDOW
BARRIER_COMPLETE=True
CAPITAL_CERTIFIED=True
NEXT_MODE=NORMAL
REPLACEMENT_BLOCKED=False
OWNER_BLOCKED=False
```

This violates AC6 and the fail-closed continuation invariant. The existing adverse test hides the defect because its REST order reports zero matched quantity despite accepted fills.

Required repair: preserve scoped cancellation/reconciliation, but carry uncertified window continuation into the existing runtime close composition **before** capital/next-mode certification and `record_close()` release. Retain replacement blocking and expose no next mode. Changing only the returned DTO after ownership has already been released is insufficient.

Add the consistent-evidence reproduction above, asserting the trigger, reconciled inventory, retained owner block and absent next mode.

**MF-02 — Owned sleeper exceptions are ignored and become unhandled asyncio diagnostics.**

Location: `_wait_for_fill_window_wakeup()` and its coordinator caller.

The helper waits for completion but retrieves only pending-task results during cleanup. A completed sleeper exception is never retrieved or propagated.

Independent reproduction injected a sleeper that raises `RuntimeError` on its first invocation and blocks on its second:

```text
SLEEP_ATTEMPTS=2
CLOSE_READS_BEFORE_CANCEL=0
UNRETRIEVED_EXCEPTIONS=1
RAW_SENTINEL_IN_EXCEPTION_CONTEXT=True
FINAL_TRIGGER=CALLER_CANCELLED
```

The coordinator continued waiting after its owned wait operation failed. Closure occurred only after external cancellation; asyncio received the original exception containing the sentinel.

Required repair: retrieve completed-task outcomes, drain both owned wait tasks, and route a wait failure into one scoped close with a sanitized failure category. Preserve the separate trigger and avoid retrying a broken sleeper or exposing its raw exception.

Add a deterministic failing-sleeper test proving immediate single closure, no unhandled loop exception, sanitized evidence/logging and retained cleanup ownership.

**MF-03 — Required coordinator acceptance scenarios remain inadequately covered.**

Required repairs to tests:

- Exercise a configured FillWindow through the coordinator, asserting no close before its exact deadline and closure at that boundary. Current configured-duration tests exercise only the synchronous decision.
- Exercise public and user health loss without changing generation while the coordinator waits. Assert prompt scoped closure, the specific trigger and blocked continuation using otherwise consistent close evidence.
- Correct `test_coordinator_waits_exact_original_deadline_even_after_full_natural_fills`: its synthetic NO order hardcodes price`.39`, conflicting with the actual admitted quote. Use actual quote economics and consistent full-fill order/trade evidence. Preserve the timing assertions and prove successful barrier/accounting with NORMAL classification when both quantities are5.

These are ordinary repairs within the approved scope.

### NOTE

The operational close budget remains separate from FillWindow timing and does not introduce a strategy timeout.

## Frozen SOP Compliance

The default deadline and resting behavior follow Frozen §§20–21. Additional fills and healthy book changes do not retime the window.

MF-02 leaves a system-level wait failure unhandled. MF-01 violates required fail-closed continuation after unreliable window evidence. No strategy redesign is needed to repair either defect.

## External Interface Review

No new runtime interface or dependency was introduced. SDK calls remain delegated through approved official-interface infrastructure.

Independent tests and probes blocked DNS/socket access and recorded zero network attempts.

## Trading Safety Review

Explicit activation, scoped IDs, mutual exclusion and existing SDK draining are preserved. No CLI path activates the coordinator; no order-create implementation was added.

MF-01 can incorrectly release replacement ownership after an uncertified trigger. MF-02 can leave owned orders resting after a coordinator wait failure.

No real-money actions were performed.

## Inventory / Capital Accounting Review

Confirmed inventory and cancel/fill reconciliation reuse existing authoritative components. The confirmed fill-during-cancel test successfully includes the additional fill once.

The MF-01 reproduction demonstrates correct economic reconciliation combined with incorrect continuation certification; invalid REST fixtures must not obscure that distinction.

## State Machine Review

The coordinator retains a trigger/result and initiates one existing close. It performs no subsequent order or cycle.

Failure provenance must constrain continuation before ownership release. Current timing-trigger handling does not satisfy that requirement.

## Test Review

Independent current-tree execution used an inline `uv run python` guard blocking/counting DNS and socket connections, then:

```python
pytest.main([
    "-q",
    "tests/test_rdp_placement.py",
    "tests/test_rdp_runtime.py",
    "tests/test_rdp_lifecycle.py",
    "tests/test_rdp_accounting.py",
    "tests/test_rdp_capital.py",
    "tests/test_rdp_book_filter.py",
    "tests/test_execution.py",
    "tests/test_engine.py",
])
```

Result: **505 passed in65.15s; NETWORK_ATTEMPTS=0**.

Independent guarded reproductions confirmed MF-01 and MF-02: **NETWORK_ATTEMPTS=0**.

Scoped Ruff and `git diff --check`: **passed**.

Worker reports current guarded full suite: **1,384 passed,2 skipped; zero network attempts**. Reviewer did not independently repeat that full suite.

Passing tests do not cover the reproduced defects or complete the missing mandatory matrix.

## Scope Review

Implementation remains within the approved runtime/test composition. No subsystem was unnecessarily replaced or duplicated.

All findings can be repaired locally without gateway changes, new interfaces or Frozen SOP revisions.

## Residual Risks

This iteration completes only an explicitly activated, already-started initial window. Pre-fill control, subsequent trading cycles and final project readiness remain deferred.

Mocked tests establish no live execution or economic evidence.

## Final Decision

RETURN_TO_WORKER. Repair MF-01 and MF-02 and complete MF-03 coverage, then independently re-review the resulting tree. No human or architecture decision is required.
