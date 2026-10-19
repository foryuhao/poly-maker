# REPAIR 风险与盈利修复设计依据

当前逻辑稿为 [REPAIR v4：以整体正EV为目标](../../RDP_REPAIR_EV_DESIGN_20261018.md)。v3的0.30/share强制修复门槛已撤回；下面的v3描述及本目录统计保留为历史依据，不代表v4成交优势或收益已验证。

[历史方案：REPAIR v3](../../RDP_REPAIR_RISK_PNL_DESIGN_20261018.md)、[旧 REBALANCE 盈利草案](../../RDP_REBALANCE_PROFIT_REPAIR_PLAN_20261018.md)。v3 区分累计 PnL 与从当前参考价值出发的前瞻风险，后者驱动 HOLD、PAIR 和 REDUCE_RISK；历史损失保留并单独限制新增风险。允许接受残仓及受控超量减险，零残仓不再是统一完成目标。本目录中的历史数据仍使用当时实际策略名称，不构成新规则的效果回测。

当前执行范围限定为 Buy Only、Maker Only：修复下单仅为对侧 post-only BUY；SELL、Taker、FOK/FAK 不属于实施阶段或能力缺口。风险超限不代表可以立即成交，挂单收益和风险改善均以实际成交为前提。HOLD_ACCEPTED 不算异常或盈利，超量后的反向仓位仍属于原 episode，持续占用预算。

盈利配对口径为 `配对兑付 - 开仓成本 - 补仓成本`；Merge 前全仓模型使用 `PnL_Y=Y-C`、`PnL_N=N-C`，不引入合并回款变量。T240 起的合并/最终兑付和已实现收益独立核对。不设置额外费用或必要成本扣减项；旧草案中的费用假设、强制等量配平和禁止超量的通用规则均不适用于 v2。

v3 的当前持有下行为 `p*Y+(1-p)*N-min(Y,N)`，修复候选使用同一决策参考点并计入追加支出；累计最坏亏损不再直接强制现有仓位配平。[场景与算术复核](../20261018_repair_forward_risk/README.md)。本目录原始统计和历史收益数据保持原口径。

[历史观察统计](evidence.json)、[逐轮 CSV](observations.csv)、[只读复算脚本](analyze.py)、[复算 Notebook](review.ipynb)。

本目录是方案的历史证据；首次报价算术不等于能成交的新策略回测，所有候选参数仍待验证。
