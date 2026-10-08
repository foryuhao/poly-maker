# ITER-0017 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP and current project authority.
- ITER-0017 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual tracked diff and both untracked placement files.
- Existing gateway typed acknowledgments, processor pending capture/registration/replay, store ownership and confirmed-fill accounting.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `97e763da15775e941a63873dcc3d0bab66294f12`

Implementation changes comprise four source/test files. Orchestrator audit/index changes were distinguished from implementation. Preserved `research_outputs/` remains untouched.

Reviewed SHA-256:

| File | SHA-256 |
|---|---|
| `src/polymaker/execution/rdp_placement.py` | `19a4f0c6f29f22611ea27c2db83e845e55c4dd42578cde7520241b3e1a59997d` |
| `src/polymaker/rdp_runtime.py` | `be6963d54e1a6ac5ecd8654c03d3eb52aa0832a9a1f396eb32f33b778230580d` |
| `tests/test_rdp_placement.py` | `04444d27e1bb8ded0e6cc2656ad322409030852a589577a0de0e42e434e5559d` |
| `tests/test_rdp_runtime.py` | `980c2d81ba288c6408d317962bf7ca7eacf60d79002d6d5200457a7689f4ebdc` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1: retained processor armed before outcome | Implemented and tested; actual admission-to-arm composition remains untested, MF-02. |
| AC2–4: registration, pre-ack replay and remaining state | Existing authority reused; tested fills apply once and newer local remaining size survives. |
| AC5–6: partial/unknown outcomes and observed-only IDs | Implemented and tested; safely attributed legs retained, observed-only IDs remain unowned. |
| AC7: conflicting acknowledgment evidence | Incomplete; duplicate-token exclusion can be reversed by a third occurrence, MF-01. |
| AC8: clean empty versus contradictory capture | Implemented and tested; anomaly evidence survives automatic buffer clearing. |
| AC9–10: replacement blocking and repeated completion | Implemented and tested, including zero-ID unknown and closure interruption. |
| AC11–12: accounting compatibility and no production execution | Satisfied within this iteration’s scope. |
| AC13: required controlled tests | Current suite passes with zero network attempts, but mandatory acceptance coverage remains incomplete, MF-02. |

## Existing Codebase Compatibility

The adapter delegates authoritative ownership, pending capture, replay, quarantine and accounting to existing components. It introduces neither another gateway nor a parallel ledger.

Local orders are inserted before registration/replay and are not subsequently overwritten with original acknowledgment size. Shared gateway, processor, store, FL and IM source remain unchanged.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — A third duplicate-token acknowledgment becomes owned after earlier occurrences were rejected as ambiguous.**

Location: `RDPPlacementOwner._validate_acknowledgements()`, construction of `safe_by_token`.

The second acknowledgment for a token removes the first candidate. A third occurrence then encounters an empty mapping entry and becomes a new safe candidate. Exclusion is therefore temporary.

An independent network-blocked probe used the existing retained-session fixture and actual resolution method with three distinct IDs, each representing the submitted YES economics:

```text
OUTCOME=PARTIAL
ANOMALIES=('TOO_MANY_ACKNOWLEDGMENTS',
           'DUPLICATE_ACKNOWLEDGMENT_TOKEN:yes-token')
ACKNOWLEDGED_IDS=('yes-2',)
REGISTERED_IDS=('yes-2',)
NETWORK_ATTEMPTS=0
```

The outcome remains blocked, but ownership has already been fabricated from ambiguous attribution. This violates the contract’s requirement that only safely attributable acknowledgments establish ownership.

Required repair:

- Permanently exclude a token once multiple acknowledgment candidates make its attribution ambiguous.
- Handle oversized acknowledgment collections without registering an ambiguously selected occurrence.
- Add a regression with three same-token acknowledgments proving none becomes owned or inserted locally. Preserve ownership of independently safe legs where attribution remains demonstrably valid.
- Keep the outcome unresolved and replacement blocked.

**MF-02 — Required admission and interruption acceptance tests are incomplete.**

The placement fixture manually constructs an eligible `RDPInitialNormalAdmission`; its filter and capital-admission evidence are absent. It does not invoke the actual initial-admission method before arming. Current tests also omit admission-scope/generation rejection and direct generic interruption resolution.

Required focused coverage:

1. Obtain a real initial admission through controlled retained-session/account/filter/quote/capital composition, arm that same session’s processor, and resolve the actual strict gateway outcome using a fake SDK. Assert the retained processor identity and no execution authority.
2. Reject wrong selected-market scope and mismatched public/user generations before arming. Assert no attempt, pending capture, local order or registry mutation.
3. Resolve a generic exception containing a credential sentinel. Assert sanitized UNKNOWN evidence, no ownership, retained replacement/admission blocking, and no sentinel in evidence or logs.

These cases are within the approved required-test matrix and existing implementation authority. No strategy change or subsystem replacement is required.

### NOTE

Passing regression tests do not establish safe attribution for the untested three-occurrence case.

## Frozen SOP Compliance

No quote-formula, state-threshold or capital-model change was found.

Acknowledgment remains distinct from fill confirmation. MATCHED-only events do not enter RDP confirmed inventory. MF-01 concerns unsafe ownership attribution, which must be corrected before this prerequisite passes.

## External Interface Review

No runtime interface or dependency was added.

Gateway placement tests use fake SDK methods. Independent tests and the defect reproduction blocked DNS/socket connections and recorded zero network attempts.

## Trading Safety Review

Production additions do not call placement, cancellation or signing. Attempt evidence fixes execution authorization to false.

Partial/unknown outcomes, including zero-ID outcomes, block replacement and initial admission. Observed-only IDs remain investigation evidence.

MF-01 must nevertheless be repaired because an ambiguous acknowledgment currently enters authoritative ownership.

Real-money trading performed: **NO**.

## Inventory / Capital Accounting Review

The existing store and processor remain authoritative. Buffered MATCHED/CONFIRMED events replay through existing validation and deduplication; tested confirmed quantities and costs are correct.

The adapter does not reserve or release capital. It preserves submitted economics in the registry and newer remaining quantity in local state.

No accounting formula was changed.

## State Machine Review

Capture is armed synchronously before simulated outcomes. Resolution captures pending anomalies before registration can clear buffers.

Clean empty outcomes may release capture. Contradictory capture, partial acceptance, unknown outcomes and replay quarantine remain unresolved. Duplicate completion is rejected.

Session closure marks an armed attempt interrupted. Authoritative cancellation, reconciliation and unknown-outcome recovery remain deferred.

## Test Review

Independent current-tree full suite used `uv run python` with DNS and socket connections blocked, invoking:

```python
pytest.main(["-q"])
```

Result:

```text
1262 passed, 2 skipped in 73.92s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Network-blocked three-duplicate-token probe: reproduced MF-01.
- Scoped Ruff for all four changed source/test files: passed.
- `git diff --check`: passed.

The complete current suite passes; MF-01/MF-02 require new meaningful regressions.

## Scope Review

The implementation remains RDP-local and within initial placement ownership scope. No unnecessary subsystem duplication or shared-core modification was found.

Both findings are ordinary implementation/test repairs. No Architect revision or human decision is required.

## Residual Risks

This prerequisite does not authorize submission or certify final market/time/account conditions.

Reconciliation, cancellation, unknown-outcome recovery, FillWindow, later NORMAL/REBALANCE operation and complete metrics remain outstanding. `MICRO_LIVE_READY` is not established.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 2

Repair permanent exclusion of ambiguous acknowledgment tokens and complete the specified acceptance tests, then return for independent re-review.
