# M23 增频分支：托在再验，首卖750ms仅诊断

版本 `m23-buffer-first-sell-while-valid-v1`，`SPEC_ONLY`。**预加托留存后，只要前置底线持续有效，首笔新SELL可在原种子期限内到来；完整受卖验证证明缓冲仍在，才确认在固定Bid锚价成交。** 本版只把独立750ms首SELL限制降为诊断，保留100ms种子、500ms验证、原种子3s总期限及连续数量守卫。Maker/Taker单列。

直接基线[V_M23_BUFFER_NO_LOSS_COVER.md](V_M23_BUFFER_NO_LOSS_COVER.md)、[先前缓冲版](V_M23_BUFFER_RETENTION.md)与[M23原主版](M23.md)完整保留，不切换运行实现。先扩大机制成立且可证明的信号接受范围，为用户模块取得可评价样本；本次不提前加入新的价格带、等待时长或执行余量筛选，不声称已经实测增频。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M23",
  "variant_id": "M23_FIRST_SELL_WHILE_VALID",
  "version": "m23-buffer-first-sell-while-valid-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_PRELOADED_BUFFER",
  "baseline_version": "m23-buffer-retention-no-loss-cover-v1",
  "basis_file": "../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json",
  "basis_sha256": "510f0315d67092a6e42d624def47f5df7aedb7a8fa04706fbe097068a231a762",
  "main_sop_preserved": "M23.md",
  "calculation_contract": "maker-discovery-common-v1_WITH_M23_BUFFER_OVERRIDES",
  "common_overrides": [
    "NO_RF_ROUND_OR_MINIMUM_LOSS_GATE",
    "BUFFER_FLOOR_NOT_RF_FLOOR",
    "NO_RF_STABILITY_EXIT"
  ],
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "preload_min_net_add_shares": "5",
  "preload_min_net_add_fraction": "0.20",
  "preload_hold_ms": 100,
  "preload_retained_add_fraction_min": "0.80",
  "pre_seed_sell_presence_before_lossy_normalization": true,
  "seed_confirmation_lateness_max_ms": 50,
  "first_sell_after_seed_filter_enabled": false,
  "first_sell_after_seed_max_ms": null,
  "first_sell_diagnostic_threshold_ms": 750,
  "first_sell_delay_role": "DIAGNOSTIC_ONLY_NO_GATE_SELECTION_OR_EXIT",
  "first_sell_diagnostic_operator": "BOTH_RECEIVED_AND_AVAILABLE_STRICT_LT_750MS",
  "first_sell_gate": "RECEIVED_AND_KEY_AFTER_SEED_AND_RECEIVED_AVAILABLE_BEFORE_SEED_HARD_DEADLINE",
  "first_sell_failure_policy": "END_DO_NOT_SKIP_OR_RESTART_WINDOW",
  "test_window_ms": 500,
  "test_min_sell_shares": "5",
  "test_min_sell_to_preload": "0.50",
  "test_min_depth_loss_to_preload": null,
  "visible_loss_zero_allowed": true,
  "test_sell_covers_depth_loss": false,
  "visible_loss_definition": "MAX_ZERO_Q0R_MINUS_MIN_OBSERVED_QUANTITY",
  "retained_preload_fraction_min": "0.20",
  "test_terminal_preload_fraction_max": null,
  "test_terminal_hold_ms": 100,
  "test_terminal_hold_role": "WITHIN_500MS_WINDOW_ALREADY_COVERED_BY_CONTINUOUS_F_NOT_EXTRA_DELAY",
  "buffer_floor_from": "LAWFUL_FIRST_NEW_SELL_HANDOFF_THROUGH_END",
  "explicit_terminal_P_quantity_after_all_test_sell_inside_window": true,
  "publication_requires_complete_original_batch": true,
  "test_confirmation_lateness_max_ms": 50,
  "seed_hard_ttl_ms": 3000,
  "deadline_rule": "SEED_CONFIRMATION_PLUS_3000MS_NO_RENEWAL",
  "extra_minimum_remaining_signal_lifetime_ms": null,
  "original_level_loss_invalidates": true,
  "root_policy": "ONE_PER_TOKEN_FROZEN_PRELOAD_FIRST_NEW_SELL_NO_REUSE",
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "tests_run": false,
  "baseline_definition_file": "V_M23_BUFFER_NO_LOSS_COVER.md",
  "baseline_definition_sha256": "3f85aba4805729bdc64570777b9729446c18161b7a850c2da6d39b5df4443944",
  "revision_date": "2026-11-17",
  "loss_to_hit_ratio_role": "DIAGNOSTIC_ONLY_NO_GATE_NO_EXIT",
  "diagnostic_missing_invalidates": false,
  "logic_change": "REMOVE_INDEPENDENT_750MS_FIRST_SELL_LIMIT_KEEP_SEED_DEADLINE_AND_CONTINUOUS_GUARDS",
  "formation_and_quantity_thresholds_changed": false,
  "seed_ttl_changed": false,
  "runtime_changed": false
}
```

## 1. 组合与改动边界

`预加托留存 → 前置底线仍有效时首个新SELL合法交接 → 完整500ms受卖及缓冲留存 → E(P,F,d; mode)`。加托不是BA/RF，真实H证明发生受卖，不由可见损失推断成交。仍是先加托后受卖，不能任意调换顺序，也不以种子或首笔SELL本身授权成交。

先前缓冲版已删除原版的`L≥0.50Δ`与末100ms`Q≤B+0.50Δ`，直接基线进一步删除H≥L。本版继承这些删除，不重复计作新增改动。真实挑战仍由H材料性证明，缓冲是否保留由固定F证明；L/H和H−L仅诊断，没有RF最少损失、恢复率或末段上限。

本次唯一放宽是：首SELL不必在种子确认后750ms内到来，750ms只作历史对照诊断。种子100ms、80%底线、验证500ms、20%缓冲底线、真实H下限、原种子3s以及所有来源/原档规则保留。末100ms是500ms窗内末段，由全验证期同一F覆盖，不再追加100ms等待；这是直接基线已有含义，不是另一次提速。保留旧版与历史XR备选身份，不默认调用XR或QR。

直接基线由首JSON中的全文hash固定；[上游清单](../signal_discovery/spec_archive/M7_M23_FREQUENCY_IMPLEMENTATION_BASIS_20261117.json)保留此前改动的来源身份，本版不改该快照。未覆盖项沿直接基线与[COMMON](COMMON.md)计算，按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)分离执行。本版由“加托后迅速受挑战”变为“加托持续有效期间受挑战”，不声称原订单或同一参与者始终留在档位。

## 2. 预加托种子

空闲时，首个合格的明确原Bid报量更新在更新前best_bid P形成增量。P事前已存在，原量B>0，新量B+Δ，`Δ≥max(5,0.20B)`。新出现的更高档不作种子；price_change.size是新总量，必须用明确前后量相减。

前量来自连续、可追溯的同档数量链，后量由本事件明确报出。冻结P/B/Δ/generation、原始批次与事件键、两份数量来源，t0为增量事件received。后续增量不扩大Δ、不移动B、不重开计时。

从t0至合法首笔验证SELL前始终守`Fpre=B+0.80Δ`、原P正量与质量。种子窗`[t0,t0+100ms)`；水位与当前完整批次处理完成后一次检查发布，`t0+100ms≤cS≤t0+150ms`，不等后来回补择时发布。窗口终点到发布路径中曾破Fpre也失败。

从t0至cS（含边界），P上首次直接接收归属该区间的SELL必须为零，在镜像/方向过滤丢失前核对。与t0同接收时刻、同批较前行的SELL同样核对；已证实原始重发按首次接收归属，未知归属为UNKNOWN。晚发现此区间SELL在发现时结束，不倒删历史成交。不能仅凭深度覆盖证明零成交。

种子不授权成交，状态`PRELOAD → WAIT_FIRST_SELL`；同token最多一个root。

## 3. 首笔新SELL与底线交接

种子发布时固定`D=cS+3s`，在WAIT_FIRST_SELL阶段持续维护Fpre、原P和质量。**cS+750ms不触发结束、清缓存、解除root占用或降低底线。** 没有首SELL时仍只能等到原D；必要守卫先失败则先结束，补量或新加托不延长D。

cS后P上的首笔可信直接SELL须received>cS、输入键>种子确认键，且received和available均严格早于D。首笔不满足来源、阶段或原截止即结束，不跳过它挑下一笔；received=cS不能靠行序改称新阶段。750ms以内、恰好750ms或超过750ms不再影响合法性，仍检查全部因果及当前守卫。

处理这笔SELL前Fpre必须一直有效；合法性成立后才切换为`F=B+0.20Δ`。此后直到root结束都守F。若深度先已破Fpre、SELL随后可得，不能用低F或事后source排序救回。无法证明输入顺序时UNKNOWN，同边界先处理已发生的失效再交接。

首SELL前冻结Q0R：明确P数量来源输入键更早、received不晚于SELL、source严格更早，连续链有效。不用B或B+Δ替代缺失的首SELL前量。令tR=首SELL.received、aR=首SELL.available，验证窗`[tR,e)`，e=tR+500ms。首笔即使很小也固定本窗；H即使提前达标仍需等完整500ms，不改为“第一笔材料性SELL”或移窗等更好的挑战。第一窗不合格/来源未知/守卫失败即结束，不重新挑首SELL。

首SELL到来时种子尚有效，不等于有足够时间确认。若e≥D，完整窗口不可能在原D前发布，不能缩窗、借未来报量或从tR重计3s；root最迟在D结束。没有首SELL时不额外添加2.45s、2.5s或预扣50ms/400ms的提前清理门槛。

只记录`first_sell_wait_received_ms=1000*(tR−cS)`、`first_sell_wait_available_ms=1000*(aR−cS)`，以及`first_sell_within_750ms=(两者均严格<750)`。等于750时诊断为false，但不否决。仅诊断导出缺失可null；必要cS/received/available/输入键不可证仍UNKNOWN，不能借“诊断化”豁免时钟或来源证明。

## 4. 真实受卖与缓冲留存

统计窗口内P上的去重直接SELL累计H，以及明确P数量事件中的最低量。可见损失定义`L=max(0,Q0R−Qmin_observed)`；没有明确数量路径为UNKNOWN，不能将缺失视为L=0。

完整窗口要求：

- `H≥max(5,0.50Δ)`；H与L不作大小门槛比较。
- 从合法首SELL交接起，全部已知路径`Q(P)≥F`，原档、generation与质量持续有效；末100ms同样守此下限。
- **没有最小L，没有数量上限，没有RF合格/稳定要求。** 即使全窗量不低于Q0R、L=0，也可继续检查其他条件。

窗口内必须有明确P数量事件，输入键晚于全部计量SELL，source严格晚于这些SELL最大source，received仍在`[tR,e)`内，关闭时已可得。其他档/BBO刷新不能代替；缺少末端证据不允许以“数量看起来还厚”补认。保持原窗内来源合同，不增加或删除逐笔100ms复核。

原始price_change按输入数组顺序检查中间状态；完整健康book视为一份状态，不把内部清空/赋值当消失。重建generation结束旧root。批次末态不能覆盖中途F跌破，原序不明为UNKNOWN。

在形成确认边界可记录`loss_to_hit=L/H`和`observed_sell_minus_loss=H−L`，因为通过材料性后H>0；仅派生诊断无法计算时记null，不因诊断缺字段取消已可证明的信号。必需H或数量路径缺失仍UNKNOWN。L>H允许，表示可见减损可含撤量、补量及不同消息时序的影响，不声称损失主要由SELL导致。零或小L也不证明隐藏挂量或参与者身份。真实H与自有成交、前置队列无关，不抵扣Maker队列。

## 5. 发布与原截止

水位越过e且完整原始批次处理完成后，首个合法边界检查发布，`e≤c≤e+50ms`且c<cS+3s。先处理全部已知失效、迟到的种子SELL反证、e到c的F路径与质量；不能只检查当前恢复量。窗内received事件仍按形成统计处理，封窗后迟到依质量合同，不能改称窗后成交绕过要求。

确认锚P，`deadline=D=cS+3000ms`。从种子而非验证确认、订单发送、ACK或补量计时。设`s=tR−cS>0`、`δ=c−e`，发布要求`0≤δ≤50ms`，且`c−cS=s+500ms+δ<3s`。组合剩余寿命为`D−c=3s−s−500ms−δ>0`。

旧版由首SELL750ms推导的“确认在1300ms内、剩余至少1700ms”不再适用。可接纳750ms之后、理论上约2.5s之前的部分首SELL，但实际c仍须在D前，不能以tR<cS+2.5s替代完整发布检查；恰好2.5s的首SELL即使δ=0也不能确认。50ms是允许的最大发布延迟，不是每个root都必须预扣的等待。

没有信号层面的最小剩余寿命门槛；短寿命信号可成立，但所选执行方式可能无法准入。未处理完整原始批次不能提前发布；同批后行失效优先，原D等号先结束。上述期限关系不是实际存活或成交保证。

750ms之后保留仍有效的种子会扩大局部可接受路径，也会延长部分root占用，可能改变后续新加托接纳。不能把旧750ms超时数量直接算成新版新增信号；首SELL后H不足、Fpre先破、F失败或末量不可证仍然阻断，不沿用旧M23结果身份。

## 6. 等待、执行及重启

确认后只维护固定F、原P存在、质量/generation/tick和原deadline；不重复要求形成H，不在任何阶段重验H≥L，不加入RF稳定退出。补量可继续存在，但不提高F、不重置Δ或期限；不把后续保量称为又一次独立确认。

失效或到期先于同边界新准入。Maker在P合法被动挂价后等待；Taker只在信号仍可引用且P有可主动取得的对手量时准入。正常未交叉同token盘口中，P的正Bid/F与同P主动买入无共同准入状态；按[固定锚价合同](FIXED_ANCHOR_CONTRACT.md)记录限制，不删底线、不改锚追价。

已知Q<F、原档消失、源序不可证明或到期立即结束并通知处理未成交部分。撤单在途、部分及实际价格不同的成交均如实保留，不因之后失效删账。历史执行预算、400ms余量和市场时段独立选择，不成为形成门槛。若仅剩80ms但全部信号条件通过，信号照常确认；执行配置若要求400ms，则记录执行余量不足，不删除信号、不延长deadline。

结束后只有received与输入键均晚于结束的全新合格加托事件可另起root；旧增量及SELL不复用，不换Δ/基线续上。新root不自动解除执行额度。其他Ask只检查质量/结构与真实执行合法性，不新增Ask方向确认。

## 7. 确认与失效复核

| 场景 | 本版处理 |
|---|---|
| B=100、Δ=50、Q0R=150、H=30、最低及末量145 | L=5、F=110；若完整来源/时间/路径合格，可通过，不强求跌至125以下 |
| 同初量，H=30、最低110 | L=40>H，但H≥25且Qmin=F=110；其余完整证据及守卫成立时允许，不把L>H继续当失效 |
| 没有真实SELL，仅静态厚量 | H门槛失败；取消L下限不取消受击证明 |
| 首SELL前140的Fpre已破，后来SELL可得 | 旧root结束，不能切110救回 |
| 全窗及末100ms守F | 是同一量界，不再额外计算一份末段留存优势 |
| 末量增加、L=0 | 允许，但须有真实H与成交后明确数量来源，缺量不按零损失 |
| 后续好量或复核完成 | 不重置种子3s，过期等号不能新准入 |
| cS=0，首SELL received/available均1.20s，c=1.72s，完整来源/量界合格 | 允许确认，D=3s、余1.28s，750ms诊断false；旧版0.75s结束 |
| 首SELL received或available恰cS+750ms，其他条件有效 | 不因等号失败，诊断false；仍须在D前完成500ms验证 |
| 首SELL received=0.74s、available=0.76s，其他来源和质量均合格 | 新版不受750ms限制；旧双时钟对照false，不因received较早就记true |
| cS=0、首SELL2.40s、完整发布c=2.92s | 可确认，余80ms；若执行配置要求400ms则仅执行受限 |
| 首SELL恰2.50s，或e<D但实际完整发布c=D | 到期等号先结束，不能缩窗或回填发布时间 |
| 到0.75s无首SELL，但Fpre/质量仍有效 | 不结束、不换root、不提前切F；原D仍为3s |
| 首SELL前Fpre在1.0s破界，1.2s又恢复并出现SELL | 已失效，不允许按当前快照或较低F救活 |
| 首笔小SELL后500ms内H不足，之后才有大SELL | 第一窗失败，不能跳过首笔、滑窗或拿后续成交补足旧窗 |
| 750ms标签false或仅标签缺失 | 不影响确认/选根/维护；必要时间戳/数量链不可证仍UNKNOWN |
| 500ms结束后再开启一个100ms末段留存计时器 | 不符合本版及直接基线；末100ms已在500ms窗内被连续F覆盖 |
| 同frame合法交接后又破F，或发布前发现种子期SELL | 先处理完整批次反证，不发布；保留已发生真实成交 |

必要输出：variant/version、SOP/参数/直接基线及上游来源hash、增量/种子/首SELL事件键、B/Δ/Q0R/H/L/Qmin、Fpre/F、零SELL及末量源证明、cS/tR/aR/e/c/deadline、两种首SELL等待时长、严格750ms对照标签、实际发布延迟与剩余寿命、反证阶段、执行模式与真实回执。仅保存小型摘要与引用，不生成逐tick日志；未实现或测试，未证明增频或结算优势。

## 8. 语意代价与全路径一致性

本版不再要求挑战迅速到来，保留加托在受击前持续有效及受击后缓冲仍在的完整语意。750ms必须在预筛选、WAIT_FIRST_SELL计时器、缓存清理、引用结束传播、首笔资格、发布、排序、重启和执行消费者中都只作诊断；不能只删除一处判断，又由另一回调提前结束或拒绝。不存在旧“剩余≥1700ms”的隐含信号门槛。

继承直接基线的H≥L删除、允许L=0及无RF退出；不得从COMMON重新引入H/L双材料性。保持首笔新SELL身份、received与输入键严格晚于种子、Fpre→F合法交接、真实H、完整成交后数量来源、同批失效优先及原种子3s，不因强调频次而将未知当通过。

本版为显式独立SOP，adapter=null不表示已实现；不支持应报告未适配，不回落到750ms旧版。本次只交付文档并更新第1批入口，不做信号测试、回测、模拟、适配开发，不查询或操作运行进程，不恢复小时任务。后续数量与效果由用户模块验证，旧版定义和历史结果保留。
