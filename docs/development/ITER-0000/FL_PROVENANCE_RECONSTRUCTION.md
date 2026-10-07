# FL Provenance Reconstruction — ITER-0000

Status: reconstruction test gate completed; additive archival baseline, no independent Review PASS claimed.
Human decision: reconstruct 97ab0f9 plus minimum provenance-supported runtime/capture changes; neither 97ab0f9 nor 19c322a is directly canonical.

## Historical sources and exact diff
Parent: 97ab0f98168f989b1878e4058022044c8372dcd3.
Source of recovered hunks: 19c322ae2c12621351e034dfdc2705ea2f9ad98f (mixed archive; no whole cherry-pick).
Canonical reconstruction commit: resolve fl-reconstructed-baseline^{commit} after acceptance/tagging.
Exact four-file source/test patch: preservation/fl-reconstruction.patch.

## Included changes and excluded neighbors

| Reconstructed file/hunk | Evidence | Inclusion and excluded neighbors |
|---|---|---|
| src/polymaker/config.py: StrategyProfile favorite_min/max and entry_start_s/last_new_entry_s, validation | All 42 manifests contain three distinct entry-setting groups; FAVORITE_ENTRY_RANGE_CONFIG_IMPLEMENTATION_REPORT documents the profile mechanism preserved in 19c322a | Exact historical 13-line hunk; no later IM eth_utils identity modification |
| src/polymaker/favorite_live.py: dataclasses.replace, configured FavoriteFirst instance and its use in engine/report/capture identity/meta checks | 42/42 recreated effective configs and SHA256 identities match manifests; 41 available corresponding run.json files agree; report names shared effective-config wiring | Exact configuration wiring from 19c322a; its neighboring Data API-only _account_baseline replacement deliberately removed, leaving 97ab0f9 account guard unchanged |
| livecfg/strategy.toml: missing btc5m-survival profile plus late-entry parameters | M4A_A1_STARTUP_IMPLEMENTATION_REPORT proves committed wrapper requires this profile; 12 October 3 manifests/runs use 0.90–0.95 and 210–220 seconds | Exact historical profile block from 19c322a, based on parent config/strategy.toml; no wallet identity change; defaults support other groups without claiming one profile replays all sessions simultaneously |
| tests/test_favorite_live.py: startup resolution test, configured fake profile, effective-config propagation assertions | Same historical tests in 19c322a directly check recovered profile availability and wrapper configuration | Only supporting historical tests/fakes; Data API/no-RPC helper/tests omitted; extra blank lines from slicing removed without behavior change |

No engine or strategy policy hunk was reconstructed. In particular '< last_new_entry_s' and '>= last_new_entry_s' remain as in the parent: manifests do not establish inclusion at the exact cutoff. No signing/identity decision: wallet signature_type=3 from committed parent remains; original local =2 diff stays preserved separately. Config defaults remain 0.60–0.70/30–180; committed live profile selects historical late-entry group. No unknown human work overwritten.

## Capture correlation
42 manifests all name 97ab0f9, with groups: 20 default (0.60–0.70,30–180), 10 range-adjusted (0.85–0.90,30–180), 12 late entry (0.90–0.95,210–220). Recreated runtime configuration via reconstructed typed profile + CONTROLLED_CONFIG replacement matches all normalized values and config_sha256/strategy_config_id for all 42. Corresponding 41 run.json files exist and match; one run record is absent (20261002T111318620980Z_btc-updown-5m-1790939700), explicitly reported in correlation JSON. A first strict correlation attempt stopped on that missing record; rerun accounted for absence rather than inventing it. All session JSON headers and first nonempty JSONL rows remain readable. No raw datasets changed or copied into Git.

This baseline is a reconstructed canonical predecessor.
It does not claim that the historical live workspace was committed exactly in this form at the time.
It reconstructs the minimum committed tree supported by surviving operational evidence.

## Complete 97ab0f9 → 19c322a classification
FL_RUNTIME_PROVEN changes are eligible, not necessarily required. Optional proven aborted-session RPC diagnostics are intentionally omitted. FL_RUNTIME_POSSIBLE, LATER_SHARED_CORE, IM_OR_LATER_STRATEGY and UNRELATED_OR_UNKNOWN changes are not imported. Table covers every changed file; mixed-file neighboring hunks are governed by the precise notes above.

| File | Classification | Disposition |
|---|---|---|
| `config/strategy.toml` | FL_RUNTIME_POSSIBLE | Explicit unchanged default bounds redundant with typed defaults; excluded from minimum tree. |
| `docs/CURRENT_STATUS.md` | UNRELATED_OR_UNKNOWN | Historical status/archive/other research artifacts; exclude as runtime change. |
| `docs/reports/B2_REDUCE_MAKER_FRONTIER_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/B2_REDUCE_QUOTE_ATTRIBUTION_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/B2_RESIDUAL_ATTRIBUTION_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/CIP_B0_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/CIP_B1_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/CIP_B2_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/CIP_B3_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/CIP_B4_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/EXTERNAL_INTERFACE_CLEANUP_M1_IMPLEMENTATION_REPORT.md` | LATER_SHARED_CORE | Data API-only cleanup has implementation evidence, but no runtime provenance proving it required for captures; exclude/defer. |
| `docs/reports/FAVORITE_ENTRY_RANGE_CONFIG_IMPLEMENTATION_REPORT.md` | FL_RUNTIME_PROVEN | Evidence document referenced by reconstruction, retained as report source copy. |
| `docs/reports/GRID_GAMMA_G0_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/M4A_A1_STARTUP_IMPLEMENTATION_REPORT.md` | FL_RUNTIME_PROVEN | Evidence document referenced by reconstruction, retained as report source copy. |
| `docs/reports/M4A_REAL_MARKET_2_RPC_IMPLEMENTATION_REPORT.md` | FL_RUNTIME_PROVEN | RPC diagnostic event is present in aborted capture/run log; optional historical diagnostic, excluded from minimum reconstruction and official-interface-only RDP scope. |
| `docs/reports/P0_CM_30_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/P0_MAKER_ONLY_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/P0_MAKER_PATH_DIAGNOSTIC_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/P0_PROBABILITY_INVENTORY_IMPLEMENTATION_REPORT.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/reports/PRE_MI_GIT_CHECKPOINT_REPORT.md` | UNRELATED_OR_UNKNOWN | Historical status/archive/other research artifacts; exclude as runtime change. |
| `docs/research/B2_REDUCE_MAKER_FRONTIER_ATTRIBUTION_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/B2_REDUCE_MAKER_FRONTIER_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/B2_REDUCE_QUOTE_ATTRIBUTION_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/B2_RESIDUAL_ATTRIBUTION_20261003.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/B2_RESIDUAL_ATTRIBUTION_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B0_BACKTEST_20261003.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B0_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B1_BACKTEST_20261003.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B1_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B2_BACKTEST_20261003.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B2_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B3_BACKTEST_20261003.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B3_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B4_BACKTEST_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/CIP_B4_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_BACKTEST_01_ASYMMETRIC_INVENTORY_SKEW.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_BACKTEST_02_QUOTE_FILTER.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_BACKTEST_03_THREE_LAYER_ENTRY.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_BACKTEST_04_CONVERGENCE_TARGET.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_M0B2_EDGE_SLACK_STUDY.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_M0B_OWN_FILL_LABEL_VALIDATION.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_M0_MAKER_FILL_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/DP_PREDICTION_MARKET_OPTIMAL_QUOTING_BASELINE.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/EXECUTION_REPLAY_DATA_AUDIT_20261003.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/GRID_GAMMA_G0_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/GRID_GAMMA_G0_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/P0_CM_30_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/P0_CM_30_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/P0_MAKER_ONLY_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/P0_MAKER_ONLY_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/P0_MAKER_PATH_DIAGNOSTIC_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/P0_PROBABILITY_INVENTORY_20261004.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/P0_PROBABILITY_INVENTORY_RESEARCH_DESIGN.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `docs/research/R0_ROLLING_PAIR_REPLAY_20261003.md` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `livecfg/strategy.toml` | FL_RUNTIME_PROVEN | Included selected hunks; see precise hunk notes below. |
| `research_tools/b2_reduce_maker_frontier_attribution.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/b2_reduce_quote_attribution.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/b2_residual_attribution.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/cip_b0_backtest.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/cip_b1_backtest.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/cip_b2_two_phase_backtest.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/cip_b3_book_invalid_backtest.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/cip_b4_residual_budget_backtest.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dp_backtest_01_inventory_skew.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dp_backtest_01b_attribution.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dp_backtest_01b_settlement_labels.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dp_backtest_02_quote_filter.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dp_backtest_03_three_layer.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dp_backtest_04_convergence_target.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dpm0_fill_audit.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dpm0_pilot.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/dpm0b2_edge_slack.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/grid_gamma_opportunity_g0.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/p0_cm_30.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/p0_cm_30_audit.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/p0_maker_only.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/p0_maker_path_diagnostic.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/p0_probability_inventory.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `research_tools/r0_rolling_pair_replay.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `src/polymaker/config.py` | FL_RUNTIME_PROVEN | Included selected hunks; see precise hunk notes below. |
| `src/polymaker/engine.py` | FL_RUNTIME_POSSIBLE | Exclude inclusive cutoff and/or on-chain reconciliation/auto-merge removal; metadata does not prove boundary behavior or absence of RPC. |
| `src/polymaker/execution/gateway.py` | FL_RUNTIME_PROVEN | RPC diagnostic event is present in aborted capture/run log; optional historical diagnostic, excluded from minimum reconstruction and official-interface-only RDP scope. |
| `src/polymaker/favorite_live.py` | FL_RUNTIME_PROVEN | Included selected hunks; see precise hunk notes below. |
| `src/polymaker/strategy/favorite_first.py` | FL_RUNTIME_POSSIBLE | Exclude inclusive cutoff and/or on-chain reconciliation/auto-merge removal; metadata does not prove boundary behavior or absence of RPC. |
| `tests/test_engine.py` | LATER_SHARED_CORE | Data API-only cleanup has implementation evidence, but no runtime provenance proving it required for captures; exclude/defer. |
| `tests/test_favorite_first.py` | FL_RUNTIME_POSSIBLE | Exclude inclusive cutoff and/or on-chain reconciliation/auto-merge removal; metadata does not prove boundary behavior or absence of RPC. |
| `tests/test_favorite_live.py` | FL_RUNTIME_PROVEN | Included selected hunks; see precise hunk notes below. |
| `tests/test_gateway_token_balances.py` | FL_RUNTIME_PROVEN | RPC diagnostic event is present in aborted capture/run log; optional historical diagnostic, excluded from minimum reconstruction and official-interface-only RDP scope. |
| `tests/test_hardening2.py` | LATER_SHARED_CORE | Data API-only cleanup has implementation evidence, but no runtime provenance proving it required for captures; exclude/defer. |
| `tests/test_p0_cm_30.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `tests/test_p0_maker_only.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |
| `tests/test_p0_probability_inventory.py` | IM_OR_LATER_STRATEGY | Separate offline later strategy/replay research, excluded; no claim these are IM production code. |

## Test gate
Existing environment: /home/ubuntu/poly-maker/.venv/bin/python, Python 3.12.14, pytest 9.1.1. PYTHONPATH=/tmp/poly-maker-fl-reconstruct-20261007/src and PYTHONDONTWRITEBYTECODE=1, pytest -p no:cacheprovider. Commands below run from reconstruction worktree; no original mixed-tree result substitutes for baseline tests.

Known predecessor test: tests/test_favorite_live.py::test_required_spender_follows_client_order_version_and_market_risk. Prior independent reproduction on isolated 19c322a: timeout 15, exit 124, preserved in prior audit (KNOWN_PREEXISTING_TEST_HANG in that execution context). Current bounded reconstruction reproduction command: timeout 15 /home/ubuntu/poly-maker/.venv/bin/python -m pytest -vv -p no:cacheprovider <exact nodeid>; result 1 passed in 0.37s. The historical hang did NOT reproduce in the current unrestricted environment. No code fix or claim about its root cause; no waiver used for final acceptance.

Authorized broad gate command: timeout 150 ... -m pytest -vv -p no:cacheprovider --deselect=tests/test_favorite_live.py::test_required_spender_follows_client_order_version_and_market_risk -o faulthandler_timeout=30; result 655 passed, 2 skipped, 1 deselected in 10.06s, exit 0. Full log retained.
Stronger unfiltered gate: timeout 150 ... -m pytest -q -p no:cacheprovider; 656 passed, 2 skipped in 10.14s, exit 0. Full log retained. No additional test excluded; research writer tests passed. Skips are existing live-marketdata tests. Earlier timeouts remain historical evidence, not erased or claimed fixed.

## Safety / limitations
Preserves historical FL behavior and gates outside configuration recovery; no RDP/IM source imported. Parent FL still contains legacy RPC account/reconciliation and merge/settlement paths. They are historical FL artifacts, NOT authorized RDP runtime interfaces or permission to trade. Future RDP Architect must confine market/trading/account/risk/execution calls to official Polymarket HTTP/WebSocket; required isolation is a future implementation contract, not silently added to historical reconstruction. No network/RPC trading action, authenticated order/cancel, or new runtime dependency was executed/introduced by this task. Tests may exercise official SDK public metadata as existing tests; no real order.

## Shared-core review for first RDP baseline
Authorization/identity gates, books_fresh, signing-price normalization, and order-read handling remain deferred. None is required to create an implementation predecessor. Bundled IM changes lack isolated accepted reuse evidence; include NONE. Later Architect/Worker/Reviewer iteration must define any actual need.

## Acceptance and handoff
Human-authorized provenance reconstruction and stronger unfiltered regression gate satisfy code/test criteria; a clean committed tree and additive tag are required before FL acceptance is complete. Git verifies parent 97ab0f9 ancestry and absence of IM runtime in reconstruction. RDP baseline may add readiness documentation only, with no shared-core additions or RDP implementation. No independent reviewer/milestone PASS certification implied.

## Checkpoint whitespace audit
Full staged diff --check reports only byte-preserved evidence: original Git graph trailing spaces, raw patch context lines (a single space denotes unchanged blank context), and the original frozen control EOF blank line. These evidence files were inspected and preserved, not cosmetically rewritten. Source/tests and newly authored documentation pass scoped staged diff --check. The first checkpoint attempt stopped before commit when its check expected only the EOF warning; scope was corrected to account for preserved raw evidence. No tag/branch was created by that failed attempt.
