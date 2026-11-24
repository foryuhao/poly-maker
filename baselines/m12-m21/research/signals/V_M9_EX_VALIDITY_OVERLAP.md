# M9 增频分支：衰竭有效期内接托复验

版本 `m9-continuous-ex-overlap-handoff-v1`，`SPEC_ONLY`。**当前支撑与连续维护中的卖压衰竭证据相遇，新复验H/L取得资格后接棒；完整复验通过且信号有效时，在固定锚价成交。** Maker/Taker独立列出。

直接基线为[V_M9_EX_HANDOFF](V_M9_EX_HANDOFF.md)，其上游为冻结外部M9 v8。旧分支和[M9主SOP](M9.md)保留。本次仅将局部3秒EX证据的创建提前至EX实际确认，并把EX单桶30%上限降为诊断；原H/L资格交接不提前，首SELL不是交接点。不修改共享EX原语或运行版本，不新增M编号；未测试、未证明频次或结算改善。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M9",
  "variant_id": "M9_EX_VALIDITY_OVERLAP",
  "version": "m9-continuous-ex-overlap-handoff-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_SELL_ABSORPTION",
  "baseline_version": "m9-ex-history-handoff-v1",
  "baseline_signal_variant": "m5-m6-m9-m10-density-v8",
  "basis_file": "../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json",
  "basis_sha256": "03ce596aa7a330f63e177748033c809bc7a0720cc6c307beb5ceccf782f43b75",
  "main_sop_preserved": "M9.md",
  "calculation_contract": "LEGACY_COMMON_WITH_M9_V8_AND_EXPLICIT_EX_OVERLAP_OVERRIDES",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "input_policy": "FROZEN_V8_FEED_WITH_VERIFIED_COMPLEMENT_MAPPING_AND_DEDUP",
  "private_seed": true,
  "seed_bid_rounds": 1,
  "seed_bid_round_ms": 500,
  "seed_bid_recovery_min": "0.20",
  "seed_hold_ms": 10,
  "seed_support_ttl_ms": 6000,
  "private_ex": true,
  "ex_observation_ms": 750,
  "ex_bin_ms": 250,
  "ex_total_fraction_max": "0.49",
  "ex_bin_fraction_max": null,
  "source_ex_ttl_ms": 750,
  "seed_ex_freshness_from_ex_confirmation_ms": 3000,
  "pre_handoff_ex_resume_fraction_min": "0.50",
  "retest_rounds": 1,
  "local_round_ms": 500,
  "local_recovery_min": "0.20",
  "local_hold_ms": 10,
  "hold_start_recovery_min": "0.20",
  "hold_clock_reset_below": "0.16",
  "held_revoke_below": "0.08",
  "material_hit_and_loss_min_shares": "0.5",
  "material_hit_and_loss_min_initial_fraction": "0.0001",
  "first_retest_gate": "AVAILABLE_INPUT_KEY_STRICTLY_AFTER_SEED_CURSOR_AS_V8",
  "first_retest_failure_policy": "END_DO_NOT_SKIP",
  "handoff_event": "FIRST_CAUSALLY_AVAILABLE_JOINT_H_AND_L_QUALIFICATION",
  "handoff_requires_preexisting_seed_validity": true,
  "same_input_priority": "PRE_HANDOFF_INVALIDATION_BEFORE_HANDOFF",
  "post_handoff_ex_role": "HISTORY_NOT_CURRENT_REGIME_VETO",
  "post_handoff_ex_regime_exit_veto": false,
  "historical_formation_counterevidence_and_quality_exit": true,
  "rolling_sell_ms": 750,
  "residual_fraction_strict_max": "0.95",
  "rolling_pressure_reset_at_handoff": false,
  "formation_ms_from_seed": 8000,
  "seed_combination_span_max_ms": 20000,
  "post_retest_rf_ttl_ms": 8000,
  "original_ap_history_ttl_ms": 20000,
  "confirmation_lateness_max_ms": 50,
  "current_bid_quantity_guard": "ORIGINAL_P_POSITIVE_NO_NEW_COMMON_40PCT_FLOOR",
  "root_policy": "FROZEN_V8_ONE_ACTIVE_PER_TOKEN_SEEN_PAIRS_CONSUMED",
  "numeric_change_from_selected_baseline": true,
  "logic_change": "EX_PROXY_BORN_AT_EX_CONFIRMATION_AND_EX_BIN_CAP_DIAGNOSTIC_ONLY_QUALIFIED_HANDOFF_UNCHANGED",
  "signal_tests_run": false,
  "baseline_definition_file": "V_M9_EX_HANDOFF.md",
  "baseline_definition_sha256": "56d68b292e3a2a7e5883171af3eaaa56f814bd9c5d54b0842fcc4d757c85aefa",
  "upstream_baseline_version": "m9-local-ex-partial-support-retest-v8",
  "diagnostic_ex_bin_fraction_max": "0.30",
  "diagnostic_missing_invalidates": false,
  "ex_formation_policy": "M9_LOCAL_RECOMPUTE_TOTAL_CAP_ONLY_NOT_REUSE_OLD_BIN_FILTERED_FACTS",
  "ex_proxy_created_at": "ACTUAL_EX_CONFIRMATION_BEFORE_SUPPORT_PAIRING",
  "ex_proxy_maintenance_starts_at": "ACTUAL_EX_CONFIRMATION_CONTINUOUS_THROUGH_PRE_HANDOFF",
  "seed_requires_native_ex_current": false,
  "seed_requires_continuously_valid_local_ex": true,
  "ex_proxy_failure_is_terminal": true,
  "early_handoff_at_first_sell": false,
  "revision_date": "2026-11-17"
}
```

## 1. 原语组合、来源及变化范围

`EX确认即建立连续维护的局部证据 → [live(S_L,BID@v8) ∥ live(EX_local, dEX=cEX+3s)] → 新R首次H/L合格时hist(EX) → 新R完整补回确认 → E(P,G,d; mode)`。S与EX任一先形成均可；实际相遇时两者仍有效，不重新计时。

S和R都是本策略局部单轮、20%部分恢复构件，不能写成P_RF两轮、BA守价或COMMON末尾100ms恢复50%。EX使用本版M9专属49%总量门槛，30%单桶只诊断，也不是 [P_EX](P_EX.md) 主版的25%／1/6阈值。原语名称只标语意，计算身份由本版及冻结来源决定。

[基线清单](../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json)冻结了v8参数、source manifest、相关源码、原SOP及共用合同的hash和完整快照。优先级为本SOP明确覆盖项 → 直接旧分支 → 该清单M9冻结来源及 [LEGACY_COMMON](LEGACY_COMMON.md) 的计算/质量合同；执行只按固定锚价合同。不能随运行目录的新参数漂移。

沿用旧分支：**EX在合法H/L资格交接以后只证明前史，不再要求市场继续处于旧EX当前态。** 本次新增的是配对前也连续维护3秒局部EX，以及单桶形态诊断化。原50%退出从EX确认起一直维护至交接输入（含该输入），不是到S出现才开始检查。交接后由新R、95%余波上限和原档等守卫承担当前有效性。20%恢复、10ms保持、6s支撑及原交接点不变。

这与M1的当前EX加多轮承接不同，也与M5当前卖攻重叠承接不同；本版仍要求衰竭前史及随后一次全新复验，归M9同家族分支，不另计独立信息。

## 2. 输入、受击轮与种子

使用同token、generation及固定原Bid档P，保持v8 feed的已核验互补映射、去重和来源政策；AP、EX、R、滚动SELL必须使用同一口径，镜像不能重复计量。价格只识别档位与执行合法性，不以涨跌作方向证据。覆盖、book/source新鲜度、tick、重建、交叉盘、迟到和乱序按冻结feed检查；缺失不等于无成交。只有L2聚合量，不推断逐单身份或所有减量的成交归因。

S和R各自从本观察器首笔同P SELL开500ms接收时间窗 `[t,e)`。冻结成交前Q0>0，计算H、Qmin、Qend，`L=Q0−Qmin`，`m=max(0.5,0.0001Q0)`。H≥m且L≥m才合格；`r=clip((Q−Qmin)/L,0,1)`。

保持规则忠实沿用v8滞回：r≥0.20时启动10ms计时；r<0.16清空正在进行的计时，r<0.08撤销已取得held；每次新低同时清空计时和held。合格后取得held且封口r≥0.20才通过。**已取得held后跌至0.08–0.16会清计时但不撤销held**；这不是末尾连续10ms都≥0.20，不暗中改成更严格恢复。水位和覆盖完整、封口可得不晚于e+50ms，否则UNKNOWN结束相关观察。

专属S确认cS后有效至dS=cS+6s。其500ms轮只需一轮；当已有S仍有效时新好轮不替换它。合格失败轮、原P丢失和质量反证按原来源结束S。

原AP_SELL保留共享750ms定义：冻结对侧前三档总深度D，V0≥max(1,0.0001D)，方向份额≥65%，至少2条成交、至少2个250ms桶有该方向且最后桶有量、最大单笔≤0.95V0。AP确认cAP后开启全新750ms EX观察；三个250ms SELL桶总和≤0.49V0，完整覆盖且封口及时，才确认本版局部EX于实际可得cEX。每桶≤0.30V0只记诊断，任一超过30%不提前结束观察、不拒绝EX或代理，也不作为后续退出。完整覆盖与可靠总量仍必需；可选诊断缺失记null，不能以此放过输入UNKNOWN。反向AP在形成中仍结束观察。

本版必须从同口径成交及冻结AP重算局部EX；旧30%观察器已丢弃的窗口，不能在末端只改组合条件就称已支持本版。共享P_EX和其他M9版本保持原样。局部EX原生事实仍为750ms TTL，滚动750ms SELL≥0.50V0或反向AP仍结束其当前态；本版独立策略证据只承接纯自然TTL，不承接硬反证。

## 3. EX确认即维护、有效期配对与交接前阶段

每份本版EX在cEX实际确认时即创建唯一局部代理，冻结EX/AP身份、token/generation、V0、cEX及`dEX=cEX+3s`，不要求当时已有S。代理先维护，不等于策略候选或交易root，不冻结尚不存在的P。从cEX当时的原滚动buffer开始，连续处理所有必要成交、AP退出、历史真实性及质量事件；不能清空形成期仍在750ms窗口内的SELL。

从创建输入起，滚动750ms SELL≥0.50V0、新AP_BUY或另一AP_SELL量≥0.50V0等旧硬退出、历史证伪或质量失败均终止代理；同一EX不得重新建代理或恢复有效。缺少cEX至当前的维护记录或无法区分结束原因，标UNKNOWN/UNAVAILABLE，不在S到来时补造“此前一直有效”。原生EX纯750ms TTL到期只记历史退出；既有局部代理可继续至dEX。组件存储不能因原生TTL自动丢弃本版代理所需引用和buffer；可用于新配对的当前代理最长3秒，失败/到期即结束其配对资格。已经合法q交接的root仍保留必要历史引用与当前滚动buffer至自身原截止，按第4节分类消费代理结束，不能把3秒清理变成q后的额外退出。

在任一新可得事实或合法边界，S与局部代理都当前有效时按原顺序尝试配对；不再要求原生EX还在750ms当前态。从S与AP较早start至配对可得时刻跨度仍≤20s。每个`S.id|EX.id`只尝试一次，同token本分支最多一个形成中或当前root。其他种子在占用期间仅登记已见，不能以后倒选；提前维护代理不解除此消费规则。配对才冻结P、S/EX/AP身份、V0、确认键及全部原截止。

种子配对可得时刻记cSeed，必须`cSeed<dSeed=min(cS+6s,cEX+3s)`，同时所有已知当前反证已处理。代理早在cEX创建，配对不新建、不重置3秒；原生EX自身TTL不延长。形成截止仍为`dForm=cSeed+8s`，原20秒组合跨度、首选根规则保持。S先到时也按同一交集处理，不要求S晚于EX。

复验观察器从种子当前输入cursor之后首个新同P SELL开R；按冻结v8以`(available,input_seq,subindex)>seed_cursor`作跨阶段gate，统计窗仍用received，不擅加received严格晚于cSeed的新门槛。不得复用种子已计成交或在源时间不可重建时把旧包当新证据；原始批次内部次序必须可证。

交接前须同时满足：S未失效、局部EX代理未失效、原生EX没有已知硬退出、`now<dSeed`、原P正量、质量有效。原生EX的**纯自然TTL**可由已冻结的3s freshness承接；旧EX的压力恢复或反向AP等退出仍否决。代理从cEX起已经持续检查滚动SELL≥0.50V0，以及新AP_BUY或另一AP_SELL量≥0.50V0的旧退出规则；配对前后同一连续链，不能因为原生750ms已到期而漏检。

从种子起全程还须滚动750ms SELL严格 `<0.95V0`。若R的最小合格量m≥0.95V0，按基线结束不可满足的复验。第一R封口不合格即结束，不能跳过后找更漂亮的一轮。

## 4. H/L资格交接：旧EX何时不再否决

交接时点q是第一R**首次同时达到H≥m、L≥m且这些事实已实际可得**的输入键，须 `q.available<dSeed` 且 `<dForm`。第一笔小SELL本身不是交接；仅未来完整R才合格也不能回填q。

该输入先计入流量/深度、应用当时已知的交接前失效，再考虑交接。如果它既令滚动SELL达到50%旧界、又令H/L合格，旧EX先失效，root结束。不得先设qualified再忽略同输入反证。若一个原始frame有可信子序，逐子项执行上述规则；q之后的子项适用交接后规则，最终确认/执行仍须等完整frame处理完。没有可证子序、无法判明反证先后时为UNKNOWN，不推定交接在先。

合法q到来才设置 `EX_role=HISTORY`，记录q键及交接前审核结果。所有原时钟、V0和滚动成交buffer原封保留。此时仅获准继续观察，**不确认候选、不提交订单**。

| 旧EX事件或其他反证 | q之前（包括触发q的同输入） | 已合法交接之后 |
|---|---|---|
| 原生EX自然750ms到期 | cEX起持续维护的3s代理仍有效即可配对/继续；不等S出现才补建 | 作为历史退出记录，不独立否决 |
| 局部EX代理／旧S自然到期 | 到dSeed不能再取得资格 | 允许自然到期，受dForm及后续新证据截止约束 |
| 旧EX因SELL≥0.50V0结束 | 结束root | 记录旧EX结束；当前只检查SELL<0.95V0，不再叠加50% |
| 新AP_SELL或AP_BUY使旧EX结束 | 沿旧退出规则结束root | 不仅因新AP身份而结束；其真实SELL仍进入95%滚动量界 |
| 旧EX正常被后续市场阶段替代／结束 | 按其明确原因处理，不能默认为可交接 | 不更新历史引用，不续期，不因旧当前态结束而否决 |
| 证明旧AP/EX形成原本不成立的反证、来源错误或质量丢失 | 结束；未知标UNKNOWN | 仍结束；“历史”不豁免事实真实性 |
| 原P消失、旧S已记录硬反证、当前新R合格却补回失败、95%量界破、当前质量失败 | 结束 | 仍结束 |

**必须按语意与发生阶段分类，不能简单忽略所有`HARD_INVALID`。** 若退出原因或因果位置无法判断，则UNKNOWN结束。后到的形成阶段反证不能伪装成“q后新市场阶段”。历史S身份不替换；保留原来源已记录的硬反证，S自然到期后的新无关S不能追溯否决或续命本root，新R及P的持续观察负责当前守卫。

共享EX与旧版局部EX状态机不变，其他策略仍按各自规则消费；本分支使用取消30%硬门槛后的独立局部EX计算身份。本分支在EX形成桶处理、代理创建/到期清理、S/EX配对、`on_fact`、滚动检查、source-end传播、代理结束和引用注册等**全部消费路径**都须使用同一分阶段规则；只删掉一个50%判断而留下另一条结束回调，不符合本SOP。

## 5. 完整确认、期限与当前维护

q以后，第一R仍须完成原500ms窗，以20%/10ms滞回规则合格关闭，并在首个合法发布边界通过全部当前守卫，实际可得cR须严格早于dForm。未知或不合格/补回失败都终止，不能更换R、P、V0或重启形成计时。

此时确认局部单轮新RF，`d=min(cR+8s,cAP+20s)`；确认必须早于d。8s来自**新受击与新补回证据**，不是EX或旧S续期。原8s形成限在确认后完成使命，不再另作当前截止；q从不额外加8s，执行回执也不续期。

当前等待继续维护：

- 原Bid P的可见量>0、同generation、完整质量；不新增COMMON的40%恢复floor。
- 全token、同输入口径的滚动750ms SELL严格小于0.95V0，等于即结束。按v8 frontier处理已知当前输入；q之前仍在窗口内的SELL、其他档SELL均不能清掉，只计P的R成交量H不能替代这项全局检查。
- P后续局部500ms受卖轮：合格但未通过20%/10ms恢复则结束；后续不合格轮按基线不自动否决。每轮仍保留`m>=0.95V0`时结束的预算检查。新好轮不延长d。第一R不得跳过的要求只针对形成首轮。
- 原S已记录硬反证、历史真实性反证、输入质量异常、必要数据不可得及`now>=d`结束。已结束root不复活。

从配对后首个R开始还需完整500ms；q可能早于封口，但不是缩短确认窗。等待新SELL、取得EX与S、输入延迟均另计，不能称固定500ms即可从空白盘口完成整个信号。

## 6. Maker/Taker执行与退出

两种方式共同要求：信号已确认且当前有效、锚P不变、实际成交归属按 [固定锚价合同](FIXED_ANCHOR_CONTRACT.md) 记录。Maker在合法P以post-only等待；Taker保留意向，只有P有可主动取得的对手量且信号仍有效时才申请。

本版保留原Bid正量。正常未交叉同token盘口中，它与同P的Taker买入没有共同准入状态；必须报告该执行限制，不能为取得成交放松原P守卫或改价。信号频次与某执行方式能否成交分开。400ms发送余量、额度、队列和市场时段是独立执行配置，不是本版新增确认条件。

失效或到期先于同边界新准入；停止未发意向、通知执行端处理在途/未成交部分。实际成交、非精确P成交及顺序未知均保留，不以失效或取消请求删去已发生暴露。没有自动Maker/Taker切换、改价、重发或续期；不自动定义成交后的平仓。

## 7. 确认／失效冲突核对

以下为定义推导，未运行策略测试：

| 边界 | 本版结论 |
|---|---|
| EX观察三桶35/5/5，V0=100 | 总量45≤49可形成本版EX，单桶30%诊断为false；不得沿旧观察器先拒绝再假装诊断 |
| cEX=0、原生到期0.75s，S于1.20s确认 | 若代理自0起连续有效且其余条件成立，可配对；dSeed=min(7.20,3)=3s，不是4.20s |
| 上例0.90s曾达到50%退出，1.20s又低于50% | 代理已终止，不能配对或重建；不能只看S到来时快照 |
| S到来时缺少0.75s以后代理维护证据 | UNKNOWN/UNAVAILABLE，不拿最新快照补连续历史 |
| S先确认、EX后确认，或cSeed=dSeed | 前者有效重叠即可；后者到期优先，不配对 |
| V0=100，q前滚动SELL=40且H/L取得资格；q后升至70 | 若其他条件通过，可继续完成R；旧50%退出不再隐藏否决95%余波规则 |
| 达到H/L资格的同输入令滚动SELL=50 | 交接前EX先失效，不得转历史救活 |
| 合法q后滚动SELL=95 | 新当前量界失效，即使R恢复很好也结束 |
| q恰等于dSeed，或cR恰等于dForm | 到期优先，不交接／不确认 |
| q后仅旧EX到期或反向AP出现 | 不独立结束；历史真实性、当前SELL、R及P守卫仍检查 |
| 新R已过而旧EX出处被证伪 | 仍结束，不能以HISTORY绕过质量问题 |
| 原S自然到期后新R完整成功 | 交接合法时允许按新cR生成8s期限；不要求旧S覆盖新期限 |

交接前50%仍可能阻止不少root取得资格，本版不承诺解决这一瓶颈。未清出滚动窗的旧SELL也会挤占新R的压力预算。单活动root占用更久可能改变后续种子接纳，因此不能推断全样本信号数必然单调增加，更不能宣称胜率或结算概率已提高。

输出应包括variant/version、基线hash、S/AP/EX/R引用、P、V0、cSeed/dSeed/dForm、q时刻与键、交接前通过的guard、旧EX结束原因及阶段、cR/d、H/L/r/held、滚动SELL、状态及结束原因、执行模式/真实成交归属。仅输出必要摘要与原始证据引用，不复制逐tick日志。首个JSON与正文共同为交付定义；没有实现检测器或适配器，没有测试。

本次没有提前交接：第一笔小SELL、R开始、仅H达标或仅L达标均不能把EX转历史；触发q的同输入仍先检查50%和全部交接前退出。首R不合格仍结束，不增加跳轮机会。新增诊断包括各EX桶量/30%标签、cEX至cSeed的连续维护证据、配对是否晚于原生750ms、dEX及代理结束原因；750ms本身仍是原生TTL，不是删除EX真实寿命。语意变为“与持续维护的衰竭证据相遇、整体卖压回落”，不再要求三桶各自受限。未实现观察器、适配器或测试，历史结果不改名。
