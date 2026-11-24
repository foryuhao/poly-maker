# M18 增频分支：加压不增损，恢复达标即可

2026-11-17，`m18-escalating-hit-stable-recovery-v1`，SPEC_ONLY。保留[M18原版](M18.md)，本版仅取消第二轮最终恢复率不得低于第一轮的比较。两轮各自严格稳定、加压、初量可比及绝对/相对损失不增均保留；Maker/Taker独立。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M18",
  "version": "m18-escalating-hit-stable-recovery-v1",
  "status": "SPEC_ONLY",
  "adapter": null,
  "family": "stress_response_amplitude",
  "direct_trades_only": true,
  "round_window_ms": 500,
  "stable_recovery_min": "0.50",
  "stable_continuous_hold_ms": 100,
  "reference_hit_shares_min": "5",
  "reference_hit_to_initial_depth_min": "0.10",
  "reference_loss_to_initial_depth_min": "0.05",
  "second_to_first_initial_depth_ratio": [
    "0.80",
    "1.25"
  ],
  "second_to_first_hit_ratio": [
    "2.00",
    "4.00"
  ],
  "second_to_first_absolute_loss_ratio_max": "1.00",
  "second_to_first_normalized_loss_ratio_max": "1.00",
  "second_final_recovery_at_least_first": false,
  "pair_confirmation_gap_max_ms": 2000,
  "pair_confirmation_deadline_exclusive": true,
  "terminal_anchor_quantity_after_all_round_trades_required": true,
  "terminal_quantity_source_strictly_after_max_trade_source": true,
  "first_round_floor_is_active_during_second_round": false,
  "post_confirmation_ttl_ms": 3000,
  "guard_closed_window_to_publication": true,
  "execution": "fixed-anchor-execution-v1",
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "observation_requirement": "DIRECT_RAW_TRADES_AND_ORDERED_ANCHOR_DEPTH_PATH",
  "next_round_gate": "RECEIVED_AND_INPUT_KEY_STRICTLY_AFTER_REFERENCE",
  "basis_version": "m18-escalating-hit-contained-loss-v1.1",
  "calculation_contract": "maker-discovery-common-v1",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "variant_id": "M18_STABLE_RECOVERY",
  "baseline_version": "m18-fixed-anchor-entry-v1",
  "baseline_definition_file": "M18.md",
  "baseline_definition_sha256": "cb0ec661e3a01760ac468b96295e716580620d9057dc00045c0cb6ac08b7db31",
  "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
  "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762",
  "main_sop_preserved": "M18.md",
  "research_family_id": "F_STRESS_TOLERANCE",
  "selection": "EXPLICIT_ONLY",
  "revision_date": "2026-11-17",
  "recovery_comparison_role": "DIAGNOSTIC_ONLY_NO_GATE_NO_EXIT",
  "tests_run": false
}
```

## 1. 交易语意与原语顺序

同Bid第二轮承受更大卖量，绝对及相对减损未扩大，第二轮达到严格稳定恢复；在新支撑及来源维护有效期间，于固定锚价成交。

**原语组合**：`hist(S_C,BID1) → 新S_C,BID2 ＋ 加压不增损`；不是AP_SELL或RF2的简单别名。

**阶段顺序**：R1提供材料足够的参考，之后首个新R2在c1+2s前完整确认；Q0比[0.80,1.25]、H比[2,4]，L2≤L1、d2≤d1；两轮各自严格稳定，r2与r1之差仅诊断。两轮严格稳定，各自末量有成交后的明确来源；不要求逐笔100ms复核。

确认过程完成后保持待执行状态；选择Maker或Taker都不跳过形成证据、不换锚、不延长原期限。本单成交不自动增加一次承接确认。

## 2. 输入与轮次

采用[COMMON.md](COMMON.md)的500ms严格Bid轮：同token、同generation、固定原价P，只计P上的直接SELL，统计窗口为received时钟的 `[start,end)`。第一轮从空闲时在事件前有效best_bid上的第一笔直接SELL开始，冻结P；两轮之间不换档。第二轮首笔须同时满足received严格晚于c1、完整输入键严格晚于第一轮事实确认键。不重叠，不复用成交；确认前已接收但后处理的SELL不算新R2，仍进入所属流量及质量/guard。迟到属于已关闭窗口时按COMMON标UNKNOWN。

每轮记录 `Q0,Qmin,Qend,H,L=Q0-Qmin,u=H/Q0,d=L/Q0,r=clip((Qend-Qmin)/L,0,1)`；两轮均须满足COMMON的实际成交与减损资格门槛，并在末尾连续100ms恢复>=0.50。

数量来源必须是明确携带P数量的price_change或连续健康流的完整book；保存原始事件键与source timestamp。Q0来源在首笔SELL之前，source也须严格早于首笔成交，期间更新链连续；全书Book时间戳或其他档位回调不能替代P数量来源。无法证明则标UNKNOWN，不倒推Q0。

**关闭时的数量复核**：每轮必须有一份位于该轮窗口内、明确携带P数量的事件，其输入键晚于该轮所有计入H的SELL，source timestamp严格大于这些成交source的最大值，received严格早于轮终点。该事件必须在实际关闭事实可得前已处理；关闭最多允许迟于终点50ms。否则标 `TERMINAL_ANCHOR_QUANTITY_UNAVAILABLE`，不能把成交量增加而书尚未更新误判为减损受控。

这是对轮末状态的来源核对，不是一一对应的成交回执。它**不等于M17逐笔100ms复核**，也不要求M16的跨越区间与时钟量化能力。中间全部已观察低谷仍计入Qmin，末量复核不能掩盖中途损失；完整路径或直接成交去重来源缺失时不能从摘要补造。详见[输入能力说明](INPUT_CAPABILITIES.md)。

## 3. 参考与加压条件

R1须严格稳定，并满足 `H1>=max(5,0.10*Q01)`、`d1>=0.05`。这给出有实际卖出和可见减损的参考，避免仅用极微小减量做分母。R1关闭于c1后冻结参考，等待其后第一轮R2，截止 `c1+2000ms`；R2失败不能跳过重选第三轮。

R2关闭时须同时满足：

1. `0.80<=Q02/Q01<=1.25`：初始队列量级接近，排除只因队列突然大很多而容易承接的比较。
2. `2<=H2/H1<=4`：同长度窗口内实际卖量明显加大，上限限制跨量级比较。
3. `L2<=L1` 且 `d2<=d1`：绝对损失和相对初量的损失均未扩大。只守其中一项会受到Q0变化影响。
4. R2合格、严格稳定，即末100ms持续恢复≥0.50、关闭r2≥0.50；**不要求r2≥r1**。R1仍须独立严格稳定。恢复差`r2−r1`及比值`r2/r1`仅诊断，不筛选、不排序、不触发退出。因为R1恢复≥0.50，已合格配对的比值分母非零；仅诊断缺字段为null，必需恢复路径不可证仍UNKNOWN。
5. `c2<c1+2000ms`，原档从R1开启至c2始终存在且质量有效。

R1只作历史响应参考；**R2形成期间不维持R1的40%数量底线**。同档新的受击可以暂时跌破旧floor，但不能丢失原档；确认后只使用已完成R2的新floor。若要求旧floor全程有效，应另立版本，不与本版混池。

## 4. 可行区间与非独立的派生量

给定Q02和R1，R2允许的损失区间是：

`max(0.5,0.0001*Q02) <= L2 <= min(L1,d1*Q02)`。

区间为空则本次不能形成，记录 `LOSS_INTERVAL_EMPTY`；不临时降低最少受击量。H2范围为 `[2H1,4H1]`，还须实际达到COMMON资格。本版未把旧R1 floor施加到R2，所以没有M13那种“真实复验空间被旧底线压缩”的同一约束；原档存在和恢复条件仍不可少。

可诊断 `k=L/H`，称为“可见减损/成交量比”。因H2>=2H1且L2<=L1，已有条件直接推出 `k2/k1<=0.50`；因Q02/Q01<=1.25，还可推出 `u2/u1>=1.60`。这些是已有门槛的算术结果，**不是另外两条独立确认**，不重复加分。

L是窗口内相对初量的最大可见减损，H是累计成交，两者不是同一种流量。挂入、撤回、成交与消息先后都会影响结果，不能以L/H推断某笔卖出造成多少损失或净新增多少买单。相同端点下L/H改善也可能是撤单减少，而非买方更知情；这应保留为反例。

## 5. 确认与交接

所有条件可得后，先处理当前输入带来的失效，再确认 `c=c2`，`deadline=c2+3000ms`，root为R1.id，锚=P。两轮理论最短约1秒，轮间空档及可用延迟另计；2秒是R1之后的形成截止，3秒是R2确认后的最长寿命。

R2新底线固定为 `F2=Qmin2+0.40*L2`。received>=R2窗口终点、且已处理到实际发布边界的中间状态，若曾跌破F2、原档消失或质量失效，则不发布，即使当前量已回补。F2不约束仍属R2形成窗的晚处理事件，也不允许在c2前提前下单。R1仍只是历史参考，不增加其旧floor。详见[交接审查](CAUSAL_HANDOFF_REVIEW.md)。

## 6. 等待期间的失效

- 任一已观察 `Qbid(P)<F2`、原档消失、tick/generation变化、质量未知或原截止到期，立即结束并通知执行端处理未成交部分。
- 确认后同档后续合格Bid轮若不严格稳定，按COMMON结束支撑；观察这些轮时也须保留数量来源。后续已开启轮若无法按第2节完成必要的轮末数量复核，则标UNKNOWN结束，不能拿不完整摘要默认支撑继续。
- 不要求每一轮卖量继续翻倍，也不因卖压暂时减少就认定失效；更大冲击下的响应只作为已经形成的历史证据，当前等待由固定floor、后续承接和短期限约束。不从新的H或k值重置时钟。
- 成交可能发生在后续轮关闭前，不能预先知道它会否恢复。所有取消在途成交进入完整经济账，另标信号失效前后，不能事后删除坏成交。

第一参考尚未形成时不下单。每token同时最多一组形成/等待状态，首个R2失败、到期或数据不足即结束root；R1/R2使用过后不重新配对。结束后的全新SELL才能启动新R1，旧轮不挪到更有利的配对；质量重建先换generation。新root不能重置每市场的一次提交限制。

## 7. 与原版的差异、确认及失效复核

本版将“更大压力下损失不增，最终恢复也不浅于前轮”简化为“更大压力下损失不增，且本轮恢复仍达到稳定标准”。例如r1=0.80、r2=0.60，两轮末100ms均通过且其他比较/来源均成立时允许；r2=0.49或末100ms中断仍失败。它弱化了相对恢复深度证据，不是原版等价改名。

唯一删除项必须在预筛选、最终配对、发布和失效回调中同时移除。确认后仍守F2及后续轮COMMON严格稳定，不再将r2/r1当维护条件；不新增相对r2的退出。确认终点至少高于F2的0.10L2，e2到c的中间破界仍先失效。c2=c1+2s已过形成期限；原3s不从提交或新好轮重计。没有删第一参考、压力倍增、初量可比或双损失比较。

**增频限制**：本轮读到的历史阶段反馈主要还未进入最终配对审查；因此删除r比较只能扩大可证明配对的接受范围，不能补齐来源或恢复时间链，也不能声称已经解决实际零信号。原阶段反馈不代替本分支结果。本分支是后备文档入口，不安排继续轮流放宽其他参数；同类输入阻断没有改善证据时，不以本分支名义反复调参。

## 8. 执行与归属

交易语意为信号有效期间在固定P成交，按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。Maker合法post-only等待；Taker仅在当前信号有效且P有实际可主动取得的对手量时准入。原Bid正量/F2在正常未交叉同token盘口下与同价Taker买入没有共同准入状态，不删守卫、不换锚。预算、市场窗口、发送余量另选，不反向当形成条件。

到期/硬失效先于新准入；结束意向并处理未成交部分，在途成交、实际价格和顺序未知均保留。自有成交不增加承接证据，不自动切执行方式、续期或定义平仓。当前来源与真实执行限制沿原合同；原版、其他调参版和本版各自识别，不合池。

输出策略/分支版本、基线hash、两轮身份和数量来源、Q0/H/L/u/d/r、仅诊断恢复差/比、c1/c2/e2/deadline/F2、失效原因与执行模式；只用摘要及证据引用。本任务未启动信号测试、回测或模拟，未修改实现或进程，未证明增频或结算优势。
