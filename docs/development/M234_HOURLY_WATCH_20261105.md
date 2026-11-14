# M2 / M3 / M4 每小时巡检

2026-11-05 用户明确恢复本对话巡检，范围扩大为 M2、M3、M4，每小时一次。
复用 `polymaker-m2-watch.timer` / `.service`，北京时间每小时 00 分 30 秒执行，
仅向原对话 `01a1a0dc-5e73-72e1-886e-bd9856858b4b` 送入复核任务。
这取代此前十五分钟和状态不变静默的通知规则：每次简短报告三组累计数量及健康状态。
巡检保持原低优先级与资源限额；单次最长 300 秒，避免只读大账本在资源等待时过早超时。

只读共享公共研究进程、报告、SQLite、冻结定义及公共研究日志，不加载账户或操作 M1。
不重启采集进程，不修改 M2/M3/M4 参数、CPU 开关或实盘。没有新信号不等于策略故障。
看板展示及归档界面不属于三策略信号测试巡检范围。对于混合模块 `dashboard/settlement.py`，
用与冻结哈希匹配的只读源码基线核对：只排除 `SettlementObserver` 看板类的变化；
公共校验函数、常量、导入或其他模块变化仍检查。不改动采集进程原有指纹或数据版本。

检查四层：系统/时效/输入连续性与资源等待；当前版本策略启用及 observer 错误；
候选、有效观察、有效根信号产出；模拟成交、方向名额、5 USDC 风险、20 USDC 场次预算、
待官方结算及收益一致性。完整窗口不是无输入缺口证明。

有效信号为根机会首次合格且数量为正的研究样本，包含同方向限笔而跳过的后续信号。
M2 仅计 `all_candidates`，不相加四个嵌套组；M3/M4 分别按自己的 CA/DF 根计数。
另列模拟开仓数量、待结算数、独立市场数及最近一小时新增数量。
历史累计是跨版本记录库存，不混合收益或当作当前规则的验证。
M4 另按旧 v1 / `m4-frequency-v2` 分列；当前版本健康、收益仅解释当前版本。

巡检输出沿用 `research_outputs/m2_watch/latest.json`、`reports/<小时桶>.json`。
`agent-state.json` 保存暂停/恢复状态及实际完成的复核游标。
恢复时 `monitoring_paused=false`；暂停会停止并禁用原定时器，已排队旧任务不撤销暂停。
单次 `--no-wake` 只生成独立报告；不得将未完成审查的桶提前标为已复核。

验证：`PYTHONPATH=tests:src:. .venv/bin/python -B -m pytest -q tests/test_m2_study_watch.py`。

后续定期价格与时间分桶分析采用
[M234_PRICE_TIME_ANALYSIS_TEMPLATE.md](M234_PRICE_TIME_ANALYSIS_TEMPLATE.md)，模板编号为 `m234-price-time-v2-text`。
