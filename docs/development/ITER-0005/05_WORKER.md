# ITER-0005 — Worker Implementation

## Status

IMPLEMENTED

## Architect Contract

Implemented the approved ITER-0005 revised contract at `docs/development/ITER-0005/04_ARCHITECT_REVISION_01.md`, following the approved $5 NORMAL repair reserve in `03_HUMAN_DECISION.md`.

## Base Repository State

- Root: `/home/ubuntu/poly-maker`
- Branch: `rdp-phase0-dev`
- HEAD: `57bee8ffb8b83c7e81c6999773c4d11edabec351`
- The contract recorded an earlier HEAD (`49291f5`); implementation followed the actual repository state.
- Preserved untracked `research_outputs/` assets were left unchanged.

## Existing Components Reused

- `snapshot_inventory()` and `RDPInventorySnapshot`
- `UserEventProcessor` ownership registry, admitted fills, and quarantine evidence
- Existing `OpenOrder`, `Quote`, `Side`, and `TradeState` types
- `RDPQuoteDecision`
- `RDPCloseResult` and `close_rdp_cycle()` for real fake-gateway close evidence

## Files Changed

- `src/polymaker/risk/rdp_capital.py` — new
- `tests/test_rdp_capital.py` — new

No existing production or test files were changed.

## Implementation Summary

Added immutable certified-capital projection and admission-result types. Projection requires explicit reliable market/order-state inputs, validates each run order against ownership and fill evidence, validates supplied market-token resting orders and remaining-quantity checksums, and requires complete fresh close evidence before releasing a missing order’s reservation.

Inventory cost is computed with Decimal arithmetic from the existing processor’s admitted confirmed fills; resting reservations use remaining quantity and registered submitted price. NORMAL admission preserves the fixed $5 reserve. REBALANCE may use it. Admission uses exact Decimal comparisons without a broad tolerance. The code does not maintain a mutable ledger or alter processor, order, or fill state.

## Behavior Before

There was no RDP-specific capital projection or CapitalGate implementing the approved NORMAL repair reserve.

## Behavior After

Reliable evidence produces a capital identity of `$30 = InventoryCost + ReservedCash + FreeCash`. Unreliable, pending, unadmitted, quarantined, contradictory, foreign, or unproved-absent order evidence produces no certified snapshot. NORMAL candidates must leave at least `$5.00`; REBALANCE candidates may use all remaining cash.

## Tests Added / Changed

Added 37 tests covering the required projection and admission matrix, including real fake-gateway close evidence, the `$7` and `$23` examples, exact NORMAL and REBALANCE boundaries, float-producer Decimal compatibility, partial fills, stale close evidence, malformed inputs, and independent captures showing processor/store inputs remain unchanged.

## Tests Executed

- `uv run pytest -q tests/test_rdp_capital.py`
- `uv run pytest -q tests/test_rdp_accounting.py tests/test_rdp_quotes.py tests/test_rdp_lifecycle.py tests/test_state.py tests/test_filltruth.py tests/test_risk.py`
- `uv run pytest -q`
- `git diff --check`
- No-index whitespace checks for both new files
- `uv run ruff check src/polymaker/risk/rdp_capital.py tests/test_rdp_capital.py`

## Test Results

- Capital tests after final edits: **37 passed**
- Affected regression tests after final edits: **220 passed**
- Full suite: **832 passed, 2 skipped**
- Ruff and whitespace checks after final edits: **passed**

The full suite ran before the final small tolerance-helper hardening and strict below-boundary assertion. After those edits, the targeted capital tests and affected regression suite were rerun and passed.

## Regression Results

Targeted and affected regression commands passed with no exclusions. The full suite passed with two skips.

## Git Diff Summary

Only the two permitted new files are untracked implementation artifacts, pending Orchestrator archival. The preserved `research_outputs/` tree remains untouched. No Git history operations were performed.

## Safety Checks

Projection and admission are synchronous and read-only. Tests use only in-memory processors and fake gateway methods. No live network reads, order placements, cancellations, or real-money trading occurred.

## Known Limitations

The caller must supply fresh authoritative market-token open-order evidence and independently certify market/order-state reliability. This component does not wire CapitalGate into a runtime controller or provide authenticated collateral reads.

## Deferred Findings

Runtime cash/allowance integration, controller wiring, Book Filter, market timing, metrics, and CLI remain outside this iteration.

## Reviewer Attention

Please verify close-evidence freshness and scope, confirmed-fill versus resting-reservation accounting, strict no-tolerance admission boundaries, and the fixed `$5` NORMAL reserve.

## Human Live Trading Performed

NO
