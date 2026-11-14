# AP 持续进攻

本次仅补齐独立原语文档；原定义/实现存在不代表当前部署或已验证收益。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "AP",
  "version": "ap-legacy-pressure-750ms-v1",
  "status": "DOCUMENTED_FROM_SOURCE",
  "origin": "EXISTING_PRIMITIVE_DOCUMENTATION_NOT_NEW_EDGE",
  "source_profile": "research.maker_signals.primitives.Pressure / legacy-cal-16 numeric profile",
  "inherits_execution_contract": false,
  "directions": [
    "BUY",
    "SELL"
  ],
  "anchor_policy": "NO_SINGLE_PRICE_ANCHOR_FROZEN_OPPOSING_TOP3_DEPTH",
  "trade_view": "SOURCE_FEED_DIRECT_AND_VERIFIED_COMPLEMENT_VIEWS_NOT_INDEPENDENT",
  "window_ms": 750,
  "subwindow_ms": 250,
  "confirmation_lateness_max_ms": 50,
  "depth_levels_max": 3,
  "minimum_direction_shares": "1",
  "minimum_direction_depth_fraction": "0.0001",
  "minimum_direction_share": "0.65",
  "minimum_direction_records": 2,
  "minimum_occupied_subwindows": 2,
  "last_subwindow_required": true,
  "maximum_single_record_fraction": "0.95",
  "current_ttl_ms": 750,
  "recheck_interval_ms": 250,
  "exit_direction_share_below": "0.60",
  "exit_direction_minimum_fraction_below": "0.50",
  "exit_empty_last_subwindow": true,
  "reverse_ap_ends_current": true,
  "source_files": [
    "research/maker_signals/primitives.py",
    "research/maker_signals/model.py",
    "research/maker_signals/feed.py",
    "research/signals/LEGACY_COMMON.md"
  ],
  "source_sha256": {
    "research/maker_signals/primitives.py": "249c711a58b6c33516f4d9383e0d039566d56cf4924b1ee994ba8c4f961ac522",
    "research/maker_signals/model.py": "41a0e8e3c976cd447f58dbe99de2d3407756e60b89e42f163c41e0b3353d7b1e",
    "research/maker_signals/feed.py": "dc7eb44cdcdf2781c5d5d40cca7fa011e322072f1680b72d1d76db9da78795f0",
    "research/signals/LEGACY_COMMON.md": "e3857f08807717ceb24b9530c1a662bafe838b5907b3f2a2c72bcd56fdd0f2a4"
  },
  "implemented_by_this_task": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 定义与方向

AP_BUY / AP_SELL记录一个完整短窗口中的主动成交规模、占优程度与持续性。方向是主动成交方向，不是深度更新消息里的挂单侧。价格只用于识别对手档位和输入对齐，不要求价格推进。它可作为Maker组合的攻击背景，也可作为Taker组合的当前进攻证据，本原语不指定执行方式。

本文件主版摘录已有research Pressure数值和生命周期；不是2026-11-01最初80%份额的原型，也不代表当前部署状态。

## 开始、计算与确认

同token、同方向没有形成中候选且没有有效AP时，首笔该方向合格成交启动t0。冻结事件前对手侧最多三档总深度D：BUY看Ask，SELL看Bid；D必须大于0。使用完整盘口实际可见档位，不能把未知截断当成完整深度。AP没有一个需持续守住的单档锚。

在[t0,t0+750ms)累计该方向量V、反向量O、该方向记录数N，以及三个250ms桶的同向量v0/v1/v2。冻结最低量m=max(1,0.0001D)。

窗口封口且必要数据可得，在结束后最多50ms内确认，须同时满足：

- V≥m，V/(V+O)≥0.65，N≥2。
- 至少两个桶有同向成交，最后一桶必须有。
- 最大单条同向成交量≤0.95V。
- 每个250ms桶具有本token的有效输入覆盖；分母为0、缺流或身份不明不可用。

只能按启动时冻结的方向确认，不能封口后挑方向。条目数不代表订单数或交易者数。同一经济成交的互补视图不能提供两份独立确认。

## 当前态与失效

c为实际确认可得时间，deadline=c+750ms，等号到期。每250ms检查最近完整750ms：同向份额<0.60、同向量<m/2、最近250ms没有同向成交，任一成立结束；总成交量为0也结束。反向AP确认结束旧方向AP。数据质量、generation或所需输入连续性失效时标不可用。新成交不延长旧ID寿命。

主来源形成统计用右开窗口；维护Flow.volume(frontier=True)包含当前检查时刻的事件。复现时保留这个来源差别，不能用重新排序或回填改写过去事实。某些M策略可保留历史AP20秒，那是历史参考期限，不是本AP仍有效。

## 时序、例子与版本边界

理论从t0到c为750–800ms；前期观察数据不是确认后的寿命。D=100、V=12、O=4，三桶同向量4/4/4且由多条组成，可满足75%份额及分布；单笔12即使占优也不满足持续性。

最初工具箱AP原型另用m=max(10,0.10D)、份额80%、至少3条、单条≤80%，其余规则须连同原型全文引用。这些门槛没有覆盖进本主版。M7/M10的局部AP另有版本，不能仅按AP名称继承本版结果。

## 数据、输出与组合

继承LEGACY_COMMON的数据质量合同；主来源feed可能包含已核验互补映射，须冻结feed版本与去重身份，不能静默换成直接成交版。需要成交方向、数量、原始身份、received/available/source及分桶覆盖；BBO或深度方向不能代替成交方向。

输出primitive/version、token/generation、root_id、方向、t0/e/c/deadline、D/m/V/O/N、桶量、事件引用、质量与结束原因。AP_SELL + RF_BID表示卖压仍在而承接存在；AP_BUY + AA_ASK表示买方进攻遇到供给恢复，不能把二者当作同向利多。组合须另定锚、等待及撤单，不自动下单。

## 来源与交接

[primitives.py](../../research/maker_signals/primitives.py)；[model.py](../../research/maker_signals/model.py)；[feed.py](../../research/maker_signals/feed.py)；[LEGACY_COMMON.md](../../research/signals/LEGACY_COMMON.md)。来源hash在首个JSON中冻结。

相关定义：[原语索引](PRIMITIVES.md)、[旧版公共约定](LEGACY_COMMON.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)、[LW](P_LW.md)。本任务未运行检测器、信号测试、回测或模拟；未修改采集、适配器或交易实现。
