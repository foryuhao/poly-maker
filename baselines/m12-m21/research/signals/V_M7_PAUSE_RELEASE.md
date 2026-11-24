# M7 增频分支：歇后即观再攻

版本 `m7-total-pause-released-reattack-v1`，`SPEC_ONLY`。**买攻后的总量回落已经成立，即可从新的BUY观察再攻；第一次买攻后的承接与暂停后的再攻均确认且仍有效时，在固定承接锚价成交。** Maker/Taker是独立执行方式。

直接基线为[V_M7_PARALLEL_CONFIRM](V_M7_PARALLEL_CONFIRM.md)，上游为外部冻结M7 v7；两者和[M7原版](M7.md)完整保留。本次仅将暂停单桶35%降为诊断，并解除暂停确认后冻结AP1对新BUY观察的占用。AP资格、暂停总量50%、新支撑算法、并行组合及原截止保持，不自动切换运行版本。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M7",
  "variant_id": "M7_PAUSE_RELEASE",
  "version": "m7-total-pause-released-reattack-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY",
  "research_family_id": "F_BUY_RETURN",
  "baseline_version": "m7-pause-then-parallel-support-reattack-v1",
  "baseline_signal_variant": "m5-m10-local-ap-calibration-v7",
  "basis_file": "../signal_discovery/spec_archive/M7_M10_M13_DISPOSITION_BASIS_20261116.json",
  "basis_sha256": "1e7d8dcbd850636d29aa707eb7437b1d8e77fb216d5cdc9e5d424ad34ed07107",
  "original_sop_preserved": "M7.md",
  "calculation_contract": "LEGACY_COMMON_WITH_M7_V7_AND_EXPLICIT_PAUSE_RELEASE_OVERRIDES",
  "signal_contract": "fixed-anchor-signal-v1",
  "execution": "fixed-anchor-execution-v1",
  "entry_semantics": "FILL_AT_FIXED_ANCHOR_WHILE_SIGNAL_VALID",
  "execution_modes": [
    "MAKER",
    "TAKER"
  ],
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE",
  "input_policy": "FROZEN_V7_FEED_DIRECT_AND_VERIFIED_COMPLEMENT_DEDUP",
  "ap_scope": "M7_PRIVATE_LOCAL_PRESSURE",
  "ap_window_ms": 750,
  "ap_bin_ms": 250,
  "ap_min_shares": "1",
  "ap_min_opposing_depth_fraction": "0.0001",
  "ap_direction_share_min": "0.65",
  "ap_min_direction_records": 2,
  "ap_min_occupied_bins": 2,
  "ap_last_bin_required": true,
  "ap_max_single_record_fraction": "0.95",
  "ap_current_ttl_ms": 750,
  "ap_rolling_recheck_policy": "NONE_IN_FROZEN_PRIVATE_V7_DO_NOT_IMPORT_SHARED_AP_EXITS",
  "ap_same_direction_live_blocks_new_watch": true,
  "pause_window_ms": 500,
  "pause_bin_ms": 250,
  "pause_bin_fraction_max": null,
  "pause_total_fraction_max": "0.50",
  "pause_role_after_confirmation": "HISTORY",
  "local_round_ms": 100,
  "local_hold_ms": 10,
  "stable_recovery_min": "0.40",
  "hold_clock_reset_below": "0.32",
  "held_revoke_below": "0.16",
  "round_min_hit_and_loss_shares": "0.5",
  "round_min_hit_and_loss_initial_fraction": "0.0001",
  "support_selection": "FIRST_QUALIFIED_STABLE_POST_AP1_ROUND_AT_FIRST_FROZEN_BID_ANCHOR",
  "unqualified_round_policy": "SKIP_WITHOUT_REANCHORING_AS_V7",
  "qualified_unstable_round_policy": "END_IN_ALL_STAGES",
  "support_ttl_ms": 8000,
  "root_ttl_from_ap1_ms": 20000,
  "round_start_to_support_confirmation_max_ms": 15000,
  "ap2_start_gate": "INPUT_START_KEY_STRICTLY_AFTER_PAUSE_CONFIRMATION_KEY",
  "ap2_start_after_support_confirmation_required": false,
  "ap2_and_support_formation_may_overlap": true,
  "positive_overlap_required": false,
  "ap2_selection": "FIRST_SUBSEQUENT_QUALIFIED_PRIVATE_BUY_AP_VERDICT_NO_REPLACEMENT",
  "restart_after_failed_attack": false,
  "candidate_requires_current": [
    "FROZEN_SUPPORT",
    "FROZEN_AP2"
  ],
  "candidate_confirmation": "FIRST_COMPLETE_INPUT_BOUNDARY_WITH_BOTH_CURRENT",
  "deadline": "MIN_CS_PLUS_8S_CA2_PLUS_750MS_CA1_PLUS_20S",
  "invalidation_before_confirmation": true,
  "deadline_equality_ends": true,
  "component_confirmation_lateness_max_ms": 50,
  "current_bid_guard": "ORIGINAL_P_POSITIVE_NO_NEW_COMMON_40PCT_FLOOR",
  "numeric_change_from_selected_baseline": true,
  "logic_change": "PAUSE_BIN_CAP_DIAGNOSTIC_AND_RELEASE_AP1_OCCUPANCY_AFTER_PAUSE_CONFIRMATION",
  "tests_run": false,
  "baseline_definition_file": "V_M7_PARALLEL_CONFIRM.md",
  "baseline_definition_sha256": "8cbd42c347af6ce37b51a2f58bad49fad784ff7889e2b9c9b3963db87633f0d7",
  "upstream_baseline_version": "m7-local-ap-short-pause-v7",
  "diagnostic_pause_bin_fraction_max": "0.35",
  "diagnostic_missing_invalidates": false,
  "ap1_history_blocks_post_pause_buy_watch": false,
  "ap2_watch_policy": "ONE_M7_PRIVATE_WATCH_SLOT_IGNORE_ONLY_FROZEN_AP1_OCCUPANCY_AFTER_KP",
  "ap2_watch_input_policy": "FIRST_GENUINELY_NEW_BUY_AFTER_KP_NO_REPLAY_NO_DOUBLE_WATCH",
  "revision_date": "2026-11-17"
}
```

## 1. 组合语意与版本边界

两条证据链为：`AP1 → 新S`，以及`AP1 → 总量pause确认 → 全新AP2`；在两条链都完成后检查`live(S) ∥ live(AP2)`，于S固定P成交。暂停确认后无需等AP1自然到期才能开始观察，仍不在AP2刚开始时确认信号。

S可以在暂停期形成，可以与AP2形成重叠，也可以晚于AP2确认才完成；最后一种情况只有AP2尚未到期或失效才可组合。并行是**允许独立完成**，不要求两个统计窗口一定有正时长重叠，也不要求S必须晚于暂停。它改变“先证实承接、再开始进攻”为“新承接与重新进攻共同得到证实”。

这仍不同于M2的两轮回流承接：M7只需一轮局部支撑，但要求暂停和全新AP2。不能删掉暂停后退化为任意AP加RF并继续沿用本版身份。恢复轮与AP均使用冻结v7局部定义，不冒称BA、P_RF两轮或完整EX。

[基线清单](../signal_discovery/spec_archive/M7_M10_M13_DISPOSITION_BASIS_20261116.json)记录上游v7的八项参数、来源hash、原文快照及不可变运行包位置。上游为100ms受击轮、10ms滞回保持、40%恢复、500ms暂停、35%单桶／50%总量；本版仅将其中35%改为诊断，其他数值不变，不套主M7的500ms轮和50%恢复。本文明确覆盖暂停筛选、观察占用及时序组合，其余以冻结来源为准，执行按 [固定锚价合同](FIXED_ANCHOR_CONTRACT.md)。

方向解释是假说：暂停期间或随后卖出得到承接，买方又形成新进攻，可能比仅有历史买攻提供更多当前信息；它也可能只是短暂双向活跃，没有结算优势。本版未测试，不承诺频次增加。

阅读优先级为本SOP明确覆盖项 → 直接旧分支 → 冻结v7来源及LEGACY_COMMON；固定锚价执行按共同合同。旧分支的单桶否决和AP1占用不是本版默认。未知仍未知，不能用旧AP/暂停事实ID冒称本版已重算。

## 2. 输入、时钟和AP1根

使用同token、generation的可判别主动方向成交、固定档深度及质量信息。沿v7 feed核验互补映射、去重及质量，不能双计镜像；AP、暂停、S使用同一来源口径。价格只标档位与执行合法性，不以价格推进、外部BTC价格或未来结果确认方向。

统计窗口用received且右开；可得事件按`K=(available,input_seq,subindex)`排序。完整原始frame后才发布候选/允许执行；已知失效、到期先于确认。无法证明因果次序、覆盖、来源或原档前置数量时为UNKNOWN，不能以零输入替代。封口水位、最多50ms延迟、book/source新鲜度、tick与重建均沿冻结来源。

私有AP从该方向首笔成交开750ms窗，冻结对侧最多三档总深度D>0。方向量V≥max(1,0.0001D)，占总成交≥65%，至少2条记录、三个250ms桶至少2个有同向量且最后桶有量，最大单笔≤0.95V；完整封口后才确认。默认每方向只在没有进行中watch且没有该方向有效私有AP时开新watch；本版唯一占用例外是本root暂停已确认后，冻结AP1不再阻止其新BUY watch。仍只用一个M7私有同向形成槽，不并行启动第二套重复观察器，不改变共享P_AP或其他策略。

空闲时首个可引用的私有AP_BUY作为AP1，冻结其ID、c1/K1、V1和`dRoot=c1+20s`。每token本分支最多一个root；忙碌期间的新AP1仅作已见根，不等空闲后回选。`restart_after_failed_attack=false`：结束root的那次再攻不能同时重启自身。

**私有AP维护区别**：v7私有AP确认后750ms有效，反向私有AP、共享反向AP传来的退出、质量/generation或原截止可使其结束；它没有P_AP主版每250ms的份额60%／半量／末桶空量退出。本版保留这项来源差异，不偷偷补入共享AP当前维护，也不称750ms内每一刻都已重新验证持续进攻。暂停尚未确认时AP1占用照旧。暂停确认以后，仅冻结AP1的当前占用不再阻止新BUY watch；AP1事实及其原750ms寿命不被删除、延长或重新发布。已有watch或非冻结AP1的其他同向当前事实仍按原规则阻挡；同一输入只能进入一个本分支BUY形成槽。

## 3. 暂停与新支撑并行观察

从c1观察完整500ms暂停窗，要求BUY总量≤0.50V1，且只使用K1之后的合格新输入。两个250ms桶仍按原边界统计，但各桶≤0.35V1仅为诊断，超过35%不失败、不提前结束root，也不阻止暂停转历史。诊断为false或可选描述不可用不单独否决；完整500ms成交覆盖、可靠总量和因果键仍必须可证，不能把必要覆盖UNKNOWN称为诊断缺失。封口检查完整覆盖及质量；失败或未知结束root。确认键记Kp、时刻cp；暂停随后只作历史。原局部暂停事实的750ms自然寿命不约束后续AP2完成，否则“暂停之后再开750ms AP2”无法在其原寿命内完成。历史真实性及质量仍须有效；暂停后BUY增加本身不撤销已完成的暂停。

同时，从K1之后第一笔能匹配正量Bid档的SELL冻结观察器原锚P。按v7输入键gate，不额外把received严格大于c1作为新筛选；已计入AP1的旧记录不可重复作为跨阶段新事件，无法证明次序则UNKNOWN。P不要求后来一直是买一，但必须始终有可见Bid量；不能失败后换档。

每轮从同P首SELL开100ms窗，Q0>0为前置量，H为窗内该档SELL，L=Q0−Qmin，`m=max(0.5,0.0001Q0)`。H≥m且L≥m才合格。`r=clip((Q−Qmin)/L,0,1)`；r≥0.40开10ms保持计时，r<0.32清计时，r<0.16撤销已取得held，新低同时清二者。封口须held且r≥0.40；不是COMMON末尾100ms≥50%定义。

未合格轮按v7跳过，P保持不变；**任一合格但恢复失败的轮终止root**，不能等下一轮成功。首个合格且稳定的新轮固定为S，确认于cS，`dS=cS+8s`，保留来源中的`cS−round.start<=15s`界。S的生成不能清空原本已开始的AP2 watch。

选定S后不换ID、不续期；后续同P合格失败轮、原P消失、质量反证或dS到达均结束root，即使AP2还没准备好。没有新增COMMON的40%数量floor；S本身使用的是v7的40%恢复门槛，两者不可混淆。

## 4. AP2首选规则与新的先后条件

暂停在cp/Kp实际可得且root仍有效后，若本分支BUY形成槽空闲，可从首个因果键严格晚于Kp的全新BUY开AP2 watch，纵使冻结AP1尚未到期。统计起点仍取该笔原received，冻结新D，完整看完750ms。只解除AP1占用，不清空已有watch、不回放AP1或暂停期成交、不把原来已形成的窗口挪到cp，也不新加received>cp条件。若原始批次内顺序不可证则UNKNOWN；完整批次有质量/原档反证仍优先。

该单一形成槽由本M7分支消费一次；旧私有AP同向自动开窗路径不能再为同一笔启动重复watch。AP1自然到期回调不终止已经合法开始的AP2 watch；历史真实性或质量反证仍照常结束。未确认AP2之前，反向AP事件按本节原阶段规则处理。其他策略和共享原语不读取本局部占用例外。

持续使用完整、不可挪动的私有AP统计窗口。AP1之后的后续BUY watch若完整关闭但不合格，则没有AP事实，沿v7释放watch等下一次真实开窗；不把它当AP2、不移窗重算。缺覆盖或迟到属于UNKNOWN，不能当普通不合格跳过。

对AP1之后首个新的合格私有BUY AP事实，先检查：

1. 暂停已经因果可得，且该AP的**原首笔start_key严格晚于Kp**。只有结束/确认晚于cp不够。暂停前开始、后来才确认的AP必须结束root，不清窗口后重新贴上“再攻”。
2. 当前root、原锚（若已冻结）、S（若已选定）与质量均未失效；所有到期等号先处理。
3. 不再检查AP2.start_key是否晚于S确认键，也不因S尚在形成而报`EARLY_REATTACK`。合法AP2冻结ID、start_key、c2与`d2=c2+750ms`，成为等待组合的当前事实。

第一条以原输入键为准，不额外加入另一套received起点门槛。相同available但有可信不同subindex时仍按原键排序；若暂停和新首笔的实际先后无法证明，不假设符合。完整frame中的后续反证先于最终组合发布。

一旦AP2选定，即使S未形成，也必须持续维护其当前有效性；AP2到期、反向AP使其结束或质量失败均结束整个root，不可保存为历史再等S，也不可改选AP3。后来的正向新事实不替换冻结S/AP2。AP2作为已见根登记，不能在同一次处理成为另一个AP1。

反向AP的阶段边界：暂停尚未确认时，私有或共享AP_SELL按原策略结束root；暂停已确认但尚无已确认AP2时，不仅凭反向AP出现就删除暂停历史，S及其他守卫照常检查，进行中BUY watch仍按原完整窗口判定；**已有AP2后**，反向AP结束所需AP2，也就结束root。这不是新增的放松，是沿用冻结来源的阶段归属。

## 5. 联合确认、截止和失效

S与AP2哪一个先完成都可以等待另一份，但先完成者必须保持当前有效。首次二者同时可引用的完整输入边界记c，须满足：

`c < d = min(cS+8s, c2+750ms, c1+20s)`。

确认时固定P、S/AP2引用、c/d；AP1与pause只作历史。通常c=max(cS,c2)，若完整frame或可引用边界更晚，记录真实c，不倒填。**不从联合确认再加750ms或8s**；AP2先完成时，等S的时间已经消耗AP2寿命。单份证据、受击刚合格或AP2刚开始都不是候选。

| 事件 | 联合确认之前 | 联合确认之后 |
|---|---|---|
| 暂停不合格、未知、暂停前反向AP，或合格再攻从Kp之前开始 | 结束root，不重选 | 已知形成历史被证伪仍结束 |
| AP2先完成，但没有S | 只等待，原d2继续走；到期结束 | 不适用，当前候选必须已有S |
| S先完成，但没有AP2 | 只等待，原dS继续走；到期结束 | 不适用，当前候选必须已有AP2 |
| 任一同P合格受卖轮恢复失败、P丢失或质量失败 | 立即结束，不因另一份证据同批成功救活 | 立即结束 |
| 所需S/AP2已结束，或dRoot到期 | 结束，不发布 | 结束，不再准入 |
| AP1或pause纯自然TTL到期 | 已确认的历史不单独否决，dRoot不变 | 同左 |
| 后续好S、正向新AP | 不替换已选证据，不续期 | 不替换、不重复确认 |

处理优先级为当前输入的质量/历史反证、数量与合格失败轮、原截止，然后生成可用事实，最后尝试联合发布；任何必要事实在同一个完整frame里先成立后失效都不能用于发布。未结束轮不因尚未恢复就提前认定失败，但原档与质量守卫实时生效。

信号生命最多由AP2的750ms限制，可能显著更短。AP2本身仍需完整750ms，S仍需100ms；并行不省略完整观察。除冻结AP1例外外的私有AP占用、空档、来源可得延迟会影响总确认时间，不能仅把三个名义窗口相加就声称固定时间内必能形成。

## 6. Maker/Taker执行

只在已确认且当前有效时申请固定P成交。Maker在P以post-only等待；Taker保留意向，仅当P有真实可主动取得的对手量且信号仍有效时申请。两者均不延长d，不换锚，不自动重发、切换方式或改价。

本版保留P正Bid，正常未交叉同token盘口下与同P Taker买入没有共同准入状态；记录执行受限，不为成交删除原档守卫。执行预算、400ms发送余量、队列与延迟单列，不进入本版确认规则。

失效先于同边界新准入，结束未发意向并通知处理在途/未成交部分；全部真实成交、真实价格及次序未知均保留。取消请求不等于成交不存在，信号失效也不自动定义成交后平仓。本单成交不算新增承接证据。

## 7. 确认与失效冲突复核

以下仅为定义算例和逻辑检查，不是测试：设c1=0、V1=100，暂停两桶为40与5，总量45≤50；0.50s完整确认暂停，单桶诊断为false。冻结AP1自然到期0.75s，首个暂停后新BUY于0.55s开启AP2，1.30s完整确认；新S于0.90s确认，其他输入与守卫均有效。可在1.30s确认组合，`d=min(8.90,2.05,20)=2.05s`。旧并行版被40>35否决暂停；即使暂停桶量均达标，旧AP1占用仍不能让0.55s开始新watch，本版允许。

- 若上述S直到2.05s才可得，AP2到期等号先处理，不能发布；不会从2.05s重计750ms。
- 若AP2窗口在暂停确认之前开始，即使1.55s才确认，也不允许把它切成新窗。
- 若1.60s同一frame中S合格但原P随后消失，最终不发布；若S已选定后有合格失败轮，不能等新好S来替换。
- S早于暂停完成也可用，只要是AP1后新证据且到联合确认仍有效；不误把“并行”改成S也必须从暂停后才开始。
- AP2先于S确认可以等待，但AP2转为历史再等S不合法；pause则必须只作历史，不能要求其750ms当前寿命覆盖整个AP2。
- 暂停未确认时，AP1占用仍在；暂停确认后可忽略冻结AP1占用，但不得重复开窗、补用暂停内BUY或绕过其他当前事实/已有watch。
- 暂停总量恰为0.50V1允许，大于则结束；单桶0.35V1仅诊断，没有形成或退出作用。
- 同一批次先确认暂停、再有可信后序BUY，可合法开watch；最终批次出现必要反证则结束，不抢先发布。
- AP1自然到期不关闭新watch；暂停历史被证伪、质量或原档失效仍结束。没有把AP2到期/反向失效改成历史退出。

同输入先后、首选根、数量与质量守卫、原截止经上述规则可同时成立。频次是否增加、是否提供结算增量未验证；旧诊断中的过早再攻失败不是可恢复信号计数，UNKNOWN也不能变成通过。

输出variant/version/basis hash、AP1/pause/S/AP2原始身份及窗口、start_key与确认键、P、V1/暂停桶量、S的Q0/H/L/r/held、c1/cp/cS/c2/c/d、当前所缺证据、失效原因、执行方式及实际成交归属。只留摘要和证据引用，不生成逐tick日志。本文未实现检测器/适配器、未运行测试。

本版将两段分别受限改为整体买量回落，允许暂停期买量分布不均；解除历史AP1占用只提前观察资格，不省略新AP2或支撑。输出另含暂停总量/单桶诊断、Kp、AP1到期、占用例外是否使用、唯一watch及首笔身份。未改变AP分桶资格、100ms/10ms支撑、750ms当前AP2、8s支撑、20s根期限或15s来源上限。单活动root和首选路径会影响全局数量，未证明频次或结算改善。
