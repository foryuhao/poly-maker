# SR 短补回落

版本`sr-short-replenishment-reversal-v1`，**SPEC_ONLY**。这是新增的盘口路径假说：某档明显增厚，短暂可见后又回到接近原量，期间实际打单不足以解释大部分回落。它不识别同一订单、撤单主体、欺骗意图或冰山单，也不单独给出买卖方向。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "SR",
  "version": "sr-short-replenishment-reversal-v1",
  "status": "SPEC_ONLY",
  "origin": "NEW_VISIBLE_PATH_HYPOTHESIS",
  "sides": ["BID", "ASK"],
  "data_clock_contract": "maker-discovery-common-v1",
  "inherits_execution_contract": false,
  "direct_consuming_trades_only": true,
  "minimum_net_add_shares": "5",
  "minimum_net_add_fraction": "0.20",
  "initial_visible_hold_ms": 20,
  "initial_retained_add_fraction": "0.80",
  "first_return_before_ms": 200,
  "first_return_available_before_deadline": true,
  "return_retained_add_fraction_max": "0.20",
  "return_baseline_fraction_min": "0.90",
  "return_hold_ms": 50,
  "pre_add_trade_buffer_ms": 500,
  "formation_hit_to_add_max": "0.25",
  "confirmation_lateness_max_ms": 50,
  "explicit_terminal_quantity_after_all_hits_required": true,
  "publication_requires_complete_original_batch": true,
  "current_ttl_ms": 750,
  "current_retained_add_fraction_max": "0.50",
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 1. 对象与初始增厚

按[COMMON](COMMON.md)的数据、时钟、质量及原档规则处理；不继承其中RF形成、支撑floor或Maker执行。BID侧计直接SELL，ASK侧计直接BUY，称为该档的“打单”；深度消息的BUY/SELL字段只是挂单侧，不是主动成交。

同token/side空闲时，第一份明确报量的健康事件使更新前最佳价档p从B>0增到B+Δ，且`Δ>=max(5,0.20B)`，开启root。冻结p、B、Δ、前后来源、原始内层事件位置和generation，t0为增量事件received。新出现价档不作为原档增厚；后续新增不重设Δ或t0。不用全书时间戳或其他档回调代替本档事件。

从t0起，原档必须存在、输入链连续。`[t0,t0+20ms)`内数量须始终`Q(p)>=B+0.80Δ`。这20ms是本地观察规则，不能用同批行号插值出时间；已知在20ms前跌破即失败，不选第二个尖峰重开同root。

## 2. 第一次回落与低量保持

20ms初始留存完成后，选择第一份明确p数量事件满足`Q(p)<=B+0.20Δ`。其received记为tr，要求`tr>=t0+20ms`，received及available均严格早于`t0+200ms`，来源时间严格晚于初始增量事件。

这个首达事件还须`Q(p)>=0.90B`，否则结束为`RETURN_OVERSHOT_BASELINE`；不能跳过更深的首次回落，后来恢复再挑一个更漂亮端点。200ms到期无合格回落即结束。原档消失、顺序无法解释或来源缺失照质量规则失败/UNKNOWN。

冻结tr，不移动首达时点。随后在`[tr,e)`、`e=tr+50ms`连续保持：

`0.90B<=Q(p)<=B+0.20Δ`。

每次原始数量事件都检查，任一越界结束，不重新计50ms。完整健康book是一份状态；price_change按原数组顺序逐项处理。批次后发布不允许忽略批内已知失败，详见[深度批次语意](DEPTH_BATCH_SEMANTICS.md)。

## 3. 打单量与来源限制

统计原token、原档p、消耗该侧的去重直接成交H，received区间为`[t0-500ms,e)`，须`H<=0.25Δ`。这包含增厚前500ms缓冲，减少把较早成交对应的晚到减量误作短补回落；其中成交可能已经反映在B中，因此只是保守扣量，不是精确归因。覆盖须包括这500ms历史；已知错位无法由所选合同处理时仍为UNKNOWN，500ms不保证交易所延迟上界。

H可以为零，但仅在所选输入合同能证明覆盖、方向和过滤范围时成立；深度/BBO活跃不能单独证明成交完整。初始化时已有缓冲量超过预算，就没有本root的可行空间，不截短历史凑通过。

在首次回落及低量保持阶段，数量较初始增厚峰至少回落0.80Δ，而整个形成窗打单至多0.25Δ。两项差至少0.55Δ是给定观察量的算术结果，**不是精确撤单量**。若消息跨边界错位、遗漏成交或来源顺序不明，则UNKNOWN，不能把少记录解释成撤掉的挂单。参考[输入能力](INPUT_CAPABILITIES.md)及[加托顺序审查](PRELOAD_SEQUENCE_REVIEW.md)。

只要当时可得的H已超过上限，结束root，不删除某些成交以保留资格。最终须有明确p数量事件，输入键和source均严格晚于所有计量打单，received仍在`[t0,e)`内且关闭时可得；没有打单时，首次回落来源及连续深度链仍必需。BBO或其他档更新不是末端p证据。

H不要求达到RF/AA的受击下限。因此SR本身不能称为“承接成功”或“经真实冲击证实防守失败”；它测量短驻增量的可见留存，需要与真实受击原语组合才有更完整交易解释。

## 4. 确认、当前有效期与失效

水位越过e且当前原始输入批次完成后确认于c，要求`e<=c<=e+50ms`。不能早于50ms低量保持结束确认。形成条件按完整已知形成窗核对，封窗后迟到按UNKNOWN处理，不回填候选。

从e起切到当前态上界`U=B+0.50Δ`；发布前审查e至c的全部已知数量路径，之后持续要求Q(p)<=U、原档存在、质量/generation/tick有效。超过U表示大部分新增厚度重新出现，结束；等于U仍有效。形成下界0.90B仅用于识别“回到原量附近”，确认交接及当前态允许继续减少，但原档消失仍结束。

`deadline=c+750ms`，到期等号失效。新打单received>=e不再加入形成H，也不自动否定过去的短补模式，但当前数量和质量仍受检查；不能用其替换形成期漏掉的成交。新增厚、新事实、引用或下单回执均不续期。

同token/side同时只保留一个形成或当前root。结束后须有received与输入键均晚于结束的全新增量事件才能另开，旧尖峰不可重复使用。连续多个SR若作为历史序列，必须明确不同事件与阶段；已过期前项不能再当当前态重叠。

## 5. 纸面例、查重与组合

例：B=100、Δ=20，量增到120；前20ms始终至少116。80ms收到量102，持续至130ms，H=0且成交覆盖与来源可信；140ms可得确认，上界U=110，截止890ms。数字有解，不是市场观察或策略测试。若H=6则超过5的上限；若首次回落为85或低量保持中反弹到105，则本root失败。

与LW区别：SR从增厚前的B出发，结束时可以仍高于B，例如102；并没有相对该原基线的净撤退。LW通常衡量固定近端集合的净减少。若另从SR峰值建立LW，二者可能共享回落事件，不能当两份独立证据。

与DF/AA/RF区别：SR不需要历史成功防守、真实受击低谷或稳定恢复，观察的是增量短驻与回落。这个新观察仍可能没有预测价值；报价更新或流量不足也能产生它。

- `SR_ASK + QR_BID/RF_BID`：供给补出但留不住，同时需求承接仍在，可作为原Bid Maker假说。
- `SR_BID + CA`：反击同时伴随买侧短驻增量，表达支撑存疑，不自动给出买入许可。
- `SR_ASK + LW_ASK`：先检查原始回落是否相同；不能把同一净变化重复加分。

以上只是组合方向，没有新M策略或执行路由。750ms原语寿命可能不足以等待Maker；组合必须检查所有组件当前有效、原锚合法及最短发送余量。

[组合审查](PRIMITIVE_COMPOSITION_REVIEW.md)进一步推导：SR确认后才启动完整QR时，两者不可能当前重叠；QR从SR增厚时点才开始，也不能满足COMMON的400ms首次提交余量。应研究已有或更早开始的QR，而不是给原SR续期。SR前置增厚还可能先使旧弱供失效，不能只看回落后的末态。这里不改变本原语版本或参数。

## 6. 输出与数据支持

记录`primitive/version, token, side, anchor, generation, root_id, B, delta, add_event_ref, t0, first_return_ref, tr, evidence_end, pre_add_H, total_hit_budget_used, terminal_quantity_ref, confirmed_available, deadline, current_upper, quality, end_reason`。自有挂撤的处理须按输入合同冻结，不能从聚合公开变化推断外部参与者意图。

需要明确原档前后数量、原始内层顺序、直接成交身份/方向/覆盖、双时钟和来源时间。接收时钟若不能分辨20ms留存及200ms期限，不得自行插值。只靠旧RF摘要、最终快照或缺方向却默认BUY的记录不能重建。所有数值为待测试的初始定义；未验证频次、Maker收益或结算概率，未实现适配器。
