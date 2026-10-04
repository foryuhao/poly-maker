# P0 C-M Maker-only Implementation Report

Status: IMPLEMENTED — AWAITING REVIEW
Commit / working tree base: `97ab0f9` (`m4a-a1-pass`), with extensive pre-existing unrelated tracked/untracked work preserved. C-M remains local and uncommitted by request.
Reviewed predecessor baseline: `m4a-a1-pass` for Git; local P0 C research outputs are the frozen, unreviewed comparison (`research_outputs/p0_probability_inventory/`).
Architecture reference: `docs/research/P0_MAKER_ONLY_RESEARCH_DESIGN.md`; independent research variant, no production architecture change.

## Scope implemented

Exact P0 30-market sample, Maker-only GTC Post-only BUY adjustment, 500 ms submit and 500 ms assumed cancel lifecycle, initial visible queue ahead, same-token SELL volume consumption, partial fills, post-only rejection, cancel races, one order at a time, full remaining-quantity reserve and potential-net/risk checks, causal book probability, fixed target and stop, Merge/redemption delays, per-order/market ledger and comparison to frozen P0 C.

## Files added

- `research_tools/p0_maker_only.py`
- `tests/test_p0_maker_only.py`
- `docs/research/P0_MAKER_ONLY_RESEARCH_DESIGN.md`
- `docs/research/P0_MAKER_ONLY_20261004.md`
- This implementation report.
- Local untracked runtime outputs under `research_outputs/p0_maker_only/`.

## Files modified

- `docs/CURRENT_STATUS.md`: concise C-M handoff appended to the already modified file. Existing content preserved.

## Important implementation decisions

- The P0 `sample_coverage.csv` is authoritative for the 30 ordered sessions and is checked against the saved replay audit.
- At accepted arrival, the complete visible same-price bid quantity is placed ahead. A public print's size is consumed once, first ahead then own remaining. Unknown queue cancellations never grant position improvement.
- A cancel request can precede exchange arrival; the order can still arrive and fill before cancel confirmation. The cancel timer is independent of submit timer.
- Post-only crossing at arrival is rejected, not filled. No Maker rebate, Taker rebate, or Taker fallback is credited.
- The 20 saved historical fee/minimum metadata snapshots are identity checked. The ten later missing snapshots are marked assumptions, not silently treated as verified.
- Realized Pair, final settlement, and conservative zero-residual stop metric are separate ledger fields.

## Deviations from architecture

- NONE. This research variant follows its fixed design; no M4 production architecture was changed.

## Tests added

Four focused tests cover queue-ahead consumption with partial fills, the post-only crossing gate, an end-to-end synthetic cancel-race fill, and a stale-book timer that requests cancellation before the next public event.

## Tests executed

- `.venv/bin/python -m pytest -q tests/test_p0_maker_only.py tests/test_p0_probability_inventory.py`
- `python3 research_tools/p0_maker_only.py`
- Independent 30-market ledger/attribution/cash/net-cap audit; order lifecycle and print-volume conservation audit.
- `python3 -m py_compile research_tools/p0_maker_only.py` and `git diff --check`.

## Test results

- 7 tests passed (4 C-M, 3 reused P0 primitive checks).
- All 30 rows reconcile Total PnL with fill spend, separate fees, Merge, and residual settlement; Pair + Residual equals Total.
- Every recorded own fill is at most the print volume remaining after the prior visible queue; 2,169 submitted = 383 rejected + 1,786 accepted, and 1,786 accepted = 1,766 canceled + 20 fully filled.
- No negative cash, net cap breach, or local timestamp inversion was found. No global stop was triggered; minimum logged conservative metric was −$19.2725.
- This Worker report does not claim independent Review PASS.

## Known limitations / unresolved issues

The public tape does not identify hypothetical queue position or own fills, and complementary matching is not fully represented by same-token SELL prints. Sixteen of 23 fill events occur during cancel delay, so the conditional result is timing-sensitive. Five of ten markets lacking saved fee/minimum metadata trade and contribute −$18.0925. Maker fee zero there is assumed. Merge/redemption delays and zero chain cost/failure are assumptions. Tracking error is decision-observation weighted. C-M's 17 traded markets and P0 C's three prevent a simple causal attribution of total PnL difference to order type.

## Safety / behavior impact

Live trading semantics changed: **NO**. Research-only script and documents; no production strategy edits, live order, credential/account access, external data request, commit, tag, or push.

## Git diff summary

Only new C-M research source/test/documents and one durable status note belong to this task. The prior P0 source, outputs, and documents remain intact. Generated C-M artifacts remain local and untracked. Unrelated pre-existing working-tree edits are preserved.

## Handoff to Reviewer

Inspect event/timer order, queue consumption, cancel races, stop metric, reserve/merge/redemption accounting, fee-metadata flags, and representative prints. Review applies only to the exact local source and output state. Do not infer live authority or a PASS checkpoint from this Worker report.
