# ITER-0013 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Repair only MF-01 and MF-04 from `docs/development/ITER-0013/05_REVIEWER_REREVIEW_01.md` under the original ITER-0013 contract:

- Preserve a valid PONG received at the heartbeat send-slot boundary, fixed cadence, and the deadline of the oldest unanswered PING.
- Make the sanitized logger compatible with the installed asynchronous WebSocket connection’s `LoggerAdapter`, without leaking wire payloads or changing default FL behavior.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `46a774d9a9e7bda38823e7ab0da9825a2148b280`
- The working tree already contained ITER-0013 implementation and audit changes from prior stages, plus untracked `research_outputs/`. These were preserved.

## Existing Components Reused

- Existing `UserStream` application heartbeat loop and event signaling.
- Existing per-instance opt-in sanitized WebSocket logger.
- Installed `websockets` `ClientProtocol` and `ClientConnection` classes.

## Files Changed

- `src/polymaker/userstream/client.py`
- `tests/test_rdp_userstream.py`

## Implementation Summary

The heartbeat loop now consumes an acknowledgment event before sending at a cadence boundary. A PONG received after the timed wait expires but before the loop resumes clears the answered request’s deadline; the next PING starts a new unanswered deadline.

The sanitized logger now implements `log()`, the method used by the installed connection’s `LoggerAdapter`. It discards raw message arguments and exception details while emitting sanitized diagnostics. The override remains limited to the opt-in RDP heartbeat path.

## Behavior Before

A timely PONG delivered at the send-slot boundary could be cleared before consumption, leaving the answered PING’s deadline active. The next request could therefore fail at the earlier deadline.

The installed `ClientConnection` wrapped the custom logger in `LoggerAdapter`, whose `.log()` call failed because that interface was missing.

## Behavior After

The deterministic boundary test records PINGs at t=0, t=10, and t=20. The PONG received at t=10 is consumed before the second send, and the still-unanswered request’s deadline remains t=25. The installed asynchronous connection can emit frame and exception logs through its adapter without raising, and sentinel payloads remain redacted.

## Tests Added / Changed

- Added `test_pong_at_send_slot_boundary_starts_next_unanswered_deadline`, which fails if the old answered request’s deadline is retained.
- Extended `test_rdp_protocol_logger_redacts_installed_frame_and_exception_logs` to construct the installed `ClientConnection`, exercise its wrapped protocol logger, and exercise exception logging through the connection logger.
- Existing cadence, timeout, cancellation, failure-category, and default-mode tests remain intact.

## Tests Executed

- `uv run pytest -q tests/test_rdp_userstream.py`
- Network-blocked affected RDP/userstream suite via `uv run python - <<'PY' ... pytest.main(["-q", *files]) ... PY`, covering:
  - `tests/test_rdp_accounting.py`
  - `tests/test_rdp_quotes.py`
  - `tests/test_rdp_lifecycle.py`
  - `tests/test_rdp_capital.py`
  - `tests/test_rdp_book_filter.py`
  - `tests/test_rdp_marketdata.py`
  - `tests/test_rdp_account.py`
  - `tests/test_rdp_live.py`
  - `tests/test_rdp_userstream.py`
  - `tests/test_userstream_parse.py`
- Network-blocked full suite via `uv run python - <<'PY' ... pytest.main(["-q"]) ... PY`. The script patched `socket.socket.connect`, `socket.socket.connect_ex`, and `socket.getaddrinfo` to reject and count attempts.
- `uv run ruff check src/polymaker/userstream/client.py src/polymaker/rdp_userstream.py tests/test_rdp_userstream.py tests/test_rdp_live.py`
- `git diff --check`
- Inspected `git status --short`, `git ls-files --others --exclude-standard`, and `git diff --stat`.

## Test Results

- UserStream tests: **21 passed**.
- Affected RDP/userstream suite: **470 passed; 0 network attempts**.
- Full suite: **1,148 passed, 2 skipped; 0 network attempts**.
- Scoped Ruff: **passed**.
- `git diff --check`: **passed**.
- An initial logger-test attempt expected an exception type while calling `.error()` outside an exception context; it reported `UNKNOWN`. The test was corrected to exercise `.exception()` inside a real exception context, matching the installed connection diagnostic path. The final focused and broader runs passed.

## Regression Results

The existing delayed-PONG cadence and silent-stream timeout tests passed alongside the new boundary regression. The installed connection test confirms the logger adapter call succeeds and emits no sentinel content. Default-mode coverage confirms that callers without the opt-in heartbeat still receive no logger override or application PING.

## Git Diff Summary

The repair adds pre-send acknowledgment consumption, the logger’s `log()` adapter method, and two regression-test updates. No Git history operations were performed.

Other ITER-0013 changes and audit artifacts were present in the working tree and were preserved. Untracked historical/research assets under `research_outputs/` were preserved.

## Safety Checks

- All repair test runs were network-blocked where required; the affected and full runs recorded zero attempts.
- No account derivation or live reads occurred during this repair.
- No orders, cancellations, allowance updates, or credential creation occurred.
- Default UserStream behavior remains unchanged.
- No Git commits, pushes, or history operations were performed.

## Known Limitations

PONG remains transport responsiveness evidence, not proof of subscription acceptance or reliable order/fill delivery. Prior accidental read-only activity from the original ITER-0013 Worker run remains documented in earlier immutable artifacts; this repair did not repeat it.

## Deferred Findings

No new findings. Previously documented ITER-0013 limitations remain deferred.

## Reviewer Attention

Please verify the send-slot boundary regression and logger behavior through the installed `ClientConnection` wrapper, including the t=25 deadline and sentinel redaction.

## Human Live Trading Performed

NO
