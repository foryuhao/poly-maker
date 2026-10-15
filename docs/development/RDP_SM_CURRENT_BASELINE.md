# 当前 RDP-SM 冻结开发基线

日期：2026-10-14。任务：RDP-RISK-002 Risk Shadow Mode v2。

人工批准分支 `single-market-pilot`，Commit
`a728c4db3d01dab2fddb2b9ac162c9c5bf4bb3a1`。DEVELOPMENT_BASELINE_APPROVED=YES。
本轮开发开始 HEAD 与批准值一致，工作树干净。项目及父目录无适用 AGENTS.md；
沿用 docs/development 文档、pytest 和现有离线网络/凭证隔离测试流程。
旧冻结 SOP 保留历史意义，不替代该 commit 的实际执行行为。

NORMAL：abs(确认 YES−NO)<5；双侧 BUY Maker，价格 BestBid−官方 Tick。
YES 数量 5+max(−Delta,0)，NO 数量 5+max(Delta,0)，仅提交数量向上取整至
0.01 股。任一腿低于现有最低名义金额时两腿跳过。

后续人工调整（2026-10-14）：当前状态判定先将确认 YES−NO 四舍五入到个位
（ROUND_HALF_UP），再比较绝对值与 5。绝对差额 4.5 起进入 REBALANCE；
原始库存、成交、报价数量计算保留原精度。以上未取整规则为批准 commit 的历史行为。

REBALANCE：买入不足侧，价格 BestBid；现有 maker_buy_size 覆盖库存缺口、
5 股/官方最小数量与 1 USDC 名义金额要求，提交数量沿用现有精度。
非法价格不自动截断。始终 BUY-only、maker-only、post-only，3 秒 fill window。

市场时间：真实 slug epoch 与官方元数据核验；T15 开始，T120 起不启动 NORMAL，
REBALANCE 到 T300；T300 停止新增 BUY，继续原有撤单/成交确认/关闭对账。
入口每次固定一个市场，不自动切换或自动调度多个市场。

后续人工调整（2026-10-15）：`NORMAL_END_S` 从 180 改为 120。
T15≤T<T120 允许 NORMAL/REBALANCE；T120≤T<T300 仅允许 REBALANCE 新挂单。

执行行情：_ExecutionReads.book 逐 Token 官方 HTTP tick-size 与 book；
SMWindowExecutor._read_books 验证身份、tick、官方时间新鲜度。
项目已有 MarketDataService 和按 Token OrderBook，使用官方 market WS；
当前执行入口不使用该 WS。本轮独立 Observer 复用 WS，不迁移交易行情路径。

实际库存：本 Session 确认成交减去已验证 Merge；现有 Data API 一致性检查。
订单、账户风险、RiskJournal、确认、Reconciliation、Fail-Closed 按批准 commit。
T240 起现有 Merge 可处理配平库存。人工接受现有 Merge 成熟性及行为，禁止本轮修改。
现有 Merge 后端使用 Blockchain RPC：这是既有事实，不表示符合 External Interface Rule。
Shadow 不初始化、调用或扩展 Merge/RPC。

历史五市场事实：2026-10-13 上海时间 22:50–23:15 五个相邻市场，四个 COMPLETED，
第五个 BLOCKED，T216 原因 DYNAMIC_TICK_UNKNOWN_OR_CHANGED。当前 HEAD 包含之后修复；
无法证明那五次运行准确源码版本，不称为“五个市场全部成功的实盘验证版本”。
对应 Session：da5f22a3442c4e649158d03511105ad5、8e967f9226a74a5ea53dff5c049ed17b、
61278a70e4f641b89bbaae1ade9daff9、59c1b555f9124c8ba85b3ac85c6959c6、
5629c7364cd749a58704def332b182ca。

以上历史限制及缺少自动多市场调度已经人工接受，不阻塞独立只读 Shadow。
本轮没有真实订单授权；OBSERVATION_ONLY 可使用无账户的官方公共 HTTP/WS。

2026-10-15 人工新边界：**每个 Session 是独立单元，不依赖其他 Session**。
移除钱包历史日志准入、旧订单启动查询、旧市场结算查证、恢复归档及历史资金预留导入。
新 Session 仅按当前市场和实时账户状态执行原 M1/M2；本轮订单、pending、资金、撤单、
确认与关闭保护保持。同一 Session 内暂时漏页仍保留风险观察；不跨 Session 继承状态。
日志改为 `rdp_sm_<funder>_<session_id>.jsonl`，Shadow读取对应当前文件。
旧日志保留但不读取或修改，旧历史/恢复 SOP 的跨轮要求由该人工新边界覆盖。
详见 [ITER-0033](ITER-0033/RESULT.md)。本轮按用户要求不调用 Architect/Reviewer。

2026-10-15 人工调整：每个 Session 的资本限制由 20 提高为 **25 USDC**。
复用 `rdp_sm.TEST_CAPITAL_LIMIT_USDC` 单一入口，M1预算、M2资金准入、Session日志及
报告利用率分母同步采用25；实际预算仍服从官方余额与allowance。新启动进程生效。
订单数量上限15股、最低BUY金额1 USDC及其余策略/执行规则保持本次调整前行为。
详见 [ITER-0035](ITER-0035/RESULT.md)。
