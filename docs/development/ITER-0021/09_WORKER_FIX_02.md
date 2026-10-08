# ITER-0021 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented only MF-04 from `docs/development/ITER-0021/08_REVIEWER_REVISION_01.md`, within the approved scope of `docs/development/ITER-0021/06_ARCHITECT_REVISION_01.md`.

The opted-in Gamma diagnostic now captures the exception class inside the handler and emits the warning after leaving the raw HTTP exception context. Default Gamma diagnostics and return behavior remain unchanged. Added a regression using a genuinely failing `logging.StreamHandler` and sentinel-bearing `RemoteProtocolError`.

## Base Repository State

- Repository: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `6d8f6532c82de296e68d79ce3d9613fb883a0e87`
- Working tree already contained ITER-0021 implementation changes, Orchestrator audit/index changes, and untracked `research_outputs/` assets. These were preserved.
- No commit, push, Git history operation, or audit-file write was performed.

## Existing Components Reused

- Existing `GammaClient.market_by_slug()` and its default-off safe diagnostics option.
- Existing RDP exact-market status helper and client context cleanup.
- Existing logging configuration and HTTPX/respx fake transport tests.

## Files Changed

This repair modified only:

- `src/polymaker/catalog/gamma.py`
- `tests/test_rdp_live.py`

Final hashes for the seven reviewed implementation/test files:

| File | SHA-256 |
|---|---|
| `src/polymaker/catalog/gamma.py` | `f3ff398bd358b5a785ff1e7a89d425157e0b5d51470c1e7fe7078806a9047741` |
| `src/polymaker/rdp_live.py` | `a3e64fd5fd5ff00c2fe3495e34de53596cf02a56bab642effeeedebffdcbe0cd` |
| `src/polymaker/rdp_runtime.py` | `13710a108668157cc74edbe05b1876260cf4cfbcfe8fd5d7891a17391e6bf6b8` |
| `tests/test_btc5m.py` | `74a3547ab31a650e34a13d337537082256e08decbec795e2dbbb73e554e70283` |
| `tests/test_rdp_live.py` | `ad087f4df1125147b176a8738f7002f6ac3f1e4841cff49f088e5281c2b55c71` |
| `tests/test_rdp_placement.py` | `51edf5901489a3bb4bfc31fef10632bd551f562919978e7ed4e7fd7b7c04d6ef` |
| `tests/test_rdp_runtime.py` | `7d04de533142c8117ad020f0ae96def3e5dcedccc06eaa6a25562295b8132a44` |

## Implementation Summary

The safe diagnostics branch now stores `type(exc).__name__` while catching the request error, then emits its categorical warning after the `except` block. This prevents a logging handler’s own exception traceback from chaining the original upstream exception. The legacy branch still logs `str(exc)` and returns `None` as before.

The regression uses an actual `logging.StreamHandler` whose stream raises `OSError`, triggers the RDP status helper with a sentinel-bearing `httpx.RemoteProtocolError`, inspects real stderr and the categorical log output, and verifies the HTTP client closes.

## Behavior Before

When an opted-in diagnostic handler failed while the upstream HTTP exception was active, Python could include that upstream exception and its untrusted text in the handler-failure traceback.

## Behavior After

The handler-failure traceback has no upstream exception context. The categorical `market_by_slug_failed` warning remains observable through a functioning handler, and the RDP status lookup still fails closed and closes its client.

## Tests Added / Changed

Added `test_status_safe_diagnostic_handler_failure_does_not_disclose_http_exception` in `tests/test_rdp_live.py`. It asserts:

- the actual failing handler emits its standard logging-error report;
- real stderr and captured diagnostic output omit the sentinel;
- the categorical warning includes `RemoteProtocolError`;
- the lookup remains a `MARKET_STATUS_LOOKUP_FAILED`;
- the Gamma request occurs once and its `AsyncClient` closes once.

## Tests Executed

Every pytest invocation used the same guard, installed before importing pytest:

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

Narrow new regression:

```python
pytest.main([
    "-q",
    "tests/test_rdp_live.py::test_status_safe_diagnostic_handler_failure_does_not_disclose_http_exception",
])
```

Repair-focused group:

```python
pytest.main(["-q",
    "tests/test_rdp_live.py::test_status_http_failure_sanitizes_upstream_reason_phrase",
    "tests/test_rdp_live.py::test_status_safe_diagnostic_handler_failure_does_not_disclose_http_exception",
    "tests/test_rdp_live.py::test_malformed_status_metadata_never_logs_raw_gamma_value",
    "tests/test_rdp_placement.py::test_terminal_account_read_failure_recaptures_market_status_freshness",
    "tests/test_rdp_placement.py::test_real_gamma_http_read_drains_and_closes_before_repeated_cancellation_returns",
    "tests/test_rdp_placement.py::test_status_await_rechecks_health_generation_expiry_and_closure",
    "tests/test_btc5m.py::test_gamma_slug_lookup_default_retains_legacy_failure_diagnostics",
])
```

Affected Gamma/RDP/execution group:

```python
pytest.main(["-q",
    "tests/test_btc5m.py", "tests/test_catalog.py", "tests/test_rdp_account.py",
    "tests/test_rdp_accounting.py", "tests/test_rdp_book_filter.py", "tests/test_rdp_capital.py",
    "tests/test_rdp_lifecycle.py", "tests/test_rdp_live.py", "tests/test_rdp_marketdata.py",
    "tests/test_rdp_placement.py", "tests/test_rdp_quotes.py", "tests/test_rdp_runtime.py",
    "tests/test_rdp_userstream.py", "tests/test_execution.py",
])
```

FL regression group:

```python
pytest.main(["-q",
    "tests/test_favorite_first.py",
    "tests/test_favorite_first_review.py",
    "tests/test_favorite_live.py",
])
```

Final full suite:

```python
pytest.main(["-q"])
```

Scoped Ruff and whitespace checks:

```bash
uv run ruff check src/polymaker/catalog/gamma.py src/polymaker/rdp_live.py src/polymaker/rdp_runtime.py tests/test_btc5m.py tests/test_rdp_live.py tests/test_rdp_placement.py tests/test_rdp_runtime.py
git diff --check
```

## Test Results

- New regression: **1 passed**; zero network attempts.
- Repair-focused group: **22 passed**; zero network attempts.
- Affected Gamma/RDP/execution group: **838 passed**; zero network attempts.
- FL regression group: **92 passed**; zero network attempts.
- Ruff: **passed**.
- `git diff --check`: **passed**.
- Final full suite: **1,442 passed, 2 skipped in 77.49s**; zero network attempts.

## Regression Results

The reviewed freshness, legacy Gamma diagnostics, HTTP503 reason-phrase sanitization, retained-client cancellation/cleanup, and FL tests passed in the guarded runs. No live HTTP, DNS, or socket action occurred.

## Git Diff Summary

The complete current-tree diff includes prior ITER-0021 and Orchestrator work. For this repair, only the Gamma diagnostic handling and its RDP live test changed; the scoped two-file diff is 309 insertions and 3 deletions. No Git archival action was taken.

## Safety Checks

- No production global logger policy, logger levels, filters, or monkeypatches were added.
- The test’s failing handler and `AsyncClient.aclose` instrumentation are local to the test and restored.
- The existing RDP read-only path remains fail-closed.
- No orders, cancellations, or live calls were made.

## Known Limitations

If every configured diagnostic handler fails, the categorical warning may not be durably recorded. The handler’s standard failure report remains, while the upstream exception text is not exposed through its exception context.

## Deferred Findings

No prior closed findings were reopened. Remaining project delivery gaps are unchanged and outside MF-04.

## Reviewer Attention

Please verify the safe warning is emitted after leaving the exception handler and inspect the regression’s captured stderr, categorical output, and client-close assertion.

## Human Live Trading Performed

NO
