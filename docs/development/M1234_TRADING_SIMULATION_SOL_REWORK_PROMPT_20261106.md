# Sol 执行 M1234 交易模拟返工提示词

你负责实施 M1234 交易模拟返工，完成后交回 Astra 独立验证。请把下面任务作为本轮持续执行目标，逐项做到可复现、可核账、可交接；不要只给分析或计划，也不要修完代码就结束。

项目目录：`/home/ubuntu/polymaker`。

先阅读适用 AGENTS.md，再读这些文件：

1. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_REWORK_ITEMS_20261106.md`
2. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_INDEPENDENT_REVIEW_20261106.md`
3. `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_WORK_ITEMS_20261105.md`
4. `/home/ubuntu/polymaker/research_outputs/m1234_sim/acceptance/20261105T145500Z-v1/HANDOFF.md`
5. `/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/review.json`

以返工工作项清单为本轮实施合同，依赖顺序执行 RW00–RW08。先冻结当前状态并复现问题，修复 R1–R4，再完成集成回归、版本冻结、确定性回放、最终版本 12 场公共模拟及新交接包。原清单中的策略、资金、输入与传输边界继续适用。

四项问题都必须有实质修复：

- R1：处理积压造成成交与深度重复推进。80 份前方队列、85 份卖出不能变成确定成交 10 份；不能保留错误 MODELLED_FILL 再加 UNKNOWN 标记，不能吞成交或放宽时效。
- R2：tick 变化后缺少官方复核。增加有界公共查询及带 revision/generation、实际可得时刻的有序结果事件；处理过期响应、失败和恢复；离线回放不得联网或捏造旧记录中不存在的响应。
- R3：订单到达时没有校验当前价格精度。0.095 买单到达前 tick 明确变为 0.01 时应拒绝、零成交；明确精度拒绝不能误当穿越价拒绝，合法订单保留正常通过路径。
- R4：同一物理订单多笔成交被错误关联到首条模型记录。修正订单级聚合/可靠关联；相同 4+6 成交记录必须得到一笔物理订单、累计量差 0、首笔等待差 0，且行顺序不影响结果，不能用空比较表通过断言。

工作区有大量既有未提交改动，保留它们。旧封存包及 `independent/astra-20261106-01` 目录只读保留；新证据统一写入 `research_outputs/m1234_sim/rework/<唯一 run_id>/`。部分旧验证脚本会写回自身目录，先检查副作用，再使用副本或指定新输出。不得修改历史失败证据、移除身份校验、削弱断言或把旧报告改名为新结果。

本轮可以执行完成清单所需的代码、测试、只读公共查询和新增模拟入口。只运行模拟，不加载真实钱包、不发真实订单、不启停或修改既有 M1/M234 服务。维持原信号参数、新单 [T15,T240)、观察至 T300、每活动市场一条公共订阅和原容量/时效边界。最终验收单次最多 12 场、4500 秒；失败留证并按清单另立 run ID，不能无限常驻或混用版本。

执行中持续更新 `/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_PROGRESS.md` 和新包的 `work-items.json`。使用 TODO / IN_PROGRESS / DONE / BLOCKED；DONE 必须有源码身份、实际命令、退出码、结果路径及通过条件。旧版 DONE 保留为历史，当前状态不能掩盖返工缺口。接口合理变更时保留等价反例和迁移依据，不要求机械保持旧测试内部写法。

运行适用的原 M1/M234/共享行情/PaperBroker 回归与新增边界/故障测试。原 562 项通过只是基线，资金算术一致也不证明撮合正确。新版本需要完整回放和独立核账；复核事件不得回填至请求时间。旧输入缺失响应时保持缺失，不能把人工补充当作真实公共记录。

修复并冻结最终版本后，完成新版 12 个完整公共观察窗口及至少一个完整公共场次的离线一致性验证。保留资源、缺口、tick 复核、订单、结算及服务身份前后证据。没有自然成交不构成失败，也不允许调阈值补足；真实校准继续 UNCALIBRATED。环境阻断时记录具体缺口并保持对应项未完成，不提前宣称整个目标完成。

最终提交新包的 HANDOFF.md、manifest.json、work-items.json、findings-resolution.json、源码归档、全部验证证据和可直接执行的复现命令。汇报新包绝对路径、源码身份、RW00–RW08 状态、R1–R4 证据、测试结果、12 场范围与未决事项。

最终状态只能是“实施完成，等待 Astra 独立复验”；独立复验标为 PENDING_ASTRA_REVIEW。完成本轮后交回验证，不扩展 HJB/CARA、参数寻优、真实试单或新仪表盘。
