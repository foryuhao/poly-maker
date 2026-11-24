# 承接与弱供家族诊断：双轮弱供（由M14降级）

版本 `repeated-weak-ask-diagnostics-v1`。保留M14中“同Ask两次可比新受击均弱恢复”的组件描述，退出独立交易策略优化。诊断不要求Bid配对，不产生固定锚价交易信号；关联母候选时不改变母确认、失效或执行。

```json
{
  "document_type": "diagnostic_feature_sop",
  "feature_id": "REPEATED_WEAK_ASK",
  "version": "repeated-weak-ask-diagnostics-v1",
  "status": "SPEC_ONLY_DIAGNOSTIC",
  "research_family_id": "F_SUPPORT_WEAK_SUPPLY",
  "origin_strategy_id": "M14",
  "basis_file": "../signal_discovery/spec_archive/M12_M15_DISPOSITION_BASIS_20261116.json",
  "basis_sha256": "e3ad4086a03349c3aab949f1f2f07a98a66e11981f0377486faffd33d856009d",
  "source": "EXISTING_M14_ASK_CHAIN_FACTS_WITH_EXPLICIT_VERSION_PROFILE_AND_PARAMETER_HASH",
  "source_profile_default": null,
  "requires_bid_pairing": false,
  "own_formation_thresholds": null,
  "computed_at": "SOURCE_A2_PUBLICATION_OR_PARENT_CONFIRMATION_FROM_ALREADY_AVAILABLE_FACTS",
  "parent_binding": "EXACT_PARENT_VERSION_CANDIDATE_TOKEN_GENERATION_AND_WEAK_ASK_LEVEL",
  "multiple_source_matches": "EARLIEST_A2_CONFIRMATION_KEY_THEN_A1_KEY_THEN_SOURCE_ROOT_ID",
  "features": ["u2_over_u1", "d2_over_d1", "r1", "r2", "r2_minus_r1", "confirmation_gap_ms"],
  "source_lifecycle": "SOURCE_A1_TO_A2_GUARD_HANDOFF_AND_ORIGINAL_A2_DEADLINE_NO_RENEWAL",
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

## 1. 来源版本与形成事实

本诊断只读取已有、可追溯的M14 Ask侧A1→A2组件事实；不从母策略的单个弱轮假造第二轮，不从旧M14完整信号记录推断所有Ask链，也不调用扫描、回放或新检测器。来源须明确strategy/version、SOP或规则hash、parameter hash、Ask观察窗、稳定反证口径、输入合同、root/round IDs及实际可得键。

可用的版本来源包括[原主SOP快照](../signal_discovery/spec_archive/M14-21f1364f1f3a4a32.md)和此前外部[R2参数快照](../signal_discovery/spec_archive/parameters-6fa7242ff121f4a5.json)对应的完整来源规则。主版Ask窗500ms、r≤0.30、d≥0.05、u/d比0.5–2、恢复增量≤0.10、A2形成期限2s；R2为250ms、0.40、0.02、0.25–4、0.20、3s。这两个profile不可拼接，R2参数文件本身也不能替代来源的时钟、guard与质量定义。没有完整profile可证时为UNAVAILABLE；不默认采用主版、不将R2改名为主版。

两轮均须有材料性直接BUY与同档损失；同token/generation/Ask原价，A2首笔的received及输入键严格晚于A1实际确认。第一轮尝试失败不跳过，A2不得重用于同来源策略的重叠root。每轮的Q0/H/L/u/d/r及比较阈值全部沿原profile，不因诊断化新增放宽。两弱轮不等于DF的“先强后弱”，也不等于完整AP_BUY。

## 2. 诊断可得与母候选关联

来源完成A2的真实发布边界后，可记录一份Ask链描述，即使从未配到Bid。A1上界须一直维护到A2发布，A2从自身窗口终点到发布及后续的数量路径按原规则审查；已破界、迟到不可重建或质量未知不能报告为当前有效。

若用于描述承接与弱供家族某母候选，只在母候选确认边界读取当时已经可得、仍有效的链。必须同市场/token/generation，且A2原Ask价与母候选明确绑定的弱供档一致；记录母版本、candidate/root/弱供证据ID和源A1/A2身份。母策略没有可绑定的Ask档或接口没有提供来源链，则相关字段null并说明原因，不延迟母确认等待特征。

多个当前来源链可匹配时，按`(A2确认键,A1确认键,源root ID)`升序取首个，确认键沿原`(available,input_seq,subindex)`；不按恢复更弱、寿命更长或事后成交择优。只在明确选择的一个来源profile内取链，不能跨profile择优。源A2若在母确认之后才成立，不能事后追加为当时已知特征。

母弱供轮与源A2可以是不同窗口/版本的事实，关联仅表示同档、当时共存，不代表事件独立。记录共享成交和轮次身份；无法判断共享关系时标UNKNOWN，不能把同一段成交计为两份独立证据。M4、M12、M19均不因本文件自动接入诊断或更改参数。

## 3. 描述字段

沿来源定义`u_i=H_i/Q0_i`、`d_i=L_i/Q0_i`，保留两轮Q0/H/L/r、形成窗口、确认键及当前上界/截止证据。

| 字段 | 定义与限制 |
|---|---|
| u2_over_u1、d2_over_d1 | 同Ask两轮归一化冲击与减损比，不是M12的跨侧比较 |
| r1、r2、r2_minus_r1 | 记录恢复水平及绝对差；不用r2/r1，允许r1=0 |
| confirmation_gap_ms | 两轮确认可得时刻之差，不等于完整形成耗时或待执行寿命 |
| source_current_at_parent | 仅描述母确认边界时来源链状态；有完整覆盖但无匹配为NO_MATCH，缺字段/覆盖为UNAVAILABLE，来源失真为UNKNOWN |

分母必须可证、有限且大于0；否则相应比值null并记录原因。无匹配不能填成0恢复、0冲击或“供给强”。不新增“诊断合格才入场”条件，也不将特征数值变成评分或筛选。

## 4. 维护、失效与历史

诊断没有独立信号寿命或交易守卫。引用的Ask链仍按源profile的A1→A2上界交接、原A2期限、原档存在、稳定Ask反证、数据质量维护；只使用Ask侧生命周期，旧完整M14缺少Bid、Bid过期或Bid floor失败不伪装成Ask链本身失效。若输出只有完整M14结束、无法区分Ask是否仍有效，则该当前状态为UNAVAILABLE，不能猜测延续。

源Ask失效、到期的同边界，先处理失效再判断能否附着于新母候选。旧链不会因后来又变薄复活，也不换A1/A2续期；新的链沿来源自己的新事件及去重规则。诊断不新增扫描或重启政策。

已记录的母确认快照保留为历史描述，后续Ask结束不会额外取消母候选；母信号是否结束仍由其本身的guard决定。来源更正保留原值并追加质量标记，不用未来数据回写当时状态。诊断字段缺失不取消母信号；若反证同时否定母信号自己的证据，仍须执行母质量合同。

## 5. 执行与冲突核对

本诊断没有P、方向订单、预算或Maker/Taker准入；母信号按其固定锚价合同执行。不能因为记录了一条无Bid的Ask链就发单，也不能把M14旧订单改名合入M12/M4。

来源A2确认与当前数量guard沿原profile，不能先确认再跳过发布前破界。母信号可以成立而本诊断无匹配，母信号也可先结束而旧诊断作为历史保留，均不构成冲突；因为本特征没有第二套确认/退出权。

当前仅交付描述接口，未实现、未测试，也未证明额外方向信息或结算优势。M14原独立策略退出优化；本特征不计作新M策略、新原语或独立V分支。
