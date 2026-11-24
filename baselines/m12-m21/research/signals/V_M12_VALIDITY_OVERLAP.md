# M12 有效期重叠分支：承接仍在、弱供未复

版本 `m12-live-support-weak-ask-overlap-v1`，`SPEC_ONLY`。**两侧分别经过真实、归一化冲击量级可比的受击验证；Bid承接证据与Ask弱供证据在各自原有效期内同时有效时确认，在固定Bid锚价成交。** Maker/Taker单列。两次受击不必在1.5秒内确认，不宣称同期受击或同一市场冲击的因果对照。

本版以[V_M12_SHOCK_COMPARABLE.md](V_M12_SHOCK_COMPARABLE.md)为直接基线，只把独立的1.5秒配对上限降为诊断。该基线和[M12原主版](M12.md)完整保留；继承去损失比规则、各自3秒期限、冲击可比及连续数量守卫，不替换运行实现。本次只交付文档，未做测试。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M12",
  "variant_id": "M12_VALIDITY_OVERLAP",
  "version": "m12-live-support-weak-ask-overlap-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_SUPPORT_WEAK_SUPPLY",
  "baseline_version": "m12-shock-comparable-response-divergence-v1",
  "baseline_definition_file": "V_M12_SHOCK_COMPARABLE.md",
  "baseline_definition_sha256": "ff39c591ea860c392b2e6e4416b85daaade4467f7e1d7082d161239b3ddfe056",
  "upstream_parameter_baseline": "m12-fixed-anchor-entry-v1/raw-path-r3",
  "baseline_parameter_revision": 3,
  "basis_file": "../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json",
  "basis_sha256": "03ce596aa7a330f63e177748033c809bc7a0720cc6c307beb5ceccf782f43b75",
  "main_sop_preserved": "M12.md",
  "calculation_contract": "maker-discovery-common-v1_WITH_FROZEN_M12_REVISION_3_PARAMETERS_AND_VALIDITY_OVERLAP",
  "input_contract": "m1223-public-raw-observation-r2/raw-path-r3",
  "direct_trades_only": true,
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": ["MAKER", "TAKER"],
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "round_window_ms": 500,
  "terminal_stable_ms": 100,
  "stable_recovery_min": "0.50",
  "bid_recovery_min": "0.50",
  "ask_recovery_max": "0.40",
  "ask_loss_fraction_min": "0.02",
  "material_hit_and_loss_min_shares": "0.5",
  "material_hit_and_loss_min_initial_fraction": "0.0001",
  "normalized_hit_ratio": ["0.10", "10"],
  "normalized_loss_ratio_filter_enabled": false,
  "normalized_loss_ratio": null,
  "loss_ratio_role": "DIAGNOSTIC_ONLY_NOT_SELECTION_OR_LIFETIME",
  "inherited_cross_side_bid_loss_floor_enabled": false,
  "pair_confirmation_gap_filter_enabled": false,
  "pair_confirmation_gap_max_ms": null,
  "pair_confirmation_gap_role": "DIAGNOSTIC_ONLY_NOT_SELECTION_OR_LIFETIME",
  "pair_gap_diagnostic_threshold_ms": 1500,
  "component_ttl_ms": 3000,
  "component_ttl_changed": false,
  "confirmation_lateness_max_ms": 50,
  "bid_guard_recovery_floor": "0.40",
  "bid_guard_failure_operator": "LT",
  "ask_guard_recovery_ceiling": "0.50",
  "ask_guard_failure_operator": "GE",
  "guard_starts": "EACH_COMPONENT_WINDOW_END_THROUGH_PUBLICATION_PAIRING_AND_WAIT",
  "continuous_guard_path_required": true,
  "candidate_requires_current": ["FROZEN_BID_SUPPORT", "FROZEN_WEAK_ASK"],
  "candidate_confirmation": "FIRST_COMPLETE_INPUT_BOUNDARY_WITH_BOTH_CURRENT_AND_HIT_COMPARABLE",
  "pair_order": "ASC_LATER_COMPONENT_KEY_THEN_ASK_KEY_THEN_BID_KEY",
  "root_policy": "EACH_ASK_COMPONENT_ONCE_NO_REBIND_OR_RENEWAL",
  "deadline_rule": "MIN_CB_PLUS_3000MS_CA_PLUS_3000MS",
  "invalidation_before_confirmation": true,
  "deadline_equality_ends": true,
  "extra_minimum_remaining_signal_lifetime_ms": null,
  "formation_and_guard_numeric_thresholds_changed": false,
  "logic_change": "REMOVE_INDEPENDENT_PAIR_GAP_LIMIT_KEEP_CURRENT_COMPONENT_OVERLAP",
  "revision_date": "2026-11-17",
  "signal_tests_run": false,
  "runtime_changed": false
}
```

## 1. 组合、来源与语意变化

`live(S_C,BID@revision3) ∥ live(W_C,ASK@revision3) + u可比 → E(P,FB,FA,deadline;mode)`。

S/W仍是COMMON严格受击轮的局部构件，不直接调用BA、AA或DF。两种顺序都允许：先有承接，后来弱供出现且承接仍在；或先有弱供，后来承接出现且弱供未修复。先后顺序只描述，不新增方向筛选，也不要求两轮500ms观察窗重叠。

直接基线文件由上面的全文hash固定；[上游冻结清单](../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json)保留第三轮参数、源码与输入政策。本版明确覆盖配对间隔，已由直接基线删除的损失比及派生Bid下限继续删除，其余按冻结来源及[COMMON](COMMON.md)计算。执行按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。不得把目录主版的750ms配对、Ask30%/5%或旧比值0.25–4作为默认值。

| 项目 | 直接基线SHOCK_COMPARABLE | 本版VALIDITY_OVERLAP |
|---|---|---|
| 跨侧确认间隔 | ≤1500ms，含等号 | 无独立硬上限，1500ms仅诊断 |
| 证据关系 | 近时可比受击下的恢复差异 | 两份已受击验证且当前有效的承接/弱供状态重叠 |
| 每侧原寿命 | 各自实际确认后3s | 不变，不按配对重计 |
| 连续路径、冲击可比、形成阈值 | 必须满足 | 全部保留 |
| 损失比及派生Bid下限 | 已删除 | 继续删除，不计为本次新增改动 |
| 组合剩余寿命 | 名义上1.5–3s，发布延迟另扣 | 只要求严格大于0，无额外信号余量下限 |

有效状态是沿冻结守卫持续维护的证据，不表示两侧每时刻都重新完成了受击验证。时间间隔更大时，跨侧冲击比只约束各自历史受击量级，不能解释为相同市场背景下的同期实验。M4要求Ask相对过去强参考转弱，本版不需要过去强Ask参考；维持M12家族分支，不新增M编号、不自动与M4合并。

## 2. 两侧独立形成与组件发布

只计同token直接SELL击原Bid P、直接BUY击原Ask A，不使用互补映射作为直接成交，不重复计事件。received用于统计窗，可得输入按`K=(available,input_seq,subindex)`排序。来源、成交前数量、同档路径、完整覆盖、新鲜度、generation、原始frame或因果顺序不可证明时按冻结合同UNKNOWN/UNAVAILABLE，不补零、不读未来输入。

每侧由首笔合法新受击固定500ms窗`[t_i,e_i)`，`e_i=t_i+500ms`，冻结原档及成交前Q0_i>0。同一token/side/price不另开重复观察、不滑窗挑选。原档不必后来仍最优，但必须持续存在；组合P<A，同token/generation。

计算`L_i=Q0_i−Qmin_i`、`m_i=max(0.5,0.0001Q0_i)`、`u_i=H_i/Q0_i`、`d_i=L_i/Q0_i`、`r_i=clip((Qend_i−Qmin_i)/L_i,0,1)`。H_B为P的直接SELL，H_A为A的直接BUY，两侧均H_i≥m_i且L_i≥m_i。

- Bid末100ms必须连续r≥0.50且封口通过；新低或低于50%重置保持，旧滞回held不能替代。
- Ask封口rA≤0.40且dA≥0.02，不额外要求稳定保持。不能以静态薄盘、零成交或零损失替代真实受击。
- 水位及输入覆盖完整，组件实际可得`e_i≤c_i≤e_i+50ms`。原档在形成中消失则失败；不能补回后复用旧轮。

组件发布前先核对从自身e_i起的全部已知数量/质量路径；曾失效再恢复也不发布。received仍在形成窗内的合法输入属于形成统计，不因为处理稍晚就错误套用轮后守卫；封窗后迟到沿冻结质量合同处理，不回填救活。

冻结`FB=QminB+0.40LB`、`FA=QminA+0.50LA`，分别从各自e_i开始维护。组件实际发布于cB/cA后，各自截止为`expiryB=cB+3s`、`expiryA=cA+3s`。expiry变量与损失比例dB/dA分开，不以窗口开始、配对或执行时间为起点。

## 3. 冲击可比与诊断条件

唯一跨侧幅度筛选仍为`0.10≤uA/uB≤10`，两端允许；各按自己的初始深度归一化，不要求两侧绝对成交量/深度相同。真实H/L材料性、Ask自身dA≥0.02均保留。

跨侧损失比dA/dB只诊断，不筛选、排序或退出。旧派生dB≥0.002或0.0125仍不得继承，Bid只保留自身材料性`dB≥mB/Q0B`；不能把删除损失比变成扩大一个有限区间。

确认间隔`gap_ms=1000*abs(cA−cB)`，记录`gap_le_1500ms=(gap_ms≤1500)`。1500仅是历史对照标签，没有准入、选根、寿命、退出或执行权限。也不以更小gap、较新组件或更长剩余寿命重排配对。

**删除必须覆盖全部配对消费路径**：预筛选、组件索引查找、等待缓存保留/清理、组合发布、引用结束传播及维护回调。未破界且未到原3s截止的组件不能在1.5s时被删掉或标不可配；也不能留下隐藏的1.5s“最低剩余寿命”要求。保留的原始数据新鲜度/来源要求不是这项可删除的gap条件。

gap派生标签缺失不单独否决已证明的信号；但cB/cA、确认键、连续路径或当前有效性本身不可证明时仍UNKNOWN，不能以“仅诊断”为由豁免必需时钟。诊断不要求新采集，不生成逐对或逐tick日志。

## 4. 有效期重叠、首次配对与原截止

每个完整输入边界先处理全部已知数量/质量失效、必要组件结束及到期，再生成可引用事实，最后尝试组合。候选必须同时满足：

1. B/A两份组件已经完整形成并实际可得，且从各自e_i至当前未结束、未中断必要守卫；同token/generation、原P/A存在且P<A。
2. 两侧u比合格；损失比和gap标签不参与判定。
3. 实际组合发布时刻c严格早于两份原截止：`c<deadline=min(expiryB,expiryA)`。

多个同时可配时按`(较晚组件键,Ask组件键,Bid组件键)`升序；组件键为`(confirmed_available,input_seq,subindex)`。每个Ask只发布一个root，绑定首个合法Bid，P/组件ID/FB/FA/c/deadline一并冻结。保留原观察器与一次性消费政策，不以补量、新好轮或执行结果替换已绑定组件、不重复发布。

c是首次两份事实可共同引用且完整原始frame已处理的实际边界，通常等于max(cB,cA)，若完整发布边界更晚则用实际c，不倒填。同frame里先成立后失效的组件不能发布，未知不能当成“暂未见反证”。只有一份组件时等待，等待不暂停原时钟；组件结束后不能保存为历史继续配对。

**不从组合确认再给3秒，也不额外延长较早组件。** 设`Δ=abs(cA−cB)`，`δ=c−max(cA,cB)≥0`，在两侧原3s期限下，名义剩余寿命为`deadline−c=3s−Δ−δ`。必须严格大于0；Δ<3s只是必要的派生边界，不能替代实际c、完整frame及连续守卫检查。不是把原1.5s配置简单改成“≤3s”。

因此可能新增`1.5s<Δ<3s`内且始终有效的路径，也可能在临近原截止时只剩很短寿命。没有信号层面的400ms或1.5s余量门槛。若执行配置要求额外余量，单列执行受限，不能删除已成立的信号或重写其确认时点。

## 5. 连续维护与失效

| 条件 | 组件发布前、等待配对、组合后均适用 | 等号/反证处理 |
|---|---|---|
| Bid数量与原档 | Qbid(P)≥FB，P正量且存在 | Qbid=FB允许，Qbid<FB立即结束B及依赖候选 |
| Ask数量与原档 | 0<Qask(A)<FA，A存在 | Qask=FA即结束；原A消失也结束，不能解释为继续追价机会 |
| 后续合格轮 | 无同P合格Bid严格恢复失败；无同A合格Ask严格稳定恢复≥0.50 | 任一反证先结束相关组件及组合；未达H/L材料性的轮不自动等于恢复失败，即时数量守卫仍生效 |
| 输入质量与身份 | 必要来源/覆盖/新鲜度、tick/generation、因果顺序持续可证明 | 未知、重建或质量失败按原合同结束，不用最后快照掩盖中途失效 |
| 原截止 | 当前时刻严格早于该组件expiry及组合deadline | 到期等号先失效，不允许同边界新确认或准入 |

组合后不要求Bid重新达到形成时的50%或Ask持续≤形成时40%；维持的是FB/FA及后续合格轮反证。不能把取消gap换成每次配对重新做100ms恢复验证。即使当前量重新变好，也不能复活已经结束的组件或旧root。

新完整组件按原形成及首选政策另行处理；既有root不换锚、不替换B/A、不修改F界、不续期。gap>1500ms、gap标签变化或某个仅诊断字段不可用，都不新增退出。失效通知所选执行方式处理未发意向和在途/未成交部分，保留真实暴露与原始发生顺序。

## 6. Maker/Taker执行独立列出

共同目标为信号已确认且当前有效时在固定P成交。Maker须P上被动挂价合法，以post-only等待；Taker只在P有真实可主动取得的对手量且信号仍有效时准入。原Bid正量/FB保留，正常未交叉同token盘口下与同P Taker买入无共同准入状态；记录执行限制，不删守卫、不换价来制造成交。

历史5USDC、时段、400ms发送余量、额度或等待上限由执行配置独立选择，本版不选取、不变更。剩余寿命小于所选执行余量时记录“已确认、执行不可准入”，不是信号不成立。新回执、切换方式或排队不会延长deadline，没有自动重发、补单、改价或平仓定义。

失效/到期先于同边界新准入；取消请求不等于取消完成，所有真实成交、部分成交、非精确P成交及顺序未知均保留，不以事后失效删去成交。执行成功不补充形成证据，也不证明结算优势。

## 7. 确认与失效冲突复核

以下是定义推导与后续核对要求，没有运行测试或模拟。

| 场景 | 本版判定 |
|---|---|
| cB=0、cA=2.2s，其他条件及全路径有效，c=2.2s | 可确认，deadline=3s、剩余0.8s；旧1.5s版拒绝，本版不从2.2s重计3s |
| cA=0、cB=2.2s，弱Ask一直未修复 | 同样可确认，旧Ask截止3s；先后仅诊断，没有强制Bid先行 |
| Δ=1.5s | 可继续核对，诊断gap_le_1500ms=true；这不是新的边界退出 |
| Δ=3s，或实际c=较早expiry | 没有当前交集，到期等号先失效，不能发布 |
| Δ=2.98s，但完整frame实际c已到较早expiry | 不发布；不能只看Δ<3s或回填较晚组件确认时刻 |
| Δ=2.9s、δ=0、其余全部有效 | 可确认但剩余0.1s；若执行配置要求400ms则执行受限，不重写信号 |
| 较早Bid在1.8s曾Q<FB，2.2s配对前又补回 | 已结束，拒绝；Ask曾Q≥FA后回薄也同理 |
| 组件自然存活至2.2s，但1.5s缓存清理已丢弃 | 消费路径仍残留旧门槛，不符合本版；不能把TTL保留写成延长原期限 |
| Bid终点r≥0.50、维持FB为40% | 终点至少高于FB 0.10LB，无确认即破界的公式冲突；e→c路径仍必审 |
| Ask终点r≤0.40、FA在50% | 终点低于FA至少0.10LA；恢复到FA等号即退出，取消gap不豁免 |
| 双侧u比恰0.10或10，d比超旧上限 | u端点可接受；d不否决，材料性/当前守卫/期限仍全部检查 |
| 旧组件已结束，后来新好组件或相同档位恢复 | 不复活旧ID；新组件按原规则处理，已消费Ask不能再次发布 |
| 同frame内对侧确认随后必要事实失效 | 最终不发布；不能先配对再忽略后行反证 |
| cB/cA、连续路径不可证明，只有“gap诊断缺失”标记 | 必要证据UNKNOWN，不得借诊断降级通过 |

与直接基线相比，只扩展可配对关系，保持同一首选顺序；更早消费Ask可能改变后来配对，不能推导全样本信号数必然单调增加，更不能把时间容差扩展直接算成信号翻倍。此前M12的开发/验收结果不改贴本版，本版未证明频次或结算概率提高。

## 8. 最小交接字段与交付范围

输出variant/version、全文/参数/basis/直接基线hash；token/generation、B/A与原档ID、固定P/A、Q0/H/L/u/d/r及严格保持证据引用；eB/eA/cB/cA、确认键、实际c、expiryB/expiryA/deadline、FB/FA、从e起的守卫与首个失效原因/阶段/可得键。只保存小型摘要与来源引用，不复制行情。

诊断包括gap_ms、gap_le_1500ms、先确认侧（BID_FIRST/ASK_FIRST/SAME_AVAILABLE，实际输入键另存）、发布时各组件年龄及剩余寿命、u比与仅诊断d比。统计原始间隔，不以received起点距离替代确认间隔；不增加按gap排序或选根的权限。必要时区分仅诊断导出缺失与时钟本身不可证。

执行模式、准入/受限、真实回执与成交归属单列。本版需显式选择，adapter=null不表示已支持；缺能力报告未适配，不静默回落到旧版。首JSON与正文共同为规格；原主SOP、直接基线、共用合同与历史结果保留，不改实现、测试配置、进程或小时任务。未计算交易数、胜率、PnL，未启动测试、回测或模拟。
