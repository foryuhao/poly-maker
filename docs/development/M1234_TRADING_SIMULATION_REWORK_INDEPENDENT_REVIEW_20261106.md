# M1234 交易模拟返工版独立验证

验证日期：2026-11-06（北京时间）。对象：RW00–RW08 的 `20261106T014400Z-rework-v2` 封存交接包。

**结论：NEEDS_REWORK，暂不接受 RW00–RW08 的最终验收。** 原 R1、R2、R4 在本次验证范围内通过；R3 的原始反例已修好，但新增独立测试发现一个 P1 缺陷：有成交历史的终态订单被排除在迟到 tick 冲突检查之外。全部成交与部分成交后撤单两种路径均可复现。

本次仅新增独立验证证据和本文，没有修改实现、原测试、封存包、交易配置或既有服务，也没有新增公共前向运行或真实交易。

**验证对象与身份**

- [封存交接说明](/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T014400Z-rework-v2/HANDOFF.md)
- 实现 SHA256：`f107c51962ebd60acc2bff8d18081b494a8cdea945f7107faed45a3ed5be12b5`；模型 `m1234-queue-v2`；输入 schema 2；seed 1234。
- 源码归档 SHA256：`403522bf1b5be83992fb278930d0019c72b3354405ebbc517ac10ffd8bac99c4`。
- 独立证据根目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02`。

**RR1 · P1 · 终态订单漏检迟到的到达约束冲突**

位置：[broker.py:300](/home/ubuntu/polymaker/src/polymaker/m1234_sim/broker.py:300)。关联原 R3 / RW03，并使 RW05 的交互边界验收不能关闭。

`update_constraints()` 仅检查 `LIVE`、`PARTIAL`、`CANCEL_IN_FLIGHT`。订单一旦进入 `FILLED` 或 `CANCELLED`，即使新收到的 WS 约束源时间早于其模拟到达时间，且旧报价不符合新 tick，也会直接跳过已有成交冲突检查。后面的 `SIM_CONSTRAINT_ORDERING_CONTRADICTS_KNOWN_FILL` 因而只保护仍在活动状态的订单。

这与封存的 [arrival-v2.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T014400Z-rework-v2/contracts/arrival-v2.json) 不一致：合同明确规定，迟到约束与已经记入的成交冲突时，应显式报错，不能仅补 UNKNOWN，更不能继续保留确定成交。

独立反例通过真实 `Engine.ingest()` 路径执行，两个排队情景均受影响：

| 时间（秒） | 输入或模型动作 |
| --- | --- |
| 0.005 | tick 更新为 0.001；提交价格 0.095、数量 10 的模拟买单 |
| 0.105 | 模拟订单按当时可得的旧约束到达并挂出 |
| 0.120 | 收到卖出打印，模型累计成交 10 份并进入 FILLED |
| 0.160 | 收到 tick=0.01 的通知；其源时间为 0.055，早于订单到达，0.095 不符合该精度 |

**实际结果：无错误、无覆盖缺口，订单仍为 `FILLED`、`uncertain=False`，库存 YES=10，支出 0.950，现金 19.050。** 将打印量改为 5 份而保持订单活动，代码会正确抛出冲突错误。另一个反例先成交 5 份，再于 0.130 请求撤单、0.340 确认 CANCELLED，0.400 收到源时间同为 0.055 的通知，也会漏检。

这里需要处理的是后来证据对已记入成交的否定或不确定性，不能倒用未来信息改写当时决策，也不能把“订单生效后才发生 tick 变化”的正常存量订单规则用于这个反例。

新增独立测试结果为 **2 failed / 3 passed**：

| 用例 | 结果 |
| --- | --- |
| 活动中的部分成交遇到冲突 | PASS：显式抛出合同规定的错误 |
| 全部成交后遇到同一冲突 | FAIL：终态跳过检查 |
| 部分成交、撤单完成后遇到同一冲突 | FAIL：终态跳过检查 |
| 价格 0.09 仍符合新精度 | PASS：保留合法成交 |
| tick 的源时间晚于订单到达 | PASS：遵守已声明的存量订单规则 |

证据：[独立测试](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/test_terminal_tick.py)、[测试结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/terminal-tests.log)、[状态与资金明细](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/late-tick-probe.json)。

可从仓库根目录复现；新建临时目录，避免覆盖本次证据：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PYTHONPATH=src:tests \
  .venv/bin/python -m pytest -q \
  research_outputs/m1234_sim/independent/astra-20261106-rework-02/test_terminal_tick.py \
  -p no:cacheprovider
```

修复验收条件：

1. 以订单到达及成交历史判断是否存在约束冲突，覆盖 FILLED、CANCELLED 等有成交历史的状态，不能只看当前是否活动。
2. 冲突必须影响整段模拟结果的有效性并显式暴露；不能保留确定成交后只补 UNKNOWN，也不能静默删除成交、调整资金掩盖问题。
3. 保留合法价格及真正后发生 tick 变化的通过对照；不引入未来信息回填决策。
4. 补入正式回归，并验证错误能从订单层传播到运行、报告和验收状态，不被结束清理或异常处理吞掉。
5. 修复后按既有 RW05–RW08 合同更新源码身份、回归、回放和最终交接证据。旧包与本次证据继续保留。

**已经独立通过的检查**

| 检查 | 结果 | 独立证据 |
| --- | --- | --- |
| 封存完整性 | 5,599 个文件、5,316,221,966 字节，全部哈希一致，无未列入且未豁免文件 | [manifest-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/manifest-independent.json) |
| 当前实现与归档 | 163 个实现文件一致；归档哈希一致；结束前再次核对无变化，所有源码可解析，模拟模块及其正式测试 Ruff 通过 | [source-final-check.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/source-final-check.json)、[ruff.log](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/ruff.log) |
| 原历史证据保留 | 1,289 个历史文件哈希和大小一致 | [history-preservation-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/history-preservation-independent.json) |
| 交接声明的完整回归 | 单独复跑 668 passed，0 failed / errors / skipped；首轮失败如实保留，详见下文 | [regression-retry.log](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/regression-retry.log) |
| 人工完整样本回放 | backlog-partial、precision-arrival 各两遍，4 次逐行审计差异均为 0，核账均 PASS | [fixtures-command.log](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/fixtures-command.log) |
| 中断恢复 | tick 复核未完成、深度归因未完成两组恢复 PASS；缺失区间保持 UNKNOWN | [恢复结果一](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/fixtures/recovered-tick.log)、[恢复结果二](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/fixtures/recovered-attribution.log) |
| R4 校准接口 | 相同 4+6 份成交只匹配 1 个物理订单，量差 0、首笔等待差 0；逆序输入结果逐字一致；无订单总量证据时不声称完全成交 | [calibration-normal.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/fixtures/calibration-normal.json) |
| 最终公共 12 场完整输入 | forward-final-02 的完整输入重读及水位检查 PASS，共 2,250,284 个行情帧 | [public-12-verification.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/public-12-verification.json) |
| 独立经济核账 | 12 场、120 个独立账户通过；按审计事实重新计算预留、支出、库存和结算，与最终快照一致 | [accounting-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/accounting-independent.json) |
| 公共整场冻结源码回放 | 市场 1793940000，245,674 个输入；完整状态一致，290,625 条审计记录逐行逐字段相同，无诊断顺序豁免 | [comparison-independent.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/comparison-independent.json) |

首轮完整回归为 **655 passed / 13 failed**，耗时 422.70 秒；失败集中在原 M1 和 BA 的真实计时测试，出现账户读取过期、未能进入提交、窗口计数或超时等。首轮运行与其他独立重放/核账任务重叠，且使用较低调度优先级。在这些任务完成后，以普通优先级、不修改源码/测试/阈值，完整原样复跑得到 **668 passed**，耗时 154.09 秒。证据：[首轮日志](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/regression.log)、[复跑命令与退出码](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-02/regression-retry-command.json)。这表明存在时序敏感性；本次不把这些旧测试的首轮失败直接归为返工版功能回归，也不声称一次运行全部通过。新增 RR1 的两个失败独立于这些旧测试。

**公共证据的范围**

本次接受核验的是 `forward-final-02`，北京时间 2026-11-06 12:25–13:25 的 12 个市场窗口，市场起点 1793939100 至 1793942400。运行总时长 3813.375 秒，监督进程记录退出码 0。前一次 `forward-final-01` 的退出证据缺口仍保留，没有拼接进这 12 场。

公共记录包含 24 次 tick 通知、12 次重复、12 次约束变化、12 次复核请求；6 个结果因目标已经变化而失效，6 个成功，累计未复核时长约 5.095 秒。两个视图的恢复逻辑及请求/响应可得时间已通过正式测试与包含 tick 变化的完整公共回放检查。

资源记录的 RSS 峰值为 125.48 MiB，最大采样输出 1364.12 MiB，最小剩余磁盘 9505.07 MiB，写队列峰值 296，符合冻结容量。保留原始质量诊断，包括最大处理延迟约 1041.14 ms 和每场结束时的断连记录；没有将其删除或宣传为无延迟输入。

12 场合计有 **28 次模拟物理提交、0 次模拟成交**（各策略/情景是独立反事实账户）。因此公共核账通过不代表已经证明成交模型准确，人工样本的正确性仍然必要。真实成交校准继续为 **UNCALIBRATED**，收益口径和原有交付边界不变；零自然成交本身不构成本轮额外返工理由。

**工作项复验状态**

| 范围 | 本次独立判定 |
| --- | --- |
| RW00 | 起点、旧失败和历史证据可追溯 |
| RW01 / 原 R1 | 通过：原 80/85 积压反例及更强断言、重复归因和共享量测试通过 |
| RW02 / 原 R2 | 通过：有界复核、失效响应处理、双视图恢复及离线复现通过 |
| RW03 / 原 R3 | **未通过：原核心反例已修复，终态历史冲突仍漏检** |
| RW04 / 原 R4 | 通过：订单级聚合、行序无关及不完整证据处理通过 |
| RW05 | 原声明回归复跑通过；新增独立边界 2 项失败，交互验收未关闭 |
| RW06 / RW07 | 本封存版本的身份、回放及 12 场运行证据通过；这些证据不能覆盖 RR1 的成交语义错误 |
| RW08 | 交接包完整性通过；最终独立结论为 NEEDS_REWORK |

返工聚焦 RR1 的终态历史校验及错误传播。再次交回时同时保留本次通过项、首轮计时测试失败、完整复跑结果和新增反例，不能用原 668 项通过替代新增边界验收。
