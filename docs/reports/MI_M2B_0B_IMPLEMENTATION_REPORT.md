# MI M2B-0B Cash / Reservation Contract Implementation Report

Status: PROCESS-AUTHORITY REPAIR IMPLEMENTED — AWAITING INDEPENDENT RE-REVIEW; no MI live-write authority
Commit / working tree base: `f25a5a539f711f2dcfe492105c7d92bbfbe812f7` plus the uncheckpointed, independently reviewed M2B-0A working tree
Reviewed predecessor baseline: M2B-0A final Re-review PASS in `docs/reviews/MI_M2B_0A_REVIEW.md`; Git checkpoint pending
Architecture reference: `docs/architecture/MI_ARCHITECTURE.md`, M2B-0B supplement

## Scope implemented

MI requires exactly $25.00 (25,000,000 integer micro-units) of confirmed
collateral before an ACTIVE market reservation can be created. The source is
the existing official CLOB collateral `balance` response. The existing CLOB
gateway's unfiltered `open_orders()` call delegates to the official SDK,
whose installed implementation reads every page. The source balance is
admitted only when that complete account-wide list is empty. This follows
[Polymarket's order lifecycle rule](https://docs.polymarket.com/concepts/order-lifecycle)
that available order size is balance less commitments to existing open orders.
With a nonempty list, this slice denies readiness rather than guessing their
cash cost. Allowance is not collateral and is not subtracted or reserved here.

The reader requires a valid Generic Gate authorization bound to the current
configured and connected execution identity and a non-paper gateway. It
validates that identity before and after the official account reads. Missing,
failed, malformed, or non-integral micro-unit balance reads fail closed.

All ordinary `MICashReservationBook` instances use one module-private
process authority with a shared lock and record namespace. It computes confirmed collateral less
all ACTIVE reservations sharing the same collateral funder. A reservation
binds that identity, canonical market `condition_id`, amount, state, and an
opaque reservation ID. The future MI runtime must own one shared book and
require the matching ACTIVE reservation before live-write admission. No MI
runtime or write path is wired by this slice.

## Files added

- `src/polymaker/mi_cash_reservation.py`
- `tests/test_mi_cash_reservation.py`
- `docs/reports/MI_M2B_0B_IMPLEMENTATION_REPORT.md`

## Files modified

- `docs/architecture/MI_ARCHITECTURE.md` — bounded M2B-0B contract.
- `docs/CURRENT_STATUS.md` — handoff state.

## Important implementation decisions

- Conservative account-wide empty-open-order gate avoids an unproven open-order
  notional calculation and avoids treating allowance as cash.
- Amounts use integer CLOB micro-units; the official response must be a
  nonnegative integer or decimal-digit string, never a binary float.
- Duplicate ACTIVE reserve for the same identity and market is idempotent.
  Market condition IDs are stripped and lowercased before keying, so case or
  surrounding whitespace cannot allocate another $25 to the same market.
  A fresh collateral snapshot below total ACTIVE commitments denies even a
  duplicate request.
- Release requires reservation ID, matching identity, and market; repeated
  release has no second credit. An old released ID cannot release a newer
  ACTIVE reservation.
- Reservation scope is one Python process across all ordinary book instances.
  Cross-process, restart, database, and external-account-actor guarantees are
  outside this slice.

## Deviations from architecture

- NONE within the user-authorized M2B-0B supplement. The wider historical MI
  architecture remains unaccepted design context where it describes later
  execution, settlement, or trial functionality.

## Tests added

Boundary amounts ($24.99, $24.999999, $25, $25.000001, $40), double
allocation, release/re-reserve, duplicate reserve/release, stale release,
identity/market isolation, missing cash, malformed official balance, official
read failure, existing account order denial, identity mismatch before and
after official reads, shared-funder
isolation across distinct signers, and concurrent
reserve attempts.

## Tests executed

- `uv run pytest tests/test_mi_cash_reservation.py tests/test_live_authorization.py tests/test_favorite_live.py tests/test_maker_inventory.py tests/test_mi_live.py tests/test_engine.py tests/test_gateway_positions_v2.py tests/test_gateway_token_balances.py -q`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/mi_cash_reservation.py tests/test_mi_cash_reservation.py`
- `uv run mypy src/polymaker/mi_cash_reservation.py`
- `uv run ruff check src tests` and `uv run mypy` to classify existing repository-wide debt.
- `git diff --check` and M1/M2A baseline diff inspection.

## Test results

- Initial M2B-0B tests: 21 passed; process-authority repair tests added below.
- Generic Gate + FavoriteFirst: 75 passed.
- M1: 57 passed; M2A: 19 passed; related Engine/gateway: 39 passed.
- Combined targeted run: 209 passed.
- Initial full suite: 811 passed, 2 skipped. Repair verification follows below.
- Ruff touched source/test: PASS. Mypy new source: PASS. Diff check: PASS.
- Repository-wide Ruff: 100 pre-existing findings in unrelated source/tests.
  Repository-wide Mypy: 40 pre-existing errors in 11 unrelated files. The new
  M2B-0B module appears in neither error list.
- M1 and M2A source/test files: no diff from `f25a5a5`.

## Known limitations / unresolved issues

- An account with **any** open order is denied even if sufficient uncommitted
  collateral may remain. This is intentional fail-closed behavior until
  account-wide order commitment accounting is proven separately.
- `ConfirmedSpendableCollateral` is a typed evidence object; Python callers
  can construct objects. Future MI runtime must obtain it through the official
  reader. This slice does not add
  a live MI entrypoint or capability gate.
- Collateral can change due to an external actor after the snapshot. The
  guarantee here is in-process duplicate reservation prevention, not a venue
  hold or cross-process lock. Future order admission still needs a fresh
  official cash and execution safety check.
- M2B-0A reviewed working tree has not yet been checkpointed. No checkpoint
  or tag is created in this implementation task.

## Safety / behavior impact

No live trading semantics changed. No MI order send, cancel, fill handling,
position update, or micro-live CLI exists in this slice. FavoriteFirst's $12
preflight and the dollar-neutral Generic Live Authorization Gate are unchanged.
No real market or authenticated write was run.

## Git diff summary

The production change is a new isolated cash reader and in-memory reservation
module. Tests and bounded documentation are additive. M1, M2A, Engine,
FavoriteFirst, and the Generic Gate are not modified by M2B-0B.

## Process-authority blocker repair — 2026-10-05

The initial independent Review (`docs/reviews/MI_M2B_0B_REVIEW.md`) returned
NEEDS FIX: separate `MICashReservationBook` instances could each reserve the
same $25. This repair changes only the process-local reservation authority.
One module-private `_PROCESS_AUTHORITY` now owns the lock, records, active
index, and reservation ID namespace. Every ordinary book instance uses that
same authority; the constructor exposes no backend injection. Reserve still
holds the shared lock across funder-level ACTIVE sum, eligibility check, and
record creation. Release and availability use the same authority and lock.

The existing 21 tests remain. New tests cover two-book double-allocation
denial, cross-book idempotency, same-funder signers, independent funders,
cross-book correct/wrong-owner release, stale ID safety, and concurrent
cross-book reserves. A private autouse test fixture replaces the private
authority for deterministic test isolation; no public reset API was added.

No official account read, fixed $25 policy, ownership check, M2B-0A Gate,
FavoriteFirst, M1, M2A, external interface, or MI execution behavior changed.
This is Worker evidence, not a Review PASS.

### Repair verification

- M2B-0B: 27 passed, including all original 21 and six new cross-instance
  tests. The offline two-book probe denied a second-market reserve from the
  same $25, reported zero remaining, and returned the same reservation ID for
  the same identity and market.
- Generic Gate + FavoriteFirst: 75 passed.
- M1 + M2A: 76 passed.
- Related Engine/gateway: 39 passed.
- Full suite: 817 passed, 2 skipped.
- Ruff on changed source/test: PASS. Mypy on changed source: PASS.
- `git diff --check`: PASS.
- No real market, order, cancel, or authenticated trade write was run.

## Handoff to Reviewer

Inspect the official balance/open-order semantics, the installed SDK's
account-wide pagination, integer precision, identity binding, and atomic
double-reservation behavior. Keep M2B execution blocked pending independent
Review PASS and a complete checkpoint. Do not infer live authority from this
primitive.
