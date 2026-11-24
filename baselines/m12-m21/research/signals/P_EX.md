# EX 进攻衰竭

本次仅补齐独立原语文档；原定义/实现存在不代表当前部署或已验证收益。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "EX",
  "version": "ex-legacy-post-ap-750ms-v1",
  "status": "DOCUMENTED_FROM_SOURCE",
  "origin": "EXISTING_PRIMITIVE_DOCUMENTATION_NOT_NEW_EDGE",
  "source_profile": "research.maker_signals.primitives.Pressure EX lifecycle",
  "inherits_execution_contract": false,
  "directions": [
    "BUY",
    "SELL"
  ],
  "requires_confirmed_ap": true,
  "ap_profile": "ap-legacy-pressure-750ms-v1",
  "window_ms": 750,
  "subwindow_ms": 250,
  "confirmation_lateness_max_ms": 50,
  "maximum_each_subwindow_reference_volume_fraction": "1/6",
  "maximum_total_reference_volume_fraction": "0.25",
  "minimum_token_coverage_events_each_bucket": 1,
  "post_ap_input_key_required": true,
  "current_ttl_ms": 750,
  "resumed_rolling_window_ms": 750,
  "exit_resumed_volume_fraction_at_least": "0.50",
  "reverse_ap_ends_watch_and_current": true,
  "ap_natural_expiry_ends_ex_watch": false,
  "source_files": [
    "research/maker_signals/primitives.py",
    "research/signals/LEGACY_COMMON.md"
  ],
  "source_sha256": {
    "research/maker_signals/primitives.py": "249c711a58b6c33516f4d9383e0d039566d56cf4924b1ee994ba8c4f961ac522",
    "research/signals/LEGACY_COMMON.md": "e3857f08807717ceb24b9530c1a662bafe838b5907b3f2a2c72bcd56fdd0f2a4"
  },
  "implemented_by_this_task": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 定义与方向

EX_BUY / EX_SELL表达已经确认的进攻，随后同方向流量明显减弱。必须有具体AP作为历史参照；普通安静、断流或没有AP都不能称为衰竭。EX_SELL不等于已经有买方接替，后者需要CA或新的AP_BUY。

主版绑定本目录P_AP主版及research Pressure实现；相同算式配另一AP版本仍是不同来源的EX。

## 开始、计算与确认

每个AP确认时冻结其ID、方向s、确认窗口同向量Vref>0、确认可得时间cA和输入键kA。以cA开启新的750ms观察，分三个250ms桶；只统计received落在各桶且输入键晚于kA的同向成交。AP确认之前已收到但后来才处理的记录不能冒充后续证据。

令桶量为v0/v1/v2。全部条件成立才在窗口封口后最多50ms内确认：

- 每桶vj≤Vref/6。
- v0+v1+v2≤Vref/4。
- 三桶均具有本token的有效深度或BBO覆盖，且参照和后续输入可用。仅PONG或其他token不能作覆盖。

每桶速率≤参照速率50%等价于上述Vref/6，不是Vref/2。首个观察不通过即不生成这份EX，不能滚动移动起点寻找低量段。

## 当前态、结束与历史交接

cE为EX实际可得确认；deadline=cE+750ms，等号到期。新的滚动750ms同向量≥0.50Vref即结束为进攻恢复；反向AP确认结束该方向的EX及形成观察；generation、覆盖或质量失效则不可用。Vref与期限不重设。

AP自然过期或因本方向变弱而结束，不自动取消其已经启动的EX观察。这里AP是冻结的历史参照，不能要求确认EX时AP还当前有效：EX观察本身已占750ms。反向AP取消观察是另一条明确规则，不能与自然过期混用。

形成使用右开桶；源码当前态恢复检查使用包含当前时刻的滚动量。完整政策须冻结这个处理边界，不事后回填。

## 时序、例子及其他版本

从AP确认起再需750–800ms；从最初AP首笔起，连续完成两阶段通常至少1500ms，最迟边界随两次最多50ms封口预算增加。不能把两阶段解释为必须一直保持AP当前有效。

Vref=60，后续桶量5/4/3，总12≤15、每桶≤10，可确认；桶量11/1/1即使总量≤15，也因第一桶超限失败。当前滚动同向量达到30即结束，等号不保留。

M7的500ms短暂停顿和不同局部AP衍生的衰减都不是本完整EX。历史EX能否作为后续种子、能保留多久，由各M策略另定，不给本EX续期。

## 数据、输出与组合

输出方向、AP ID/version、Vref、cA/kA、三个桶量与覆盖、e/cE/deadline、结束原因。缺失方向/去重或未覆盖的零记录为UNKNOWN，不按零量通过。参照与后续使用同一成交口径，不能一段含镜像、一段仅直接。

RF_BID + EX_SELL表达承接仍有效且卖压衰竭；EX_SELL → CA表示后续出现主动买入接替。Maker仍需其锚处有效支撑和完整等待政策，衰竭本身不证明后续回流安全或更易胜出。

## 来源与交接

[primitives.py](../../research/maker_signals/primitives.py)；[LEGACY_COMMON.md](../../research/signals/LEGACY_COMMON.md)。来源hash在首个JSON中冻结。

相关定义：[原语索引](PRIMITIVES.md)、[旧版公共约定](LEGACY_COMMON.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)、[LW](P_LW.md)。本任务未运行检测器、信号测试、回测或模拟；未修改采集、适配器或交易实现。
