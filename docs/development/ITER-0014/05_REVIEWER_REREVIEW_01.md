# ITER-0014 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP, baseline and iteration index.
- ITER-0014 `01_ARCHITECT.md`, `02_WORKER.md`, `03_REVIEWER.md` and `04_WORKER_FIX_01.md`.
- Actual seven-file implementation/test change, including untracked runtime files.
- Relevant existing public transport, authenticated account capture, user stream, backing evaluator, store, processor and accounting code.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `96071c399c1f4aca216324e5c30530855b57af68`

Tracked diff: six files including the index, 267 insertions and 42 deletions. Both untracked runtime files were reviewed separately. Historical `research_outputs/` remains preserved.

Reviewed SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `5c751abc9add2cbd5f9ab0db650531d217ffb271aad389b02411f0d2cf5b067a` |
| `src/polymaker/rdp_marketdata.py` | `d283e17a8e0517be2a8b0ecc6412f0c1a8faf9fa9068d9a72983a79427543973` |
| `src/polymaker/rdp_userstream.py` | `432f1eb7b4daca630c25751c94724cf53a3bd264bd7464d5010bc1807d792971` |
| `src/polymaker/cli.py` | `6d85315a1fcb51030d2c3453a81d633373603d4b03e80754f1489d5c0168d591` |
| `tests/test_rdp_runtime.py` | `795a2a00787d67208e6651efefd2d32201ae40cedec87e40185b736e27643efb` |
| `tests/test_rdp_marketdata.py` | `9547bbddf9ed2329b8c6ee2005c628f64408da25f71b92496593326a8fccbd0f` |
| `tests/test_rdp_live.py` | `f53d57366f587d69f5c3193c237016279b170f06a281c5b277ad3d52ce7e2766` |

## Acceptance Criteria Review

| Criteria | Status |
|---|---|
| AC1–2: CLI gates and bounded API compatibility | Satisfied; regressions pass. |
| AC3–6: retained selected owners, backing and processor | Real composition now exercised. MF-01/MF-02 repaired; remaining test gaps detailed under MF-04. |
| AC7: continued dynamic books/ticks | Service remains retained; current composed test updates ticks before startup, leaving post-start evidence incomplete. |
| AC8: categorized health failure | Startup monitoring repaired. Cancellation during failure draining remains defective, MF-05. |
| AC9–10: quiet lifetime and market-end closure | Real CLI observer reaches market end and closes both fake sockets and gateway. |
| AC11: cancellation and partial-start draining | Existing drains remain intact, but one cancellation is swallowed; composed read-drain coverage incomplete. |
| AC12: structured read-only evidence | Categories and cancellation records improved; known startup scope is lost, MF-03. |
| AC13: tests and isolation | Current full suite passes with zero network attempts. Required matrix remains partially uncovered. |

## Existing Codebase Compatibility

The repair retains existing working infrastructure. No shared-core, FL or IM modification was introduced.

Runtime resolution of injectable factories supports controlled composition tests without adding a parallel implementation. Existing bounded APIs pass their regressions.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-03 — Startup termination records still lose already known selected scope.**

Location: `src/polymaker/rdp_runtime.py:338–347`.

Failures are now logged by the outer observer, but `session` remains `None` until `open()` returns. Consequently even failures after successful bootstrap and public readiness record `condition_id=None` and discard captured generation context.

Independent network-blocked reproduction:

```text
Known selected condition: runtime-condition
Public failure during authenticated startup: FEED_DISCONNECTED
Recorded termination:
category=FEED_DISCONNECTED
condition_id=None
```

Required repair: retain safe startup context for termination logging. Include the selected condition and available generations after they are known; retain partial context for earlier failures. Use the observation’s supplied clock consistently where available. Preserve sanitized reasons and the read-only/trading fields.

Add assertions for a failure after bootstrap/public readiness and an earlier bootstrap failure.

**MF-04 — Required composed integration matrix remains incomplete.**

The new CLI test successfully exercises the actual observer, retained public/user owners, account capture, real backing evaluator and shared processor. Its MATCHED/CONFIRMED assertions now close the original processor-identity gap.

The following requested scenarios remain uncovered:

1. **Post-start book/tick updates.** The `.002` tick frame is included in the socket’s initial frame list and processed before startup. No book delta or tick change is injected after successful session startup.
2. **Actual selected-state startup rejection.** The test modifies a captured account and calls the evaluator separately; it does not return non-empty selected state through gateway capture and verify observer startup rejection/cleanup.
3. **Generation/health changes immediately before termination.** Existing tests change fake owner flags before ordinary polling, without exercising the selected market-end boundary.
4. **Repeated cancellation during composed account reads or cleanup.** New composed coverage exercises success; existing repeated-cancellation coverage still replaces both owners.
5. **Complete scope and secret assertions.** The composed fake socket does not retain subscription frames for checking the selected pair/condition. Credential assertions cover CLI output but not the generated durable log and returned evidence.

Required repair: extend the existing real-owner fixture with controlled events/clocks rather than adding another subsystem. Exercise the scenarios above, including the MF-05 drain boundary. Assert all sockets, store/task ownership and gateway work close before cancellation propagates. Capture subscription frames and inspect durable logs for credential sentinels.

These are remaining portions of the approved required-test matrix, not new features.

**MF-05 — Cancellation arriving during failed-startup draining is swallowed.**

Location: `src/polymaker/rdp_runtime.py:288–303`.

`_cancel_and_drain_user_start()` catches every `CancelledError` without recording whether the observer itself was cancelled. When invoked because public health failed, cancellation of the observer during this drain is discarded and the original public failure is subsequently raised.

Deterministic network-blocked reproduction:

1. Suspend authenticated startup.
2. Set public failure to `FEED_DISCONNECTED`.
3. Let startup cancellation enter a delayed cleanup block.
4. Cancel the outer observer while that cleanup is draining.
5. Release cleanup.

Observed:

```text
Raised: RDPObservationError
Category: FEED_DISCONNECTED
Public owner closed: True
Cancellation propagated: NO
```

Required repair: distinguish cancellation of the child from cancellation of the owning observer. Continue draining all owned work despite repeated cancellation, then propagate owner cancellation and record `CANCELLED`. An uncancelled public failure must retain its original category.

Add a deterministic regression for first and repeated owner cancellation during this failure drain.

### NOTE

MF-01’s public-health monitoring and simultaneous-completion ownership handling are repaired.

MF-02’s final startup-expiry rejection is repaired.

MF-03’s missing ordinary categorized startup records and market-end cleanup cancellation records are substantially repaired. An independent probe now correctly propagates cleanup cancellation and records a final `CANCELLED` failure after the earlier market-end event.

## Frozen SOP Compliance

No strategy-semantic changes were found. This remains input ownership and observation work; no trading policy, strategy timer or new risk rule was added.

The remaining fixes fit the Frozen SOP and approved contract.

## External Interface Review

Only existing official Polymarket HTTP/WebSocket paths are composed. No external runtime interface or dependency was added.

Independent tests and probes blocked socket connections and DNS resolution and recorded zero attempts.

## Trading Safety Review

No order submission, signing, cancellation, credential creation, allowance mutation, order adoption or Engine startup was introduced.

Synthetic registration is confined to test fixtures. No live action or real-money trading occurred.

## Inventory / Capital Accounting Review

The actual composed fixture now uses the existing backing evaluator and exact retained processor. MATCHED evidence contributes zero inventory; CONFIRMED evidence produces the expected quantity and cost.

Accounting policy remains unchanged. Selected-state rejection through actual observer startup still requires MF-04’s integration test.

## State Machine Review

Pinned public health is monitored during authenticated startup, with returned owners closed on simultaneous failure. Final startup certification checks expiry.

Normal polling retains generation, connection, heartbeat and task checks without treating quiet books as stale. The remaining cancellation defect is confined to failed-startup draining.

No NORMAL/REBALANCE controller is implemented in this iteration.

## Test Review

Independent current-tree full suite, run through `uv run python` with `socket.socket.connect`, `socket.socket.connect_ex` and `socket.getaddrinfo` blocked, invoking:

```python
pytest.main(["-q"])
```

Result:

```text
1171 passed, 2 skipped in 17.23s
NETWORK_ATTEMPTS=0
```

Additional checks:

- Network-blocked cancellation, startup-scope and market-end cleanup probes: completed, zero attempts.
- Scoped Ruff for repaired source/tests: passed.
- `git diff --check`: passed.

Passing tests do not close the reproduced cancellation defect or remaining contractual coverage gaps.

## Scope Review

No scope expansion or unnecessary subsystem replacement was found.

All remaining repairs are ordinary implementation/test fixes within ITER-0014. No architecture revision or human decision is required.

## Residual Risks

Account/backing snapshots remain historical evidence. User heartbeat does not prove authentication acknowledgment or reliable owned-event delivery.

Trading orchestration, reconnect reconciliation and complete Phase 0 metrics remain deferred. MICRO_LIVE_READY remains unestablished.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 3

Close remaining MF-03/MF-04 and repair MF-05, then return for independent re-review.
