# ITER-0018 — Reviewer Report

## Verdict

RETURN_TO_ARCHITECT

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP and current project authority.
- ITER-0018 `01_ARCHITECT.md`, `02_WORKER.md`, `03_REVIEWER.md` and immutable `04_WORKER_FIX_01.md`.
- Actual three-file source/test tree.
- Existing gateway cancellation/executor behavior, lifecycle barrier, processor/store and capital validation.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `977b5739938cfeccd14ef6bfdeaf8ab3901950b7`

Implementation changes remain in the three iteration files. Orchestrator audit/index changes were distinguished from implementation. Preserved `research_outputs/` remains untouched.

Reviewed SHA-256:

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `44229acd30bb0f6cf5cd11b7208b4a46c0a81892acb175f22ee8907142871895` |
| `src/polymaker/execution/rdp_placement.py` | `2d2bcfdb99b26565b27cb622ca0dfb256f147b48dea846f2a7ed8beb00f4c592` |
| `tests/test_rdp_placement.py` | `a6d73967bfcddbf1a8cd50bb0d3b3219f79a33634eb0d7bce2305c78edf91520` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Criteria | Result |
|---|---|
| AC1–3: scoped barrier and fill reconciliation | Implemented; cancellation adaptation conflicts with approved delegation scope. |
| AC4–5: capital proof and exact mode boundary | Fresh projection and registry checks repair the previous release defect. |
| AC6: incomplete evidence remains blocked | Existing cases pass; read-operation cancellation ownership remains incomplete. |
| AC7–9: unknown subsets and foreign/observed orders | Prior satisfied behavior remains intact. |
| AC10: current proof at recording | MF-01 closed. |
| AC11: additive history/accounting records | Satisfied. |
| AC12: cancellation/teardown ownership | MF-02 remains for SDK reads. |
| AC13: read-only cleanup | No implicit order cancellation introduced. |
| AC14: required safe validation | Guarded tests pass; diagnostic failure still exposes exception context, MF-03. |

## Existing Codebase Compatibility

Fresh capital evaluation and current registry validation appropriately reuse existing infrastructure.

The repaired cancellation adapter reproduces the gateway’s empty/paper handling, limiter, observation events, retained SDK client and executor. However, it now owns a copied cancellation execution path instead of delegating to `ExecutionGateway.cancel()`. This requires Architect justification under the explicit contract and existing-codebase rule.

Shared-core and FL source remain unchanged.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-02 — Actual SDK reads still detach from close-operation ownership on cancellation.**

The repair drains executor futures in `_StrictCloseGateway.cancel()`. Its `_call()` helper for `open_orders`, `get_order` and `get_trades` still awaits directly without equivalent draining.

Independent reproduction blocked the actual derive-only gateway executor’s fake SDK `get_open_orders()`, then cancelled the close caller:

```text
STAGE=open-read
CANCEL_CALL_DONE=True
CLOSE_TASK_CLEARED=True
EXECUTOR_STILL_RUNNING=True
```

Repeating the original SDK cancellation probe confirmed that branch is repaired:

```text
STAGE=cancel
CANCEL_CALL_DONE=False
CLOSE_TASK_CLEARED=False
EXECUTOR_STILL_RUNNING=True
```

Required repair: apply operation ownership/draining to every SDK operation used by the barrier. Cancellation or deadline completion must not clear the close marker while an owned SDK operation remains running.

Add actual-executor read regressions covering first/repeated cancellation, deadline and teardown. Keep overlap rejected until draining completes.

**MF-03 — Logging-handler failure still exposes the active raw SDK exception.**

The new warning record contains safe fields, but it is emitted inside the exception handler for the raw SDK failure.

An independent reproduction used a standard `logging.StreamHandler` with a stream that raises `OSError` on write. Logging’s diagnostic traceback included the active credential-bearing SDK exception:

```text
RESULT_REASON=CANCELLATION_UNCERTAIN
SENTINEL_IN_LOGGING_ERROR=True
```

The Worker report disclosed this class of failure, then changed the test to replace root handlers with a functioning capture handler. That verifies normal records but does not repair the exception-context exposure.

Required repair: emit sanitized diagnostics without retaining the raw SDK exception as active or chained context. Preserve the useful error category. Add a failing-handler regression that captures stderr and proves the sentinel is absent.

**MF-04 — Cancellation implementation conflicts with the approved delegation contract.**

Architect Minimal Scope item4 requires:

```text
Delegate cancellation/order/trade reads unchanged.
```

The adapter now directly uses gateway private fields, limiter, observation hooks, `_io` and `_client.cancel_orders()`, duplicating the gateway cancellation implementation. It no longer calls the gateway’s cancellation method.

Although the mirrored behavior is close to the existing path, this is an unapproved parallel execution path and maintenance boundary. A compatible Architect revision must choose and justify the smallest solution—for example, a narrowly scoped existing-gateway diagnostic extension or an explicitly approved adaptation.

This is an implementation architecture decision within Frozen semantics and official interfaces. No human strategy decision is indicated.

### NOTE

**MF-01 is closed.** Recording now validates current registry economics and recomputes capital through the existing evaluator. The original late-MATCHED reproduction now returns:

```text
STALE_PROOF_BLOCKED=True
FRESH_REASON=PENDING_FILL
```

Changed-price, changed-condition and reopened-order regressions are also present.

All independent reproductions used fake interfaces and recorded zero network attempts.

## Frozen SOP Compliance

No strategy formula, threshold or capital-model change was found.

Fresh proof validation now correctly preserves the block after pending fill evidence. Required cancellation ownership and diagnostic safety remain incomplete.

## External Interface Review

No external runtime interface or dependency was added.

The duplicated cancellation method still uses the retained official SDK, but its integration boundary requires Architect approval. No actual account, order or cancellation action occurred.

## Trading Safety Review

Known-ID scope, observed-only exclusion and partial/unknown blocking remain intact. Execution authorization remains false.

The outstanding defects concern ownership of blocking SDK operations and secret-safe diagnostics. Neither should be bypassed to progress delivery.

Real-money trading performed: **NO**.

## Inventory / Capital Accounting Review

Current registry validation and fresh capital projection repair stale proof reuse. Confirmed accounting remains unchanged; MATCHED-only evidence prevents release.

Closure history remains additive and owned accounting records are preserved.

## State Machine Review

SDK cancellation draining now preserves the close marker through interruption. SDK reads do not yet provide the same guarantee.

Unknown subsets remain blocked after their known IDs close. No automatic subsequent action or read-only cancellation path was added.

## Test Review

Independent guarded current-tree tests used `uv run python`, blocking DNS/socket connections, invoking:

```python
pytest.main([
    "-q",
    "tests/test_rdp_placement.py",
    "tests/test_rdp_lifecycle.py",
    "tests/test_rdp_capital.py",
    "tests/test_filltruth.py",
    "tests/test_rdp_accounting.py",
    "tests/test_execution.py",
    "tests/test_state.py",
])
```

Result:

```text
381 passed in 2.39s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Original late-MATCHED proof reproduction: repaired.
- Actual SDK cancellation reproduction: draining repaired.
- Actual SDK read cancellation reproduction: MF-02 confirmed.
- Failing logging-handler reproduction: MF-03 confirmed.
- Scoped Ruff and `git diff --check`: passed.

Worker records a current guarded full suite of **1,298 passed, 2 skipped**. The preceding independent full suite passed **1,293 tests, 2 skipped**. This re-review did not repeat the full suite; meaningful current regressions and direct reproductions establish the remaining findings.

## Scope Review

The fresh-proof repair stays within approved scope.

The cancellation mirror requires Architect revision because it contradicts explicit unchanged delegation and introduces duplicated execution logic. Remaining drain and diagnostic defects must be resolved in that approved implementation.

## Residual Risks

Submission, FillWindow scheduling, unknown-placement recovery, subsequent NORMAL/REBALANCE operation and complete metrics remain deferred.

`MICRO_LIVE_READY` is not established.

## Final Decision

RETURN_TO_ARCHITECT

BLOCKER: 0  
MUST_FIX: 3

Architect should approve the smallest compatible cancellation integration, retaining current-proof validation and requiring all SDK operations to drain and diagnostics to remain sanitized even when a logging handler fails. Then proceed through Worker and independent re-review.
