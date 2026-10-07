# ITER-0004 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md` and Frozen SOP
- ITER-0004 `01_ARCHITECT.md`, original Worker/Reviewer reports, and `04_WORKER_FIX_01.md`
- Complete repaired lifecycle source and tests
- Relevant existing processor, filltruth, gateway and accounting behavior
- Current Git state and relevant untracked files

## Repository / Diff Reviewed

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `f4b085c8bfdd86eafbf2ae76c05623b47866ed0a`
- Changes remain in the approved lifecycle source/test additions.
- Other changes are iteration evidence and Orchestrator index maintenance.
- Preserved `research_outputs/` remains outside scope.

Reviewed SHA-256:

```text
rdp_lifecycle.py:
1bba8027aa7baaa64bbe2ccc82e50dbf5f100ceca7b85e3432278aea7939288b

test_rdp_lifecycle.py:
46951089ca05fa27d80c93fdecf62ce19cc1fc6a35f78440a4502e96fbc0752d
```

Reviewer performed no edits, Git mutations or trading.

## Acceptance Criteria Review

- **MF-01: CLOSED.** Finite deadline validation, pre/post gateway guards, guarded reconciliation reads, mutation checks and final completion checks repair the expired/non-yielding deadline defects.
- **MF-02: CLOSED.** Final local market-token order checks block the late-LIVE reproduction; a subsequent attempt recovers through existing terminal observations.
- **MF-03: PARTIALLY CLOSED.** Requested cancellation statuses, foreign-condition scope, seeded local-order removal, uncertain-cancel fill preservation, unadmitted recovery and remaining-live-order cases were added. The required COMPLETE-reconciliation/RETRYING case remains absent.

All production acceptance checks otherwise remain satisfied.

## Existing Codebase Compatibility

Existing gateway methods, filltruth, processor and accounting remain authoritative.

The deadline read adapter delegates to existing gateway methods and adds checks; it introduces no alternate parser, ledger, recovery implementation or runtime interface.

No subsystem was unnecessarily replaced, no safety control was bypassed, and no shared-core or FL/IM behavior changed.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-03 remains open — Exercise already-admitted RETRYING evidence with COMPLETE reconciliation.**

Location: `tests/test_rdp_lifecycle.py:199`.

The new RETRYING fixture supplies RETRYING-only REST evidence. Existing recovery does not admit that fill, so the test expects `RECONCILIATION_FAILED`.

This does not establish acceptance criterion 4:

> COMPLETE reconciliation with MATCHED/MINED/RETRYING evidence remains incomplete.

Independent local probe confirmed the required distinct path works:

```text
Processor: MATCHED → RETRYING, quantity 2
REST order: CANCELED, size_matched 2
REST trade: same exact maker fill, RETRYING

reconciliation outcome: COMPLETE
barrier reason: PENDING_CONFIRMATION
complete: False
inventory: None
```

Required test-only repair:

1. Seed the exact fill through MATCHED, then RETRYING, using the real processor.
2. Supply matching terminal order and RETRYING REST evidence.
3. Assert the per-order reconciliation outcome is COMPLETE while closure returns PENDING_CONFIRMATION and no inventory.
4. Change the same trade to CONFIRMED and assert a subsequent attempt completes exactly once.

The current RETRYING-only REST rejection case may remain separately. No production or architecture change is required.

### NOTE

None.

## Frozen SOP Compliance

The repaired implementation preserves Cancel → Reconcile → Update Inventory → Next Action.

No strategy timer, threshold, STOP reason or quoting policy was introduced. Deadline checks constrain this operational attempt only.

## External Interface Review

Only existing official-interface gateway methods are used.

No new external runtime interface, dependency, RPC or settlement path was added. Tests and probes were local.

## Trading Safety Review

Exact cancellation scope and foreign-order preservation remain intact.

Expired attempts cannot proceed through immediate gateway operations. Final contradictory local order state blocks completion.

Awaited operations are not detached or shielded; timeout and external task cancellation tests verify quiescence.

No real-money trading or cancellation occurred.

## Inventory / Capital Accounting Review

Confirmed recovered fills are preserved when cancellation is uncertain.

Pending, quarantined or unadmitted evidence prevents final inventory release. Accounting remains supplied by the reviewed projection.

No independent inventory/capital ledger or CapitalGate policy was added.

## State Machine Review

Fresh terminal observations and cumulative checksums remain required.

Final local order-state, admission and confirmation checks occur without another await before accounting/completion.

The remaining issue concerns durable test evidence for the implemented RETRYING confirmation gate.

## Test Review

Independent results:

| Command | Result |
|---|---|
| `uv run pytest -q tests/test_rdp_lifecycle.py` | 42 passed in 0.41s |
| `uv run pytest -q tests/test_filltruth.py tests/test_state.py tests/test_execution.py tests/test_filltest.py tests/test_rdp_accounting.py tests/test_rdp_quotes.py` | 245 passed in 5.62s |
| `uv run pytest -q` | 794 passed, 2 skipped in 9.77s |
| `git diff --check` | Passed |

No exclusions or failures occurred. A separate local probe verified the missing COMPLETE/RETRYING scenario.

## Scope Review

Repair remains within the two approved files.

The remaining correction belongs entirely in the existing lifecycle test file. No human or Architect decision is required.

## Residual Risks

The caller must serialize decisions, provide complete run scope and prevent submissions during closure.

A completed barrier remains an observation requiring fresh safety gates before later execution.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 1

Add the specified admitted-RETRYING test and confirmation retry, preserve prior artifacts, and return the repaired tree for re-review. Production MF-01 and MF-02 are closed.
