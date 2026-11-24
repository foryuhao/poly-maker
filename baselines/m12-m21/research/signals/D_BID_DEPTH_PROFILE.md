# 需求留存诊断：Bid三档分布（由M20降级）

版本 `bid-depth-profile-diagnostics-v1`。描述固定Bid锚档与两个后方档的净变化；后方回补不再由本诊断导致退出。M20退出独立策略优化，本文件不创建另一个“前聚合格”信号。

```json
{
  "document_type": "diagnostic_feature_sop",
  "feature_id": "BID_DEPTH_PROFILE",
  "version": "bid-depth-profile-diagnostics-v1",
  "status": "SPEC_ONLY_DIAGNOSTIC",
  "research_family_id": "F_BID_FRONTLOAD",
  "origin_strategy_id": "M20",
  "basis_file": "../signal_discovery/spec_archive/M17_M23_DISPOSITION_BASIS_20261116.json",
  "basis_sha256": "c248f4745b7aa57fc10f4c1c2ea7ceb0be405383ca7bfcd2221dd36d2796c89f",
  "eligible_parent_definitions": ["M17", "M18", "M21_SINGLE_ROUND", "M23_BUFFER_RETENTION"],
  "parent_binding": "EXACT_PARENT_VERSION_ROOT_INITIAL_TRIGGER_ANCHOR_AND_CONFIRMATION_KEY",
  "baseline_time": "STATE_IMMEDIATELY_BEFORE_PARENT_ROOT_INITIAL_TRIGGER",
  "rear_level_selection": "TWO_NEAREST_EXISTING_BIDS_STRICTLY_BELOW_PARENT_P_AT_BASELINE",
  "evaluation_time": "PARENT_CONFIRMATION_COMPLETE_INPUT_BOUNDARY",
  "features": ["anchor_net_add", "rear1_net_loss", "rear2_net_loss", "anchor_add_fraction", "rear1_loss_fraction", "rear2_loss_fraction", "fixed_total_ratio", "anchor_share_before", "anchor_share_at_confirmation"],
  "thresholds": null,
  "additional_window_or_hold": null,
  "independent_signal": false,
  "admission_filter": false,
  "invalidation_guard": false,
  "own_anchor_or_deadline": false,
  "execution": null,
  "creates_orders": false,
  "missing_feature_policy": "NULL_WITH_REASON_NO_PARENT_CHANGE",
  "updates_from_future_events": false,
  "implementation": null,
  "tests_run": false
}
```

## 1. 冻结母候选的原档与基线

本版仅为JSON列出的母定义声明可选接口，不自动接入它们，不接入M16。按确切版本/root取得母锚P和原始触发事件：M17为首SELL，M18为R1首SELL，M21_SINGLE_ROUND为唯一轮首SELL，M23_BUFFER_RETENTION为预加托增量事件。基线一律是该原始触发事件**之前**的已知盘口，不改用较好的低谷、第二轮起点或成交前夕。

固定P及其严格下方最近两个实际存在的Bid价P1>P2，初量q0/q10/q20都>0。记录各档明确数量来源、基线输入键、连续链与generation；quantity读取必须可证明是该事件前状态。缺两个后档、缺历史或档位身份不可证，相应诊断为UNAVAILABLE，不用未来出现的档补齐。M23中的q0对应预增厚前B，不是验证前Q0R。

输入能力足够时可用既有事件引用读取；本任务不要求新增采集、回放或大日志。M18基线跨两轮，M17为750ms窗、M21为500ms窗、M23跨种子及验证，必须保留各自起止与版本，不当作相同观察长度的可比样本。

## 2. 描述量

在母候选实际确认的完整输入边界，读取同generation下固定三档已知量q/q1/q2。令`x=q−q0`、`D1=q10−q1`、`D2=q20−q2`，初始总量T0=q0+q10+q20、当前T=q+q1+q2。输出x、D1/D2、各自相对初量比、T/T0、q0/T0与q/T。

D为正表示净减，负表示净增，不强制“后两档都退”。三档总量不增、后方增加、前档占比没有提高，也都是合法描述。占比由原数量派生，不另当一份独立确认；聚合净变化不证明同一订单从后方搬到前方。

当前某固定后档已明确归零可记0；若仅因可见范围不足或缺源而看不到，须为UNKNOWN，不能补0。分母为0、非有限或来源不明时相应字段null。共同需要三个量的字段在任一缺失时null，已可证的单档字段可保留并标各自质量。

不沿用旧M20的前增20%/至少5、后减10%–30%/至少0.5、总量不减、额外500ms/100ms保持或逐笔100ms复核。母策略本来的相应要求仍执行，不能用本诊断豁免；诊断本身不再创建这些门槛。

## 3. 生命周期与冲突

在母确认时固定一次描述，不要求三档形态在之后继续保持。后方从80回到95可以改变后续盘口，但不由本诊断触发母退出；母自己的锚档floor、当前承接、来源与截止继续生效。源量更正保留原记录、追加标记，不回写成当时已知。

没有自己的root/TTL/订单，没有阈值、不参与母候选选择或排序。仅诊断缺字段不影响母信号；若反证同时否定母锚档数据，则仍按母质量合同结束，不借“诊断无权退出”覆盖真实问题。

## 4. 家族与执行边界

该诊断可作为受卖后需求量状态的上下文；M20不等于M17，也不是M11（后档增加）的同义过滤。旧M20样本不能改名合入母策略，Ask后移的M22也不能当同向Bid前聚。Maker/Taker与固定锚价成交全部由母合同决定。

本次未定义M22的独立诊断接口或新方向原语。来源、窗口及确认时间明确后才可比较描述；字段多不代表独立信息多。未实现或测试，旧M20的完整规则保留在[历史SOP](M20.md)。
