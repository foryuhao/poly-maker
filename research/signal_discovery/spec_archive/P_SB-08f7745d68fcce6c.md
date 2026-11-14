# SB 短退回补

版本`sb-short-depth-dip-replenishment-v1`，**SPEC_ONLY**。某原价档先明显减量，短暂保持低量，随后迅速补回大部分减量；规定区间内已知直接打单量较小。它是[SR短补回落](P_SR.md)的反向数量路径分支，归同一“短暂数量偏离与返回”研究家族，不另算独立方向优势。

“退”和“补”仅指可见总量变化，不证明具体撤单、同一批订单返回或做市商意图。BID表示买量短退后补回，ASK表示供给短退后补回；两侧不能统一解释成看多。未经过真实受击验证，不能称承接成功。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "SB",
  "version": "sb-short-depth-dip-replenishment-v1",
  "status": "SPEC_ONLY",
  "origin": "SIGNED_DIP_AND_RETURN_BRANCH_OF_SR_NOT_INDEPENDENCE_CLAIM",
  "research_primitive_family": "SHORT_LIVED_DEPTH_EXCURSION_AND_RETURN",
  "sides": ["BID", "ASK"],
  "side_means": "PASSIVE_SIDE_WITH_SHORT_DEPTH_REDUCTION_THEN_REPLENISHMENT",
  "data_clock_contract": "maker-discovery-common-v1",
  "inherits_RF_contract": false,
  "inherits_execution_contract": false,
  "direct_consuming_trades_only": true,
  "minimum_net_drop_shares": "5",
  "minimum_net_drop_fraction": "0.20",
  "initial_post_drop_quantity_positive": true,
  "initial_low_hold_ms": 20,
  "initial_retained_drop_fraction_min": "0.80",
  "first_return_before_ms": 200,
  "first_return_available_before_deadline": true,
  "return_remaining_drop_fraction_max": "0.20",
  "return_baseline_fraction_max": "1.10",
  "return_hold_ms": 50,
  "pre_drop_trade_buffer_ms": 500,
  "formation_hit_to_drop_max": "0.25",
  "confirmation_lateness_max_ms": 50,
  "explicit_terminal_quantity_after_all_hits_required": true,
  "publication_requires_complete_original_batch": true,
  "publication_opportunity": "FIRST_COMPLETE_BATCH_OR_TIMER_BOUNDARY_AFTER_FORMATION_CLOSE",
  "current_ttl_ms": 750,
  "current_remaining_drop_fraction_max": "0.50",
  "current_quantity_guard_starts": "FORMATION_END_E_CHECKED_THROUGH_PUBLICATION",
  "forming_upper_ceiling_after_e": false,
  "post_formation_consuming_trade_policy": "NOT_ADDED_TO_FORMATION_H_CURRENT_DEPTH_AND_QUALITY_GUARDS_CONTINUE",
  "additional_post_hit_100ms_recheck": false,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 1. 原档首次净减量

只继承[COMMON](COMMON.md)的数据、时钟、来源、质量及原档规则，不继承RF或自动执行。BID侧直接SELL、ASK侧直接BUY称为目标档的“打单”；深度消息上的挂单侧字段不等于主动成交方向。价格只标识原档，不使用推进或外部BTC价格。

同token/side空闲时，第一份明确报量的健康事件，使更新前该侧最佳原档p从B>0减到B−Δ，且`Δ>=max(5,0.20B)`、`B−Δ>0`，开启root。固定p、B、Δ、前后明确数量来源、内部输入位置和generation；t0为该减量事件received。前量须来自事件之前的明确p报量及连续链，后量来源/输入键须严格在其后，不能用BBO或其他档更新时间补足。

原档消失不作为可恢复的起点；后来重新挂出同价也不能救活。必要可行条件为`max(5,0.20B)<B`，实际Δ仍须小于B。SR增量没有这一正量上限，所以本版并非把SR所有输入机械改一个符号即可。

自t0起，原p始终存在、质量及连续链有效。初始`[t0,t0+20ms)`须`0<Q(p)<=B−0.80Δ`，即至少80%初减量短暂保留。任何已知提前补回过界都失败，不靠同批行号插值出20ms。继续减量不重设B、Δ或t0。

## 2. 首次补回与50ms保持

初始20ms保持完成后，取第一份明确p数量事件满足：

`Q(p)>=B−0.20Δ`。

其received为tr，要求`tr>=t0+20ms`，received和available均严格早于`t0+200ms`，source严格晚于初始减量事件，输入键也更晚。第一达标事件还须`Q(p)<=1.10B`；若已经超过这个上界，直接结束为`RETURN_OVERSHOT_BASELINE`，不能跳过它，后来又回到原量附近才重选。

20ms结束至首达事件前，只保持原档、质量和形成打单预算，不要求仍留在最初80%减量区；允许逐步补回。200ms到期仍无合格首达事件即结束，等号不允许。时间/来源不明标UNKNOWN，不用较晚报量倒推。

冻结tr，设`e=tr+50ms`。整个`[tr,e)`连续要求：

`B−0.20Δ<=Q(p)<=1.10B`。

任一越界即结束，不重计50ms。完整健康book视作一份完整状态；增量数组保留原顺序并检查已知中间状态，不能只看最后又合格的快照。未知的快照间路径不补造，参见[批次语意](DEPTH_BATCH_SEMANTICS.md)。

## 3. 形成打单预算与数量来源

原token、原p、消耗该侧的去重直接成交H，统计received区间为`[t0−500ms,e)`，要求`H<=0.25Δ`。初始化时历史缓冲已超预算就失败；之后一旦可得的H超预算立即结束，不删成交、不截短历史。500ms只是保守缓冲，不保证交易所或网络延迟有这个上界。

H可以为零，但必须有原始直接来源、方向、去重和完整覆盖合同。镜像重复不增加攻击量，过滤后空集不能证明无打单；缺口、未知方向或来源错位为UNKNOWN。该历史范围内的成交可能已反映在初量B里，因此H也不是某一次减量的精确归因。

初减量Δ与已知打单上限0.25Δ之间至少相差0.75Δ；首达补回量相对初次低量至少0.80Δ。两者只是所选观察量的算术关系，不等于精确撤单、精确新增量或相同订单回归。随后更低的数量也不扩大冻结Δ。

最终须有明确p数量事件，其source及输入键晚于所有计量打单，received仍在`[t0,e)`内、关闭时已可得，且来源不早于首达补回事件；首达事件本身可以提供合格末端证据。没有打单时仍须有首达补回来源及连续数量链。只有全书更新时间或其他档回调不够。

形成期的迟到记录按其原阶段和质量规则处理，不能在确认后改称新流量以绕过预算或来源条件。发现历史反证时结束并保留已发生暴露，不倒填历史撤单。

## 4. 发布与当前态

水位越过e后，在首个完整原始批次/相应计时器边界检查发布，确认c须`e<=c<=e+50ms`。先处理完整批次全部已知失效及超时；没有以后等待更好发布点的机会。

从e起，形成带宽切为当前数量底线：

`F=B−0.50Δ`，持续要求`Q(p)>=F`、原档存在及质量有效。

e到c的完整已知路径也要审查；不能只检查发布快照。原形成上界1.10B从e起解除，继续加量不单独结束；同样，发布时不另加“仍须补回80%”的门槛。形成曾保持`B−0.20Δ`，交接/当前只守F，这两个阶段不能混用。此处与SR的阶段切换相对应，没有套入QR/XR的额外发布点80%或90%规则。

当前截止`d=c+750ms`，到期等号失效。新增挂量、新事实、引用或订单回执不续期。Q<F、原档消失、generation/tick变化、质量失败或历史反证均结束；后补回来也不复活。

received>=e的新打单不再加入已关闭的形成H，也不自动否定过去的短退回补；继续执行已知数量和质量守卫。本版没有额外逐笔100ms复核，也没有RF恢复退出。若某个Maker组合需要更严格的新打单复核，必须在其自己的版本中明确，不能从本原语推导自动下单或继续暴露的权限。

理论确认耗时`70ms<=c−t0<300ms`；前500ms是历史覆盖，不机械加为额外等待。这里只是定义边界，不是市场实测的速度。

同token/side只保留一个形成或当前root。结束后必须由received及输入键均更晚的全新材料性减量事件重新开启；旧低谷及旧首达补回不可复用。

## 5. 纸面例与反例

B=100、Δ=20，t0=0量由100降至80，前20ms一直不高于84。80ms首次明确报98，符合96至110，并连续保持至e=130ms；H=0且来源/覆盖完整，140ms首次发布，则F=90、d=890ms。没有任何实际行情、撮合或收益测试。

若H=6，超过预算5；首次补回为115，越过1.10B；保持阶段回到95，低于96；或首达事件received=190ms但available=205ms，均失败。不能从后来恢复的快照重选首达时点。

e之后量91仍满足当前底线，发布时也可通过；量89结束。e之后继续增加至120不单独结束，因为形成上界已解除。若120出现于首达补回或50ms保持阶段，则不通过。这些差别来自已冻结阶段，而非事后选择更宽松解释。

## 6. 同家族查重及共享事件

| 对照 | 关系和边界 |
|---|---|
| SR | 同属短暂数量偏离后返回：SR先增后减，SB先减后增。保存不同SOP便于引用，不将两个文件当作独立优势；减量仍受原档正量约束 |
| BA/RF/QR | 它们需要真实受击或成交强度等证据；SB只有低打单预算，不能将“补回”直接叫守价成功或耐耗 |
| RD/M8重建 | RD先有真实恢复后回吐，M8分支还要新SELL重建旧量界；SB没有这条受击历史，不是缩短版M8 |
| LW | LW看固定多档的净减少；SB只说明一个原档补回。后两档仍退量时，局部补回与三档LW可以同时成立 |
| QO | 若QO已当前有效，同档初量b处在其±5%带内，新SB至少20%减量会落到至多0.84倍QO基线，先结束旧QO。不能称为两份当前确认 |

**同一次SR回落又作SB起点时，回补会否定原SR。** 限定SR基线A、增量δ，且SB的初量恰为原SR峰值`A+δ`，不含后来额外加量。若同一减量D把它带到SR允许的返回区，必有`D<=0.10A+δ`。SB首达补回量至少：

`A+δ−0.20D >=0.98A+0.80δ`。

SR当前上界为`A+0.50δ`，两者差至少`0.30δ−0.02A>=0.04A>0`，因为δ至少0.20A。因此SB在该同源路径上补回达标时，已突破原SR上界；若发生在SR的50ms形成保持期，那里上界更低，同样失败。这不是两次独立确认，也不说明所有不同root互斥。

**不同事件与基线可以共存。** 例如SR此前已确认，A=100、δ=20、当前U=110，量104。随后另一SB从104降至80，D=24满足材料性门槛，短暂保持后补至100；SB要求补至至少99.2，当前F=92。若两者各自的来源、时间、原截止等均满足，当前量100可同时落在`[92,110]`。这表达两段不同历史，不应把名称的相反直觉当作逻辑矛盾或独立预测证据。

**局部补回不自动取消多档撤量。** 某Ask LW已从原三档100/100/100变为100/40/40且仍有效；同一前档随后按SB从100降80，再补到98，固定三档此时98/40/40，总量仍比300少122。假设直接BUY为零及其余条件合格，LW仍可维护，SB也可确认。不能因前档补回就把全侧撤供清零，也不能据此认定卖方整体防守已强。

## 7. 组合入口与Maker限制

- `既有S_BID + SB_BID`：只有旧S从初减量起就未失效，才是当前重叠；如果SB低谷已破旧floor，补回不能救活旧S。在同P、同数量口径的这一前提下，初减量后`B−Δ>=F旧`，而`F_SB=B−0.50Δ>B−Δ`，所以SB底线已经覆盖旧数量guard；旧受卖历史、原截止及其他退出仍要保存，不能把整个S删掉。
- `SB_BID → 新SELL/QR验证`：可研究“短退补回后，真正受卖时还能否留量”，但原SB当前态和历史交接必须明确，不能直接套M8重建定义或复用旧QR。
- `AP_BUY + SB_ASK`：主动买入同时供给补回，是对抗观察，不能自动把两者记为同向利多。
- `SB_ASK + LW_ASK`：先分清一个原档与固定三档，以及是否共享同一次初减量；不能因为两个名字都包含退量就重复加分。

上述均未形成可执行的新策略。750ms当前寿命仍约束新组合；若从SB确认后才开启完整750ms QR并要求两个当前事实重叠，QR完成前SB就已到期。完整Maker政策还需冻结原锚、首次准入、成交后量检查、队列、撤单竞态及全部结算账。

## 8. 数据能力与输出

输出`primitive/version, token, side, p, generation, root_id, B, delta, drop_event_ref, pre_quantity_ref, t0, first_return_ref, tr, e, pre_drop_H, total_formation_H, terminal_quantity_ref, c, d, F, guard_phase, quality, end_reason`。仅保留有限证据引用，不复制全量盘口日志。

需要明确原档前后量、内部输入顺序、直接成交身份/方向/覆盖、received/available/source、水位及连续数量链。无法区分20ms保持或200ms首达期限时不能插值；旧RF布尔值、最终快照或默认BUY记录都不够。自有挂量如何处理须在输入合同中冻结，聚合量变化不能识别外部参与者。

参见[输入能力](INPUT_CAPABILITIES.md)、[零成交证据](ABSENCE_EVIDENCE_REVIEW.md)、[时间字段边界](TRADE_TIMING_CAPABILITIES.md)。参数均未校准，未实现检测器或适配器，未运行测试、回测、模拟或采集，未验证频次、PnL、trim3/trim5或结算概率优势。不新增M编号。
