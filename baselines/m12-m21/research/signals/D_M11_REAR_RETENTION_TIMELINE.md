# M11 诊断：母信号之后的后档续托

2026-11-17，`m11-rear-retention-timeline-diagnostics-v1`。M11退出强制附加筛选与独立调频，保留[原SOP](M11.md)供追溯。后档变化只描述母确认之后的路径，不延迟母确认、发单，不增加母退出或订单。

```json
{
  "document_type": "signal_diagnostic_feature",
  "feature_id": "M11_REAR_RETENTION_TIMELINE",
  "version": "m11-rear-retention-timeline-diagnostics-v1",
  "status": "SPEC_ONLY_DIAGNOSTIC",
  "origin_strategy_id": "M11",
  "research_family_id": "O_REAR_RETENTION",
  "allowed_parent_definitions": [
    "M4.md",
    "M9.md",
    "V_M9_EX_HANDOFF.md"
  ],
  "baseline_at": "EXACT_PARENT_CONFIRMATION_COMPLETE_INPUT_BOUNDARY",
  "diagnostic_clock": "AVAILABLE_MONOTONIC_OBSERVED_STATE",
  "fixed_lower_bid_levels": 2,
  "reference_cross_window_ms": 500,
  "reference_retention_ms": 500,
  "reference_net_add_min_shares": "0.5",
  "reference_net_add_initial_fraction": "0.10",
  "reference_retention_first_cross_fraction": "0.50",
  "observation_max_ms_from_parent_confirmation": 1050,
  "reference_thresholds_role": "DESCRIPTION_ONLY_NOT_TRADING_GATES",
  "missing_feature_blocks_parent": false,
  "changes_parent_confirmation_or_deadline": false,
  "creates_signal_order_or_exit": false,
  "future_features_backfilled_at_parent_confirmation": false,
  "requires_new_capture": false,
  "adapter": null,
  "tests_run": false,
  "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
  "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762",
  "baseline_definition_file": "M11.md",
  "baseline_definition_sha256": "1788ef2a8444eb964eecce7efc6281c699b39e0d3b69ab096ae53ae35401fe83"
}
```

## 1. 母候选与基线

只对显式选择的母定义、版本和root提供接口，不自动接入实盘M4。M4沿其本身冻结信号版本；M9主版与EX交接版各自绑定，不混用参数。旧M10已为诊断，不再作为母信号；不自动改挂M2或其他策略。接口声明不代表当前模块已输出这些字段。

在母实际确认cP的完整输入边界，冻结母P严格下方最近两个真实存在的Bid档P1/P2、初量Q01/Q02及来源/generation。不足两档或来源不够则此项UNAVAILABLE，母按自身规则继续；不能后来换档补齐。有诊断不代表母输入一定有效，真正否定母原档数据的反证仍由母合同处理。

与[M20的分布诊断](D_BID_DEPTH_PROFILE.md)同属数量分布研究入口，但M20以母原始触发之前为基线、在母确认取值；本诊断以母确认为基线、以后才有路径结果，不共享数值或合并为同一时点特征。

## 2. 有限时段的描述

依已有可得事件，记录`delta_i=Q(Pi)−Q0i`，不累计正跳变。tCross、描述区间和观察截止均用与母cP相同的available单调时钟，保存原received及输入键但不事后重排。500ms描述的是本地已可得状态段，不宣称与历史M11接收窗口逐条等价。参考越线标准仍为`Ai=max(0.5,0.10Q0i)`，只用于可解释的描述，不是策略准入阈值。

在cP之后且严格<cP+500ms，若首次两档同时delta≥Ai，记tCross及可得键，冻结当时ai和`Fi=max(Ai,0.50ai)`。后续完整500ms是否每个已知状态delta≥Fi，作为留存路径描述。若未越线，记NO_JOINT_CROSS；途中跌破记RETENTION_BROKEN；两者均不取消、不延迟母信号，不在同母root重新挑更好的cross。缺数量/路径不能填成未越线或破界，记UNAVAILABLE/UNKNOWN。

后档明确消失可描述LEVEL_LOST；仅超出可见范围或缺源不能补零。只有连续、可证明的输入路径才可描述完整留存，计时器独自走完不补出覆盖。某后档变化没有违反母自身守卫时，诊断不得自行撤单。无需对每次母成交启动新诊断，不将自己的挂量当后档支持的独立证据。

诊断观察最多至cP+1050ms，或母信号结束/到期、generation结束等更早可得边界。只使用该边界前实际已可得的信息；如果原500ms留存尚未完成，标CENSORED_PARENT_ENDED或UNAVAILABLE，不推测后续通过。允许的50ms仅为原完整路径描述的处理余量，不延长母deadline；未处理的晚信息不能回填已结束的诊断记录。无需为了该描述另采行情或运行任务。

## 3. 防止未来信息和诊断权力回流

每个描述都保存`parent_confirmed`、`as_of_available`、输入键、已观察区间和数据状态。cP时只有基线，后续才产生的cross/留存结果不许回填为入场当时已知；后续统计若按它分组，必须明确是事后路径描述，不能宣称母入场使用了这份信息。

不产生候选、独立TTL、过滤、排序、发单、平仓或退出。旧M11的500ms＋500ms等待、后档Fi维护及“母必须等留存完成”从母交易合同中移除；本文件中的相同数字只帮助描述历史M11形状，不再成为母动作条件。母本来的锚、期限、输入保护和执行方式完整保留。

仅诊断字段缺失不影响母信号；若发现母所依赖的输入本身无效，按母质量合同处理，不能用“诊断无权退出”保护无效母信号。来源更正追加标记，不静默重写当时值；成交及在途事实不得删除。

## 4. 边界核对与输出

- 母cP已确认但后档尚无变化：母立即按自己的执行规则判断，不等500ms。
- tCross之后母先到期：留存描述截断，不能借诊断500ms续命。
- 两后档后来减少：只影响该描述；母自己的P/floor失败仍按母规则结束。
- M10诊断存在：它不成为母root，不自动建立本观察。
- 同一母附多个描述：仍是一份母候选，不增加交易数或独立信号计数。

输出feature/version、母版本/hash/root、固定档位及基线来源、各delta、tCross、ai/Fi、路径状态、实际可得键及截断原因。只定义小型摘要与证据引用。未实现观察器或适配器、未做信号测试，不承诺频次或结算优势；原M11及历史结果保持。
