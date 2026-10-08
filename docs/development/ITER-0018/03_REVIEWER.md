# ITER-0018 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP and current project authority.
- ITER-0018 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual runtime, placement-owner and test diff.
- Existing close barrier, strict gateway, executor ownership, processor/store, reconciliation and capital-proof validation.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `977b5739938cfeccd14ef6bfdeaf8ab3901950b7`

Implementation changes are confined to three approved source/test files. Orchestrator index/audit changes were distinguished from implementation. Preserved `research_outputs/` remains untouched.

Reviewed SHA-256:

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `46183b8c432343a46431c2c357ed1f28a73acf12a303787fb933cfd1dd16bbe4` |
| `src/polymaker/execution/rdp_placement.py` | `4d28ec394d5ff38b781bcec7871865e6c4166d58164c2bd449991a0138cc59ee` |
| `tests/test_rdp_placement.py` | `2a2eec5037f2bff0ee34726b1fa2d2126eb170aaac849d20c12487ed225f876c` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1–3: actual barrier, scoped cancellation and fill reconciliation | Implemented and exercised through controlled composition. |
| AC4–5: capital proof and five-share mode boundary | Runtime uses existing projection; closure-recording freshness remains defective, MF-01. |
| AC6: incomplete evidence retains block | Existing scenarios pass; stale pending evidence at recording and executor deadline/cancellation require repair. |
| AC7–9: unknown subsets, zero IDs and foreign/observed orders | Implemented and tested; uncertainty remains blocked and scope is restricted. |
| AC10: proof cannot survive processor/scope changes | Not satisfied, MF-01. |
| AC11: additive history and accounting records | Satisfied. |
| AC12: cancellation/teardown cannot detach close work | Not satisfied for actual gateway executor work, MF-02. |
| AC13: read-only paths initiate no cancellation | Satisfied by inspected composition and regressions. |
| AC14: required safe validation | Tests pass with zero network attempts; credential-log requirement fails, MF-03. |

## Existing Codebase Compatibility

The implementation reuses the existing cancellation/reconciliation barrier, processor, store and capital evaluator. It does not duplicate a gateway, ledger or reconciliation subsystem.

The strict read adapter preserves exact account values while presenting the existing barrier interface. FL, IM and shared-core source remain unchanged.

Three integration defects remain despite passing tests.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Closure recording can release a stale proof after new pending fill evidence.**

Location: `RDPPlacementOwner.record_close()`.

The method checks current confirmed inventory, quarantine, failed fills and local open orders, but trusts the previously supplied certified capital object. A new MATCHED fill can leave confirmed inventory unchanged while invalidating current capital certification.

Independent reproduction:

1. Obtain an actual complete zero-fill close barrier and certified capital projection.
2. Deliver a valid new owned MATCHED fill.
3. Call `record_close()` with the prior barrier/capital evidence.

Result:

```text
STALE_PROOF_BLOCKED=False
FRESH_CAPITAL_CERTIFIED=False
FRESH_REASON=PENDING_FILL
```

The existing fresh capital evaluator correctly rejects the new evidence, but recording releases the replacement block. This violates AC10 and the proof-bound release invariant.

Required repair: revalidate the close proof against current processor, registry and submitted scope at the release point, reusing existing capital/evidence validation. A prior `certified=True` object and unchanged confirmed inventory are insufficient.

Add regressions for a late MATCHED fill and changed registered economics/scope after certification. Stale evidence must retain the block without deleting accounting records.

**MF-02 — Cancelling the close coroutine can detach running SDK executor work.**

Locations: `close_initial_normal_placement()`, `_StrictCloseGateway._call()` and close-operation ownership.

Existing cancellation tests replace gateway I/O with inline calls and use an async fake that deliberately survives cancellation. They do not reproduce the actual retained derive-only gateway executor.

An independent reproduction restored the actual `ExecutionGateway._io`, enabled derive-only mode, and blocked the fake SDK’s `cancel_orders()` in a worker thread. Cancelling the close caller produced:

```text
CANCEL_CALL_DONE=True
CLOSE_TASK_CLEARED=True
EXECUTOR_STILL_RUNNING=True
```

The caller returns and `_placement_close_task` is cleared while the gateway still owns running cancellation work. Another close is no longer excluded by that task marker.

Required repair: retain close-operation ownership and drain the actual underlying SDK operation before cancellation or deadline completion returns and clears the operation marker. Preserve cancellation propagation and replacement blocking.

Add real-executor fake-SDK regressions for first/repeated cancellation, operational deadline and teardown. While the SDK call remains blocked, overlap must remain rejected and resources must remain owned. Release the fake thread explicitly during cleanup.

**MF-03 — Cancellation failure logs expose raw SDK exception text.**

Location: the new `_StrictCloseGateway.cancel()` composition through existing `ExecutionGateway.cancel()`.

The result reason is sanitized, but the reused gateway cancellation path logs `str(exc)` directly. Derive-only mode does not sanitize that log.

An independent fake-SDK reproduction emitted:

```text
cancel_failed err=SYNTHETIC_CREDENTIAL_SENTINEL n=2
CANCEL_FAILURE_RESULT_REASON=CANCELLATION_UNCERTAIN
```

This fails the contract’s requirement that credential sentinels remain absent from logs.

Required repair: apply narrowly scoped RDP cancellation diagnostic sanitization while preserving cancellation semantics and safe categories. Do not change FL defaults or silently expand shared-core scope.

Add a cancellation-exception sentinel test asserting absence from captured console/structured logs as well as returned evidence. If a compatible local repair cannot be made within the approved scope, return `WORKER_BLOCKED` for Architect revision.

All reproductions used fake external interfaces and recorded zero network attempts.

### NOTE

Passing inline-I/O tests do not establish ownership of concurrent executor operations.

## Frozen SOP Compliance

No strategy formula or state-threshold change was found.

The composition follows Cancel → Reconcile → confirmed inventory → capital projection. NORMAL/REBALANCE classification uses the exact absolute five-share boundary.

MF-01 weakens the required current-evidence release guarantee and must be corrected.

## External Interface Review

No external runtime interface or dependency was introduced. The new operation uses retained official gateway methods.

Independent full-suite validation and reproductions blocked DNS/socket connections and recorded zero attempts.

## Trading Safety Review

Only safely acknowledged, registry-validated IDs enter cancellation scope. Observed-only and foreign/manual IDs are not adopted or canceled.

Partial/unknown placement uncertainty survives known-subset closure. Zero-ID unknown outcomes perform no cancellation.

No production order-create path or execution authorization was introduced. Ordinary read-only cleanup does not initiate cancellation.

Real-money trading performed: **NO**.

## Inventory / Capital Accounting Review

Existing reconciliation and confirmed-fill accounting remain authoritative. Tests exercise partial confirmed fills, cancel/fill races, pending confirmation and quarantine.

Capital remains fixed at `$30`, and successful runtime projection uses existing close validation. MF-01 concerns stale proof reuse at the recording boundary, rather than a changed accounting formula.

## State Machine Review

Closure history is additive and owned accounting records remain available. Unknown subsets stay unresolved.

The runtime owns a close coroutine, but actual executor cancellation is not drained before the marker clears, MF-02. Incomplete, interrupted or stale closure must retain both operation ownership and replacement blocking.

## Test Review

Independent current-tree full suite ran through `uv run python` with `getaddrinfo`, `create_connection`, socket `connect` and `connect_ex` blocked:

```python
pytest.main(["-q"])
```

Result:

```text
1293 passed, 2 skipped in 73.93s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Three controlled reproductions: MF-01, MF-02 and MF-03 confirmed.
- Scoped Ruff across the three changed source/test files: passed.
- `git diff --check`: passed.

The current suite passes but omits the failing freshness, actual-executor and log-secrecy scenarios identified above.

## Scope Review

Changes remain within explicit initial-placement closure scope. No unnecessary subsystem replacement was found.

The findings require ordinary correctness and safety repairs. They do not require strategy or external-interface changes. Shared-core expansion remains subject to the approved contract.

## Residual Risks

Submission authorization, FillWindow scheduling, unknown-placement recovery, subsequent NORMAL/REBALANCE actions and complete metrics remain deferred.

This iteration does not establish `MICRO_LIVE_READY`.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 3

Repair current-proof release validation, actual executor draining and cancellation-log sanitization, then return for independent re-review.
