# 当前 RDP-SM 冻结开发基线

2026-10-17 第一阶段同步旁路托管：每次`rdp-sm-run`按配置创建独立读取与记录线程，
仅绑定本次主进程市场/Session，退出时排空并保存完整snapshot与策略事件版本关联。
两份运行TOML显式开启，用户外层shell循环12次；程序内部仍只跑一个市场。
NORMAL、REBALANCE、资金/关闭与网络授权仍读旧路径，不消费旁路快照。
旧交易日志格式与市场选择/交易规则保持，旁路记录另存research_outputs。
完整说明见 [主进程托管同步旁路](RDP_PERIODIC_ORDER_SYNC_ATTACHED.md)。未启动真实交易。

2026-10-17 拒单快照复核耗时修正：账户快照仅刷新已经完整关闭证明中的拒单 ID，
共用一次完整开放订单及成交分页，不再为拒单重新串行核验全部历史已关闭订单。
读取期间恢复 UNKNOWN 和最大承诺；新订单/成交、分页失败、WS 事实或其他证据版本变化
继续阻断，局部刷新不能修复其他失效关闭证明。每轮完整收尾核验、账户/库存检查及
10 秒快照时限保持；REBALANCE、OBI、仓位、最小金额及日志格式不变。
详见 [拒单复核耗时修复](RDP_REJECTION_REFRESH_FIX_20261017.md)。未启动或重启交易，新进程加载生效。

2026-10-17 跨市场 Session 独立性修正（按用户重申，覆盖旧的跨市场账户恢复阻断规则）：
新目标市场没有自己的恢复标记时，不读取前一市场日志，不跟随账户文件中的旧会话指针，
不因旧 UNRESOLVED 状态触发 OBI_PRIOR_SESSION_UNRESOLVED。继续执行新市场的实时账户、
资金、持仓、盘口和交易时间检查；账户独占锁及同市场防重复启动保持。
旧日志、市场标记和旧异常状态保留历史事实，不伪造 CLOSED。真实只读新市场预检已通过。
详见 [Session 独立性修复](RDP_SESSION_INDEPENDENCE_FIX_20261017.md)。未启动交易。

2026-10-17 提交期间已撤销的回执兼容：严格识别身份绑定的
`order <ID> is invalid. Already canceled.` POST 错误，完整订单/成交分页无矛盾后按 REJECTED 关闭，
不再依赖不存在的订单详情。真实订单/成交证据仍走原核验，迟到事实恢复 UNKNOWN 与资金承诺。
补仓策略和日志格式、措辞均不改，历史日志及 UNRESOLVED 标记保持原样。
详见 [回执核验修复](RDP_CANCELED_SUBMISSION_FIX_20261017.md)。未启动交易，新进程加载后生效。

2026-10-17 NORMAL 挂单存续更新（覆盖下文“软反转不撤单”与挂单后 100 ms 过期关闭规则）：
下单前仍要求 100 ms 盘口/信号新鲜度；发单后单纯 `WS_BOOK_STALE` 不撤单，
同向或 NONE 保留原单，新鲜有效的相反信号触发 `SIGNAL_REVERSED` 关闭。
原信号起算 2 秒截止时间不重置；不自动反向下单，关闭核对完成后才允许下一轮。
明确断线、其他无效盘口及原有执行保护仍关闭。REBALANCE 的 3 秒及独立于 OBI 的规则不变。
详见 [挂单存续更新](RDP_NORMAL_ACTIVE_ORDER_20261017.md)。未启动交易，新进程加载后生效。

2026-10-17 撤单去重与收益报表修复：按关闭批次统一 DELETE 的逻辑操作身份，
单独撤当前订单与撤全部已知订单不再重复计为两个慢操作；原始范围和逐请求延迟保留。
修正完全 Merge 后配对收益重置为 0，分开呈现已验证合并毛收益与未合并配对估计，
残仓、返佣及净收益证据不足时继续 UNKNOWN。详见 [本次修复](RDP_CANCEL_DEDUP_FIX_20261017.md)。
未启动交易，新进程加载后生效。

2026-10-17 NORMAL 单侧更新（覆盖下文 NORMAL 双侧报价规则）：当前 `obi_v1` 仅在
信号侧挂一张 BUY，YES/NO 分别挂各自 BestBid，无信号不下单。未挂侧不参与最小金额与
资金承诺校验；单侧数量沿用既有 Skew/TargetDelta 计算。REBALANCE 仍按确认库存补缺口，
NORMAL 2 秒/REBALANCE 3 秒期限、一次性信号区间及网络/账户/关闭检查继续生效。
详见 [NORMAL 仅信号侧挂单](RDP_NORMAL_SIGNAL_SIDE_20261017.md)。未启动交易。

2026-10-17 网络门控更新（覆盖下文历史 p95 阻断规则）：30 秒 p95 仅作诊断，
单次超过 500 ms 只告警；最近 5 秒同方法最多 3 个独立操作中 2 个慢请求触发暂停。
同组订单的撤单重试去重。传输失败立即暂停；恢复使用 2/4/8 秒退避、故障后连续
3 次健康 `/time` GET 和一批真实策略订单验证，GET 不直接证明写接口恢复。
未知/未关闭订单继续阻断；100 ms 行情/信号时效、固定期限及资金/库存检查保持。
补充逐请求、状态变化和首次关闭原因日志；配置下次启动加载，未启动交易。
详细规则与验证边界见 [网络准入更新](RDP_NETWORK_GATE_20261017.md)。

2026-10-16 23:15 场后行情真实边界与 dust 更新（覆盖下文旧严格重连规则）：
执行 WS 的源时间差按整秒比较；不足一秒的回退正常应用，更早的旧更新忽略而不重连。
重连清空旧源时钟；双侧源时间比较也使用秒级，接收时效与信号年龄仍用 monotonic 毫秒。
新连接完整快照之前的增量只忽略并等待快照，不反复重连。
完整 socket frame 全部应用后，再以最新明确 BBO 清理矛盾缓存档位；保留真实更新数量，
缺少官方顶档数量时等待后续数据，不补造数量。交叉/锁定盘口、顶档待补仅使快照暂不可报价，
连接继续消费，后续完整数据可恢复。重复完整快照更新接收时效但不制造新信号事件；
重复 Tick 通知幂等，准备期间 REST/WS Tick 暂不一致等待重试。身份及非法数值仍拒绝。
统一忽略严格小于 0.1 股的持仓/净差额：启动清洁检查、报价 Skew、状态选择及 Data API
数量差异适用；恰好 0.1 股继续判断。确认成交、原始库存、资金成本与账本保持实际精度。
不足 0.1 对不触发 Merge；正常 Merge 上限为确认的 Session 配对数量，不合并外部 dust，
物理余额允许小于 0.1 股差异，但链上前后 token 与抵押品变化仍精确核对并写入审计证据。
修复终端结构化撤单回执误标 NOT_ACKNOWLEDGED 和 OBI 成交方向误显 0；
日志补充提交前候选失效原因、WS 代次变化与最终修复/旧包计数。
保留 100ms 接收时效/信号年龄、500ms RTT 与年龄加 RTT、单方向一次及 T+120 NORMAL 截止。
网络窗口中失败样本仍可阻止准入直到滚出 30 秒窗口；这些为保留的策略限制。
验证及其边界见 [本次复查](RDP_WS_REALITY_REVIEW_20261016.md)。未启动交易。

2026-10-16 WS 心跳、重建关闭与零订单中断修复：执行 Market WS 按官方协议每 10 秒
发送文字 `PING`，连接关闭等待上限设为 1 秒，避免故障后在旧连接关闭上长时间停留。
严格盘口顶档一致性、交叉及源时钟校验保持。OBI Session 在任何候选持久化开始之前、
零 Placement/已知订单/成交/Merge 且无其他硬故障时，人工中断只解除本次控制中断标记，
继续完整关闭核对与日志排空；通过后账户状态可写 CLOSED，结果保留人工中断原因。
候选写入开始前即设置不可回退的风险标记；取消 durable await、已准备候选、在途/未知
订单、日志失败等情形仍不能使用零订单退出路径。保留同市场重启限制，不自动启动交易。

2026-10-16 WS 启动恢复与诊断修复：将 `WS_NOT_CONNECTED`、`WS_DISCONNECTED`、
`WS_RECONNECT_REQUIRED` 纳入既有只读暂停/重试，启动预检遇到这些状态时在原市场截止前
等待 WS 重建并重新执行完整预检，不直接结束 Session、不切换市场、不允许无效盘口下单。
执行 Feed 保留最近一次解析/传输故障的类型、原因、连接代次和序号；预检超时、最终新鲜度
失败及 Session 等待日志携带该诊断，终端单独显示 WS 原始原因。未知传输异常仅记录类型，
避免代理凭证进入日志。交叉盘口、源时钟回退、身份及资金校验保持原规则。

2026-10-16 账户状态落盘修复：账户锁文件从 `a+` 改为 `O_RDWR|O_CREAT` 加 `r+`，
保留同一 inode 的独占锁；写入先 seek(0)、再覆盖并 truncate/fsync。
修复正常关闭将 CLOSED JSON 追加到 UNRESOLVED JSON 后，下一 Session 账户预检
因 `JSONDecodeError: Extra data` 持续失败的问题。畸形状态仍拒绝加载，不自动忽略或清空。

2026-10-16 网络门槛进一步调整：两份运行 TOML 的 `[rdp_sm.obi]` 及代码默认值
均将 `max_rtt_p95_ms` 从当前 400 放宽至 **500 ms**，覆盖下文的 400 ms 设置。
`max_signal_plus_rtt_ms=500` 仍独立生效：NORMAL 信号年龄同时受 100 ms 上限和
`500−P95` 剩余预算限制。盘口有效性、连续健康样本要求及两阶段订单期限保持原值。
新启动进程生效；本次未启动交易。

2026-10-16 网络门槛调整：两份运行 TOML 的 `[rdp_sm.obi]` 均显式设置
`max_rtt_p95_ms=400.0`，代码默认值同步由 250 改为 400 ms。
该门槛同时用于 GET/POST/DELETE 分组 P95、连续健康请求恢复、新单最终准入及活跃订单的网络失效关闭。
适用于 OBI NORMAL 和 REBALANCE。30 秒统计窗口、连续 3 个健康样本、100 ms 盘口/信号时效、
信号年龄加 P95 不超过 500 ms、NORMAL 信号+2秒与 REBALANCE POST+3秒期限保持原值。
新启动进程生效；本次未启动交易。

2026-10-16 运行配置调整：`config/config.toml` 与 `livecfg/config.toml` 均显式选择
`normal_entry_mode="obi_v1"`、`base_size="5"`；代码的默认基础数量同步改为 5 股。
每个 Session 的资本上限由 25 降为 **15 USDC**，继续使用
`rdp_sm.TEST_CAPITAL_LIMIT_USDC` 单一入口；M1/M2、Session 日志及报告分母同步采用 15。
实际数量仍按既有库存 Skew、TargetDelta 和最低量/金额约束计算，5 股不是逐单数量上限。
两份配置原有 `target_delta` 分别保持 0 和 1。此调整覆盖下文的历史配置说明，
新启动进程生效；本次只修改配置与默认值，不启动交易。

2026-10-16 开发更新：按 [NORMAL OBI 冻结方案](RDP_NORMAL_OBI_PLAN_20261016.md)
新增显式 `rdp_sm.normal_entry_mode="obi_v1"`。默认仍为 `legacy`，两份用户 TOML 未更改。
新模式由执行进程自己的官方 WS 重建盘口；NORMAL 使用 L1 OBI、信号侧 Bid/对侧 Bid−Tick、
真实信号事件起算 2 秒及每连续方向区间一次 POST；REBALANCE 使用独立控制器和 POST 起算 3 秒租期。
Sizing、TargetDelta、4.5 附近分类边界、25 USDC 上限、确认及 Merge 保持原契约。
新模式增加预签名候选、异步单 writer、逐腿签名 ID 和持续关闭，并使用账户独占锁及恢复标记：
未解决 OBI Session 不得通过换 Session/市场绕开；同市场异常重启不自动恢复 NORMAL。
这是新模式对下文历史 Session 隔离说明的特定恢复扩展，不导入 legacy 历史资金或成交。
工程结果与验证边界见 [开发结果](RDP_NORMAL_OBI_RESULT_20261016.md)。本次没有真实交易运行。

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

2026-10-15 人工修复：`MINIMUM_ORDER_EXCEEDS_MAXIMUM` 在提交前刷新时出现，
按“本轮不提交、已有订单完整安全关闭后跳过、下一轮重新读取盘口”处理。
相同提交前边界中的可重试GET错误复用既有恢复分类；无效官方时间沿用截止时钟暂停等待。
未创建本轮Placement且无fatal才允许安全跳过；关闭证明不足时仍禁止新增BUY。
认证/身份错误、未知下单结果、成交矛盾、日志失败和人工取消保留既有保护。
详见 [ITER-0036](ITER-0036/RESULT.md)。

2026-10-15 人工修复：成交/订单分页故障不再永久锁死当前Session。
完整重读原集合与原过滤范围后解除对应读取故障；已观察订单、成交和资金风险继续保留。
读取故障使旧关闭证明失效，必须重新通过当前Session订单终态、真实成交及完整账户准入。
当前市场未知pending允许状态演进，仅相同经济事实的正式CONFIRMED/FAILED解除；
解除当次仍暂停并要求下一次完整账户快照，遗漏不能清除风险。
持续故障仍暂停至T300；UNKNOWN写入、身份矛盾、日志失败等硬保护保留。
不读取上一Session，不改变Sizing/Timing/Pricing、Gateway、Merge或资本上限。
历史分页文档中的永久锁定限制由本次人工授权覆盖。
详见 [ITER-0037](ITER-0037/RESULT.md)。
