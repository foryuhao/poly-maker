# ITER-0021 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved contract in `docs/development/ITER-0021/01_ARCHITECT.md`: obtain fresh official Gamma tradability evidence for each initial NORMAL admission and bind it to the retained market identity and existing freshness, health, and account checks.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `9e250350188abb34c69a566e142ea8e4de178fed`
- The contract document lists a different planning HEAD (`dad76b7…`); implementation used the actual repository HEAD above.
- Initial worktree contained only preserved untracked `research_outputs/`.

## Existing Components Reused

- Official `GammaClient.market_by_slug()` and its HTTP client/context cleanup.
- Existing `_validate_market()` and official-origin validation.
- Retained bootstrap identity, admission serialization, account-read ownership, task draining, and admission precondition checks.
- Existing immutable `RDPInitialNormalAdmission`.

## Files Changed

- `src/polymaker/rdp_live.py`
- `src/polymaker/rdp_runtime.py`
- `tests/test_rdp_live.py`
- `tests/test_rdp_runtime.py`
- `tests/test_rdp_placement.py`

No files outside the approved scope changed.

## Implementation Summary

Added immutable `RDPMarketStatusEvidence` and a fresh-read helper using the existing exact-slug Gamma client and strict market validator. It checks the response against the retained slug, condition, ordered tokens/outcomes, neg-risk value, and BTC5m end time.

`RDPInputSession.open()` now snapshots and validates the official Gamma origin and `risk.ws_stale_halt_s` from the copied configuration before its first await. Each admission owns and drains a fresh Gamma read, checks session health, generation, and expiry after the read, then rechecks both Gamma and account freshness immediately before the final synchronous decision.

Eligible admissions contain the validated status and its evaluated age and limit. Only `CURRENT_MARKET_TRADABILITY` is cleared; reconciliation, final-submit, and human-authorization gates remain. The change grants no execution authority.

## Behavior Before

Startup Gamma validation was the only market tradability evidence. An initial admission did not refresh market status.

## Behavior After

Every initial admission performs a fresh exact-market Gamma GET. Missing, malformed, failed, mismatched, future-dated, expired, or stale status fails closed without cached fallback. The configured origin and age limit cannot be changed by later mutation of the caller’s config.

## Tests Added / Changed

- `test_rdp_live.py`: exact Gamma GET, strict status booleans, changed identity, missing market, HTTP failure, and redirect without alternate-origin fallback.
- `test_rdp_runtime.py`: pre-await configuration snapshot, unapproved-origin rejection, and existing observer scenarios using explicit typed status fakes.
- `test_rdp_placement.py`: actual initial admission over respx-backed `GammaClient`, repeated fresh reads, age boundaries and invalid receipts, joint status/account freshness, health/generation/expiry/closure checks, and repeated cancellation/teardown with actual HTTP-client cleanup.
- Existing synthetic admission fixtures now provide explicit status fakes.

## Tests Executed

All final test commands used a temporary global DNS/socket blocker at `/tmp/rdp_network_block.py`. Its contents were:

```python
import socket

attempts = []
_orig = {}

def _blocked(label):
    def deny(*args, **kwargs):
        attempts.append(label)
        raise AssertionError(f"network blocked by rdp_network_block: {label}")
    return deny

def pytest_sessionstart(session):
    _orig["connect"] = socket.socket.connect
    _orig["connect_ex"] = socket.socket.connect_ex
    _orig["getaddrinfo"] = socket.getaddrinfo
    _orig["create_connection"] = socket.create_connection
    socket.socket.connect = _blocked("socket.connect")
    socket.socket.connect_ex = _blocked("socket.connect_ex")
    socket.getaddrinfo = _blocked("socket.getaddrinfo")
    socket.create_connection = _blocked("socket.create_connection")

def pytest_sessionfinish(session, exitstatus):
    print(f"RDP_NETWORK_BLOCK attempts={len(attempts)} categories={attempts}")
    for key, value in _orig.items():
        if key in ("connect", "connect_ex"):
            setattr(socket.socket, key, value)
        else:
            setattr(socket, key, value)
    if attempts:
        session.exitstatus = 1
```

Final-tree targeted tests:

```bash
PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block \
  tests/test_rdp_live.py tests/test_rdp_runtime.py tests/test_rdp_placement.py
```

Result: **299 passed in 65.43s; 0 DNS/socket attempts.**

Affected RDP account, accounting, book-filter, capital, lifecycle, market-data, quotes, user-stream, and FL FavoriteFirst regressions:

```bash
PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block \
  tests/test_rdp_account.py tests/test_rdp_accounting.py \
  tests/test_rdp_book_filter.py tests/test_rdp_capital.py \
  tests/test_rdp_lifecycle.py tests/test_rdp_marketdata.py \
  tests/test_rdp_quotes.py tests/test_rdp_userstream.py \
  tests/test_favorite_first.py tests/test_favorite_first_review.py \
  tests/test_favorite_live.py
```

Result: **484 passed in 3.27s; 0 DNS/socket attempts.**

Final full suite:

```bash
PYTHONPATH=/tmp uv run pytest -q -p rdp_network_block
```

Result: **1,422 passed, 2 skipped in 76.85s; 0 DNS/socket attempts.**

Final lint and whitespace checks:

```bash
uv run ruff check src/polymaker/rdp_live.py src/polymaker/rdp_runtime.py \
  tests/test_rdp_live.py tests/test_rdp_runtime.py tests/test_rdp_placement.py
git diff --check
```

Both passed.

## Test Results

The final tree passes the targeted, affected, and full suites above.

During development, two status-matrix expectations were corrected to match the existing exact lookup/parser categories. A config-snapshot fixture was changed to use an actually empty processor. A joint-freshness failure led to reevaluating Gamma freshness immediately after account-read completion, including when account backing itself is stale. An expiry fixture was updated to accept the existing `MARKET_EXPIRED` category. A nonfinite receipt is categorized as `INVALID_MARKET_STATUS_RECEIPT`.

One initial combined runtime/placement run was started without the global blocker. It was interrupted before completion after the placement fixture’s local guard reported a blocked `getaddrinfo` attempt toward the default official Gamma `/markets?slug=…&limit=1` request. One attempt was observed; the total is unknown because the run was stopped at about 32%. DNS was blocked, so no HTTP request reached the service; status-first admission short-circuited before account reads. No order or cancellation calls occurred. All subsequent test commands used the global blocker and recorded zero attempts.

## Regression Results

Final full-suite and scoped FavoriteFirst regressions passed. The status read uses the existing Gamma client; no shared Gamma behavior or FL runtime behavior was changed.

## Git Diff Summary

- 5 approved files changed.
- `git diff --stat`: 827 insertions, 32 deletions.
- Final status: only those five tracked files modified; 748 untracked files remain under `research_outputs/`; zero non-research untracked files.
- No commit, push, or other Git history operation performed.

Final file SHA-256 values:

```text
f1d50ef0e4445fccc2be81be4676046029d3f343285debae41f1e6665471bcf7  src/polymaker/rdp_live.py
f750e5ebd7a0e600d15fea7b9e4c983e9849683fdefd6dc5b42e63ab004df63a  src/polymaker/rdp_runtime.py
966ddad5d495713314d93b9c6094ab6fd30ccc9435f7c030384e0b1a955fc25c  tests/test_rdp_live.py
7d04de533142c8117ad020f0ae96def3e5dcedccc06eaa6a25562295b8132a44  tests/test_rdp_runtime.py
e76dead3b0bac5aa3fb76f0bf3dddd628c0cc88edf5a5ea5dddd93057dda6761  tests/test_rdp_placement.py
```

## Safety Checks

- All fresh status reads use the validated official Gamma origin.
- Redirects do not reach alternate origins.
- No create, cancel, placement, or coordinator call was added.
- Cancellation and teardown drain the status task and close the Gamma client before releasing admission ownership.
- `execution_authorized` remains fixed false.
- No real-market call or real-money action was performed.

## Known Limitations

Gamma evidence certifies only the admission capture and its freshness window. Final submission must still revalidate execution conditions.

## Deferred Findings

Final submission revalidation and subsequent RDP runtime behavior remain outside this iteration’s scope.

## Reviewer Attention

Please verify strict agreement between Gamma response identity and retained bootstrap identity, final joint status/account freshness, and status-task cleanup on repeated cancellation.

## Human Live Trading Performed

NO
