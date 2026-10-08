# ITER-0021 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented Revision 01 in `docs/development/ITER-0021/06_ARCHITECT_REVISION_01.md`: recapture market-status freshness for terminal admission rejections, preserve the primary rejection reason, and sanitize RDP Gamma failure diagnostics through a default-off extension to the existing Gamma client.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD and `origin/rdp-phase0-dev`: `6d8f6532c82de296e68d79ce3d9613fb883a0e87`
- Preserved untracked `research_outputs/` assets remain untouched.
- No Git history or audit-file operation was performed.

## Existing Components Reused

- Existing Gamma client, exact-slug lookup, parser, and HTTPX transports.
- Existing market-status Decimal freshness evaluator and admission rejection factory.
- Existing fake HTTP, respx, structured logging, and retained-session cancellation tests.
- Existing RDP numeric metadata precheck and shared market validator.

## Files Changed

The seven source/test files in the current ITER-0021 tree:

| File | SHA-256 |
|---|---|
| `src/polymaker/catalog/gamma.py` | `f0ee4707b9a0c0453a0e44ceb2ad236a81e709359ed0757e0524a1ea37af31bc` |
| `src/polymaker/rdp_live.py` | `a3e64fd5fd5ff00c2fe3495e34de53596cf02a56bab642effeeedebffdcbe0cd` |
| `src/polymaker/rdp_runtime.py` | `13710a108668157cc74edbe05b1876260cf4cfbcfe8fd5d7891a17391e6bf6b8` |
| `tests/test_btc5m.py` | `74a3547ab31a650e34a13d337537082256e08decbec795e2dbbb73e554e70283` |
| `tests/test_rdp_live.py` | `7b8a9aed291ccc11d1a5e32a2a4d56b19b3e474d6c9c2d9e8c10f29a857e7d25` |
| `tests/test_rdp_placement.py` | `51edf5901489a3bb4bfc31fef10632bd551f562919978e7ed4e7fd7b7c04d6ef` |
| `tests/test_rdp_runtime.py` | `7d04de533142c8117ad020f0ae96def3e5dcedccc06eaa6a25562295b8132a44` |

## Implementation Summary

- Terminal rejected admissions recalculate retained status age at rejection capture. They preserve the primary account, input, or precondition reason.
- Invalid, future, stale, or post-expiry status cannot clear `CURRENT_MARKET_TRADABILITY`. Fresh status may clear its own gate when another admission prerequisite rejects.
- `GammaClient` now has a default-off `safe_diagnostics` option. It wraps the existing default and mounted transports, preserves mount keys and `None` mounts, and delegates close idempotently. The adapter changes only the response’s diagnostic reason phrase before HTTPX logs it.
- Only `read_rdp_market_status()` enables this option. That path logs exception types instead of exception text. Other Gamma callers retain their default behavior and diagnostics.
- The existing numeric metadata precheck and shared parser conversions were not changed.

## Behavior Before

A failed account read could return the right rejection reason while reporting an earlier status age and clearing the tradability gate. HTTPX and Gamma logs could include an untrusted upstream 503 reason phrase.

## Behavior After

Rejected admission evidence reflects freshness at capture time while preserving the primary rejection reason. RDP status-read logs omit untrusted reason phrases. Default Gamma callers retain legacy failure diagnostics.

## Tests Added / Changed

- Added delayed categorized and unexpected account-read failures that check stale, future, and invalid status timestamps at rejection.
- Added gate assertions for post-await health, generation, closure, and market-expiry failures.
- Added fake HTTP 503 tests under default console and configured durable JSON logging. They check that the reason-phrase sentinel is absent while response status, headers, body, and exact lookup request remain unchanged.
- Added a legacy Gamma test that checks its default exact-slug failure behavior and diagnostics.
- Existing malformed numeric sentinel, redirect, fractional-boundary, and cancellation/cleanup tests remain covered.

## Tests Executed

Each pytest invocation used this DNS/socket guard, installed before importing pytest:

```python
import socket
attempts = []

def deny(label):
    def blocked(*args, **kwargs):
        attempts.append(label)
        raise AssertionError(f"network blocked by ITER-0021 guard: {label}")
    return blocked

socket.getaddrinfo = deny("getaddrinfo")
socket.create_connection = deny("create_connection")
socket.socket.connect = deny("socket.connect")
socket.socket.connect_ex = deny("socket.connect_ex")

import pytest
code = pytest.main(PYTEST_ARGUMENTS)
print(f"NETWORK_ATTEMPTS={len(attempts)} categories={attempts}")
raise SystemExit(2 if attempts else code)
```

The final targeted repair invocation used:

```python
paths = [
    "tests/test_rdp_live.py::test_status_http_failure_sanitizes_upstream_reason_phrase",
    "tests/test_rdp_live.py::test_malformed_status_metadata_never_logs_raw_gamma_value",
    "tests/test_rdp_placement.py::test_terminal_account_read_failure_recaptures_market_status_freshness",
    "tests/test_rdp_placement.py::test_real_gamma_http_read_drains_and_closes_before_repeated_cancellation_returns",
    "tests/test_rdp_placement.py::test_status_await_rechecks_health_generation_expiry_and_closure",
    "tests/test_btc5m.py::test_gamma_slug_lookup_default_retains_legacy_failure_diagnostics",
]
code = pytest.main(["-q", *paths])
```

Result: **21 passed; 0 network attempts**.

After the final legacy-test assertion adjustment, its exact invocation was:

```python
code = pytest.main([
    "-q",
    "tests/test_btc5m.py::test_gamma_slug_lookup_default_retains_legacy_failure_diagnostics",
])
```

Result: **1 passed in 0.07s; 0 network attempts**.

The affected Gamma/RDP/execution group used:

```python
paths = [
    "tests/test_btc5m.py", "tests/test_catalog.py", "tests/test_rdp_account.py",
    "tests/test_rdp_accounting.py", "tests/test_rdp_book_filter.py", "tests/test_rdp_capital.py",
    "tests/test_rdp_lifecycle.py", "tests/test_rdp_live.py", "tests/test_rdp_marketdata.py",
    "tests/test_rdp_placement.py", "tests/test_rdp_quotes.py", "tests/test_rdp_runtime.py",
    "tests/test_rdp_userstream.py", "tests/test_execution.py",
]
code = pytest.main(["-q", *paths])
```

Result: **837 passed in 68.08s; 0 network attempts**. This run preceded the final assertion-only adjustment to the legacy diagnostics test.

The FL regression group used:

```python
paths = [
    "tests/test_favorite_first.py",
    "tests/test_favorite_first_review.py",
    "tests/test_favorite_live.py",
]
code = pytest.main(["-q", *paths])
```

Result: **92 passed in 0.60s; 0 network attempts**. This run also preceded that assertion-only adjustment.

The final full-suite invocation, after the final test adjustment, used:

```python
code = pytest.main(["-q"])
```

Result: **1,441 passed, 2 skipped in 76.94s; 0 network attempts**.

Final Ruff command:

```bash
uv run ruff check src/polymaker/catalog/gamma.py src/polymaker/rdp_live.py src/polymaker/rdp_runtime.py tests/test_btc5m.py tests/test_rdp_live.py tests/test_rdp_placement.py
```

It passed. `git diff --check` passed.

## Test Results

The final full suite passed under the DNS/socket guard. The targeted repair case also passed after its final assertion adjustment. The broader affected and FL groups passed before that assertion-only test change; the final full suite includes it.

During development, Ruff first found a missing `respx` import and an import-order issue; both were fixed. A test logger setup that used an incompatible raw `PrintLogger` was replaced with the application logging configuration. The legacy diagnostic assertion was adjusted to inspect both pytest-captured stdlib logs and console output.

## Regression Results

The FavoriteFirst regression set passed: **92 passed**. RDP status reads alone opt into safe diagnostics; legacy Gamma callers retain their existing behavior.

## Git Diff Summary

The seven implementation/test files show **1,215 insertions and 35 deletions** relative to HEAD. The only untracked files are 748 preserved files under `research_outputs/`. HEAD and `origin/rdp-phase0-dev` remain at `6d8f6532c82de296e68d79ce3d9613fb883a0e87`. No commit or push was performed.

## Safety Checks

- Pytest DNS/socket guards recorded zero attempts in every run.
- Tests used fake HTTP transports; no live-market request was performed.
- No order creation, cancellation, signing, or coordinator activation was added.
- Admission remains read-only and execution authorization remains false.

## Known Limitations

Market-status evidence is valid only for its capture and freshness interval. Final submission revalidation and the remaining single-market RDP runtime are unfinished; this work does not establish MICRO_LIVE_READY.

## Deferred Findings

Final submission revalidation, subsequent NORMAL/REBALANCE operation, complete reconciliation, and the remaining delivery requirements are outside this revision.

## Reviewer Attention

Verify terminal status age and gate state after failed awaits, especially invalid timestamps and market expiry. Inspect the actual HTTPX and durable log output for the 503 sentinel, default Gamma compatibility, transport routing, and cleanup.

## Human Live Trading Performed

NO
