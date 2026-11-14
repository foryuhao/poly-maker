# M1234 交易模拟 v4 独立复验

日期：2026-11-07，北京时间。对象：RW17–RW24 的 `20261106T150054Z-third-rework-v4` 交接包。

**结论：NEEDS_REWORK。原 RR2 的离线失败输出及重开报告/核账已修复；在线协调器仍有一个 P2 缺口：持续写入失败使当前市场未进入结束状态，汇总跳过这个已失效市场，已有健康市场时仍输出 PASS。**

735 项正式完整回归首次独立运行全部通过，原四项 RR2 独立反例全部通过。新增实际协调器测试四项失败，覆盖完整成交、部分成交撤单两种冲突，以及有无既有健康市场的组合。正常公共运行、完整回放、独立核账和封存完整性通过，不能代替这条故障路径的验收。

本次没有修改产品源码、正式测试、交易配置或封存材料，没有启动新的公共采集或真实交易，没有修改既有服务。新增材料仅为独立复验目录、本文和进度追加说明。

**对象与身份**

- [交接说明](/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T150054Z-third-rework-v4/HANDOFF.md)；[RW17–RW24 验收合同](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_THIRD_REWORK_ITEMS_20261106.md)。
- 实现 SHA256：`11e913a748120d315f30036ece21645023dd67bee9f64d4e366852161c26ef1c`。
- 归档：`sources/final-source-v4-r2.tar.gz`，SHA256：`446671724ceac97a6687bb1bfc3cbd70482535c7e63692cf38b051eda9d40e10`。
- 模型 `m1234-queue-v4`，输入 schema 2，快照 schema 4，有效性合同 `arrival-history-v2`，失败证据 schema 1。
- 独立证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04`。

**RR2-LIVE · P2 · 未结束的失效市场被在线汇总跳过，持续写失败还会中断完成记录**

主位置：[runner.py:461](/home/ubuntu/polymaker/src/polymaker/m1234_sim/runner.py:461)。关联位置：[清理队列异常分支](/home/ubuntu/polymaker/src/polymaker/m1234_sim/runner.py:610)、[运行最终清理](/home/ubuntu/polymaker/src/polymaker/m1234_sim/runner.py:702)。这是 RR2 在线传播尚未关闭的分支，不否定已经通过的离线修复。

`update_report()` 在检查有效性前先跳过 `not engine.ended` 的市场。实际协调器清理队列时遇到迟到 tick 冲突，已将内存和独立失败证据设为 INVALID；但持续快照写入异常又从 `engine.invalidate()` 抛出，使执行无法到达 END。外层 `run()` 随后调用 `finish_reports()`，失效市场因此没有进入汇总。之后未隔离异常的 `engine.persist()` 再次失败，后续录制器关闭、资源和 `completion.json` 输出也未执行。

独立测试使用真实 `LiveRun.run()`、原生策略输入和真实报告/数据库消费者；公共 API 与订阅替换为离线夹具。在合法经济前缀提交后，仅持续替换快照写入为 OSError，直到协调器退出和报告生成结束。没有修改有效性字段、经济账本或手工补发 END。健康前置市场通过正常 `Engine.ingest()` 完整处理，用于验证已有成功结果时的汇总行为。

| 观察项 | 没有前置健康市场 | 已有一个前置健康市场 |
| --- | --- | --- |
| 当前冲突市场内存及绑定失败证据 | INVALID | INVALID |
| 当前市场 `ended` | false | false |
| 当前市场已提交快照 | VALID 的较早前缀 | VALID 的较早前缀 |
| 根目录 `latest.json` | PENDING，0 个市场 | **PASS，仅保留健康市场** |
| 根目录 `invalid_markets` | 空 | 空 |
| `completion.json` | 未生成 | 未生成 |
| 重新打开冲突市场的 audit/report | 均退出 1，正确拒绝 | 均退出 1，正确拒绝 |
| 对该停止运行执行验收工具 | RUNNING，退出 0 | RUNNING，退出 0 |

两种终态冲突均复现上述结果，合计四项失败。网络替身的任务已被协调器取消；测试在记录上述观察后做额外资源清理，避免遗留测试线程，该清理不用于修复或改变被测报告。

证据：[四例状态摘要](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/coordinator-probe-summary.json)、[可执行独立测试](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/test_coordinator_persistent_failure.py)、[四项失败日志](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/coordinator-02.log)。各例的观察、重开命令输出和正式验收命令/退出码保存在 `coordinator-02-tmp/`。

本反例中 `LiveRun.run()` 抛出 OSError，没有把运行异常吞成正常返回。验收工具输出的是 RUNNING，也不是正式验收 PASS。问题是已知失败没有贯通在线汇总与最终状态。它没有在本轮正常 12 场公共运行中被观察到。

现有 [在线报告测试](/home/ubuntu/polymaker/tests/test_m1234_sim_persistence_matrix.py:354) 主动发送 END，并确认 `engine.ended` 后才检查汇总，覆盖了已结束市场的缓存和在途报告；没有覆盖实际协调器在到达 END 前因持续写入失败退出的分支。

关闭条件：

1. 已确认 INVALID 的市场无论是否到达 END，都必须出现在失败诊断中；运行汇总不能因跳过该市场而成为 PASS 或 PENDING。正常前置市场的历史事实可以保留。
2. 清理和快照失败应分别记录，不能让单个清理异常阻止其余可执行的关闭及失败状态输出。在其他文件可写的本反例中，须留下明确的运行失败结果；不得伪造 END 或完整窗口。
3. 实际协调器的上述四例应通过，保持故障直到报告和重开验证结束；保留已通过的离线、正常运行及合法成交对照。已停止的失败运行不能继续被呈现为仍在运行。

从仓库根目录复现，使用新的输出目录：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PYTHONPATH=src:tests \
  .venv/bin/python -m pytest -q \
  research_outputs/m1234_sim/independent/astra-20261107-rework-04/test_coordinator_persistent_failure.py \
  -p no:cacheprovider \
  --basetemp "research_outputs/m1234_sim/verification/rr2-live-$(date -u +%Y%m%dT%H%M%SZ)"
```

当前预期为四项失败。此测试只操作新模拟目录，无需网络、凭据或真实交易。

**独立通过项**

| 检查 | 实测结果 | 证据 |
| --- | --- | --- |
| 源码与归档身份 | 164 个实现文件、391 个归档清单路径及整包哈希一致；复验结束再次核对未变 | [身份检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/identity.json)、[最终检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/source-final-check.json) |
| 封存包 | 14,811 个文件、3,908,715,536 字节及 824 个声明符号链接一致，文件集合无增减 | [封存检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/manifest-independent.json) |
| 历史保护 | 六个历史根目录，14,392 个文件、16,083,134,021 字节及文件集合一致 | [历史检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/history-preservation-independent.json) |
| 完整回归 | 首次独立执行 735 passed，0 failed/errors/skipped；pytest 用时 222.46 秒，普通优先级，与批量扫描及回放错开 | [回归日志](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/regression.log) |
| 原 RR2 四项 | 原独立测试直接重跑全部通过；正式纳入版本与原件 AST 一致 | [测试汇总](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/tests-independent-summary.json) |
| 测试夹具迁移 | 原 302 条断言不变；除读取位置与导入外模块 AST 一致；19 项固定参数与不可变 v3 归档两份 M1 profile 精确相同 | [独立迁移核对](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/fixture-migration-independent.json) |
| 静态检查 | 模拟模块、比较/验收脚本及相关正式测试 Ruff 和语法检查通过 | [Ruff 日志](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/ruff.log) |
| 冻结源码离线复现 | 正常、两类 RR1、两类 RR2、重开和源失败续跑拒绝，以及两场公共回放；30 条子命令退出符合预期 | [复现结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/reproduction/result.json) |
| 人工样本严格比较 | 各与封存两遍结果比较：正常 611 行、RR1 完整 83 行/撤单 100 行、RR2 完整 71 行/撤单 92 行，均逐行一致 | [独立完整比较](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/comparisons-independent.json) |
| 人工经济核账 | 正常及四个故障样本独立算术通过；RR2 旧 VALID 前缀与更晚 INVALID 证据同时保留，未授予故障有效性 | [人工核账](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/artificial-accounting-independent.json) |
| 12 场完整输入 | 全量重读 1,993,631 条 FRAME；事件序号、时钟、水位、模型身份和正常验收通过 | [公共输入检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/public-12-verification.json) |
| 公共独立核账 | 120 个独立账户、62 条按情景记账的物理提交、8 条模拟成交事件；110 个账户已结算，其余对应待定市场 | [公共核账](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/accounting-independent.json) |
| 完整公共回放 | tick 场次 1793985900：147,973 条审计；成交场次 1793988000：103,005 条审计。状态相同、原始有序审计零差异 | [逐行比较结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/comparisons-independent.json) |

逐行比较没有启用诊断顺序豁免。独立资金脚本直接读取 SQLite 审计，用 Decimal 重建现金、预留、库存和结算，不导入撮合器。每个数据库身份与定义、实现、市场及必需关联文件独立核对；不同执行的实例 UUID 分别验证，共同定义和故障事实/水位精确比较。

**公共证据的范围**

最终验收采用同一冻结实现的 `forward-v4-02`，北京时间 2026-11-07 01:15–02:15 的 12 个市场窗口，监督命令实际退出 0，程序记录 3781.02 秒。首次 `forward-v4-01` 因并发读取问题失败，材料保留，未计入最终 12 场；修复采用共享只读事务，新增两项并发提交测试已包含在本次 735 项回归中。

独立资源复算：RSS 峰值 135.09 MiB、最大采样输出 1164.75 MiB、最小剩余磁盘 15395.70 MiB、写队列峰值 223，符合原限制。封存前后的服务身份一致，复验结束再次读取仍一致。见 [资源与运行检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/public-resources-independent.json)。

14 条公共输入缺口继续保留；完整观察窗口不等于无缺口覆盖。封存时 11 场已有官方结果，1793988600 待定；本次使用封存证据离线验证，没有补造或覆盖待定结算。

自然模拟成交位于 1793988000，cancel-half 情景的两个 M1 profile 各成交 11.450 份、价格 0.04，各自成本 0.45800、现金 19.54200、预留 0.54200；订单仍带不确定性。两个账户是独立反事实，不能合并为真实组合，也不能将已知成交部分当成全部确定收益。真实成交校准继续为 **UNCALIBRATED**。

**工作项判定与验证器更正**

| 工作项 | 独立判定 |
| --- | --- |
| RW17 | 通过：起点、原反例和历史证据可追溯 |
| RW18 | 原离线持续失败输出通过；在线整体传播仍需收尾修复 |
| RW19 | **未通过：在线汇总仍可能跳过未结束的失效市场** |
| RW20 | 已有测试通过；实际协调器持续故障新增四例失败，覆盖未闭合 |
| RW21 | 通过：735 项完整回归、夹具等价迁移、静态检查和独立核账；不代替新边界 |
| RW22 | 通过：交付正负样本和冻结源码复现通过 |
| RW23 | 通过：本版本 12 场正常公共运行、输入、回放及核账通过 |
| RW24 | 封存完整性通过；最终独立状态为 NEEDS_REWORK |

复验工具自身的两项更正均留存，未计作产品缺陷：新增协调器探针首轮在保存 Decimal 诊断时序列化失败，修正保存方式后，四例均到达并失败于报告状态断言；归档检查首轮漏计三个符号链接配置，随后对链接解析内容逐项哈希，391 个声明路径全部通过。原日志、原探针版本、首次归档结果与更正说明均在独立目录。

后续返工聚焦 RR2-LIVE 的在线汇总与失败收尾，保留本轮已通过项，不需要重做撮合模型或测试参数迁移。
