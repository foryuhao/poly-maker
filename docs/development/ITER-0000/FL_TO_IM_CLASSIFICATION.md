# Canonical FL → Preserved IM Change Classification

FL: fl-reconstructed-baseline / 76fe93ffeb8cc1f8ca440587ab901ea402221228.
IM: im-paused-20261007 / a03b59fd0b3c42955029193b30893a395e03dfa4.
Actual Git diff includes source, tests and documentation on divergent branches, including readiness files existing only on reconstructed FL. D means confirmed eligible shared-core; NONE accepted. A excludes IM-specific code; B excludes unfinished/uncertain/unrelated differences; C is candidate review only. Documentation/evidence differences do not imply runtime reuse. This replaces the old candidate-relative classification for future baseline decisions while preserving its historical record.

| Diff | Path | Bucket | Disposition |
|---|---|---|---|
| M | `README.md` | A | IM-specific source/tests/program integration; exclude. |
| M | `config/strategy.toml` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| M | `docs/CURRENT_STATUS.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/architecture/MI_ARCHITECTURE.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/architecture/MI_M3_ARCHITECTURE.md` | A | IM-specific source/tests/program integration; exclude. |
| D | `docs/development/BASELINE.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/01_READINESS_AUDIT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/FL_PROVENANCE_RECONSTRUCTION.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/RESULT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/INITIAL_BLOCKED_01_READINESS_AUDIT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/INITIAL_BLOCKED_RESULT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/PRE_RECONSTRUCTION_BASELINE.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/PRE_RECONSTRUCTION_RESULT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/branch` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/capture-manifest-inventory.json` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/data-inventory.json` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/file-sha256.json` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/fl-provenance-correlation.json` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/fl-reconstruction.patch` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/head` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/staged.patch` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/status` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/tracked.patch` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/unexplained-config.patch` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preservation/untracked.txt` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/.codex/config.toml` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/.codex/tmp/agents/architect.toml` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/.codex/tmp/agents/reviewer.toml` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/.codex/tmp/agents/worker.toml` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/AGENTS_RDP.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/README.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/docs/history/AGENTS_LEGACY_PRE_RDP.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| D | `docs/development/ITER-0000/preserved-controls/docs/strategy/RDP_PHASE0_FROZEN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/B2_REDUCE_MAKER_FRONTIER_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/B2_REDUCE_QUOTE_ATTRIBUTION_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/B2_RESIDUAL_ATTRIBUTION_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/CIP_B0_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/CIP_B1_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/CIP_B2_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/CIP_B3_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/CIP_B4_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/EXTERNAL_INTERFACE_CLEANUP_M1_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/GRID_GAMMA_G0_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/M4A_REAL_MARKET_2_RPC_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/MI_M1_IMPLEMENTATION_REPORT.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reports/MI_M2A_IMPLEMENTATION_REPORT.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reports/MI_M2B_0A_IMPLEMENTATION_REPORT.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reports/MI_M2B_0B_IMPLEMENTATION_REPORT.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reports/MI_M2B_IMPLEMENTATION_REPORT.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reports/MI_M3_IMPLEMENTATION_REPORT.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reports/P0_CM_30_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/P0_MAKER_ONLY_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/P0_MAKER_PATH_DIAGNOSTIC_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/P0_PROBABILITY_INVENTORY_IMPLEMENTATION_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reports/PRE_MI_GIT_CHECKPOINT_REPORT.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/B2_REDUCE_MAKER_FRONTIER_ATTRIBUTION_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/B2_REDUCE_MAKER_FRONTIER_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/B2_RESIDUAL_ATTRIBUTION_20261003.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/B2_RESIDUAL_ATTRIBUTION_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B0_BACKTEST_20261003.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B0_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B1_BACKTEST_20261003.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B1_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B2_BACKTEST_20261003.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B2_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B3_BACKTEST_20261003.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B3_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B4_BACKTEST_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/CIP_B4_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_BACKTEST_01_ASYMMETRIC_INVENTORY_SKEW.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_BACKTEST_02_QUOTE_FILTER.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_BACKTEST_03_THREE_LAYER_ENTRY.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_BACKTEST_04_CONVERGENCE_TARGET.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_M0B2_EDGE_SLACK_STUDY.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_M0B_OWN_FILL_LABEL_VALIDATION.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_M0_MAKER_FILL_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/DP_PREDICTION_MARKET_OPTIMAL_QUOTING_BASELINE.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/EXECUTION_REPLAY_DATA_AUDIT_20261003.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/GRID_GAMMA_G0_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/GRID_GAMMA_G0_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/P0_CM_30_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/P0_CM_30_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/P0_MAKER_ONLY_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/P0_MAKER_ONLY_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/P0_MAKER_PATH_DIAGNOSTIC_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/P0_PROBABILITY_INVENTORY_20261004.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/P0_PROBABILITY_INVENTORY_RESEARCH_DESIGN.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/research/R0_ROLLING_PAIR_REPLAY_20261003.md` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `docs/reviews/MI_M1_REVIEW.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reviews/MI_M2A_REVIEW.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reviews/MI_M2B_0A_REVIEW.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reviews/MI_M2B_0B_REVIEW.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `docs/reviews/MI_M3_REVIEW.md` | A | IM-specific source/tests/program integration; exclude. |
| A | `research_tools/b2_reduce_maker_frontier_attribution.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/b2_reduce_quote_attribution.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/b2_residual_attribution.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/cip_b0_backtest.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/cip_b1_backtest.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/cip_b2_two_phase_backtest.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/cip_b3_book_invalid_backtest.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/cip_b4_residual_budget_backtest.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dp_backtest_01_inventory_skew.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dp_backtest_01b_attribution.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dp_backtest_01b_settlement_labels.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dp_backtest_02_quote_filter.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dp_backtest_03_three_layer.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dp_backtest_04_convergence_target.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dpm0_fill_audit.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dpm0_pilot.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/dpm0b2_edge_slack.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/grid_gamma_opportunity_g0.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/p0_cm_30.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/p0_cm_30_audit.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/p0_maker_only.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/p0_maker_path_diagnostic.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/p0_probability_inventory.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `research_tools/r0_rolling_pair_replay.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| M | `src/polymaker/btc5m.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| M | `src/polymaker/cli.py` | A | IM-specific source/tests/program integration; exclude. |
| M | `src/polymaker/config.py` | C | Shared identity/readiness/signing/order-read candidate, or mixed historical cleanup; defer, not required to create RDP predecessor. |
| M | `src/polymaker/engine.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| M | `src/polymaker/execution/gateway.py` | C | Shared identity/readiness/signing/order-read candidate, or mixed historical cleanup; defer, not required to create RDP predecessor. |
| M | `src/polymaker/favorite_live.py` | C | Shared identity/readiness/signing/order-read candidate, or mixed historical cleanup; defer, not required to create RDP predecessor. |
| A | `src/polymaker/live_authorization.py` | C | Shared identity/readiness/signing/order-read candidate, or mixed historical cleanup; defer, not required to create RDP predecessor. |
| M | `src/polymaker/marketdata/service.py` | C | Shared identity/readiness/signing/order-read candidate, or mixed historical cleanup; defer, not required to create RDP predecessor. |
| A | `src/polymaker/mi_cash_reservation.py` | A | IM-specific source/tests/program integration; exclude. |
| A | `src/polymaker/mi_live.py` | A | IM-specific source/tests/program integration; exclude. |
| A | `src/polymaker/mi_micro_live.py` | A | IM-specific source/tests/program integration; exclude. |
| A | `src/polymaker/mi_readonly_account.py` | A | IM-specific source/tests/program integration; exclude. |
| M | `src/polymaker/strategy/favorite_first.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `src/polymaker/strategy/maker_inventory.py` | A | IM-specific source/tests/program integration; exclude. |
| M | `tests/test_engine.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| M | `tests/test_execution.py` | C | Shared identity/readiness/signing/order-read candidate, or mixed historical cleanup; defer, not required to create RDP predecessor. |
| M | `tests/test_favorite_first.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| M | `tests/test_favorite_live.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `tests/test_gateway_token_balances.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| M | `tests/test_hardening2.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `tests/test_live_authorization.py` | C | Shared identity/readiness/signing/order-read candidate, or mixed historical cleanup; defer, not required to create RDP predecessor. |
| A | `tests/test_maker_inventory.py` | A | IM-specific source/tests/program integration; exclude. |
| A | `tests/test_mi_cash_reservation.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `tests/test_mi_live.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `tests/test_mi_micro_live.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `tests/test_p0_cm_30.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `tests/test_p0_maker_only.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |
| A | `tests/test_p0_probability_inventory.py` | B | Uncertain/mixed legacy production, offline research, tests or differing documentation; exclude as runtime reuse. Audit evidence stays documentary. |

## Deferred candidate decisions
- Authorization/identity gates: strategy-neutral intent and historical review/tests, but bundled with MI integration; deferred, not a baseline prerequisite.
- Book freshness: shared extraction with historical tests and generation semantics; deferred, current FL already contains equivalent inline checks.
- Signing-price normalization: useful SDK boundary helper; deferred, not independently imported from unfinished M3 snapshot.
- Order-read handling: response/not-found/mismatch changes in M3 preservation snapshot; deferred, not required to begin implementation and no acceptance inferred from older fingerprints.
- Historical Data API-only/on-chain/merge cleanup: excluded from provenance reconstruction; requires explicit minimal future official-interface contract if RDP would reach inherited legacy paths.

Shared-core included: NONE. No full IM merge, no cherry-pick, no transplant of IM code. Future RDP runtime authority is restricted to official Polymarket HTTP/WebSocket; legacy FL/RPC code is preserved historical functionality, not an authorized RDP interface.
