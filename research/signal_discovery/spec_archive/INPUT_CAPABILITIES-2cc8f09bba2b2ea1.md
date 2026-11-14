# 新信号的数据能力说明

记录日期：2026-11-11。用途是SOP交接，不是行情质量或模拟器的验收报告。本次只读了字段与源码，没有扫描录制、运行测试或改动适配器。

## 已核对的接口事实

官方公开流区分订单簿与成交消息。book和price_change给出价档数量；last_trade_price给出成交方向与价格等字段。统一SDK文档允许部分成交size、timestamp、transactionHash缺失，不能因接口存在就假定每条数据齐全。[官方Market Channel](https://docs.polymarket.com/api-reference/wss/market)，[官方Real-Time Data](https://docs.polymarket.com/market-data/realtime-data)

这些文档本次查看的字段中没有给出book与trade逐笔共同撮合序号；故不能仅以两个消息时间接近，证明它们是一次成交前后的精确库存。这是对所读接口的能力边界判断，不宣称所有平台数据接口都缺这项能力。

## 工作区中的三个交接陷阱

| 项目 | 读到的实现 | 对新SOP的影响 |
|---|---|---|
| 直接成交与镜像 | `feed.py::_trade` 先生成直接/互补视图，按经济视图身份去重；先来的镜像可占用后来的直接视图身份 | 新版要求直接流时，不能仅从已去重的NORMALIZED_TRADE筛选mirrored=false；需原始来源或等价来源集合 |
| 时间基准 | `Round.qualified_at`取context.now；hold_start使用received；关闭摘要导出qualified_available_at | M16不能直接相减，需要同接收时钟的完整事件路径和最终保持段 |
| 深度数量 | `feed.py::_depth`把price_change.size作为该价新的总量赋值 | 不能把它累计成“新增挂量”；只有前后差才是可见净变化，仍不能分开逐单挂入/撤出 |

源码：[feed.py](../../research/maker_signals/feed.py)、[primitives.py](../../research/maker_signals/primitives.py)、[model.py](../../research/maker_signals/model.py)。目录中保留的源码hash只标识阅读版本，不证明任何正在运行的服务已加载该版本。

镜像去重例（解释代码行为，不是数据测试）：如果某token的SELL经济视图先由互补token的BUY生成，后来的直接SELL按相同交易/时间/数量身份可能被忽略。去掉mirrored=true后，早先那个视图也被删掉，结果会漏掉直接流。必须在来源信息尚未丢失的阶段定义“仅直接成交”。

## 新SOP所需能力

| 信号 | 必需的额外信息 | 只有旧轮摘要时 |
|---|---|---|
| M12/M14 | 两侧原档真实成交、逐次减损/恢复、严格末尾保持、冻结供给恢复界限 | 原hold滞回与新严格保持不同，不能直接复用stable |
| M13/M15 | 原LW固定集合、形成前500ms到整个root终点的累计BUY、全程guard、新受击资格键 | 不能只从3秒滚动缓存重算历史累计量；不得把自然TTL与硬失效混为一谈 |
| M16 | 同时钟受击时刻、最终低谷、最终连续保持段、跨越前后帧、时钟量化 | qualified_available_at与hold_start不足以证明恢复提速 |
| M17 | 原始直接SELL身份/量、前置P数量、各笔成交后更晚的同档数量、完整中间底线 | H加末量不能证明已及时更新，也不能证明新增买量 |

测试模块应分别标记 `SUPPORTED_BY_INPUT_CONTRACT`、`REQUIRES_RAW_RECONSTRUCTION`、`UNAVAILABLE`。这些是逐输入版本的能力标签，不能从SOP存在推导“已经支持”。未知事件顺序、缺失所需字段、镜像重复归因和不完整质量覆盖均应显式保留。

## 价格角色与计算边界

允许用价格确定同一档、Bid/Ask侧、tick合法性、post-only、数量预算和结果分层。来源核验不应悄悄引入价格推进或外部BTC价格来补方向。只有L2时不把净量留存称为同一订单/参与者留存，也不把公开成交总量直接当成策略订单成交量。
