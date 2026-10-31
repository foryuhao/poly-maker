# Opposite BA exit v1

The independent `opposite_ba_v1` repair policy replaces the HD/TP/SL decision
controller when selected. The full `hd_tp_sl_v1` policy remains selectable.
Pre-development checkpoint: `fed28b4`, pushed to `origin/codex/ba-normal-v1`.

## Contract

`[rdp_sm.repair] ba_enabled` defaults to `true` for existing configurations.
The current deployment configuration explicitly sets `false`. When disabled,
BA detection/auditing continues but selection, ordinary REPAIR preparation,
native POST validation and both initial/restored TP handoffs reject BA orders
with `BA_REPAIR_DISABLED`. CA opening and TP pricing/quantity rules are unchanged;
ordinary REPAIR can still restore its default TP when configured. Existing order,
fill and account close proofs remain mandatory. Configuration/code is loaded at
process start; changing the file does not hot-reload an already running process.

`[rdp_sm.ca_matched_tp] ttl_sec` defaults to 15 seconds from the first CA TP's
native POST start. At expiry, cancel the unfilled TP and retain the full close
proof barrier, including late/partial fills. The deadline is attached to the
opening order's FIFO lots; restored TP orders inherit it without resetting it.
Expired CA lots cannot cause ordinary REPAIR to repost a TP. Reverse BA lots
remain independent, and an already accepted BA retains its own signal deadline.

1. An opening economic fill creates REPAIR ownership. Both exit policies use
   the existing inclusive 2.5-share completion threshold. Actual quantities and
   acquisition costs remain in the ledger even when the round completes.
2. A dedicated instance of the existing BidAbsorption detector consumes the
   same ordered market messages as the entry detector. Parameters and claims
   are independent; consuming an exit never consumes a future entry opportunity.
3. Net YES selects NO BA; net NO selects YES BA. The first SELL must be at or
   after the earliest known opening fill. Missing opening-time evidence blocks
   admission. NORMAL retires both opportunities when both sides have valid BA.
   REPAIR evaluates only the net position's opposite outcome: a valid held-side
   BA does not veto, consume or invalidate that exit. Waiting diagnostics use
   the same outcome scope. Source ordering, age, anchor and reconnect checks
   remain those of BidAbsorption. Old or expired opportunities are not replayed.
4. Submit one GTC/post-only BUY per BA episode at its anchor Bid. The shared
   `ba_repair_buy_quantity` function adds a 1 USDC minimum to shared repair
   sizing: round up the residual and legal minimum/notional to 0.01 shares, require reduced
   absolute Delta and the existing Delta/capital/max-order admission. A true
   Delta breach may use a feasible partial recovery. Smaller reverse residuals
   are allowed up to 2.5 shares inclusive; a larger reverse residual, or an
   equal or larger absolute residual, is rejected. If a TP handoff requires
   overbuy, cancel and fully reconcile the old TP before replanning the BA
   from remaining inventory. No BA deadline is extended by this close barrier.
5. The deadline is the earlier of the original BA deadline and T240. Recheck
   BA claim, direction, anchor, freshness, POST/live budgets, revision, account,
   capital and Delta immediately before the native POST. Consumption happens
   at POST start, including an eventual UNKNOWN result.
6. Anchor/data invalidation, deadline or execution failure closes the order.
   Partial fills remain actual inventory. No overlap/replacement is allowed
   until the ownership barrier resolves the previous order, including UNKNOWN
   submissions and delayed fills. Then wait for a new BA if repair is still
   required. An active order is closed when actual residual reaches <=2.5 shares
   or its direction reverses; that close still requires full reconciliation.
7. Residual <=2.5 plus safe order/fill/account reconciliation completes the
   round. Existing FIFO cooldown and NORMAL time/admission rules apply. A larger
   reverse residual uses `install_reverse_episode` after the ownership barrier,
   retaining FIFO cost and the same cooldown round. Its mode is WAIT_OPPOSITE_BA,
   and it waits for a fresh opposite BA from the residual opening-fill time.
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

BA exit sizing metadata supplies the same reporting contract as other REPAIR
controllers: `signed_target_delta="0"`, `target_sizing_applied=false`, and
`favorite_data_validity="NOT_USED"`. These fields describe the zero-target exit;
they do not introduce price or Favorite signals. The common M3 inventory/PnL
projection consumes this metadata after every exit outcome, including rejection.
Omitting it previously raised `KeyError: signed_target_delta` after an otherwise
reconciled BA exit window and stopped the session with its actual residual.

`out_of_order_tolerance_ms` defaults to `10.0` and is declared independently in
`[rdp_sm.ba]` and `[rdp_sm.repair.opposite_ba]` in both local configurations.
Source-time regressions up to and including the configured gap are discarded
without invalidating existing BA; larger regressions keep the existing invalidation
and close behavior. Zero enables strict detection. Late depth is discarded before
updating shared books, even when the entry/exit tolerances differ. Ignored messages
cannot move the source watermark backwards, refresh a book or SELL clock, contribute
SELL/depletion/refill evidence, or extend the original BA/order deadline.
Diagnostics distinguish `BA_EVENT_OUT_OF_ORDER_IGNORED` from invalidating
`BA_EVENT_OUT_OF_ORDER`, with the actual rollback and configured tolerance in ms.

## Verification

Ordering-tolerance regression: **404 tests passed**, including **45 new tests**
covering late full books, depth, trades and BBO in entry and exit detectors,
inclusive boundaries, configurable strict mode, independent settings, preserved
books/evidence/clocks, reconnect and native SDK guards/active orders. The old
implementation failed the new late-snapshot test by overwriting current depth.
Configuration loading, changed-file Ruff and `git diff --check` passed.

Phase-specific conflict regression: **49 opposite-BA tests passed**, including
both inventory directions, either order of BA confirmation, NORMAL conflict
rejection, no held-side fallback, waiting diagnostics, SDK execution/completion
and both-sided exits through the M3 loop with and without cached reads. Five
new selection/diagnostic cases failed on the previous implementation before
the fix. Another **400 related tests passed** across BA entry/network admission,
shared sizing/reporting, small residuals, the original price path, OBI execution
and cooldown. Ruff on the changed Python files and `git diff --check` passed.

Offline tests use the existing dummy signer, real SDK and mocked HTTP/WS/venue
fixtures. Cover opposite directions, stale/pre-opening/conflicting BA, separate
entry/exit claims, shared sizing, complete/partial/no fills, repeated signals,
UNKNOWN ACK, final POST guards, T180 independence, high-price independence,
T240, inclusive residual completion and FIFO handoff of large reverse residuals.
Six additional M3 coordinator tests cover real BA entry followed by rejected
exit/retry, partial exit and full exit, each with and without the execution cache.
They exercise inventory reporting, continuation and human rendering together;
the rejected-exit case reproduced the missing-field exception before the fix.
Reporting-fix regression: **332 passed**, including the six new coordinator
cases, BA quantity/admission, common sizing/PnL projection, CLI rendering and
small-residual tests. The original 362-test quantity regression remains a
separate run; this fix adds reporting metadata without changing execution rules.

Focused regression: **362 passed**, including all **32 opposite-BA tests**.
Coverage includes 0.005-share tails, the inclusive 2.5-share boundary, legal
minimum overbuy, large reverse residuals, fresh BA after FIFO handoff and actual
cost conservation. The same run covers existing BA entry/admission, price
policy/path execution, SL execution/recovery, small residuals, cooldown, MATCHED
order tolerance, inventory precision, known dust and sync companion tests.
New-module Ruff and `git diff --check` pass. `config` selects BA exits and
`livecfg` selects HD/TP/SL; their configuration checks pass.

The earlier expanded 1,834-test RDP run was **not all green**: two cached/timing cases
failed in the combined run and passed when rerun separately. The run was then
stopped at `test_repair_preparation_quote_race.py`, which did not complete.
That file also exceeded a 30-second limit in an isolated checkout of the
pre-development `fed28b4` baseline and in the new code. Its old TP preparation
hooks were not changed as part of the BA exit implementation. This is not a
claim of a completed full-suite run.

Commands use `PYTHONPATH=src:tests uv run python -m pytest`; the test modules
import shared fixtures by their module names. No live process was started or
restarted and no real order was submitted for this implementation.
