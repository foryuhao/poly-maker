# RDP Phase 0 — Capital Survival Inventory Engine

Status: FROZEN DEVELOPMENT BASELINE

## Authority

This document is the strategy source of truth for RDP Phase 0.

No coding agent may modify or reinterpret its trading semantics.

Any strategy-semantic modification requires explicit human architecture
approval.

## Development Principle

Minimum Problem
-> Minimum Change
-> Fast Real-Market Validation

## External Interface Rule

poly-maker trading, market data, account state, risk and execution may rely
only on:

- official Polymarket HTTP APIs
- official Polymarket WebSocket APIs

Forbidden without explicit human architecture approval:

- blockchain RPC
- third-party runtime market/data APIs
- third-party runtime trading APIs
- unofficial Polymarket services
- any new external runtime interface

---

RDP Phase 0 — Capital Survival Inventory Engine
Status: FROZEN BASELINE
1. Phase 0 目标
* 起始资本：$30
* 连续参与：288 个 BTC 5m 市场
* 每市场目标：3–5 个 ActiveCycles
* 总目标：864–1,440 个 ActiveCycles
Phase 0 验证：
* Ending Capital
* Max Drawdown
* Worst Market Loss
* Inventory Recovery
* Capital Availability
* 1-tick Maker Fill EV
第一原则：
Capital Survival

2. Inventory State
维护：
* QY = YES 持仓数量
* QN = NO 持仓数量
* Delta = QY - QN
* InventoryExposure = abs(Delta)
每次真实 Fill 后立即更新：
* QY
* QN
* Delta
系统不维护 Pair State，只维护 Inventory State。

3. Inventory Cost
分别维护：
* CY = YES 实际累计买入成本
* CN = NO 实际累计买入成本
* C_total = CY + CN
平均成本：
* AvgYESCost = CY / QY
* AvgNOCost = CN / QN
对应库存为 0 时，平均成本记为 N/A。
只有 confirmed fills 计入 CY / CN。
Resting Orders 不计入 Inventory Cost。

4. Capital State
实时维护：
* FreeCash
* ReservedCash
* InventoryCost = C_total
在没有结算等现金流变化时：
Capital = FreeCash + ReservedCash + InventoryCost
每次新增订单前执行 CapitalGate。
CapitalGate 要求：
1. FreeCash 足够支付新订单；
2. Inventory + Resting Orders + New Orders 不突破 $30 总资本；
3. NORMAL 不得耗尽后续库存修复能力。
原则：
Capital Survival > Quote Count

5. Balanced 与 Residual Inventory
定义：
* BalancedQty = min(QY, QN)
* Delta = QY - QN
Residual：
* ResidualYES = max(Delta, 0)
* ResidualNO = max(-Delta, 0)
因此：
* QY = BalancedQty + ResidualYES
* QN = BalancedQty + ResidualNO
任一时刻只有一侧存在 Residual。

6. Balanced Inventory Cost
使用当前各侧平均成本进行经济归因：
* BalancedYESCost = BalancedQty × AvgYESCost
* BalancedNOCost = BalancedQty × AvgNOCost
因此：
BalancedCost = BalancedQty × (AvgYESCost + AvgNOCost)

7. Residual Inventory Cost
定义：
ResidualCost = C_total - BalancedCost
含义：
* Delta > 0：Residual 为 YES
* Delta < 0：Residual 为 NO
* Delta = 0：ResidualCost = 0

8. Locked PnL
一份 YES + 一份 NO 最终确定结算为 $1。
因此：
LockedPnL = BalancedQty - BalancedCost
LockedPnL 表示 Balanced Inventory 已经锁定的确定性经济收益。
在尚未 Merge / Settlement 时：
LockedPnL 不是 Realized PnL。

9. Residual Risk
Residual 的最终价值取决于市场结果。
* 最坏价值：0
* 最好价值：abs(Delta)
因此：
* ResidualWorstLoss = ResidualCost
* ResidualBestPnL = abs(Delta) - ResidualCost
Phase 0 不对 Residual 做方向预测。

10. Worst Settlement PnL
如果 Residual 最终全部归零：
WorstSettlementValue = BalancedQty
因此：
WorstSettlementPnL = BalancedQty - C_total
也可以写成：
WorstSettlementPnL = LockedPnL - ResidualCost
以后统一使用 WorstSettlementPnL，不再使用 PnL_floor。

11. Best Settlement PnL
如果 Residual 所在方向最终获胜：
BestSettlementValue = max(QY, QN)
因此：
BestSettlementPnL = max(QY, QN) - C_total
当前库存最终结算结果一定落在：
WorstSettlementPnL ≤ SettlementPnL ≤ BestSettlementPnL

12. Final Market PnL
如果 YES 胜：
MarketPnL = QY - C_total
如果 NO 胜：
MarketPnL = QN - C_total
统一表示：
MarketPnL = WinningQty - C_total
这是单市场最终真正的 Realized PnL。

13. 运行状态
系统只有两个交易状态：
* NORMAL
* REBALANCE
另有一个终止动作：
* STOP
STOP 后不再增加任何新风险。

14. NORMAL
进入条件：
abs(Delta) < 5
执行：
Dual Maker + Inventory Size Skew

15. NORMAL Entry Gate
读取：
* BY = YES BestBid
* BN = NO BestBid
* TickY = YES TickSize
* TickN = NO TickSize
Price Gate
要求：
BY + BN ≤ 1.00
NORMAL 报价：
* MY = BY - TickY
* MN = BN - TickN
TickSize 必须动态读取，不得硬编码 $0.01。

16. Book Filter v0
Book Filter 是负向过滤器。
任一条件触发则 Reject。
Inactive
任一侧最近 2 秒同时满足：
* 无真实成交；
* BestBid / BestBid - 1 tick 附近 aggregate depth 变化 < 5 shares。
则 Reject。
Depth Extreme
定义：
Q_eff = Size(BestBid) + Size(BestBid - 1 tick)
如果：
max(QY_eff, QN_eff) / max(min(QY_eff, QN_eff), 5) > 4
并且：
max(QY_eff, QN_eff) > 50
则 Reject。
Fast Move
任一侧最近 1 秒 BestBid 移动达到或超过 2 ticks：
则 Reject。
最终 Entry 条件：
PriceGate
AND not Inactive
AND not DepthExtreme
AND not FastMove
AND CapitalGate

17. NORMAL Quote Size
基础 Clip：
5 shares
根据当前库存差：
qY = 5 + max(-Delta, 0)
qN = 5 + max(Delta, 0)
例如：
Delta = 0   → 5 YES / 5 NO
Delta = +2  → 5 YES / 7 NO
Delta = -3  → 8 YES / 5 NO
Entry 通过后同时提交：
BUY qY YES @ MY
BUY qN NO  @ MN
全部为 Post-only。

18. Quote Cycle
一个 Quote Cycle：
Post
→ Rest / Fills
→ Cancel
→ Reconcile
→ Final Inventory
同一时间只允许存在一个当前 Cycle。
核心顺序：
Cancel
→ Reconcile
→ Update Inventory
→ Next Action
旧 Cycle 未完成 reconciliation 前，不得开启新 Cycle。

19. 首次 Fill 前
尚无 Fill 时：
* Quote 有效 → Continue Resting
* Quote 无效 → Cancel → Reconcile → Requote
Pre-Fill QuoteInvalidated 包括：
1. Price Gate 失效；
2. Book Filter 触发；
3. Quote 不再对应 BestBid - TickSize；
4. Market Data stale；
5. Order State 不可信；
6. 市场不可正常交易。

20. Fill Window
第一笔真实 Fill 后启动：
FillWindowSec
参数可配置。
Phase 0 初始默认：
3 秒
窗口内：
* 保持当前 Cycle 剩余订单；
* 接受自然成交；
* 每次 Fill 后更新库存；
* 不开启新 Cycle；
* 普通盘口移动不重新定价。

21. Cycle Close
Fill Window 到期，或发生系统级异常：
Cancel All
→ Reconcile
→ Update Inventory
然后：
abs(Delta) < 5   → NORMAL
abs(Delta) >= 5  → REBALANCE

22. ActiveCycle
定义：
ActiveCycle = 至少发生一次 confirmed Fill 的 Quote Cycle
零成交 Quote 不计入 ActiveCycle。
单独记录：
ZeroFillQuoteAttempts
Phase 0 目标：
每市场 3–5 个 ActiveCycles

23. REBALANCE
进入条件：
abs(Delta) >= 5
停止双边 NORMAL Quote。
如果：
* Delta > 0：YES 库存较多，只买 NO
* Delta < 0：NO 库存较多，只买 YES
基础 Rebalance Order：
5 shares
唯一目标：
尽快恢复到 abs(Delta) < 5。

24. REBALANCE Price
使用：
HighestNonCrossingMakerPrice
计算：
Price = BestAsk - TickSize
并必须保持 Post-only。
原则：
Time Efficiency > Execution Cost
Rebalance Cost 记录，但不作为停止条件。

25. REBALANCE Loop
Read Inventory
      ↓
abs(Delta) < 5 ?
   │
   ├─ YES
   │    ↓
   │  Cancel Remaining
   │    ↓
   │  Reconcile
   │    ↓
   │  NORMAL
   │
   └─ NO
        ↓
CanSafelyContinue?
   │
   ├─ NO
   │    ↓
   │  Cancel Remaining
   │    ↓
   │  Reconcile
   │    ↓
   │  STOP
   │
   └─ YES
        ↓
Determine Deficient Side
        ↓
HighestNonCrossingMakerPrice
        ↓
CapitalGate
        ↓
Post / Reprice
        ↓
Fill / Market Update
        ↓
Update Inventory
        ↓
Repeat

26. CanSafelyContinue
定义：
CanSafelyContinue =
    TimeAvailable
AND CapitalAvailable
AND MarketTradable
AND StateReliable
其中：
TimeAvailable
剩余时间足以安全完成：
Order
→ Cancel
→ Reconcile
CapitalAvailable
FreeCash 足够提交最小合法 Rebalance Order。
MarketTradable
市场仍然接受合法 Maker Order。
StateReliable
Market Data、Order State、Fill State 均可可靠确认。

27. REBALANCE 停止逻辑
REBALANCE 只有两个出口。
Recovery
当：
abs(Delta) < 5
执行：
Cancel
→ Reconcile
→ NORMAL
Cannot Safely Continue
当：
CanSafelyContinue = False
执行：
Cancel
→ Reconcile
→ STOP
允许的 StopReason：
* TIME_EXHAUSTED
* CAPITAL_INSUFFICIENT
* MARKET_NOT_TRADABLE
* STATE_UNRELIABLE
以下情况不构成停止理由：
* 暂时没有成交；
* Rebalance 持续时间较长；
* 成交价格较差；
* Matched Cost > 1；
* 当前 Rebalance 处于亏损。
不存在 MaxRebalanceSec 硬停止规则。

28. REBALANCE Economic Attribution
Rebalance 前记录：
* LockedPnL_0
* WorstSettlementPnL_0
Rebalance 后记录：
* LockedPnL_1
* WorstSettlementPnL_1
计算：
RebalanceLockedPnLDelta
= LockedPnL_1 - LockedPnL_0
RebalanceWorstPnLDelta
= WorstSettlementPnL_1 - WorstSettlementPnL_0
同时记录：
* TimeToNormal
* PriceConcession

29. Natural Completion Economic Attribution
Natural Completion 不直接称为 Realized PnL。
记录：
* NaturalLockedPnLDelta
* NaturalWorstPnLDelta
分别衡量：
1. 新增了多少确定性匹配收益；
2. 改善或恶化了多少 Worst Settlement PnL。

30. 1-Tick Strategy EV
定义：
p = NaturalCompletionRate
Locked EV
定义：
* LN = AvgNaturalLockedPnLDelta
* LR = AvgRebalanceLockedPnLDelta
则：
LockedEVPerActiveCycle
= p × LN + (1 - p) × LR
用于衡量：
每个 ActiveCycle 平均创造多少新的确定性匹配价值。
Survival EV
定义：
* WN = AvgNaturalWorstPnLDelta
* WR = AvgRebalanceWorstPnLDelta
则：
SurvivalEVPerActiveCycle
= p × WN + (1 - p) × WR
用于衡量：
每个 ActiveCycle 平均改善还是恶化最坏结算结果。
不预设固定 Natural Completion Rate 门槛。

31. Market-Level PnL
Cycle Metrics 用于解释策略机制。
最终 Edge 由完整市场结果决定：
MarketPnL = WinningQty - C_total
288 个市场累计：
TotalPnL = sum(MarketPnL)
核心观察：
* AvgMarketPnL
* MedianMarketPnL
* WorstMarketLoss
* WinMarketRate
* MaxDrawdown
* EndingCapital
最终只有：
TotalPnL > 0
才能说明 Phase 0 在真实市场路径上产生正收益。

32. Phase 0 核心指标
Activity
* QuoteAttempts
* ActiveCycles
* ZeroFillQuoteAttempts
* ActiveCyclesPerMarket
Inventory
* QY
* QN
* Delta
* MaxAbsDelta
* BalancedQty
* ResidualQty
* TimeToNormal
* TimeInRebalance
Cost
* CY
* CN
* C_total
* BalancedCost
* ResidualCost
* AvgYESCost
* AvgNOCost
NORMAL
* FillRate
* NaturalCompletionRate
* PartialFillRate
* NaturalLockedPnLDelta
* NaturalWorstPnLDelta
REBALANCE
* RebalanceRate
* RebalanceLockedPnLDelta
* RebalanceWorstPnLDelta
* PriceConcession
* TimeToNormal
* StopRate
* StopReason
Economics
* LockedPnL
* WorstSettlementPnL
* BestSettlementPnL
* LockedEVPerActiveCycle
* SurvivalEVPerActiveCycle
* MarketPnL
* AvgMarketPnL
* TotalPnL
Capital
* FreeCash
* ReservedCash
* InventoryCost
* MinFreeCash
* PeakReservedCash
* PeakInventoryCost
* EndingCapital
* MaxDrawdown
* WorstMarketLoss

33. Phase 0 验证问题
最终回答：
1. BestBid - 1 Tick 是否能产生足够真实 Fill？
2. Natural Completion Rate 是多少？
3. Natural Completion 能创造多少 LockedPnL？
4. REBALANCE 平均多久恢复库存？
5. REBALANCE 对 LockedPnL 与 WorstSettlementPnL 的影响是多少？
6. 1-tick Maker Fill 的 Locked EV / Survival EV 如何？
7. $30 是否足够支撑 288 markets、864–1,440 ActiveCycles？
8. 最终 MarketPnL、TotalPnL、MaxDrawdown 是否证明策略存在真实 Edge？

34. Frozen State Machine
NORMAL
   ↕
REBALANCE
   ↓
 STOP
NORMAL：
BestBid - 1 Tick
+ Dual Maker
+ Inventory Size Skew
REBALANCE：
Highest Non-Crossing Maker
+ Single Side
+ Fast Risk Closure
STOP：
No New Risk

35. Frozen Accounting Principle
严格区分以下四个概念：
InventoryCost
真实库存买入成本。
LockedPnL
Balanced Inventory 已锁定的确定性经济收益。
WorstSettlementPnL
Residual 全部归零时的最终盈亏。
MarketPnL
市场正式结算后的真实最终盈亏。
以上口径不得混用。

36. Frozen Core
核心研究对象：
1-tick Maker Fill 的真实经济性。
过程层判断：
Natural Completion
+ LockedPnL
+ WorstSettlementPnL
最终层判断：
MarketPnL
+ EndingCapital
+ MaxDrawdown
最终由真实市场决定：
Keep / Modify / Drop
除非 Phase 0 的真实市场结果证明需要修改，否则不新增策略状态、过滤器或风险规则。

