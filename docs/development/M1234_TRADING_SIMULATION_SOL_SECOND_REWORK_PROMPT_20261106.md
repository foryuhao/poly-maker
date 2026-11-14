# Sol 执行 M1234 交易模拟第二轮返工提示词

你负责完成 M1234 交易模拟第二轮返工，逐项执行 RW09–RW16，完成后交回 Astra 独立验证。项目目录是 `/home/ubuntu/polymaker`。请持续执行到代码、测试、最终版本公共模拟、回放和交接材料全部完成，不停留在分析或计划。

先读适用 AGENTS.md，再读：

1. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_SECOND_REWORK_ITEMS_20261106.md`
2. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_REWORK_INDEPENDENT_REVIEW_20261106.md`
3. `/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/test_terminal_tick.py`
4. `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T014400Z-rework-v2/HANDOFF.md`
5. `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T014400Z-rework-v2/contracts/arrival-v2.json`

以第一份工作项清单为本轮实施合同。原 R1、R2、R4 已在独立验证范围内通过，作为回归基线。本轮核心是 RR1：FILLED 和部分成交后 CANCELLED 的订单跳过迟到 tick 冲突检查，导致不再可信的成交仍被当成确定结果。冻结起点、在新目录复现五项独立用例的 2 失败/3 通过，再按依赖顺序修复和验证。

修复必须覆盖有成交历史的终态订单，并让冲突贯通 Engine、结束排空/清理、持久化、报告、恢复/结算和最终验收。保留既有显式错误语义。不能只增加状态枚举然后忽略错误传播；不能删除成交、调整余额、补 UNKNOWN 或倒用未来信息伪造历史拒单。正常对齐价格、真正晚于到达的 tick 变化及正常成交/结算对照必须继续通过。账本算术通过不等于模拟有效。

保留原 668 项测试选择，补入五项独立反例及传播测试。完整回归单独运行，不与本轮重型扫描/回放/公共采集重叠；所有失败尝试留证，不放宽断言或交易时效，不靠无记录重复测试挑出成功。旧封存包、两轮独立证据只读保留，历史脚本先检查写入副作用。所有新材料写入 `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/<新的唯一 run_id>/`，保留工作区既有改动。

完成回归后冻结新模型和源码身份。正常与两类冲突人工样本各重复离线执行，分别证明确定性成功与确定性失败；不得绕过身份校验。再按原最终版本合同，单次完成 12 个公共窗口、最多 4500 秒、至少一个完整公共场次回放、全部输入与账本核对，并记录实际退出码。保持原容量、时效、信号参数和模拟传输边界；不把旧 12 场改名计入新版本。无自然成交不调阈值补足，真实校准继续 UNCALIBRATED。

只进行模拟与有界公共只读查询；不加载真实钱包、不发送真实订单、不启停或修改既有 M1/M234 服务，不扩展 HJB/CARA、策略优化或新功能。

持续更新 `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_PROGRESS.md` 与新包 `work-items.json`。DONE 必须有源码身份、实际命令、退出码、结果路径和验收说明。环境阻断或证据不足时如实保留未完成状态，不提前宣布目标完成。

交回新封存包及 HANDOFF、manifest、RR1 前后证据、全部测试/回放/公共运行/核账结果和可执行复现命令；列明 RW09–RW16 状态与未决事项。实际全部完成后，结论只写“实施完成，等待 Astra 独立复验”，独立复验标为 PENDING_ASTRA_REVIEW。不要替 Astra 宣布最终验收通过。
