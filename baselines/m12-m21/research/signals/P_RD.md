# RD 回补回吐

版本 `rd-recovery-decay-without-new-hit-v1`，**SPEC_ONLY**。先有真实受击及稳定恢复，再观察原档恢复量在没有新增直接打单记录时回落并留在低位。BID侧表示买方回补流失，ASK侧表示卖方回补流失；不是新一轮主动攻破，也不证明撤单主体、意图或某批订单被撤回。

这是“历史恢复＋随后留存”的路径定义，不把普通RF失效另算一份独立方向证据。只有完整输入中的零新增打单、首次回吐、低量保持及发布检查均满足才确认；原支撑可以先于RD确认失效。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "RD",
  "version": "rd-recovery-decay-without-new-hit-v1",
  "status": "SPEC_ONLY",
  "origin": "STABLE_HIT_RECOVERY_THEN_QUIET_DECAY_PATH_HYPOTHESIS",
  "sides": ["BID", "ASK"],
  "data_clock_contract": "maker-discovery-common-v1",
  "common_sha256": "c3ec4443e8651e8a6f685309bdd6142166bada3de6c0846d3c18531cdc803645",
  "common_snapshot": "../signal_discovery/spec_archive/COMMON-c3ec4443e8651e8a.md",
  "inherits_execution_contract": false,
  "direct_consuming_trades_only": true,
  "round_start": "FIRST_NEW_DIRECT_CONSUMING_TRADE_AT_PRE_EVENT_BEST_LEVEL_WHILE_IDLE",
  "seed_round_window_ms": 500,
  "seed_minimum_loss_shares": "5",
  "seed_minimum_loss_fraction": "0.10",
  "seed_hit_must_cover_loss": true,
  "seed_stable_recovery_fraction_min": "0.50",
  "seed_terminal_hold_ms": 100,
  "seed_hold_contract": "COMMON_STRICT_TERMINAL_HOLD_NOT_LEGACY_HYSTERESIS",
  "seed_explicit_terminal_quantity_in_last_ms": 100,
  "seed_terminal_quantity_source_and_key_after_all_hits": true,
  "seed_publication_recovery_fraction_min": "0.50",
  "seed_publication_handoff_requires_continuous_half_recovery": true,
  "quiet_interval_start": "SEED_ROUND_END_MINUS_100MS_INCLUSIVE",
  "quiet_interval_end": "RD_CONFIRMATION_INCLUSIVE",
  "quiet_direct_hit_max": "0",
  "decay_first_cross_received_and_key_after_seed_confirmation": true,
  "decay_first_cross_recovery_fraction_max": "0.20",
  "decay_hold_ms": 100,
  "decay_hold_failure_ends_root": true,
  "decay_publication_requires_recovery_at_most": "0.20",
  "formation_publication_before_seed_plus_ms": 750,
  "confirmation_lateness_max_ms": 50,
  "publication_requires_complete_original_batch": true,
  "current_recovery_fraction_max": "0.40",
  "current_ttl_ms": 750,
  "post_confirmation_new_hits_allowed": true,
  "post_confirmation_new_hit_received_strictly_after_confirmation": true,
  "seed_becomes_historical_at": "SEED_CONFIRMATION_FOR_RD_DECAY_OBSERVATION",
  "seed_current_support_guard_required_during_decay": false,
  "one_root_per_token_side": true,
  "new_root_hit_received_and_key_after_previous_end": true,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 1. 真实受击与恢复种子

采用绑定 [COMMON](COMMON.md) 的时钟、数据质量、原档及严格稳定轮语意；**不使用旧RF的0.4/0.2滞回held摘要替代严格保持**。BID收直接SELL，ASK收直接BUY。按token/side分开状态，generation不能跨越。

空闲时，第一笔价格等于事件前该侧最优档的直接消耗成交开启root及500ms种子轮R，冻结p、明确前置数量Q0、来源和输入键。成交received为tR，`eR=tR+500ms`，统计区间 `[tR,eR)`。原p须连续存在且正量，不因最佳价变化换档；无前置数量或可靠方向不能开轮。

记录该原档轮内最低量Qmin、直接成交量H及 `L=Q0−Qmin`，计算恢复相对量 `r_raw(t)=(Q(p,t)−Qmin)/L`。本文件涉及20%/40%/50%的数量边界均直接使用冻结Qmin/L，不用裁剪后的r掩盖负值或超额恢复。

种子须同时满足：

- `L>=max(5,0.10*Q0)`，且 `H>=L`。因此H也至少达到该受击门槛，不把它再算另一份独立规模证据。这是待测试的材料性门槛，比COMMON基础小轮要求更强；H>=L仍不证明每份减量由成交造成。
- 末100ms按COMMON严格规则持续恢复至少50%；出现新低按原严格轮规则重置。不是先前曾经恢复过50%就算稳定。
- 在 `[eR−100ms,eR)` 至少有一份明确携带p数量的事件；其source和输入键均严格晚于本轮全部计量成交，且在种子发布前可得。BBO、其他档回调或缓存p不能作为这份末端报量。
- 从 `eR−100ms` 起没有直接消耗p的成交；这个零记录守卫会一直延续到RD最终发布，而不只检查种子尾段。

水位关闭eR后，在第一个完整原始批次结束或定时检查边界确认种子cS，要求 `eR<=cS<=eR+50ms`。eR至cS的完整已知路径及发布点须保持 `Q(p)>=Qmin+0.50L`，零打单、原档和质量条件也持续有效；首次发布检查失败就结束，不等待下一次恢复。

种子只是RD的历史参考，**此时不发布RD，不下单**。它可能同时构成COMMON的单轮支撑，但不是两轮RF；后续不能为了观察回吐而延迟其他策略对支撑失效的处理。若R不合格、保持失败、来源不足或发布超时，本root结束；不能从同一轮里挑一段重新定义种子。

## 2. 零新增打单的范围

RD要求原token、原p、消耗该侧的直接成交在接收区间 `[eR−100ms,c]` 为零，c为最终RD可得确认时刻。形成期随着当前可得边界检查已知前缀；发布时先完成原始批次并核对水位、质量和全部已知输入。BID侧不是“所有SELL都为零”，只限制原p；ASK对称限制原p的BUY。其他档的流量不自动证明或否定原档零记录，但不得绕过原档存在及质量规则。

必须在直接来源尚未被互补镜像、方向过滤或去重丢失前核对存在性。缺side不能默认BUY；缺失、断流、无法识别原始批次或原始成交身份，不是合格的零。已证实的同一原始事件重发按首次接收归属；只有相同transaction_hash或相同量价不足以证明重发，参见[缺席证据审查](ABSENCE_EVIDENCE_REVIEW.md)。

只要形成过程中发现一笔落入上述区间的合格直接打单，就结束这个root，不论数量多小，不把它从H中扣掉后维持“零”。这没有排除真实市场中尚未收到或无法辨识的成交，因此名称只能说明**所选完整输入合同下没有观察到新增打单**，不能认定回补必然被撤掉。

种子的末端数量来源在已知受击之后，加上尾段100ms的零记录，减少了直接把受击后的滞后减量当成新回吐的混淆；两者都不构成交易所book/trade共同撮合序号或网络延迟上界。已知source错位无法解释时为UNKNOWN，不能用本地received正常就强行通过。参见[输入能力](INPUT_CAPABILITIES.md)及[时间能力](TRADE_TIMING_CAPABILITIES.md)。

## 3. 第一次回吐与低量保持

种子确认后冻结两个边界：

`T20=Qmin+0.20L`，`U40=Qmin+0.40L`。

选择cS之后**第一份**明确p数量事件使 `0<Q(p)<=T20`，记录tr及事件键。该事件received须严格晚于cS，输入键也严格晚于种子发布键；数量source须严格晚于种子末端p报量来源。早接收晚处理的量不能冒作新回吐。原档明确为零或消失直接结束，不等待它重建。

第一次达到T20后，在 `[tr,eF)`、`eF=tr+100ms` 连续守 `0<Q(p)<=T20`。任何反弹超过T20、原档消失、零打单守卫失败或数据未知均结束，不重计100ms，不跳过第一次回吐选后一次。完整book是完整状态，price_change保留内层顺序；同批中间反弹不能被末态再回落抹去。

达T20之前不要求数量单调下降，也不禁止超额恢复，但始终使用原Qmin/L和原截止；后来更高的峰值不重定义基线。由种子至少50%恢复、首次回吐至多20%可知，相对种子最低合格发布量至少回落 `0.30L`，不把这个派生差另算一次独立确认。

**旧支撑退出与RD观察分开：** 若同一R曾生成COMMON的S_BID，其底线是U40。第一次到达T20必定已经低于U40，因为L>0。因此旧S应在首次跌破U40时失效；RD可以继续用R作为历史参考，不能同时要求那个S仍当前有效，也不能让旧Maker订单等到RD的100ms保持完才撤。

## 4. RD首次发布

水位关闭eF后，在第一个完整原始批次结束或定时检查边界确认c，要求：

`eF<=c<=eF+50ms`，且 **`c<cS+750ms`**。

形成期限包含首次回吐、保持和发布交接，不是只要求tr落在750ms内。eF至c的全部已知路径和发布点继续守T20，零打单守卫延续至c（含等号）；先处理同批失效，再发布。首次发布失败即结束，不借50ms迟到预算延长750ms硬截止，不重新选参考或恢复窗口。

所有条件通过，才发布RD当前事实。从种子确认起理论用时 `100ms<c−cS<750ms`；加前面的500ms种子及最多50ms发布开销，从首笔受击到RD理论范围为大于600ms、小于1300ms。这里只给定义的时序边界，未测实际发生频次。

晚发现的旧输入不能回填种子、保持或订单。若received属于种子轮的记录推翻原种子，或属于 `[eR−100ms,c]` 的直接打单推翻零记录，发现时结束并标迟到反证；无法判断影响则UNKNOWN。确认前发现就拒绝发布，确认后发现则在当前可得时刻结束，保留此前事件与真实经济记录。

## 5. 当前态、结束和重启

当前事实有效期 `[c,c+750ms)`，从发布后维护：

`0<Q(p)<=U40`，原档、质量及generation有效。

形成的20%上界只在发布通过后放宽到40%；正常发布量不超过T20，故不会因切换上界立即失效。任何数量超过U40表示原恢复量重新增加，本RD结束；等于U40允许，原S也不会因此复活。此时不再要求低量持续100ms后才失效。

发布后新打单可以出现，不自动否定已经观察到的历史回吐；须同时满足该成交 `received>c` 且输入键晚于RD发布键，才属于允许的新阶段流量。received等于c或更早的形成/交接期记录仍按迟到反证处理；不能把它改名为新流量。新打单也不续期，且数量、原档及质量规则照常检查。

到期、原档消失、数量重新超过U40、质量/generation失效或历史反证，均结束，不因后来重新变薄而复活。每token/side同时最多一个种子形成/回吐观察/当前root；结束后只能由received和输入键均严格晚于结束边界的新首笔受击开下一轮。旧R不重复使用，新正向事实不替换参考或延长期限。

## 6. 查重及组合边界

| 对照 | RD新增或保留的信息 | 不能混用的解释 |
|---|---|---|
| RF/COMMON S失效 | 额外要求真实受击后严格稳定、尾段起零新增打单、第一次20%回吐及100ms保持 | RD是其中一类可观察后续路径；不是旧支撑失效后仍在承接，也不是所有RF失效都属于RD |
| SR短补回落 | RD从受击轮低谷和恢复比例出发，已有真实成交历史；SR从单次净加量出发且使用前500ms低打单预算 | 同一次回补可能被两套定义共享，不据名称计两份独立证据；二者不互为全局子集 |
| LW疑似退量 | RD只看一个受击原档恢复量是否回吐；LW看固定三档净减量并累计扣成交 | RD可能达标而三档净损失不足30%；不能把RD视为精确撤单或LW的默认替代 |
| DF防守衰退 | RD回吐阶段不允许原档新增打单，DF通常以新真实冲击比较恢复 | 同一零打单回吐区间不能充当DF的新受击轮；老DF与RD是否共存须逐版本核对 |
| M8失守后修复 | RD可作为未来“回吐后修复”研究的历史起点 | 旧M8的弱轮需要真实新受击，不能把RD直接塞进其W槽或继承旧收益 |

组合研究入口：

- `RD_ASK + 当前S_BID/QR_BID`：卖侧曾经恢复的量重新流失，买侧仍有承接。可研究原Bid Maker，但必须另写配对顺序、锚档、两原期限和准入；不能仅有这个表达就执行。
- `RD_BID + CA`：主动反击与被动回补流失并存，表达相反方向的观察，不自动给出买入许可。
- `RD_BID → 新SELL修复`：RD转历史后观察新阶段，须另写首次新轮、恢复目标、失败和等待；不能复活原S，也不修改M8。

RD确认后750ms的寿命可能限制Maker排队。后续完整组合需核对COMMON至少400ms的首次发送余量；形成前的真实H不能给未来Maker抵扣前置队列。现有策略等待中若早已跌破自己的底线，应按原规则退出，不得为了等待RD正式确认而拖延撤单。

## 7. 纸面路径（不是行情或策略测试）

设BID原档Q0=100；0ms发生直接SELL，总H=40，30ms明确量为60，得到L=40。320ms恢复至84，450ms再次明确报量84，来源在全部受击之后；末100ms持续r=0.60且没有新增SELL。500ms种子窗关闭，510ms完成批次并发布cS，量仍84。材料性门槛10、H>=L和严格保持都满足。

冻结 `T20=68`、`U40=76`。此前从320ms起数量一直保持84，620ms第一次报量66，原档仍在；620–720ms保持不高于68，整个400–730ms区间未观察到直接SELL且覆盖完整，730ms完成发布。RD截止1480ms，当前上界76；同一R的旧S在620ms跌至66时已经失效，不能和RD同时当作当前支撑。

这条特定路径不符合以320ms的60→84作为起点的SR：其前500ms包含40份SELL，大于0.25×24=6的预算，而且620ms首次回落已在增量300ms以后。若另外两个固定Bid档各100，三档从100/100/100到66/100/100净损失仅34/300≈11.33%，也不足同基线LW的30%形成门槛。这证明定义有不同可行路径，不证明统计独立、常见或盈利。

若零记录区间出现任意一笔SELL，或首次66后在保持中反弹到69，本root失败，不能剔除该记录或重新计时。若发布后真正新接收的SELL使量进一步减少但原档仍存在，则历史回吐仍可用到原截止；若回补到77，则当前RD结束。

## 8. 数据能力、审计与交付

需要直接成交来源/身份/方向、严格500ms轮的全路径、原档前置/最低/末段/首次回吐数量来源、接收与可得双时钟、原始内层顺序及完整覆盖。只有旧RF或DF摘要、全书更新时间、终点快照或被过滤后的空成交表，都不足以确认本版。现有字段有表达这些观测量的基础，但尚未核验具体录制和输入合同能全部支持；不能把缺项补零。

至少输出 `primitive/version/sop_hash/common_hash/input_policy_hash/token/side/generation/root/p/tR/eR/Q0/Qmin/L/H/seed_quantity_refs/seed_hit_refs/cS/quiet_start/T20/U40/first_cross_ref/tr/eF/c/deadline/quality/end_reason/late_counterevidence`。保存必要的数量和事件引用即可，不要求本研究复制逐tick日志。

本任务只交付SOP和逻辑审查，不实现检测器、适配器或测试。RD可用于不利证据与后续阶段研究；没有证据表明它或其组合已提高BTC五分钟二元市场结算概率、获得101笔真实模拟Maker成交或达到去掉最大盈利3/5笔后的正PnL目标。
