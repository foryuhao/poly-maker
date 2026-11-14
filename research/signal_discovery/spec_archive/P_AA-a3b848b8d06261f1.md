# AA 卖方吸收

本次仅补齐独立原语文档；原定义/实现存在不代表当前部署或已验证收益。

```json
{
  "document_type": "orderbook_primitive_sop",
  "primitive_id": "AA",
  "version": "aa-toolbox-net-recovery-750ms-v1",
  "status": "SPEC_ONLY",
  "origin": "EXISTING_PRIMITIVE_DOCUMENTATION_NOT_NEW_EDGE",
  "source_profile": "orderbook-toolbox-v1 section 4, original research definition",
  "inherits_execution_contract": false,
  "sides": [
    "ASK"
  ],
  "consuming_direction": "BUY",
  "anchor_policy": "PRE_EVENT_BEST_ASK_MUST_REMAIN_SAME_TOUCH",
  "round_window_ms": 750,
  "minimum_hit_shares": "5",
  "minimum_hit_depth_fraction": "0.10",
  "minimum_loss_shares": "5",
  "minimum_loss_depth_fraction": "0.10",
  "recovery_measure": "NET_FROM_TROUGH_NOT_SUM_POSITIVE_ADDITIONS",
  "hold_cross_recovery": "0.50",
  "hold_maintenance_recovery": "0.40",
  "hold_ms": 100,
  "new_trough_resets_forming_hold": true,
  "confirmation_deadline_from_first_hit_ms": 750,
  "confirmation_at_deadline_allowed": true,
  "aa_extra_processing_budget_ms": 0,
  "current_ttl_ms": 750,
  "current_recovery_uses_trough_frozen_at_confirmation": true,
  "exit_recovery_below": "0.20",
  "round_stays_open_to_fixed_end": true,
  "closed_round_processing_budget_ms": 50,
  "stable_round_end_recovery_minimum": "0.50",
  "source_files": [
    "docs/research/ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.md",
    "docs/research/ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.parameters.json"
  ],
  "source_sha256": {
    "docs/research/ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.md": "c9b3ea8058b49ae007df13cf8c327b55695574234913bcf21ef117798324da64",
    "docs/research/ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.parameters.json": "c85e803bc836f3de1e2cd2732ebc054e0214d0f807a979debe3d386ce245b154"
  },
  "implemented_by_this_task": false,
  "execution_owner": "USER_SIGNAL_TEST_MODULE"
}
```

## 定义与来源

AA_ASK表示主动BUY击打原Ask后，可见供给在同档净恢复并保持。本文件保存2026-11-01工具箱原型，尚不能视为已有独立检测器或当前部署。它与BA并非只有买卖方向不同：AA算低谷后的净恢复，BA旧实现累计后续正增量。

价格只定位原Ask和成交，不使用涨跌。该原型要求原触及Ask身份保持；仅同价仍在远档不能保留原触及队列的比较资格。

## 计算与确认

首笔合格BUY命中事件前Ask时冻结p、Q0>0、t0，观察截止为t0+750ms。先有打单，再记录同档最低量Qlow；H为同档BUY累计量，D=Q0−Qlow，Rnet=min(D,max(0,Q−Qlow))，r=Rnet/D。D=0不可计算恢复率。

最低冲击m=max(5,0.10Q0)，同时H≥m且D≥m才合格。两者是共同观察证据，不证明每份损失都由成交消耗。

合格后r首次达到0.50启动计时，连续100ms保持r≥0.40，且该保持及确认实际在t0+750ms以内完成，允许恰在截止点由已有证据封口。r跌破0.40清空计时，窗口内可重新达到0.50再开始；出现新低更新D/r并清空正在计时，原截止不变。确认时r可能只有0.40–0.50，不要求额外回到0.50。

事件区间为[t0,t0+750ms)，右端事件不进入该轮。AA本身没有额外50ms延迟确认额度；底层轮末统计的50ms预算不是补发AA的许可。只有BUY无可见损失、只有补量无合格BUY均不确认；数据缺口为不可用，不能记防守失败。

## 确认后的AA与底层轮封口

c为首次合法AA确认，冻结当时Qlow及D，用此基线维护旧AA：r_current<0.20、原Ask触及档改变、generation或质量失效即结束，否则c+750ms到期，等号失效。新打单不延长旧AA；后续低谷不能重设旧AA底线来救活它。

底层轮仍到原t0+750ms封口，期间不开启重叠新轮。供原型RF/DF引用时，稳定成功还要求曾完成AA式保持，且封口r_end≥0.50；统计需在轮末后50ms内可得。早先AA确认与最后是否稳定是两个字段，不能用AA确认记录数代替完整轮次。

如果深度先降、成交后来才到，不能倒排成“打后补”；事件顺序无法解析时标EVENT_ORDER_UNRESOLVED。

## 时序、例子和适用边界

形成所需保持至少100ms，实际须在首笔后750ms以内确认；确认后寿命另为750ms。Q0=100、低谷70，达量85时r=0.50，随后100ms一直≥82则可确认，即使确认时只有82、r=0.40。如果轮末仍82，该轮不满足RF原型稳定成功的r_end≥0.50。

若确认基线为Qlow=70、D=30，之后量<76才触发20%恢复退出；这个例子不替代原触及档和质量退出。

500ms滞回轮、COMMON严格末100ms支撑以及M4弱供观察均有独立规则，不从本AA隐式继承或缩短窗口。Bid方向的同式研究轮可以服务原型RF/DF，但不改名冒充旧BA。

## 数据、输出与组合

使用原工具箱G：同generation、完整快照与已知连续链、事件前合法盘口、明确源时间/接收/可得时间、直接可对齐成交与逐档更新；本地book≤200ms、源证据≤500ms。未知去重/缺口不记零；主原型样本排除无法剥离的自有量污染。档位更新size是新总量，不能当新增量。

输出p、Q0/Qlow/D/H/r_cross/r_confirm、保持起止、c/deadline、轮末r_end与稳定标记、来源/质量/失效原因。AP_BUY + AA_ASK表示买攻遇到卖方恢复；它不证明买攻失败、隐藏订单或最终结算方向。更多轮的稳定性由RF/DF分别判断，单个AA无自动Maker权限。

## 来源与交接

[ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.md](../../docs/research/ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.md)；[ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.parameters.json](../../docs/research/ORDERBOOK_SIGNAL_TOOLBOX_V1_20261101.parameters.json)。来源hash在首个JSON中冻结。

相关定义：[原语索引](PRIMITIVES.md)、[旧版公共约定](LEGACY_COMMON.md)、[AP](P_AP.md)、[AA](P_AA.md)、[RF](P_RF.md)、[EX](P_EX.md)、[DF](P_DF.md)、[CA](P_CA.md)、[BA](P_BA.md)、[LW](P_LW.md)。本任务未运行检测器、信号测试、回测或模拟；未修改采集、适配器或交易实现。
