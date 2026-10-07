# ITER-0011 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the optional read-only account snapshot and default-off derive-only gateway connection. Account evidence is separate from public feed readiness and does not authorize capital admission, inventory import, order adoption, or trading.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- Base HEAD: `550ff9d5864d379b8d32e604871c21011d0b443e`
- Preserved untracked `research_outputs/` assets were left untouched.

## Existing Components Reused

- `ExecutionGateway` client construction, identity, thread pool, authenticated open-order and collateral reads, official Data API positions, and required-spender resolution.
- SDK `derive_api_key()` for GET-only existing credential derivation.
- Existing RDP HTTP/WebSocket preflight and CLI logging.

## Files Changed

- `src/polymaker/execution/gateway.py`
- `src/polymaker/rdp_account.py` — new immutable account snapshot wrapper.
- `src/polymaker/rdp_live.py`
- `src/polymaker/cli.py`
- `tests/test_rdp_account.py` — new account and gateway tests.
- `tests/test_rdp_live.py`

## Implementation Summary

Added keyword-only `derive_only=False` to `ExecutionGateway.connect()`. The default path still calls `create_or_derive_api_key()`; derive-only calls `derive_api_key()` exclusively and fails if credentials are incomplete. In derive-only mode, gateway read-error logs include exception types rather than raw exception text.

The account reader copies configuration before awaits, validates wallet identity, signature type, and official CLOB/Data API origins, then uses existing gateway reads. It validates collateral and required-spender evidence using exact `Decimal` six-decimal conversion, preserves selected positions and orders without mutation, records separate read timestamps, and closes the gateway on success, failure, expiry, or cancellation.

The CLI adds opt-in `--account-check`. Without it, the public-only preflight path remains unchanged. With it, HTTP/feed readiness and account-read evidence are logged separately.

## Behavior Before

RDP preflight had no authenticated account-read path. Gateway connection used the SDK method that may create an API key before deriving one.

## Behavior After

`rdp-live --market <slug> --preflight-only --account-check` derives existing credentials only, validates configured identity and official endpoints, and reports a scoped, immutable account snapshot. Existing orders and positions remain untouched. Failures are sanitized and fail closed.

## Tests Added / Changed

Tests cover default versus derive-only connection; derivation failure and incomplete credentials without creation fallback; identity and origin rejection; configuration mutation; exact monetary conversion; invalid balance, allowance, and spender evidence; selected existing positions/orders; authoritative read failures; expiry; cancellation cleanup; CLI opt-in success/failure logging; and help output.

## Tests Executed

- `uv run ruff check src/polymaker/execution/gateway.py src/polymaker/rdp_account.py src/polymaker/rdp_live.py src/polymaker/cli.py tests/test_rdp_account.py tests/test_rdp_live.py`
- `uv run pytest -q tests/test_rdp_account.py tests/test_rdp_live.py tests/test_execution.py tests/test_gateway_positions_v2.py tests/test_favorite_live.py tests/test_config_aliases.py`
- `uv run pytest -q tests/test_favorite_live.py tests/test_config_aliases.py tests/test_settlement_sweep.py`
- `uv run pytest -q`
- `uv run polymaker rdp-live --help`
- `git diff --check`

## Test Results

- Ruff: passed.
- Focused account, CLI, gateway, positions, FavoriteFirst, and config tests: **283 passed**.
- Wallet/config/settlement regression command: **120 passed**.
- Full suite: **1071 passed, 2 skipped**.
- CLI help and `git diff --check`: passed.

## Regression Results

Default gateway behavior remains on the existing `create_or_derive_api_key()` path; derive-only tests prove derivation failure and incomplete credentials do not call creation. Existing gateway, positions, FavoriteFirst, and configuration regressions pass.

During test development, the mocked CLI fixture initially lacked `condition_id`, `receipt_ts`, and selected-position `token_id` fields; I corrected those fixture omissions before final verification. No unresolved test failures remain.

## Git Diff Summary

Tracked diff: 4 files changed, 166 insertions, 9 deletions. The new account module and test are untracked and are not included in ordinary `git diff --stat`. `git status` showed only authorized implementation/test changes plus preserved `research_outputs/`. No Git history operations were performed.

## Safety Checks

- Tests used fake gateways/SDK clients and mocked public bootstrap/feed calls.
- Derive-only never calls API-key creation; no credential values are logged.
- No order placement, signing, cancellation, allowance update, RPC, or Engine path was invoked.
- No real-money trading was performed.

## Known Limitations

The snapshot records sequential reads with separate receipt times; it is not an atomic exchange snapshot. It does not establish a clean startup baseline, import inventory, adopt orders, or approve CapitalGate.

## Deferred Findings

Runtime reconciliation of existing account state, actual-cash binding into CapitalGate, authenticated user-stream readiness, and continuous account reliability remain deferred.

## Reviewer Attention

Verify derive-only behavior cannot fall back to creation, account inputs are captured before awaits, all required spender evidence is validated, and account-read failures remain distinct from public feed readiness.

## Human Live Trading Performed

NO
