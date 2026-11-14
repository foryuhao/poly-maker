# M4 分支：供弱后续攻

版本 `m4-weak-supply-fresh-buy-attack-v1`，SPEC_ONLY。交易语意：**Bid已有受卖恢复，Ask防守相对变弱；组合就绪之后新开始的持续买攻又确实打到这份弱供原档。在支撑、弱供和新买攻都有效期间，于固定Bid锚价成交。** Maker/Taker单列，不新增M编号。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M4",
  "variant_id": "M4_DF_AP",
  "version": "m4-weak-supply-fresh-buy-attack-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_FIXED_ANCHOR_VARIANT",
  "research_family_id": "F_SUPPORT_WEAK_SUPPLY",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "direct_trades_only": true,
  "seed_profile": "M4_FREQUENCY_V2_SIGNAL_RULES",
  "seed_support_rounds": 1,
  "seed_support_ttl_ms": 8000,
  "seed_DF_ttl_ms": 5000,
  "AP_input_override": "DIRECT_ONLY_RECOMPUTED_FROM_RAW_SOURCE_NOT_MIRRORED_FALSE_FILTER",
  "AP_first_eligible_new_attempt_only": true,
  "AP_must_start_received_and_input_key_after_seed": true,
  "AP_window_ms": 750,
  "AP_subwindow_ms": 250,
  "AP_minimum_shares": "1",
  "AP_minimum_depth_fraction": "0.0001",
  "AP_direction_share_min": "0.65",
  "AP_min_records": 2,
  "AP_min_occupied_subwindows": 2,
  "AP_last_subwindow_required": true,
  "AP_max_single_record_fraction": "0.95",
  "AP_ttl_ms": 750,
  "AP_recheck_interval_ms": 250,
  "AP_exit_share_below": "0.60",
  "AP_exit_volume_to_frozen_minimum_below": "0.50",
  "AP_exit_empty_last_subwindow": true,
  "AP_reverse_current_fact_invalidates": true,
  "target_ask_hit_min_shares": "5",
  "target_ask_hit_min_weak_round_initial_fraction": "0.10",
  "confirmation_lateness_max_ms": 50,
  "deadline": "MIN(FROZEN_SUPPORT_DEADLINE,FROZEN_DF_DEADLINE,NEW_AP_DEADLINE)",
  "bid_continuous_floor_added": false,
  "ask_continuous_recovery_ceiling_added": false,
  "post_target_buy_terminal_quantity_required": true,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "component_specs": [
    {
      "file": "M4.md",
      "version": "m4-fixed-anchor-entry-v1",
      "sha256": "4ce1613bc25bd3030f27cac0969efe259c82d0414b05731581bc7fce90f8de06",
      "snapshot": "../signal_discovery/spec_archive/M4-4ce1613bc25bd303.md"
    },
    {
      "file": "P_DF.md",
      "version": "df-m4-frequency-ask-v2-sop-v1",
      "sha256": "3756beed29c23b8b310dd57237150c41353a193e4f91431f0068b511687a469a",
      "snapshot": "../signal_discovery/spec_archive/P_DF-3756beed29c23b8b.md"
    },
    {
      "file": "P_AP.md",
      "version": "ap-legacy-pressure-750ms-v1",
      "sha256": "772cdc2a001d6fc4ec1bf836cdc258384715401e6e7c0a7c839aba1069f4bd93",
      "snapshot": "../signal_discovery/spec_archive/P_AP-772cdc2a001d6fc4.md"
    },
    {
      "file": "LEGACY_COMMON.md",
      "sha256": "e3857f08807717ceb24b9530c1a662bafe838b5907b3f2a2c72bcd56fdd0f2a4",
      "snapshot": "../signal_discovery/spec_archive/LEGACY_COMMON-e3857f08807717ce.md",
      "identity_policy": "FROZEN_BYTES_BY_SHA256_NO_DECLARED_DOCUMENT_VERSION"
    },
    {
      "file": "FIXED_ANCHOR_CONTRACT.md",
      "sha256": "9dd080082d2d74035105e010d4b43afdc7d8a9a6e52420d81b2908b28dcad3d2",
      "snapshot": "../signal_discovery/spec_archive/FIXED_ANCHOR_CONTRACT-9dd080082d2d7403.md",
      "signal_contract": "fixed-anchor-signal-v1",
      "execution_contract": "fixed-anchor-execution-v1"
    }
  ],
  "calculation_contract": "LEGACY_COMMON.md",
  "seed_selection": "FIRST_NEW_M4_PUBLICATION_WHILE_IDLE",
  "seed_clock": "ACTUAL_FIRST_COMPLETE_BATCH_AVAILABILITY_NOT_BACKDATED_COMPONENT_MAX",
  "frozen_component_tracking": "REQUIRED_UNAVAILABLE_IF_ONLY_LATEST_SLOT_CAN_BE_PROVEN"
}
```

## 1. 信息增量与反例

`[live(S_L,BID@M4) ∥ live(DF_ASK@frequency-v2)] → 全新AP_BUY@direct → 三项当前有效时原锚成交`。

主M4观察Bid和Ask受击后的恢复差，本分支再要求组合之后出现新的持续买入，并有材料性BUY落在冻结的弱Ask档。要研究的是：弱供并非只是过去一次数量响应，而是在新的买入需求出现时仍没有被原退出规则否定。若这种需求持续与供给响应不足确有信息，可能改善锚价成交的结算倾向；尚无结果支持。

反面解释：新BUY可能只是短促反弹；旧弱供的退出按轮关闭判断，尚未及时反映恢复；对手可能等买攻过去再补货。当前AP与DF并不保证真实买方更知情，也不说明同一卖方撤退。执行成功不会验证这些因果解释。

这是M4家族的顺序分支，不当成独立方向。与M15不同，种子是有强参考的DF而不是LW，随后要求完整持续AP而非一轮弱Ask；与M7不同，没有“暂停后再攻”，也不新增一轮Bid支撑。与“主M4事后选AP时刻”不同，本版提前冻结首个新AP尝试、等待和失效，失败不重选。

## 2. 种子与冻结选择

按冻结[M4信号层](M4.md)及[P_DF](P_DF.md)的frequency-v2形成种子：Bid一轮旧500ms滞回稳定、r≥0.50，寿命8s；Ask稳定参考后下一合格可比轮满足u/d比[0.1,10]、d≥0.05、r≤min(0.60,0.80r_ref)，DF寿命5s。不得替换成COMMON严格S、P_RF两轮、相对退出备选或Ask固定50%数量上界。

本策略空闲时接受第一个全新发布且可用的M4信号种子，不回选空闲前的旧候选；同一可得边界多个种子按`(确认键,DF键,支撑键)`升序取首个。确认键含available/input_seq/subindex。冻结支撑ID、P、cB/dB；DF ID、原Ask A、弱轮Q0A、cD/dD。cS取该组合经完整批次确认后实际首次可得时刻，须cS≥max(cB,cD)，不能将较晚发布倒填为组件时间。同token/generation，P<A，原档与来源均有效。

原种子用于观察，不立即确认本分支。只维护冻结组件的原期限及结束事件，不替换成更晚或更漂亮的组件。另一个正向ID出现不续期；来源明确终止冻结ID时仍结束本root。根键为原支撑ID与DF ID，每对只试一次。冻结旧组件是本分支显式规则：主M4的latest槽替换不等于旧ID收到硬失效；若来源只保留latest而不能继续证明冻结ID的期限、原档及后续轮退出，本分支标UNAVAILABLE，不假定旧ID仍活着。

## 3. 新AP的严格先后与输入口径

使用[P_AP](P_AP.md)的750ms数值及生命周期，但输入明确改为**同token有完整来源的直接成交重新计算**。这是显式输入变体，不能把已有镜像混合AP事实当等价；也不能简单对有损去重后的NORMALIZED_TRADE筛mirrored=false冒充完整直接流。

直接AP观察器按P_AP独立持续维护。种子前已启动或已确认的AP不清空、不移窗，也不用来确认本分支。只选种子之后**第一个真正新启动的AP_BUY尝试**，其首笔received严格晚于cS，输入键严格晚于种子键，received/available都早于min(dB,dD)。已有AP占用时等待其按原规则结束；不能将此期间某笔BUY强行另开重叠AP。到种子截止仍无合格新尝试则结束。

选中的第一个新尝试若形成失败或数据不足，本分支结束，不等待第二个尝试。开始之前旧AP结束不代表新AP已形成；开始之后也不能把旧形成成交补入新窗。

以新AP首笔t0冻结事件前对手Ask最多三档深度D>0、m=max(1,0.0001D)。窗`[t0,e)`，e=t0+750ms，直接BUY量V、直接SELL量O：V≥m，V/(V+O)≥0.65，至少两条可辨BUY、至少两个250ms桶有BUY且末桶必须有，最大单笔≤0.95V，覆盖与水位完整。

另取同窗内原A直接BUY量`HA`，要求`HA>=max(5,0.10Q0A)`。它保证新买攻实际触及被冻结的弱供档，原A不必始终为best_ask。HA是V的子集，不是第二份独立BUY证据，也不能把其他档成交映射成A。所需量大于可观察实际HA就失败，不更换A或降低门槛。

## 4. 形成中维护及发布

从种子起，原Bid/Ask档、generation/tick、输入质量及两冻结组件的硬失效持续检查。新AP的BUY也输入原DF的后续轮观察；若它们使一轮合格稳定Ask恢复≥0.50，则按原语顺序先终止旧DF，本root结束。即使同一处理生成新DF，也不把新ID接回本root。Bid后续合格轮失败同样结束。

全窗结束后，须有明确A数量事件在全部计量HA的BUY之后，输入键与source均严格更晚，received仍<e且实际发布前可得。A前置数量与连续路径也必须可追溯。这是本分支新增的数据确认，不等同于逐笔撮合回执；无末量来源标UNKNOWN，不补等未来事件。

新AP事实在完整原始输入批次后可引用，确认c满足`e<=c<=e+50ms`且`c<min(dB,dD)`。先处理当前批次中的组件结束、超时和质量问题，再检查AP仍当前有效、HA达标及来源完整，才确认本分支。AP自有cAP和dAP=cAP+750ms不能因组合发布重计；若批次结束晚于AP原发布时刻，使用原cAP/dAP。

没有额外“新Ask恢复必须≤30%”条件；原DF依旧是相对弱化、后续稳定轮退出。没有原Bid连续floor新增门槛。末量来源确认也不允许忽略已经发生的原语结束。

## 5. 有效期与失效

`deadline=min(dB,dD,dAP)`，确认后最多750ms。当前阶段同时要求冻结支撑、冻结DF、新AP仍有效。只用新AP原m和输入合同，每250ms检查完整最近750ms：BUY份额<0.60、BUY量<m/2、最近250ms无BUY、总量为0任一结束；新反向AP_SELL确认也结束当前AP。形成统计右开、维护包含当前检查时刻事件的边界按P_AP保留。

HA只用于已经完成的新AP形成验证，不要求等待期间重复达到HA门槛，不由新成交续期。原A/P消失、组件失败或明确END、旧截止、数据未知、历史反证均立即结束。执行请求和回执不改变c/d。当前输入先触发失效时不再发起新执行。

新AP的形成至少750ms；种子若剩余不足完成窗口便无法确认，本版不提前宣告成功。即使确认，采用旧COMMON400ms发送余量时仍可能无法准入；这属于执行配置的可行性，不修改信号期限。

## 6. 执行方式

已确认、可引用且未到deadline时，以固定Bid P为目标，按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)单独选择：

- Maker：post-only挂P，信号结束停止准入并处理未成交量。形成BUY和SELL均不能充作本单成交量。
- Taker：维持待执行意向，在P有可成交对手量且信号仍可引用时才申请。正常未交叉同token盘口中，原Bid存在与同价Taker没有共同准入状态；记录受限，不改P或忽略支撑消失。

不自动模式回退、不默认重试。可选COMMON时间/额度参考不改变形成；在途成交和实际价格照实保留。自有主动BUY不能回填AP形成，也不能用于延长本分支；自有活动及来源不明应按冻结输入合同标识，不能把自己交易当新外部确认。

## 7. 数量与时序可行性审查

文档算例：种子cS=0，dB=8s、dD=5s；新AP从100ms开始，850ms封窗、870ms可得，dAP=1620ms，则本分支deadline=1620ms，不是5870ms或8870ms。假设Q0A=100，HA至少10；V=30、O=10、BUY分布覆盖三个桶且最大单条≤28.5时，流量门槛可有交集。还须原DF在新AP期间没有被稳定轮退出、原档与末量来源有效。这是条件算例，不是模拟成交。

若新AP期间某Ask轮稳定r=0.55，旧DF即使最初由0.80→0.55产生，也会按下一稳定轮规则结束，不能因“仍小于旧参考80%”继续。本分支采用原DF主版退出，故这些路径在确认前失败；它不是一个被隐式改成相对退出的M4。

若HA已达到量门槛，但全token BUY未达AP持续性，失败；若全token AP很强但原A的HA不足，也失败。两者是范围约束，不代表两份独立方向信息。精确首选事件、旧ID结束及完整批次使结果可被复现，不能倒选有利AP。

输出variant/version、种子组件ID与来源、P/A/Q0A、cS/dB/dD、首个合格AP尝试键、D/m/V/O/桶量/最大单笔、HA与末量来源、cAP/dAP/c/deadline、组件结束原因、执行模式及成交归属。只保存小型引用和摘要。未验证实际输入能力；未实现适配器、未测试，也未计算交易数、胜率或PnL。
