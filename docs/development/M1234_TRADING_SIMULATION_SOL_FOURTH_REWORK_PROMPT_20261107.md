# Sol 执行 M1234 交易模拟第四轮返工提示词

请在 `/home/ubuntu/polymaker` 持续执行 M1234 第四轮返工，逐项完成 RW25–RW32，最后交回 Astra 独立验证。完成实现、测试、复现、最终版本公共验收和封存交接，不停留在分析或代码修改。

先读适用 AGENTS.md，再完整阅读：

1. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_FOURTH_REWORK_ITEMS_20261107.md`
2. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_V4_INDEPENDENT_REVIEW_20261107.md`
3. `/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/test_coordinator_persistent_failure.py`
4. `/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/coordinator-probe-summary.json`
5. `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T150054Z-third-rework-v4/HANDOFF.md`
6. `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T150054Z-third-rework-v4/contracts/failure-binding-v4.json`

以第一份清单为实施和验收合同。原 RR1、原 RR2 的离线输出及重开检查已通过；735 项完整回归和 v4 公共证据均保留为基线。本轮只关闭 RR2-LIVE：实际协调器在持续快照写入失败时未能到达 END，汇总跳过当前 INVALID 市场，有前置健康市场时仍 PASS；清理再次抛错又使完成记录缺失，正式验收误显示 RUNNING 并退出 0。

先冻结起点、在新目录复现四项反例，再修复这条完整失败链：失效市场不依赖 ended 才进入报告；缓存和在途报告不能撤销失败；单步清理异常不能阻断其余可执行的关闭及失败记录；停止后的运行在报告、终态和正式验收中一致失败。正式验收只有真正通过才返回 0，确实仍运行或证据不足须明确标识为未通过。保留正常前置市场的历史，不把所有市场一律改坏。

故障必须保持到产品输出和清理验证结束。不得手工补 END、恢复写入器后再验证、删成交、改余额、释放未知风险或只改顶层文案。根因与次生错误分别保留，失败证据沿用可靠身份和水位绑定；其他通道可写时保存运行 FAIL，所有通道都不可写时诚实非零，不声称保存了不存在的结果。

将四例正式纳入回归，并至少增加一条真实 run CLI 子进程驱动的离线协调器故障路径。检查产品返回/抛出前的资源关闭状态，测试最终兜底清理不能算作产品成功。保留正常协调器、缓存/在途、并发读取、原离线四例、RR1 和合法成交正对照。若异常类型或接口有意改变，说明等价适配，不弱化行为断言。

保留原 735 项选择及已验证的固定参数夹具，完整回归单独运行，所有失败尝试留存，不放宽断言、时效或参数。冻结新版本后重复验证正常、RR1、离线 RR2 和本轮四组在线路径，核对实际子命令退出码。再完成最终版本单次 12 场、最多 4500 秒的公共模拟、完整回放和独立核账；旧 12 场不能改名计入新版本，无自然成交不调阈值补足。

保留工作区既有改动和八个历史证据根目录，先检查旧脚本的输出副作用。新材料写入 `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/<新的唯一 run_id>/`。只做模拟与有界公共只读查询，不加载真实钱包、不发真实订单、不启停或修改既有服务，不扩展 HJB/CARA、策略优化或通用进程管理平台。

持续更新 `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_PROGRESS.md` 与新包 `work-items.json`。每项 DONE 附源码身份、实际命令、真实退出码、结果路径和通过条件。条件未满足就保留未完成并说明原因。

最终提交新封存包、源码/归档身份、RW25–RW32 状态、RR2-LIVE 前后证据、完整测试/清理/回放/公共运行/核账结果及可执行复现命令。实际全部完成后写“实施完成，等待 Astra 独立复验”，独立状态 PENDING_ASTRA_REVIEW，真实校准 UNCALIBRATED；不要替 Astra 宣布最终独立通过。
