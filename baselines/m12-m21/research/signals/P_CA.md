# CA 反击

本次仅补齐独立原语文档；原定义/实现存在不代表当前部署或已验证收益。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "CA",
  "version": "ca-m3-sell-then-buy-v1",
  "status": "DOCUMENTED_FROM_SOURCE",
  "origin": "EXISTING_PRIMITIVE_DOCUMENTATION_NOT_NEW_EDGE",
  "source_profile": "M3Signals CounterAttack with frozen M3 BA configuration",
  "inherits_execution_contract": false,
  "directions": [
    "SELL_THEN_BUY"
  ],
  "direct_trades_only": true,
  "anchor_policy": "FIRST_SELL_PRE_EVENT_BEST_BID_MUST_REMAIN_BEST_BID",
  "minimum_sell_shares": "5",
  "minimum_subsequent_buy_shares": "10",
  "buy_must_follow_sell_threshold_ordinal": true,
  "observation_deadline_from_first_sell_ms": 750,
  "confirmation_deadline_exclusive": true,
  "lifetime_from_first_sell_ms": 3000,
  "ttl_from_confirmation": false,
  "max_book_age_ms": 200,
  "max_trade_source_age_ms": 500,
  "out_of_order_tolerance_ms": 20,
  "source_future_tolerance_ms": 2,
  "source_profile_is_m3_not_standalone_defaults": true,
  "price_advance_required": false,
  "execution_admission_inherited": false,
  "source_files": [
    "src/polymaker/rdp_sm_ca.py",
    "src/polymaker/m234_signal.py",
    "src/polymaker/config.py",
    "research/signals/M3.md"
  ],
  "source_sha256": {
    "src/polymaker/rdp_sm_ca.py": "a61b70fea8e752552473da20f2b2ccd53313fb64e53f92ac8c279bfc75caf324",
    "src/polymaker/m234_signal.py": "7c9690bef86ab776b56f2108228a9b58fe3c05582cddf25e8413b1d537f4c4ca",
    "src/polymaker/config.py": "d3a6d14be72dfd059234d65bffa8f371e23d34e524a050f05e5940f3bd99802f",
    "research/signals/M3.md": "0bda6466d06c81e9805b0a7b203d81468654013ac9ac38d5433986083ed47616"
  },
  "implemented_by_this_task": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 定义与版本

CA表示原Bid先受SELL冲击，之后主动BUY接替达到规模，别名“反击”。它不要求买一卖一推进，不使用外部BTC价格，也不证明随后回踩有承接。

主版保存M3调用的CounterAttack配置：观察750ms、首笔起总寿命3秒、本地book预算200ms。基础配置的2秒寿命/100ms book与独立执行准入并非本版。

## 因果顺序和确认

同token无有效episode时，首笔直接SELL在事件前新鲜best_bid成交，冻结p、t0=received、观察截止t0+750ms、总截止t0+3000ms。启动前买盘不能算反击。

形成期累计合格SELL达到5份，在该条事件记录输入ordinal及tS。只累计ordinal严格晚于这个卖量门槛事件的直接BUY，且每笔在其事件前新鲜best_ask成交；达到10份即确认c=tC。SELL达到5份之前的BUY不计入后续10份。

原Bid锚全程须仍为best_bid，Ask可以按当时合法best_ask匹配；不要求所有BUY价格相同。有效BUY/SELL的方向、数量、源时间和成交前档位都须通过来源检查。确认必须实际可得于t0+750ms之前，等号到期；不能排队处理后补发过去的确认。

只要求输入ordinal严格先后，不新增received时间必须不同的门槛；同一接收时刻可有后续输入事件。确认后证据冻结，后续成交不继续累计以修改强度或延长寿命。

## 当前有效性与失效

截止始终t0+3秒，等号结束；不是c+3秒。原Bid不再为best_bid、generation/tick改变、盘口交叉或不新鲜、无合法快照、来源严重乱序/重复歧义、观察器故障等结束或不可用。还未确认时先遇750ms观察到期即失败，不等总寿命。

源时间倒退≤20ms的事件在旧观察器里被忽略而保留episode；超过容差失效。这是旧来源处理语义，不证明丢弃成交对统计无影响，也不能把这项宽容继承给EX/LW的零成交推断。源时间最多容许超前本地墙钟2ms。

## 事实有效与执行可用分开

独立CA snapshot另检查确认BUY证据年龄≤500ms、首笔年龄≤1000ms；独立交易路径还可能要求确认耗时>100ms及网络余量。M3直接读取CA episode，不调用相同准入路径，因此这些门槛不是CA形成条件，也不是本原语自动下单许可。

“CA确认→2秒内买卖一各推进2 tick→原买一+1 tick等回踩”属于另一含价格推进的策略，不能写回CA定义；原CA在原Bid改变时还可能已结束，该策略若使用过去CA必须明确历史交接。

## 时序、例子、数据与输出

最早由实际卖后买事件决定，无固定100ms形成等待；必须c−t0<750ms。即使700ms才确认，总截止仍为首笔后3秒，剩约2.3秒。

例：SELL 3、BUY 6、SELL 2、BUY 4、BUY 6。只有卖量达到5后的最后两笔BUY累计10可确认；更早BUY 6不能抵扣。

需要直接成交和事件前BBO/深度、received/processed/source/ordinal、generation/tick及去重诊断。相同价格数量时间不是独立交易者证明，来源的重复处理须记录。输出episode/root、p/t0/tS/tC、阈值事件ordinal、SELL/后续BUY量、观察与总截止、质量、结束原因。

CA → 新BA的完整组合见M3：BA首笔received须严格晚于CA确认并同原Bid；与CA自身ordinal要求分开。CA可提供主动接替背景，BA提供后续受卖守价证据；两者不自动保证Maker成交后的结算优势。

## 来源与交接

[rdp_sm_ca.py](../../src/polymaker/rdp_sm_ca.py)；[m234_signal.py](../../src/polymaker/m234_signal.py)；[config.py](../../src/polymaker/config.py)；[M3.md](../../research/signals/M3.md)。来源hash在首个JSON中冻结。

相关定义：[原语索引](PRIMITIVES.md)、[旧版公共约定](LEGACY_COMMON.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)、[LW](P_LW.md)。本任务未运行检测器、信号测试、回测或模拟；未修改采集、适配器或交易实现。
