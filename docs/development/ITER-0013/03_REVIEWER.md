# ITER-0013 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- `AGENTS.md`, Frozen SOP and current development records.
- ITER-0013 `01_ARCHITECT.md` and `02_WORKER.md`.
- Actual seven changed/new source and test files, including untracked observer files.
- Existing account validation, gateway cleanup, processor routing and installed WebSockets protocol logging.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `46a774d9a9e7bda38823e7ab0da9825a2148b280`

Research assets were preserved. Reviewer performed no writes, Git mutations or live calls.

SHA-256:

| File | Hash |
|---|---|
| `src/polymaker/userstream/client.py` | `973789565434736e569be8186814598467e75176862bb2d21ff5fd144efe8ddd` |
| `src/polymaker/rdp_account.py` | `edbf3c9fc303d0666d78f97a32b3bfef87b991018a8cf9de10f1723dcf003efe` |
| `src/polymaker/rdp_userstream.py` | `82e2920ff90165969817d2230a068cfeacd7d8a7f0a842d7697bb8b4823f49f5` |
| `src/polymaker/rdp_live.py` | `8ed0973bb6573649068dc8beebbb69973a3db0454a4052bddad46a7d46d9fe12` |
| `src/polymaker/cli.py` | `5c491754fde20fa15b5bcccf7a5d267400fbab2bf599ded16bc76a8f498e6614` |
| `tests/test_rdp_userstream.py` | `6a2e9d6b6762c017769b7a30042c69c6a7e933d6a9986b791452d994ff3e2372` |
| `tests/test_rdp_live.py` | `2bb039091fcdf64da442a619bf33e4126af56737c033cca313876b2252f9d85d` |

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 — Public path remains unauthenticated | Satisfied. |
| 2 — One gateway, same credentials, selected subscription | Satisfied. |
| 3 — Actual application PONG required | Satisfied. |
| 4 — Initial and recurring documented cadence | Initial ordering satisfied; recurring cadence defective, MF-01. |
| 5 — Failure/disconnect/reconnect/expiry prevent capture | Reviewed rejection paths fail closed; specific failure cause is overwritten, MF-03. |
| 6 — Active stream during reads and final health check | Satisfied for reviewed paths. |
| 7 — Immutable, scoped, secret-free evidence | Returned evidence is appropriate; underlying diagnostics leak credentials, MF-04. |
| 8 — Standalone account and FL defaults preserved | Satisfied by inspected code and regression evidence. |
| 9 — Existing parser/processor reused | Satisfied. |
| 10 — Cleanup on every exit, including cancellation | Resource draining generally works; cancellation during successful cleanup is swallowed, MF-02. |
| 11 — Required tests and no real-money actions | Current tests pass; required adversarial coverage remains incomplete. |

## Existing Codebase Compatibility

The implementation reuses `UserStream`, account validation, gateway ownership and existing processor routing. No transport, parser or fill ledger was duplicated.

Application heartbeat defaults remain disabled. Standalone account capture retains its API. Repairs should remain opt-in or per-instance and preserve legacy transport/logging behavior.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 — Waiting for PONG delays the required PING cadence.**

Location: `UserStream._application_heartbeat_loop()`.

The loop waits for a response before calculating the next-send delay. When response latency exceeds the heartbeat interval but remains within the response timeout, the next PING is sent immediately after that response rather than on schedule.

Controlled reproduction:

```text
Configured interval: 0.01s
Response delay: 0.04s
Response timeout: 0.06s
Observed PING intervals: [0.04, 0.04]
```

The production equivalent misses the ten-second send when a permitted response arrives later than ten seconds.

Repair: maintain the documented send cadence independently of response latency while preserving bounded missing-response detection and one owned heartbeat task. Do not repeatedly reset the outstanding-response deadline so a silent connection survives indefinitely.

Required tests: delayed-but-permitted PONG, recurring send timing and bounded failure when responses stop.

**MF-02 — Cancellation first received during successful cleanup is swallowed.**

Locations: `_drain_tasks()`, `_finish_cleanup()` and observer `finally`.

Both cleanup helpers catch cancellation and continue without recording that cancellation must propagate. An earlier cancellation in the main body remains pending, but a first cancellation during a pending successful return is lost.

Controlled reproduction:

1. Complete successful observation.
2. Block the fake gateway’s `aclose()` inside `finally`.
3. Cancel the observer while it awaits cleanup.
4. Release cleanup and await the observer.

Actual outcome:

```text
CANCEL_DURING_SUCCESS_CLEANUP_RETURNED_SUCCESS RDPAuthenticatedObservation
```

Repair: finish every owned cleanup operation, then propagate cancellation received during cleanup. Leave no detached cleanup work and preserve event-loop responsiveness.

Required tests: first cancellation during stream drain and gateway closure, plus subsequent cancellations while those operations remain blocked. Verify cleanup and caller-visible cancellation.

**MF-03 — `run_once()` overwrites an already recognized failure category.**

Location: `UserStream.run_once()` exception handler.

The handler unconditionally replaces `failure_category` with `USER_STREAM_CONNECTION_FAILED`. A recognized subscription rejection followed by a receive/teardown exception therefore loses its actual cause.

Controlled reproduction:

```text
Received auth_error
Subsequent receive/teardown RuntimeError
Returned/stored category: USER_STREAM_CONNECTION_FAILED
```

Repair: preserve the first specific recognized failure; assign a generic connection category only when no specific category exists.

Required tests: subscription rejection and heartbeat timeout followed by transport/teardown failure, retaining the original category with sanitized diagnostics.

**MF-04 — WebSockets frame diagnostics bypass `sanitize_errors=True`.**

Location: `_connect_and_listen()` connection construction.

Only application exception logs are sanitized. The underlying WebSockets connection retains its default logger, which logs outgoing subscription frames at DEBUG level.

A controlled fake transport routed the actual `UserStream` subscription through the installed Sans-I/O `ClientProtocol`, with `sanitize_errors=True` and DEBUG diagnostics enabled:

```text
SANITIZED_USERSTREAM_INSTALLED_FRAME_LOG_EXPOSES_API_KEY True
```

The installed protocol emitted a TEXT-frame log containing the literal test API-key sentinel `LEAK`.

Repair: protect underlying frame and exception diagnostics for the RDP connection itself. Preserve legacy logging and avoid a global logging change that affects unrelated connections.

Required tests: actual installed frame logging and exception diagnostics at DEBUG/error levels, with credential sentinels absent from logs, CLI output, errors and returned evidence.

### NOTE

The Worker disclosed accidental official read-only HTTP/account requests and an authenticated user subscription from stale fixtures. Those historical calls cannot be undone and must remain recorded.

Current independent tests ran with socket connection and DNS calls blocked and recorded **zero attempted network operations**, confirming the corrected fixture isolation. No real-money action was identified.

## Frozen SOP Compliance

No strategy-semantic change was found. Transport evidence is correctly distinguished from complete order/fill reliability. Existing inventory is not imported or adopted; confirmed-fill accounting and capital rules remain unchanged.

## External Interface Review

The observer fixes the user endpoint to the official Polymarket URL and reuses existing official HTTP account reads. No new runtime dependency or external interface was introduced.

Reviewer probes used fake transports and the installed protocol without network access.

## Trading Safety Review

No signing, placement, cancellation, allowance update, credential creation or order ownership was added. The observation store is isolated and has no owned-order registry.

Credentials remain absent from returned evidence, but underlying diagnostics violate the secrecy invariant under MF-04.

## Inventory / Capital Accounting Review

Account capture reuses existing exact order economics and validation. Startup backing remains separate and fixed at `$30`; the approved `$5` reserve is unchanged.

User events route through the existing processor in an isolated store. No authoritative project inventory or reservation state is adopted.

## State Machine Review

Generation, connection health, application heartbeat and task completion are checked before final capture. PONG is explicitly described as transport responsiveness rather than authentication acknowledgment.

Heartbeat scheduling, failure-category preservation and cleanup cancellation require the identified repairs.

## Test Review

Independent tests ran through `pytest.main()` with `socket.socket.connect`, `connect_ex` and `socket.getaddrinfo` patched to reject network operations.

- Focused files: `test_rdp_userstream.py`, `test_rdp_live.py`, `test_rdp_account.py`, `test_filltruth.py`, `test_userstream_parse.py`  
  **234 passed in 2.55s; network attempts = 0**
- Full suite  
  **1141 passed, 2 skipped in 13.06s; network attempts = 0**
- Scoped Ruff: **passed**
- CLI help: **passed**
- `git diff --check`: **passed**

A preliminary command included nonexistent `tests/test_tracker.py`, collected no tests and was corrected before the reported runs.

Existing tests cover immediate PONG and cancellation before cleanup, but miss delayed-response cadence, cancellation first arriving in `finally`, preservation of recognized failure causes and underlying frame diagnostics.

## Scope Review

The changes remain within the approved transport/account-observation prerequisite. The four findings are ordinary implementation defects resolvable within that scope.

No Architect revision or human strategy/interface decision is required.

## Residual Risks

PONG does not establish authentication/subscription acceptance or authoritative fill delivery. Returned evidence becomes historical after cleanup.

Long-lived stream ownership, reconnect reconciliation, confirmed-fill integration and continuous reliability remain deferred.

## Final Decision

**RETURN_TO_WORKER**

BLOCKER = 0  
MUST_FIX = 4

Repair MF-01 through MF-04, add the specified meaningful regressions and return the actual tree for independent re-review.
