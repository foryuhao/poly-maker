# ITER-0014 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved ITER-0014 contract: compose the existing RDP market-data, authenticated account/user-stream, and confirmed-fill components into one read-only single-market observation lifetime, with `--observe-only` CLI support.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `96071c399c1f4aca216324e5c30530855b57af68`
- `research_outputs/` was untracked and preserved.

## Existing Components Reused

- `RDPMarketDataService` and its selected-frame validation, book reconstruction, and dynamic tick handling.
- Existing bounded public readiness and authenticated account/user-channel capture.
- `ExecutionGateway` derive-only connection, account validation, and draining.
- `StateStore`, `UserEventProcessor`, and confirmed-fill accounting.
- Existing startup-backing evaluator and `rdp-live` CLI.

## Files Changed

- `src/polymaker/rdp_marketdata.py`
- `src/polymaker/rdp_userstream.py`
- `src/polymaker/rdp_runtime.py` (new)
- `src/polymaker/cli.py`
- `tests/test_rdp_marketdata.py`
- `tests/test_rdp_live.py`
- `tests/test_rdp_runtime.py` (new)

## Implementation Summary

Added retained public market-data and authenticated user-channel sessions. Existing bounded APIs delegate through those owners and retain their prior cleanup behavior.

Added `RDPInputSession` to own one selected BTC5m market’s public service, authenticated gateway/user stream, in-memory store, processor, and startup evidence. Startup requires current book and user-channel readiness plus eligible initial backing. The session pins both stream generations, exposes the same processor passed to `UserStream`, monitors health through market end, and drains resources on failure, cancellation, or normal termination.

Observation uses an RDP-local single-attempt public transport option so disconnects end the attempt instead of triggering the shared service’s automatic reconnect. The option defaults off, preserving bounded preflight behavior.

Added `rdp-live --observe-only`, mutually exclusive with `--preflight-only`. It reports read-only market-end termination and `real_money_trading_performed=false`.

## Behavior Before

Public and authenticated checks ran sequentially and closed their connections after startup evidence was captured. No runtime owner kept both input paths and their processor alive for one selected market.

## Behavior After

The observer opens one public feed and one derive-only authenticated user stream for the selected market, captures account/backing evidence while both inputs are owned, and retains both through the market interval. It fails on generation change, disconnect, fatal feed validation, stale heartbeat, or unexpected task termination. A healthy quiet market remains active until market end.

## Tests Added / Changed

- Added `tests/test_rdp_runtime.py` for retained ownership, startup backing, generation and health failures, quiet operation through market end, partial-start cleanup, repeated cancellation, and confirmed-fill projection.
- Added CLI tests for help, conflicting modes, and controlled observe-only market-end output.
- Added a public single-attempt regression; the default reconnect behavior remains disabled only for the observation runtime.
- The synthetic processor fixture verifies MATCHED evidence contributes no inventory and CONFIRMED evidence is counted.

## Tests Executed

- `uv run polymaker rdp-live --help`
- Focused suite:  
  `uv run pytest -q tests/test_rdp_runtime.py tests/test_rdp_marketdata.py tests/test_rdp_userstream.py tests/test_rdp_live.py`
- Expanded affected suite, network-blocked, using this exact command:

```bash
uv run python - <<'PY'
import socket, sys, pytest
attempts = []

def deny(name):
    def blocked(*_args, **_kwargs):
        attempts.append(name)
        raise AssertionError(f"network blocked: {name}")
    return blocked

socket.socket.connect = deny("socket.connect")
socket.socket.connect_ex = deny("socket.connect_ex")
socket.getaddrinfo = deny("socket.getaddrinfo")
files = [
    "tests/test_rdp_accounting.py", "tests/test_rdp_quotes.py",
    "tests/test_rdp_lifecycle.py", "tests/test_rdp_capital.py",
    "tests/test_rdp_book_filter.py", "tests/test_rdp_marketdata.py",
    "tests/test_rdp_account.py", "tests/test_rdp_live.py",
    "tests/test_rdp_userstream.py", "tests/test_rdp_runtime.py",
    "tests/test_userstream_parse.py", "tests/test_filltruth.py",
    "tests/test_gateway_positions_v2.py", "tests/test_execution.py",
]
status = pytest.main(["-q", *files])
print(f"NETWORK_ATTEMPTS={len(attempts)}")
if attempts:
    print(f"NETWORK_ATTEMPT_METHODS={attempts}")
sys.exit(int(status) or bool(attempts))
PY
```

- Final full suite, network-blocked, using this exact command after the final CLI wording and configuration-retention edits:

```bash
uv run python - <<'PY'
import socket, sys, pytest
attempts = []

def deny(name):
    def blocked(*_args, **_kwargs):
        attempts.append(name)
        raise AssertionError(f"network blocked: {name}")
    return blocked

socket.socket.connect = deny("socket.connect")
socket.socket.connect_ex = deny("socket.connect_ex")
socket.getaddrinfo = deny("socket.getaddrinfo")
status = pytest.main(["-q"])
print(f"NETWORK_ATTEMPTS={len(attempts)}")
if attempts:
    print(f"NETWORK_ATTEMPT_METHODS={attempts}")
sys.exit(int(status) or bool(attempts))
PY
```

- Scoped Ruff:

```bash
uv run ruff check src/polymaker/rdp_runtime.py src/polymaker/rdp_marketdata.py src/polymaker/rdp_userstream.py src/polymaker/cli.py tests/test_rdp_runtime.py tests/test_rdp_marketdata.py tests/test_rdp_live.py
```

- `git diff --check`
- Inspected `git status --short`, `git ls-files --others --exclude-standard`, and `git diff --stat`.

## Test Results

- Focused runtime/market-data/userstream/CLI suite: **128 passed**.
- Expanded affected RDP/account/userstream/execution suite: **679 passed; 0 network attempts**.
- Final full suite after the final edits: **1,165 passed, 2 skipped; 0 network attempts**.
- CLI help: passed and displayed `--observe-only`.
- Scoped Ruff: passed.
- `git diff --check`: passed.

An intermediate implementation run caught cleanup methods misplaced outside `RDPMarketDataSession`; that was corrected and the final full suite passed. A CLI test assertion was adjusted for Typer’s wrapped output.

## Regression Results

The existing bounded preflight and authenticated account/user-channel APIs passed their regressions. Shared transport defaults remain unchanged. The final full suite passed with socket and DNS operations blocked.

## Git Diff Summary

The tracked diff reports **258 insertions and 36 deletions** across five tracked files; this excludes the new runtime module and runtime test file. No commits, pushes, or other Git history operations were performed.

`research_outputs/` remains preserved and unmodified.

## Safety Checks

- Final full and affected controlled test runs recorded zero socket or DNS attempts.
- The runtime uses the existing derive-only authenticated account path.
- No order signing, submission, cancellation, allowance update, credential creation, or order adoption was added.
- The observer uses an isolated in-memory store and leaves configured project state databases untouched.
- Credentials are not added to observation evidence, logs, or CLI output.
- No real-money trading was performed.

## Known Limitations

This is read-only input observation, not trading readiness. Startup account/backing evidence is a timestamped snapshot and is not continuously refreshed. A responsive user stream does not prove subscription acceptance or reliable order/fill delivery.

## Deferred Findings

Automatic reconnect recovery and authoritative account refresh remain outstanding. Order registration, placement capture, reconciliation, FillWindow, NORMAL/REBALANCE execution, STOP policy, and full Phase 0 metrics remain unintegrated.

## Reviewer Attention

Please verify the retained public and authenticated session lifetimes, preflight API compatibility, single-attempt observation behavior, and cancellation cleanup. The documented CLI path was exercised through controlled fixtures; no real account or network calls were made.

## Human Live Trading Performed

NO
