# ITER-0014 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP, baseline and iteration index.
- ITER-0014 contract01, Worker02, Reviewer03, WorkerFix04, Reviewer05 and WorkerFix06.
- Actual source, tests and working-tree diff, including both untracked runtime files.
- Relevant existing retained owners, transport, account capture, gateway cleanup, processor and backing evaluator.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `96071c399c1f4aca216324e5c30530855b57af68`

No shared-core changes were found. Historical `research_outputs/` remains preserved. This review made no source, audit or Git edits.

Reviewed SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `42c3e674652eb85d46a4ff627f4bb7cab9533754b184fd04e4b2f457047b0b22` |
| `src/polymaker/rdp_marketdata.py` | `d283e17a8e0517be2a8b0ecc6412f0c1a8faf9fa9068d9a72983a79427543973` |
| `src/polymaker/rdp_userstream.py` | `432f1eb7b4daca630c25751c94724cf53a3bd264bd7464d5010bc1807d792971` |
| `src/polymaker/cli.py` | `6d85315a1fcb51030d2c3453a81d633373603d4b03e80754f1489d5c0168d591` |
| `tests/test_rdp_runtime.py` | `ad5d7cc36e5323a56868e8a9885ee5d8be23a740a5c206d1985e13e85faf4c88` |
| `tests/test_rdp_marketdata.py` | `9547bbddf9ed2329b8c6ee2005c628f64408da25f71b92496593326a8fccbd0f` |
| `tests/test_rdp_live.py` | `f53d57366f587d69f5c3193c237016279b170f06a281c5b277ad3d52ce7e2766` |

## Acceptance Criteria Review

Previous findings are closed:

- **MF-01:** Public health is monitored during authenticated startup; simultaneous completion retains cleanup ownership.
- **MF-02:** Final startup expiry is rejected.
- **MF-03:** Failure records retain available selected scope/generations and the supplied clock.
- **MF-04:** Real-owner fixtures now cover post-start books/ticks, selected-state startup rejection, generation failure at market end, account-read/cleanup cancellation, subscription scope, logs and retained processor evidence.
- **MF-05:** Owner cancellation during failed-startup draining is distinguished from child cancellation and propagated after repeated cancellation and draining.

AC1–11 otherwise remain supported by source and passing tests. AC12 and the credential-secrecy invariant are not fully satisfied because of MF-06. The full suite passes, but the current teardown test expressly accepts the unsafe raw exception cause.

## Existing Codebase Compatibility

Existing working transport, account, gateway, store, processor and accounting components remain reused. No unnecessary subsystem replacement, safety bypass or unrelated FL/IM modification was found.

Bounded API regressions pass.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-06 — Raw child teardown exceptions expose sensitive details through the returned exception chain.**

Location: `_await_user_start_with_public_health()` in `src/polymaker/rdp_runtime.py`, both public-failure and market-expiry drain-error branches.

The repair logs child errors safely but then raises `RDPObservationError` **from the original `drain_error`**. Its message can therefore appear in a formatted traceback or through `__cause__`.

The existing test `test_child_teardown_error_is_chained_without_losing_public_failure_category` deliberately raises:

```python
RuntimeError("secret-sentinel teardown")
```

and asserts that the raw exception remains the cause.

Independent network-blocked reproduction used that same boundary: suspended authenticated startup, public disconnect, then child teardown failure. Results:

```text
CATEGORY=FEED_DISCONNECTED
TOP_LEVEL_MESSAGE_CONTAINS_SENTINEL=False
FORMATTED_EXCEPTION_CONTAINS_SENTINEL=True
RAW_CAUSE_CONTAINS_SENTINEL=True
PUBLIC_CLOSED=True
NETWORK_ATTEMPTS=0
```

The category and resource cleanup are correct. The error object violates the contract requirement that credentials never enter errors.

Required repair: preserve the public/expiry category and sanitized teardown observability without exposing the original exception message through the outward cause/context chain. Suppress the raw chain or replace it with sanitized diagnostic information.

Update the regression to assert the category, cleanup, sanitized diagnostic and absence of the sentinel from the returned exception and formatted traceback. Cover both public-failure and expiry drain-error branches. Preserve repeated-cancellation behavior.

### NOTE

No other unresolved finding remains from the previous review.

## Frozen SOP Compliance

No strategy-semantic change was found. The repair remains within read-only ownership and observation scope.

No NORMAL/REBALANCE policy, additional risk rule or strategy timer was introduced.

## External Interface Review

Only existing official Polymarket HTTP/WebSocket paths are composed. No new runtime interface or dependency was added.

Independent tests and probes blocked socket connections and DNS resolution, recording zero attempts.

## Trading Safety Review

No submission, signing, cancellation, credential creation, allowance mutation, order adoption or Engine startup was introduced.

Synthetic orders remain test-only. No live action or real-money trading occurred.

## Inventory / Capital Accounting Review

The real retained processor remains the confirmed-fill projection authority. MATCHED evidence contributes no inventory; CONFIRMED evidence produces the expected quantity and cost.

The composed selected-state scenario now exercises actual gateway capture, backing rejection and owner cleanup. Accounting semantics remain unchanged.

## State Machine Review

Startup public monitoring, generation checks, final expiry rejection and cancellation-resistant ownership remain intact.

The composed termination-boundary test rejects generation change before reporting market end. Quiet healthy operation reaches normal market termination.

Trading-state integration remains outside this iteration.

## Test Review

Independent current-tree full suite ran through `uv run python`, with `socket.socket.connect`, `socket.socket.connect_ex` and `socket.getaddrinfo` blocked, invoking:

```python
pytest.main(["-q"])
```

Result:

```text
1177 passed, 2 skipped in 15.68s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Sanitized-output traceback reproduction above: completed, zero network attempts.
- Scoped Ruff for repaired source/tests: passed.
- `git diff --check`: passed.

The full-suite result does not close MF-06.

## Scope Review

MF-06 is an ordinary implementation defect introduced by repair. Its correction fits the approved contract and needs no architecture revision or human decision.

## Residual Risks

Account/backing snapshots remain historical evidence. Heartbeat does not prove authentication acknowledgment or reliable owned-event delivery.

Trading orchestration, reconnect reconciliation and complete Phase 0 metrics remain deferred. This iteration does not establish MICRO_LIVE_READY.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 1

Repair the outward teardown exception chain, retain the existing categories and cleanup behavior, and return for independent re-review.
