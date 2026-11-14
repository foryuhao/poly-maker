# M1234 v5 独立复验：RW25–RW32

日期：2026-11-07，北京时间。独立结论：**NEEDS_REWORK**。新增 **RR3，P2**；原 RR2-LIVE 四项协调器反例已通过。

本轮复验使用封存输入、冻结源码和离线故障适配器。产品实现未修改，既有服务身份保持一致。真实成交校准仍为 **UNCALIBRATED**。

- 交接包：[fourth-rework-v5](/home/ubuntu/polymaker/research_outputs/m1234_sim/rework/20261107T090419Z-fourth-rework-v5/HANDOFF.md)。
- 本轮独立证据目录：`/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05`。
- [机器可读结论](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/review-result.json)。
- 实现 SHA256：`c39b3e3454388985f9950f37bf6dafa72b1650a05702e126b4d4adc6cc303fee`。
- 冻结归档 SHA256：`3a6b4807f5cb8d1dab701662250c16043e6b441f6ad85dbd13532b4101a10906`。
- 模型 `m1234-queue-v5`；输入 schema 2；快照 schema 4；运行终态及市场失败证据 schema 1。

## 1. 未通过项：RR3 / P2

**只有根报告保留运行失败时，重新生成报告会擦掉这份失败证据，输出 PASS。**

问题入口：[report.py:355](/home/ubuntu/polymaker/src/polymaker/m1234_sim/report.py:355)。`publish()` 只有在 `completion.json` 或 `*.run-failure.json` 存在时才调用运行状态检查。它没有覆盖已由本轮合同明确支持的第三种情况：两份终态文件均不可写，只有 `latest.json.run_failure` 成功保存。

已有 [runstate.py](/home/ubuntu/polymaker/src/polymaker/m1234_sim/runstate.py:111) 能验证这份报告的运行 UUID、配置及源码身份，并将其识别为已知失败。但随后执行支持的 `report --database … --output <原运行目录>` 时，`publish()` 跳过了检查，直接根据健康市场账本发布新报告，覆盖原先的运行失败原因。

### 实际复现

使用正式离线驱动调用真实 `run` CLI；正常人工市场完成后，仅对两份运行终态文件持续注入写入失败。信号、撮合、经济账本及结束事件均由产品代码产生。没有手写 FAIL 记录、修改有效性或补造 END。

| 操作 | 实际状态 | 实际退出码 |
| --- | --- | --- |
| 实际运行，两份终态文件写入失败 | 根报告 FAIL，保存绑定的清理失败；运行报终态无法保存 | 1 |
| 首次正式验收 | FAIL，能从根报告识别已知运行失败 | 1 |
| 在原运行目录重新生成报告 | **PASS，`run_failure` 字段消失** | **0** |
| 再次正式验收 | **INCOMPLETE，原失败原因已丢失** | **2** |

工作区与重新提取的冻结归档均复现同一行为。冻结复现中，报告重生成前后数据库 SHA256 完全相同，说明缺陷发生于失败证据与报告传播，未改变已记录的经济事实。

正式验收没有返回成功：它仍然以 2 拒绝验收。因此本项评为 P2；具体影响是报告错误显示 PASS，以及已确认失败退化成证据不足，违反 RW28 对报告重生成和失败证据保留的要求。

证据：

- [行为摘要及两种源码复现关联](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/finding-rr3.json)。
- [工作区实际命令、退出码及前后状态](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/report-only-failure/result.json)。
- [冻结归档实际命令及数据库不变证明](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/reproduction/frozen-project/research_outputs/m1234_sim/independent-report-only-failure/result.json)。
- [独立复现程序](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/probe_report_only_failure.py)。程序保留全部子命令输出；外层退出 1 表示“失败必须保留”的验收要求未满足。

可在新的独立目录重现，无需网络、钱包或真实订单：

```bash
cd /home/ubuntu/polymaker
M1234_RR3_OUT="$(mktemp -d /home/ubuntu/polymaker/research_outputs/m1234_sim/independent/rr3.XXXXXXXX)"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B \
  research_outputs/m1234_sim/independent/astra-20261107-rework-05/probe_report_only_failure.py \
  --output "$M1234_RR3_OUT/case"
```

### 关闭条件

报告重生成应先验证并继承同一运行已有的失败证据，包括只有根报告这一条通道存活的情况；新报告不能消除已知失败或转成 PASS。需增加这一组合下的重生成、导出和重开回归，同时保留健康运行、证据不足及外来运行身份的正确处理。不能靠把所有缺少 completion 的运行都判 FAIL 满足负例。

## 2. 原问题与完整回归

- 原四项实际协调器反例全部通过：完整成交/部分成交撤单 × 无/有健康前置市场。当前市场保持 INVALID、`ended=false`；根报告 FAIL，资格关闭；健康前置市场的事实保留。
- 持续快照故障在产品退出时仍保持注入；可关闭的 feed、API、策略、数据库、录制器和日志已由产品关闭，测试兜底没有代替产品收尾。单库 audit/report 和正式验收按要求拒绝。
- 本轮独立完整回归首次执行为 **761 passed，0 failed/error/skipped，退出 0**，约 335.93 秒。原 735 项选择、原四项独立协调器用例及新增 22 项边界全部保留。
- 18 条真实 CLI 故障/正常控制均符合各自预期，包括报告、备份、录制器、终态文件、全部失败输出、取消及手动停止等。RR3 是现有矩阵遗漏的“仅报告通道存活后再次生成报告”组合。
- 210 个原测试文件和 10 个配置文件逐个字节相同；原四项独立探针 AST 相同；36 个适用文件的语法及 Ruff 检查、差异检查通过。

证据：[回归结果](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/regression-summary.json)、[实际 CLI 矩阵](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/matrix-independent.json)、[原测试与参数核对](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/baseline-assertions-parameters.json)。

## 3. 冻结复现、核账与公共工程证据

冷提取冻结归档后的复现共执行 **152 个真实子命令**，全部实际退出码符合预期。正常、两类 RR1、两类离线 RR2、正常 12 场人工协调器及四组在线故障分别重复两次；预期故障实际非零，外层仅在检查这些失败语义后返回 0。

独立 SQL/Decimal 重算了最终选定的 **12 场、120 个独立账户、74 个情景订单、24 条模拟成交事件**，现金、预留、支出、库存及结算一致。不同策略/profile/排队情景不合并成真实组合收益。人工正常样本及四类故障样本的经济前缀也通过独立重算，算术通过不授予失效样本资格。

公共运行覆盖北京时间 **2026-11-07 18:45–19:45** 的 12 个连续窗口，实际进程退出 0，记录时长 3629.56 秒。独立读取全部 **1,171,826 条 FRAME**，核对连续序号、可得时钟、源码身份、全部提交的 `[T15,T240)` 边界、T15 前预热、T300 观察和录制水位。正式 `--full-inputs` 验收通过。12 条原始 GAP 完整保留，没有从输入或报告中豁免。

资源实测峰值 RSS 153.38 MiB、输出 398,923,703 字节、最小剩余磁盘 12,805,623,808 字节、写队列高水位 114，均满足冻结上限。输入队列、12 场/4500 秒范围及配置未放宽。

三场自然模拟成交全部完整回放，首选场同时覆盖自然 tick 复核。使用独立比较程序，所有状态字段及有序审计逐行相等，无排序重排或事件豁免：

| 对照 | 比较的有序审计行数 | 差异 |
| --- | ---: | ---: |
| 人工正常 | 611 | 0 |
| RR1 完整成交 / 部分撤单 | 83 / 100 | 0 |
| RR2 完整成交 / 部分撤单 | 71 / 92 | 0 |
| 公共 1794049200 | 83,573 | 0 |
| 公共 1794048600 | 26,749 | 0 |
| 公共 1794051600 | 35,154 | 0 |

原运行停止时有两场官方结果待定；封存包对 1794051300 在新目录进行了公开结果续跑。每市场只选一份最终账本后，110 个账户对应的 11 场已结算，1794051600 仍待定。上述都是封存时事实；本次复验没有追加公开查询或采集。

证据：[冻结复现](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/reproduction/result.json)、[公共输入与资源独立检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/public-independent.json)、[独立核账](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/accounting-independent.json)、[完整比较](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/comparisons-independent.json)。

## 4. 身份、封存与方法说明

395 个项目归档文件、23 个包内冻结文件及整个归档的哈希核对通过。相较 v4，原文件改动为 8 个模拟/验收相关文件，新增运行状态模块及三份驱动/测试文件；撮合文件、原有信号参数和测试断言保留。

最终全量检查：本包 **24,431 个文件、1,782,565,285 字节、1,257 个链接**一致；八个历史根目录 **32,064 个文件、20,260,803,637 字节、2,903 个链接**一致。排除项按各自封存清单保留。复验前后既有服务状态、PID 和启动时间一致，395 个归档项目文件未变化。

独立清单校验首轮把历史清单中“同时列为链接与解析后文件内容”的 12 个配置链接漏计为缺失。该轮程序与结果已保留；随后逐项核验目标内容，修正校验器并再次全量检查通过。此校验器问题未计作产品缺陷。交接包早期回归尝试及 BA 时钟相关诊断材料也全部保留；本次首次完整回归未复现该失败。

证据：[最终封存检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/final-manifest-integrity.json)、[最终历史保存检查](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/final-history-integrity.json)、[结束时源码和服务身份](/home/ubuntu/polymaker/research_outputs/m1234_sim/independent/astra-20261107-rework-05/end-state-check.json)。

## 5. 工作项结论

| 工作项 | 独立判断 |
| --- | --- |
| RW25 | 起点身份、历史保存和原反例保留通过 |
| RW26 | 原未结束失效市场、缓存和在途报告场景通过 |
| RW27 | 已测资源清理及当前失败输出通过；后续报告重生成另受 RR3 阻断 |
| RW28 | **未关闭**：仅报告通道中的已知失败可在重生成时丢失 |
| RW29 | 761 项通过；仍需补 RR3 对应的组合回归 |
| RW30 | 声明的冻结复现及重复比较通过；同一冻结版本也复现 RR3 |
| RW31 | 本版本有限公共工程证据通过 |
| RW32 | 封存与交接完整性通过；整体验收仍为 NEEDS_REWORK |

主框架最终独立验收需在 RR3 修复及相应复验通过后关闭。现有通过证据保留；真实成交校准为 UNCALIBRATED，历史 M1/M234 数据统一接入仍按已约定的后续阶段处理。
