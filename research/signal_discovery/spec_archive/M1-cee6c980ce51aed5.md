# M1 承接后的卖压余波

```json
{
  "document_type": "signal_sop", "strategy_id": "M1",
  "version": "m1-hold-v2/cal-16", "status": "DOCUMENTED_FROM_SOURCE",
  "common_contract": "LEGACY_COMMON.md", "role": "DIRECTIONAL_MAKER_SIGNAL",
  "source": ["src/polymaker/rdp_sm_m1_signal.py", "src/polymaker/m1_live_config.py"],
  "ap_window_ms": 750, "ex_window_ms": 750, "ex_ttl_ms": 750,
  "round_window_ms": 500, "rf_rounds": 2, "rf_ttl_ms": 8000,
  "rf_sequence_span_ms": 15000, "combination_span_ms": 20000,
  "mirror_complement_flow": true, "flow_requires_top_match": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 交易语意

多轮真实卖出被承接，先前卖方进攻随后衰竭；Maker在原承接档等后续零散卖盘。要检验的是这些实际成交能否在同价、同剩余时间下更常胜出，不能仅凭RF/EX存在推出结算优势。

## 输入与计算

按 [LEGACY_COMMON.md](LEGACY_COMMON.md) 的cal-16 AP、500ms轮、两轮RF和EX_SELL；不是配置类默认的3轮/750ms旧参数。实际参数hash必须与实验记录匹配。

先有AP_SELL后才能建立其EX；RF可与该阶段重叠，不强制RF必须在AP确认之后。RF固定原Bid档，允许离开买一后该价档仍可见。成交与深度减少都要真实存在；静态挂墙不算承接。

## 确认与时间

同token、同generation的当前RF_BID与EX_SELL同时有效，且从RF第一轮开始、原AP开始中较早者，到二者最晚确认的跨度<=20s。未使用过的RF/EX配对成为候选，锚价=RF原Bid。

`c=max(cRF,cEX)`，`deadline=min(cRF+8s,cEX+750ms)`。从完全无证据开始，AP+EX至少约1.5s；两轮RF至少1s，可与前者重叠。确认后的可交易寿命最多750ms，不是8s。已经有RF时，新增EX确认即可形成候选。

## 失效与Maker交接

到期、EX滚动卖量恢复>=原参照50%、反向AP_BUY、RF后续合格承接失败、原锚消失、行情质量或generation变化均使组合不可用。旧实现用当前RF/EX槽位；新证据替换时须记录候选身份，不能把新期限续到旧订单上。已消费RF/EX不能再开原机会；自有活动不能建立新主证据。

信号输出原锚及原截止；测试模块决定具体Maker执行版本。严格语意版本需在支撑仍有效时等待，失效撤剩余，成交持有到官方结算；撤单在途成交仍保留。历史M1实盘低价区间、预算和账户限制属于执行配置，不是本信号的方向条件。

## 可观测性与边界

需要可信逐笔方向、原档深度、镜像去重、完整静默覆盖及因果时钟。缺数据不能当卖压衰竭。需分别报告RF形成耗时、EX形成耗时和候选实际剩余期限；本文件不声称新的收益验证。


## 原语组合与Maker成交语意（统一映射 v1）

本节对应主版本 `m1-hold-v2/cal-16`，只整理既有定义，不改上文参数、执行或备选。`hist/live/→/∥`及旧轮、严格轮的版本区别见[统一映射](STRATEGY_PRIMITIVE_MAP.md)。

**原语组合**：`hist(AP_SELL@M1) → live(EX_SELL@M1)`，与 `live(RF2@M1)` 重叠；AP/EX/RF分别对应原语的旧版本语意，消费端仍按本策略。

**先后顺序**：RF两轮可以早于或跨越AP/EX形成，不新增“AP后才开始RF”。同token/generation内，从RF首轮或AP的最早起点到组合确认不超过20s；在两个事实都当前可用时确认。

**当前等待与失效**：锚为原RF Bid。截止取 `min(cRF+8s,cEX+750ms)`；EX回强/反向AP、合格RF失败轮、原档或质量失败按主版结束。旧RF没有COMMON持续40%floor。新正向事实替换候选的旧行为保留，已发订单不得重计截止。

**Maker成交语意**：多轮承接已经成立、原卖压进入衰竭，Maker接后来零散SELL；成交本身不证明这笔新卖压已完成恢复，也不保证结算。

**重叠与合并边界**：与M5/M9合并为“卖压承接”研究家族：M5接当前强压，M9等衰竭后的新复验。M1不是M9的统一父候选；同父AP与EX不作为两个当前独立证据。

形成期流量不抵扣随后Maker订单前置队列；全部实际成交（含失效后及撤单在途）保留经济记录。这里只表达等待政策下的成交阶段，不宣称结算概率或盈利已经提高。
