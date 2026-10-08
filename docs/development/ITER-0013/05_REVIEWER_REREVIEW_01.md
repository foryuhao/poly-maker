# ITER-0013 — Reviewer Report

## Verdict

RETURN_TO_WORKER

## Reviewed Inputs

- ITER-0013 Architect Contract, original Worker/Reviewer artifacts and `04_WORKER_FIX_01.md`.
- `AGENTS.md`, Frozen SOP and current development records.
- Actual source/test tree, including untracked observer files.
- Installed WebSockets `ClientProtocol`, `ClientConnection` and their logger-adapter integration.

## Repository / Diff Reviewed

Root: `/home/ubuntu/poly-maker`  
Branch: `rdp-phase0-dev`  
HEAD: `46a774d9a9e7bda38823e7ab0da9825a2148b280`

Research assets were preserved. No Reviewer source/audit edits, Git mutations or live calls occurred.

SHA-256:

| File | Hash |
|---|---|
| `src/polymaker/userstream/client.py` | `852795765a81c26d399ee98798997ddc8af446748676a6926ad110fbc50540b7` |
| `src/polymaker/rdp_account.py` | `edbf3c9fc303d0666d78f97a32b3bfef87b991018a8cf9de10f1723dcf003efe` |
| `src/polymaker/rdp_userstream.py` | `2757227a7b38e3fd5acdb02a01e94870bcc34eb835fc30d5431034aecc4b544c` |
| `src/polymaker/rdp_live.py` | `8ed0973bb6573649068dc8beebbb69973a3db0454a4052bddad46a7d46d9fe12` |
| `src/polymaker/cli.py` | `5c491754fde20fa15b5bcccf7a5d267400fbab2bf599ded16bc76a8f498e6614` |
| `tests/test_rdp_userstream.py` | `855f0395c34bda5e58debf4f6b39066e950d90a60f206e0949b9f562de3fab06` |
| `tests/test_rdp_live.py` | `2bb039091fcdf64da442a619bf33e4126af56737c033cca313876b2252f9d85d` |

## Acceptance Criteria Review

| AC | Assessment |
|---|---|
| 1 — Public path unauthenticated | Satisfied. |
| 2 — One gateway, same credentials, selected subscription | Satisfied. |
| 3 — Current-generation application PONG | Satisfied generally; boundary receipt can be discarded, MF-01. |
| 4 — Documented recurring cadence | Cadence repaired; response ownership remains defective, MF-01. |
| 5 — Failures/reconnect/expiry prevent capture | Reviewed failure paths reject; MF-01 introduces premature timeout. |
| 6 — Active stream and final health checks | Satisfied for reviewed paths. |
| 7 — Secret-free evidence/diagnostics | Evidence is appropriate; logger integration incomplete, MF-04. |
| 8 — Account API and FL defaults | Satisfied. |
| 9 — Existing parser/processor reuse | Satisfied. |
| 10 — Owned cleanup and cancellation | MF-02 closed. |
| 11 — Required tests/no real-money actions | Tests pass; two required interoperability/race cases remain missing. |

## Existing Codebase Compatibility

Existing transport, account validation, gateway ownership and processor routing remain reused. No subsystem was duplicated or replaced.

Default callers receive neither application heartbeat nor logger override. Repairs remain within the opt-in path. The custom logger must additionally satisfy the interface used by the actual installed asynchronous connection.

## Findings

### BLOCKER

None.

### MUST_FIX

**MF-01 remains open — A timely PONG at a send-slot boundary can be discarded.**

Location: `UserStream._application_heartbeat_loop()`.

The fixed-cadence repair works ordinarily. However, its next-send branch clears `_heartbeat_event` before consuming a response that arrived after the preceding wait timed out. The local `outstanding_since` retains the already-answered earlier PING.

Deterministic reproduction using the actual heartbeat loop and controlled clock/wait scheduling:

1. Send PING at `t=0`; interval is ten seconds, response timeout fifteen seconds.
2. The wait for the next-send slot times out at `t=10`.
3. Before the heartbeat loop resumes, process a valid current-generation PONG at `t=10`.
4. Resume the loop: it sends the next PING and clears the response event.
5. It retains the first PING’s deadline and fails at `t=15`.

Observed:

```text
AFTER_TIMELY_PONG_NEXT_RESPONSE_WAIT 5.0 receipt 10.0
TIMELY_PONG_RACE sent [0.0, 10.0] failure APPLICATION_PONG_TIMEOUT failure_time 15.0
```

The first request was answered. The next unanswered request was sent at `t=10`, so its response deadline should be `t=25`.

Repair: preserve and consume acknowledged-response state across scheduled-send boundaries. Keep fixed cadence and the deadline of the oldest genuinely unanswered request; do not mask missing responses by resetting deadlines indiscriminately.

Required regression: deterministic PONG arrival after wait timeout but before the next-send iteration, plus existing delayed-response and silent-stream tests.

**MF-04 remains open — The sanitized logger breaks the installed asynchronous connection.**

Location: `_SanitizedWebSocketLogger`.

Installed `ClientConnection` wraps the supplied protocol logger in `logging.LoggerAdapter`. That adapter invokes the underlying logger’s `log()` method, which the custom logger does not implement.

Independent reproduction constructed the installed `ClientProtocol` and `ClientConnection` with the actual RDP logger, without creating a transport or network connection:

```text
INSTALLED_ASYNC_CONNECTION_FRAME_LOG_FAILURE AttributeError '_SanitizedWebSocketLogger' object has no attribute 'log'
INSTALLED_ASYNC_CONNECTION_ERROR_LOG_FAILURE AttributeError '_SanitizedWebSocketLogger' object has no attribute 'log'
```

DEBUG frame logging and ERROR diagnostics both fail. The repair test exercises the bare Sans-I/O protocol and misses the asynchronous connection’s adapter.

Repair: provide a compatible per-connection logging interface while retaining payload/exception redaction and unchanged default logging.

Required regression: construct the installed asynchronous connection, exercise frame logging through its wrapped protocol logger and exception diagnostics through its connection logger. Assert no exception and no credential sentinel leakage.

### NOTE

- **MF-02 closed:** cancellation first received during successful cleanup is remembered; owned drains finish before cancellation propagates.
- **MF-03 closed:** recognized rejection and heartbeat-timeout categories survive subsequent transport errors.
- Original accidental read-only Worker activity remains honestly recorded in immutable artifacts. This review’s network-blocked runs recorded zero attempts.

## Frozen SOP Compliance

No strategy-semantic change was introduced. Transport evidence remains distinct from complete order/fill reliability. Inventory is not imported or adopted; capital and confirmed-fill accounting remain unchanged.

## External Interface Review

Only existing official HTTP and WebSocket interfaces are used. No runtime dependency or external interface was added.

Reviewer tests and probes used controlled fixtures and installed protocol objects with no live calls.

## Trading Safety Review

No signing, placement, cancellation, allowance update, credential creation or order ownership was added.

Returned evidence remains secret-free. Per-instance diagnostic redaction is appropriate in principle but must work through the actual asynchronous logger adapter.

## Inventory / Capital Accounting Review

Account precision, immutable order evidence, fixed `$30` backing and subsequent `$5` reserve remain unchanged.

The observation store remains isolated, with no owned-order registry or authoritative inventory import.

## State Machine Review

Current generation, connection health, heartbeat and task completion remain checked before capture.

Cancellation propagation and specific failure retention are repaired. The heartbeat acknowledgment race can still generate a false transport failure.

## Test Review

Independent current-tree tests ran with socket connection and DNS functions blocked:

- Focused account/userstream/preflight/fill-truth/parser suite:  
  **240 passed in 2.86s; network attempts = 0**
- Full suite:  
  **1147 passed, 2 skipped in 13.28s; network attempts = 0**
- Scoped Ruff: **passed**
- `git diff --check`: **passed**

New tests meaningfully cover cadence, silent timeout, cleanup cancellation and category preservation. They miss the response/send-slot race and actual asynchronous logger wrapping exposed by independent probes.

## Scope Review

Both remaining defects are ordinary implementation issues within the approved heartbeat and per-connection sanitization scope.

No Architect revision, strategy change or human decision is required.

## Residual Risks

PONG remains transport evidence rather than authentication/subscription acceptance or authoritative fill delivery. Long-lived ownership and reconnect reconciliation remain deferred.

The current tree cannot receive PASS until the two remaining defects are repaired.

## Final Decision

**RETURN_TO_WORKER**

BLOCKER = 0  
MUST_FIX = 2

Close the remaining MF-01 and MF-04 defects with the specified regressions, then return the exact repaired tree for independent re-review.
