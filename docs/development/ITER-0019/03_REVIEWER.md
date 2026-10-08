# ITER-0019 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP and current project authority.
- ITER-0019 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual seven-file implementation/test tree, including untracked `rdp_fill_window.py`.
- Existing user-stream receipt handling, processor validation/replay, accepted fill evidence, accounting and retained-session lifecycle.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `aa0208c67ec53b0ec2aea00f1ab60959eaa4169f`

Implementation remains within the seven approved source/test files. Orchestrator audit/index changes were distinguished from implementation. Preserved `research_outputs/` remains untouched.

Reviewed SHA-256:

| File | SHA-256 |
|---|---|
| `src/polymaker/userstream/client.py` | `7a17241b42dede1359b23fae596a965d156be8f77bfba1704f7d4bd0816e4a77` |
| `src/polymaker/state/tracker.py` | `9e7977f9968c8f45615e5f9571db255d3f4206711cee8d0ea32e1403ffd2b510` |
| `src/polymaker/rdp_userstream.py` | `457c9e9896acd9dad65546e64d7c66b3851eae05cc64df215f6e1d2e4f8f8121` |
| `src/polymaker/rdp_runtime.py` | `5cb1d93ab02ef470cf755d6ad929afca5d8bf3946c18f048c85f2a4f7dff1565` |
| `src/polymaker/execution/rdp_fill_window.py` | `bf7fd6cef36a51d52f4972b4b7de205462c2ee19b66fd604b5df39109498776b` |
| `tests/test_rdp_placement.py` | `3615293fc90acfa2c5146d47ab5b8480eba9247aec33008764b1ab466bdb420d` |
| `tests/test_rdp_userstream.py` | `2fe65918d4b24dbe9bcfb03c8c08007ad081a4a775523e15146fde14529bc15a` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1: default-off compatibility and production receipt capture | Implemented and covered by guarded regressions. |
| AC2–4: confirmed authority, ordering and original receipts | Basic cases pass; later admission of an earlier receipt remains defective, MF-01. |
| AC5–7: exact/configured duration, no extension or early completion | Basic boundaries pass; MF-01 prevents complete certification. |
| AC8–9: malformed/missing provenance and REST-only recovery | Missing/REST cases fail closed; malformed generation is accepted, MF-02. |
| AC10–11: adverse scope and healthy book movement | Source generally reuses authority correctly; required direct coverage is incomplete, MF-04. |
| AC12: zero external actions and independent close path | Query is synchronous and performs no gateway actions. Authorization metadata invariant requires MF-03. |
| AC13: regressions | Current full suite passes with zero network attempts; required missing regressions remain. |

## Existing Codebase Compatibility

The shared changes are default-off metadata additions. Existing admission, lifecycle progression, replay ordering, accounting and callbacks remain unchanged.

RDP enables receipt capture independently of research. Original confirmation metadata uses first-entry retention, preventing a later duplicate from repairing missing original timing.

No fill ledger, transport or reconciliation subsystem was duplicated.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — An earlier confirmation admitted after the window starts is silently ignored.**

Locations: `initial_fill_window()` and `RDPFillWindow.start()`.

Independent reproduction:

1. NO CONFIRMED arrives at100s without accepted MATCHED evidence.
2. YES MATCHED→CONFIRMED becomes accepted, with confirmation receipt102s.
3. Query establishes deadline105s.
4. NO MATCHED arrives at104s, admitting its original confirmation receipt100s.
5. Query still certifies deadline105s.

```text
EARLIER_ACCEPTED_ORIGINAL_RECEIPT=100000000000
FIRST_DEADLINE=105000000000
LATER_DEADLINE=105000000000
LATER_RELIABLE=True
LATER_CLOSURE_DUE=False
```

With the same final processor evidence but no previous query, the component chooses deadline103s and reports closure due. Thus query history can conceal an earlier qualifying origin.

Required repair: do not silently certify the retained later deadline when newly accepted evidence contradicts its origin. Preserve the established deadline and fail closed with a timing reason, consistent with the contract’s unreliable-evidence rule. Do not restart the window or assign a fresh period.

Add the exact two-leg CONFIRMED-before-MATCHED sequence, including a query between admissions. Assert retained deadline, uncertified continuation and no fabricated fresh window.

**MF-02 — Malformed generation metadata can certify a window.**

The generation check rejects booleans and unequal values but does not require an integer. Python equality makes float4.0 equal current generation4.

Independent reproduction:

```text
FLOAT_GENERATION_RELIABLE=True
STARTED=True
REASON=None
```

Required repair: validate generation as a non-boolean integer before comparing it with current generation. Add malformed-generation regressions, including float4.0, alongside malformed receipt fields. Preserve first-receipt non-repair behavior.

**MF-03 — The decision constructor can claim execution authorization.**

`RDPFillWindowDecision.execution_authorized` is an ordinary constructor field defaulting false. Unlike existing RDP evidence types, it can be supplied or replaced with true:

```text
CONSTRUCTOR_EXECUTION_AUTHORIZATION=True
```

This does not enable an actual send path, but violates the required constant metadata invariant.

Required repair: make the field constructor-independent and fixed false, following existing RDP evidence conventions. Verify ordinary returned evidence remains immutable and cannot be constructed/replaced with authorization true.

**MF-04 — Required window-specific adverse-evidence tests are missing.**

Existing processor/placement tests do not exercise the new window decision for these required scenarios:

- Foreign/observed-only IDs; wrong market/token/economics; zero-size evidence; quarantine and FAILED evidence.
- Partial/unknown placement and pending-overflow outcomes.
- Healthy book movement, including Book Filter rejection, after a window starts.

Add focused window assertions: rejected evidence never starts a certified window; failed/quarantined or unresolved scope prevents certified continuation; zero/unowned evidence contributes no confirmed inventory. Healthy ordinary book changes must preserve the established first receipt/deadline without gateway actions.

All independent reproductions used fake interfaces and recorded zero network attempts.

### NOTE

The existing basic exact-boundary, REST-only, pre-ack replay and default-off tests pass. They do not cover MF-01’s intermediate-query sequence.

## Frozen SOP Compliance

No accounting formula, quote rule or state threshold was changed.

The intended timer uses accepted confirmed evidence and original receipt time. MF-01 currently permits certification inconsistent with an earlier qualifying receipt. Its repair requires fail-closed evidence handling, not a strategy redesign.

## External Interface Review

No external runtime interface or dependency was introduced.

The window component has no gateway, account or execution client. Independent validation blocked DNS/socket connections and recorded zero attempts.

## Trading Safety Review

Window queries initiate no signing, placement, cancellation, account read or reconciliation. Expiry does not release replacement blocking.

Runtime-returned authorization remains false, but the evidence constructor must enforce that invariant, MF-03.

Real-money trading performed: **NO**.

## Inventory / Capital Accounting Review

Inventory is derived through `snapshot_inventory`; metadata is not independent fill truth. CONFIRMED-only evidence waits for accepted economic admission, and REST-only timing remains uncertified.

Shared accounting and capital behavior remain unchanged. Direct adverse-evidence regressions are still required.

## State Machine Review

Transient receipt context is captured before control handling can await and is cleared in `finally`. Existing pending replay preserves receipt/generation metadata.

The window is bound to the current completed pair. Health/generation loss, closure and market expiry fail closed. Earlier accepted provenance after start requires MF-01.

Existing explicit close remains separate and unchanged.

## Test Review

Independent current-tree full suite ran through `uv run python`, blocking `getaddrinfo`, `create_connection`, socket `connect` and `connect_ex`:

```python
pytest.main(["-q"])
```

Result:

```text
1344 passed, 2 skipped in 75.35s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Three controlled reproductions: MF-01, MF-02 and MF-03 confirmed.
- Scoped Ruff across all seven implementation/test files: passed.
- `git diff --check`: passed.

Passing regressions do not close the reproduced defects or mandatory coverage gaps.

## Scope Review

The implementation remains within the approved receipt-metadata and FillWindow prerequisite.

All findings are ordinary implementation/test repairs within existing authority. No Architect revision, strategy reinterpretation or human decision is required.

## Residual Risks

The component reports timing only. Automatic cycle closure, submission/final guards, subsequent NORMAL/REBALANCE execution, unknown recovery and complete metrics remain deferred.

`MICRO_LIVE_READY` is not established.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 4

Repair contradictory timing certification, strict generation validation and constant authorization metadata, then complete the required window-specific regressions and return for independent re-review.
