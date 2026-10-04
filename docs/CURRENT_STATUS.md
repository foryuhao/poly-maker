# Current Project Status

## Active MakerInventory work

Current program: MakerInventory (MI) Baseline 1.0
Current milestone / slice: M2A — Live Wiring / NO-SEND, final validation complete
Status: M2A CLOSED / PASS after M2A.1 Re-review and final third NO-SEND market; this document is part of the reviewed M2A checkpoint. M1 remains CLOSED / PASS. No MI send-enabled authority exists.
Canonical predecessor baseline: MI M1 checkpoint `5befd9ee6eb27293bb6f6d885fbe458ea44cabe4` on `m4-dynamic-ev`.
Canonical M2A baseline: `mi-m2a-reviewed-baseline` tag on the checkpoint commit containing this status entry; verify the tag and branch on remote before using it.
Latest architecture: `docs/architecture/MI_ARCHITECTURE.md` (M2A NO-SEND supplement records the new user authorization; broader reconstructed design remains a draft).
Latest implementation report: `docs/reports/MI_M2A_IMPLEMENTATION_REPORT.md` (M2A.1 repair and final third-market observation appended).
Latest review: `docs/reviews/MI_M2A_REVIEW.md` (initial NEEDS FIX, M2A.1 Re-review PASS, final NO-SEND market PASS); M1 review: `docs/reviews/MI_M1_REVIEW.md`.
Current blocker: M2B design and real execution require separate architecture and authorization; this checkpoint grants no MI trading authority.
Next authorized action: Verify the pushed M2A checkpoint, then separately plan M2B design from `mi-m2a-reviewed-baseline`.
Explicitly unauthorized next actions: MI real order submission or cancellation, M2B implementation without separate authorization, additional M2A market observations, Merge, Settlement, Trial/W_trial, and any M1 strategy-semantic change.
Pre-existing dirty files retained: `livecfg/config.toml` and local `research_outputs/`

## Prior M4 program state

Current program: M4 — Dynamic Exit EV Research
Current milestone: M4A — Research Telemetry
Completed slice: M4A.5 — Audit + Acceptance Harness
Current completed iteration: External Interface Cleanup M1 — Trading Core Only
Current implementation: FavoriteFirst Data API startup baseline, removal of on-chain position override, and disabled live auto-merge
Status: IMPLEMENTED — AWAITING INDEPENDENT REVIEW; M4A-A1 identity REVIEW PASS — CHECKPOINTED
Review: PASS
Implementation status: IMPLEMENTED — INDEPENDENT RE-REVIEW PASS
M4A implementation: COMPLETE / CHECKPOINTED
M4A Acceptance Preflight: BLOCKED
Actual M4A readiness: NOT READY
Reason: The A1 identity repair and P1 context-validation repair passed independent re-review. Real FIRST-fill verification and trusted LIVE, callback latency, incremental loop-lag and isolation evidence remain outstanding.

Canonical predecessor baseline: `m4a4-pass` (`31dd22c`).
Canonical current baseline: `m4a-a1-pass`.
Reviewed checkpoint: `m4a-a1-pass`.
External Interface Cleanup M1 predecessor: `m4a-a1-pass` (`97ab0f9`); this implementation remains unreviewed. The pre-MI Git archival checkpoint does not confer a Review PASS.
Canonical predecessor checkpoint: `m4a5-pass` (`4d75a19`).
Stable rollback: `m3c-settlement-live-pass` (`fff2842`).

Latest research: `docs/research/DYNAMIC_EV_EXIT_RESEARCH_DESIGN.md`.
Latest architecture: `docs/architecture/M4A_ARCHITECTURE.md`.
M4A-A1 implementation: `docs/reports/M4A_A1_IMPLEMENTATION_REPORT.md`.
Latest review: `docs/reviews/M4A_A1_REVIEW.md` (initial NEEDS FIX; re-review PASS for the reviewed A1 working tree).
Latest implementation: `docs/reports/EXTERNAL_INTERFACE_CLEANUP_M1_IMPLEMENTATION_REPORT.md` (awaiting independent Review).
Additional requested implementation: FavoriteFirst entry bounds are now profile-backed (`favorite_min` / `favorite_max`) with unchanged 0.60–0.70 defaults; see `docs/reports/FAVORITE_ENTRY_RANGE_CONFIG_IMPLEMENTATION_REPORT.md` (awaiting independent Review).
Acceptance preflight: `docs/reports/M4A_ACCEPTANCE_PREFLIGHT.md` (BLOCKED).
Acceptance architecture: AE-1 in `docs/architecture/M4A_ARCHITECTURE.md`.

The terminal material-snapshot audit compares FavoriteFirst decision content against the causal event prefix. Dataset enumeration and caller-evidence provenance remain fail-closed. No real M4A collection or verified peak-traffic evidence was audited; actual readiness remains NOT READY.

M4A.4 reviewed checkpoint: implementation `08e4186`, separate governance `7a51272`, status `31dd22c`, tag `m4a4-pass`. M4A.5 test/check results and limitations are recorded in its Implementation Report.
M4A.5 reviewed implementation commit: `d375830`. Its separate checkpoint-status commit is included in `m4a5-pass`; neither commit establishes actual M4A readiness.

Checkpoint history: `m4a2-pass` is historical and incomplete; `m4a2-reviewed-baseline` is the corrected M4A.2 baseline. `m4a3-pass` contains the reviewed M4A.3 tree before ancestry repair; `m4a3-reviewed-baseline` is the canonical M4A.4 predecessor. `m4a4-pass` is the canonical M4A.5 predecessor. Preserve all historical tags.

Current blocker: External Interface Cleanup M1 awaits independent Review. The pre-MI Git checkpoint only preserves the current completed work; its unreviewed changes have no live or milestone approval. No live run is part of this task. Actual M4A readiness remains NOT READY; a real FIRST-fill identity audit and preflight evidence B–E are still outstanding. Historical M4A.5 implementation PASS remains valid for its reviewed scope.
Next authorized action: Independent Review of External Interface Cleanup M1; no live run is authorized by this iteration.
Purpose: Remove third-party RPC authority from FavoriteFirst startup, live position reconciliation and automatic merge.
Real multi-day collection: NOT YET AUTHORIZED.
Live test: Not run in External Interface Cleanup M1.
M4B authorization: NO.
M4B: UNAUTHORIZED.
Explicitly unauthorized next actions: multi-day real collection, new trading behavior, M4B implementation before real M4A engineering acceptance.

Separate BTC5m B3 research replay (2026-10-03): **IMPLEMENTED — AWAITING REVIEW**; result `NO MATERIAL BENEFIT VS B2`. See `docs/research/CIP_B3_BACKTEST_20261003.md` and `docs/reports/CIP_B3_IMPLEMENTATION_REPORT.md`. The local B2 research predecessor is unreviewed/uncheckpointed; no B3 PASS tag or live authority exists. Next B3 action: independent review of Phase 1 path divergence and two valid-book cancel-race fills. Do not infer authorization for another strategy change from this research result.

Separate B2 Reduce quote attribution (2026-10-04): **IMPLEMENTED — AWAITING REVIEW**; result `MIXED EXECUTION BOTTLENECK`. Read `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_20261004.md` and `docs/reports/B2_REDUCE_QUOTE_ATTRIBUTION_IMPLEMENTATION_REPORT.md`. B2 strategy and production code were not changed. Next action for this analysis: independent review of the 14 capital-feasible historical opportunity paths; no requote implementation is authorized.

Separate B2 maker frontier attribution (2026-10-04): **IMPLEMENTED — AWAITING REVIEW**; result `BEST BID IS NEAR MAKER EXECUTION FRONTIER`. See `docs/research/B2_REDUCE_MAKER_FRONTIER_ATTRIBUTION_20261004.md` and `docs/reports/B2_REDUCE_MAKER_FRONTIER_IMPLEMENTATION_REPORT.md`. The 89 no-fill B2 orders had 14 capital-valid prior best-bid opportunities, 7 instant dynamic-frontier opportunities, and zero frozen-latency dynamic-frontier opportunities. The dynamic frontier can lose a prior persistent-bid fill when the legal frontier moves down; no strategy or live change is authorized. Next action for this analysis: independent review of those seven lost paths and the zero-hit latency sequence.

Separate BTC5m B4 residual-budget research (2026-10-04): **IMPLEMENTED — AWAITING REVIEW** on local, unreviewed B2 and Git base `m4a-a1-pass` (`97ab0f9`). Result `RESIDUAL CONTROL WORKS, STRATEGY STILL NEGATIVE`: 32 markets, T150 residual 110 versus B2 380, final residual 40 versus 245, TotalPnL −$42.27 versus −$69.42, with 166 versus 238 Phase 1 fills and zero Phase 1 overshoots. See `docs/research/CIP_B4_BACKTEST_20261004.md`, `docs/research/CIP_B4_RESEARCH_DESIGN.md`, `docs/reports/CIP_B4_IMPLEMENTATION_REPORT.md`, and ignored `research_outputs/cip_b4_20261004/`. Next B4 action: independent review of the guardrail's B2-only strategy delta and inherited matched-target source/prose tension. No production code, live authority or checkpoint changed.

Separate BTC5m Grid Gamma G0 research (2026-10-04): **RESEARCH COMPLETE — AWAITING INDEPENDENT REVIEW** on Git base `m4a-a1-pass` (`97ab0f9`), with unrelated working-tree changes preserved. Fixed 5¢ YES-mid grid on the same 32 execution-replayable markets found 1,921 crossings and 740 rolling opposite-direction pairs, but 713 pairs had zero theoretical edge; result **NO MATERIAL GRID EDGE**. See `docs/research/GRID_GAMMA_G0_RESEARCH_DESIGN.md`, `docs/research/GRID_GAMMA_G0_20261004.md`, `docs/reports/GRID_GAMMA_G0_IMPLEMENTATION_REPORT.md`, and local `research_outputs/grid_gamma_g0_20261004/`. No production code, orders, position simulation, external data, or checkpoint changed. Next G0 action: independent review of crossing/censoring/pair accounting; no maker execution test is authorized by this result.

Separate P0 probability-following BUY ONLY inventory research (2026-10-04): **IMPLEMENTED — AWAITING INDEPENDENT REVIEW** on Git base `m4a-a1-pass` (`97ab0f9`), with pre-existing working-tree changes preserved. Fixed earliest 30 replayable BTC5m markets; A ideal original mapping +$25.772, B ideal 5× mapping +$68.245, C conditional taker/$100 replay −$6.799 after the conservative −$20 interim-loss stop allowed trades in only the first three markets. See `docs/research/P0_PROBABILITY_INVENTORY_RESEARCH_DESIGN.md`, `docs/research/P0_PROBABILITY_INVENTORY_20261004.md`, `docs/reports/P0_PROBABILITY_INVENTORY_IMPLEMENTATION_REPORT.md`, and local untracked `research_outputs/p0_probability_inventory/`. No production change, network call, live order, commit, tag, or push. Next P0 action: independent review of event ordering, stop accounting, fee assumption, and ledger; no parameter change or live authority is authorized by this result.

Separate P0 C-M Maker-only inventory research (2026-10-04): **IMPLEMENTED — AWAITING INDEPENDENT REVIEW** on Git base `m4a-a1-pass` (`97ab0f9`), using the exact local P0 30-market sample without altering P0 outputs. Conditional visible-queue/same-token-SELL replay: Total PnL −$0.6148, Pair −$1.995, Residual +$1.3802, 17 traded markets, 13 zero-trade markets, no −$20 stop trigger; minimum logged conservative metric −$19.2725. Sixteen of 23 fill events occur during cancel wait and five of ten markets with missing historical fee/minimum snapshots trade, so actual execution PnL is not established. See `docs/research/P0_MAKER_ONLY_RESEARCH_DESIGN.md`, `docs/research/P0_MAKER_ONLY_20261004.md`, `docs/reports/P0_MAKER_ONLY_IMPLEMENTATION_REPORT.md`, and local untracked `research_outputs/p0_maker_only/`. No production change, real order, network request, commit, tag, or push. Next C-M action: independent review of queue and cancel timing, risk/cash ledger, and metadata assumptions; no tuning or live authority is authorized.

Separate P0 C-M path diagnostic (2026-10-04): **IMPLEMENTED — AWAITING INDEPENDENT REVIEW** on the frozen local C-M output and Git base `m4a-a1-pass` (`97ab0f9`). All 23 fill events reconcile to causal local public prints and the ledger: 22 reduced target error, one unchanged, zero increased/opposed target direction; 16 occurred during cancel wait. `1791015900`'s −$19.55 came from a target-consistent 21.25 NO fill followed by no YES fill, with one legal YES order canceled on invalid book and subsequent full-delta attempts blocked by the fixed potential-stop-risk gate. The conditional queue assumption and missing historical Maker fee/minimum snapshot prevent an actual-execution PnL claim. See `docs/research/P0_MAKER_PATH_DIAGNOSTIC_20261004.md`, `docs/reports/P0_MAKER_PATH_DIAGNOSTIC_IMPLEMENTATION_REPORT.md`, `research_tools/p0_maker_path_diagnostic.py`, and local `research_outputs/p0_maker_path_diagnostic_20261004/`. Next action: independent review of this diagnostic; no strategy change or new replay authorized by it.

Separate P0-CM-30 independent-$30 market research (2026-10-04): **IMPLEMENTED — AWAITING INDEPENDENT REVIEW** on Git base `m4a-a1-pass` (`97ab0f9`) and the original P0 30-market sample. Conditional Total PnL +$7.24505 across 30 separately reset accounts (Pair −$9.435, Residual +$16.68005); 17 profitable, four losing, nine zero-fill; no −$20 stop. `1791015900` remains −$13.05 after 15 NO and a conditional 5 YES pure-reduction fill. Twenty-one of 28 fills occur during local cancel wait; ten markets lack historical Maker fee/minimum snapshots and the 0.01-share quantity increment is unverified. This does not establish continuous-$30 feasibility or actual executable edge. See `docs/research/P0_CM_30_RESEARCH_DESIGN.md`, `docs/research/P0_CM_30_20261004.md`, `docs/reports/P0_CM_30_IMPLEMENTATION_REPORT.md`, `research_tools/p0_cm_30.py`, `research_tools/p0_cm_30_audit.py`, and local `research_outputs/p0_cm_30/`. Next action: independent review of planner, queue clocks, and accounting; no tuning, sample expansion, production change, or live authority is authorized by this result.
