# ITER-0005 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved ITER-0005 revision 04 repair for MF-01 and MF-02 from `docs/development/ITER-0005/06_REVIEWER.md`. Scope remained limited to the authorized capital module and its tests. No policy or quote behavior changed.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `57bee8ffb8b83c7e81c6999773c4d11edabec351`
- Pre-existing untracked `research_outputs/` and Orchestrator-owned iteration files were preserved.

## Existing Components Reused

- Existing `UserEventProcessor` and its `StateStore.orders` local order observations.
- Existing `OpenOrder` and `OrderEvent` types.
- Existing `close_rdp_cycle()` fake-gateway test infrastructure and complete `RDPCloseResult`.
- Existing inventory accounting, capital projection, and NORMAL candidate/gate.

## Files Changed

- `src/polymaker/risk/rdp_capital.py`
- `tests/test_rdp_capital.py`

## Implementation Summary

Close evidence now rejects any covered order ID that remains in the processor’s current local open-order map. The check is read-only and does not clear or alter contradictory evidence.

The NORMAL capital guarantee test now admits the actual dual pair—five YES at `$0.29` and five NO at `$0.59`—from `$20.60` inventory cost and `$9.40` free cash. It checks full and fractional YES-only and NO-only confirmed-fill outcomes, reservation replacement at exactly `$5.00` free cash, and verified cancellation release.

## Behavior Before

Prior close evidence could release a reservation even after a newer local LIVE order observation. The partial-fill tests did not exercise the admitted five-share NORMAL dual pair at the exact reserve boundary.

## Behavior After

A reappeared local order invalidates its earlier close proof. A renewed successful close restores certification. For each tested single-sided fill outcome, confirmed acquisition cost replaces the corresponding posted reservation while free cash remains `$5.00`; verified close releases the remaining reservation without reducing cash below the protected amount.

## Tests Added / Changed

- Added a late-LIVE regression: old close evidence rejects, the local order remains intact, and renewed fake-gateway close evidence restores certification.
- Replaced the undersized single-order partial-fill case with four actual NORMAL dual-pair cases: full and fractional fills on either YES or NO, including exact expected cost, reservation, and cash values.

## Tests Executed

- `uv run pytest -q tests/test_rdp_capital.py`
- `uv run pytest -q tests/test_rdp_accounting.py tests/test_rdp_quotes.py tests/test_rdp_lifecycle.py tests/test_state.py tests/test_filltruth.py tests/test_risk.py`
- `uv run pytest -q`
- `uv run ruff check src/polymaker/risk/rdp_capital.py tests/test_rdp_capital.py`
- `git diff --check`
- Untracked implementation files were checked with `git diff --no-index --check`; no whitespace errors were reported.

## Test Results

- Capital tests: **40 passed**.
- Affected integration/regression tests: **220 passed**.
- Full suite: **835 passed, 2 skipped**.
- Ruff: **All checks passed**.
- Whitespace checks: **Passed**.

## Regression Results

The prior accounting, quote, lifecycle, state, fill-truth, and risk regression set passed: **220 passed**. Full suite passed with **835 passed, 2 skipped**.

## Git Diff Summary

Worker changes are limited to the two authorized files. They were new untracked files at the time of inspection, so the ordinary tracked `git diff --stat` does not include them. The working tree also contains pre-existing Orchestrator changes and preserved untracked `research_outputs/`; none were modified by this Worker.

## Safety Checks

No network calls, order placement, live cancellation, Git history operations, or authoritative production-state mutations were performed. The new source check only reads current local order IDs. Test execution used fake gateways.

## Known Limitations

Fresh official account/order snapshots and runtime freshness validation remain caller responsibilities, as defined by the contract.

## Deferred Findings

No additional findings were addressed.

## Reviewer Attention

Please verify that the local-order check rejects only close-covered IDs currently present in `StateStore.orders`, and that the four dual-pair fill cases demonstrate exact `$5.00` post-admission cash before verified cancellation releases remaining reservation.

## Human Live Trading Performed

NO
