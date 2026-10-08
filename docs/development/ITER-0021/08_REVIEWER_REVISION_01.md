# ITER-0021 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md` and `docs/strategy/RDP_PHASE0_FROZEN.md`.
- Authoritative ITER-0021 `06_ARCHITECT_REVISION_01.md`.
- `05_REVIEWER_REREVIEW_01.md` and `07_WORKER_REVISION_01.md`.
- Actual seven-file implementation/test tree.
- Existing Gamma/HTTPX routing, parser behavior, admission freshness and cleanup ownership.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `6d8f6532c82de296e68d79ce3d9613fb883a0e87`

Implementation remains within the revised scope. Orchestrator audit/index changes and preserved `research_outputs/` were left untouched.

| File | Reviewed SHA-256 |
|---|---|
| `src/polymaker/catalog/gamma.py` | `f0ee4707b9a0c0453a0e44ceb2ad236a81e709359ed0757e0524a1ea37af31bc` |
| `src/polymaker/rdp_live.py` | `a3e64fd5fd5ff00c2fe3495e34de53596cf02a56bab642effeeedebffdcbe0cd` |
| `src/polymaker/rdp_runtime.py` | `13710a108668157cc74edbe05b1876260cf4cfbcfe8fd5d7891a17391e6bf6b8` |
| `tests/test_btc5m.py` | `74a3547ab31a650e34a13d337537082256e08decbec795e2dbbb73e554e70283` |
| `tests/test_rdp_live.py` | `7b8a9aed291ccc11d1a5e32a2a4d56b19b3e474d6c9c2d9e8c10f29a857e7d25` |
| `tests/test_rdp_placement.py` | `51edf5901489a3bb4bfc31fef10632bd551f562919978e7ed4e7fd7b7c04d6ef` |
| `tests/test_rdp_runtime.py` | `7d04de533142c8117ad020f0ae96def3e5dcedccc06eaa6a25562295b8132a44` |

Reviewer made no source, test, audit or Git edits.

## Acceptance Criteria Review

| Revised criteria | Result |
|---|---|
| AC1–4: terminal recapture and primary reasons | Satisfied; failed account reads record current age and preserve their rejection category. |
| AC5: exact fractional freshness | Satisfied; producer boundary regressions pass. |
| AC6: safe upstream diagnostics | HTTP503 reason/body tests pass, but failing diagnostic handlers can expose raw exception context, MF-04. |
| AC7: numeric payload sanitization | Satisfied; original sentinel reproduction remains fixed. |
| AC8: legacy Gamma compatibility | Satisfied; default behavior and diagnostics remain covered. |
| AC9: routing, response semantics and cleanup | Inspected and independently probed; no routing or ownership defect found. |
| AC10: local adaptation without duplicated pipeline | Satisfied. |
| AC11: regression | Current full suite passes with zero network attempts; MF-04 remains uncovered. |

Unaffected original acceptance criteria remain implemented and covered by the current regressions.

## Existing Codebase Compatibility

The extension wraps the existing client’s transports and delegates requests and cleanup. It does not duplicate Gamma lookup or parsing.

Mount keys, proxy selection, `None` fallback mounts and shared underlying transport identity are preserved. Legacy callers leave `safe_diagnostics` disabled.

The RDP numeric guard retains existing conversions/defaults and delegates to the shared validator. No FL strategy, accounting or execution behavior changed.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-04 — Safe Gamma diagnostics are emitted inside the raw HTTP exception handler.**

Location: `GammaClient.market_by_slug()`.

The opted-in warning contains only `error_type`, but executes while the original `HTTPError` remains the active exception. A failing logging handler can therefore expose that exception through Python’s chained traceback.

Independent guarded reproduction:

1. Use the actual RDP status helper and Gamma client.
2. Fake the official request with `httpx.RemoteProtocolError("private-untrusted-status-line-sentinel")`.
3. Configure a genuine `logging.StreamHandler` whose stream raises `OSError` from `write()`.
4. Capture the real stderr output from `logging.handleError()`.

Observed:

```text
FAILING_HANDLER_RAW_EXCEPTION_IN_STDERR=True
FAILING_HANDLER_ERROR_DIAGNOSTIC=True
NETWORK_ATTEMPTS=0
```

An HTTP protocol exception can contain untrusted response text. Safe diagnostics must not reproduce it through a handler-failure traceback.

Required repair: capture the safe exception type/category, leave the raw exception handler, then emit the opted-in diagnostic. Preserve legacy default diagnostics and lookup return behavior.

Add a regression using an actually failing handler and sentinel-bearing HTTP error. Inspect real stderr, preserve categorical output and verify client cleanup. Do not suppress the handler failure in the fixture or mutate global production logging policy.

This is an ordinary repair within the approved shared-diagnostic scope.

### NOTE

Previous findings are closed:

- **MF-01:** Exact Decimal freshness boundaries remain correct.
- **MF-02:** Independent delayed account-failure reproduction now preserves `ACCOUNT_READ_FAILED`, records age11 with limit10 and retains `CURRENT_MARKET_TRADABILITY`.
- **MF-03:** Actual fake HTTP503 reason phrases are sanitized before HTTPX logging; default/configured/durable regression tests pass. Numeric payload sentinels remain absent from diagnostics.

## Frozen SOP Compliance

Fresh status remains an admission prerequisite without changing strategy semantics.

Exact age comparison, current terminal evidence, dynamic tick authority, CLOB minima and existing capital policy remain intact. MF-04 requires no Frozen SOP change.

## External Interface Review

Official Gamma exact-slug requests and redirect restrictions remain unchanged. No new runtime interface or dependency was introduced.

Independent verification recorded zero DNS/socket attempts.

## Trading Safety Review

Admission remains read-only and execution authorization remains fixed false. No order creation, cancellation, inventory adoption or coordinator activation was added.

Read ownership and cleanup regressions pass. Diagnostic failure must not expose raw upstream exception content.

## Inventory / Capital Accounting Review

Inventory, confirmed-fill accounting, fixed-$30 backing and the approved NORMAL reserve remain unchanged.

Terminal recapture corrects status evidence without replacing the primary account/input rejection reason.

## State Machine Review

Admission serialization and retained task ownership remain intact. Every terminal rejection now reevaluates available status evidence synchronously.

No new controller, trading state or subsequent action was introduced.

## Test Review

Independent current-tree full suite used an inline `uv run python` guard blocking/counting DNS and socket connections, then:

```python
pytest.main(["-q"])
```

Result: **1,441 passed, 2 skipped in 76.92 seconds; NETWORK_ATTEMPTS=0**.

Independent guarded probes verified:

- Delayed account failure, current age11 and unresolved status gate.
- Actual HTTP503 reason-phrase sanitization.
- Mounted/direct routing and `None` fallback.
- Unchanged mount keys, status/body/header semantics and one underlying close per transport across repeated client closure.
- MF-04’s failing-handler exception-context leak.

All probes recorded **NETWORK_ATTEMPTS=0**.

Scoped Ruff across all seven files and `git diff --check`: **passed**.

Existing tests do not exercise the genuinely failing diagnostic handler required to catch MF-04.

## Scope Review

The revised architecture is followed. The transport adapter is a local diagnostic adaptation around existing infrastructure; no replacement client or global logger mutation was introduced.

MF-04 can be repaired within the current approved Gamma diagnostic extension. No further architecture or human decision is required.

## Residual Risks

Fresh status applies only to its capture. Final submission revalidation and remaining project delivery work remain outstanding.

Mocked verification provides no live trading or economic evidence.

## Final Decision

RETURN_TO_WORKER for MF-04 only. Preserve the closed findings and reviewed routing/cleanup behavior, add the failing-handler regression, then independently re-review the resulting tree.
