# ITER-0011 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP, baseline and iteration index.
- ITER-0011 Architect Contract, initial Worker/Reviewer reports and `04_WORKER_FIX_01.md`.
- Actual gateway/account implementation, CLI integration, modified tests and installed SDK HTTP helper.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `550ff9d5864d379b8d32e604871c21011d0b443e`

Reviewed tracked changes and untracked account source/tests. Preserved research assets were untouched. No Reviewer writes, Git mutations or live actions.

SHA-256:

| File | Hash |
|---|---|
| `src/polymaker/cli.py` | `e02f25d7e1420892f748206f422c2e35df37ecdda663b309336686b4288834a7` |
| `src/polymaker/execution/gateway.py` | `5d813e90a68b5eb3d21b481917fe0a59030c82d2250313bfc8799c5b2fffa0f6` |
| `src/polymaker/rdp_live.py` | `b22c80db101b8de44b3b5c74279fb3aeede6e43f9123ae9efb7e741e223c0d90` |
| `src/polymaker/rdp_account.py` | `e61ab733fdb077dfb4df2060265d075310abad4677f468e3f26e1ed0701b8113` |
| `tests/test_rdp_live.py` | `2fc93a4c7f9af657220a120fe97f125dae7fd14b6eff8faf174a5360689df791` |
| `tests/test_rdp_account.py` | `24301fe43bed963265c06f97675d8e9b7ec1b06001b0bbd7a80313980789168a` |

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 — Default connection behavior | Satisfied. |
| 2 — Derivation without creation fallback | Satisfied. |
| 3 — Identity/configuration/credential failures | Satisfied for reviewed cases. |
| 4 — Immutable scoped evidence | Satisfied; MF-01 repaired. |
| 5 — Strict collateral/spender evidence | Satisfied. |
| 6 — Read failures/malformed evidence | Satisfied; actual parsers now reject the reproduced malformed rows. |
| 7 — Existing account state preserved | Satisfied. |
| 8 — Configuration captured before awaits | Satisfied. |
| 9 — Expiry prevents success | Satisfied. |
| 10 — Separate account/public CLI evidence | Satisfied. |
| 11 — No prohibited account/execution mutation | Satisfied. |
| 12 — Relevant regression tests | Satisfied. |

The additional mandatory resource-cleanup requirement remains unsatisfied under repeated cancellation.

## Existing Codebase Compatibility

Existing gateway readers, executor, SDK and preflight are reused. No subsystem was duplicated or replaced.

Strict account parsing is opt-in. Default quantity defaults and zero-position filtering remain covered by regression tests. SDK log suppression applies only within derive-only worker calls; default diagnostics remain visible.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-02 — Cleanup remains interruptible by a subsequent cancellation.**

Locations: `ExecutionGateway.aclose()` and the awaited cleanup in `rdp_account.py`’s `finally`.

The first cancellation correctly enters asynchronous drain and waits for an already-running executor read. A second cancellation interrupts `asyncio.gather()` inside `aclose()`. Execution never reaches `self.close()`, while the running concurrent future remains tracked.

Independent controlled reproduction:

1. Start account capture with an actual gateway executor call blocked on a threading event.
2. Cancel capture; verify it waits in cleanup.
3. Cancel capture again before releasing the read.
4. Await cancellation and inspect ownership state.

Observed:

```text
FIRST_CANCEL_CLEANUP_WAITING True
SECOND_CANCEL_RETURNED closed False read_running True tracked 1
```

The probe then released the read and explicitly drained/closed the gateway. No live requests occurred.

**Required repair:** complete owned drain and closure when cancellation arrives during cleanup, then propagate cancellation. Keep the event loop responsive and leave no detached cleanup work. Preserve default gateway behavior.

**Required regression:** repeat cancellation while an executor-backed account read is still running; verify capture does not finish cleanup prematurely, closure occurs, tracked work is drained, and cancellation remains observable afterward.

### NOTE

- **MF-01 closed:** strict reads require original/matched order quantities and validate position prices before zero-size handling. Tests exercise actual parsers and preserve valid zero-position evidence.
- **MF-03 closed:** the thread-local SDK-helper filter suppresses raw response/request-error sentinels in derive-only worker calls while preserving default logging.

## Frozen SOP Compliance

No strategy-semantic changes were introduced. Existing account positions remain observation evidence and are not imported into confirmed-fill accounting. Frozen quote, capital and state-transition behavior is unchanged.

## External Interface Review

The account path remains limited to official CLOB and Data API HTTP reads. Public preflight retains official market interfaces. Address validation invokes no blockchain RPC. No new runtime dependency or interface was added.

## Trading Safety Review

Derive-only authentication retains no key-creation fallback. No order signing/submission, cancellation, allowance update, credential deletion or account cleanup is invoked.

SDK diagnostics now satisfy the reviewed sanitization cases. Repeated cancellation still leaves owned read work/resource cleanup incomplete.

No real-money trading or live account action occurred.

## Inventory / Capital Accounting Review

Immutable selected-state capture and exact Decimal collateral conversion remain intact. Required spender evidence and contradictory normalized allowances fail closed.

The snapshot does not authorize CapitalGate, adopt orders or mutate inventory accounting.

## State Machine Review

No trading controller starts. Public readiness and account evidence remain separate. Expiry checks prevent successful capture after market termination.

Single-cancellation cleanup works; repeated cancellation remains defective.

## Test Review

Independent current-tree checks:

- `uv run pytest -q tests/test_rdp_account.py tests/test_rdp_live.py tests/test_execution.py tests/test_gateway_positions_v2.py`  
  **224 passed in 2.04s**
- `uv run pytest -q`  
  **1076 passed, 2 skipped in 12.78s**
- Ruff across all six source/test files: **passed**
- `git diff --check`: **passed**

Repair tests meaningfully exercise actual parsers, executor-backed cancellation and installed SDK logging. The cancellation test covers one cancellation only; the independent repeated-cancellation probe exposes the remaining defect.

## Scope Review

The repairs address approved strict-read, sanitization and cleanup requirements. No unrelated strategy changes or subsystem replacement were found.

The remaining fix is an ordinary cleanup defect within the existing approved account/gateway scope.

## Residual Risks

Account observations remain sequential rather than atomic. Live credential derivation and authenticated account reads were not validated during review.

Startup reconciliation, actual-cash capital binding and authenticated-stream readiness remain deferred prerequisites.

## Final Decision

**RETURN_TO_WORKER**

BLOCKER = 0  
MUST_FIX = 1

Close the remaining MF-02 cancellation-cleanup defect and return the repaired tree with its targeted regression evidence for independent re-review.
