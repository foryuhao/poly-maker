# Single Market Pilot

Release date: 2026-10-12 (Asia/Shanghai).

This release captures the current RDP-SM single BTC 5-minute market pilot. The
entry point is `uv run polymaker rdp-sm-run`. Each invocation pins one market,
uses the existing M1/M3/M2 and FL Gateway path, closes its orders, and exits.

## Trading schedule

| Market elapsed time | Behavior |
| --- | --- |
| 0 <= T < 15 seconds | Wait in the selected market; do not submit orders. |
| 15 <= T < 180 seconds | Allow NORMAL and REBALANCE Maker BUY orders. |
| 180 <= T < 300 seconds | Allow only REBALANCE. NORMAL waits for market end, including after T=240. |
| T >= 300 seconds | Stop new orders; finish cancellation, terminal verification and fill reconciliation, then exit. |

NORMAL means `abs(ConfirmedYES - ConfirmedNO) < 5`. Only confirmed fills update
strategy inventory. NORMAL uses a dual-leg post-only batch; REBALANCE buys only
the inventory-deficient side. An unresolved close barrier blocks another window.

## Included fixes

- Validate complete safe zero-order history and expired-market evidence without
  deleting or rewriting historical Journals. Retain unresolved current-session
  and UNKNOWN placement protections.
- Use the official account balance and applicable allowance with a 30 USDC test
  cap. Pre-session commitments are operator-managed in the CLI; current-session
  exposure remains subject to M2 accounting and reconciliation.
- Skip both NORMAL legs if either BUY notional is below 1 USDC. Increase a
  REBALANCE leg to the minimum legal share quantity covering at least 1 USDC.
- Accept less than 0.1 share remaining on a verified MATCHED order for terminal
  checking; preserve actual fill quantities and confirmed-only inventory.
- Perform account admission before final book reads. Reuse a revision-valid
  previous close proof; new trade facts or terminal contradictions invalidate it.
- Keep the 3-second fill window and post-only submission guards. Distinguish
  planned quotes, accepted orders, rejections, confirmed fills and UNKNOWN in
  human logs; retain the JSON/Journal output paths.

## Verification

- RDP-SM offline regression: **530 passed**.
- FL execution, FillTruth and positions regression: **117 passed**.
- Ruff and Git whitespace checks: passed.
- Offline guards: **0 network/DNS attempts**; workspace credentials denied.
- No real orders were executed while preparing or publishing this release.

Earlier development and review reports are preserved as historical milestone
records. The schedule and funding rules above describe this release snapshot.
