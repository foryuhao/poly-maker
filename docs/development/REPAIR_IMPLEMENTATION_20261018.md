# REPAIR 第一版实现与验证

> 本文记录旧ev_legacy/Q模型实现。后续hd_tp_sl_v1开发版见[最新开发状态](REPAIR_HD_TP_SL_IMPLEMENTATION_20261018.md)；旧策略保留兼容，勿混同为新方案已上线。

本次把当前 `normal_entry_mode = "obi_v1"` 的库存修复接入 `RepairController`。
NORMAL 信号、方向及报价算法继续使用原实现；新增 session Delta 准入和 episode 占用检查。
代码已实现，模型参数仍为 **UNCALIBRATED**；离线算例通过不代表实际交易整体正 EV。

## 代码入口

|入口|职责|
|---|---|
|`src/polymaker/repair_model.py`|纯数值求值、合法数量及绝对期限候选、HOLD/BUY/KEEP/CANCEL、超限恢复|
|`src/polymaker/rdp_sm_repair.py`|真实盘口及波动率输入、后台求值、当前订单复核、过期结果丢弃|
|`src/polymaker/rdp_sm_lifecycle.py`|复用签名、post-only GTC、资金预留、独立撤单定时器和最终守卫|
|`src/polymaker/rdp_sm_execution.py`|统一经济成交、实际仓位证据、去重、session Delta、episode 原始实际量|
|`src/polymaker/rdp_sm_sync.py`|仓位证明后停止无效的 CONFIRMED 等待轮询，事实变化后重新排队|
|`src/polymaker/rdp_sm_merge.py`|T240 后使用同一经济账本核对并 Merge|

旧 `RebalanceController` 和 `normal_entry_mode = "legacy"` 保留历史兼容行为；
当前 OBI 协调器不再调用它。OBI 新窗口状态及 CLI 为 **REPAIR**，新增 `repair_windows`；
旧 JSON 字段 `rebalance_windows` 暂保留为兼容计数。冻结的 risk-shadow 观察器保持原样，
新策略的动作及数值以 `sm_repair_decision` 日志为准。

## 已落实的约束

- Buy Only、Maker Only、post-only GTC；经济模型不计费用或返佣。
- `Delta = Y_actual - N_actual`，整个 session 固定上限 5 股。
  最终 POST 守卫与候选数量同时检查。真实尾差不从经济账或风险中删除。
- 真实超限优先恢复限额，允许必要的负增量 EV 买入；完整恢复不可行时可以部分改善。
  `actual_limit_excess` 保留真实超额，`projected_delta_if_fully_filled` 仅描述计划全成交后状态，
  下单不等于已经恢复。回到限额内后，余单重新使用普通 EV 准入。
- 15 USDC 对整个 session 连续计账，不按 episode 重置；未知成交及可再成交量继续预留。
- 原始量取开仓实际成交。旧 `inventory_delta` 的严格小于 0.1 股忽略规则仅用于配平判断。
  OBI 实际仓位与经济账差异的核对精度为严格小于 0.000001 股；不会把未知 0.02 股当成零后再买
  5 股。无法解释的背景尾差先阻止准入，已知逐笔产生的尾差则完整连续入账。
  最低量/最低金额造成的合法反向残仓保留原 episode 方向，正常情况下不自动买回原侧。
- 数量搜索包含合法整股、最低合法量、精确缺口及资金/最大量边界，向内满足 0.01 股精度。
  仅最低规则所迫时超过配平量，不主动扩大投机仓位。
- 新单使用真实对侧 best bid。活动单保留原价及队列年龄，不随当前 best bid 追价。
  本地 ACK 时刻仅作为接受时间的明确代理，记录 `sm_repair_queue_origin`。
- 常规复核约 2 秒；经济事实变化可提前重算。KEEP 保留绝对 `cancel_at`，明确改变期限才记录
  `sm_repair_deadline`，不会通过普通复核自动重置 TTL。
- 模型后台运行，有独立计算期限；超时不使用半成品或过期结果发单。已有合法计划保留。
  市场重连、经济修订或可执行盘口变化会使对应结果失效。
- T240 停止新单并撤余单；撤单不等待后台求值。旧单与迟到成交可靠解决后才释放预留和替换。
  T300 未解决状态沿用既有持久化恢复保护，不冒充成功。

## 实际仓位与经济入账

`confirmed()` 仍表示原生 CONFIRMED；新 `economic_fills()` 合并原生确认与仓位对账证据，
按 `(trade_id, maker_order_id)` 去重，供库存、PnL、预算、NORMAL 准入及 Merge 共用。
后到 CONFIRMED 只更新同一笔经济事实，不重复增加份额。

仓位证明目前使用完整 Data API 仓位、资金余额及全部已知订单/成交的联合核对：

1. 已知订单身份、价量、逐笔引用及终态必须一致，目标资产无未解决的活动订单。
2. 完整仓位前后读取一致；数量与已知买入及已 Merge 数量连续解释。
3. 现金必须满足 `初始现金 - 实际买入成本 + 已 Merge 兑付 = 当前现金`。
4. MATCHED/MINED 不因超时或缺行变成零；FAILED 仅在仓位、现金及终态唯一解释时选择入账或排除。
5. 不完整、失败或矛盾为 UNKNOWN，继续保留风险与预留。不会使用读取失败得到的零。

原生状态不改写为 CONFIRMED。同步服务接受的是相同来源快照的经济证明，后续订单、成交、
仓位或现金变化后恢复待核对队列；该排队接口不授予交易权限。
原有 `ExecutionGateway.token_balance()` 的 RPC 失败返回 0 问题也已修正为返回 None。
新证明不依赖该旧单资产 RPC 方法，也未新增自动链上余额接管。

同市场重启沿用原有 fence：只撤销持久化的可能提交 ID 并保留恢复证据，不重置资金、
不重新开启 NORMAL，也不自动恢复 REPAIR 发单。这是本版保留的运行边界。

## 数值模型与继续策略的明确边界

默认 `rdp_sm.repair.model_version = "review_lease_v2"`。

- 价格核是以当前 YES 中价为锚的有界鞅，0/1 吸收，内部每秒方差为 sigma²；靠近边界缩减
  转移概率以保持合法。公平概率不按订单 tick 四舍五入。
- 成交强度同时依赖公平价与挂价距离、队列年龄；提交到达时可能 post-only 拒绝，撤单有延迟。
  模型对价格穿越默认全量成交的假设可配置为有限成交深度/概率；真实入账始终只来自执行证据。
- 当前实际对侧 bid/ask 与 tick 进入求值，未来盘口使用显式的公平价偏移和 tick 平移近似。
- 求值使用成交与价格的联合分布，累积 `dX * (成交时条件胜率 - 买价)`，同时计算二元兑付
  的终端方差。当前浮盈、局部等待波动及结算标准差分别记录，不简单相加成一个风险分数。

v2 采用一个有界、统一的继续规则，而非完整 Bellman 最优解：**单次 rollout 最多尝试一张
新订单**。根动作 BUY 消耗该数值额度，拒单也消耗；根动作 HOLD 或 CANCEL 在下一次计划复核
且旧单已经解决时，可以根据当时观察到的价格和成交量再优化一张订单或 HOLD。
根动作 KEEP 承诺在本次 rollout 内遵守所选旧单计划。截断后不新增订单，保留的计划按其
绝对期限结束，余仓按条件二元兑付评价。这不是实际累计提交次数限制，真实控制器继续滚动复核。

下一次价格分布用线性权重投影到继续求值网格，保留概率质量与一阶条件概率；这是数值近似。
原价格网格默认 0.005，继续网格 0.02，时间步 0.1 秒；数量和期限同样是有限候选搜索。
10 位小数并列处理只是数值排序规则，**不是已证明的误差上界**，输出显式记载
`numerical_resolution.certified_error_bound = null`。

旧 `bounded_martingale_v1` 保留研究算例回归，含旧的一/两单 rollout，不能把它的值当作 v2
的统一继续策略 Q。真实控制器整体 EV 仍需独立闭环回放/实际样本验证，不能累加局部 advantage 证明。

## 算例与性能

完整输入、候选及输出保存在 `reviews/20261018_repair_implementation/scenarios.json`。
使用开发演示参数、YES 原仓 5 股成本 1.5、sigma=0.02，普通盘口半价差 0.01：

|场景|v2 动作|模型终端期望 PnL|HOLD/撤后继续基线|
|---|---|---:|---:|
|T100 p=0.31，无活动单|BUY NO 5 @0.68，计划 T240 撤|0.06870926|0.06862703|
|T102 p=0.25，原 NO5@0.68 无成交|KEEP，计划 T240 撤|-0.12625359|-0.23142494|
|T102 p=0.30，原单同上|KEEP，计划 T240 撤|0.05747330|0.02117050|
|T102 p=0.35，原单同上|CANCEL|0.10000000|0.10000000|

p=0.35 的 0.1 是模拟撤单延迟内穿价全成交的结果，不是据行情推断实际已成交。
半 tick 中价 p=0.315、对侧 0.684/0.686、tick=0.001 的单独输入得到 HOLD，说明动作并非硬编码。
当前机器这组普通输入新单约 2 秒、活动单约 0.1–2 秒；更细继续网格可能超过 3 秒。
默认求值上限 3 秒，因此 2 秒是复核目标周期，不保证每轮计算必在 2 秒完成；硬撤单独立运行。

T100 继续网格 0.04/0.02/0.01/0.005 的动作均为 BUY；细化后的基线约 0.068631。
这组收敛检查仅支持这组算例，不是所有状态的数值精度或真实成交参数校准证明。

## 验证命令

```bash
PYTHONPATH=tests .venv/bin/python -m pytest -q
PYTHONPATH=tests .venv/bin/python -m pytest tests/test_repair_model.py tests/test_repair_marketdata.py tests/test_rdp_sm_repair.py -q
```

新增测试覆盖联合兑付恒等式、参考算例、HOLD 继续策略、真实负 EV 超限恢复、最低金额反向残仓、
真实秒级 sigma 与重连、MATCHED/FAILED 实际仓位、CONFIRMED 去重、缓存待核对队列、部分成交
余量、独立 T240 撤单，以及三轮 episode 共用 15USDC。既有签名、迟到接受/成交、拒单、资金、
Merge、fence 及 NORMAL 回归继续保留。

验证结果：全量回归 **1932 passed, 2 skipped**；最后的背景成交失效与尾差守卫补强后，
REPAIR、OBI 执行/资金、缓存及真实行情边界相关回归 **102 passed**。变更文件 Ruff 和
`git diff --check` 通过。背景开仓 FAILED 时会先撤可能超限的活动余单；撤单期间仍迟到成交的
场景保留实际超额，并验证后续负增量 EV 的限额恢复，不以发出撤单冒充风险已经消失。

本次仅运行离线测试及数值算例，未启动实盘交易。
