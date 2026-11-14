# M1234 交易模拟 v3 独立复验

日期：2026-11-06，北京时间。对象：RW09–RW16 的 `20261106T061419Z-second-rework-v3` 封存交接包。

**结论：NEEDS_REWORK。原 RR1 终态漏检已关闭；仍有一个 P2 问题阻止最终验收：数据库快照持续写入失败时，失败输出及重新打开后的报告、核账可回退到旧 VALID 快照，输出 PASS。**

现有 700 项回归首次独立复跑全部通过。两个原终态反例、正常持久化下的失败传播、冻结源码回放和本轮 12 场公共工程证据均通过。本次新增故障测试为 3 failed / 1 passed，三个失败是同一问题的不同出口。

本次仅新增独立验证材料、本文及工作区进度说明；没有修改产品实现、原测试、封存包、交易配置或既有服务，没有新增公共采集或真实交易。

**对象与身份**

- [交接说明](/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T061419Z-second-rework-v3/HANDOFF.md)
- 实现 SHA256：`3c9530cdf5cada7bb20429b7d3a72eaedd9b910b97ebe7eac70484508bb86f25`。
- 源码归档 SHA256：`a99b1edaf4a3f40e72747090b50cf87a5be86aa9b49bdac00a08b239c63089a7`。
- 模型 `m1234-queue-v3`，输入 schema 2，快照 schema 3，有效性合同 `arrival-history-v1`。
- 独立证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03`。
- 验收依据：[RW09–RW16 合同](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_SECOND_REWORK_ITEMS_20261106.md)。

**RR2 · P2 · 持续写盘失败后，旧快照重新获得成功和样本资格**

主位置：[runner.py:176](/home/ubuntu/polymaker/src/polymaker/m1234_sim/runner.py:176)。关联入口：[report.py:23](/home/ubuntu/polymaker/src/polymaker/m1234_sim/report.py:23)、[audit.py](/home/ubuntu/polymaker/src/polymaker/m1234_sim/audit.py)。影响 RW11、RW12。

`failed_output()` 在关闭引擎、写入失败状态都未成功时，仍调用 `publish(directory, [summary(engine.store.path)])`。`summary()` 从数据库读取上一次提交的状态和审计，不合并当前引擎的失效事实，也不识别已成功保存的同运行 `failure.json`。因此，失败处理函数可以同时写出 FAIL 的失败文件和 PASS 的业绩报告。

独立复现步骤：

1. 使用真实 `Engine.ingest()` 和模拟订单路径，生成两个情景各 10 份、价格 0.095 的成交，保存一个正常提交的 VALID 快照。
2. 仅对快照写入持续注入 `OSError`；没有手工修改有效性字段或数据库记录。原始冲突通知的源时间为 0.055，订单到达为 0.105，可得时间为 0.160。
3. 接入该通知。新代码正确识别 RR1，内存状态为 INVALID，并尝试记录 `SIM_CONSTRAINT_ORDERING_CONTRADICTS_KNOWN_FILL`；快照/事务未能提交。
4. 执行实际 `failed_output()`。`failure.json` 和 `replay.json` 成功保存 FAIL，但 `latest.json` 从旧快照生成 PASS。
5. 按失败进程退出的事务语义回滚未提交内容并关闭数据库，再调用真实 audit/report CLI。二者仍退出 0 并返回 PASS。

| 观察位置 | 实际结果 | 应达到的结果 |
| --- | --- | --- |
| 引擎内存、失败输出及 failure.json | INVALID / FAIL | 已正确发现和记录失败 |
| 自动生成的 latest.json | PASS / VALID | 明确失败或拒绝发布有效业绩 |
| 已冲突的 M2 成交 | filled_shares=10，cost=0.950，sample_eligible=true，performance_eligible=true | 只能作为失效诊断事实，不获得有效样本资格 |
| 重新打开后的 audit CLI | 退出 0，status=PASS，acceptance_status=PASS | 识别同运行的持久失败，不能授予模型验收通过 |
| 重新打开后的 report CLI | 退出 0，status=PASS | 继续失败，不从旧快照恢复有效业绩 |
| 未注入故障的正常数据库 | audit/report 均退出 0、PASS | 正常使用保持通过 |

证据：[完整状态对照](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/failed-snapshot-probe.json)、[可复现测试](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/test_failed_snapshot_validity.py)、[3 失败 / 1 通过结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/snapshot-tests.log)。具体子命令与退出码位于独立目录的 `snapshot-tests-tmp/`。

该反例不声称原运行会退出 0，也不声称 12 场正式验收命令会把这个故障运行接纳。问题是原运行已经明确失败后，其报告和独立核账入口仍将旧数据标为有效，违反 RW11 对失败持久传播及样本资格的要求。它没有在本轮正常公共运行中被观察到。

现有 [写入失败测试](/home/ubuntu/polymaker/tests/test_m1234_sim_validity.py:137) 在关闭引擎前恢复了写入器，随后成功保存 INVALID；它验证了暂时故障恢复，没有覆盖“写入持续失败时仍需导出失败诊断及重新打开”的路径。

从仓库根目录复现，不覆盖本轮证据：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PYTHONPATH=src:tests \
  .venv/bin/python -m pytest -q \
  research_outputs/m1234_sim/independent/astra-20261106-rework-03/test_failed_snapshot_validity.py \
  -p no:cacheprovider
```

修复验收条件：

1. 失败输出不能用较旧的成功快照覆盖当前已知失败。数据库不可更新时，仍可输出明确失败的诊断；如果无法可靠合并，拒绝发布有效业绩。
2. 重新打开后的报告、核账及相关验收入口须识别与运行/数据库身份及输入水位关联的持久失败证据，防止把其他运行的失败错误套用，也防止旧快照绕过同运行的失败。资金算术结果可以单列，模型验收和样本资格不能为成功。
3. 保留原成交与资金历史，不通过改余额、删成交或只加 UNKNOWN 修复。若所有存储都失效，应明确返回失败，不承诺无法落盘的结果。
4. 正式纳入持续写失败、失败诊断输出、关闭重开及正常对照测试；修复后按既有版本和交接合同更新相关证据。

**本轮独立通过项**

| 检查 | 实测结果 | 证据 |
| --- | --- | --- |
| 当前实现与冻结源码 | 164 个实现文件一致，源码集合无缺漏；归档整包哈希一致 | [identity.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/identity.json) |
| 新包完整性 | 3,355 个文件、3,643,322,046 字节哈希与大小一致；302 个已声明符号链接目标一致 | [manifest-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/manifest-independent.json) |
| 历史保护 | 四个历史根目录共 9,316 个文件、12,203,936,924 字节，哈希和文件集合均未变 | [history-preservation-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/history-preservation-independent.json) |
| 完整回归 | 首次独立运行 700 passed，0 failed / errors / skipped，190.27 秒；与批量扫描和重放错开 | [regression.log](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/regression.log) |
| 静态检查 | 模拟模块、两项验收/比较脚本及正式模拟测试 Ruff 通过 | [ruff.log](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/ruff.log) |
| 正常与两类原 RR1 人工样本 | 从冻结归档重新执行，正常命令退出 0，两类冲突 replay/audit/compare 按合同退出 1；故障保持 INVALID | [冻结源码复现](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/reproduction/result.json) |
| 人工样本重复性 | 新执行结果分别与封存的两遍结果独立逐行比较，状态和全部审计字段一致：正常 611 行、完整成交冲突 83 行、撤单后冲突 100 行 | [comparisons-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/comparisons-independent.json) |
| 人工经济核账 | 正常 M3 成交 5 份、结算现金 22.525；完整成交故障现金 15.00050、撤单后部分成交故障现金 17.525，故障样本只通过算术核对 | [artificial-accounting-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/artificial-accounting-independent.json) |
| 12 场完整输入检查 | 全量重读，序号、时钟、水位、快照与验收检查通过 | [public-12-verification.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/public-12-verification.json) |
| 公共独立核账 | 12 场、120 个独立账户、50 次物理提交通过；审计资金、库存与结算重新计算后吻合 | [accounting-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/accounting-independent.json) |
| 含 tick 复核的完整公共场次 | 1793949900：150,078 个输入；完整状态相同、111,327 条审计逐行一致 | [公共复现结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/reproduction/public-replay.log) |
| 额外检查实际产生模拟成交的完整场次 | 1793951700：226,521 个输入；完整状态相同、244,027 条审计逐行一致 | [成交场次重放](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/public-filled-replay.log) |

上述逐行比较均未使用诊断顺序豁免。独立核账脚本直接读取审计表，以 Decimal 重算经济变化，不调用撮合器生成预期。

归档逐文件清单中的 376 项全部匹配，另含 13 份辅助合同/工具，受已验证的整包 SHA256 覆盖。独立校验脚本首轮把这 13 项误归为哈希不匹配，已区分“清单外辅助文件”与“列入文件哈希错误”，原结果和更正说明均保留在 [归档核对结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03/archive-all-files-independent.json) 所在目录。这不构成交接包缺陷。

**公共运行与解释边界**

接受本次核验的是同一最终源码的 `forward-v3-01`：北京时间 2026-11-06 15:05–16:05 的 12 个市场窗口，市场起点 1793948700 至 1793952000，程序记录运行 3721.03 秒，监督命令记录实际退出码 0。12 场官方结果均已处理，每场选择一份最终账本。

原记录包含 2,501,371 个行情帧、22 条 GAP；其中 9 条有 T300 后计划收场的时序说明，其余缺口及恢复风险继续保留。完整窗口不等于无缺口覆盖，VALID 也不代表真实排队模型已经校准。独立资源汇总得到 RSS 峰值 138.72 MiB、最大采样输出 1638.33 MiB、最小剩余磁盘 7476.17 MiB、写队列峰值 358，均在冻结容量内。

本轮确实产生了自然模拟成交：在市场 1793951700，cancel-half 情景的 M1-live-mirror、M1-virtual 各成交 25 份、价格 0.04，各自成本 1 USDC、结算现金 19 USDC，trades-only 同场成交 0。两账户是独立反事实，不能将份数或收益汇总为一个真实组合。成交所在场次的额外完整回放和独立资金核算已通过。真实成交校准仍为 **UNCALIBRATED**，费用口径继续按原披露，不据此推断实盘成交概率或收益。

**工作项判定**

| 范围 | 独立结论 |
| --- | --- |
| RW09 | 通过：返工起点、旧反例及历史证据可追溯 |
| RW10 / 原 RR1 | 通过：终态订单检查与两情景的原反例已关闭，合法对照保留 |
| RW11 | **未通过：持续写失败时，报告与重新打开的核账仍可输出 PASS** |
| RW12 | 已有路径测试通过；新增持续故障边界 3 失败、1 正对照通过，覆盖尚需补齐 |
| RW13 | 声明的 700 项完整回归通过；不能替代新增故障验收 |
| RW14 | 已交付正常及两类原 RR1 样本的确定性和故障状态通过 |
| RW15 | 本版本 12 场公共运行、完整回放、输入检查和核账通过 |
| RW16 | 封存完整性通过；最终独立复验为 NEEDS_REWORK |

下一轮返工聚焦 RR2 的持续失败输出和重新打开后的有效性判定，保留本轮已通过项与全部新反例。
