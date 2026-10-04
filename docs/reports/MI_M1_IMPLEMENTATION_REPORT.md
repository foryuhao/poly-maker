# MI M1 Implementation Report

Status: M1.1 IMPLEMENTED — AWAITING INDEPENDENT RE-REVIEW
Commit / working tree base: `19c322a` on `m4-dynamic-ev`; MI files remain uncommitted.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) is the last reviewed live baseline; `19c322a` is the pre-MI archival Git predecessor and does not grant Review PASS to its unreviewed live changes.
Architecture reference: User-provided MakerInventory Baseline 1.0 M1 specification (2026-10-04). No canonical MI architecture document existed in the repository at implementation start.

## Scope implemented

- Added an independent, pure, single-market MakerInventory controller. It accepts both books, confirmed YES/NO quantities, cash available for a new order, elapsed time, data-validity flag, venue tick/minimum/quantity step, and at most one caller-confirmed MI order lifecycle.
- Computes the two-book market probability proxy, continuous net target, confirmed net/pair/residual quantities, tracking delta, and current or candidate potential net-inventory interval using `Decimal(str(value))` arithmetic.
- Emits only WAIT, maker BUY YES/NO intent at the respective best bid, KEEP, CANCEL, or WAIT_FOR_ORDER_LIFECYCLE. Cash, venue minimum, quantity precision, hard inventory bound, full-flip, constrained-flip, data, time, and working-order rules are explicit reasons in the decision.
- The caller must supply a verified spendable cash amount after its external fee/reservation policy and authoritative venue quantity step. M1 does not infer account balances or venue precision.

## Files added

- `src/polymaker/strategy/maker_inventory.py`
- `tests/test_maker_inventory.py`
- `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md`

## Files modified

- `docs/CURRENT_STATUS.md`: records MI M1 handoff while preserving prior M4 state.

## Important implementation decisions

- MI uses its own Config, Book, Snapshot, State, Order, and Decision. It reuses only the generic `Side.BUY` enum, with no FavoriteFirst state or policy dependency.
- `quantity_step` and venue minimum are snapshot inputs because the repository does not have a general-purpose, authoritative BUY quantity quantizer. Quantity is always floored to the supplied step and never raised to meet the minimum.
- `cash_available_for_new_order` represents actual spendable buying power for a new order. An existing order's potential full fill is checked against its explicit `reserved_cash` plus still available cash.
- A precision-limited full direction flip is treated as constrained: the next new order can only reduce old net exposure and cannot cross zero. Partial fills are represented by fresh confirmed quantities and the order's remaining quantity.
- A cancellation already requested/pending emits WAIT_FOR_ORDER_LIFECYCLE. A submit-pending order can request CANCEL for invalid data, final window, or other keep/cancel violations, while never allowing a second PLACE.
- The config rejects a max-absolute-inventory value over 25 shares so a caller cannot widen the Baseline 1.0 hard bound.

## Deviations from architecture

- NONE from the user-provided M1 specification. A repository MI architecture document does not yet exist; Architect review is the next gate.

## Tests added

- 50 deterministic MI tests cover probability and continuous targets, 4.99/5 thresholds, sizing and floor precision, confirmed net inventory, both flip directions, constrained reduction, partial-fill remainder, potential bounds, all order lifecycle states, price/direction/no-need cancellation, small target drift, cash, data, final window, invalid books, and BUY-only placement invariants.

## Tests executed

- `uv run pytest tests/test_maker_inventory.py -q`: 50 passed.
- `uv run pytest tests/test_maker_inventory.py tests/test_favorite_first.py tests/test_quoting.py tests/test_config_aliases.py -q`: 124 passed.
- `uv run pytest -q`: 739 passed, 2 skipped.
- `uv run ruff check src/polymaker/strategy/maker_inventory.py tests/test_maker_inventory.py`: passed.
- `uv run mypy src/polymaker/strategy/maker_inventory.py`: passed.
- `git diff --check`: passed for tracked changes; new files were separately inspected.

## Test results

All final local checks passed. No live, authenticated, network, or order-submission command was run.

## Known limitations / unresolved issues

- M1 deliberately trusts caller-supplied YES/NO identity, confirmed inventory, spendable cash, order lifecycle, and venue metadata. Their extraction and validation in a live process are M2 concerns.
- M1 does not model fees, Merge, settlement, W_market, or a persistent fill ledger. The caller's spendable cash input must already reflect any relevant external buying-power reserve.
- Architect review is outstanding. No MI PASS tag or checkpoint has been created.

## Safety / behavior impact

Existing live trading semantics changed: NO. MI is not wired into Engine, CLI, gateway, user stream, or any live order path. MI itself emits no SELL action and performs no network or trading I/O.

## Git diff summary

Two new MI code/test files, this report, and one documentation handoff update. Pre-existing `livecfg/config.toml` modification and 748 local `research_outputs/` files were preserved untouched. No commit or push was made.

## Handoff to Architect

Review the full M1 caller contract and deterministic action rules against the user specification, especially cash/reservation meaning, venue quantity step, full-flip precision behavior, and pending-order cancellation intent. Review the new files as untracked artifacts. Do not infer live trading authority from passing pure-core tests.

## M1.1 repair iteration — 2026-10-04

Trigger: `docs/reviews/MI_M1_REVIEW.md` returned NEEDS FIX on the original M1 working tree with two P1 findings and one test gap. This section preserves the original implementation and test history above rather than replacing it. The reconstructed `docs/architecture/MI_ARCHITECTURE.md` is now present as a draft awaiting acceptance; the explicit user M1.1 sizing and reduction instructions govern this bounded repair.

### Scope repaired

- Full target quantity is floored to the caller-supplied venue step before checking minimum size, cash, and potential inventory. A legal 39.9-share near-full flip is no longer mistaken for an inadmissible exact 40-share order. When a full quantized flip truly fails admission, fallback remains capped at old net exposure before flooring and cash/bound checks.
- `reducing_old_exposure` now describes the candidate's full possible fill path: an opposite-side BUY starts from nonzero net inventory and finishes no farther than zero. It no longer reflects which sizing branch was used. Full flips crossing zero remain false; ordinary reductions to zero or to a smaller same-side net position are true.
- Kept probability, target formula, threshold, order lifecycle, price, cash input, hard bound, data/time gates, and existing strategy/runtime code unchanged.

### Files changed in M1.1

- `src/polymaker/strategy/maker_inventory.py`: bounded sizing and decision flag repair.
- `tests/test_maker_inventory.py`: corrected the old 19.8-share expectation and added symmetric quantized flip, both-direction pure-reduction, constrained quantized fallback, and subminimum working-remainder regressions.
- `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md` and `docs/CURRENT_STATUS.md`: repair evidence and handoff only.

### M1.1 tests and results

- `uv run pytest tests/test_maker_inventory.py -q`: **57 passed**.
- `uv run pytest tests/test_maker_inventory.py tests/test_favorite_first.py tests/test_quoting.py tests/test_config_aliases.py -q`: **131 passed**.
- `uv run pytest -q`: **746 passed, 2 skipped**.
- `uv run ruff check src/polymaker/strategy/maker_inventory.py tests/test_maker_inventory.py`: passed.
- `uv run mypy src/polymaker/strategy/maker_inventory.py`: passed.
- `uv run ruff format --check` and `git diff --check`: passed after formatting and documentation update.

### Remaining status and handoff

M1.1 is implemented and locally tested, not independently re-reviewed. The earlier M1 NEEDS FIX verdict remains historical evidence for the original tree. Reviewer should inspect this exact uncommitted repair against the two P1 findings and the added working-remainder case, then update the same canonical Review document. No MI commit, tag, push, M2 code, live order, network call, Merge, or Settlement occurred in this repair.
