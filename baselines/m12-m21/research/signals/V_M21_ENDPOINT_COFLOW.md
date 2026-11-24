# M21 增频分支：受卖补回，供给净退

版本 `m21-endpoint-recovery-withdrawal-v1`，`SPEC_ONLY`。**同一受卖窗口已有真实Bid补回、原Ask在零BUY证据下净减；两侧数量守卫仍有效时，在固定Bid锚价成交。** Maker/Taker独立列出。直接基线为[V_M21_COFLOW_NO_PREQUIET](V_M21_COFLOW_NO_PREQUIET.md)，原[M21](M21.md)、[单轮版](V_M21_SINGLE_ROUND.md)及此前分支保留，不自动替换运行版本。

本次仅落实两项授权：双侧末100ms保持改为诊断；首轮Bid受卖占初量10%、损失占初量5%改为诊断。当前恢复与净减、真实数量来源、零BUY及F/U不降级。语意由“双方都已稳定”改为“双方已在封口时成立，随后连续维护”，不称为COMMON严格稳定S。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M21",
  "variant_id": "M21_ENDPOINT_COFLOW",
  "version": "m21-endpoint-recovery-withdrawal-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_WITHDRAWAL_SUPPORT",
  "baseline_version": "m21-single-joint-withdrawal-no-prequiet-v1",
  "baseline_definition_file": "V_M21_COFLOW_NO_PREQUIET.md",
  "baseline_definition_sha256": "0d4adbc48d17efd5595902d460104d48c80ac53e272adde8a32b2bfb9d8e115a",
  "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
  "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762",
  "main_sop_preserved": "M21.md",
  "calculation_contract": "COMMON_WITH_EXPLICIT_M21_ENDPOINT_AND_INITIAL_MATERIALITY_OVERRIDES",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": ["MAKER", "TAKER"],
  "input_policy": "SAME_TOKEN_DIRECT_TRADES_ONLY",
  "rounds_required": 1,
  "round_window_ms": 500,
  "bid_min_hit_shares": "5",
  "bid_min_loss_shares": "0.5",
  "bid_min_hit_fraction": null,
  "bid_min_loss_fraction": null,
  "bid_endpoint_recovery_min": "0.50",
  "bid_terminal_hold_ms": null,
  "ask_min_net_loss_shares": "0.5",
  "ask_min_net_loss_fraction": "0.10",
  "ask_terminal_hold_ms": null,
  "ask_prequiet_required": false,
  "diagnostic_terminal_hold_ms": 100,
  "diagnostic_bid_hit_fraction_min": "0.10",
  "diagnostic_bid_loss_fraction_min": "0.05",
  "diagnostic_ask_history_ms": 250,
  "diagnostic_history_required_for_signal": false,
  "diagnostic_missing_invalidates": false,
  "formation_direct_buy_at_ask_max_shares": "0",
  "zero_buy_until": "ACTUAL_COMPLETE_BATCH_PUBLICATION_INCLUSIVE",
  "bid_guard_recovery_floor": "0.40",
  "bid_guard_failure_operator": "LT",
  "ask_retained_net_loss_fraction": "0.50",
  "ask_guard_failure_operator": "GT",
  "guards_from": "ROUND_WINDOW_END_THROUGH_PUBLICATION_AND_WAIT",
  "followup_bid_round_materiality": "COMMON_MAX_0_5_OR_0_0001_Q0_FOR_H_AND_L",
  "followup_bid_round_recovery_min": "0.50",
  "followup_bid_round_terminal_hold_required": false,
  "terminal_bid_and_ask_sources_after_all_sell_inside_window": true,
  "direct_buy_presence_before_lossy_normalization": true,
  "publication_requires_complete_original_batch": true,
  "confirmation_lateness_max_ms": 50,
  "post_confirmation_ttl_ms": 3000,
  "deadline_rule": "FIRST_JOINT_ROUND_CONFIRMATION_PLUS_3000MS",
  "second_round_required": false,
  "cross_round_ratio_filters": null,
  "second_round_formation_deadline_ms": null,
  "new_direct_buy_after_confirmation_invalidates": false,
  "post_confirmation_buy_gate": "RECEIVED_AND_INPUT_KEY_STRICTLY_AFTER_CONFIRMATION",
  "late_formation_buy_action": "END_ON_DISCOVERY_KEEP_ALL_FILLS",
  "root_policy": "ONE_PER_TOKEN_FIRST_ELIGIBLE_SELL_FIXED_WINDOW_NO_REUSE_NO_RENEWAL",
  "revision_date": "2026-11-17",
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "tests_run": false
}
```

## 1. 原语组合、阅读顺序与输入

`同一SELL轮 [Bid真实受卖后封口补回 ∥ 原Ask无BUY新净减] → E(P,F,U,d; mode)`。这是M21局部单轮构件，不输出COMMON严格稳定S、两轮RF、BA或P_XW。没有第二轮、跨轮比较、旧2秒计时或额外前置平稳要求。

本SOP明确覆盖项优先，其余读直接基线、其冻结basis及[COMMON](COMMON.md)。COMMON只继承未覆盖的计算、数据质量和时钟规则，不重新套首轮比例或100ms稳定布尔值。固定锚价执行按[共同合同](FIXED_ANCHOR_CONTRACT.md)。本文件首JSON与正文共同定义，未支持应报告未适配，不回退到旧版。

仅同token去重直接成交。received决定统计窗，available与输入键决定实际可得顺序；覆盖、序列、source/book新鲜度、generation、tick、乱序、交叉盘按原合同。缺失不等于零，未来量不倒推过去；完整原始批次后才发布。价格只识别档位和执行合法性。

## 2. 起点与固定观察

空闲时首笔满足必要来源和质量的直接SELL冻结此前best_bid P、best_ask A、Q0/a0、generation、事件身份和t0=received，要求P<A、两档正量。初量各自来自首笔前明确报量：输入键更早、received不晚于首笔、source严格更早，至首笔的更新链连续。BBO、全书时间戳、别档刷新和成交后量不能替代。

每token最多一个形成或等待root，固定`[t0,e)`，`e=t0+500ms`。P/A始终存在，不要求后来一直最优，不换锚、不滑窗。a0取首SELL前实测值，不取过去更高量；本窗必须有新增净减。

前250ms Ask平稳只作可选诊断。已有完整额外历史时记相对a0最大偏离；缺失记null/UNAVAILABLE，不填零、不等补齐。必要当前初量、连续链及COMMON质量覆盖仍必需。

## 3. 单轮确认与诊断分离

Bid统计Qmin、Qend、直接SELL量H、`L=Q0−Qmin`、`u=H/Q0`、`d=L/Q0`、`r=clip((Qend−Qmin)/L,0,1)`。

**首轮全部材料性条件为H≥5股且L≥0.5股**，封口r≥0.50。首轮不另用COMMON合格布尔值加入相对初量下限；零冲击、静态厚墙或单纯新增挂量仍不合格。新低改变本轮Qmin/L及最终恢复分母，不需要重新完成100ms保持。

Ask冻结`g=max(0.5,0.10a0)`；要求封口`0<aend≤a0−g`、`D=a0−aend≥g`。g≥a0时无正量解，结束root。D是相对本轮初量的净减，不累加所有负跳变，不用此前已薄的Ask代替本轮响应。

以下只描述，不预筛、延迟确认、取消候选、排序择根或触发退出：

- `u>=0.10`、`d>=0.05`的数值与布尔标签。
- 末100ms内Bid按当时已知最低量连续恢复≥0.50、Ask连续保持`0<Qask≤a0−g`的各自标签。新低按旧诊断口径重置稳定段；不能用旧held冒充真实连续保持。
- 可选前250ms平稳标签。诊断缺失记null，不能把必需数量路径缺失伪装为可选诊断缺失。

原A直接BUY从t0至实际发布c（含边界）必须为零，包括e→c及发布原批次后行。必须在原始直接来源被有损方向/镜像归一化过滤前或等价完整集合核对。真实BUY为反证，无法证明完整性为UNKNOWN；深度覆盖或过滤后的空集不证明零BUY。这里只证明净减，不能识别逐单撤单、同一参与者或SELL导致撤供。

P/A各有明确末量报量：输入键晚于本窗全部计量SELL，source严格晚于它们最大source，received在`[t0,e)`内，关闭时已可得。两档可分别报量；不要求报量后再稳定100ms。不能拿窗外量、BBO、别档或重建快照补足。形成失败结束，不挑后续好轮救活。

## 4. 发布、当前守卫及后续轮

`IDLE → JOINT_ROUND → CONFIRMED → END`。完整水位与原批次全部处理后，在首个合法边界检查，`e≤c≤e+50ms`；超过50ms为UNKNOWN，不延后补发。已知失效及到期先于发布。

冻结`F=Qmin+0.40L`、`U=a0−0.50D`。e至c的全部已知路径及当前状态须满足`Qbid(P)≥F`、`0<Qask(A)≤U`、原档及质量、零BUY；中途破界再恢复仍拒绝。按时窗内received输入用于形成统计，轮后输入用于guard；封窗后迟到按质量合同，不能靠重划阶段补认。

通过后`deadline=c+3000ms`。等待时任一Bid<F、Ask>U、原档消失、generation/tick变化、质量未知或now≥deadline结束。**Bid=F、Ask=U允许**。F/U全程固定，新补回、更多撤供、回执或重复响应均不替换、不续期。

后续同P新SELL轮仍按COMMON的500ms轮及基础材料性`m=max(0.5,0.0001Q0_new)`观察，H/L均≥m才合格；这不是首轮已删除的10%/5%强度过滤。**合格后封口r<0.50才按恢复失败结束，末100ms保持仅诊断。** 100ms不在后续失效、COMMON stable传播或组件结束回调中复活；共享COMMON本身不修改，本分支须按原因消费，不能忽略所有HARD_INVALID。真实数量、质量、封口超时反证仍结束。后续未合格轮不单独证明补回失败，但F/U实时检查不豁免。首轮不重复当作后续轮复核；新好轮不换F/U或期限。

没有Ask RF退出，不要求后续每笔SELL再次引起Ask净减。真正c后新BUY须同时received>c、输入键>确认键；本身不结束，仍守A和U。c后才发现首次直接接收属于本root且received≤c的BUY，属于形成反证，发现时结束并保留全部成交，不倒填取消；等于c不能靠行序改称新阶段。历史重发按可证身份处理，归属不明为UNKNOWN。

结束后只有received和输入键均严格晚于结束的新SELL可重新检查起点；旧窗口、成交及root不复用。执行额度不随新root自动重置。

## 5. Maker/Taker独立执行

共同目标是在信号有效期间于固定P成交。Maker按真实post-only准入后等待；Taker只有P存在可主动取得的真实对手量且信号有效时准入。当前Bid正量/F保留，正常未交叉同token盘口下同价Taker买入没有共同准入状态，记录限制，不删守卫或改价制造成交。

400ms发送余量、金额、队列、市场时段和提交次数是独立执行配置，不影响信号确认。失效先于同边界新准入，通知执行端处理未成交部分；撤单请求不等于取消完成。全部真实成交及在途不确定性保留，不自动切换方式、重发、续期或定义成交后平仓。自有订单不增加形成证据。

## 6. 确认／失效逻辑核对（仅定义算例，未运行）

| 场景 | 本版处理 |
|---|---|
| Q0=1000，H=5，L=1，Qend=999.6 | H/L绝对门槛、r=0.60通过；0.5%/0.1%仅诊断。F=999.4，仍需Ask、来源及完整路径 |
| 双侧只在窗口最后20ms达到r≥0.50、D≥g | 可确认，两个100ms标签为false；不另加100ms延迟，e→c守卫仍完整 |
| Bid封口r=0.50，Ask封口aend=a0−D | Bid比F高0.10L；Ask比U低0.50D，确认与当前数量界有交集 |
| 终点数量合格，但末量不在最后SELL之后或来源不可证 | UNKNOWN，不能把来源检查随稳定诊断一起删除 |
| e→c曾Bid<F或Ask>U，发布时恢复 | 不确认，不能只看发布快照 |
| 新后续轮材料合格、封口r≥0.50但未稳定100ms | 不仅因此退出；原F/U、质量、截止继续维护 |
| 新后续轮合格但r<0.50 | 结束；不等未来补回，不因诊断化忽略真正恢复失败 |
| 发布批次后行有BUY@A／c后真正新BUY@A | 前者反证形成；后者仅依原档、U等当前guard判断 |
| c=e+50ms／now=deadline | 前者可通过；后者到期先处理 |

最少输出variant/version/hash、root/事件引用、P/A、Q0/H/L/u/d/r、a0/g/D/F/U、t0/e/c/deadline、原始零BUY与末量来源、诊断标签及可得性、失效原因/阶段、执行归属。只保留小型摘要与引用，不新增逐tick日志。

本版接受刚补回、刚退量及相对初量较小的冲击；不再声称双侧已稳定100ms。与M6同属撤供留托家族，但仍要求同一窗口的单档共同证据，不改成任意旧S与LW相遇。首选根和占用可能改变全局数量；未测试、未证明增频或结算概率提高。
