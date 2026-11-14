# XR 攻后跟挂

版本`xr-cross-side-following-liquidity-v1`，**SPEC_ONLY**。一侧真实主动成交先达到规模，另一侧此前平稳、未被消耗的原档随后净增挂量，并留存部分增量。它描述同token的跨侧时间顺序，不证明主动成交导致挂单、同一参与者跟单或知情交易。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "XR",
  "version": "xr-cross-side-following-liquidity-v1",
  "status": "SPEC_ONLY",
  "origin": "NEW_CROSS_SIDE_SEQUENCE_HYPOTHESIS",
  "sides": ["BID", "ASK"],
  "side_means": "PASSIVE_SIDE_RECEIVING_NET_ADDITION",
  "data_clock_contract": "maker-discovery-common-v1",
  "inherits_RF_contract": false,
  "inherits_execution_contract": false,
  "direct_trades_only": true,
  "pre_attack_target_flat_window_ms": 250,
  "target_flat_tolerance_fraction": "0.05",
  "target_flat_through_attack_qualification": true,
  "target_consuming_trade_lookback_ms": 500,
  "formation_target_consuming_trades_max": "0",
  "zero_target_trade_until": "FORMATION_END_E_EXCLUSIVE",
  "attack_qualification_before_ms": 250,
  "minimum_attack_shares": "5",
  "minimum_attack_to_initial_hit_level_depth": "0.10",
  "attack_volume_freezes_at_first_threshold_prefix": true,
  "qualification_available_before_deadline": true,
  "minimum_target_net_add_shares": "5",
  "minimum_target_net_add_fraction": "0.20",
  "net_add_received_and_input_key_after_attack_qualification": true,
  "first_net_add_before_qualification_plus_ms": 250,
  "net_add_available_before_deadline": true,
  "post_qualification_pre_add_target_floor_fraction": "0.95",
  "net_add_hold_ms": 100,
  "forming_retained_add_fraction_min": "0.80",
  "publication_retained_add_fraction_min": "0.80",
  "confirmation_lateness_max_ms": 50,
  "terminal_hit_and_target_quantities_after_all_frozen_attacks_required": true,
  "current_retained_add_fraction_min": "0.50",
  "current_quantity_guard_starts": "FORMATION_END_E_CHECKED_THROUGH_PUBLICATION",
  "current_ttl_ms": 3000,
  "hit_level_required_until": "PUBLICATION_ONLY_HISTORICAL_AFTERWARDS",
  "target_hit_recheck_from": "FORMATION_END_E",
  "target_hit_recheck_max_ms": 100,
  "pending_recheck_blocks_new_action": true,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 1. 侧别与固定对象

只继承[COMMON](COMMON.md)的数据、时钟、来源、原档及质量规则，不继承RF或自动执行。只计可辨认的直接成交；互补镜像不是第二份攻击证据。

| XR侧别 | 先发生的主动成交 | 受击原档X | 随后增量的目标原档P | 形成期禁止的P成交 | 方向含义 |
|---|---|---|---|---|---|
| XR_BID | BUY | 事件前best_ask | 事件前best_bid | SELL | 买入发生后，买侧另有可见净增量 |
| XR_ASK | SELL | 事件前best_bid | 事件前best_ask | BUY | 卖出发生后，卖侧另有可见净增量 |

BID/ASK指被增厚的挂单侧，不是成交字段。XR_ASK不是看多，也不自动授权卖空或在互补token发单。P、X在首笔主动成交之前冻结；后来最优价变化不换档，也不作为方向条件。

每token/目标side只保留一个形成或当前root。空闲时第一笔可信、位于事件前有效受击侧最优档的直接成交开启，t0为其received。冻结受击档初量QX>0、目标档初量B>0、两档原始数量来源和generation。两份初量必须明确报量、输入键在首笔前、received不晚于首笔、source严格更早，且连续链有效；全书时间戳、BBO或其他档更新不能替代。

## 2. 初始平稳与零受击合同

在`[t0-250ms,t0)`内，目标P的每个已知状态须满足`0.95B<=Q(P)<=1.05B`。从t0至第3节攻击资格发布边界cH，继续保持同一平稳带，不能先出现明显加量，再由后来成交追认“攻后跟挂”。前250ms覆盖、数量链或来源不足则UNKNOWN。

从`t0-500ms`到第5节形成终点e之前，目标P上消耗该侧的直接成交必须为零，即BID模式无SELL、ASK模式无BUY。完整区间为`[t0-500ms,e)`，左端包含、右端不包含。先检查原始直接来源，再进行可能丢失来源的镜像去重/过滤；只有零记录而无覆盖合同不能通过。

这500ms是排除近期受击回补混入的保守历史规则，不是交易所延迟上界或因果证明。没有记录不证明没有任何未观测交易；已知缺口标UNKNOWN。禁止的成交一经观察即结束；封窗后才发现旧received属于该禁止区间，在发现时结束并保留此前已发生的暴露，不回写历史撤单。

两原档从开启到发布必须持续存在。断线重建、generation/tick变化、不可解释顺序或覆盖缺口结束root，不用后来的档位或零值补齐。

## 3. 先确认有限的真实攻击量

只累计原X上该模式方向的去重直接成交，门槛`Hmin=max(5,0.10QX)`。从首笔开始，第一次累计达到门槛的成交前缀定义H，冻结其事件集合、末笔键和最大source；不能跳过早先成交挑一个更合适前缀。

在首次完整输入批次边界发布该资格于cH，要求计量成交received均在`[t0,t0+250ms)`，且`cH<t0+250ms`。资格发布前检查该批全部已知失效及目标平稳带。达到量门槛但批次处理到可得时已经到期，不获延期。到期无资格即结束。

H随后不再增加，也不要求攻击持续；这不是750ms AP、多参与者进攻或Ask恢复弱化。后续同方向成交属于新流量，不能用于回补原前缀的缺失记录。真实成交与来源完整性需成立，但没有用X的减损或恢复率推断防守强弱。

## 4. 资格之后的首次目标净增量

从cH之后到首次增量达标前，目标数量不得低于`0.95B`；允许逐步增量，初始平稳带的上限不再延续。取首个明确P数量事件使：

`Q(P)-B>=G`，`G=max(5,0.20B)`。

该事件received记为tA，要求received严格晚于cH，输入键严格晚于资格发布边界；received及available均严格早于`cH+250ms`，source严格晚于被冻结H的全部成交来源。源时间相等不能仅用数组行号证明“攻后”；资格同批内的增量不从received插值制造新阶段。

冻结`Delta=Q(P,tA)-B`，不因后来加量扩大Delta或重设B。这里是相对首笔攻击前B的**净增量**，可以由若干次更新累计达到；不同于M23/SR以某一次更新前后差定义的增量。达到门槛后不能另选更好的一次，不足/来源不明/超时均结束。

## 5. 增量留存、形成关闭与发布

令`e=tA+100ms`。在`[tA,e)`整个区间连续满足`Q(P)>=B+0.80Delta`。任何已知跌破都结束，不能重计100ms。第2节的零目标成交规则一直覆盖到e；真实主动成交发生在X，不等于P已经受击。

关闭前需明确携带X数量及P数量的末端证据，两者输入键及source均晚于被冻结H的所有成交，received仍早于e，发布时已可得。P末端来源不得早于tA的增量事件；该增量本身可提供合格P报量。两档证据可来自不同输入，不要求伪造同时更新；BBO和其他档回调不够。这里检验可观测先后，不宣称逐单撮合或精确反应延迟。

水位越过e，在首个完整批次/对应计时器边界检查发布，确认c须`e<=c<=e+50ms`。发布点当前P数量仍须`>=B+0.80Delta`；不足直接结束，不等后来恢复再发布。X此时也须存在且质量正常。

从e起，整条交接路径改守当前底线`F=B+0.50Delta`；不能只检查发布快照而忽略中间跌破F。发布点80%是额外检查，不要求整个交接区间连续80%。received仍在已关闭形成窗的晚处理事件不能改称新流量；依据其原阶段判定，在发现时结束为UNKNOWN或相应失败，不回填确认。

e及之后的新目标成交已不属于形成期的零成交预算，而进入第6节复核；监控不能等发布后才启动。如果发布时有尚未超时的待复核量，可记录已形成事实但标PENDING_RECHECK，不能据此首次确认新组合或发单。已超时则不发布。

由以上期限可推导`100ms<c-t0<650ms`；前置500ms历史不计作新增确认耗时。这只是定义的理论范围，不是测得的确认分布。所有数量阈值均为待测试初始规格，没有数据校准。

## 6. 当前事实、后续目标成交与失效

事实截止`d=c+3000ms`，等号到期。持续守目标P的固定F、原档、质量、generation/tick；不使用RF恢复退出。增量、后续攻击、引用或回执均不续期。

X在发布后只作为攻击历史参照；其后数量变化或该历史档消失，不单独结束XR，但仍执行全局book质量检查。不要将此历史X当作仍有效的弱供或当前守价档。若后来发现X在发布之前就已消失，须按旧阶段缺失处理，不能用“现在已是历史”绕过。

对每笔received>=e的P消耗方向直接成交j，冻结`Dj=received_j+100ms`。须有明确P数量事件：输入键在成交之后、source严格更晚、received不早于该成交，且received/available均严格早于Dj。相同source、其他档、BBO或PONG不能复核；一份合格更新可覆盖多笔较早成交。成交本身可得时已过Dj也不能重新给100ms。

未完成复核时`can_reference_for_new_action=false`，复核及时完成可恢复引用，但d不变。到Dj先处理超时；根事实截止更早则先到期。已知Q(P)<F不等复核即结束。组合若允许已发订单在原100ms预算内等待，必须事先写入其执行SOP；否则本原语本身没有授权订单继续暴露。

原语结束后须由received和输入键均晚于结束的全新攻击首笔另开，旧攻击、增量和窗口不得复用。一个root失败不会自动切换成BA、RF、QR或另一个XR。

## 7. 纸面例与查重

例：XR_BID，QX=100、B=100，前250ms目标量100，前500ms及形成期无P上的SELL。t0=0收到X上BUY=12；10ms完成资格cH，P仍100。50ms明确P=120，Delta=20，持有至150ms，160ms发布时仍120；期间X持续存在且有20ms的明确新报量，来源/键在BUY之后。则形成门槛有解，F=110、截止3160ms。没有使用市场数据或假设订单成交。

反例：资格前P已到110，超过初始105上限；第一达标加量来源不晚于BUY；留存阶段P跌到115；或者形成期出现任何P SELL，均不能确认。e之后P=112不因80%形成阈值而退出，但发布点若只有112则不发布；已经发布后的112可以维持当前F。

| 对照 | 明确区别及重叠边界 |
|---|---|
| BA / RF / QR | 它们在目标档要求受击及特定响应；XR形成期明确没有目标消耗成交，观察另一侧攻击之后的净加量。不同窗口仍可共享历史，不证明统计独立 |
| M20 | M20在原Bid真实SELL后检查三档前聚；XR_BID形成期无原Bid SELL，也不要求后两档减量。相同形成区间不可能同时满足这两类目标成交条件 |
| M21 | 同样涉及另一侧响应，但M21看SELL后未受BUY的Ask净减，XR_ASK看SELL后未受BUY的Ask净增；同基线、同一量状态的净减与净增不等价 |
| M23 | XR可以提供“加量先于后续受击”的研究种子，但冻结B/Delta、起点和确认不同；不能直接替换现有M23种子或继承其结果 |
| SR | 若同侧、同增量事件且B/Delta恰好相同，SR回落要求<=B+0.20Delta，低于XR当前F；回落发生在XR形成期也违反80%留存。两者不能在该同源路径上同时当前有效。不同基线或不同事件不能套这个互斥结论 |

XR不是“更多挂量必然更强”的声明。新增量可能只是短暂调整或延迟报价；公开L2不能识别谁挂的，也不能把净增加唯一分解成逐单行为。更多前置量还可能降低Maker成交率。

## 8. 组合研究入口与数据要求

- `XR_BID + DF_ASK`：买入之后买侧跟挂，而原Ask恢复转弱。须绑定DF版本、核对失效；同一BUY可能被两项使用，不能当两份独立攻击证据。
- `既有QR_BID + XR_BID`：历史受卖保量之后出现新跟挂。若QR最近一笔P SELL在l，则XR零成交历史至少要求`t0>l+500ms`；不能忽略这个间隔，把刚确认QR立即与XR拼接。各原截止仍取最早者。
- `XR_BID → 新SELL复验`：可研究M23类完整交易语意，但需要另写新阶段、底线切换和期限；本页不把未经受击的挂量称为承接成功。
- `CA + XR_BID`：若CA所用P SELL落在XR的500ms历史排除区间，组合不可形成；不因有主动BUY就把它们视为总能叠加。

以上尚非可执行策略，无自动Maker/Taker路由。需要完整原始方向及去重身份、两档前置/末端报量、250ms平稳与500ms成交历史覆盖、内层数组顺序、received/available/source、水位及逐笔复核。仅RF摘要、默认BUY方向或过滤后的零记录不足；已有字段路径不等于录制通过验收。参见[输入能力](INPUT_CAPABILITIES.md)、[零记录证据](ABSENCE_EVIDENCE_REVIEW.md)和[深度批次语意](DEPTH_BATCH_SEMANTICS.md)。

输出`primitive/version, token, target_side, attack_direction, generation, root_id, P, X, QX, B, t0, frozen_attack_event_refs, H, cH, qualification_key, first_net_add_ref, tA, Delta, e, target_zero_trade_interval, terminal_P_ref, terminal_X_ref, c, d, F, pending_rechecks, can_reference_for_new_action, phase, end_reason`。自有挂量与合成订单是否进入信号深度必须随输入合同冻结，不能凭聚合变化宣称外部资金行动。

未实现检测器或适配器，未运行测试，未验证频次、实际Maker收益、trim3/trim5或结算概率优势。归入原语工具箱，不增加M策略编号。
