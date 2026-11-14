# Sol 执行 M1234 交易模拟第三轮返工提示词

请在 `/home/ubuntu/polymaker` 持续执行 M1234 第三轮返工，逐项完成 RW17–RW24，完成后交回 Astra 独立验证。不要停留在分析、计划或只修代码。

先读适用 AGENTS.md，再完整阅读：

1. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_THIRD_REWORK_ITEMS_20261106.md`
2. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_V3_INDEPENDENT_REVIEW_20261106.md`
3. `/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/test_failed_snapshot_validity.py`
4. `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T061419Z-second-rework-v3/HANDOFF.md`
5. `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T061419Z-second-rework-v3/contracts/failure-propagation.json`

以第一份清单为实施和验收合同。原 RR1 已关闭，700 项回归和 v3 公共工程证据已通过，保留为基线。本轮关闭 RR2：数据库快照持续写入失败时，内存和 failure.json 已明确失败，自动报告及重开 audit/report 却从旧 VALID 快照返回 PASS，并将失效成交列为有效样本。

先冻结起点，在新目录复现四项独立用例的 3 失败/1 通过。修复当前失败输出和重开消费者，使同运行已知失败不能被旧快照覆盖。保持算术核账与模型有效性分离，明确运行/数据库身份及输入水位，防止串用其他运行的记录。不能只改顶层文案或 CLI 退出码；失效结果的样本与业绩资格也必须关闭。不要删成交、改余额、补 UNKNOWN 或伪造历史事实。

故障必须持续到失败报告和重开核账验证结束，不能提前恢复写入器。覆盖正常对照、重复失败输出、持久失败记录本身写入失败、身份关联及支持的备份/导出方式。至少有一条现有执行入口的确定性故障注入流程，不能只手工造 FAIL JSON。所有存储都不可写时如实失败，不宣称保存了不存在的证据。

保留原 700 项选择，加入 RR2 测试；完整回归单独运行，失败尝试全部留存，不放宽断言、交易时效或信号参数。冻结新版本后，完成正常、原 RR1 和 RR2 样本的重复离线验证，再按既有合同完成最终版本单次 12 场、最多 4500 秒的公共模拟、完整回放和独立核账。旧 12 场不能改名计入新版本；无自然成交不调阈值补足，真实校准保持 UNCALIBRATED。

本轮只进行模拟与有界公共只读查询，不加载真实钱包、不发送真实订单、不启停或修改既有 M1/M234 服务，不扩展 HJB/CARA、策略优化或新功能。保留工作区既有改动和六个历史证据根目录；先检查历史脚本副作用。新材料统一写入 `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/<新的唯一 run_id>/`。

持续更新 `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_PROGRESS.md` 与新包 `work-items.json`。DONE 必须有源码身份、实际命令、退出码、结果路径和通过条件。缺少条件则保留未完成并说明原因，不提前宣布目标完成。

最后提交新封存包、源码身份、RW17–RW24 状态、RR2 前后证据、全部回归/故障/回放/公共运行/核账结果和可执行复现命令。实际全部完成后，状态写“实施完成，等待 Astra 独立复验”，独立复验为 PENDING_ASTRA_REVIEW；不要替 Astra 宣布最终验收通过。
