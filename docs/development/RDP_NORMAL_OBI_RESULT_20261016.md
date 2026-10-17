# RDP NORMAL OBI 开发结果

日期：2026-10-16，北京时间。依据 [冻结方案](RDP_NORMAL_OBI_PLAN_20261016.md)。
开发基点 `c9136c93cc40d6bc534df6b60610e13df8326c79`，分支 `rdp-size-001`。
未提交 Git commit；所有变更留在工作树供审阅。未启动实盘、未发送真实订单或撤单。
状态：工程实现及本次离线验收完成；真实运行与未见市场验证留待明确运行任务。

## 实现

| 模块 | 实现行为 |
| --- | --- |
| `rdp_sm_marketdata.py` | 当前 Session 独立官方 WS；双 Token BOOK/DELTA 严格校验、完整 frame 原子发布、不可变快照、socket wall/monotonic 与处理时间分别记录。坏帧、断连、丢帧、时钟故障使旧书失效，重连重建；DELTA 不能恢复坏书。重复 frame、TICK、TRADE 不刷新旧书年龄。 |
| `rdp_sm_signal.py` | 四个真实 L1 量、Decimal 联合 OBI、±0.30 包含边界、NONE/INVALID 分离；Session 信号区间及原子 claim，HTTP 开始时消费资格。无成交、拒绝、UNKNOWN 均不重复入场；INVALID、重连、阶段切换不重新授权。 |
| `rdp_sm_normal.py` | NORMAL 独立控制器；YES/NO 镜像报价，各自 tick；原 Sizing、Skew、Favorite/TargetDelta 与最低量/金额约束保留。无信号、任一腿非法或预算不足不提交。 |
| `rdp_sm_rebalance.py` | REBALANCE 独立控制器；确认缺口侧 Bid，沿用数量算法，独立 POST+3 秒租期。合法空对侧不阻断修复；无有效 Favorite 回到原有目标 0。 |
| `rdp_sm_lifecycle.py` | 账户/资金证据先完成；准备互斥候选、SDK 签名及逐腿 ID，一批 PREPARED 记录持久化后等待新的真实 WS 事件。每槽只发一次，只发送原签名；最终 guard 允许不改变方向及报价的深度更新，拒绝过期、断连、价格/tick/风险变化。 |
| `rdp_sm_network.py` | 当前 Session 有界请求样本；GET、POST、DELETE 分别计算 P95，保留失败，整体证据检查新鲜度；连续健康样本与滚动门槛共同恢复。仅公共 GET 暖机，无探测性真实写单。 |
| `rdp_sm_writer.py` | 有界单 writer，后台 JSON/flush/fsync；PREPARED 等待完成期间行情及撤单仍运行。普通日志在操作之后入队，固定 L1 字段取代整本深度复制。容量不足/磁盘失败禁止新增并优先关闭。取消 durable await 不取消正在执行的写入或破坏 writer。 |
| `rdp_sm_fence.py` | 账户 OS 独占锁、未解决 Session 指针、同市场信号区间恢复屏障。只查询/撤销本模式持久化的候选签名 ID，不按价格猜归属、不 cancel-all、不导入 legacy 历史。旧风险未解决时换 Session 或市场也不能新增。 |
| `execution/gateway.py` | 签名准备与原签名发送分离；操作段额外 GET 被拒绝。结构化逐单撤单结果，保留 not-canceled/unknown；DELETE 使用独立线程容量。旧 `place/cancel` 契约继续保留。 |
| M1/M2/M3/入口 | 新模式 discovery 不读 REST book；M1、两阶段与最终校验均用执行 WS；协调器串行授予两个控制器交易权，旧完整 `execute_window` 在新模式被明确禁止。行情/网络门禁失败不会切回 legacy。 |

NORMAL 两腿共同截止于选定真实信号事件+2 秒，提交延迟计入其中；POST、ACK、新行情不续期。
在途请求仍由原任务持有并排空；关闭监测在 POST 接受前就可按准备 ID 发 DELETE，
not-found 及 HTTP 200 逐单失败均不能证明关闭，晚接受后继续撤单并核对官方订单/成交。
丢 ACK 的可靠签名身份经官方关闭证明后记为 `VERIFIED`，不伪造 ACK。
未知、部分成交、MATCHED/MINED、分页故障继续保留风险和原有确认屏障。

## 配置与恢复

默认模式是 `legacy`。以下仅说明未来明确运行任务中的显式配置，本次没有写入用户 TOML：

```toml
[rdp_sm]
normal_entry_mode = "obi_v1"

[rdp_sm.obi]
threshold = "0.30"
signal_horizon_sec = 2.0
rebalance_order_ttl_sec = 3.0
max_book_age_ms = 100.0
max_signal_age_ms = 100.0
max_pair_skew_ms = 100.0
max_rtt_p95_ms = 250.0
max_signal_plus_rtt_ms = 500.0
network_min_samples = 10
network_window_sec = 30.0
network_sample_max_age_sec = 2.0
network_recovery_samples = 3
cancel_retry_sec = 0.25
repeat_same_signal = false
```

`rdp_sm.obi` 拒绝未知字段、NaN、非正预算和不一致时限；首版不能打开持续同向重复入场。
两 Token 的接收时差及源时间差共用显式 `max_pair_skew_ms` 预算。
网络的“最新样本≤2秒”作用于当前整体证据，各请求类在30秒窗口内单独保留 P95/失败；
否则 NORMAL 的2秒成交窗口结束后会因上一条 POST 超龄而永久不能产生下一次写入。
无近期写样本时明确记录 GET 代理健康证据，不能据此宣称写链路已经测量。

执行日志记录实际配置目录、mode、最终/候选快照、连接代次、四个 L1 量、OBI、报价/数量/tick、
准备和持久化时刻、区间/事件/POST、共享 deadline、撤单请求/SDK 开始/ACK、关闭和 writer 状态。
Shadow 保持独立连接和独立 writer。操作后兼容策略事件明确标注执行 WS 来源，Observer 的
1秒采样只用于其自身比较，不替代执行快照，也不成为交易依赖。

账户锁文件 `rdp_obi_account_<hash>.json` 与市场标记 `rdp_obi_market_<hash>.json` 位于实际 journal 目录。
只有关闭证明与日志排空均完成才将账户状态持久化为 CLOSED。
同市场重启仍无法重建连续信号资格，首版继续清理候选风险并返回
`OBI_RESTART_NORMAL_DISABLED`；其他市场遇到旧未解决 Session 返回
`OBI_PRIOR_SESSION_UNRESOLVED`。恢复不自动重发、清空标记或重开 NORMAL。

## 验证

| 最终检查 | 结果 |
| --- | --- |
| 全仓 pytest | **1701 passed，2 skipped**，137.52秒；跳过的是既有显式 live market-data 测试 |
| 其中新模式专项 | **60 passed**，包含软方向变化留证但不提前关闭/重挂 |
| 日志重新配置新增回归 | **1 passed** |
| 定向 Ruff（实现及新增测试、维护的 RDP/Shadow 契约） | **通过** |
| `git diff --check` | **通过** |

最终命令：

```bash
uv run pytest -q -p no:cacheprovider -o 'pythonpath=src tests' \
  --junitxml=research_outputs/RDP_NORMAL_OBI_DEV_20261016/pytest.xml
```

证据：`research_outputs/RDP_NORMAL_OBI_DEV_20261016/pytest.xml`、`MANIFEST.json`、`VALIDATION.md`。
早期全仓运行的24个别名隔离失败及随后10个日志探针污染失败分别保留在
`pytest_initial.xml` 和 `pytest_after_alias.xml`；修复后全仓已通过。
最终加入软信号诊断后再次完整运行得到上表结果。

专项测试直接使用真实 M2/Gateway/SDK 签名、Tracker、账户准入及关闭逻辑，仅在官方 HTTP/WS、
时钟、磁盘边界注入故障；计时与在途 POST/DELETE 竞态使用真实 event loop 和线程同步。
覆盖一次性区间、阈值、非法量、时钟/队列年龄、镜像报价、无 OBI 修复、T120 后修复、
原签名正文、两秒/三秒、部分拒绝、丢/晚 ACK、先撤后接受、逐单失败重试、
慢落盘、运行中磁盘故障、读池拥塞、调用者取消、同市场/跨市场恢复及独占锁。
签名 ID 对普通/neg-risk domain 和 0/1/2 签名模式，使用独立 ABI/EIP-712 公式与 SDK 交叉验证。

原方案列出的三个过时断言已维护：真实 sizing schema 保留严格校验；历史冻结检查与后续开发分开；
Shadow 的等价行为和只读能力继续验证。未删除或跳过失败用例。
全仓回归还发现 CLI fixture 仅设置旧凭证别名、以及 FillTruth 日志探针污染惰性 logger 的问题；
修复隔离和探针恢复方式，保留凭证冲突检查及 stdout 单 JSON 的严格断言。
日志代理另加重新配置回归，避免库先使用 logger 后绕过 CLI stderr/writer。

全仓 Ruff 仍有 **91个存量问题**，与 HEAD 基线逐文件对比一致，位于 filltest、
userstream parser、engine/execution/filltest/filltruth 测试；本次涉及实现和新测试的定向 lint 通过。
`test_filltruth.py` 本次维护日志探针，其原有两个 lint 问题也保留披露。
没有为获得全仓 lint 绿色重写 FL、Merge 或未涉及的策略。

## 实际边界

本次完成工程实现与离线验证，未运行新的未见市场实盘验证，也未对47场旧研究样本宣称收益。
2秒是本地请求关闭的目标，不保证交易所暴露时间、撤单实时性或成交；日志区分实际滞后。
自身小额挂单对公共盘口的影响按冻结要求忽略，NORMAL 不要求等量；残余风险与配对部分单列。
仍须在后续明确运行任务中核对实际配置、钱包、观察范围，并用新的执行证据评价时延和盈利性。

协议依据：[官方逐单撤单结果](https://docs.polymarket.com/trading/manage-orders#cancellation-results)、
[官方订单生命周期](https://docs.polymarket.com/concepts/order-lifecycle)、
[官方 V2 订单 Hashing 合约](https://github.com/Polymarket/ctf-exchange-v2/blob/main/src/exchange/mixins/Hashing.sol)。
2026-10-16 只读核对；没有修改签名算法、认证或官方 SDK。
