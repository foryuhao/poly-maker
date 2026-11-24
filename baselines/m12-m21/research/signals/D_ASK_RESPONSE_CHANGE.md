# 承接与弱供家族诊断：Ask响应变化（由M19降级）

版本 `ask-response-change-diagnostics-v1`。保留“买入冲击、可见损失与恢复如何变化”的描述，M19退出独立策略优化。本特征不生成“轻攻增损合格”信号，不延迟母候选等待第二轮。

```json
{
  "document_type": "diagnostic_feature_sop",
  "feature_id": "ASK_RESPONSE_CHANGE",
  "version": "ask-response-change-diagnostics-v1",
  "status": "SPEC_ONLY_DIAGNOSTIC",
  "research_family_id": "F_SUPPORT_WEAK_SUPPLY",
  "origin_strategy_id": "M19",
  "basis_file": "../signal_discovery/spec_archive/M17_M23_DISPOSITION_BASIS_20261116.json",
  "basis_sha256": "c248f4745b7aa57fc10f4c1c2ea7ceb0be405383ca7bfcd2221dd36d2796c89f",
  "source_binding": "EXACT_ALREADY_SELECTED_REFERENCE_AND_CURRENT_ASK_ROUND_IDS_WITH_PROFILE",
  "parent_binding": "EXACT_PARENT_VERSION_CANDIDATE_AND_SOURCE_PAIR_IDS",
  "source_profile_default": null,
  "features": ["Q02_over_Q01", "H2_over_H1", "u2_over_u1", "L2_over_L1", "d2_over_d1", "r1", "r2", "r2_minus_r1", "loss_to_hit_1", "loss_to_hit_2"],
  "computed_at": "SOURCE_PAIR_PUBLICATION_OR_PARENT_CONFIRMATION_FROM_ALREADY_AVAILABLE_ROUNDS",
  "thresholds": null,
  "selects_reference_round": false,
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

## 1. 只描述已确定的两轮

沿母定义或既有组件明确选定的同token/generation/固定Ask价A的参考轮A1和当前轮A2，记录来源版本、profile/参数hash、round/root IDs、窗口、实际可得键和统计定义。两轮顺序及来源资格由该来源定义决定，本特征不另造一套M19窗口、不跨profile拼接、不从全局轮池挑更显著的一对。

M4等已有明确参考/当前轮的来源可提供绑定；M12只有当前Ask轮时，缺少明确A1就null，不向前搜索最佳参考或向后等待新轮。已有双轮弱供组件也可提供同一对轮，但与[D_REPEATED_WEAK_ASK](D_REPEATED_WEAK_ASK.md)共享事件，不能当两份独立方向证据。不能将旧M19候选/成交改名并入M4/M12。

源轮量纲、计量档位、直接成交范围和统计方式必须一致且可证；available不晚于诊断读取边界。源参考已按母规则转历史是允许的，不额外要求它当前仍弱或守旧floor；母当前组件是否有效仍按母定义判断。

## 2. 计算与缺失

取各轮已有`Q0_i,H_i,L_i,r_i`，`u_i=H_i/Q0_i`、`d_i=L_i/Q0_i`；按JSON输出初量比、H/u/L/d比、r1/r2和绝对恢复差r2−r1。L/H仅叫“可见损失与成交量比”，不解释成准确成交冲击系数或撤单归因。

分母须有限且>0，否则相应字段null并记录原因；r1=0不妨碍计算r2−r1，不计算r2/r1。没有输入不是0，也不是更轻、更薄或更强。比例方向可以相反，因为初始Q0不同，这不是诊断错误或拒绝条件。

旧M19的Q0比0.80–1.25、H/u比0.25–0.50、L/d至少1.25、H≥L与r2≤min(0.30,r1)，**均不作为本诊断筛选或退出**。来源自身如有这些条件保留其身份，但不能由本特征额外叠加。没有“最少改善”门槛、优先排序或评分。

## 3. 可得、历史与失效

在已有源配对发布边界可记录组件描述；若附着交易母候选，只在母确认时读取已可得的源轮及其明确绑定关系，冻结当时值。母确认后的新轮不得回填为入场已知特征；本诊断不要求母候选再等一个窗。

没有独立TTL或交易guard。母结束后描述留作历史；后续比值变化不取消母信号，不替换参考续期。来源更正保留原值并追加质量标记，不用未来数据静默修正当时计算。仅诊断缺字段不影响母信号；真正否定母证据的质量问题仍按母合同处理。

## 4. 执行与核对

固定P、Maker/Taker、原截止、订单及失效由母版本决定，本特征没有执行权。相同母候选附带多个响应字段仍是一份候选，派生L/H变化不重复算确认。

r2高于旧M19门槛、H2没有减半、L2没有扩大都可以是合法描述，不与母确认冲突。只有一轮则无配对诊断，不阻止母确认。未实现或测试，未证明信息增量；M19旧数值和完整规则见[历史定义](M19.md)。
