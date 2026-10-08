# ITER-0013 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved bounded, read-only RDP user-channel check composed with authenticated account preflight. Strategy behavior and trading authority are unchanged.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `46a774d9a9e7bda38823e7ab0da9825a2148b280`
- Preserved untracked `research_outputs/` assets; did not modify them.
- No Git history operations performed.

## Existing Components Reused

Reused `ExecutionGateway` derive-only connection and account reads, existing account validation, `UserStream` subscription and event processing, `UserEventProcessor`, `StateStore`, and the RDP preflight and CLI.

## Files Changed

- `src/polymaker/userstream/client.py`
- `src/polymaker/rdp_account.py`
- `src/polymaker/rdp_userstream.py` — new
- `src/polymaker/rdp_live.py`
- `src/polymaker/cli.py`
- `tests/test_rdp_userstream.py` — new
- `tests/test_rdp_live.py`

## Implementation Summary

Added default-off, generation-scoped application heartbeat handling to `UserStream`. RDP preflight opens the fixed official user endpoint, subscribes to the selected condition using the same derived-only gateway credentials as the account reads, requires a current text or byte `PONG`, and performs account reads while the stream remains active.

The capture is bounded by a 15-second initial PONG deadline and market end. It rejects stream termination, stale generation or heartbeat, and account-read failure. It closes the in-memory store, stream and heartbeat tasks, and gateway on success, error, expiry, or cancellation. Returned evidence is immutable and secret-free; CLI and structured logs explicitly avoid describing PONG as an authentication or subscription acknowledgment.

## Behavior Before

Authenticated RDP preflight read account state but did not check the selected-market user stream. `UserStream` had no application-level PING/PONG support.

## Behavior After

`--preflight-only --account-check` reports account and user-channel evidence from one derived-only gateway session. Public-only preflight remains unauthenticated. Existing `UserStream` callers keep heartbeat disabled by default.

## Tests Added / Changed

Added fake-transport and fake-gateway coverage for endpoint and subscription scope, shared credentials, text/byte PONG, recurring cadence, JSON-PONG rejection, timeout, subscription rejection, send/receive/account failures, stale-generation rejection, disconnect during reads, market expiry, secret sanitization, isolated-store behavior, and repeated-cancellation cleanup. Updated preflight tests for the composed observer and CLI evidence.

## Tests Executed

- `uv run ruff check src/polymaker/rdp_account.py src/polymaker/userstream/client.py src/polymaker/rdp_userstream.py src/polymaker/rdp_live.py src/polymaker/cli.py tests/test_rdp_userstream.py tests/test_rdp_live.py`
- `uv run pytest -q tests/test_rdp_userstream.py tests/test_rdp_live.py tests/test_rdp_account.py tests/test_filltruth.py`
- `uv run pytest -q`
- `git diff --check`

## Test Results

- Scoped Ruff: passed.
- `git diff --check`: passed.
- Affected account, userstream, fill-truth, and preflight tests: **181 passed**.
- Full suite: **1,141 passed, 2 skipped**.
- Whole-repository `uv run ruff check` reported 153 findings in unrelated existing files and preserved research assets. No such files were changed.

## Regression Results

Standalone account-snapshot tests, existing userstream/fill-truth tests, RDP preflight tests, and the full suite passed on the final tree.

An initial combined regression run used stale fixtures that patched `account_snapshot` rather than the new composed-observer seam. The two parameter cases in `test_account_preflight_reports_startup_backing_without_rejecting_account_read` consequently reached external read-only endpoints before failing on unresolved spender metadata. Logs showed existing-key derivation with `derive_only=True`, GETs to CLOB `/time`, `/data/orders`, and `/version`, and the Data API `/v2/positions`; spender resolution failed before balance/allowance reads. The official user WebSocket also logged `user_ws_subscribed`; the test does not establish whether a PONG arrived before cleanup. No credential creation, order, cancellation, allowance update, or RPC action occurred. Two other stale fixtures failed on missing `market.tokens` before gateway construction and made no requests. The fixtures now mock the composed observer; subsequent test runs used fakes.

## Git Diff Summary

Five tracked files modified and two implementation/test files added. Preserved untracked research assets remain untouched. No commit or other Git history operation performed.

## Safety Checks

No order placement, cancellation, allowance update, or trading authority was added. The observer uses only the official Polymarket HTTP and WebSocket interfaces. Credentials do not appear in returned evidence, CLI output, or RDP diagnostics. The isolated store has no owned-order registry. PONG is reported as transport evidence only.

## Known Limitations

PONG does not prove server authentication or subscription acceptance. This bounded preflight does not establish complete order/fill reliability or implement reconnect recovery for a long-running RDP runtime.

## Deferred Findings

Long-lived runtime ownership, authoritative reconciliation after reconnect, and confirmed-fill integration remain deferred per the Architect Contract.

## Reviewer Attention

Review repeated-cancellation cleanup, current-generation checks immediately before capture, default-off behavior for existing callers, and the reported accidental read-only test-path contact.

## Human Live Trading Performed

NO
