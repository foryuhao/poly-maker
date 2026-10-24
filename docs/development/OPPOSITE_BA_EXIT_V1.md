# Opposite BA exit v1

The independent `opposite_ba_v1` repair policy replaces the HD/TP/SL decision
controller when selected. The full `hd_tp_sl_v1` policy remains selectable.
Pre-development checkpoint: `fed28b4`, pushed to `origin/codex/ba-normal-v1`.

## Contract

1. An opening economic fill creates REPAIR ownership. Any positive unpaired
   residual, including <=2.5 shares, retains that ownership and blocks NORMAL.
2. A dedicated instance of the existing BidAbsorption detector consumes the
   same ordered market messages as the entry detector. Parameters and claims
   are independent; consuming an exit never consumes a future entry opportunity.
3. Net YES selects NO BA; net NO selects YES BA. The first SELL must be at or
   after the earliest known opening fill. Missing opening-time evidence blocks
   admission. Both-side conflicts, source ordering, age and reconnect rules
   remain those of BidAbsorption. Old or expired opportunities are not replayed.
4. Submit one GTC/post-only BUY per BA episode at its anchor Bid. Use the smaller
   of the residual and configured maximum order, rounded down to 0.01 shares.
   If that is below the legal minimum/notional, wait without enlarging it.
5. The deadline is the earlier of the original BA deadline and T240. Recheck
   BA claim, direction, anchor, freshness, POST/live budgets, revision, account,
   capital and Delta immediately before the native POST. Consumption happens
   at POST start, including an eventual UNKNOWN result.
6. Anchor/data invalidation, deadline or execution failure closes the order.
   Partial fills remain actual inventory. No overlap/replacement is allowed
   until the ownership barrier resolves the previous order, including UNKNOWN
   submissions and delayed fills. Then wait for a new BA if still unpaired.
7. Exact net zero plus safe order/fill/account reconciliation completes the
   round. The existing FIFO cooldown and NORMAL time/admission rules apply.
   A small residual is not a completed round. No automatic reverse leg exists.
8. No hold timeout, price/volatility exit, T180 forced exit, or high-entry-price
   abandonment applies. T240 stops new submissions; cancellation and accounting
   continue. Residual inventory remains until settlement. The conservative
   inventory-lag lease remains specific to the original price-policy actor;
   BA submission requires reconciled account evidence.

## Configuration

`[rdp_sm.repair.opposite_ba]` uses the existing `RDPBAConfig` schema, including
its `.network` subsection. Defaults are validated, independent objects. Both
configuration files declare the current entry BA starting parameters for exit:
5-share SELL/depletion/refill, 750ms observation, 3s lifetime, 500ms SELL source
age, 200ms local book/pair skew and 1000ms initial submission age. These are
starting parameters, not a claim of validated strategy profitability.

`rdp-sm-repair-check` reports the selected policy. BA readiness means local
configuration completeness only; it does not inherit price-policy calibration
requirements or claim performance validation. Capital remains the current
20-USDC session budget. Switching configuration does not restart a running bot.

## Verification

Offline tests use the existing dummy signer, real SDK and mocked HTTP/WS/venue
fixtures. Cover opposite directions, stale/pre-opening/conflicting BA, separate
entry/exit claims, exact sizing, complete/partial/no fills, repeated signals,
UNKNOWN ACK, final POST guards, T180 independence, high-price independence,
T240, small residual ownership and return to NORMAL only after actual pairing.

Final focused regression: **256 passed**, including all **26 opposite-BA tests**,
existing BA entry/admission, price policy, path execution, small residuals,
cooldown, sync companion and configuration tests. New-module Ruff and
`git diff --check` pass. Both local configuration checks pass; `config` selects
BA exits and `livecfg` selects HD/TP/SL.
An additional **72 tests passed** for queue follow-up, rejection latency,
runtime regressions and SL execution/recovery after the interrupted expanded run.

The expanded 1,834-test RDP run was **not all green**: two cached/timing cases
failed in the combined run and passed when rerun separately. The run was then
stopped at `test_repair_preparation_quote_race.py`, which did not complete.
That file also exceeded a 30-second limit in an isolated checkout of the
pre-development `fed28b4` baseline and in the new code. Its old TP preparation
hooks were not changed as part of the BA exit implementation. This is not a
claim of a completed full-suite run.

Commands use `PYTHONPATH=src:tests uv run python -m pytest`; the test modules
import shared fixtures by their module names. No live process was started or
restarted and no real order was submitted for this implementation.
