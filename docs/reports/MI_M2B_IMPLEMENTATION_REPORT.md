# MI M2B Single-Market Micro-Live Execution Implementation Report

Status: BLOCKED BEFORE IMPLEMENTATION — AWAITING ARCHITECTURE DECISION; no MI execution authority
Commit / working tree base: `f25a5a539f711f2dcfe492105c7d92bbfbe812f7` (`mi-m2a-reviewed-baseline`) on `m4-dynamic-ev`; pre-existing `livecfg/config.toml` and `research_outputs/` retained.
Reviewed predecessor baseline: MI M2A checkpoint `f25a5a539f711f2dcfe492105c7d92bbfbe812f7` (M2A CLOSED / PASS).
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md` (accepted M2A NO-SEND supplement; no accepted M2B send-enabled slice); explicit M2B user requirements.

## Scope implemented

None. Read-only inspection found two conditions that the M2B requirements explicitly say must stop execution wiring. No production code, tests, live config, credentials, or runtime artifacts were changed.

## Files added

- This blocker and handoff report.

## Files modified

- `docs/CURRENT_STATUS.md`: records the M2B blocker and next Architect action.

## Important implementation decisions

1. The existing controlled-live preflight is FavoriteFirst-specific. `favorite_live.validate_preflight()` uses a fixed $12 threshold for two five-share orders; `_preflight()` calls `engine._favorite_economics()` and depends on FavoriteFirst recovery state. `Engine.arm_favorite_first()` checks FavoriteFirst memory, orders and engine state. This is not a generic MI authorization gate. MI must not reuse it as-is, copy a weaker version, or route its orders through FIRST/PAIR. The minimum prerequisite is an Architect-reviewed generic extraction of wallet/funder, official feed/user-stream, target-market cleanliness, Fill Truth and cash checks, with MI-specific order admission and explicit arming. The existing FavoriteFirst behavior must remain unchanged.
2. M2A `OfficialReadOnlyAccount.snapshot()` returns `min(CLOB collateral balance, exchange allowance)` as `cash_available`, while its open-order result contains only token IDs. MI startup rejects orders in the target market, not orders in other markets. The official [Polymarket order lifecycle documentation](https://docs.polymarket.com/concepts/order-lifecycle) says maximum new-order size is balance minus amounts reserved by existing open orders. Consequently the M2A field is not proven to be spendable cash for M2B when other-market orders exist. Neither a generic reservation ledger nor an authenticated account response containing effective free collateral was found in the inspected MI/gateway/risk path. An unverified subtraction could double-count or omit reservations. The minimum prerequisite is an Architect-reviewed cash contract: define the official balance field, enumerate and value all account-wide open BUY remainders (including ambiguous in-flight MI orders), establish whether venue reporting already nets reservations, and test both no double-spend and no double-subtraction. Until then, fail closed before any MI submit.
3. The current canonical MI architecture describes M2A as NO-SEND and says no M2B path is defined. Its broader execution design is marked reconstructed and awaiting acceptance. The user's explicit M2B implementation authorization does not resolve the two concrete interface contracts above.

## Deviations from architecture

- No implementation deviation: work stopped at the M2B prerequisite gate. No architecture revision was made by a Worker.

## Tests added

- None; execution integration and corresponding tests were not started.

## Tests executed

- None. The task stopped on read-only prerequisite inspection before a code change; historical M1/M2A counts were not represented as current M2B test results.

## Test results

- M2B: not run / not implemented.
- M1 integrity: `git diff f25a5a5 -- src/polymaker/strategy/maker_inventory.py tests/test_maker_inventory.py` was empty at inspection.

## Known limitations / unresolved issues

- A generic, strategy-neutral live authorization contract is not yet accepted.
- Account-wide effective available collateral and open-order reservation semantics are not yet established for the installed official SDK/API and current account mode.
- No MI submit, cancel, lifecycle, Fill Truth projection, or T+300 final reconciliation is implemented.

## Safety / behavior impact

Live trading semantics are unchanged. No live market was run; no order was submitted or cancelled. M2A remains NO-SEND only. The gateway has official CLOB write primitives, but MI has no reachable write path.

## Git diff summary

Documentation only: this report and a concise `CURRENT_STATUS.md` handoff. The pre-existing local config/research changes remain untouched. No commit, tag, or push.

## Handoff to Architect

Define and accept the smallest generic live authorization extraction and the exact official cash/reservation contract, with account-wide order coverage and deterministic tests. The existing user authorization for M2B code development can then proceed against `mi-m2a-reviewed-baseline`; actual market execution remains separately prohibited. Do not arm MI, run a market, or treat M2A `cash_available` as proven spendable collateral in the meantime.

## M2B-0A follow-up — 2026-10-04

Blocker A (generic live authorization) now has a bounded implementation in
`docs/reports/MI_M2B_0A_IMPLEMENTATION_REPORT.md`, status IMPLEMENTED — AWAITING
INDEPENDENT REVIEW. This does not retroactively change the initial inspection
result or authorize MI order writes. Blocker B (cash/reservation contract) remains
open and is explicitly reserved for M2B-0B.
