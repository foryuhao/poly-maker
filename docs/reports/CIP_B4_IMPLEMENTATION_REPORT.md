# CIP B4 Implementation Report

Status: **IMPLEMENTED — AWAITING REVIEW**
Commit / working tree base: `97ab0f9` on `m4-dynamic-ev`; unrelated pre-existing modified and untracked files preserved.
Reviewed predecessor baseline: `m4a-a1-pass` (`97ab0f9`) for Git; B2 research script/output are the frozen local comparator but have no independent reviewed checkpoint.
Architecture reference: User's B4 specification, `docs/research/CIP_B4_RESEARCH_DESIGN.md`; no M4 production architecture change.

## Scope implemented

Research-only B4 replay of the exact B2 32-market universe. A Phase 1 wrapper blocks new five-share risk-increasing BUY intent beyond absolute net five and requests heavy-side cancellation at the threshold. The B2 class retains the entire Phase 2 method, book reconstruction, event ordering, latency, maker fill and accounting. Added guardrail events, Phase 1 time-weighted exposure, overshoot diagnostics and paired B2 economics. Wrote the full result in `docs/research/CIP_B4_BACKTEST_20261004.md`.

## Files added

- `research_tools/cip_b4_residual_budget_backtest.py`
- `docs/research/CIP_B4_RESEARCH_DESIGN.md`
- `docs/research/CIP_B4_BACKTEST_20261004.md`
- `docs/reports/CIP_B4_IMPLEMENTATION_REPORT.md`
- Ignored local output under `research_outputs/cip_b4_20261004/`: `summary.json`, `market_results.csv`, `orders.csv`, `fills.csv`, `inventory_paths.csv`, `guardrail_events.csv`, `action_audit.csv`, plus inherited B2 `phase_transitions.csv`.

## Files modified

- `docs/CURRENT_STATUS.md` handoff entry only. No production trading code, tests, configuration or B2 research source changed by B4.

## Important implementation decisions

- The script subclasses the research-only B2 `TwoPhaseReplay` and binds it into B2's existing loader/main for the exact same 32 audited market paths. Its Phase 2 `reconcile` branch delegates unchanged to B2. B4 only guards Phase 1 placement and asks to cancel the current heavy-side order if one exists when the budget activates.
- Original book-invalid cancel-all semantics run through B2. The new guardrail cancellation reason is excluded from `BBO_ONLY_REPRICE`; a budget cancellation is inventory-driven. In the observed path, all threshold activations followed full-clip heavy-side fills, leaving no same-side order to cancel. The branch remains implemented and independently reviewable.
- Outstanding orders retain B2 submit/cancel timing and strict maker fill eligibility. Phase 1 overshoots are measured rather than suppressed. B4 observed none; Phase 2 observed one inherited cancel-race risk-increasing fill after a zero-net stop.
- B2's CSV helper uses the first row's fields and can discard late optional Reduce audit fields. B4 wraps only output writing with a union of row keys; this changes output completeness, not replay behavior. The corrected `fills.csv` retains `risk_increase_shares` and `reason_code` for the Phase 2 race.
- The B2/B1 executable gamma source has an inherited `matched>=TARGET` balanced-state stop around 25 matched shares even though some B2 prose says “no pair cap.” B4 preserves the executable B2 rule for path comparability and records the tension for independent review.

## Deviations from architecture

- NONE from the executable B2 comparator and B4's sole requested mechanism. The inherited B2 matched-target prose/source conflict is explicitly recorded above; removing it in B4 would create an additional strategy change.

## Tests added

- NONE; this is a replay research script. Direct output and invariant checks are documented below.

## Tests executed

- `python3 -m py_compile research_tools/cip_b4_residual_budget_backtest.py`.
- Full `python3 research_tools/cip_b4_residual_budget_backtest.py` on all 32 markets, twice; the second run preserves all optional fill-audit CSV fields.
- Independent CSV audit of market set, exact T150 transition times, frozen capital, each fill's strict trade-through price and three post-activation timestamps, every Phase 1 active placement projection, every Phase 2 placement side, book-invalid cancellation validity, zero BBO-only reprices, per-market PnL and residual accounting.
- `git diff --check`, new-file whitespace check and output file/row audit.

## Test results

The completed replay reports 32 markets, 166 Gamma fills, T150 residual 110, no Phase 1 overshoot, 16 Reduce fills, final residual 40, GammaPnL −$26.9617, ResidualPnL −$15.3083 and TotalPnL −$42.27. B2 TotalPnL was −$69.42. `BBO_ONLY_REPRICE=0`, `phase1_guardrail_intent_violations=0`, all 32 T150 transition timestamps exact, capital ≤$25, and no data/invalid-action error. Classification is `RESIDUAL CONTROL WORKS, STRATEGY STILL NEGATIVE`. No independent Reviewer PASS is claimed.

## Known limitations / unresolved issues

- The inherited maker fill rule has no queue model or public-print-size partial-fill inference. This is replay evidence only.
- Only 19 of 32 selected markets overlap stricter M4A telemetry integrity PASS.
- The local B2 research comparator remains unreviewed/uncheckpointed, and its inherited matched-target source/prose tension needs review.
- The result improves across many markets but still loses $42.27 overall and sacrifices substantial matched-edge quality.

## Safety / behavior impact

Live trading semantics changed: **NO**. The script reads saved local replay data and cached settlement labels only, writes local research output, performs no network call and sends no order.

## Git diff summary

Research-only B4 source, design, result report, implementation report and a concise status handoff were added/updated. Generated outputs are ignored locally. Existing unrelated dirty files remain untouched. No commit, tag or push was made.

## Handoff to Reviewer

Review the Phase 1 placement projection and heavy-side cancellation behavior, especially the no-cancel observed path; verify inherited B2 Phase 2 and book-invalid behavior, the one Phase 2 cancel-race fill, all 32 T150 snapshots, and the matched-target source/prose tension. Check tracked and untracked artifacts. B4 is implemented and awaits independent review; no PASS checkpoint exists.
