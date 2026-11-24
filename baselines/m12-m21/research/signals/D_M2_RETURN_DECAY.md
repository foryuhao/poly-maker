# M2 家族诊断特征：回流变化（由M10降级）

版本 `m2-return-decay-diagnostics-v1`。M10不再作为独立策略继续优化；保留两轮回流的绝对卖量、相对深度负荷和恢复变化，作为M2母候选的**描述字段**。不新增信号、确认条件、退出条件、订单或研究编号。本文只定义文档接口，未修改M2或M10运行实现。

```json
{
  "document_type": "diagnostic_feature_sop",
  "feature_id": "M2_RETURN_DECAY",
  "version": "m2-return-decay-diagnostics-v1",
  "status": "SPEC_ONLY_DIAGNOSTIC",
  "research_family_id": "F_BUY_RETURN",
  "parent_strategy_id": "M2",
  "origin_strategy_id": "M10",
  "parent_binding": "EXACT_M2_VERSION_CANDIDATE_AP_AND_R1_R2_IDS",
  "basis_file": "../signal_discovery/spec_archive/M7_M10_M13_DISPOSITION_BASIS_20261116.json",
  "basis_sha256": "1e7d8dcbd850636d29aa707eb7437b1d8e77fb216d5cdc9e5d424ad34ed07107",
  "features": ["H2_over_H1", "u2_over_u1", "r2_over_r1"],
  "context_fields": ["Q01", "Q02", "round1_window", "round2_window", "parent_round_profile"],
  "computed_at": "PARENT_CONFIRMATION_FROM_ALREADY_AVAILABLE_FROZEN_ROUNDS",
  "independent_signal": false,
  "admission_filter": false,
  "invalidation_guard": false,
  "thresholds": null,
  "own_anchor_or_deadline": false,
  "execution": null,
  "creates_orders": false,
  "missing_feature_policy": "NULL_WITH_REASON_DO_NOT_CHANGE_PARENT_SIGNAL",
  "updates_from_future_events": false,
  "old_M10_runtime_equivalence_claimed": false,
  "implementation": null,
  "tests_run": false
}
```

## 1. 绑定母候选，不能重建一套M10

只取 [M2](M2.md) 指定版本确认时已经采用的同AP背景、同固定Bid锚、同generation的两轮R1/R2。记录母策略版本、SOP hash、candidate/root/AP/round IDs及真实可得键。不从全局轮池挑更符合递减的一对，也不从M10旧campaign补齐M2缺少的轮。

M2现有实现的新AP/RF替换政策仍由母策略自己定义。本诊断不把它改成M10的冻结root，不延长AP历史，不改变未合格/失败轮处理、恢复阈值、确认时刻、锚、deadline或执行。新母候选绑定它自己的证据，旧诊断不搬过去。

旧M10完整定义保留在 [历史快照](../signal_discovery/spec_archive/M10-aad8f498bee74c82.md)；旧候选、成交和测试身份不能改名并入M2。共同骨架相近，不等于实际样本包含关系或可合池。

## 2. 三项描述量

沿母候选的轮定义取得`Q0_i、H_i、L_i、r_i`，`u_i=H_i/Q0_i`，不按新的窗口重算母证据。

| 字段 | 计算 | 只能说明 |
|---|---|---|
| H2_over_H1 | H2/H1 | 第二轮该档绝对SELL量相对第一轮的变化 |
| u2_over_u1 | (H2/Q02)/(H1/Q01) | 第二轮卖量相对该轮初始Bid深度的负荷变化 |
| r2_over_r1 | r2/r1 | 母版本定义下恢复率的相对变化 |

各字段分母必须可证、有限且大于0；否则该字段为null并记录原因，不能补0、无穷或默认“递减”。H较少与u较低可能不同向，因为Q02/Q01变化；保留两个Q0和原窗口作为上下文。两轮量比不是跨空档的卖压速率，也不自动证明持续衰竭、买方优势或结算概率上升。

原M10主版的0.90/0.90/0.80、后续1.25倍压力退出，及外部版本的0.99/0.60等**均不作为本特征的阈值**。这次不创建“递减合格”布尔标签或可执行子策略。以后若要分组或筛选，应显式提出独立分析定义，不能悄悄恢复M10信号。

## 3. 可得、缺失与失效语意

三项特征在母候选确认边界，用当时已可得的冻结R1/R2计算一次；不读取未来成交、回补、价格或结算。无法从现有输出取到某字段时标UNAVAILABLE，不为得到完整特征新增等待、不延后M2确认，也不实现新采集。

诊断自身没有信号有效期。母信号结束时，特征仍作为该历史候选的描述保留，不再属于一个当前有效的母候选；不会因后来H/u变化触发母信号失效。若证据被更正或证伪，保留原计算与版本、追加质量标记，不用未来数据静默改写当时值；母策略是否失效仍按自己的质量合同。

诊断缺字段与母信号输入无效要区分：仅诊断接口缺字段不取消母信号；若发现母证据本身不可证，则不能借“诊断不影响母信号”绕过M2原质量规则。

## 4. 与执行及M11的边界

Maker/Taker、固定P、准入与失效均由母M2版本和执行合同决定。本特征没有自己的订单、仓位、root、预算或成交计数。同一个M2候选附带三项数值仍是一份候选，不能按多个标签重复计为独立信息或交易。

M11旧SOP中的M10母策略引用只属于历史M10定义；本特征不能作为M11母信号，也不自动把M11重挂到M2。需要新的M11组合必须另有显式SOP，当前不实施。

## 5. 逻辑核对与研究处理

H2/H1<1但u2/u1>1是合法描述，不是矛盾或拒绝条件；r2/r1不足旧M10门槛也不影响已成立的M2。特征计算不生成新确认，不改锚和期限，因此不会与母信号确认/失效发生另一套规则冲突。

M10主版及外部变体从独立优化队列退出，旧文档与结果保留；M2主体保持。本次只交付特征SOP，不计算交易数、胜率或PnL，不进行测试，也不声明当前模块已经输出这些字段。
