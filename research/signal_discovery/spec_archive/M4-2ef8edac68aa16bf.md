# M4 守强攻弱

```json
{
  "document_type": "signal_sop", "strategy_id": "M4",
  "version": "m4-frequency-v2", "status": "DOCUMENTED_FROM_SOURCE",
  "sop_revision": "2026-11-11-r2-source-transition-clarification",
  "common_contract": "LEGACY_COMMON.md", "role": "DIRECTIONAL_MAKER_SIGNAL",
  "source": ["src/polymaker/m4_frequency_signal.py", "src/polymaker/m4_rounds.py"],
  "direct_trades_only": true, "round_window_ms": 500,
  "bid_support_rounds": 1, "bid_support_ttl_ms": 8000,
  "ask_reference_rounds": 1, "ask_reference_min_recovery": "0.50",
  "ask_reference_ttl_ms": 15000, "weak_rounds": 1,
  "weak_loss_fraction_min": "0.05", "weak_recovery_max": "0.60",
  "weak_reference_fraction_max": "0.80", "comparable_ratios": ["0.10", "10.00"],
  "ask_weak_ttl_ms": 5000, "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 语意与版本

Bid经真实卖出冲击仍稳定补回，Ask对可比买入冲击的恢复能力比此前下降；Maker等后续卖盘回到原承接档。用户已将M4列为初筛通过方向，本文件不据此宣布新24h标准达标。

记录2026-11-11工作区frequency-v2：`weak_min_loss_fraction=0.05`。相同definition名字历史参数可能变过，交给测试模块时须同时记录源码/参数hash。不是早期两轮RF加严格2+2轮DF的M4。

## 计算与确认

采用LEGACY_COMMON的500ms原滞回保持轮。Bid一轮合格stable、r>=0.50，cB确认支撑，截止cB+8s。

Ask一轮合格stable且r>=0.50作为参考，15s内下一合格轮比较：两轮的u=H/Q0、d=L/Q0比值均在[0.1,10]；新轮d>=0.05，`rweak<=min(0.60,0.80*rref)`。cA确认弱供，截止cA+5s。比较后消费参考；不挑后续最好看的轮。新轮若自身stable且r>=0.50仍可按源码生成新参考，审计保留输入顺序。

两组件同token/generation且当前有效、原Bid档可见、盘口合法时确认。锚=Bid支撑档；`deadline=min(cB+8s,cA+5s)`。Ask两轮理论约1s，Bid可重叠；**8s是Bid证据寿命，不是观察8s确认**，候选确认后最多5s。

## 失效

Bid下一合格轮不stable清除支撑；Ask已有弱供后出现stable且r>=0.50的恢复轮清除弱供；组件TTL、原档消失、queue重建、质量失效结束。旧实现以最新support/df配对产生新candidate ID，订单root通常是df.id；新候选不得被测试模块当作旧订单的隐式延期。

**同一Ask轮的处理顺序**：源码先用当前stable且r>=0.50的轮清除已经存在的旧DF，再用该轮与旧reference比较，若减弱条件满足可以创建新DF，最后再决定是否将当前轮设为新reference。不能改写成“当前r>=0.50就禁止任何DF”。例如参考r=0.80，新轮r=0.55且其余条件成立，仍满足0.55<=min(0.60,0.64)，可以创建新DF；之后另一stable轮r=0.50却可以清掉它。这反映旧版的相对减弱准入与绝对恢复退出规则，不代表退出时恢复率一定比弱轮更高。此处忠实说明现有实现，不修改其逻辑；新研究若要调整，应另立版本。

## Maker和可观测性

信号没有COMMON中新研究额外Bid数量floor，整理时不增加该约束。测试模块自行冻结等待/取消规则，忠实保留信号结束事件。需要直接BUY/SELL成交、固定档轮次与hold路径；单纯Ask减量不算DF。确认量、原锚、deadline、后续明确失效都需可重建。

## 同家族备选分支：相对弱供保持

以下为2026-11-11提出的**未测试备选** `m4-relative-exit-v1`，必须显式选择。上文主版本仍为`m4-frequency-v2`，不是自动升级；本文件首个JSON与主版本数值参数保持原样。不新增M编号，不修改源码或正在运行的配置。

```json
{
  "document_type": "signal_sop_variant",
  "strategy_id": "M4",
  "version": "m4-relative-exit-v1",
  "status": "SPEC_ONLY",
  "selection": "EXPLICIT_ONLY_NOT_DEFAULT_OR_DEPLOYED",
  "research_family_id": "F_SUPPORT_WEAK_SUPPLY",
  "inherits_version": "m4-frequency-v2",
  "baseline_definition_sha256": "adcbaf309a7a4e2a46c1fc5194634e5fafbce651023aa45e45cc889586c5e947",
  "baseline_snapshot": "../signal_discovery/spec_archive/M4-adcbaf309a7a4e2a.md",
  "common_contract": "LEGACY_COMMON.md",
  "adapter": null,
  "override": "EXISTING_DF_STABLE_ASK_RECOVERY_EXIT_ONLY",
  "recovery_exit": "STABLE_AND_RECOVERY_STRICTLY_GT_MIN_0_60_0_80_TIMES_FROZEN_DF_REFERENCE_RECOVERY",
  "threshold_reference": "EXISTING_DF_REFERENCE_NOT_CURRENT_REFERENCE_SLOT",
  "recovery_exit_requires_comparable_hit_or_loss": false,
  "reference_slot_updates_change_existing_df_threshold": false,
  "preserved_existing_df_deadline_ms": 5000,
  "new_df_replacement_policy": "INHERIT_BASELINE",
  "new_bid_support_policy": "INHERIT_BASELINE",
  "execution_profile": "EXPLICIT_FROZEN_PROFILE_MATCHED_TO_BASELINE",
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

### 交易语意与单一改动

保持原准入：Bid稳定承接、Ask相对历史参考转弱。形成每份DF时，从这份DF保存的原reference冻结`theta=min(0.60,0.80*r_reference)`。后续真正稳定的Ask恢复，只有超过theta才以恢复理由结束这份旧DF。

待检验的是：已被原准入定义为相对弱供的恢复水平，是否应该仅因达到绝对50%就结束等待。原版可能有保守退出的实际价值，因此这不是已证实的bug修复。新分支只是另一种等待假说，不证明继续等待能改善实际结算结果。

除了下述“清旧DF”判断，完全继承上述冻结基线的真实成交、500ms原hold轮、参考选择/消费、Bid支撑、准入、原档/质量、TTL、同轮创建新DF及更新reference的顺序。不能借本分支切到COMMON严格末100ms轮、添加新Bid floor、延长5秒，或换为更容易成交的挂价。若要改变其他规则，须另记明确版本。

### 每轮处理与失效

收到原基线合格Ask轮R时，先处理原到期、generation/namespace和质量失效。对当时仍存在的旧DF D：

1. 取D形成时冻结的theta_D，不能取当前reference槽位的恢复率重算；缺少D原reference时本版UNAVAILABLE，不用最近轮补齐。
2. 仅当`R.stable=true`且`rR>theta_D`，以`ASK_RELATIVE_RECOVERY_EXCEEDED`清除D。等于theta不因本条件失效；不稳定轮仍按基线，不凭一个末端高值触发稳定恢复退出。
3. 随后照原基线，用当前reference与R尝试创建新的DF，消费该reference，再按原条件决定R是否建立新reference。这几步不被新的退出判断跳过。

本退出判断与原版一样，不额外要求R的u/d与旧参考可比；它是撤销旧证据的稳定恢复门槛，不是再创建一个合格DF。若需可比性才能退出，是另一个研究问题，本版没有添加。

即使D没有被第2步清掉，只要第3步按原规则生成新DF，旧D仍被新DF替换；不是同时保留两份D供结果择优。新reference本身则不能改变尚未被替换D的theta或deadline。Bid下一合格轮的支撑替换/失败、任何必要档消失、输入质量失败、队列重建及原期限到达，均照原规则处理。

原D截止始终为`cD+5s`；组合截止仍取当次support原截止与D原截止的最小值。原reference的15s只限制形成新的DF，消费后不能拿它给旧D重计时。已有订单继承其发送时冻结的原截止；新support、新reference或新DF不能给该订单续期。

### Maker交接

两版本必须使用同一预先冻结的Maker执行profile比较：原锚合法post-only，按所选信号的实际失效发撤单，资金/数量、延迟、队列、额度、费用与结算口径保持一致。profile未提供时只能交付信号事实，不能报告实际Maker验证。

本分支仅改变上述恢复退出原因；不将T240久挂结果等同于信号有效期间成交。DF替换导致旧root不再当前有效时，旧订单按原身份结束规则撤剩余；即使同一处理批次出现新候选，也不能把它当旧订单延期。取消在途成交和失效前已成交均进入全量经济账。

延后部分撤单不保证成交更多，更不保证成交更好。若更早的新增成交占用市场额度、资金或影响后续下单，完整订单集合也未必是原版超集。不能只抽取新分支多等到的赢家，再把原版后来的成交全部保留。

### 变化范围与边界例

原轮stable已经要求r>=0.50，参考形成也要求r_reference>=0.50，所以theta在[0.40,0.60]内。若r_reference<0.625，theta<0.50，两个版本对此退出原因相同。差异只在r_reference>=0.625且后续轮稳定、`0.50<=rR<=theta`时出现。这里theta严格/非严格比较来自本分支的明确选择，不以浮点容差模糊边界。

| 原DF参考恢复率 | theta | 后续稳定rR | 原版清旧DF | 本分支清旧DF |
|---|---|---|---|---|
| 0.80 | 0.60 | 0.50 | 是 | 否 |
| 0.80 | 0.60 | 0.60 | 是 | 否 |
| 0.80 | 0.60 | 0.61 | 是 | 是 |
| 0.625 | 0.50 | 0.50 | 是 | 否 |
| 0.60 | 0.48 | 0.50 | 是 | 是 |

表中只判断处理顺序里的“清旧DF”一步，假设旧DF未先因其他原因失效；不保证整轮处理结束后它仍存在。如果同一轮又产生了新DF，旧root仍结束，新root必须单独识别。

例如旧DF来自r_reference=0.80、r_weak=0.55，theta=0.60。后一稳定r=0.50不会因本分支清掉旧DF；若当前reference原为0.55，则0.50又不满足新DF阈值0.44，旧DF可继续至其他失效或原5s截止。该轮会按基线成为新reference，但不能把旧theta降到0.40。再之后若某一轮生成新DF，则替换旧DF，不能将旧DF存活算成新DF寿命。

旧theta=0.60时，后续稳定r=0.61会清旧DF，而且它超过全局0.60弱轮上限，因此同一轮不可能又创建新DF；该轮可按基线成为新reference，之后的新轮才可能形成另一DF。

反过来，旧DF未被恢复条件清除，也仍可能被新弱轮替换。例如旧DF的当前reference恢复率为0.60，后来r=0.40且其他新DF条件成立，则可创建theta=0.48的新DF并替换旧DF；这不延长旧root。候选身份必须沿实际状态顺序追踪，不能仅凭“恢复退出没有发生”推断旧候选一直有效。

### 输入、审计与验收边界

除主版字段，记录`variant_version, original_df_id, original_reference_id, original_reference_recovery, frozen_exit_threshold, current_reference_id, subsequent_round_id, subsequent_stable, subsequent_recovery, old_absolute_exit_predicate, relative_exit_predicate, df_replaced_same_round, original_candidate_deadline, execution_profile_hash`。引用已有轮和事件，不要求新增逐tick日志。

来源复核：`m4_frequency_signal.py`的DF本身保存reference，原清除发生在后续新DF比较之前；`m4_rounds.py`稳定轮以held且r>=0.50判定。这里只读到了可供规格描述的源码字段，未验证实际录制保留完整引用，也未确认任一运行实例支持本分支。

本分支没有新增入场方向。研究时先区分原版退出后、本版继续期间出现的有效等待和实际成交，再评价同一冻结24h/profile的全部订单、费用、结算及trim3/trim5；不能只用存活时间或假设成交宣布优势。当前SPEC_ONLY，尚无频次、PnL或结算概率改善证据。


## 原语组合与Maker成交语意（统一映射 v1）

本节对应主版本 `m4-frequency-v2`，只整理既有定义，不改上文参数、执行或备选。`hist/live/→/∥`及旧轮、严格轮的版本区别见[统一映射](STRATEGY_PRIMITIVE_MAP.md)。

**原语组合**：`live(S_L,BID@M4) ∥ live(DF_ASK@frequency-v2)`。DF对应P_DF主版；Bid是单轮旧稳定支撑，不是P_RF两轮或COMMON严格S。

**先后顺序**：Bid一轮500ms合格稳定、恢复≥50%，提供8s事实。Ask先有≥50%稳定参考，15s内下一可比冲击满足r≤min(0.60,0.80*r_ref)且减损≥5%，提供5s DF；两组件重叠即可，没有额外的跨侧先后要求。

**当前等待与失效**：截止 `min(cBid+8s,cDF+5s)`。下一合格Bid失败清支撑；Ask新稳定轮先清旧DF、再与旧参考比较，可能生成新DF，最后更新参考；新ID不是旧订单续命。不新增连续Bid floor。相对退出备选只在显式选中时改旧DF退出。

**Maker成交语意**：Bid曾真实受卖并恢复、Ask相对以前更难恢复，在这些事实仍可用时接原Bid的后续SELL。8s是支撑寿命，不是确认耗时。

**重叠与合并边界**：与M12/M14/M19归“承接与弱供”。相对弱化允许0.80→0.55，不等于绝对弱轮≤0.30；备选m4-relative-exit-v1单独记录等待合同。

形成期流量不抵扣随后Maker订单前置队列；全部实际成交（含失效后及撤单在途）保留经济记录。这里只表达等待政策下的成交阶段，不宣称结算概率或盈利已经提高。
