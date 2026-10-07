# ITER-0011 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `ee6562bf3f3c5b2e8acdbc19dfffba23d54b43cb`.
- ITER-0010 PASS archived at `3acbf1c0c5024bb4de287d25a2d7509af3f90f5c`.
- Inspected current authority/index/baseline, previous result/review/receipt, RDP preflight, gateway connection/read methods, installed SDK authentication and relevant tests.
- Preserved untracked `research_outputs/` remains untouched.
- Prior full-suite evidence: `1039 passed, 2 skipped`.

## Problem

RDP preflight observes public market state but has no authenticated account-read path. Existing gateway connection uses an SDK method that first attempts API-key creation through POST.

Add one read-only account-snapshot prerequisite using existing official reads and derive-only authentication.

## Evidence / Current Behavior

- Existing gateway provides authenticated open-order reads, collateral balance/allowance and official Data API positions.
- `connect()` calls `create_or_derive_api_key()`.
- The installed SDK implements that method by trying `create_api_key()` through POST before deriving.
- The SDK also exposes `derive_api_key()` through GET.
- FavoriteFirst account baseline includes blockchain reads and strategy-specific startup conditions; do not reuse that orchestration.
- Existing collateral helper accepts permissive numeric conversion. RDP snapshot validation must reject malformed monetary evidence explicitly.

## Existing Components Reused

- Existing `ExecutionGateway` client construction, identity fields and thread pool.
- Official SDK `derive_api_key()` and existing authenticated reads.
- Existing `open_orders()`, `positions()`, `balance_allowance()` and `required_collateral_spenders()`.
- Existing configured wallet identity and official endpoint validation.
- Existing RDP public preflight, CLI and logging.
- Existing gateway/position/authentication test fixtures.

## Existing Components Modified

- `src/polymaker/execution/gateway.py`: default-off derive-only connection option.
- `src/polymaker/rdp_live.py`: compose optional account snapshot.
- `src/polymaker/cli.py`: expose explicit account-check option and report its scope.
- Relevant tests.

**Shared-Core Change: YES**, limited to the backward-compatible connection extension and CLI wiring.

**FL Behavior Impact:** NONE; default connection behavior remains unchanged.

**IM Dependency:** NONE.

## New Components Required

A small `src/polymaker/rdp_account.py` module containing:

- immutable validated account-read evidence;
- categorized account-read failure;
- thin orchestration over the existing gateway.

No new account API client or execution subsystem.

## Desired Behavior

Extend the safe command:

```bash
uv run polymaker rdp-live --market <exact BTC5m slug> --preflight-only --account-check
```

Without `--account-check`, preserve public preflight behavior.

With it:

1. Validate configured wallet identity, supported signature type and official CLOB/Data API origins before authenticated requests.
2. Capture configuration/endpoint inputs before awaits so mutation cannot redirect requests.
3. Connect the existing gateway using a new derive-only option. Use `derive_api_key()` exactly; never fall back to key creation.
4. Require complete usable derived credentials. Validate gateway signer/funder against configured identity; for signature type zero, signer and funder must agree.
5. Read authoritative open orders, official Data API positions, collateral balance/allowance and required collateral spenders through existing methods.
6. Return separately identifiable selected-market positions/orders, total account open-order count and validated monetary evidence.
7. Recheck market expiry before successful capture.
8. Close owned resources in every exit path.

The snapshot reports account evidence only. It does not approve CapitalGate, import inventory, adopt existing orders or certify a clean startup baseline.

## Minimal Scope

Add a keyword such as `derive_only=False` to gateway connection. Default callers retain the existing method. Derive-only failure propagates; no creation fallback or paper fabrication may certify RDP account reads.

For the account result:

- Balance/allowance inputs must be finite, nonnegative numeric values; reject booleans and malformed fields.
- Preserve monetary values using Decimal and the existing six-decimal collateral unit convention.
- Require every resolved relevant spender’s allowance. Reject missing, contradictory case-normalized spender entries or unresolved spenders.
- Reuse existing strict order/position reads; reject unavailable or malformed results rather than manufacturing empty state.
- Preserve selected existing positions and orders in evidence without mutation.
- Record receipt times for sequential reads; do not claim an atomic exchange snapshot.
- Sanitize errors and logs so credentials, private keys and authentication headers cannot be exposed.

No authenticated user WebSocket or trading state starts.

## Invariants

- Read-only official HTTP interfaces.
- No API-key creation, credential deletion, allowance update, order signing/POST, cancellation or RPC.
- No automatic cleanup of human account state.
- Existing selected inventory is not converted into confirmed-fill accounting.
- A snapshot is not capital admission or trading authorization.
- Default gateway/FL behavior remains compatible.
- No real-money action during development.

## Acceptance Criteria

1. Default gateway connection retains its current behavior.
2. Derive-only connection calls GET derivation and never creation, including failure paths.
3. Missing wallet, invalid identity/type/origin or incomplete credentials fail explicitly.
4. Valid account fixtures exercise existing official read methods and produce immutable, scoped evidence.
5. Missing/invalid balance, required allowance or spender evidence fails closed.
6. Open-order/position read failures cannot become empty account state.
7. Nonzero selected positions and existing selected orders are reported unchanged; no cleanup occurs.
8. Account/configuration mutation cannot redirect subsequent requests.
9. Market expiry prevents account-check success.
10. Complete CLI paths distinguish public readiness from account-read evidence and preserve default public behavior.
11. No forbidden execution/RPC/authentication mutation path is invoked.
12. Existing gateway, position, public preflight and predecessor regressions pass.

## Required Tests

Use fake SDK clients, HTTP fixtures and mocked CLI:

- Default versus derive-only connection.
- Derivation failure and malformed credentials; creation method patched to raise if called.
- Signer/funder mismatch and signature-type-zero identity.
- Invalid endpoints and configuration mutation during awaits.
- Valid zero/nonzero selected positions and selected/foreign open orders.
- Order/position read errors and malformed evidence.
- Exact six-decimal monetary conversion.
- Boolean, negative, nonfinite, missing and malformed balance/allowance inputs.
- Required-spender selection, missing spender and contradictory normalized keys.
- Immutable result capture and separate receipt timestamps.
- Expiry and resource cleanup on success/failure/cancellation.
- Complete CLI account-check success/failure and unchanged public-only path.
- Order creation/signing, cancel, key creation, allowance update, RPC and legacy Engine methods patched to fail if invoked.

Run focused tests, affected execution/positions/config/preflight tests and the full suite when practical. Record exact commands/results; inspect status, untracked files, diff stat and `git diff --check`. Safely exercise CLI help and the complete command through mocks.

## Observability Requirements

Reuse the RDP preflight log. Record:

- account-check result or sanitized failure category;
- signer/funder identity;
- balance and relevant allowance in dollars;
- required spender identities;
- selected position quantities;
- selected open-order IDs and total account order count;
- read receipt timestamps;
- `real_money_trading_performed: false`.

Never persist derived credentials.

## Live Validation

No order or cancellation is authorized.

The eventual safe pre-live invocation can validate these official reads using configured credentials. This iteration may rely on controlled fixtures; record any live read validation separately and honestly. Failure to derive an existing key must never trigger key creation.

## Explicit Non-Goals

- User WebSocket or fill processing.
- Clean-account admission policy.
- Inventory import or existing-order adoption.
- Actual cash binding into CapitalGate.
- Allowance changes or account cleanup.
- Continuous reconciliation/controller.
- SDK cache refresh.
- Trading authorization options.
- Real-money trading.

## Risks

Sequential account reads are observations at different times. The runtime must revalidate before any later admission.

Derived credentials may not exist; read-only preflight must fail without creating them.

Legacy balance reads return empty mappings on some failures; strict RDP validation must reject these.

## Deferred Findings

- Runtime startup must reconcile existing selected account state without fabricating fill history.
- Capital admission must bind the frozen $30 budget to reliable actual cash and reservations.
- Authenticated user-stream readiness and continuous reliability remain required.
- Existing position-reader duplicate-row behavior should be assessed if later reconciliation depends on such evidence.

## Human Approval Required

NO.

This adds a bounded, explicitly requested authenticated-read check using existing official infrastructure. It changes no strategy semantics and grants no account mutation or trading authority.
