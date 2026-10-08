# ITER-0019 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved ITER-0019 contract: retain original confirmed-fill receipt metadata and expose a scoped, read-only FillWindow decision for the current completed initial NORMAL pair. No strategy semantics, submission, cancellation, or reconciliation behavior was changed.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `aa0208c67ec53b0ec2aea00f1ab60959eaa4169f`
- Final working tree remains uncommitted for Orchestrator archival.
- Preserved the pre-existing untracked `research_outputs/` assets.

## Existing Components Reused

Reused `UserStream` receipt handling, `UserEventProcessor` attribution and replay, `RDPPlacementOwner` acknowledged-pair scope, existing session health checks, and `snapshot_inventory`. Receipt metadata supplements processor evidence; it does not alter fill admission or accounting.

## Files Changed

- `src/polymaker/userstream/client.py`
- `src/polymaker/state/tracker.py`
- `src/polymaker/rdp_userstream.py`
- `src/polymaker/rdp_runtime.py`
- `src/polymaker/execution/rdp_fill_window.py` — new
- `tests/test_rdp_placement.py`
- `tests/test_rdp_userstream.py`

## Implementation Summary

Added default-off receipt capture to `UserStream` and default-off first-confirmation receipt retention to `UserEventProcessor`. RDP enables both independently of research. The processor preserves the first validated confirmation receipt—including missing timing—through existing pending-event replay, so a later duplicate cannot replace it.

Added an immutable FillWindow decision and synchronous `RDPInputSession.initial_fill_window()` query. It requires the current fully acknowledged pair, accepted `CONFIRMED` evidence, valid original WebSocket receipt timing, and reliable session health and generations. It derives inventory from `snapshot_inventory`, starts at the earliest accepted receipt across the pair, and reports expiry at the exact deadline. The query makes no gateway, account, order, cancellation, or reconciliation calls.

## Behavior Before

Production RDP did not capture frame receipts unless research was enabled. The retained initial placement had no decision based on the original confirmed-fill receipt.

## Behavior After

RDP captures original frame receipt time and generation without enabling research. A valid accepted confirmation starts the configured FillWindow; the default is 3 seconds. Queries fail closed on missing or invalid timing, REST-only recovery, unsafe placement scope, unhealthy inputs, generation changes, session closure, or market expiry. Later fills do not extend the deadline, and full fills do not trigger early closure.

## Tests Added / Changed

Added coverage for default-off behavior and RDP opt-in; receipt cleanup after data, control, and parser-failure paths; lifecycle ordering; original receipt retention through both-leg pre-ack replay with reversed acknowledgment order; missing receipt non-repair; REST-only, malformed, future, and wrong-generation timing; default and configured durations; exact deadline; full-pair fills; unhealthy scope; and zero account reads from window decisions.

## Tests Executed

- `uv run pytest -q tests/test_rdp_placement.py -k 'fill_window or preack_confirmations' --tb=short` — **22 passed, 76 deselected**
- `uv run pytest -q tests/test_rdp_placement.py -k 'configured_duration' --tb=short` — **1 passed, 98 deselected**
- `uv run pytest -q tests/test_rdp_userstream.py -k 'receipt_context or receipt_capture' --tb=short` — **4 passed, 21 deselected**
- `uv run pytest -q tests/test_userstream_parse.py tests/test_rdp_userstream.py tests/test_rdp_runtime.py tests/test_rdp_placement.py tests/test_rdp_accounting.py tests/test_rdp_capital.py tests/test_rdp_lifecycle.py tests/test_engine.py` — **385 passed**
- `PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block` — **1,344 passed, 2 skipped**; `RDP_NETWORK_GUARD_ATTEMPTS=0 names=[]`
- Scoped `uv run ruff check` on all seven changed source/test paths — **passed**
- `git diff --check` — **passed**

The network guard blocked and counted `socket.connect`, `socket.connect_ex`, `socket.create_connection`, and `socket.getaddrinfo`; it failed the run if any attempt occurred.

## Test Results

Final guarded full suite passed. No network attempts were recorded.

Early test runs exposed issues that were corrected: a missing accounting import, a test fake without account-call counters, a reversed-ack test fixture with mismatched token attribution, and a receipt-cleanup test that needed to expect the sanitized stream error. The first combined targeted run was interrupted while a fake WebSocket waited for queued frames; the fixture was corrected. These failures are not present in the final results.

## Regression Results

The affected suite included user-stream parsing, RDP userstream/runtime/placement/accounting/capital/lifecycle, and the FL engine regression tests. All passed. Receipt metadata remains opt-in for existing callers.

## Git Diff Summary

Six tracked files changed; one new 74-line FillWindow module was added. The tracked diff reports 635 insertions and 9 deletions. `research_outputs/` remains unmodified and untracked.

## Safety Checks

- No real-money trading, orders, cancellations, or account actions were performed.
- The FillWindow query has no execution or gateway authority.
- The final full suite ran with DNS/socket access blocked and zero attempted network calls.
- No Git history operations were performed.

## Known Limitations

This iteration reports FillWindow state only. It does not automatically close the cycle or perform the later cancel/reconcile barrier; those remain separate required operations.

## Deferred Findings

Production submission, automatic cycle closure, subsequent NORMAL/REBALANCE operation, unknown-placement recovery, and broader metrics remain deferred as specified by the contract.

## Reviewer Attention

Please verify that first-receipt retention and fail-closed timing checks remain metadata-only, that the RDP opt-in leaves default stream and processor behavior unchanged, and that expiry does not release the existing placement close barrier.

## Human Live Trading Performed

NONE
