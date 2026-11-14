# M1234 交易模拟升级进度

> 队列修复与巡检（2026-11-08 09:07，北京时间）：已完成连续模式待处理水位压缩、未变化价格精度的重复深拷贝优化及 socket 异常清理修复；113 项定向测试通过，49,799 条故障前输入回放的状态除源码定义哈希外一致。已在[新目录](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T090722-4f03ad)重启 M1–M4。五分钟巡检已启用，异常会唤醒当前对话复核修复；当前状态见[巡检记录](/home/ubuntu/polymaker/research_outputs/m1234_sim/monitor/latest.json)。源码实现哈希 `348c106ad81fd8519f45f3451c214231d8baa9417620b2cf10419e69ba904f46`。本次仍为实际连续运行验证中，旧失败轮次不并入新 288 场。

> 公共行情运行故障（2026-11-08 早间检查）：**24 小时任务已失败停止。** 00:52:26 行情采集队列溢出（`SIM_CAPTURE_QUEUE_OVERFLOW`），00:52:29 服务退出。仅完成 3 个五分钟窗口，第 4 场无效。信号 M1/M2/M3/M4 = 4/3/1/1；跨配置/情景物理提交 = 4/6/0/2；确认模拟成交均为 0，M1 有 4 笔尾部未决订单。运行整体 FAIL，不能算连续全天验证通过。见[核对结果](/home/ubuntu/polymaker/research_outputs/analysis/m1234_24h_status_20261108/result.json)。下方启动正常的记录仅反映启动检查当时状态。

> 公共行情运行（2026-11-08 00:35，北京时间）：**已启动 M1–M4 连续 24 小时模拟。** 后台服务 `polymaker-m1234-sim-24h-20261108t003035.service`，首个窗口为 11 月 8 日 00:35，计划观察至 11 月 9 日 00:35。首条心跳 9,777 帧、0 次断档、RSS 峰值 84.05 MiB；仅模拟成交。运行结果持续写入[本次目录](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T003035)，启动检查见 [startup-check.json](/home/ubuntu/polymaker/research_outputs/m1234_sim/forward/24h-20261108T003035/launch/startup-check.json)。当前为运行中，完整结果以结束后的终态与核账为准。

> 连续运行扩展（2026-11-08）：**M1–M4 的 24 小时模式已实现并完成定向验证。** 新配置为 288 个五分钟窗口，20 GiB 输出上限、4 GiB 磁盘余量、512 MiB RSS 上限；换场关闭已结束市场资源，待结算实例有界保留。人工时钟下 288 场协调器测试与首尾回放通过，另有 65 项配置/故障回归通过。真实行情连续 24 小时尚未启动；本次为后续扩展，不改写下方已冻结的第一阶段验收。见[运行说明](/home/ubuntu/polymaker/docs/development/M1234_CONTINUOUS_24H.md)及[验证汇总](/home/ubuntu/polymaker/research_outputs/analysis/m1234_24h_enablement_20261107/result.json)。

> 最新状态（2026-11-07）：**第一阶段整体工程验收 PASS，可交付。** RR3 与对应遗留要求已关闭；最终交付为 v5 基线加 RR3 补丁。真实成交校准仍为 UNCALIBRATED。见[第一阶段整体验收](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_PHASE1_ACCEPTANCE_20261107.md)。

> 交付后存储整理（2026-11-07 23:42，北京时间）：按用户要求，旧轮次 4,624 个模拟数据库、快照及附属文件已去重压缩并移除展开副本，目录从 21.03 GiB 降至 5.82 GiB。最终 v5、RR3、对应最终复验包，以及全部原始行情、源码和配置保留；62,507 个保留文件/链接复核通过，实际恢复检查通过。历史清单继续描述当时状态，已归档路径以[归档索引](/home/ubuntu/polymaker/research_outputs/m1234_sim/storage_archive/20261107/index.json)和[恢复说明](/home/ubuntu/polymaker/research_outputs/m1234_sim/storage_archive/20261107/README.txt)为准。见[清理结果](/home/ubuntu/polymaker/research_outputs/analysis/m1234_storage_cleanup_20261107/result.json)。本次未重新执行工程验收或更改策略，既有服务身份不变。

以下阶段摘要及实施/复验记录按原文保留，反映当时状态；当前结论以上方摘要和文末整体验收记录为准。

> 当前进度以文末 RW25–RW32 为准。v4 独立结论为 NEEDS_REWORK，本轮处理 RR2-LIVE。

> 当前进度以文末 RW17–RW24 为准。v3 独立结论为 NEEDS_REWORK，原 RR1 已关闭，RR2 本轮实施修复完成，等待 Astra 独立复验。

> 当前以文末 RW09–RW16 状态为准。RW00–RW08 DONE 是历史交接，最新 Astra 独立结论为 NEEDS_REWORK，RR1 本轮实施修复已完成，等待 Astra 独立复验。

> 当前验收以文末 RW00–RW08 返工状态为准；旧 W00–W13 DONE 仅为历史实施记录，Astra 已要求返工。

验收根目录：`research_outputs/m1234_sim/acceptance/20261105T145500Z-v1/`。
仅模拟；保留原工作区改动、现有服务与历史输出。工程、公共运行覆盖和真实成交校准分别验收。

| 工作项 | 状态 | 交付与证据 | 未完成部分 |
| --- | --- | --- | --- |
| W00 | DONE | baseline.json、contract.json、signal-contract.json；公开参数/源哈希/时区/依赖/服务/容量冻结，无凭据 | 无 |
| W01 | DONE | event-contract.json、events.py；fixtures/handoff-tape.jsonl.gz、replay/handoff-diff.json；录制/时钟/溢出/写盘故障验证 | 无 |
| W02 | DONE | tests/signal-consistency.json / handoff-regression.xml；原检测器逐事件对照，UUID 一一映射；M4 重建 ID 唯一性反例与修复证据 | 无 |
| W03 | DONE | policy.py 复用 M1Market/M1Ledger；官方上下界、双 profile、方向机会与 UNKNOWN 双物理请求验收 | 无 |
| W04 | DONE | broker.py；生效/回执/撤单分离，撤单先到 NOT_FOUND，取消在途与迟到事实验收 | 无 |
| W05 | DONE | fixtures/handoff-manual-80-85-5.json；共享 FIFO/精确部分成交/身份与深度归因；源时间因果、晚到/倒退隔离验收 | 无 |
| W06 | DONE | reconciliation/handoff.json 独立核账 PASS；近期撤单 GAP 风险恢复、ACCOUNT_OPEN、迟到事实重新预留并核账测试 | 无 |
| W07 | DONE | run/replay/recover/settle；T239/T245、跨 T240 候选存续、T300 未决、恢复、传输边界验收 | 无 |
| W08 | DONE | replay/handoff-1/latest.json、report.md；独立账户毛收益/未知范围/价格变化/分层/覆盖/有界后台报表 | 无 |
| W09 | DONE | replay/handoff-diff.json：1393 输入，两次完整状态与审计日志一致；对象/序列化动作顺序验证；reconciliation/handoff.json PASS | 无 |
| W10 | DONE | tests/handoff-regression.xml：最终冻结源码562 passed，含56项模拟验收；命令/退出码见 handoff-regression-command.json；静态检查、故障矩阵及失败运行拒绝证据 | 无 |
| W11 | DONE | live/forward-08/completion.json：12完整窗口、1436209帧、3757秒；reconciliation/public-acceptance.json 全输入/核账PASS；replay/public-full-01/comparison.json 状态/经济一致，诊断顺序例外单列；official-review-final.json 12结果全部处理；services-after.json 身份一致 | 无；公共自然成交覆盖不足另列，不调阈值补足 |
| W12 | DONE | calibration/import-format.json；离线接口测试通过；local-m1-coverage-final.json：只读评估已有 685 个 M1 账本，无可用真实成交生命周期；UNCALIBRATED | 真实精度未校准，数据缺口不伪造 |
| W13 | DONE | HANDOFF.md、work-items.json、final/verification-summary.json、final/latest.json/report.md、source-identity.json、sources/两份冻结包、manifest.json；离线复现/核账/续跑命令齐全 | 实施无未完成项；交 Astra 独立验证 |

## 记录

- 2026-11-05：读完工作清单和设计文档；检查工作区、当前服务、行情/信号/提交前实现。W00 冻结了当前实际参数，未读取钱包、环境秘密或凭据文件。
- 官方费用文档已查询；市场适用费率尚未冻结，默认仅毛收益且不计未经证实返佣。原行情连接及 Public API 的只读边界将复用。

- 自动化阶段：相关旧入口回归 526 项通过；扩展后的模拟/故障测试 30 项通过。人工事件带为明确标注的构造数据，不计公共运行覆盖。

- 公共试运行 forward-01 已启动，计划与设置在启动前落盘。最终 M1/模拟相关补跑：201 passed / 59.39s。禁止以当前短时运行替代 12 个完整窗口。

- forward-01 发现原 SessionFeed 突发读取循环不让出调度，触发 WS_QUEUE_OVERFLOW，已停止新增模拟并保留全部原始记录。属于工程缺陷，不能写成公共验收通过；修复后改用新 run ID。W01/W07/W10 的公共突发条件验证重新进行，旧测试通过记录保持历史事实。

- forward-01 在请求人工停止前已因 SIM_CAPTURE_QUEUE_OVERFLOW 自行退出（164.4 秒，0 完整场），记录无丢失伪装。定位原 reader 调度与新 broker 的 O(depth²) 档位构造。已修复为 opt-in reader 公平调度（旧默认 0 不变）及仅挂单价位的线性更新；增加到达前深度、跨账户深度归因测试。最终修复验收：169 passed / 37.62s，pre-forward02.xml；ruff 通过。forward-02 用新 run ID、原规则与原容量重跑，不调信号阈值。

- forward-02 完成 2 个完整窗口后在第 3 场因 512 MiB 峰值内存保护自行退出（696 秒）；未提高容量上限。完整文件、数据库一致性快照、资源样本均保留。修复报表一次载入全部诊断记录造成的峰值：流式读取经济事实与机会计数，缓存已结束市场报告，释放旧检测器图。同时修正自然到达时 maker 穿越拒绝分类，以及穿越成交/深度交叉归因。新增对应回归；最终公共验收将使用 forward-03，早期 2 场不冒充 12 场完成。

- 最终撮合/生命周期/报表修复已经冻结于 sources/forward-03-source.tar.gz。报表改为有界后台作业，逐笔 markout 按 1/5/30 秒触发（最多等待 250ms 的有效未来盘口），旧观察器在市场结束后释放。人工反例和全路径回放/核账均已通过：frozen-final.xml；开始 forward-03，维持原 512 MiB / 4096 MiB / 2048 帧参数。

- forward-03 在前段验证时主动停止（207.5 秒，0 完整场），原因是在线字典与落盘 JSON 键排序造成独立账户日志顺序差异。经济状态相同，但未将其掩盖为通过。serialization-before-fix.txt 保存真实失败；INIT 统一规范化后 canonical-final.xml：39 passed。新冻结源码 sources/forward-04-source.tar.gz，正式补跑 forward-04。

- forward-04 已保留并主动停止，修正 M1 适配器必须以官方 elapsed_lower/upper 注入 M1Market（不能用主机墙钟替代），同时补齐 M234 候选仍有效时跨 T240 保留挂单、至自身 deadline 再撤单。新增真实准入/生命周期反例：clock-and-lifetime-final.xml，42 passed。最终冻结 sources/forward-05-source.tar.gz，公共验收使用 forward-05，早期补跑不折算为最终 12 场。

- forward-05 的补充源时间核对发现晚到成交可能早于模拟队列生效；先前局部验收证据保持历史事实，对应工作项重新 IN_PROGRESS。增加源时间因果门槛与倒退隔离后才重新验收，不把未校准当作工程缺陷的豁免。

- 源时间因果修正完成：队列段记录生效源时间，成交不能作用于当时尚不存在的订单；晚到深度不授予预生效撤单信用；源时间倒退隔离；已撤订单收到撤前成交事实时重新预留历史未决风险并独立核账；撤单先于 POST 到达返回 NOT_FOUND，不虚构未来取消。causal-late-facts-final.xml：50 passed。源码冻结 sources/forward-06-source.tar.gz。正式运行 forward-06，仍按 12 场 / 4500 秒、原容量与原时效，不累计前期诊断场数。

- M4 命名修复：每次队列重建使用新且确定的命名空间计数；原检测器对照不再注入同一固定命名空间，采用保留多重性的一一映射，防止掩盖 ID 重用。m4-namespace-before-fix.txt 保留真实失败；stable-namespace-final.xml：51 passed。冻结 sources/forward-07-source.tar.gz，前向验收 run ID forward-07。

- forward-07 完成 8 个完整窗口后主动停止。一次网络重连用于复核丢失消息区间：新增 GAP 最早未处理接收水位，近期已撤订单若与可接纳源时间缺口重叠则恢复历史风险预留；不将未收到的撤前成交当零损失。另补齐通用账户初始资金配置/ACCOUNT_OPEN 核账和同价不同文本格式共享 FIFO。final-kernel.xml：56 passed；冻结 sources/forward-08-source.tar.gz，最终前向运行 forward-08。旧 8 场不冒充最终 12 场。

- forward-08 10 万公共输入前缀比对：原始日志 1,154 行位置差异，273 个输入块；均为原 SessionFeed 的 touched set 导致同帧 token 诊断排列不同。严格差异完整保留，replay/public-prefix-08/comparison.json 为 DIFF；限定连续诊断组、同一输入、全部字段和序号区间不变后，候选/动作/经济事件有序差异为 0。diagnostic-classification.json 和 tests/diagnostic-classifier-validation.json 保存分类规则及拒绝金额/候选差异的反例。该前缀不替代最终完整公共回放。

- 最终实施验收：forward-08 正常退出，12/12 完整窗口，1436209 原始帧，3757.1 秒；全输入扫描/12场核账通过。完整公共记录166881事件回放，状态哈希相同，候选/动作/经济有序差异0；2080行同帧原始诊断顺序差异保留并明确按W09元数据例外分类。最终回归562 passed（含56项模拟验收），静态检查通过。12个官方结果均已处理，现有服务身份与W00一致。自然模型成交0，真实校准UNCALIBRATED；不据此宣称收益或成交精度有效。

## 当前公共试运行状态

{
  "beijing": "2026-11-06T03:01:25.166352+08:00",
  "run": "forward-08",
  "complete_windows": 12,
  "target": 12,
  "official_results": 12,
  "status": "PUBLIC_ENGINEERING_ACCEPTED",
  "public_full_replay": "PASS_WITH_DIAGNOSTIC_ORDER_DIFFERENCES",
  "calibration": "UNCALIBRATED"
}


2026-11-06 运行证据补齐：forward-final-01 已留下12个完整窗口和 PLANNED_COMPLETE 输出，但外层工具会话退出143，内层CLI实际OS退出码未采集；没有补写0。该运行保留于新包 live/forward-final-01，退出证据缺口见 live/forward-final-01-exit-gap.json。最终验收改用同一冻结源码/预算的独立监督运行 forward-final-02，重新单次完成12场，不混拼两轮。

## 2026-11-06 当前返工 RW00–RW08

旧版历史保留；当前 R1/R2/R3/R4 分别由 RW01/RW02/RW03/RW04 修复。

新包：`/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T014400Z-rework-v2`。独立复验 PENDING_ASTRA_REVIEW；真实校准 UNCALIBRATED。

| 工作项 | 状态 | 实际命令/证据 | 未完成 |
| --- | --- | --- | --- |
| RW00 | DONE | tests/before/counterexamples.command.json → tests/before/reproduction-summary.json；tests/before/tick-control.command.json → tests/before/tick-reverification-probe.json | 无 |
| RW01 | DONE | tests/after/r1-02.command.json → tests/after/r1-02.xml；tests/after/r1-independent.command.json → tests/after/r1-independent.xml；tests/after/r1-hand-inspection.command.json → fixtures/r1-hand-traces.json | 无 |
| RW02 | DONE | tests/after/r2-03.command.json → tests/after/r2-03.xml；tests/after/r2-trace-inspection.command.json → fixtures/r2-coordinator-trace.json | 无 |
| RW03 | DONE | tests/after/r3-02.command.json → tests/after/r3-02.xml；tests/after/r3-independent.command.json → tests/after/r3-independent.xml；tests/after/r3-trace-inspection.command.json → fixtures/r3-hand-trace.json | 无 |
| RW04 | DONE | tests/after/r4-01.command.json → tests/after/r4-01.xml；tests/after/r4-cli-normal.command.json → calibration/r4-normal.json；tests/after/r4-cli-reversed.command.json → calibration/r4-reversed.json；tests/after/r4-hand-inspection.command.json → calibration/local-coverage-v2.json | 无 |
| RW05 | DONE | tests/after/regression-04.command.json → tests/after/regression-04.xml；tests/after/integration-03.command.json → tests/after/integration-03.xml；tests/after/ruff-04.command.json → tests/after/coverage-matrix.json；tests/after/compile-02.command.json → tests/after/compile-02.log；tests/after/diff-check-02.command.json → tests/after/diff-check-02.log | 无 |
| RW06 | DONE | tests/after/freeze-02.command.json → source-identity.json；tests/after/build-tapes-06.command.json → fixtures/full-06/built.json；tests/after/final-replays-01.command.json → reconciliation/final-artificial.json；tests/after/recovery-03.command.json → reconciliation/recovery-03.json | 无 |
| RW07 | DONE | live/forward-final-02.command.json → live/forward-final-02/completion.json；tests/after/public-acceptance.command.json → reconciliation/public-acceptance.json；tests/after/public-extra.command.json → reconciliation/public-extra.json；tests/after/official-recheck-final02.command.json → live/official-result-recheck-final02.json；tests/after/last-results.command.json → reconciliation/public-final-latest.json；tests/after/post-public-02.command.json → reconciliation/public-final.json | 无 |
| RW08 | DONE | tests/after/final-handoff.command.json → HANDOFF.md；seal-receipts/preseal-verify.command.json → seal-receipts/preseal-result.json | 无 |

## 2026-11-06 第二次独立复验与续返工 RW09–RW16

上表保留为 v2 实施方交接历史。最新独立结论为 **NEEDS_REWORK**：原 R1、R2、R4 在本次验证范围内通过；原 R3 的终态历史边界存在 RR1（P1），全部成交及部分成交撤单完成后会漏检迟到 tick 冲突。完整原回归单独复跑 668 passed；新增独立边界 2 failed / 3 passed。12 场公共工程证据、完整回放和核账通过，不能代替 RR1 的修复。

依据：[独立复验报告](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_REWORK_INDEPENDENT_REVIEW_20261106.md)。续返工合同：[RW09–RW16 清单](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_SECOND_REWORK_ITEMS_20261106.md)；[Sol 提示词](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_SOL_SECOND_REWORK_PROMPT_20261106.md)。此处仅登记待执行工作，本次编制清单没有实施修复或启动公共运行。

| 工作项 | 状态 | 完成目标 |
| --- | --- | --- |
| RW09 | TODO | 冻结起点、保护历史、复现 RR1 |
| RW10 | TODO | 修复终态订单的成交历史约束校验 |
| RW11 | TODO | 失败贯通运行、清理、持久化、报告与验收 |
| RW12 | TODO | 五项独立用例及两情景、故障传播、正对照覆盖 |
| RW13 | TODO | 原完整回归、新增测试、静态检查与独立核账 |
| RW14 | TODO | 新版本冻结、正常及冲突样本确定性回放 |
| RW15 | TODO | 最终版本有限 12 场公共模拟及完整回放 |
| RW16 | TODO | 新交接包、完整证据与可执行复现命令 |

真实成交校准继续为 UNCALIBRATED。新包待实施时创建；只有实际完成后才可声明“实施完成，等待 Astra 独立复验”。

## 2026-11-06 第二轮返工 RW09–RW16

新包 `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T061419Z-second-rework-v3`。独立复验 PENDING_ASTRA_REVIEW。真实校准 UNCALIBRATED。

| 工作项 | 状态 | 命令与证据 | 剩余工作 |
| --- | --- | --- | --- |
| RW09 | DONE | tests/before/baseline.command.json → baseline.json，tests/before/terminal.command.json → tests/before/reproduction.json，tests/before/rr1-trace-02.command.json → tests/before/rr1-traces.json | 无 |
| RW10 | DONE | tests/after/rw10-01.command.json → tests/after/rw10-01.xml，tests/after/rw10-01.command.json → contracts/terminal-arrival-v3.json | 无 |
| RW11 | DONE | tests/after/paths-01.command.json → tests/after/paths-01.xml，tests/after/paths-01.command.json → contracts/failure-propagation.json | 无 |
| RW12 | DONE | tests/after/paths-01.command.json → tests/after/coverage-matrix.json，tests/static/check-01.command.json → tests/static/result.json | 无 |
| RW13 | DONE | tests/after/regression-01.command.json → tests/after/regression-result.json，tests/static/check-01.command.json → tests/static/result.json，tests/after/native-02.command.json → fixtures/native-02/built.json | 无 |
| RW14 | DONE | tests/after/freeze-v3.command.json → source-identity.json，tests/after/compare-normal.command.json → replay/artificial-final/result.json，tests/after/compare-filled-conflict.command.json → replay/artificial-final/filled-conflict/comparison.json，tests/after/compare-cancelled-partial-conflict.command.json → replay/artificial-final/cancelled-partial-conflict/comparison.json，tests/after/regression-tapes-v3.command.json → fixtures/regression-v3/built.json，tests/after/recovery-final.command.json → reconciliation/recovery-final.json | 无 |
| RW15 | DONE | live/forward-v3-01.command.json → live/forward-v3-01/completion.json，tests/after/public-acceptance.command.json → reconciliation/public-acceptance.json，tests/after/public-extra.command.json → reconciliation/public-extra.json，tests/after/official-recheck.command.json → live/official-result-recheck.json，tests/after/public-full-compare-strict.command.json → replay/public-full-final/comparison-strict.json，tests/after/post-public.command.json → reconciliation/public-final.json，tests/after/preservation.command.json → reconciliation/preservation.json | 无 |
| RW16 | DONE | tests/after/offline-reproduction.command.json → reproduction/checked-01/result.json，tests/after/preservation.command.json → reconciliation/preservation.json，tests/after/preseal.command.json → reconciliation/preseal.json，seal-receipts/initial-verify-command.command.json → seal-receipts/initial-verify.json | 无 |

实施完成，等待 Astra 独立复验。独立复验 PENDING_ASTRA_REVIEW，真实成交校准 UNCALIBRATED。新包最终封存核验见 seal-receipts/final-verify.json。

## 2026-11-06 v3 独立复验结论

最新独立状态为 **NEEDS_REWORK**，上方 RW09–RW16 的 DONE 保留为实施方交接历史。

- 原 RR1 已关闭；终态订单、正常写入时的失败传播及合法对照通过。
- 700 项完整回归首次独立复跑全部通过；12 场输入、120 个独立账户核账，以及 tick 复核场次和自然模拟成交场次的完整回放通过。
- 新发现 RR2（P2）：快照持续写入失败时，FAIL 失败文件已经存在，但自动报告和重新打开后的 audit/report 可读取旧 VALID 快照并返回 PASS。新增独立测试 3 failed / 1 passed。
- RW11 尚未关闭，RW12 需补持续写入故障及重新打开的边界；原始账与历史包保持不变。真实成交校准继续为 UNCALIBRATED。

报告：[v3 独立复验](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_V3_INDEPENDENT_REVIEW_20261106.md)。证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261106-rework-03`。本次复验没有实施产品修复或启动新的公共采集。

## 2026-11-06 第三轮返工 RW17–RW24 待执行

当前独立结论仍为 **NEEDS_REWORK**。本轮针对 RR2：快照持续写入失败后，自动报告和重开核账不能从旧 VALID 快照恢复 PASS 或有效样本资格。原 RR1 已关闭，700 项回归及 v3 公共工程证据保留为已验证基线。

执行合同：[RW17–RW24 工作项清单](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_THIRD_REWORK_ITEMS_20261106.md)；[Sol 执行提示词](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_SOL_THIRD_REWORK_PROMPT_20261106.md)。本次仅编制待执行工作，没有实施修复、运行测试或启动公共模拟。

| 工作项 | 状态 | 完成目标 |
| --- | --- | --- |
| RW17 | TODO | 冻结起点、保护六个历史根目录、复现 RR2 四项独立用例 |
| RW18 | TODO | 修复持续写失败时的失败输出及样本资格 |
| RW19 | TODO | 统一重开后的报告、核账及相关验收的有效性判断 |
| RW20 | TODO | 补齐持续故障、身份关联、关闭重开与正常对照 |
| RW21 | TODO | 原 700 项选择及新增完整回归、独立经济核账 |
| RW22 | TODO | 冻结新版本、正负样本重复验证及离线复现 |
| RW23 | TODO | 最终版本有限 12 场公共验收及完整回放 |
| RW24 | TODO | 封存新交接包、完整证据和可执行复现命令 |

新证据包待实施时创建，历史包与独立材料只读保留。每项 DONE 必须附源码身份、实际命令、退出码及证据路径；实际全部完成后，才可声明“实施完成，等待 Astra 独立复验”。真实成交校准继续为 UNCALIBRATED。

## 2026-11-06 第三轮返工 RW17–RW24

新包 `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261106T150054Z-third-rework-v4`。独立复验 PENDING_ASTRA_REVIEW，真实校准 UNCALIBRATED。

| 工作项 | 状态 | 命令与证据 | 剩余工作 |
| --- | --- | --- | --- |
| RW17 | DONE | tests/before/baseline.command.json → baseline.json，tests/before/rr2.command.json → tests/before/reproduction.json，tests/before/trace.command.json → tests/before/rr2-trace.json | 无 |
| RW18 | DONE | tests/after/final-boundaries.command.json → tests/after/final-boundaries.xml，tests/after/final-boundaries.command.json → contracts/failure-propagation.json，tests/after/consistent-boundaries.command.json → tests/after/consistent-boundaries.xml | 无 |
| RW19 | DONE | tests/after/final-boundaries.command.json → contracts/failure-binding-v4.json，tests/after/legacy-v3-audit.command.json → compatibility/v3-normal/provenance.json，tests/after/legacy-v3-report.command.json → compatibility/v3-normal/report/latest.json，tests/after/consistent-boundaries.command.json → tests/after/consistent-boundaries.xml | 无 |
| RW20 | DONE | tests/after/final-boundaries.command.json → tests/after/coverage-matrix.json，tests/static/check-01.command.json → tests/static/result.json，tests/after/consistent-boundaries.command.json → tests/after/consistent-boundaries.xml | 无 |
| RW21 | DONE | tests/after/regression-03.command.json → tests/after/regression-03-result.json，tests/static/check-03.command.json → tests/static/result-03.json，tests/fixture-migration/probe.command.json → tests/fixture-migration/equivalence.json | 无 |
| RW22 | DONE | tests/after/freeze-v4-r2.command.json → source-identity.json，tests/after/consistent-final-determinism.command.json → replay/final-r2/result.json，tests/after/consistent-regression-fixtures.command.json → fixtures/regression-v4-r2/backlog-partial/facts.json，tests/after/consistent-recovery.command.json → reconciliation/recovery-final-v4-r2.json，tests/after/frozen-archive-reproduction-02.command.json → reproduction/frozen-offline-02/result.json | 无 |
| RW23 | DONE | live/forward-v4-02.command.json → live/forward-v4-02/completion.json，tests/after/public-acceptance.command.json → reconciliation/public-acceptance.json，tests/after/public-extra.command.json → reconciliation/public-extra.json，tests/after/official-recheck.command.json → reconciliation/final-selection.json，tests/after/post-public.command.json → reconciliation/public-final.json，tests/after/preservation-final-02.command.json → reconciliation/preservation-final.json | 无 |
| RW24 | DONE | tests/after/preseal.command.json → reconciliation/preseal.json，tests/after/frozen-public-reproduction.command.json → reproduction/frozen-public-01/result.json，tests/after/preservation-final-02.command.json → reconciliation/preservation-final.json，seal-receipts/initial-check.command.json → seal-receipts/initial-verify.json | 无 |

第三轮状态为“实施完成，等待 Astra 独立复验”。独立复验 PENDING_ASTRA_REVIEW，真实成交校准 UNCALIBRATED。最终交接见新包 HANDOFF.md。

## 2026-11-07 v4 独立复验结论

最新独立状态为 **NEEDS_REWORK**。上方 RW17–RW24 的 DONE 保留为实施方交接历史，本次未修改产品实现或封存材料。

- 735 项完整回归首次独立执行全部通过；原四项 RR2 反例全部通过，离线失败输出与重开报告/核账的修复有效。
- 剩余 RR2-LIVE（P2）：实际在线协调器在持续快照写入失败后，当前市场未到达 END；汇总跳过该 INVALID 市场。已有健康市场时汇总显示 PASS，无前置市场时显示 PENDING。收尾再次写入失败，未生成 completion.json，验收工具随后显示 RUNNING、退出 0。新增四项实际协调器用例均失败；单库重开 audit/report 均正确退出 1。
- 12 场完整输入、120 个独立账户核账、正常及四类故障样本、两场公共完整回放通过；原始有序审计逐行一致。源码/归档、封存文件集合、六个历史根目录及固定测试夹具迁移核对通过。
- RW19、RW20 尚未闭合，RW18 的离线修复保留通过，在线整体传播需联动收尾修复。RW21–RW23 的本版本工程证据保留；RW24 封存完整性通过不等于最终独立验收通过。
- 封存时 11 场已结算、1 场待定；自然模拟成交仍有不确定性，真实成交校准继续为 UNCALIBRATED。本次没有新增公共采集、真实交易或服务变更。

报告：[v4 独立复验](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_V4_INDEPENDENT_REVIEW_20261107.md)。[机器可读结论](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04/review-result.json)。独立证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-04`。

## 2026-11-07 第四轮返工 RW25–RW32 待执行

当前独立状态仍为 **NEEDS_REWORK**。本轮针对 RR2-LIVE：未结束的失效市场必须进入失败汇总，清理异常不能阻断其余可执行的关闭与失败终态，停止后的失败不能被正式验收当作 RUNNING/退出 0。

执行合同：[RW25–RW32 工作项清单](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_FOURTH_REWORK_ITEMS_20261107.md)；[Sol 执行提示词](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_SOL_FOURTH_REWORK_PROMPT_20261107.md)。本次仅编制待执行工作，没有实施修复、运行测试或启动公共模拟。

| 工作项 | 状态 | 完成目标 |
| --- | --- | --- |
| RW25 | TODO | 冻结起点、保护八个历史根目录、复现四项在线反例 |
| RW26 | TODO | 汇总纳入未结束的失效市场，覆盖缓存和在途报告 |
| RW27 | TODO | 隔离清理异常，继续可完成的关闭并保存运行失败 |
| RW28 | TODO | 统一停止状态、持久失败证据与正式验收退出语义 |
| RW29 | TODO | 实际协调器故障矩阵、原 735 项及新增回归、独立核账 |
| RW30 | TODO | 冻结新版本并重复验证协调器和离线正负路径 |
| RW31 | TODO | 最终版本有限 12 场公共验收、完整回放和核账 |
| RW32 | TODO | 封存新交接包及可执行复现命令 |

原离线修复、735 项回归、v4 公共运行与历史证据保留。新包待实施时创建，每项 DONE 必须附身份、命令、退出码和证据。实际全部完成后，才可声明“实施完成，等待 Astra 独立复验”。真实成交校准继续为 UNCALIBRATED。

## 2026-11-07 第四轮返工 RW25–RW32

新包 `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261107T090419Z-fourth-rework-v5`。独立复验 PENDING_ASTRA_REVIEW，真实校准 UNCALIBRATED。

| 工作项 | 状态 | 实际命令与证据 | 剩余条件 |
| --- | --- | --- | --- |
| RW25 | DONE | tests/before/baseline.command.json → immutable-history-before.json，tests/before/coordinator.command.json → tests/before/coordinator.xml，tests/before/offline-controls.command.json → tests/before/offline.xml，tests/before/reproduce.command.json → tests/before/reproduction.json | 无 |
| RW26 | DONE | tests/after/full-regression-05.command.json → tests/after/regression-05-result.json，tests/after/matrix-summary-02.command.json → reconciliation/live-matrix-final.json | 无 |
| RW27 | DONE | tests/after/full-regression-05.command.json → tests/after/regression-05.xml，tests/after/matrix-summary-02.command.json → reconciliation/live-matrix-final.json，tests/static/check-05.command.json → contracts/run-failure-v5.json | 无 |
| RW28 | DONE | tests/after/full-regression-05.command.json → tests/after/regression-05-result.json，tests/after/matrix-summary-02.command.json → reconciliation/live-matrix-final.json，tests/after/full-regression-05.command.json → tests/after/regression-05.xml | 无 |
| RW29 | DONE | tests/after/full-regression-05.command.json → tests/after/regression-05-result.json，tests/static/check-05.command.json → tests/static/result.json，tests/after/matrix-summary-02.command.json → reconciliation/live-matrix-final.json | 无 |
| RW30 | DONE | tests/after/freeze-v5.command.json → source-identity.json，tests/after/frozen-offline-reproduction.command.json → reproduction/frozen-offline-01/result.json，tests/after/final-native.command.json → fixtures/native-final-v5，tests/after/final-regression-fixtures.command.json → fixtures/regression-v5/backlog-partial/facts.json，tests/after/final-recovery.command.json → reconciliation/recovery-final-v5.json | 无 |
| RW31 | DONE | live/forward-v5-01.command.json → live/forward-v5-01/completion.json，tests/after/public-acceptance.command.json → reconciliation/public-acceptance.json，tests/after/public-extra.command.json → reconciliation/public-extra.json，tests/after/official-recheck.command.json → live/official-result-recheck.json，tests/after/post-public.command.json → reconciliation/public-final.json，tests/after/frozen-public-reproduction.command.json → reproduction/frozen-public-01/result.json，tests/after/preservation-final.command.json → reconciliation/preservation-final.json | 无 |
| RW32 | DONE | tests/after/handoff-draft.command.json → HANDOFF.md，tests/after/preseal.command.json → reconciliation/preseal.json，seal-receipts/initial-check.command.json → seal-receipts/initial-verify.json，tests/after/preservation-final.command.json → reconciliation/preservation-final.json，tests/after/frozen-public-reproduction.command.json → reproduction/frozen-public-01/result.json | 无 |

实施完成，等待 Astra 独立复验。RW25–RW32 全部 DONE；独立复验 PENDING_ASTRA_REVIEW；真实成交校准 UNCALIBRATED。
最终身份、命令与证据见新包 source-identity.json、work-items.json 与 HANDOFF.md。

## 2026-11-07 v5 独立复验结论

最新独立状态为 **NEEDS_REWORK**，新增 **RR3（P2）**。上方 RW25–RW32 的 DONE 保留为实施交接历史；本次未修改产品实现或封存材料。

- 原 RR2-LIVE 四项实际协调器反例已通过。761 项完整回归首次独立执行全部通过，18 条实际 CLI 故障/正常控制符合预期；原测试、参数及四项独立探针均保留。
- RR3：两份运行终态文件写入失败、只有根报告保存绑定失败时，支持的报告重生成命令会覆盖唯一失败证据，输出 PASS/退出 0。正式验收随后由 FAIL/1 退化为 INCOMPLETE/2，仍未误返回成功。工作区与冷提取的冻结归档均复现，数据库内容不变。RW28 尚未关闭，RW29 需补这一组合回归。
- 冻结归档 152 个真实子命令的退出码符合预期；12 场全输入检查、120 个独立账户核账、人工正常/故障样本及三场自然模拟成交的完整回放通过，状态与有序审计逐行零差异。
- 本包 24,431 个文件、八个历史根的 32,064 个文件及全部清单链接最终全量核对通过；395 个归档项目文件及既有服务身份前后不变。
- 封存最终选择为 11 场已结算、1 场待定。真实成交校准继续为 UNCALIBRATED；本次复验只使用封存输入，没有新增公共采集或真实交易。

报告：[v5 独立复验](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_V5_INDEPENDENT_REVIEW_20261107.md)。[机器可读结论](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/review-result.json)。独立证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05`。

## 2026-11-07 RR3 最小返工 RW33–RW36 待执行

按用户最新要求“只做必要的测试，不要越修问题越多”，本轮限定为报告重生成保留已有失败的小修。当前独立结论仍为 NEEDS_REWORK；本次仅编制工作项与提示词，没有实施产品修复或运行产品测试。

执行合同：[RW33–RW36 最小返工清单](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_FIFTH_REWORK_ITEMS_20261107.md)；[Sol 执行提示词](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_SOL_FIFTH_REWORK_PROMPT_20261107.md)。本合同按最新用户指令替代旧轮次的重复大规模验收要求。

| 工作项 | 状态 | 完成目标 |
| --- | --- | --- |
| RW33 | TODO | 引用既有反例与基线，确认小补丁边界和独立输出目录 |
| RW34 | TODO | 验证并继承仅存于根报告中的同一运行失败，重生成保持 FAIL |
| RW35 | TODO | 主例、必要对照和局部检查通过；同例复用验证重复生成及导出重开 |
| RW36 | TODO | 提交基线加补丁的轻量增量包，等待 Astra 定向复验 |

既有 761 项回归、公共 12 场、核账和完整回放保留为 v5 历史证据。本轮默认不重跑这些检查，不扫描全部历史目录，不新增公共采集或真实交易。补丁差异、主例、必要对照和局部检查通过即交接；下一次独立复验采用相同有限范围。真实校准保持 UNCALIBRATED，历史数据接入仍后置。

## 2026-11-07 RR3 最小修补实施记录 RW33–RW36


增量包 `/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261107T131117Z-rr3-patch`。独立复验 PENDING_ASTRA_REVIEW；真实校准 UNCALIBRATED。

| 工作项 | 状态 | 实际命令与证据 | 剩余条件 |
| --- | --- | --- | --- |
| RW33 | DONE | evidence/baseline.command.json → references/baseline-check.json | 无 |
| RW34 | DONE | evidence/targeted-01.command.json → evidence/rr3-summary.json | 无 |
| RW35 | DONE | evidence/targeted-01.command.json → evidence/test-summary.json；evidence/static-01.command.json → evidence/static-summary.json | 无 |
| RW36 | DONE | evidence/handoff-draft.command.json → HANDOFF.md；evidence/static-01.command.json → source-identity.json；seal-receipts/initial-check.command.json → seal-receipts/initial-verify.json | 无 |

RR3 最小修补实施完成，等待 Astra 定向复验。RW33–RW36 全部 DONE。上方待执行表保留为清单编制历史，当前状态以本节实施记录为准。
独立状态 PENDING_ASTRA_REVIEW；真实校准 UNCALIBRATED。仅5项定向测试和局部检查，未重跑大规模验收。

## 2026-11-07 RR3 定向独立复验通过

最新独立结论：**PASS**。RR3 关闭，RW33–RW36 通过；对应 RW28/RW29 遗留要求关闭。此前 NEEDS_REWORK 与实施方待复验记录保留为历史。

- 从 v5 归档加补丁重建并核对源码身份，独立执行约定的 5 项定向测试一次：5 passed，0 failed/error/skipped，退出 0，16.02 秒。
- 同一实际故障运行在两次报告重生成、完整目录导出重开后，均保持 FAIL/1；原因与身份保留，数据库哈希及市场经济内容不变。健康、证据不足、外来身份及 cached/inflight/empty 对照均通过。
- 唯一产品改动为报告逻辑；三份改动文件局部检查、320 文件增量包及 11 个基线引用核对通过，既有服务身份前后一致。
- 按最新有限范围完成复验并停止扩测。v5 全量工程证据保留原身份；本轮没有重新执行完整回归、公共采集或大规模回放。

**主框架本阶段工程验收通过，本次报告边界返工结束。** 真实成交校准仍为 UNCALIBRATED，历史数据统一接入与成交校准留待后续阶段。

报告：[RR3 定向复验](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_RR3_DIRECTED_REVIEW_20261107.md)。[机器可读结论](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed/review-result.json)。独立证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rr3-directed`。

## 2026-11-07 第一阶段整体验收通过

对照 W00–W13 原始清单及后续范围约定，第一阶段可交付为可运行、可回放、可核账的 M1–M4 交易模拟研究框架。历轮已知阻塞项全部关闭，不新增返工。

本次重新核对当前 165 个实现文件、396 个项目文件、11 个基线引用及基线归档/增量补丁身份，全部一致。采用已独立验证的 v5 完整工程证据与 RR3 定向补丁证据，没有重跑产品测试、公共采集或历史全量扫描。

代码工程验收与有限公共模拟验收完成；真实成交校准仍为 UNCALIBRATED。历史 M1/M234 测试数据统一接入、持续模拟校准及实盘验证属于后续阶段。

报告：[第一阶段整体验收](/home/ubuntu/polymaker/docs/development/M1234_TRADING_SIMULATION_PHASE1_ACCEPTANCE_20261107.md)。证据：[本次交付身份核对](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-phase1-acceptance/identity-check.json)。
