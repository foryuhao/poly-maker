# SL 信号驱动执行：实现与离线验收

日期：2026-10-19。用户已确认 FIFO 成本口径并授权修改代码。**代码及离线验收完成，可进入有人观察的单市场实盘测试。** 本轮未启动、重启交易进程，未发送真实订单。运行中的旧进程不会热加载本次修改。

本次实现依据[已确认方案 V2](../../SL_SIGNAL_DRIVEN_EXECUTION_PROPOSAL_V2_20261019.md)及[开发前 Review](../20261019_sl_execution_predevelopment/README.md)。验收摘要与文件指纹见 [validation.json](validation.json)。此前记录的 RP-001 不在本次修改范围。

后续首场实盘更新：18:25–18:30暴露RP-002，单订单HTTP200无效详情引发整个读取组退避、阻塞网络恢复，SL最终未配平。[实盘复盘](../20261019_sl_execution_live_1825/README.md)已闭合证据并离线复现；随后三处修复及2,092项完整回归已通过，见[修复验收](../20261019_sl_execution_recovery_fix/README.md)，待下一场实盘验证。下文为最初实现的历史离线记录，不代表实盘验收通过。

## 行为变化

- SL 首单、TP→SL、SL 追价使用独立执行协调器。有效目标在本地预占资金与数量后启动新 POST，同轮独立启动旧单 DELETE；不等待旧 POST/DELETE 回执、账户刷新或整批关闭。
- 只短暂重叠，立即撤旧单。签名与发送上下文按请求隔离，旧 TP 的关闭任务不会撤掉新 SL；迟到 POST 接受会再次触发撤单。
- 当前 `obi_v1 + hd_tp_sl_v1` 的 session 总预算为 20 USDC；受实际有依据账户资金约束。全部 NORMAL 与 REPAIR 共用，不按方向、episode、重试重置。其他旧模式仍使用原 15 USDC。
- 以 `[实际Delta－未决NO，实际Delta＋未决YES]` 检查全部在途订单；区间保持在 `[-5,+5]`。第三次追价空间不足时立即撤旧单、保留最新目标，逐单证据释放空间后重算派发。
- MATCHED 抵扣执行缺口，但不提前成为实际库存，也不释放对应资金和 Delta。订单终态与完整成交记录一致时只释放已证明未成交部分；UNKNOWN、FAILED、身份冲突保留相应风险与隔离。
- 实际反向残仓 ≤2.5 股停止新修复；>2.5 股按实际 session 成交 FIFO 归集剩余数量、成本和时间，进入普通 HD，复用已有 TP/SL 条件。session PnL、预算与时钟连续。前一轮已证明的 dust 也计入 FIFO，避免数量漏算。
- T180 强制 SL、T240 禁止新 REPAIR 并撤余单、Merge 边界保留。完整对账仍用于最终交接；不放到每次 SL 接替之前。

本次没有修改 NORMAL 信号、HD/TP 决策阈值与定价、Merge 策略、旧 EV 模型或 SDK 版本。共享模块仅增加 SL 所需的请求隔离、逐单证明、资金口径和交接支持。

## 代码位置

| 文件 | 用途 |
|---|---|
| `src/polymaker/rdp_sm_sl.py` | SL 目标协调、并行提交/撤单、逐单回执与证据、截止收尾 |
| `src/polymaker/rdp_sm_sl_ledger.py` | 全在途 Delta/资金、MATCHED 执行量、FIFO 反向普通 REPAIR 输入 |
| `src/polymaker/rdp_sm_path_repair.py`、`rdp_sm_lifecycle.py` | 原入口交接、最终关闭、反向 episode 交回普通策略 |
| `src/polymaker/execution/gateway.py`、`rdp_sm_execution.py` | 每请求 SDK 上下文、临时订单归属、矛盾隔离及预算 |
| `src/polymaker/rdp_sm_cached_reads.py` | 后台逐单事实消费，不以全账户读屏障阻挡新目标 |
| `src/polymaker/rdp_sm_network.py` | 解除同一 SL 组在途互斥，保留真实熔断与冷却 |
| `src/polymaker/rdp_sm.py`、`rdp_sm_logging.py` | 20 USDC 准入及显示、SL 信号/命令/等待/反向事件 |
| `src/polymaker/state/store.py` | 仅撤销明确未提交/被拒 SL 的临时订单归属 |

## 离线验收

两组互不重复的测试合计 **2,072 passed、2 skipped、0 failed、0 errors**：

| 范围 | 结果 | 记录 |
|---|---|---|
| 所有 `test_repair*.py`、`test_rdp_sm*.py` | 1,359 passed，267.68 秒 | [日志](regression.log)、[JUnit](regression.xml) |
| 其余全部测试，包含共享网关、状态与其他策略 | 713 passed、2 skipped，10.32 秒 | [日志](shared_regression.log)、[JUnit](shared_regression.xml) |

Ruff 与 `git diff --check` 通过。主回归两条 warning 是已有 `record_property` 与 JUnit xunit2 格式的兼容提示，无运行失败。

新增 SL 专项覆盖真实签名/SDK/网关/跟踪器，仅替换交易所 I/O：旧 POST ACK 不返回时仍发新 SL、旧撤单 ACK 不返回时仍发新 SL、双成交 FIFO/反向 HD、MATCHED 与实际证明分离、低价最小订单、第三追价受限与释放后执行、T240 迟到接受、签名前后盘口变化、后台缓存执行。原生产入口验收也覆盖 NORMAL→HD→TP→SL、下一 NORMAL、T180/T240 与 Merge 交接。

首次完整主回归的两项失败及修正保存在 [regression_initial.log](regression_initial.log)：一项是旧模式日志资本字符串 `15.0` 与原 `15` 不同，已恢复兼容；另一项是 NORMAL 缓存测试采用 0.6 秒测试超时在整套负载下不足，单测重跑通过后将该测试设置为生产已有 `ws_stale_halt_s`。生产 NORMAL 超时未修改；独立的队列饥饿测试保留。旧 TP 串行等待测试已改为断言同次调用立即交接 SL；SL 的持久身份写入报价竞态由新专项覆盖。

## 耗时与实盘边界

[模拟耗时记录](simulated_latency.json)从最终回归日志按请求体 hash 关联 SL 命令与真正 SDK HTTP 开始时间，27 个已发送请求：平均 5.81ms、p95 9.46ms、最大 10.08ms。该样本使用模拟交易所 I/O，起点为协调器识别并接纳 SL 目标，**不包含之前策略识别耗时、限额等待，也不是实盘网络或成交耗时**；不能据此宣称实盘 p95 或收益改善。

实盘测试仍需观察真实信号至 POST/DELETE、Maker 成交等待、短暂重叠及方向翻转次数。日志包含 `sm_sl_signal`、`sm_sl_command`、`sm_sl_wait`、`sm_sl_order_release`、`sm_repair_reverse`，以及原生网络开始/结束时间；受限未发单会记录原因。正 EV 与实际 PnL 改善未由离线执行验收证明。

`config`、`livecfg` 仅把 `validation_reference` 更新为本次凭据，策略数值参数未改。现有 `uv run polymaker rdp-sm-run` 在下一次新进程启动时使用本次代码；本轮没有替用户启动交易。
