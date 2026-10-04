# P0-CM-30 Implementation Report

Status: IMPLEMENTED — AWAITING REVIEW
Commit / working tree base: `97ab0f9` (`m4a-a1-pass`), with unrelated pre-existing tracked/untracked working-tree changes preserved. Local research is uncommitted by request.
Reviewed predecessor baseline: `m4a-a1-pass` for Git. The local P0 C-M research predecessor is unreviewed and uncheckpointed.
Architecture reference: `docs/research/P0_CM_30_RESEARCH_DESIGN.md`; independent research variant without production architecture changes.

## Scope implemented

Exact frozen P0 30-market sample, each market with independent $30 initial cash; continuous probability target clamped to ±15; C-M Maker-only queue/cancel/latency behavior; full-delta order planning with cash, exit reserve, potential stop and partial-state checks; lawful pure-reduction fallback; stop-mode reduction permission; isolated Merge and redemption recovery; order/ledger/market/coverage/summary artifacts; raw-file audit and full report.

## Files added

- `research_tools/p0_cm_30.py`
- `research_tools/p0_cm_30_audit.py`
- `tests/test_p0_cm_30.py`
- `docs/research/P0_CM_30_RESEARCH_DESIGN.md`
- `docs/research/P0_CM_30_20261004.md`
- This implementation report.
- Local generated output under `research_outputs/p0_cm_30/`.

## Files modified

- `docs/CURRENT_STATUS.md`: appended a concise separate-research handoff; prior content preserved.

## Important implementation decisions

- Instantiate a fresh replay account for each market and assert final assumed-recovery cash equals `$30 + market PnL`. Do not transfer funds across markets.
- Floor new order quantity to a 0.01-share research grid; retain partial fills and legal order remainders below five shares. The historical quantity increment is not independently confirmed.
- Check cash, net cap, conservative lower bound and exit reserve at zero fill, old-net elimination, and full fill. Between these points each constraint is affine, so these checks cover all intermediate partial quantities.
- When a full reversal fails, submit a legal pure reduction first. If old net is below the 5-share order minimum, a smaller lawful flip may be considered under all the same checks.
- Keep C-M's visible-queue/same-token-SELL conditional fill model, arrival and cancel timers, Post-only rejection and no rebate. Record all public-print exchange, receipt and available clocks in fill ledger rows.
- Pair and Residual are average-cost attribution; the stop metric is separate from final PnL. A transient pre-Merge fill ledger row is not the post-Merge stop decision.

## Deviations from architecture

- NONE. The user-authorized P0-CM-30 design is research-only; no production architecture was changed.

## Tests added

Five focused tests cover cash-blocked full flip with legal pure reduction, new-risk exit reserve and downward shrink, stop-mode pure reduction, intermediate partial-fill reserve/cap checks, and legal below-minimum remaining quantity on an existing partial order.

## Tests executed

- `.venv/bin/python -m pytest -q tests/test_p0_cm_30.py tests/test_p0_maker_only.py`
- `python3 research_tools/p0_cm_30.py`
- `python3 research_tools/p0_cm_30_audit.py`
- `python3 -m py_compile research_tools/p0_cm_30.py research_tools/p0_cm_30_audit.py`
- `git diff --check` and new-file whitespace inspection.

## Test results

- Nine focused tests passed (five new, four inherited C-M lifecycle checks).
- All 30 markets retained. Conditional Total PnL +$7.24505 = Pair −$9.435 + Residual +$16.68005; 17 positive, four negative, nine zero-PnL/zero-fill markets. No −$20 stop trigger.
- Audit independently reconciled all 28 fill events with unique saved raw same-token public prints and their three clocks, all 30 market cash/PnL/Pair/Residual rows, Merge and redemption releases, order lifecycle, quantity/cap/reserve and print-volume bounds.
- Worst market `1791015900` remains a −$13.05 loss: 15 NO at $0.92, 5 YES at $0.85, $5 Merge, ten residual losing NO shares.
- This Worker report does not claim independent Review PASS.

## Known limitations / unresolved issues

The 28 fills are conditional on C-M's queue attribution, not observed hypothetical order executions; 21 occur during local cancel wait. Ten markets lack matching historical Maker fee/minimum metadata, seven of which trade. The 0.01-share order increment is a research assumption, not proven market metadata. Merge/redemption delays are assumptions; unknown chain costs/failures are unpriced. The arithmetic PnL sum across 30 isolated accounts cannot establish continuous $30 account feasibility or positive long-run edge. C-M comparison changes four dimensions simultaneously, preventing one-factor attribution.

## Safety / behavior impact

Live trading semantics changed: **NO**. Only research files and status note changed. No production edit, real order, external data request, credential/account access, commit, tag, or push.

## Git diff summary

This task adds separate P0-CM-30 research source, focused tests, design/report documents, independent local audit and generated outputs; it appends one handoff paragraph. Original P0, C-M and path-diagnostic files/outputs remain intact. All unrelated pre-existing modifications are preserved.

## Handoff to Reviewer

Inspect exact sample identity, 0.01 precision assumption, partial-state cash/exit-reserve/stop proof, stop-mode order behavior, pure-reduction classification, queue and cancel race clocks, Merge release timing, all-market accounting, and the `1791015900` conditional YES print (257.19 shares versus 252.19 visible queue ahead). Review only the exact local working tree and artifacts; no PASS or checkpoint is claimed.
