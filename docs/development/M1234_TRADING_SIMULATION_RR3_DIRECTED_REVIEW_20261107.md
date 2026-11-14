# RR3 最小修补定向复验

日期：2026-11-07，北京时间。结论：**PASS**。**RR3 关闭，RW33–RW36 通过；对应 RW28/RW29 的遗留要求关闭。**

按用户要求及 [RW33–RW36 清单](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_FIFTH_REWORK_ITEMS_20261107.md)，本轮仅核对小补丁、增量包并执行约定的必要测试。已达到停止条件，没有新增本补丁范围内的阻塞问题。

## 身份与范围

- [增量交接包](/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261107T131117Z-rr3-patch/HANDOFF.md)。
- 本轮独立证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed`。
- [机器可读结论](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed/review-result.json)。
- 新实现 SHA256：`aff0ccbe9e3d5f82adc2852a31fd6a566f6a25f84fdaba88e002f9f1ad32d271`。
- 基线实现 SHA256：`c39b3e3454388985f9950f37bf6dafa72b1650a05702e126b4d4adc6cc303fee`。
- 增量补丁 SHA256：`d6b06e706c8247ad8a0dd34b45ca2e86b31b519588f167108c9c33014880de8f`。

独立比对确认：唯一产品改动是 [report.py](/home/ubuntu/polymaker/src/polymaker/m1234_sim/report.py:353)。覆盖报告前，将仅有 plan 与 latest 的运行也纳入已有身份验证，并且只继承被验证为 FAIL 的结果。INCOMPLETE 不被制造成确定失败。

测试改动为原健康对照增加报告重生成断言，以及新增一个复用同一故障运行的 RR3 用例。原断言保留，其他 393 个基线归档项目文件一致；信号、撮合、资金、费用、生命周期、协调器及运行状态检查实现均未修改。模型及 schema 版本保持不变。

## 实际复验结果

从 v5 冻结归档加补丁，在新的独立目录重建源码；重建后的源码指纹及三份改动文件与工作区、交接包完全一致。仅执行约定的 5 项定向测试一次：**5 passed，0 failed/error/skipped，pytest 退出 0，16.02 秒**。

| 检查 | 独立结果 |
| --- | --- |
| RR3 主例 | 实际 run CLI 退出 1；两份终态文件均不存在，只有根报告保存经验证的失败 |
| 连续两次报告重生成 | 每次报告 FAIL/退出 1，正式验收 FAIL/退出 1；原因、清理错误、运行身份均保留 |
| 同例导出重开 | 完整目录复制后正式验收仍 FAIL/1，身份与原因一致 |
| 正常对照 | 健康报告重生成 PASS/0，运行状态保持 PASS |
| 证据不足与外来身份 | 正式验收保持 INCOMPLETE/2；外来报告失败不归属于当前运行 |
| 既有 INVALID 报告 | cached / inflight / empty 三种参数化对照均通过 |

复验只创建一次 RR3 故障运行，重复生成、导出和外来身份检查复用该样本。16 条相关实际 CLI 子命令的退出码逐条核对符合预期。原目录、导出目录、外来对照目录各自的 6 份数据库哈希均与主例最初记录一致；报告中的市场经济内容保持一致，没有补造完成记录或 END。

三份改动 Python 文件的语法、Ruff 和补丁空白检查通过。复验者未修改产品源码。

证据：[重建与测试执行结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed/rebuilt/result.json)、[独立逐项检查摘要](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed/targeted-summary.json)、[实际测试日志](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed/rebuilt/targeted-tests.log)。

## 增量包与基线保留

本增量包的 **320 个文件、11,941,974 字节、3 个链接**与清单一致；11 个基线引用的哈希一致。复验结束后再次核对小包文件、引用及当前源码，均未变化。既有服务状态、PID 和启动时间前后一致。见[独立完整性核对](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed/integrity.json)。

本轮没有执行 761 项完整回归、152 命令整套复现、新公共采集、大规模回放或历史目录全量扫描。v5 已通过的完整回归、公共 12 场、核账和完整回放继续按原源码身份引用，不重标为本补丁版本的新运行结果。历史 NEEDS_REWORK 记录保留，本报告关闭其 RR3 遗留项。

## 收尾判断

RW33 基线与范围、RW34 报告修复、RW35 必要回归、RW36 增量交接均通过。结合已独立验证的 v5 基线，**主框架本阶段工程验收通过，本次报告边界返工结束**。

真实成交校准仍为 **UNCALIBRATED**。工程验收不代表策略收益或真实成交准确性已获验证；M1/M234 历史数据接入及真实成交校准按后续阶段处理。
