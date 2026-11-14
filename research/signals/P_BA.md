# BA 守价

本次仅补齐独立原语文档；原定义/实现存在不代表当前部署或已验证收益。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "BA",
  "version": "ba-m3-cumulative-refill-v1",
  "status": "DOCUMENTED_FROM_SOURCE",
  "origin": "EXISTING_PRIMITIVE_DOCUMENTATION_NOT_NEW_EDGE",
  "source_profile": "M3Signals BidAbsorption with frozen M3 configuration",
  "inherits_execution_contract": false,
  "sides": [
    "BID"
  ],
  "consuming_direction": "SELL",
  "direct_trades_only": true,
  "anchor_policy": "FIRST_SELL_PRE_EVENT_BEST_BID_MUST_REMAIN_BEST_BID",
  "minimum_sell_shares": "5",
  "minimum_cumulative_depletion_shares": "5",
  "minimum_cumulative_positive_refill_shares": "5",
  "depletion_count_starts_after_sell_threshold": true,
  "refill_count_starts_after_depletion_threshold_ordinal": true,
  "refill_measure": "SUM_POSITIVE_DEPTH_DELTAS_NOT_NET_RECOVERY",
  "observation_deadline_from_first_sell_ms": 750,
  "confirmation_deadline_exclusive": true,
  "lifetime_from_first_sell_ms": 3000,
  "ttl_from_confirmation": false,
  "max_book_age_ms": 200,
  "max_sell_source_age_ms": 500,
  "out_of_order_tolerance_ms": 20,
  "source_future_tolerance_ms": 2,
  "post_confirmation_quantity_floor": null,
  "execution_admission_inherited": false,
  "source_files": [
    "src/polymaker/rdp_sm_ba.py",
    "src/polymaker/m234_signal.py",
    "src/polymaker/config.py",
    "research/signals/M3.md"
  ],
  "source_sha256": {
    "src/polymaker/rdp_sm_ba.py": "0808e46c2fddfe73ec0707a24bc4fe5f19f10749fdae8a25a624c3b8481b9ff3",
    "src/polymaker/m234_signal.py": "7c9690bef86ab776b56f2108228a9b58fe3c05582cddf25e8413b1d537f4c4ca",
    "src/polymaker/config.py": "d3a6d14be72dfd059234d65bffa8f371e23d34e524a050f05e5940f3bd99802f",
    "research/signals/M3.md": "0bda6466d06c81e9805b0a7b203d81468654013ac9ac38d5433986083ed47616"
  },
  "implemented_by_this_task": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 定义与版本

BA记录原Bid受卖后发生可见减量，随后出现补量，别名“守价”。主版忠实保存M3调用的旧BidAbsorption：SELL、累计负增量、累计正增量均至少5份；750ms观察，首笔起3秒截止。

“守价”是名称，数量证据不包含未来价格表现。该版并非AA的净恢复镜像、不是RF多轮稳定，也没有COMMON原量40%floor。

## 三个顺序阶段

1. 首笔新鲜直接SELL在事件前best_bid成交，固定p、Q0、t0=received、t0+750ms观察截止及t0+3秒总截止。原档须持续为best_bid。
2. 形成期原档SELL累计S≥5后，才开始计入随后深度事件的减量：Dsum增加max(Qbefore−Qafter,0)。Dsum≥5时冻结depleted_ordinal。此前深度下降不能倒补计量。
3. 只有输入ordinal严格晚于depleted_ordinal的正增量才计Rsum：增加max(Qafter−Qbefore,0)。Rsum≥5且此时合法、实际处理时间严格早于观察截止，立即确认c。

Dsum与Rsum都是累计量。回落不会扣掉已经累计的正增量；不要求Qend回到某个净恢复线、不要求100ms保持，也不要求H≥累计减量。增量消息size是新总量，须用前后明确数量相减。

深度先降、之后SELL才达门槛，不能按新条件回填前面的下降。一个输入位置不能同时当达到减损门槛及其之后的回补证据；同接收时间的后续ordinal可以有效，不能凭时间相同打乱顺序。

## 确认后维护与结束

证据确认后停止累计，固定deadline=t0+3秒，不是c+3秒。原Bid不再是best_bid、原档不存在、tick/generation变化、盘口非法/不新鲜或来源错误即不可用；未确认时750ms等号超时，总截止等号结束。

旧BA在合法原Bid仍存在时，不因“回补量后来再次下降”自动执行RF式净恢复退出。本版不添加固定数量floor。BBO通知与当前book的最佳买卖档不一致可触发BA_TOP_CHANGED，包括Ask侧变化的通知；复现时保留原通知/深度事件顺序，不能只看最终一致快照。

≤20ms源时间倒退在来源里忽略，超过容差失效；重复歧义和无法证明的成交历史不可用。旧容差不是完整流量证明，不推广到EX/LW等缺失敏感条件。

## 原语状态与调用者区别

独立BA evaluate还考虑最近SELL年龄、consumed状态、同时两token有效的冲突；M3直接读取episode并另查原锚/CA，不使用完全相同的选择路径。上述执行选择不能混入BA形成门槛。

M3额外规定CA确认后清空旧BA，BA首笔received严格晚于cCA，且BA原Bid等于CA原Bid；原BA本身不要求先有CA。不同母策略需冻结调用方式，不能只按“BA”汇总收益。

## 例子与时间

SELL已达5之后，量100→97→95，累计Dsum=5；再95→98→96→98，累计正增量3+2=5，可确认。此时相对低谷95的净恢复只有3份：这正说明累计回补不是净回补5份。

形成可提前触发，需c−t0<750ms，没有固定保持等待。总截止仍首笔后3秒。基础RDPBAConfig默认2秒寿命、100ms book预算与本M3配置不同，文件版本及调用配置必须同时匹配。

## 数据、输出与组合

需要直接SELL、事件前最优档、每次原档前后量、source/received/processed/ordinal、合法tick及连续链；只有终点快照不能复原累计正负增量。默认BUY或镜像方向不能冒充本版直接SELL。

输出p/Q0、S/Dsum/Rsum、depleted_ordinal、t0/c、观察与总截止、原始引用、质量、结束原因和母策略配置。BA + EX_SELL可表达已有守价及卖压减弱；BA → 新QR须用新的受卖阶段，不能把同批BA证据重复叫复验。原语本身无订单、挂价或自动Maker权限。

## 来源与交接

[rdp_sm_ba.py](../../src/polymaker/rdp_sm_ba.py)；[m234_signal.py](../../src/polymaker/m234_signal.py)；[config.py](../../src/polymaker/config.py)；[M3.md](../../research/signals/M3.md)。来源hash在首个JSON中冻结。

相关定义：[原语索引](PRIMITIVES.md)、[旧版公共约定](LEGACY_COMMON.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)、[LW](P_LW.md)。本任务未运行检测器、信号测试、回测或模拟；未修改采集、适配器或交易实现。
