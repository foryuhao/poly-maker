# LW 疑似退量：Ask 撤供 / Bid 撤托

版本 `lw-fixed-side-depth-withdrawal-v1`，**SPEC_ONLY**。这是已有 LW 的独立规格及 Bid 侧推广，沿用其主要数量比例，明确固定档位和生命周期；不算新的独立机制，也不替换 M6 或 COMMON 已有 LW。Ask 退量表示供给减少，Bid 退量表示买托减少；两者同时出现不能单凭标签判断方向。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "LW",
  "version": "lw-fixed-side-depth-withdrawal-v1",
  "status": "SPEC_ONLY",
  "origin": "EXISTING_LW_EXTRACTED_AND_BID_GENERALIZED_WITH_EXPLICIT_LIFECYCLE",
  "sides": ["BID", "ASK"],
  "side_means": "PASSIVE_SIDE_WITH_UNEXPLAINED_NET_DEPTH_LOSS",
  "source_definition": "COMMON.md",
  "source_definition_section": 4,
  "source_definition_sha256": "c3ec4443e8651e8a6f685309bdd6142166bada3de6c0846d3c18531cdc803645",
  "source_definition_snapshot": "../signal_discovery/spec_archive/COMMON-c3ec4443e8651e8a.md",
  "data_clock_contract": "maker-discovery-common-v1",
  "inherits_RF_contract": false,
  "inherits_execution_contract": false,
  "legacy_candidate_equivalence_claimed": false,
  "initial_positive_fixed_levels_required": 3,
  "root_trigger": "FIRST_EXPLICIT_NET_DECREASE_OF_PRE_EVENT_TOP_THREE_WHILE_IDLE",
  "pre_start_hit_buffer_ms": 500,
  "direct_consuming_trades_only": true,
  "formation_publication_before_start_plus_ms": 750,
  "formation_loss_fraction_min": "0.30",
  "formation_residual_fraction_min": "0.20",
  "formation_residual_to_loss_min": "0.50",
  "formation_hold_ms": 100,
  "pre_hold_failure_resets_streak_not_root": true,
  "first_completed_streak_is_binding": true,
  "publication_requires_formation_thresholds": true,
  "publication_failure_ends_root": true,
  "confirmation_lateness_max_ms": 50,
  "publication_requires_complete_original_batch": true,
  "original_front_level_must_remain_positive": true,
  "rear_explicit_zero_allowed": true,
  "current_loss_must_be_positive": true,
  "current_residual_fraction_min": "0.10",
  "current_residual_to_loss_min": "0.25",
  "current_ttl_ms": 750,
  "hit_accumulator_never_rolls_forward": true,
  "one_root_per_token_side": true,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 1. 固定对象与第一笔减量

使用 [COMMON](COMMON.md) 的时钟、质量和输入顺序，不继承 RF、支撑底线或执行合同。按 token / generation / side 独立建状态；ASK 消耗成交为直接 BUY，BID 为直接 SELL。深度消息中的挂单侧字段不能冒充主动成交方向。

空闲时，每次有明确数量来源的健康深度事件，以**事件前**该侧最近三个真实、正数量价档组成 G，初始数量为 q1、q2、q3，总量 `T0=q1+q2+q3`。本版要求恰有三个可识别初始档；不足三个为 UNAVAILABLE，不用零量补齐。第一个使这个固定集合总量下降的事件开启 root，冻结 G、T0、原最优档 p0、前后数量来源及事件键，t0 为该事件 received。

没有最低初次减量门槛；先发生小减量也会占用该 root，不能跳过它选择较大的后一次。增量消息按原数组内层次序处理；同 generation 的健康完整 book 作为完整状态处理，不把重建字典时的临时删除当成减量。参见[批次语意](DEPTH_BATCH_SEMANTICS.md)。同批前一更新可以提供后一更新的基线，但必须可辨其内层事件身份；来源不足不能用全书刷新时间补造。

从开始到结束始终使用原 G，不随最优价变化换档。p0 须持续存在且量为正；其不再最优本身不构成方向证据。两个后档可在明确删除、或所选完整快照确能证明该档不存在时记零；截断深度中的缺档是 UNKNOWN。后档重新出现只按原价格更新数量，不重设 T0。跨 generation、重连重建或失去 p0 终止。

## 2. 净减量与成交扣量

在每个可得输入前沿 b，使用当时已知数量与直接成交：

`L(b)=max(0,T0−Σ Q(G,b))`

`H(b)=Σ 直接消耗本侧、价格在G、received>=t0−500ms、且截至b已可得的成交量`

`U(b)=max(0,L(b)−H(b))`。

H 从冻结的历史起点累计到当前，不随缓存滚动、不因恢复或确认清零。开始之前 500ms 的成交可能已反映在 T0 中，仍按本合同保守扣除；这不是逐笔量价归因。root 以后新增的同侧消耗成交也进入 H，不能只统计最初 750ms。

须有前 500ms 的直接成交覆盖，并在镜像映射、过滤或跨 token 去重丢失来源之前核对本 token 的直接记录。成交身份、方向、数量、固定档位和单位必须可靠；同一成交去重，缺方向不能默认 BUY，空成交数组或深度/BBO 活跃不能证明 H=0。参见[缺席证据审查](ABSENCE_EVIDENCE_REVIEW.md)。

L2 中的新增、撤回和成交可互相抵消；U 仅是按这个观察模型扣量后的净差，**不是精确撤单量、撤单意图或同一参与者行为**。成交先到/深度先到会改变中途门槛；不可解释错位为 UNKNOWN，不靠重排使信号成立。

## 3. 形成、保持与首次发布

形成门槛为：

`L/T0>=0.30`，`U/T0>=0.20`，`U/L>=0.50`。

等号通过，L=0 不通过，不除零。等价的成交预算是 `H<=min(L−0.20T0,0.50L)`，并须有 `L>=0.30T0`；该预算是原式推导，不是额外独立证据。

观察按 received 计时，可行动判断按 available。第一段满足门槛的连续保持从 h 开始；在 `[h,h+100ms)` 每个已知数量或成交事件都检查。**尚未完成 100ms 前**门槛破坏，只清空保持计时；原 t0、G、T0、H 及形成截止不动。达到条件可在同 root 内重新起保持，不能选择后来更有利的基线。

第一段完成 100ms 的保持具有约束力，冻结 `e=h+100ms`。水位关闭 e 后，在第一个完整原始批次结束或定时检查边界发布，记 c。要求 `e<=c<=e+50ms` 且 **`c<t0+750ms`**；只在 t0+750ms 前完成保持、却在其后发布，不通过本版。到期等号结束；不能借封口迟到预算延长形成期限。

e 至 c 的所有已知路径及发布点仍须满足上述**形成门槛**；先处理同批失效，再发布。首个已完成保持的发布检查失败就结束 root，不继续等待另一段恢复。统计上的 100ms 保持不能靠同批行号插值，输入覆盖/来源不足为 UNKNOWN。该规则有意冻结首个发布机会，不能宣称与旧 LW 实际候选完全一致。

晚到输入不回填已关闭窗口、重选保持或回溯下单。若后来发现 received 属于形成/发布区间的旧成交或深度事件，会推翻已使用的保持、门槛或覆盖证明，则在发现时结束并标记迟到反证；历史发布和已发生经济记录保留。无法判断影响时为 UNKNOWN。

## 4. 确认后的当前态及结束

从 c 起采用较宽的维护门槛：

`L>0`，`U/T0>=0.10`，`U/L>=0.25`。

H 仍累计，维护等价于 `H<=min(L−0.10T0,0.75L)`。每笔相关数量/直接消耗成交均先更新并检查，再供组合使用；期间不重新要求 100ms 保持，不继承 QR 的逐笔 100ms 复核机制。

当前事实有效区间为 `[c,c+750ms)`。首次违反维护门槛、p0 消失、数据质量或 generation 失效、迟到反证，或到达截止，均结束；数量后来恢复也不能复活旧 root。门槛失败应区分 `DEPTH_RESTORED`、`HIT_BUDGET_EXCEEDED`、`FRONT_LEVEL_LOST`、`UNKNOWN_DATA`、`LATE_COUNTEREVIDENCE` 和 `EXPIRED`，不要把成交扣量增加全部命名为“供给恢复”。

确认门槛强于维护门槛，因此正常发布不会仅因切换门槛立即失效；新成交或深度事件仍可能在随后立即结束它。本版没有承诺至少可用 750ms。

每 token/side 同时至多一个形成或当前 root。结束之后，只能由 received 和输入键均严格晚于结束边界的**新减量事件**启动下一 root；结束事件及同批旧减量不能重用。新 root 不为已有组合续期。状态端点、前后来源、重置原因都须可审计。

## 5. 与已有名称和组合的关系

本版从 [COMMON 第4节](COMMON.md) 提取计算量，并增加 Bid 对称定义、恰三初始档、后档零量语意及明确首个发布/重启规则；独立版本记录这些选择。它不是“旧版 LW 已有 Bid 支持”的声明，不替换 [M6](M6.md)、M13、M15 的原组件、历史基线或结果。旧资料中未充分展开的边界不能假定与本版相同。

| 表达 | 可观察语意 | 解释限制 |
|---|---|---|
| LW_ASK | 固定近端卖量减少，已知 BUY 不足以覆盖该净减量 | 未经过真实买入检验，不等同 DF_ASK |
| LW_BID | 固定近端买量减少，已知 SELL 不足以覆盖该净减量 | 不等同原买一已失守，不自动看空结算 |
| LW_ASK + LW_BID | 两侧近端同时疑似退量 | 属于两个现有侧实例的组合，不另起“双边退量”编号；不能证明同一主体撤单 |
| S_BID + LW_ASK + LW_BID | 原档支撑尚在，但买侧更宽范围也退量 | 单档留托不足以证明买侧整体承接增强；属于 M6 家族的上下文审查入口 |
| QR_BID + LW_BID | 原买一受卖仍保量，而固定三档合计退量 | 两者可以共存；保量与多档退量不是相同范围的相反命题 |

当前态组合必须同 token/generation，分别保留原固定档组、来源、确认及截止；公共当前期限取原截止最早者，不从第二侧确认起统一重计 750ms。已有一侧到期不能当作双侧仍有效。SR 与从增厚峰开始的 LW 可能共享一次回落，不能重复当独立撤量证据；XR 是跨侧攻击后加量，与 LW 的净减量也不能只按名称相加打分。

**纸面反例（不是市场结果）：** 某旧 COMMON 支撑的 p0 底线 F=88，当前 Bid 三档各 100，总量 T0=300。随后原档仍为 100，两个后档各降至 55，固定总量变成 210；此前缓冲及观察期直接 SELL 为零且覆盖完整。L=U=90，三项形成比例为 30%、30%、100%，保持及发布时间合格便可形成 LW_BID；与此同时 `Q(p0)=100>=88`，旧单档支撑可以继续有效。若 Ask 的独立 LW 也当前成立，则“撤供＋原档留托”与“双侧退量”可同时为真。

同单位、同原档且 S 仍有效时，`ΣQ(G)>=F`，所以 LW 形成的必要条件是 `F<=0.70T0`；有 H 时进一步需 `F<=T0−max(0.30T0,H+0.20T0,2H)`。这只是数量可行性的必要条件，不能代替保持、期限和输入检查。此处使用 COMMON 的 S 作反例，**没有把其固定 floor 反向套到旧 M6 的 RF**。

“未出现 LW_BID”也不证明全买侧没有退量；可能门槛未达、root 占用、观察范围有限或数据不足。任何将其作为过滤器的后续组合，必须另写否定证据的覆盖、初始化及 UNKNOWN 处理，不直接给旧 M6 加隐式过滤。

## 6. 数据交付与执行边界

公开多档数量和直接成交在字段层面可表达这个假说；现有输入是否保留固定三档的每次改量、原始直接方向/身份、完整覆盖和接收/可得时刻，需要用户测试模块按实际输入版本核验。本任务未读整批录制验收、未实现检测器或适配器。

每个事实至少记录 `primitive_id/version/sop_hash/common_hash/input_policy_hash/token/generation/side/root_id/G/p0/q1/q2/q3/T0/t0/h/e/c/deadline/L/H/U/quantity_sources/trade_ids/input_keys/quality/end_reason`。H 的累计起点、前500ms覆盖、后档明确零量的来源及保持重置记录必须可追踪；只需必要状态摘要，不要求落全量行情日志。

本原语不提交 Maker 或 Taker 单，也不提供成交概率结论。后续策略必须独立冻结锚价、根事件选择、等待条件、首次准入、取消在途成交和经济账；形成前成交不抵扣新订单前置队列。没有测试证明其提高结算概率、达到101笔或满足去掉最盈利3–5笔后仍盈利的目标。
