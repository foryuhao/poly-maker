# 加托与受卖顺序的输入审查

日期：2026-11-11。适用[M23 加托扛卖](M23.md) v1。只读源码及SOP，未读取原始行情、运行测试或修改适配器。以下补清现有规则的来源要求与派生约束，不改变M23或COMMON参数，不新增信号编号。

## 1. 本地先观察到，不等于市场上先发生

M23的“先加托、后受卖”依赖冻结的本地输入顺序：先观察明确的Bid数量增加，形成100ms种子，随后观察符合新阶段条件的直接SELL。received定义统计窗口，available及输入键定义当时可采取的行动；source用于原档前后数量的来源核验。三个字段各有用途，不能用后到的源时间排序重写已经结束的root。

即使source字段不倒退，也没有据此证明深度增加、成交及发布的交易所内部因果顺序。没有明确接口合同，source相等不能借本地行号升格为严格的市场先后。M23的解释应是“可观察的新增缓冲经过后续受卖验证”，不能说新增资金预知卖盘、同一参与者提前托盘或新增订单已被卖盘消耗。

## 2. 源码中的能力边界

| 已读位置 | 当前代码行为 | M23需要保留的区别 |
|---|---|---|
| [feed.py](../../research/maker_signals/feed.py) `apply` | `subindex`取外层rows的enumerate位置；每个price_change行再按token筛选内部变化 | 外层行号不等于内部每次P改量的身份。单token原始内部顺序可以从完整原文辨认，不能从一个行号假造多个来源事件 |
| 同文件 `_depth` | 逐项赋值并触发depth观察；全部变化完成后才更新全书received/source并发布整行深度结果 | 观察回调不保证携带本次P数量事件的来源。不能在回调里拿全书旧source，或在整行结束后只取最终P量，声称已保存每次增量 |
| [model.py](../../research/maker_signals/model.py) `Book` | bid/ask字典存数量，received/source是全书字段，没有逐价位来源映射 | 最终Book状态不足以恢复P增量前量、增量事件及末笔SELL后的明确P报量 |
| `feed.py::_trade` | 原始side通过索引读取；直接/镜像共享去重集合，已见身份可不再发NORMALIZED_TRADE | 直接SELL存在性必须在来源丢失前核对，不能从筛选后的零记录判断种子通过 |
| [parse.py](../../src/polymaker/marketdata/parse.py) `parse_last_trade` | `msg.get("side", "BUY")`使缺失side默认BUY；缺失或无法解析timestamp可变成0 | 下游一个BUY不能证明原始方向字段存在。对M23的零SELL区间，缺失方向/源时间应按原质量规则UNKNOWN，不能借默认值确认“不是SELL” |
| [service.py](../../src/polymaker/marketdata/service.py) 与 [capture.py](../../src/polymaker/research/capture.py) | TRADE payload含token、side、price、size，未在该调用携带transaction_hash/原始帧身份；DELTA逐项emit，source_ref为None | 本地event_seq能排序已发出的行，不能单独证明成交身份、未发出的原始行或批次完成边界；顶层monotonic_ns也不是原接收时刻 |

这些是本次源码阅读结果，不是实际录制缺字段、发生丢包或服务出错的结论。原始journal等其他输入可能保留所需信息，本轮未核验逐条关联合同。源文件hash另记在catalog本说明条目，不覆盖旧审查的阅读版本，也不证明运行实例已经加载。

同名side字段还应区分事件类型：price_change的BUY指Bid侧挂量变化；last_trade_price的SELL才是本策略计量的主动卖出。不能把增加Bid的BUY字段当成一笔主动BUY成交，或用深度SELL字段作为主动SELL存在性证据。

## 3. 内层事件身份不能由最终快照补造

纸面输入：P原量100，同一price_change行内，P按原数组顺序更新为150、再变为160。M23的首个合格增量是Δ=50，第二次增加不改B/Δ；若只保留最终160，就会误记Δ=60。两条变化可以共用外层received/source/seq/subindex，数值端点相同不代表事件身份相同。

在保留原文的输入上，可引用`原消息ID、外层行位置、内部change位置`作为明确的来源定位；或使用已经冻结、可证明等价且保留原序的唯一展开键。它们是来源定位，不提供虚构的毫秒间隔。COMMON的有序键在选用输入中必须能无歧义表达这层顺序，不能把旧Feed的外层subindex直接当成已经满足。

完整健康book快照仍作为一份状态，不能把内部赋值顺序拆成增量事件。如果只存最终book而没有两次改量的来源，不能反推原数组曾如何变化。这里没有要求新增逐tick日志；测试模块可以引用已有原始记录，不能重建时标UNAVAILABLE。

## 4. 同批次先SELL还是先减量，会得到不同结果

纯规则例：B=100、Δ=50、Fpre=140、F=110，种子cS=110ms。一个received=150ms、available=151ms的原始批次包含“直接SELL 30份”和“P量变120”；两行source相等，初始P量150有更早source。另有窗口内更晚source的明确P量120，满足末端来源；其他质量、末段与时间条件均假设成立。

| 原始行顺序 | M23 v1处理 |
|---|---|
| SELL，再P=120 | 首笔符合新阶段条件，先切F=110；随后120不破F，验证继续 |
| P=120，再SELL | 先违反Fpre=140，root立即结束；后行SELL不能追认切换底线 |

批次结束才发布候选，不意味着允许按批次末态忽略中间失效；也不能为了得到一致结果重排为SELL优先。这是v1选择的输入规则带来的样本差别，不是交易所内部顺序的证明。只知道两行同source或同received而不知原序时，不能选对自己有利的一种。

末端来源另有独立要求：若最后SELL与最后明确P报量source相等，哪怕P行在本地更晚，也不满足本版“数量source严格晚于全部计量SELL”。需要原500ms窗口内一份更晚source的P数量证据；只有其他档更新、BBO刷新或窗外补报都不够。等待后来的证据不能延长窗口或提前确认。

## 5. 后续增厚不重置Δ，也不会把验证量门槛变低

设首笔SELL前量为`Q0R=B+kΔ`。前置底线已要求k>=0.80；末段上界为`Qend<=B+0.50Δ`，且QminR<=Qend。因此原规则还推出：

`L >= max(0.50,k-0.50)*Δ`，以及`H >= max(5,0.50Δ,(k-0.50)Δ)`。

例如B=100、Δ=50，在首笔SELL前继续增厚到200，则k=2；末段最多125，故L至少75且H至少75。不能仍只卖25份就通过，也不能重设基线为150来缩小损失。本例只联立现有数量条件，不添加门槛或预测频次。

这也解释为何一次增厚种子不等于持续加厚越多越容易通过：后续厚度会增加形成所需成交与回落，也可能增加Maker前置队列。形成、准入、排队成交和最终结算分别评价，不能把其中一步的改善当成完整交易结果。

## 6. 交给测试模块的能力判定

- 仅有NORMALIZED_TRADE及最终Book：对上述逐档来源、直接存在性与批次合同，**不能从当前结构证明完整支持**。
- 已有原始消息且包含必需方向、数量、时间、身份和原序：属于**需要原始重建及输入合同核对**，不自动等于SUPPORTED。
- 只有该service→capture调用产生的TRADE/DELTA行，且没有可关联的原始来源：缺失字段不能靠event_seq、默认BUY、同价同量或timestamp补造。

建议仅补有限来源引用与阶段失败原因：`raw_direction_present, raw_timestamp_present, raw_parent_ref, outer_row_index, depth_change_index, first_qualifying_add_ref, pre_sell_quantity_ref, terminal_quantity_ref, pre_guard_breach_key, first_sell_key`。这份说明不实现字段采集，不要求额外完整日志，也不改变现有旧信号行为。

M23保持v1、SPEC_ONLY、研究第12顺位。参数未改；若将来选择批次末态、放宽严格source比较或用默认方向填缺失，都会改变定义，应单独版本化，不能作为本版无声的兼容处理。当前仍无真实Maker频次、收益或结算概率提升结论。
