# 验收测试映射

测试文件 tests/test_risk_shadow.py。参数化后测试项数及完整结果见 RESULT.md。
单元测试同时经过既有禁止网络/DNS及工作区凭证读取的 offline runner。

| 验收 ID | 测试函数/证据 |
| --- | --- |
| BASELINE-01 | baseline_01_and_protected_sources_identical：HEAD、所有既有 src/tests 字节与批准 commit 一致 |
| BASELINE-02 | baseline_02_default_off_has_no_io |
| BASELINE-03 | baseline_03_shadow_no_execution_import_or_gateway_rpc_permission；既有报价/执行回归 |
| MARKET-01 | market_01_02_mid_age_and_official_clock_independent |
| MARKET-02 | 同上，官方时间与本地时间不同仍以本地 Age 计算 |
| MARKET-03 | market_03_stale_boundary，恰好 5 秒/超过 5 秒 |
| MARKET-04 | market_04_asynchronous_and_low_confidence |
| MARKET-05 | market_05_06_slots_and_no_future_backfill |
| MARKET-06 | 同上；real_last_update_in_bucket_selected_and_no_future_price |
| VOL-01 | vol_01_formula_separate_from_gate |
| VOL-02 | vol_interval_count_gate_independent_of_duration：24 秒覆盖但只有 6 区间 |
| VOL-03 | vol_02_03_thresholds：10 区间但不足 20 秒 |
| VOL-04 | vol_04_price_gap_excluded_not_zero |
| VOL-05 | vol_05_06_old_cache_duplicate_vs_real_zero_update_and_bucket_last；WS fingerprint 去重 |
| VOL-06 | 同上，真实不变 Mid 可获得有效零方差 |
| VOL-07 | vol_07_market_restart_clean_state；vol_window_both_endpoints_and_generation |
| RISK-01 | risk_01_02_03_fixed_proxy |
| RISK-02 | 同上，零库存风险为零但未知 sigma 不被伪造 |
| RISK-03 | 同上；inventory_unknown_and_late_confirmation_never_rewrites_history |
| RISK-04 | risk_04_transition_and_realized_start_inventory |
| RISK-05 | vol_07_market_restart_clean_state；lifecycle_01_to_04_full_300_and_five_markets_stop_independent |
| SETTLE-01 | settle_01_to_06，参数 Delta=+5 |
| SETTLE-02 | settle_01_to_06，参数 Delta=−5 |
| SETTLE-03 | 两个参数化案例使用相同 p，方差均 0.2475 |
| SETTLE-04 | 两个参数化案例 WinProb 分别 .99/.01 |
| SETTLE-05 | settle_05_zero_and_tie_unknown_distinct |
| SETTLE-06 | settle_01_to_06：期望变化≈0 |
| SHADOW-01 | shadow_01_02_boundaries；execution_boundary_uses_true_decision_time |
| SHADOW-02 | shadow_01_02_boundaries，149/150 边界 |
| SHADOW-03 | shadow_03_invalid_not_clipped |
| SHADOW-04 | shadow_04_dynamic_tick_changes；WS tick 更新不刷新报价 Age |
| SHADOW-05 | shadow_05_hold_all_conditions 参数化 5 案例及 UNKNOWN |
| SHADOW-06 | shadow_06_execution_link_no_fill_inference_no_future |
| SHADOW-07 | baseline_03 AST 禁止 Gateway 写入；真实观察进程禁止 execution import |
| SHADOW-08 | baseline_03 AST 禁止 RPC/签名；真实观察进程禁止 RPC/signing import |
| LIFECYCLE-01 | complete_production_observer_mock_ws_and_deterministic_replay，无交易 Session |
| LIFECYCLE-02 | shadow_06_execution_link_no_fill_inference_no_future，STOP 后行情仍有效 |
| LIFECYCLE-03 | 生产 Observer 模拟完整 300 秒，240/270 有效 |
| LIFECYCLE-04 | 模拟五个市场，全新模型与文件状态隔离 |
| LIFECYCLE-05 | lifecycle_05_bounded_writer_errors_and_unknown_json；独立进程无执行引用 |
| DATA-01 | lifecycle_05_bounded_writer_errors_and_unknown_json，ZERO/UNKNOWN/N/A 分离 |
| DATA-02 | shadow_06_execution_link_no_fill_inference_no_future，winner 事件不能改历史 |
| DATA-03 | data_03_virtual_inventory_isolated |

补充：metadata_uses_actual_start_not_listing_creation、candidate_all_four_and_zero_delta_none、
low_confidence_no_inventory_risk；生产路径模拟还校验所有 300 条市场/风险/虚拟字段可确定性重放。
实际交易附加模式本轮只做离线验证，不假定获得实盘订单授权。
