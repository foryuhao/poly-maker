# QR 耗而不薄

版本`qr-consumed-queue-retention-v1.1`，**SPEC_ONLY**。从[M17](M17.md)提取可复用的“持续成交伴随可见队列保量”原语，新增ASK对称表达。v1.1补回M17已有的发布时90%保量检查；v1漏了这一项，不能称两版行为相同。它不是新发现的一份独立方向证据；BID侧与M17共享形成信息，ASK侧尚未验证。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "QR",
  "version": "qr-consumed-queue-retention-v1.1",
  "supersedes_version": "qr-consumed-queue-retention-v1",
  "revision_reason": "RESTORE_M17_PUBLICATION_RETENTION_GATE",
  "status": "SPEC_ONLY",
  "origin": "EXTRACTED_FROM_M17_WITH_ASK_SIDE_GENERALIZATION",
  "source_signal_version": "m17-retained-depth-under-selling-v1.1",
  "source_definition_sha256": "3b4b4617bff370b12f785faca280bae0f4c17b2e26b1fd8b6d83a587af0e33e1",
  "source_definition_snapshot": "../signal_discovery/spec_archive/M17-3b4b4617bff370b1.md",
  "sides": ["BID", "ASK"],
  "data_clock_contract": "maker-discovery-common-v1",
  "inherits_execution_contract": false,
  "direct_consuming_trades_only": true,
  "observation_window_ms": 750,
  "subwindow_ms": 250,
  "minimum_hit_shares": "5",
  "minimum_hit_to_initial_depth": "0.50",
  "minimum_distinct_hit_records": 3,
  "occupied_subwindows_required": 3,
  "maximum_single_hit_fraction": "0.80",
  "continuous_quantity_floor_fraction": "0.70",
  "terminal_quantity_floor_fraction": "0.90",
  "terminal_hold_ms": 100,
  "publication_quantity_floor_fraction": "0.90",
  "publication_opportunity": "FIRST_COMPLETE_BATCH_OR_TIMER_BOUNDARY_AFTER_WATERMARK_CLOSE",
  "failed_publication_retention_ends_root": true,
  "post_hit_recheck_max_ms": 100,
  "post_hit_recheck_clock": "HIT_RECEIVED_PLUS_100MS_EXCLUSIVE",
  "explicit_same_level_recheck_required": true,
  "pending_recheck_blocks_new_action": true,
  "current_ttl_ms": 3000,
  "adapter": null,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 1. 对象与初始数量

只继承[COMMON](COMMON.md)的数据、双时钟、质量、窗口及原档规则；不继承RF恢复条件或自动执行。选择BID时以第一笔直接SELL开启，选择ASK时以第一笔直接BUY开启，成交须在事件前该侧最佳价p。冻结原token/side/p/generation，窗口起点t0为首笔received，固定观察窗`[t0,e)`、`e=t0+750ms`。

Q0>0来自首笔前明确报出p数量的事件：输入键在成交前，received不晚于成交，source严格更早，且增量链连续。全书source、BBO或其他档回调不能替代；无法确定前置量时UNKNOWN。随后p不再最优也不换档，只要原档仍存在且输入质量有效。

同token/side只保留一个形成或当前root。BID与ASK分别建状态，不把一个token的互补镜像当作另一份直接成交证据。

## 2. 确认条件

原p上消耗该侧的去重直接成交量为H，三个250ms子窗分别为H1/H2/H3。完整750ms窗同时满足：

- H>=max(5,0.50Q0)，至少3条有可辨身份的成交，各子窗Hj>0，最大单笔<=0.80H。
- 全过程Q(p)>=0.70Q0，任一次跌破结束；原档消失也结束，不因后来补回复活。
- 最后100ms `[e-100ms,e)`持续Q(p)>=0.90Q0。
- 形成窗每笔成交都完成第3节的同档数量复核。

不要求足够深的低谷、净恢复率、RF轮数或累计正增量。三子窗正量不等于三段同等强度，也不证明多个不同参与者。计时器只推进水位，不能创造缺失的盘口状态。

## 3. 每笔成交后的数量复核

对每笔j，冻结`Dj=trade_j.received+100ms`。需明确携带原p数量的深度事件，输入键在该笔之后、source严格更晚、received不早于该笔，且received与available均严格早于Dj。形成窗成交还要求复核received严格早于e。

一份合格后续报量可以复核多笔先前成交；这不是逐笔撮合回执。仅BBO、其他档更新、PONG或source相等的记录不能复核；健康完整book可提供数量，重建generation的快照先结束旧root。

到Dj先检查未完成项超时，等号已经失效；成交本身可得时已晚于Dj也直接UNKNOWN。不能从available重给100ms、删掉未复核成交重算H，或用窗外更新补形成证明。数据迟到和下游处理积压的成本都由该100ms预算承担。

## 4. 发布、待复核与当前态

水位越过e后，在首个完整原始批次边界（无待处理批次时为对应计时器边界）进行发布检查，c须`e<=c<=e+50ms`。**发布点当前Q(p)还须>=0.90Q0**；不足则本root结束为`PUBLICATION_RETENTION_FAILED`，不能等后来回补再挑一个发布点。

从e到发布之间的完整路径以及发布后的当前态，维持固定底线F=0.70Q0、原档存在与质量；不要求整个交接区间都连续90%，发布后也不继续守90%。新成交继续进入100ms复核监控，不进入已冻结形成H。此处“交接70%＋发布点90%”与“仅交接70%”是不同规则。

发布前检查e以来的全部已知路径及已到期复核，不只看当前快照。后处理但received仍在已关闭形成窗的事件按质量规则处理，不能改称新成交绕过形成要求。

事实固定截止`c+3000ms`。确认后新打单尚待复核时，状态为`PENDING_RECHECK`，`can_reference_for_new_action=false`：可以记录已确认历史，但不能据此首次确认新组合或首次发单。及时复核清除待办后可恢复当前可引用状态，原截止不变。已经发出的组合订单在该等待期间如何处理，须遵守其事先冻结规则，不能事后删除暴露；按M17式使用时允许原100ms预算，超时结束并撤剩余。

底线跌破、档位消失、复核超时、缺口、generation/tick变化或到期使事实结束。没有RF稳定/不稳定退出，不因新好轮续期。结束后首笔received与输入键都更晚的新合格打单才能开启另一root，旧成交不复用。

## 5. 语意、查重与组合

`QR_BID`表达卖出持续成交但买量仍保留；`QR_ASK`表达买入持续成交但卖量仍保留。二者方向相反，不能把“QR出现”统一编码成看多。可见量留存不证明同一订单、隐藏流动性或同一做市商。

例如Q0=100，三个子窗H为20、20、20，每笔及时得到同档新报量，数量始终至少95、末100ms为100，则数量条件有解；可以没有合格RF低谷。只有静态100而H=0不成立。所有例子都是定义算术，不是信号或市场测试。

与AA/BA/RF区别：这些原语要求特定冲击后的减损/恢复或正增量过程，QR允许可见低谷很小。与M17关系：QR_BID是其主要形成与维护信息的原语化，不能把M17和QR同时触发算作两份独立确认；M17的Maker执行仍由其原SOP规定，本文件不修改它。

- QR_BID配SR_ASK、DF_ASK等，可表达“需求耐耗，而对侧补给短驻或衰退”，形成Maker候选仍需组合自己的原锚、截止与执行规则。
- AP_BUY配QR_ASK表达“主动买入遇到保量供给”，是对抗信息，不能自动提高买入方向评分。
- BA_BID之后若要求全新QR_BID，则QR首笔received及输入键均须晚于BA确认；若复用了BA事件，只能标共同证据，不能宣称再次独立验证。

与SR的组合优先使用已有或较早开始的QR。按原期限，`SR确认 → 全新QR`无法保持两者同时当前有效；即使QR在SR增厚时就开始，联合确认后的剩余时间也小于300ms，不能满足COMMON的400ms首次提交余量。具体推导与待复核边界见[组合审查](PRIMITIVE_COMPOSITION_REVIEW.md)，不改变本原语条件。

## 6. 输出与能力边界

记录`primitive/version, token, side, anchor, generation, root_id, initial_quantity_ref, Q0, t0, evidence_end, H, subwindow_H, distinct_hits, largest_hit, Qmin, Qend, floor, recheck_refs, pending_recheck_deadlines, confirmed_available, deadline, can_reference_for_new_action, quality, end_reason`。

需要直接成交身份、原档前后来源、完整路径与双时钟；只有H/末量/RF摘要不足。QR事实不代表Maker已成交，也不授权任何实际下单；形成H不能抵扣未来Maker前置队列。未验证新组合频次、经济收益或结算概率，未实现检测器/适配器。

## 7. v1.1修订与复用

v1提取时保留了750ms末段90%条件，却在e到c的段落只留下70%底线，漏掉M17第5节的发布点90%。例如Q0=100、末段持续95，e后降到80且首个发布点仍为80，旧QR文字可以发布，新版与M17均不能发布。这是行为修订，因此升版，旧稿保留于[QR v1快照](../signal_discovery/spec_archive/P_QR-8742dc840d5c31e2.md)，不得按同一版本混用。

750ms形成、3秒寿命、70%当前底线和100ms复核不变。M17主版本也不变；[M17中的保量配短补备选](M17.md)显式引用QR v1.1与SR v1并独立冻结组合和执行规则，不能由原语出现自动发单。
