# ITER-0012 — Architect Contract

## Status

APPROVED_FOR_IMPLEMENTATION

Revision 01 resolves `03_REVIEWER.md`. It supersedes the original contract’s gateway restriction while retaining the same initial-capital problem.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`.
- Branch: `rdp-phase0-dev`.
- HEAD: `b5245503067ec7114d514e3f7f87ab7590795383`.
- Reviewed working implementation: seven modified source/test files described in `02_WORKER.md`.
- Reviewer verdict: `RETURN_TO_ARCHITECT`, three MUST_FIX findings.
- Preserved untracked research assets and Orchestrator-owned artifacts remain untouched.
- Inspected actual parser, account capture, backing evaluator, tests and review findings.
- Prior reviewed-tree tests: `1107 passed, 2 skipped`; these do not establish acceptance.

## Problem

Certify the initial empty $30 RDP run from account backing without losing authoritative partial-fill precision.

The current implementation performs exact commitment arithmetic after the gateway has already converted quantities to floats. Authorize the minimum opt-in producer correction and repair the two ordinary validation defects.

## Evidence / Current Behavior

The actual gateway computes:

```text
float("1.1") − float("0.2")
= 0.9000000000000001
```

For a foreign BUY at `0.5` with balance/allowance `$30.45`, the implemented evaluator consequently reports backing below `$30`.

Reviewer also demonstrated:

- an exact `0.1`-second freshness boundary rejected through float subtraction;
- an invalid required-spender string accepted by the evaluator.

These are implementation defects, not missing strategy authority.

## Existing Components Reused

- Existing gateway SDK request, response envelopes, authentication, executor ownership and sanitized errors.
- Existing strict account identity/cumulative-field checks.
- Existing account capture and immutable order evidence.
- Existing exact Decimal commitment/backing arithmetic.
- Existing fixed `CAPITAL`, subsequent NORMAL/REBALANCE gate, preflight and CLI.
- Existing local spender-address validation.

## Existing Components Modified

- `src/polymaker/execution/gateway.py`: opt-in exact-decimal open-order reading.
- `src/polymaker/rdp_account.py`: request and retain exact order economics.
- `src/polymaker/risk/rdp_capital.py`: freshness and spender-validation repairs.
- Current preflight/CLI implementation remains authorized.
- Relevant execution/account/capital/preflight tests.

**Shared-Core Change: YES.** This revision explicitly authorizes the default-off gateway read extension, in addition to existing CLI wiring.

**FL Behavior Impact:** NONE. Default gateway return types, numeric behavior, parser checks and callers remain unchanged.

**IM Dependency:** NONE.

## New Components Required

A small immutable exact-order read record in the gateway module, with Decimal price and remaining size.

Do not place Decimal economics into the existing float `OpenOrder` type. No new client, transport, subsystem or dependency.

## Desired Behavior

Retain the original backing behavior:

```text
AccountBUYCommitment = sum(price × remaining_size for all BUY orders)
ObservedCashBacking = balance_usd − AccountBUYCommitment
ObservedAllowanceBacking = allowance_usd − AccountBUYCommitment
```

Both backing values must reach `$30`. Selected positions/orders must be empty. Existing human account state is reported and preserved.

Add a keyword such as `exact_decimal=False` to `open_orders()`:

- Exact mode requires `strict_account=True`.
- Reuse the same SDK request and response handling.
- Parse authoritative price, original quantity and matched quantity before any float conversion.
- Preserve decimal strings, Decimal values and integer values exactly; reject booleans, malformed/nonfinite values and float fields whose source decimal precision is unavailable.
- Validate cumulative quantities and calculate remaining quantity exactly.
- Return the dedicated immutable Decimal record.
- Omitted exact mode preserves existing `OpenOrder` results and legacy arithmetic.

RDP account capture requests exact mode and retains Decimal economics directly. It must not recover apparently exact values through `Decimal(str(float_result))`.

## Minimal Scope

1. Add only the opt-in exact open-order producer path.
2. Preserve exact subtraction independently of the process Decimal context. Use coefficient/exponent arithmetic or sufficient local precision; no global context mutation.
3. Repair freshness comparison using exact decimal representations of supported timestamps and the age limit. Exact boundary passes; a genuine excess fails.
4. Validate required spenders as local address identities, including normalized uniqueness, consistently with the account reader.
5. Retain the original initial-backing completeness, scope, emptiness, expiry and reporting requirements.

No monetary/time tolerance or arbitrary rounding is authorized.

## Invariants

- Fixed starting capital remains `$30`.
- Subsequent NORMAL repair reserve remains `$5` inside that budget.
- No inventory import, order adoption or account cleanup.
- BUY remaining commitments are deducted once; SELL orders do not reserve collateral cash.
- Default execution/order types remain compatible.
- Exact-mode malformed evidence fails closed.
- No new network interface, order action or trading authority.
- Account-read success remains separate from backing eligibility.

## Acceptance Criteria

1. Actual gateway parser → account capture → evaluator with price `0.5`, original `1.1`, matched `0.2`, balance/allowance `$30.45` yields remaining `0.9`, commitment `$0.45`, backing `$30` and eligibility.
2. A genuine cash or allowance shortfall on that same producer path remains ineligible.
3. Long authoritative decimal quantities survive subtraction and capture without context rounding.
4. Invalid/missing/nonfinite/boolean exact fields and invalid cumulative quantities fail explicitly.
5. Default gateway calls return existing float `OpenOrder` objects with unchanged behavior.
6. Exact mode cannot run without strict cumulative-field validation.
7. `now=1000.0`, receipts `(999.9,999.95,1000.0)`, limit `0.1` passes freshness; immediately older evidence fails.
8. Invalid, missing or duplicate-normalized spender identities cannot certify backing.
9. Original initial-backing criteria remain satisfied: complete order collection, independent backing checks, selected empty state, fixed capital, freshness/expiry, immutable results and separate CLI reporting.
10. Existing capital projection/admission and predecessor regressions pass.
11. No live read/write, signing, cancellation or RPC is performed during implementation/testing.

## Required Tests

Add controlled producer integration tests using the **actual gateway parser**, with fake SDK responses and actual account capture/evaluator. Do not replace the producer with preconstructed Decimal remaining quantities.

Cover:

- Reviewer’s exact partial-fill fixture and genuine micro-dollar shortfalls.
- Multiple fractional fills and long decimal quantities.
- Unsupported numeric types, missing cumulative fields, matched greater than original and negative/nonfinite values.
- Default versus exact return types and behavior.
- Exact-mode strict-flag requirement.
- Fractional freshness boundary and immediately excessive age.
- Valid spender addresses, malformed/missing addresses and case-normalized duplicates.
- Existing startup backing, account/preflight and NORMAL/REBALANCE regressions.

Run focused producer/account/capital tests, affected execution/positions/predecessor tests and the full suite when practical. Record exact commands/results. Inspect status, untracked files, diff stat and `git diff --check`.

## Observability Requirements

Retain initial-backing reporting from the original contract:

- eligibility/reason;
- fixed capital;
- exact BUY commitment;
- independent cash/allowance backing;
- read age and selected baseline;
- no real-money trading.

Keep errors sanitized. Do not persist credentials or raw authenticated payloads.

## Live Validation

No live order or cancellation is authorized.

This repair is verified with controlled authoritative-shaped responses. Final readiness must separately establish live read compatibility and trading-time revalidation.

## Explicit Non-Goals

- Changing legacy open-order numeric behavior.
- Changing shared `OpenOrder` or fill-processing numeric types.
- Position-reader precision redesign.
- Trading-time cash binding, user stream or controller.
- Inventory import, account cleanup or settlement.
- New SDK/client or private-cache changes.
- Real-money trading.

## Risks

An SDK response containing already-rounded float fields cannot establish original decimal precision; exact mode must reject rather than guess.

Sequential account observations still require later runtime revalidation.

## Deferred Findings

- Position-reader precision should be assessed before its values certify runtime inventory baselines.
- Continuous cash/order reliability and controller integration remain outstanding.
- Final tick provenance and per-token minimum-size integration remain required.

## Human Approval Required

NO.

The revision authorizes a bounded exact-read producer correction within existing official infrastructure. It resolves the reviewer’s scope conflict without changing Frozen semantics or default predecessor behavior.
