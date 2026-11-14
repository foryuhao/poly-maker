# M1234 交易模拟独立验证与返工清单

验证日期：2026-11-06。对象为 `20261105T145500Z-v1` 封存交接包及其对应源码。结论：**需返工，暂不接受 W00–W13 全部完成的最终验收。**

输入、策略适配、模拟订单、账本、结算和回放的主要闭环已经实现；封存证据可以复现。但独立反例发现会制造错误成交的边界缺陷，另有运行中价格精度复核缺失及部分成交校准关联错误。这些是工程验收问题，与缺少盈利证明或真实成交校准无关。

本次仅新增独立验证材料和本文，没有修改实现、原测试、封存包、既有交易配置或服务。真实成交状态继续为 UNCALIBRATED；本次没有新增公共前向运行或真实交易。

**验证对象与证据**

封存目录：[交接包](/home/ubuntu/polymaker/research_outputs/m1234_sim/acceptance/20261105T145500Z-v1/HANDOFF.md)。实施身份 `a528bdd79c1b2d70eb9af5585358be358d7a269135a2952d890328d560e1d59e`，源码归档 SHA256 `752320e70f92e3ffc9aaa5be88ebebe8a7169e1d92630736a6e26589589a13b1`。

| 独立检查 | 结果 | 证据 |
| --- | --- | --- |
| 封存文件及当前源码哈希 | 1,137 个证据文件、176 个源码文件，全部一致 | [manifest-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/manifest-independent.json) |
| 原交接包全部指定回归 | 562 passed，0 失败、0 跳过 | [regression.txt](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/regression.txt) |
| 静态检查 | Ruff、compileall、git diff --check 均通过 | [static-checks.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/static-checks.json) |
| 12 场完整输入重新检查 | 通过；保留已披露数据质量诊断 | [public-validation.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/public-validation.json) |
| 另写 SQL 与 Decimal 核账 | 最终选择的 12 场、120 个独立账户通过；12 场官方结果已处理 | [accounting-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/accounting-independent.json) |
| 完整公共场次离线回放 | 166,881 个输入；完整状态哈希一致 | [public-replay.log](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/public-replay.log) |
| 另写逐输入审计差异核对 | 113,098 行；2,080 行诊断顺序变化，涉及 494 个输入块；候选、动作、经济事实有序差异为 0 | [comparison-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/comparison-independent.json) |
| 新增独立边界用例 | 4 failed、1 passed；失败对应下述 R1、R3、R4，R2 另有控制实验及公共输入证据 | [counterexamples-final.txt](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/counterexamples-final.txt) |

公共回放状态哈希为 `c8c2173f7ff5fd3244094154ae2debed73dd61db88e4a02a748bb402f0e52851`。严格原始日志比较仍为 DIFF；独立核对只允许同一输入内、同一连续原始诊断区段中完整字段多重集相等，其他行及其位置严格相等。接受交接包已披露的这一诊断顺序例外，不把它称为日志逐行一致。

资金守恒证明已记入账本的金额算术一致，不能证明撮合本来就应该产生这些成交。公共样本没有自然模型成交，也没有覆盖下面的错误撮合路径。

**R1　P1　积压中的成交与深度下降会重复推进队列**

位置：[broker.py:305](/home/ubuntu/polymaker/src/polymaker/m1234_sim/broker.py:305)、[engine.py:137](/home/ubuntu/polymaker/src/polymaker/m1234_sim/engine.py:137)。影响 W01、W05、W10。

`depth_decreases` 和 `trade_credits` 使用处理时刻 `self.now`，一秒后即将未归因下降分配为前方撤单；Engine 又在处理当前帧之前调用 `broker.advance()`。因此，已经收到但排队等待处理的直接成交可能来不及参与归因，随后再消耗一次已经被撤单假设缩短的队列。

独立人工场景：前方 80 份，自身 10 份；t=0.2 收到前方深度减少 80 的快照；对应 85 份卖出源时间为 0.21，接收时间为 0.22。当它在 0.25 被处理时，两种情景均成交 5 份；只把处理时间改为 1.25，trades-only 仍成交 5，cancel-half 却成交 10，且 `uncertain=False`。该成交消息接收时只有 10ms 源延迟，没有超过 500ms 接纳门槛。

返工与通过条件：

- [ ] 将归因时间与处理耗时分开，利用已记录接收/源时间及待处理输入水位；有相关消息尚未处理时，不提前完成撤单归因。
- [ ] 超出可确定覆盖的情况保持未归因或明确不确定，不能先创造成交，再仅靠资金核账判通过。不得通过放宽时效、取消积压门槛或吞掉成交来修复。
- [ ] 覆盖成交先到、深度先到、同帧、积压、归因边界、成交信用清理及穿越价等顺序。此反例应为确定的 5 份，或带明确缺口的未决风险，不能为确定的 10 份。
- [ ] 两情景逐笔核对可分配量、外部队列变化、实际模拟成交及未知剩余风险。

**R2　P1　运行中 tick 变化后没有官方复核路径**

位置：[runner.py:312](/home/ubuntu/polymaker/src/polymaker/m1234_sim/runner.py:312)、[signals.py:57](/home/ubuntu/polymaker/src/polymaker/m1234_sim/signals.py:57)。对照原 M1 虚拟基线：[m1_virtual.py:441](/home/ubuntu/polymaker/src/polymaker/m1_virtual.py:441)。影响 W01、W02、W07、W11。

新运行器只在 INIT 前读取 tick；之后既不响应 `tick_revision` 重新查询，也不记录和回放复核结果。WS 将盘口 tick 从 0.01 改成 0.001 后，`tick_verified` 仍为 0.01，原检测器 `legal()` 持续返回 false。即使后续盘口新鲜、连接正常，也不会消除这个不一致。原 M1 虚拟运行器会在 tick revision 变化时重新查询官方约束；仅比较裸检测器不能证明完整运行基线等价。

[离线控制实验](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/tick-reverification-probe.json)显示两个视图均由合法变为 `WS_TICK_UNVERIFIED`；补入与新 tick 一致的官方复核后，在同一新鲜盘口上恢复合法。公共记录中 6 场收到合计 24 条 tick 通知，包含重复通知；其中 `btc-updown-5m-1793902200` 在 T189.401 已发生变化，尚有约 50.6 秒新单窗口。这里不能把后续不可用解释为纯粹行情源质量问题。证据：[public-tick-changes.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/public-tick-changes.json)。没有据此推断一定遗漏多少机会或收益。

返工与通过条件：

- [ ] 增加有界公共 tick 复核，并记录请求对应 revision、结果实际可得时刻、token、成功/失败和冲突。
- [ ] 复核结果进入统一有序输入；回放消费记录，不访问网络。旧 REST 结果不能覆盖更新的 WS 约束，复核失败不能自动视为合法。
- [ ] 保留各视图接纳语义；补充完整 M1 基线运行路径对照，不只对照检测器函数。
- [ ] 覆盖重复通知、精度变细/变粗、查询失败、查询期间再次变化、重连、T240 后存量订单观察及 markout 的有效性。
- [ ] 更新受影响公共覆盖说明；旧 12 场的采集长度证据保留，不能继续作为修复版信号完整性证明。

**R3　P1　订单到达时没有重新检查价格精度**

位置：[broker.py:165](/home/ubuntu/polymaker/src/polymaker/m1234_sim/broker.py:165)。影响 W03、W04、W05、W10。

LIVE 处理只检查盘口和是否穿过卖价，没有验证 `price % book.tick`。独立场景在 tick=0.001 时提交 0.095 的买单，50ms 后 tick 变为 0.01，订单在 100ms 才到达。此时 0.095 已不符合新精度，但模型仍让它入队，并被随后 0.09 的卖出打印填满 10 份。

下单价格必须符合当前有效价格增量，这是交易所约束，不是可自由选择的排队假设。已核对 [Polymarket 官方交易约束](https://docs.polymarket.com/market-data/market-details#trading-constraints)。

返工与通过条件：

- [ ] 在模拟到达时，依据当时可用且有身份/版本记录的市场约束重新检查价格精度；未知约束与明确不合法应分别处理。
- [ ] 精度拒单生成明确拒绝事实，释放相应预留，不产生库存；拒绝回执交给既有 M1 规则，不误标为价格穿越拒绝而触发错误重报。
- [ ] 覆盖发送后精度变粗导致无效、变细仍有效、变粗后仍对齐、恰好同时间事件顺序，以及回执晚于拒绝。
- [ ] 此反例必须为 0 成交，账户和审计轨迹一致；已有订单因规则变化如何处理，与尚未到达的新单分开规定。

**R4　P2　部分成交校准重复使用第一条模型记录**

位置：[calibration.py:102](/home/ubuntu/polymaker/src/polymaker/m1234_sim/calibration.py:102)。影响 W12。

导入格式明确允许一个物理订单多次成交，但 `assess()` 对每条实际成交都寻找同一物理订单的第一条模型记录，没有聚合或可靠的一对一关联。实际与模型均为同一订单先成交 4 份、再成交 6 份时，完全相同的输入被输出为两项差异：0 份/0 秒，以及 -2 份/-1 秒；同一物理订单还被计为 2 个。调换模型记录行顺序，误差又变为 +2 份/+1 秒和 0。

返工与通过条件：

- [ ] 按完整物理订单键聚合双方累计成交、首笔等待及可比较的成交后指标；若需要逐笔关联，使用明确可靠的关联规则，不能重复使用第一条记录。
- [ ] `matched_physical_orders` 统计唯一物理订单；成交笔数另列。未匹配、多对多、缺失时间及实际/模型数据不足均明确输出。
- [ ] 补充同订单多次部分成交、行乱序、实际与模型拆分笔数不同但总量相同、多物理重试订单、重复 fill ID，以及校准/验证区间测试。
- [ ] 相同成交记录的误差必须为 0，输入行顺序不得改变结果；继续保持 UNCALIBRATED，接口修好不等于已真实校准。

**交回 Sol 的执行顺序与复验要求**

1. R1 修复归因；R2 接入复核事件；R3 使用到达时约束；R4 修复关联。按这些问题分别保存变更说明及失败前/修复后证据，不改写原封存包。
2. 将独立反例加入正式验收，并补齐各项通过条件。重新执行受影响模拟测试、原 M1/M234/共享行情回归及静态检查。现有 562 项全部通过只是基线。
3. 冻结新的模型、源码和输入版本。旧版回放仍使用旧冻结源码；新版比较保留原输入来源及版本转换依据，不绕过源码身份校验，不把旧报告改名为新结果。
4. 由于 R2 改变实际行情准入路径，用修复后的最终版本完成原清单规定的有限 12 窗口模拟验收，保存 tick 复核、资源、输入、结算与完整公共回放证据。边界情景仍由人工用例验证，不要求自然出现成交或人为调整阈值。
5. 更新进度与交接包，明确 W02/W04/W05/W12 的修复证据，并重新关闭 W10/W11/W13。保留旧包及本报告，交回独立复验。

原工作范围继续适用：仅新增模拟入口和公开只读查询；不改动既有服务，不进行真实试单。当前公共零成交、费用未计入且仅报毛收益、真实成交 UNCALIBRATED 都是已声明的交付边界，不作为额外返工理由。

**复现入口**

在项目根目录执行独立反例。当前封存源码的预期结果是 4 failed、1 passed；这些失败是本报告证据，不能删除或改成无条件通过。

```bash
cd /home/ubuntu/polymaker
PYTHONPATH=src:tests .venv/bin/python -m pytest -q research_outputs/m1234_sim/independent/astra-20261106-01/test_independent_boundaries.py
PYTHONPATH=src:tests .venv/bin/python research_outputs/m1234_sim/independent/astra-20261106-01/tick_reverification_probe.py
.venv/bin/python research_outputs/m1234_sim/independent/astra-20261106-01/review_checks.py
```

第一条命令使用 pytest 临时输出，不覆盖封存记录。后两条只写独立验证目录。独立公共回放及原回归的命令/输出见同目录 `public-replay.log`、`regression-command.json`；完整可复现命令和返工状态另见 [review.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-01/review.json)。
