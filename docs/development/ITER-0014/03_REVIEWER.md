# ITER-0014 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`
- `docs/strategy/RDP_PHASE0_FROZEN.md`
- `docs/development/BASELINE.md`
- `docs/development/ITERATION_INDEX.md`
- ITER-0014 `01_ARCHITECT.md` and `02_WORKER.md`
- Actual changed/new source and tests.
- Relevant existing market-data transport, authenticated account capture, user-channel ownership, startup-backing evaluator, store, processor and confirmed-fill projection.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `96071c399c1f4aca216324e5c30530855b57af68`

Reviewed five tracked implementation/test changes and both untracked runtime files. The index and Worker artifact are audit changes. Preserved `research_outputs/` assets were excluded and untouched.

Reviewed SHA-256 hashes:

| File | SHA-256 |
|---|---|
| `src/polymaker/rdp_runtime.py` | `ebd755a3cc9cea2e2b0faa6d30880406fbb0214b594e3a36e2ad5a23556e944e` |
| `src/polymaker/rdp_marketdata.py` | `d283e17a8e0517be2a8b0ecc6412f0c1a8faf9fa9068d9a72983a79427543973` |
| `src/polymaker/rdp_userstream.py` | `95978ad2f353c7fb6e3a2f8877f30038a4bfc79a9781306cc83bff9abda01e8d` |
| `src/polymaker/cli.py` | `5f271f9e0850b88a622f19ff5c4dc5c930de021af59ad509a20a1a2a33ed6666` |
| `tests/test_rdp_runtime.py` | `f30efce5b8bbfefaf771b92a5ef88c759f31fdb08a0565434d54a292a6623ecf` |
| `tests/test_rdp_marketdata.py` | `9547bbddf9ed2329b8c6ee2005c628f64408da25f71b92496593326a8fccbd0f` |
| `tests/test_rdp_live.py` | `f53d57366f587d69f5c3193c237016279b170f06a281c5b277ad3d52ce7e2766` |

## Acceptance Criteria Review

| Criteria | Review |
|---|---|
| AC1–2: mode gates and bounded preflight compatibility | Satisfied by source inspection and passing regressions. |
| AC3: one selected gateway and stream pair | Composition appears correct; required integrated evidence remains incomplete, MF-04. |
| AC4: both streams alive through capture and lifetime | Successful-path retention exists; public startup loss is unmonitored during account startup, MF-01. |
| AC5: readiness and eligible initial backing | Existing evaluator reused; expired startup can still be reported successful, MF-02. |
| AC6: exact retained processor | Source wires the same processor; required retained-processor event fixture is missing, MF-04. |
| AC7: continued book/tick updates | Existing service remains retained; composed update test missing, MF-04. |
| AC8: categorized health failures | Post-start checks exist; startup monitoring and durable failure evidence need repairs, MF-01/MF-03. |
| AC9–10: quiet operation and market end | Existing fake-session tests pass; complete composed runtime/CLI evidence remains pending, MF-04. |
| AC11: cancellation and partial-start cleanup | Reviewed cleanup drains resources in probes; integrated gateway-read coverage remains incomplete, MF-04. |
| AC12: read-only evidence | Successful results use the required mode and trading flag. Failure/cancellation records are incomplete, MF-03. |
| AC13: required tests and network isolation | Current suite passes with zero network attempts; required composed scenarios remain missing. |

## Existing Codebase Compatibility

The implementation reuses existing transport, account validation, gateway, store, processor and accounting components. It does not replace or duplicate a working subsystem.

The public single-attempt option defaults off. Existing bounded APIs retain their signatures and pass regressions. No shared-core, FL or IM source changes were found.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Public health is not monitored during authenticated startup/account capture.**

Location: `src/polymaker/rdp_runtime.py:102`.

`open()` directly awaits the complete authenticated starter. Public health is next inspected only after that starter returns and backing evaluation finishes. Public failure can therefore leave authenticated startup and account reads running without the required healthy public input.

Deterministic network-blocked reproduction:

1. Let public readiness succeed.
2. Suspend the user-session factory on an event.
3. Mark the public session disconnected.
4. Yield for 60 ms.

Observed:

```text
open_still_pending=True
public_closed=False
```

Cancelling the outer operation then drained the public owner successfully.

Required repair: monitor the pinned public attempt while authenticated startup is pending. Public disconnect, generation change, fatal validation or task completion must end startup with its category and cancel/drain the authenticated startup operation and its owned work. Preserve ownership if startup completion and public failure coincide.

Add deterministic tests with a suspended account read and public loss, including the simultaneous-completion boundary. Do not add reconnect behavior or a new subsystem.

**MF-02 — Startup can be certified after the selected market has ended.**

Locations: `src/polymaker/rdp_runtime.py:103–139`.

Backing eligibility uses an earlier time capture. Session construction captures `started_at` again, but no final expiry check precedes successful startup.

Deterministic reproduction used a complete flat selected account, sufficient $30 balance/allowance, valid spender identity and the **actual** `evaluate_initial_rdp_backing`:

```text
backing evaluation time=109
market end=110
next startup clock capture=110
actual_backing_eligible=True
started_at=110
startup result=SUCCESS
```

The wrapper advanced the clock after the real evaluator returned; it did not alter eligibility.

Required repair: reject expiry at the final startup certification boundary, before emitting startup success or returning the retained session. Drain both owners and preserve a categorized expiry reason. Add a test for expiry during final startup certification; the existing test covers only expiry before public creation.

**MF-03 — Durable observer termination evidence omits ordinary startup failures and cancellation phases.**

Locations: `src/polymaker/rdp_runtime.py:95–98,140–149,234–241`; observe-only CLI error handling.

The startup handler logs only `RDPObservationError`. Reused `RDPBootstrapError` subclasses, including feed/account/user-channel errors, bypass that record. Bootstrap occurs outside the handler. Cancellation handling surrounds only `wait_until_end()`, excluding startup and cleanup.

Network-blocked reproductions:

```text
user startup APPLICATION_PONG_TIMEOUT:
termination_events=0
public_closed=True

cancellation during suspended startup:
termination_events=0
resources_closed=True

cancellation first delivered during normal-end cleanup:
termination categories=['MARKET_END']
CancelledError propagated
resources_closed=True
```

Required repair: record sanitized observer termination for categorized discovery/feed/account/user failures and cancellation during startup, observation or cleanup. Preserve specific existing categories. Cancellation during cleanup must not leave the durable final outcome solely as successful completion. Include required mode, selected scope where available, termination time and `real_money_trading_performed=false`.

Add log/output assertions for these phases and credential sentinels. Keep cancellation propagation and resource draining intact.

**MF-04 — Required tests do not exercise the actual composed retained runtime.**

Locations: `tests/test_rdp_runtime.py`, `tests/test_rdp_live.py`.

The new runtime fixture replaces both session owners and the backing evaluator. Its processor is `object()`. Consequently it cannot verify the real authenticated starter, actual account capture, derive-only gateway, stream processor identity or retained dynamic books together.

`test_retained_processor_counts_only_synthetic_confirmed_owned_fill` constructs a separate store and processor without opening a retained session. The CLI market-end fixture replaces `observe_rdp_market` entirely, so it verifies formatting rather than the contract’s full observation path.

Required repair: use real RDP session owners/composition with controlled fake gateway and WebSocket dependencies. Cover the contract’s requested evidence:

- Both streams active before, during and after account capture; one derive-only gateway and selected subscription scopes.
- Exact `UserStream` processor identity, with synthetic MATCHED/CONFIRMED events projected through that retained processor.
- Dynamic book/tick updates visible in the retained service.
- Actual startup-backing rejection for selected pre-existing state.
- Health/generation changes immediately before startup capture and termination.
- Repeated cancellation while account reads or gateway cleanup drain.
- Credential sentinels absent and forbidden mutation/signing methods untouched.
- Full observe-only CLI invocation through the real observer, advancing controlled time to market end.

Reuse existing fixture components. No additional feature or architecture is required.

### NOTE

The current tests pass; the findings concern uncovered behavior and missing contractual integration evidence.

## Frozen SOP Compliance

No strategy-semantic change was found. The observer adds no NORMAL, REBALANCE, FillWindow or STOP policy.

The Frozen SOP remains unchanged. These repairs concern input ownership, fail-closed startup and audit evidence.

## External Interface Review

Only existing official Polymarket HTTP/WebSocket paths are composed. No new runtime interface, blockchain RPC path or external dependency was introduced.

Independent tests and probes blocked socket connections and DNS resolution and recorded zero attempts.

## Trading Safety Review

No order signing, submission, cancellation, credential creation, allowance mutation or order adoption was added. No Engine startup occurs.

The observer remains read-only. No real-money action was performed.

## Inventory / Capital Accounting Review

The existing startup-backing evaluator is reused without policy changes. Existing account state is not imported into authoritative trading inventory.

Confirmed-fill accounting is unchanged. The required proof through the retained processor remains incomplete, MF-04.

## State Machine Review

The observation lifetime retains components after startup and checks pinned generations and health during normal polling. Quiet book activity alone does not cause staleness. Single-attempt public transport avoids automatic recovery.

Startup monitoring and final expiry certification require MF-01/MF-02. Trading-state integration remains explicitly deferred.

## Test Review

Independent current-tree full suite, run through `uv run python` with `socket.socket.connect`, `socket.socket.connect_ex` and `socket.getaddrinfo` blocked, invoking:

```python
pytest.main(["-q"])
```

Result:

```text
1165 passed, 2 skipped in 13.03s
NETWORK_ATTEMPTS=0
```

Additional independent checks:

- Network-blocked deterministic reproductions above: completed, zero attempts.
- Scoped Ruff across all seven changed/new source/test files: passed.
- `uv run polymaker rdp-live --help`: passed; displays `--observe-only`.
- `git diff --check`: passed.

Existing full-suite success does not close MF-01–MF-04.

## Scope Review

Changes remain within RDP composition and its CLI/tests. No unnecessary subsystem replacement or unrelated refactoring was found.

All requested repairs fit the approved contract. Architecture revision and human intervention are unnecessary.

## Residual Risks

Startup account/backing evidence remains historical rather than continuously refreshed. Heartbeat evidence does not establish authentication acknowledgment or reliable owned-event delivery.

Trading orchestration, reconciliation integration and complete Phase 0 metrics remain deferred. This iteration does not establish MICRO_LIVE_READY.

## Final Decision

RETURN_TO_WORKER

BLOCKER: 0  
MUST_FIX: 4

Repair the scoped startup monitoring, expiry certification, termination evidence and composed integration tests, then return for independent re-review.
