# QO 挂量回摆

版本`qo-bounded-depth-oscillation-v1`，**SPEC_ONLY**。原价档挂量始终在初量上下5%内，但三个固定250ms节点的数量变化连续两次反向，累计变化至少初量20%；规定历史及形成、当前期内没有该档消耗方向的直接成交。它记录**少量净变化背后的可见数量回摆**，不是消息条数、精确撤单率、参与者身份或“虚挂”的证明。

本原语只提供上下文，BID/ASK对称且本身无看多/看空含义。不将它直接升级为Maker策略，也不声称它比SR、LW等具有统计独立的预测信息。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "QO",
  "version": "qo-bounded-depth-oscillation-v1",
  "status": "SPEC_ONLY",
  "origin": "NEW_FIXED_GRID_BOUNDED_QUANTITY_PATH_HYPOTHESIS",
  "research_role": "CONTEXT_PRIMITIVE_NO_DIRECTIONAL_EDGE_CLAIM",
  "sides": ["BID", "ASK"],
  "side_means": "PASSIVE_SIDE_OF_FIXED_OSCILLATING_LEVEL",
  "data_clock_contract": "maker-discovery-common-v1",
  "inherits_RF_contract": false,
  "inherits_execution_contract": false,
  "root_trigger": "FIRST_MATERIAL_ORIGINAL_BATCH_ENDPOINT_CHANGE_AT_PRE_BATCH_BEST_LEVEL",
  "minimum_change_shares": "1",
  "minimum_change_fraction": "0.03",
  "root_trigger_and_each_grid_step_use_same_change_threshold": true,
  "fixed_initial_quantity_band_fraction": "0.05",
  "band_guard_from": "OPENING_ORIGINAL_BATCH_INCLUSIVE_THROUGH_CURRENT_FACT_END",
  "target_consuming_trade_lookback_ms": 500,
  "target_consuming_trades_max": "0",
  "zero_target_trade_until": "END_OF_CURRENT_FACT_INCLUSIVE_OF_PUBLICATION_INPUT",
  "grid_step_ms": 250,
  "grid_steps": 3,
  "formation_window_ms": 750,
  "each_grid_bucket_requires_explicit_target_quantity": true,
  "grid_sample": "LEFT_LIMIT_AT_GRID_END_USING_LAST_EXPLICIT_TARGET_QUANTITY_IN_BUCKET_FROM_COMPLETED_ORIGINAL_BATCH",
  "sample_source_strictly_increases": true,
  "same_original_batch_cannot_supply_multiple_grid_buckets": true,
  "grid_step_absolute_change_minimum_required": true,
  "consecutive_grid_steps_must_alternate_sign": true,
  "grid_total_variation_to_initial_quantity_min": "0.20",
  "checkpoint_lateness_max_ms": 50,
  "checkpoint_policy": "FIRST_COMPLETE_BOUNDARY_NO_RETRY_OR_SLIDING_START",
  "current_quantity_band_unchanged": true,
  "current_ttl_ms": 750,
  "new_target_consuming_trade_ends_current_fact": true,
  "new_oscillation_renews_deadline": false,
  "one_forming_or_current_root_per_token_side": true,
  "new_root_received_and_key_after_prior_end": true,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 1. 固定档位与输入单位

继承[COMMON](COMMON.md)的数据、时钟、质量和原档约定，不继承RF或执行。价格只标识档位，BID看原Bid数量、ASK看原Ask数量；不使用价格推进、价差方向、BTC价格或结算信息。信号数量视图须随输入合同冻结，不能用合成订单的插入/撤销制造回摆。

原始输入批次必须有可辨认身份、统一的原received、完整结束边界和可保留的内部顺序。完整健康book快照是一份完整状态，不把清空字典等内部赋值步骤当更新。增量数组仍按原顺序检查已知失效，但不把每个子项分别计成一次回摆。缺父批次关系或只有拆散的DELTA行，不能自行拼出本规格的批次身份，能力为UNAVAILABLE。

每token/side空闲时，关注批次前已存在的该侧best档P。取**首个完整原始批次**：它明确更新P，且批次末P数量相对批次前数量B的绝对变化达到`G=max(1,0.03B)`，以此开启root。t0为此批次原received；P、B、generation、批次前明确数量来源及开启批次身份冻结。

B必须来自批次之前明确携带P数量的输入，输入键更早、received不晚于t0，source严格早于开启批次的末端P报量来源。首次实际处理到完整批次时才能作开启判断；不能把t0误当当时已可下单的确认。若批次末P消失，或任何已知P中间状态越过第2节带宽，root失败，不能只取批次末已回到带内的量。

这里以批次端点决定开启、以逐项已知路径检查失效，二者用途不同；不能改成“一个批次只看末态”。全书/BBO更新时间不能替代明确P报量。原档、generation/tick、覆盖或顺序失效均结束，后来最优档变化不换P。

## 2. 全程小幅带宽与零直接消耗

从开启批次包含的第一个P状态起，到形成、发布和当前期结束，始终要求：

`0.95B<=Q(P)<=1.05B`，P保持正量，原档及质量合格。

等号允许；任何已知越界立即结束，后来恢复不复活。因为初次改变也须留在带内，必要可行条件是`G<=0.05B`，即本门槛下B至少20份；数量精度还可能进一步限制可行性。不得为通过条件另选B或静默缩小G。

目标P的消耗方向为BID侧SELL、ASK侧BUY。直接成交存在性在原始来源被镜像去重/过滤丢失前检查，规定区间从`t0-500ms`开始，覆盖整个形成、各检查点交接及当前期。这个区间内一笔合格的目标消耗成交即结束，无论它是否使可见量减少。相反方向/其他价档不用于该零成交条件，但仍需来源与质量可解释。

没有消息或过滤后空集不足以证明零成交；缺历史覆盖标UNKNOWN。晚处理的旧received若属于规定区间，在发现时结束并保留所有先前暴露，不倒改确认或撤单；来源/接收归属未知不能补零。500ms只是本观察的历史排除选择，不是网络延迟上界。

## 3. 三个固定网格节点

冻结`e0=t0`，`ei=t0+i*250ms`，i=1,2,3，形成终点`e=e3`。不滚动起点，不挑事后更整齐的窗口。设`q0=B`。

每个桶`[e(i-1),ei)`必须有至少一份明确P数量更新。样本qi取截至ei左侧、按原输入合同得到的最后P数量状态，其来源必须是该桶中明确P报量，所属原始批次已完整处理；不能用ei及之后的更新补前桶，也不能用PONG/BBO或其他档更新时间补量。原始批次共用received时只属于一个桶，处理耗时跨越多个节点也不拆成多桶证据。

相邻q0/q1/q2/q3的明确数量来源source须严格递增，输入键也递增。只有本地处理顺序而源时间相同，不足以通过该来源门槛。这里的source检查只要求可观察先后，不证明精确撮合时刻或不同参与者。

水位过ei后，在**首个完整批次/相应计时器边界**ci检查，要求`ei<=ci<=ei+50ms`。先处理该边界全部已知失效，再冻结qi及其来源；超时、缺样本、来源不明或前缀失败即结束，不等下一次更好数量后重试。已经关闭桶里的迟到输入按原阶段处理，不能改写qi救活当前root。

从ei到ci的已知路径仍守原带宽、零目标成交与质量。qi是ei左侧的封窗样本，不是ci快照；即使交接时数量不同，也不能用ci的新数量回填前桶。三次节点约250ms分开，使原始消息被拆成更多回调本身不增加节点数；传输延迟、接收节奏和网格相位仍可能改变观察，本规格不声称对它们不敏感。

## 4. 回摆确认公式

定义三个固定节点变化`gi=qi-q(i-1)`。每个节点都要求`abs(gi)>=G`；第二和第三节点还要求：

`g1*g2<0`，`g2*g3<0`。

即两次明确的方向反转，允许“增—减—增”或“减—增—减”。开启批次只选定root，g1方向按第一个固定节点决定，不把桶内每次小跳动累计成额外反转。

第三节点须另满足网格总变差：

`V=abs(g1)+abs(g2)+abs(g3)>=0.20B`。

V是三个节点之间的数量路径长度，不是累计撤单量、累计新增订单量或消息数。全程带宽保证最终净变化`abs(q3-B)<=0.05B`。这两个量分别记录，不能把相同端点解释为期间没有变化。

带宽同时给出`V<=0.25B`：第一步最多0.05B，后两步各最多0.10B。所选20%门槛低于这个上界，不强制每个节点恰好取带宽边界；但它是较窄的一段形状选择，实际频次须另测。不能把总变差和它派生的较大区间跨度重复算成独立证据。

所有检查通过后，在第三个首次检查点发布`c=c3`。发布点当前数量和整条交接路径仍守±5%带宽、零目标成交和质量，不额外要求当前数量等于封窗q3。理论上`750ms<=c-t0<=800ms`；前500ms覆盖不是额外等待的加法，也不是已测确认耗时分布。

## 5. 当前事实、失效与重新开始

当前事实含义是：“最近这段窗口已出现规定回摆，随后原档仍在小幅区间且没有观察到目标消耗成交”。它**不要求确认后继续每250ms反转**，也不宣称此刻仍在不断换挂。原截止`d=c+750ms`，等号到期。

数量越界、目标消耗成交、原档/质量/generation/tick失效、历史反证或d到期均结束。新回摆、新报量、引用和回执不续期，不把后来静止当成重新确认。QO结束只表示这份有限事实不再当前有效；它不等于挂量变稳定，也不自动生成相反方向信号。

每token/side一个形成或当前root。失败/到期后，只有received及输入键均晚于结束的新完整批次，才可作为下一root的首次材料性端点变化。不能从失败窗口内部另选起点；同批结束后不能复用该批重新开启。这个生命周期可能漏掉其他相位的回摆，是定义选择而非对市场不存在该模式的断言。

## 6. 纸面例、反例和输入代价

例：B=100，G=3；初量来源在开启批次之前，前500ms无目标消耗成交。t0=0批次将P从100改为104，桶1最后明确量104；300ms的新批次给出96，桶2最后量96；600ms的新批次给出104，桶3最后量104。所有中间已知状态在95至105内，三份source严格递增、质量和零成交覆盖完整。c1=260、c2=510、c3=760ms均为各自首次完整检查点，则：

`(g1,g2,g3)=(4,-8,8)`，`V=20`，净变化4；可以确认，截止1510ms。没有使用行情、撮合或结算结果。

若数量精度允许，`100→104.5→95.5→104.5`给出`V=22.5`，各节点都在带宽内部、总变差也严格超过20%；因此并非只能同时卡在数量边界上成立。这仍是纸面路径，不代表市场频次。

反例包括：一次原始批次内的104→96→104被拆成三个跨时回调；桶2无P明确报量；某一中间状态到106后又回104；第二节点仍104导致g2=0；source相同；同档出现任何消耗方向直接成交。上述路径不能靠发布快照或更多回调数补足。快速回摆若在每个桶内都回到同一数值，也可能不通过本规格；“无QO”不等于没有回摆。

若G因份数精度大于带内允许步幅，直接不可行。若录制只有每个网格末态而没有连续深度链，无法检验带宽全程未破；若只有逐项DELTA且无父批次，无法恢复本版开启/封桶身份。现有字段路径只能提示潜在支持，不能据此宣布实际录制已经可用。

## 7. 与已有原语的重叠和组合边界

| 对照 | 不同观察与合并边界 |
|---|---|
| SR短补回落 | SR先有至少20%的单次加量，再快速回落；本例所有量在95至105，任意两已知量的增幅最多约10.53%，不够SR20%。不同历史窗口仍可能关联，不能声称统计独立 |
| LW疑似退量 | LW看固定三档净减与打单解释差；QO单档有反转而净差小，不推出三档撤供。后两档的LW可与本原档小幅回摆共存 |
| QR、BA、RF、RD | QO规定区间零目标消耗；它没有新受击、恢复或耐受事实。更早的历史支撑可仍有效，但不能将这段回摆当作一轮新的承接 |
| XR/XW | 它们要求另一侧攻击后的单档材料性净增/减；QO不要求另一侧进攻，关注固定节点的双反转，不把“无进攻”作为条件 |
| AP/EX | 本页不统计成交条数、进攻分布或衰减；固定网格只描述深度量路径，不能借节点数称多名参与者 |

若QO已经当前有效，又从同一P的带内数量b开启新XR/XW，则`0.95B<=b<=1.05B`。新XR至少20%净增会达到`1.20b>=1.14B>1.05B`；新XW至少20%净减会达到`0.80b<=0.84B<0.95B`。所以在这条明确先后路径上，它们达到材料性数量门槛之前/当时就已结束旧QO，不能随后再叫两个**当前事实**同时确认。同理，同档新SR的20%增厚会越过QO带宽。不同开始顺序、基线或不同价档要另查，不能作全市场互斥结论。

QO_BID也不能直接充当“成交时仍满足零SELL”的Maker买入硬守卫：驱动原P成交的SELL本身会结束该零成交状态。真实/模型成交与失效的先后仍由冻结执行合同记录，竞态成交全部保留，不能为维持解释而删除。较合理的待研究入口是`QO_BID历史 → 首次新SELL后的QR/承接验证 → Maker`；需要在新SELL处明确历史交接、后续资格和新截止。现已有[M17回摆后扛卖](M17.md)的显式备选`m17-qo-then-retained-sell-v1`写成完整SOP；本页的泛化箭头不替代该分支的首次事件、独立窗口、一次准入和原截止，也不提供自动执行权限。

`既有S_BID + QO_ASK`则可同时表达“需求侧有历史承接、供给数量近期回摆”，但QO_ASK没有净撤供或受BUY后恢复弱的证据，不能直接替代DF、LW或SR。它优先作为上下文分层/机制诊断，需另证是否有可用方向信息，而非默认越回摆越利多。

## 8. 输出与交付边界

输出`primitive/version, token, side, P, generation, root_id, opening_original_batch_ref, t0, B, B_source_ref, G, band_low, band_high, grid_endpoints, grid_confirmation_keys, q0_q1_q2_q3, sample_quantity_refs, sample_source_times, signed_grid_steps, V, terminal_net_change, direct_presence_contract, zero_trade_interval, c, d, phase, end_reason`。保留有限引用与摘要即可，不复制逐tick日志。

依赖原始批次、数量及直接成交覆盖、received/available/source、明确P报量与水位。参见[输入能力](INPUT_CAPABILITIES.md)、[批次语意](DEPTH_BATCH_SEMANTICS.md)、[零成交证据](ABSENCE_EVIDENCE_REVIEW.md)和[时间字段边界](TRADE_TIMING_CAPABILITIES.md)。本页选择的是输入中可观察的有界回摆，不把来源时钟或缺流保证扩大成对市场内部全部订单的知识。

未实现检测器、模拟适配器或执行，未运行信号测试、回测、模拟或采集。所有门槛是未校准的初始规格；未验证频次、Maker收益、trim3/trim5或结算概率优势，不新增M策略编号。
