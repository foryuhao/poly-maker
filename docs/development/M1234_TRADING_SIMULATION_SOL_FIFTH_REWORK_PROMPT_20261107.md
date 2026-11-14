# Sol 提示词：RR3 最小修补

在 `/home/ubuntu/polymaker` 完成 RW33–RW36，只修复 RR3：两份运行终态文件写入失败、只有根报告保存绑定失败时，报告重生成不能擦掉失败并输出 PASS。完成后交回 Astra 定向复验。

先读适用的 AGENTS.md 和以下资料：

1. [本轮最小返工清单](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_FIFTH_REWORK_ITEMS_20261107.md)。
2. [v5 独立复验报告的 RR3 段落](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_V5_INDEPENDENT_REVIEW_20261107.md)。
3. [RR3 行为摘要](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/finding-rr3.json)和[独立探针](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/probe_report_only_failure.py)。

以本轮清单为范围与验收合同。用户最新要求是“只做必要的测试，不要越修问题越多”；这取代旧提示词中每轮重复完整回归、全套复现、新公共 12 场运行及全量历史扫描的要求。v5 的 761 项通过、12 场公共证据、核账和完整回放作为旧版本已验证基线保留，不冒充本次补丁的新运行结果。

优先只修改 `src/polymaker/m1234_sim/report.py` 并补少量直接相关回归，复用 `runstate.py` 已有的同一运行身份验证。覆盖旧报告之前继承已验证的失败，保留原因与身份。主例重生成后报告 CLI 必须 FAIL/1，正式验收保持 FAIL/1；重复生成和复制完整运行目录后重开也要保持失败，数据库不变。

只跑清单中的主例、正常/证据不足/外来身份对照，以及已有 cached/inflight/empty 三个失效报告用例。尽量复用现有实际 CLI 离线驱动；同一主例复用到重生成与导出检查，不重复创建整套故障运行。不为单场人工对照追加公共 12 场验收。最终相关测试集通过一次，再检查改动文件的语法、适用 Ruff 和差异即可。

保持信号、撮合、资金、费用、订单生命周期、协调器清理、模型和 schema 版本以及既有服务不变。记录实际新源码指纹，不改旧 INIT 或旧证据来适配。暂不接入历史测试数据，不扩展策略或基础设施。若失败用例证明确有补丁直接引入的回归，只修正并复跑直接相关项；无关的一般建议另记后续，不扩进本轮。

使用新的独立输出目录，保留原工作区改动，旧交接包和独立材料只读。提交轻量增量包：基线引用、源码指纹、改动文件与相对 v5 的补丁、实际命令与退出码、测试结果、RR3 前后证明、小包校验清单和复验命令。注意相关文件可能未被 Git 跟踪，不能只依赖 `git diff` 获取补丁。

持续记录 RW33–RW36 状态。**主例、必要对照和局部检查通过，且差异符合范围后立即进入交接，不再扩测或重构。** 最后写“RR3 最小修补实施完成，等待 Astra 定向复验”，独立状态 `PENDING_ASTRA_REVIEW`，真实校准 `UNCALIBRATED`。本轮不替 Astra 宣布独立通过。
