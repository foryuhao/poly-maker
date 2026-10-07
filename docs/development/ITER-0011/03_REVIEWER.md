# ITER-0011 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- ITER-0011 `01_ARCHITECT.md` and `02_WORKER.md`
- Actual source, tests, tracked diff and relevant untracked implementation.
- Existing gateway parsers, executor cleanup and installed SDK authentication/HTTP helpers.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `550ff9d5864d379b8d32e604871c21011d0b443e`

Reviewed six implementation/test files, including untracked account module and tests. Index/audit changes were considered separately. Preserved `research_outputs/` was untouched.

SHA-256:

| File | Hash |
|---|---|
| `src/polymaker/cli.py` | `e02f25d7e1420892f748206f422c2e35df37ecdda663b309336686b4288834a7` |
| `src/polymaker/execution/gateway.py` | `f3e93948f642d3710ffaab2f006a21ee3c64815ad9416085f84a0a963f6a8ed2` |
| `src/polymaker/rdp_live.py` | `b22c80db101b8de44b3b5c74279fb3aeede6e43f9123ae9efb7e741e223c0d90` |
| `src/polymaker/rdp_account.py` | `6885fe459ec48f4ba1ae1deb4eab2ca8bc80ed2698cbbdad8d51992bf4e79981` |
| `tests/test_rdp_live.py` | `2fc93a4c7f9af657220a120fe97f125dae7fd14b6eff8faf174a5360689df791` |
| `tests/test_rdp_account.py` | `4ca731798c75e4d716c15d4f1f8ec9f89e13093358978a6696d8de82dd1140d5` |

No Reviewer writes, Git mutations or real account actions.

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 — Default connection unchanged | Satisfied; existing authentication path retained. |
| 2 — Derive-only, no creation fallback | Satisfied by implementation and tests. |
| 3 — Invalid identity/configuration/credentials fail | Satisfied for reviewed supported cases. |
| 4 — Immutable, scoped evidence | Partially satisfied; malformed source evidence can be certified, MF-01. |
| 5 — Strict collateral/spender validation | Satisfied; exact Decimal conversion and normalized-key checks. |
| 6 — Failed/malformed reads cannot become empty state | Not satisfied, MF-01. |
| 7 — Existing state preserved without cleanup | Satisfied for valid inputs; malformed quantity can be fabricated, MF-01. |
| 8 — Configuration captured before awaits | Satisfied. |
| 9 — Expiry prevents success | Satisfied. |
| 10 — Separate CLI account/public evidence | Satisfied. |
| 11 — No prohibited execution/account mutation | Satisfied by inspected flow. |
| 12 — Relevant regressions pass | Satisfied; independently verified. |

Mandatory cleanup and log-sanitization requirements additionally fail under MF-02 and MF-03.

## Existing Codebase Compatibility

The implementation reuses the gateway, official readers, thread pool, SDK and existing preflight. It does not duplicate or replace a working subsystem.

Default FL authentication and logging behavior remain unchanged. Passing predecessor tests support compatibility. Repairs should remain confined to the opt-in account-read path and preserve default behavior.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Legacy defaults/filtering certify malformed account evidence.**

Locations: `gateway.py:860–864`, `gateway.py:925–932`, `rdp_account.py:198–225`.

Independent controlled probes used the actual gateway parsers within account orchestration:

1. An open-order row containing only:
   ```json
   {"id":"malformed","asset_id":"up","side":"BUY","price":"0.42"}
   ```
   lacks original quantity and matched quantity. The gateway defaults both to zero. Account capture succeeds and reports `remaining_size=0.0`.

2. A selected-token position row containing:
   ```json
   {"token_id":"up","current_size":"0","avg_price":"-1"}
   ```
   passes the gateway’s finite-value check and is discarded because quantity is zero. Account capture then succeeds with manufactured `(quantity=0, average_price=0)` evidence.

Observed outputs:
```text
MALFORMED_SUCCESS [('malformed', '0.0')]
MALFORMED_ZERO_POSITION_SUCCESS [('up', '0', '0'), ('down', '0', '0')]
```

The new wrapper cannot validate information already defaulted or discarded.

Repair: reject missing authoritative order quantities and invalid position rows before defaults/filtering in the RDP read path. Preserve legacy default behavior and reuse existing readers. Add actual-parser tests for missing original/matched quantities and invalid zero-quantity position prices, alongside valid zero/empty responses. Do not replace the reader subsystem.

**MF-02 — Cancellation leaves owned blocking account work running.**

Locations: `rdp_account.py:247–252`, `gateway.py:137–147`.

The wrapper calls `close()` on cancellation, but executor shutdown uses `wait=False`. Cancelling the asyncio future does not stop an already-running executor call.

A controlled probe blocked a real gateway `_io()` operation, cancelled account capture and awaited its cancellation. The task returned with:

```text
CANCEL_RETURNED closed True blocking_read_still_running True
```

The probe released and drained the operation afterward. No live request occurred.

Repair: ensure account cleanup tracks and finishes owned in-flight blocking work before cleanup is considered complete, preserving cancellation propagation and keeping the event loop responsive. Preserve default gateway behavior. Test cancellation during actual executor-backed derivation/read work; a fake `closed=True` flag alone does not establish resource cleanup.

**MF-03 — SDK diagnostics bypass wrapper sanitization.**

Locations: `gateway.py:_error_detail`, installed `py_clob_client_v2/http_helpers/helpers.py:request`.

The SDK logs raw error response bodies and raw request-exception text before gateway/account exception handling. Replacing the wrapper’s exception text therefore does not sanitize all logs.

An intercepted derive-only GET returned HTTP 400 with a response containing a secret sentinel. Account error text was sanitized, but captured SDK logs contained the sentinel:

```text
SANITIZED_WRAPPER read-only account check failed (PolyApiException)
SDK_LOG_SECRET_LEAK True
```

Repair: prevent unsafe SDK diagnostics from escaping during the opt-in account-read flow, including worker-thread logs. Retain sanitized failure categories and preserve unrelated/default logging. Test HTTP-error bodies and request-exception messages containing credential/header sentinels through the actual SDK HTTP helper.

### NOTE

Account evidence remains sequential rather than atomic, as correctly documented. It does not establish clean-account admission, authoritative fill inventory or CapitalGate approval.

## Frozen SOP Compliance

No strategy-semantic change was found. Account positions are not imported into confirmed-fill accounting. NORMAL, REBALANCE, STOP and frozen economic formulas remain unchanged.

Malformed evidence and incomplete cleanup must be corrected before this prerequisite is accepted.

## External Interface Review

The inspected path uses official CLOB HTTP and official Data API reads. Existing public preflight retains official HTTP/WebSocket infrastructure.

Address validation imports existing libraries without invoking blockchain RPC. No new runtime interface or dependency was introduced.

## Trading Safety Review

Derive-only authentication calls `derive_api_key()` without creation fallback. No order placement/signing, cancellation, allowance update, credential deletion or legacy Engine path was found.

Existing human positions/orders remain untouched. No real-money trading or live account action was performed during review.

Credential-safe logging is incomplete under MF-03.

## Inventory / Capital Accounting Review

Balance and required allowance use exact six-decimal Decimal conversion, including large values without context rounding. Invalid monetary values and contradictory normalized spender entries fail closed.

Snapshot balances do not activate the $30 capital model or change inventory accounting. MF-01 prevents accepting the account-state evidence as presently implemented.

## State Machine Review

No trading state machine starts. Public readiness and account evidence remain separate. Market expiry is checked before capture and after sequential reads.

Cancellation resource ownership is incomplete under MF-02.

## Test Review

Independent current-tree results:

- `uv run pytest -q tests/test_rdp_account.py tests/test_rdp_live.py tests/test_execution.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py`  
  **283 passed in 2.27s**
- `uv run pytest -q`  
  **1071 passed, 2 skipped in 12.77s**
- Ruff across all six implementation/test files: **passed**
- `uv run polymaker rdp-live --help`: **passed**
- `git diff --check`: **passed**

Passing tests miss the three reproduced defects. Repair tests must cover real parser behavior, running executor cancellation and actual SDK logging. Use controlled transports and retain forbidden mutation-method guards required by the contract.

## Scope Review

The implementation addresses one account-read prerequisite and preserves existing infrastructure. No unrelated strategy change or subsystem replacement was found.

All findings concern behavior already required by the approved contract. Route ordinary repairs to Worker; broader shared-core changes would require Architect review.

## Residual Risks

No live credential derivation or authenticated official account read was validated. Current evidence consists of controlled fixtures and independent tests.

Startup reconciliation, actual-cash capital binding and authenticated stream reliability remain deferred prerequisites.

## Final Decision

**RETURN_TO_WORKER**

BLOCKER = 0  
MUST_FIX = 3

Repair MF-01, MF-02 and MF-03, add the specified regression evidence, then return the exact repaired tree for independent re-review.
