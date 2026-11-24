# M12 新分支：同击异损、买强卖弱

版本 `m12-shock-comparable-response-divergence-v1`，`SPEC_ONLY`。**相近时刻，两侧受到按各自初始深度归一化后可比的真实冲击，Bid恢复而Ask仍薄；在支撑与弱供仍有效期间，于固定Bid锚价成交。** 两侧损失大小不必相近，Maker/Taker独立列出。

本版以此前审查的 **M12 frequency-revision-3** 为数值基线，只取消跨侧损失幅度比的硬要求。保留 [M12主SOP](M12.md)，不改运行参数、原语、测试模块或其他策略。名称“同击”表示下述有界容差，并非两侧成交量相等。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M12",
  "variant_id": "M12_SHOCK_COMPARABLE",
  "version": "m12-shock-comparable-response-divergence-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_SUPPORT_WEAK_SUPPLY",
  "baseline_version": "m12-fixed-anchor-entry-v1/raw-path-r3",
  "baseline_parameter_revision": 3,
  "basis_file": "../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json",
  "basis_sha256": "03ce596aa7a330f63e177748033c809bc7a0720cc6c307beb5ceccf782f43b75",
  "main_sop_preserved": "M12.md",
  "calculation_contract": "maker-discovery-common-v1_WITH_FROZEN_M12_REVISION_3_PARAMETERS",
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
  "pair_confirmation_gap_max_ms": 1500,
  "component_ttl_ms": 3000,
  "confirmation_lateness_max_ms": 50,
  "bid_guard_recovery_floor": "0.40",
  "bid_guard_failure_operator": "LT",
  "ask_guard_recovery_ceiling": "0.50",
  "ask_guard_failure_operator": "GE",
  "guard_starts": "EACH_COMPONENT_WINDOW_END_THROUGH_PUBLICATION_PAIRING_AND_WAIT",
  "pair_order": "ASC_LATER_COMPONENT_KEY_THEN_ASK_KEY_THEN_BID_KEY",
  "root_policy": "EACH_ASK_COMPONENT_ONCE_NO_REBIND_OR_RENEWAL",
  "deadline_rule": "MIN_CB_PLUS_3000MS_CA_PLUS_3000MS",
  "numeric_change_from_selected_baseline": false,
  "logic_change": "REMOVE_NORMALIZED_LOSS_RATIO_GATE_AND_ITS_DERIVED_FLOOR_ONLY",
  "signal_tests_run": false
}
```

## 1. 组合、基线与增频假说

`live(S_C,BID@revision3) ∥ live(W_C,ASK@revision3) + 仅u可比 → E(P,G,d; mode)`。

原规则同时比较“输入冲击u”和“结果减损d”，可能排除同等量级冲击下Bid损失小、Ask损失大的非对称路径。本版只要求输入冲击可比，让损失差异成为观察结果；恢复差与当前数量守卫仍决定方向结构。

冻结 [基线清单](../signal_discovery/spec_archive/M9_M12_FREQUENCY_BASIS_20261116.json) 中的第三轮参数与源码。主SOP的Ask恢复30%、损失5%、配对750ms、比值0.25–4仍保留在原文件；**本版继承测试第三轮的40%、2%、1500ms、0.10–10**，这些放宽不是本次新增效果。相对第三轮只删除dA/dB的筛选；不是继续扩大比值范围或搜索参数。

S/W为COMMON严格受击轮构件，不直接调用BA、AA或DF。M4需要Ask过去强恢复再转弱，M12不需要历史强Ask参考；M19比较先后两次Ask轻攻更薄，本版比较近时两侧冲击；因此保留M12家族分支，未合并成M4/M19。

逻辑假说是输入量级可比时的两侧响应差异可能反映需求/供给状态。L2不证明参与者意图，也不证明该状态持续到二元结算；未测试，不宣称增加信号或提高结算概率已经实现。

## 2. 输入与两侧独立轮次

按冻结原始输入合同及 [COMMON](COMMON.md) 的来源、因果可得、覆盖、新鲜度、原始frame和质量门槛处理。只计同token直接SELL击Bid、直接BUY击Ask；不以互补映射成交代替，不重复计同一事件。来源、成交前Q0、同档深度路径、generation或计量单位不可证明时，相关证据为UNKNOWN/UNAVAILABLE。

每侧原档分别以首笔新受击开500ms接收窗 `[t_i,e_i)`；同一token/side/price只维护一个该轮观察，按冻结baseline的开轮/关闭和新成交gate，不滑窗挑最佳片段。锚从真实受击原价冻结，不要求后来仍是最优档，但全程必须存在；Bid锚P与Ask原价A满足P<A。

每轮冻结Q0_i>0，取窗口内Qmin_i和Qend_i、同档真实主动成交量H_i，计算：

`L_i=Q0_i−Qmin_i`；`m_i=max(0.5,0.0001Q0_i)`；`u_i=H_i/Q0_i`；`d_i=L_i/Q0_i`；`r_i=clip((Qend_i−Qmin_i)/L_i,0,1)`。

两侧都必须H_i≥m_i且L_i≥m_i，不能用零损失或无实际成交的轮配对。Bid末尾连续100ms恢复率≥0.50，期间新低或低于0.50重置保持，封口rB≥0.50；Ask要求rA≤0.40且dA≥0.02，不要求Ask稳定。不是M9旧滞回held算法。

两侧原档形成中消失则失败；不能随后补回重用旧轮。水位完整封口，可得c_i不晚于e_i+50ms，不读取未来事件也不回填已结束窗。沿冻结raw-path-r3严格保持与窗口终点交接规则，不将较晚可得但received在窗内的事件误套成轮后守卫。

## 3. 保留冲击可比，取消损失相近

唯一跨侧幅度筛选为 **`0.10 <= uA/uB <= 10`**，含两端点。这里各按自己的Q0归一化，是第三轮已采用的一数量级容差；不表示HA=HB，不表示BUY总量大于SELL总量，也不表示两侧深度相同。

`dA/dB`只输出诊断，**无最小或最大接受区间，不排序、不择根、不影响当前维护**。这是删除一个条件，不是把阈值改成更大的有限范围。计算仍保留dA、dB和两侧H/L，L2可见减量不全归因于成交，H/L可超过1。

必须同时取消旧损失比造成的隐含门槛：第三轮`dA>=0.02`与`dA/dB<=10`推导的`dB>=0.002`不再成立；主SOP推导的0.0125也不适用于本版。Bid仅保留自己的材料性损失下限`dB>=mB/Q0B`，Ask仍保留`dA>=max(0.02,mA/Q0A)`。不删除各侧m门槛，也不以“结果不要求相近”为由允许无损失轮。

消费端不得继承旧`comparable(u,d)`双检查、d比例配置默认值、预筛选缓存或由损失比推导的Bid下限。所有配对路径只对u作跨侧比值筛选；本版不改M4/M14/M19或共用原语的可比定义。

## 4. 组件确认、配对与固定期限

各侧组件仅在形成条件和从自身窗口终点起的守卫均通过时发布。`cB/cA`为实际组件可得时刻，各自到期`dB_time=cB+3s`、`dA_time=cA+3s`；这些时间变量与损失比例dB/dA不同。

每次完整输入边界先处理已知失效，再从当前有效组件中取同token/generation、P<A、确认间隔`abs(cA−cB)<=1500ms`且u可比的配对。多个同时成立按`(较晚组件键, Ask组件键, Bid组件键)`升序；单组件键为`(confirmed_available,input_seq,subindex)`。同一Ask只发布一个root，取首个符合条件的Bid；不得按最小dB或最大恢复差重排。

组合确认c为首个实际可发布的处理边界；通常等于max(cA,cB)，若原始frame尚未完成或事实稍后才可共同引用，记录实际可得c，不能回填更早时间。`deadline=min(cB+3s,cA+3s)`，c须严格早于deadline；不从配对时重新加3秒。单组件原失效已发生则永不再参与。

理论上两轮可同时形成，至少各500ms；组件确认间隔最多1.5s时，较晚组件刚可得的名义交集尚有1.5–3s，额外发布延迟只会缩短这一交集。无对侧轮、输入延迟或旧组件失效都不能靠延长TTL补足。

## 5. 当前数量守卫与失效

每轮自身终点e_i之后，持续检查其固定原档数量，覆盖组件发布前、等待配对、组合发布及等待成交，不只检查配对瞬间快照：

| 守卫 | 有效条件 | 首次触发后 |
|---|---|---|
| Bid底线`FB=QminB+0.40LB` | Qbid(P)≥FB，P存在 | Q<FB立即结束B及依赖信号，恢复后不复活；等于FB仍有效 |
| Ask上界`FA=QminA+0.50LA` | 0<Qask(A)<FA，原A存在 | Q≥FA立即结束A及依赖信号；等于FA已失效；原A消失同样结束 |
| 后续同原档合格轮反证 | 未见合格Bid严格恢复失败、未见合格Ask严格稳定恢复≥0.50 | 任一出现先结束相关组件，再考虑全新组件 |
| 质量与截止 | 同generation、输入完整可得、now<deadline | 缺口／重建／必要来源不可证明或到期结束，不倒填有效状态 |

从e_i到c_i的已处理数量路径曾破界，即使发布时又恢复也不得发布。窗内received的合法事件留在形成统计，跨界received事件才进入轮后路径；无法确认次序则UNKNOWN。后续未达到H/L材料门槛的轮不自行构成“恢复失败”，但不能屏蔽即时数量守卫。

后续好Bid轮不能替换本信号B、延长deadline；Ask达到FA后不能凭后来重新变薄复活，须有新的完整弱Ask轮和新的合法配对。损失比不再作为任何失效条件；既不在候选后补检，也不借其他泛化“冲击可比”回调重新加入。

## 6. Maker/Taker执行

共同语意为**信号有效期间在固定P成交**，按 [固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。Maker在P被动挂单等待；Taker持有待执行意向，只有P存在可主动取得的对手量且全部信号守卫仍有效时申请。任一失效/到期先于同边界新准入，执行回执不重置c或deadline。

本版保留P正Bid及FB。正常未交叉同token盘口中，二者与同P的Taker买入没有共同准入状态；报告执行受限，不能删FB、忽略原档消失或改价来制造成交。历史5USDC、市场时段、400ms发送余量和提交限制由执行配置另选，不作为信号形成门槛。

失效结束未发意向并通知处理在途/未成交部分。保留全部真实成交、实际价格和顺序不确定的暴露，不因撤单请求或事后失效删账；不自动切换方式、重发、续期或定义成交后平仓。

## 7. 确认／失效冲突核对

以下为公式和边界检查，不是回测或模拟：

| 检查点 | 核对结果 |
|---|---|
| Bid确认rB≥0.50，等待floor为0.40 | 窗口终点量至少比FB高0.10LB；无确认即失效的公式冲突，发布前仍须检查后续已知破界 |
| Ask确认rA≤0.40，退出在r=0.50对应的FA | 窗口终点量至少比FA低0.10LA；保持正量时可同时成立；达到FA的等号退出 |
| 删除损失比，却保留dA≥0.02 | 不冲突：前者比较两侧响应是否相近，后者保证Ask自身有材料性减损 |
| Bid损失很小 | 仍须LB≥max(0.5,0.0001Q0B)，不能以零损失换高频；不继承额外0.002下限 |
| 配对间隔恰1500ms | 可以配，但各自3s期限不变，任何已到期/破界组件不能配 |
| 取消d检查后早配到另一Bid | 沿同一最早键规则消费Ask，不回选旧版更晚的Bid；这会改变样本归属 |

定义算例：两侧Q0都为100、H都为20，所以uA/uB=1。Bid最低99、末量99.8，LB=1、rB=0.80；Ask最低80、末量84，LA=20、rA=0.20、dA=0.20。若完整路径、末100ms保持、来源与时序均合格，双方达到材料性门槛，FB=99.4、FA=90，发布量均守界。旧第三轮因`dA/dB=20>10`拒绝，本版允许配对。若Ask在发布前曾达到90，则本版仍拒绝，删除损失比不能掩盖供给已恢复。

放松d比例可增加**可配对的组件关系**，但最早配对和Ask一次性消耗可能改变后续选根，不能由集合放宽直接证明全样本信号频次单调增加。也不能据此推断胜率、PnL或二元结算优势。

必要输出：variant/version、冻结参数hash、两轮身份与证据范围、Q0/H/L/u/d/r、保持路径引用、u比和仅诊断d比、cA/cB/c/deadline、P/A/FB/FA、发布前及当前guard、根选择键、失效原因、执行模式与真实成交。仅保留小型摘要和证据引用，不产生逐tick日志。首个JSON与正文共同为定义，没有实现检测器/适配器，没有做测试。
