# NORMAL / REBALANCE 当前服务器读取盘点

历史基线：本文盘点切换前 `5fc4c7c` 的读取路径与数量；第二阶段已改为共享缓存，改动后行为见 [接入说明](../../RDP_PERIODIC_ORDER_SYNC_EXECUTION.md)。本目录数量与来源哈希保留切换前证据，不作为改动后实际请求数。

日期：2026-10-17，北京时间。盘点当前 `uv run polymaker rdp-sm-run` 的 `normal_entry_mode="obi_v1"` 路径，仓库两份运行配置均使用该模式。仅检查代码与已安装 SDK，不发 API、不启动交易、不修改策略或配置。

**结论：NORMAL 与 REBALANCE 的常规服务器读取数量相同，二者共用 `BatchLifecycle`、`SMWindowExecutor._snapshot()` 和关闭核验。健康单页路径下，每轮基础快照 7 次，新建准备槽另 2 次 tick，一次关单核验另 4N 次读取。N 是本 Session 当前全部已知订单数，包含已结束历史订单及本次刚 ACK 的订单。**

“调用”区分三个层次：控制器方法调用、本地内存读取、真正服务器读取。本报告的计数针对服务器逻辑完整读取；一个完整集合若有多页，会转为多个 HTTP 请求。例子假设各集合单页、SDK 版本已缓存、没有异常、额外重试或 merge。

## 1. 两个控制器本身没有直接 HTTP

- NORMAL：`NormalController.enter()` → `BatchLifecycle.prepare()` → `activate()` → `_send()`。
- REBALANCE：`RebalanceController.repair()` → 同一组生命周期方法。
- 两者不是并行开启的两条轮询链路；单市场主循环按当前确认库存选择其中一个，执行器锁串行管理窗口。
- NORMAL 的 OBI 判断、REBALANCE 的缺口及报价/数量计算都在本地完成，不额外发 HTTP。NORMAL 可能预签 YES、NO 两个互斥候选，但最终只 POST 信号侧；预签两个候选不会把账户读取或 tick 读取再翻倍。

源码：[NORMAL](../../../../src/polymaker/rdp_sm_normal.py)、[REBALANCE](../../../../src/polymaker/rdp_sm_rebalance.py)、[主循环](../../../../src/polymaker/rdp_sm_loop.py)、[共享生命周期](../../../../src/polymaker/rdp_sm_lifecycle.py)。

## 2. 一轮基础快照：两种策略都是 7 次

当前主循环在选择 NORMAL/REBALANCE 和检查信号/网络准入之前调用 `executor._snapshot(include_books=False)`。

| 顺序 | 读取 | NORMAL | REBALANCE | 调用来源 |
| --- | --- | ---: | ---: | --- |
| 1 | Gamma 当前市场元数据 `/markets?slug=…` | 1 | 1 | `_snapshot` → `reads.market` → `exact_market` |
| 2 | Data API 全账户持仓 `/v2/positions` | 1 | 1 | `_snapshot` → `_account` → `gateway.positions` |
| 3 | 全账户未完成订单 `/data/orders` | 1 | 1 | `_account` → `gateway.open_orders` |
| 4 | 余额及 allowance `/balance-allowance` | 1 | 1 | `_account` → `gateway.balance_allowance` |
| 5 | 本市场完整成交集合 `/data/trades` | 1 | 1 | `_account` → `AccountRiskPolicy.inspect` → `gateway.get_trades` |
| 6 | 再读全账户未完成订单 `/data/orders` | 1 | 1 | `_snapshot` 的后续账户/占用核对 |
| 7 | 服务器时间 `/time` | 1 | 1 | `_snapshot` 末尾 `reads.server_time` |
| **合计** | | **7** | **7** | 单页且完整成功 |

余额与 allowance 是同一个返回结果，不算两次请求。`required_collateral_spenders()` 虽然异步经过工作线程，普通情况下只是读取缓存 SDK 版本、解析本地合约配置，不另发 HTTP。冷版本缓存第一次会额外读取 `/version`，下节另述。

全账户挂单每轮确实读了两遍，第一次是账户资金检查，第二次是后续订单身份、状态与预留核对。本市场成交虽然在账户检查时已经完整读取，关单阶段仍会按每个订单重读。

来源：[基础快照](../../../../src/polymaker/rdp_sm_execution.py)、[账户读取](../../../../src/polymaker/rdp_sm.py)、[账户策略读取成交](../../../../src/polymaker/rdp_sm_entry.py)。

### 不下单也可能执行这 7 次

主循环顺序是：

```text
前一窗口不安全则先补做关闭核验
→ 完整基础快照（7 次）
→ 本地汇总库存，必要时 merge
→ 选择 NORMAL / REBALANCE
→ 检查交易时段、WS 盘口及网络准入
→ 尝试准备与激活
```

所以以下情况仍可能反复走基础读取：无有效信号、准备后等不到合法信号、网络准入暂停、WS 盘口无效，以及 NORMAL 已到 T120、库存仍属 NORMAL、等待 T300 结束的阶段。T300 在外层循环开头检查，达到后进入最终关闭，不再继续普通循环。

这里不能按“成功 POST 次数 × 7”计算整场账户查询数，因为一笔 POST 之前可能经历多轮无提交循环。

## 3. 准备新挂单：另外 2 次 tick

`BatchLifecycle.prepare()` 对 YES、NO 两个 token 都主动清除 SDK tick 缓存，再分别调用 `get_tick_size()`：

| 准备相关操作 | NORMAL | REBALANCE |
| --- | ---: | ---: |
| YES tick `/tick-size` | 1 | 1 |
| NO tick `/tick-size` | 1 | 1 |
| 新准备槽合计 | **2** | **2** |
| 合法缓存准备槽被复用 | **0** | **0** |

即使 REBALANCE 只交易缺口侧，也查两个 tick。准备槽复用只省此准备阶段查询，不会省掉主循环此前已执行的 7 次基础读取。其他原因使准备提前返回，可能还没走到这两次查询。

底层已安装 `py-clob-client-v2==1.0.2`：`create_order()` 内部还会调用 `get_tick_size()`，但该路径已预热缓存，且已显式提供 `neg_risk`、tick，V2 不读 V1 fee，因此通常不会为每个候选再次产生 GET。`__resolve_version()` 在准备、账户、签名阶段有多处调用，但已缓存时为 0 HTTP，不能每个方法调用都算一次 `/version`。

## 4. 发单、挂单监视和发撤单请求前：状态查询为 0

| 操作 | NORMAL | REBALANCE |
| --- | --- | --- |
| 实际 POST 前最终检查 | 读取本地盘口、版本、资金预留及健康状态 | 同左 |
| 实际 POST 发送段 | 0 次 GET，显式禁止额外 GET | 同左 |
| 挂单监视 | 本地 WS 盘口、OBI/期限/网络状态 | 本地 WS 盘口、期限/网络状态 |
| 发撤单前 | 按已知或预算签名 ID 直接 DELETE，0 次前置状态 GET | 同左 |

`SessionFeed.snapshot()`、`NetworkHealth.snapshot()` 是本地内存读取。watcher 没有固定频率查询订单状态。它会约每 5ms 检查本地状态，触发关闭后发 DELETE；每次 DELETE 完成后等待 `cancel_retry_sec=0.25` 秒再继续，直到核验完成。DELETE 网络耗时、限流等待也计入实际轮次间隔，不能理解为严格每秒四次撤单。主发送流程和全量关单路径也有自己的撤单入口，可能叠加 DELETE。

后台网络探测和独立旁路仍在运行，不能把这段主执行逻辑的“0 次 GET”误说成进程在挂单期间完全不发 GET。

## 5. 关单核验：每个已知订单 4 次

主路径为 `_send()` → `_verify_close(cancel=True)` → 遍历 `sorted(self.known)` → `_verify_order(oid)`。

| 同一个订单的核验顺序 | 查询 | 次数 |
| --- | --- | ---: |
| 1 | 详情：先验证签名订单的市场、token、方向、数量、价格 | 1 |
| 2 | `reconcile_owned_order_once` 再读订单详情 | 1 |
| 3 | 同一个 reconcile 读取本市场完整成交集合 | 1 |
| 4 | `_verify_order` 最后再读订单详情，核对最终终态及数量 | 1 |
| **每订单合计** | **3 次详情 + 1 次本场成交集合** | **4** |

这四个请求对同一订单按顺序等待，不是并行请求。外层订单遍历也是逐个 `await`，因此健康单页一轮为 **4N 次串行逻辑读取**，不是只核验当前那一个短命订单。`self.known` 是本 Session 累积集合，不会因订单已取消或成交完毕就从普通核验循环排除；merge 后这些 ID 也仍可能存在。

本轮 `_verify_close()` 会清空 proofs 并重新验证，旧终态/旧证明不用于跳过每个普通 known ID 的读取。此前对拒单循环的修复只优化了 `_snapshot()` 中严格拒单证明的复核，没有把这里的 4N 全量关闭核验迁移走。

源码：[逐订单核验](../../../../src/polymaker/rdp_sm_execution.py)、[FillTruth 内层读取](../../../../src/polymaker/execution/filltruth.py)。

## 6. 一次健康新挂单窗口的基础总量

前提：一次主循环就成功新建准备槽并提交 ACK，只需一轮关单核验，各集合单页，SDK 版本缓存已热；不含下节列出的异常、旁路、网络保温、启动、最终关闭及 merge。

```text
基础快照 7 + 新准备 tick 2 + 一轮关单 4N = 9 + 4N
```

| 本 Session 累计 known 订单 N（含当前 ACK 单） | NORMAL | REBALANCE |
| --- | ---: | ---: |
| 1 | 13 | 13 |
| 5 | 29 | 29 |
| 10 | 49 | 49 |
| 15 | 69 | 69 |

准备槽复用可比此少 2；多一次无提交基础循环至少再走相应基础读取。错误或提前退出时某些读取不会执行，所以这些数字既不是所有分支统一最小值，也不是上限，更不是整场实测均值。

多页时不能沿用 4N：每个订单为 3 次详情 HTTP，加该订单那次完整成交集合的全部页。两次全账户挂单和持仓也各自完整翻页。所有集合分页均须计入实际 HTTP。

## 7. 条件分支的额外调用

以下对 NORMAL、REBALANCE 共用，不能简单归为某一策略独有：

| 条件 | 额外读取及触发范围 |
| --- | --- |
| `_snapshot` 开始时有 M 个 known ID 尚无证明 | 每个 1 次详情 + 1 次本场成交完整集合，即单页 2M；健康前窗已关闭时通常 M=0 |
| `_snapshot` 有严格 `Already canceled` 拒单证明需刷新 | 所有相关 ID 共用 1 次全账户挂单 + 1 次本场成交，单页固定 2 次，不再按历史订单全量重复 |
| 关闭中有 UNKNOWN/INTENT 放置结果 | 本轮增加 1 次全账户挂单查找 |
| 关闭中有 C 个严格拒单证明候选 | 当前每候选另读 1 次挂单集合 + 1 次成交集合，单页 2C；与上一行、剩余 known ID 核验可叠加 |
| 历史外部 BUY 已不在两份当前挂单列表里，尝试释放其保守占用 | 每个候选最多 2 次详情 + 1 次其市场成交；不满足条件可提前返回，不能统一算满 3 次 |
| 成交待确认 | 等待最多 2 秒后，再做完整 `_verify_close()`，单页健康基数又是 4N；该 waiter 没有传 cancel=True，但仍可能有独立 watcher DELETE |
| 前一窗口 barrier 不安全 | 下一轮基础快照之前，再调 `verify_close(cancel=True)`；失败可暂停 2 秒后重试 |
| 分页失败 | `_recover_pagination()` 对每个失败的集合/筛选范围从第一页重读；恢复后仍会继续当前正常读取 |
| `WS_TICK_UNVERIFIED` | 额外调用 `refresh_feed_ticks()`，YES/NO 共 2 次 tick；之后新建准备槽时仍可能再查 2 次 |
| 持仓对不上 | `_wait_for_inventory()` 额外查 1 次 `/time`，等待最多 1 秒，再进入下一轮完整账户快照 |
| SDK 版本缓存冷 | 额外 1 次 `/version`，正常重复 resolver 调用只用缓存；主 POST 发送段禁止额外 GET |
| Session 正常结束 | 再做一次全量关闭核验，普通单页 4N；有异常时异常处理入口也可能重新关单 |
| T240 以后有可合并配对且成功 merge | merge 前后各做一次全量关闭核验，单页另 8N；还包括结算 SDK 市场元数据、链上余额/交易回执和 relayer 轮询，属于独立结算 I/O |
| 结束时检查 merge | 未传 elapsed 时、且已有 known ID，会另查 `/time`，即使最终没有可合并配对也可能发生 |

merge 不能简单并入一次 NORMAL 或 REBALANCE 窗口，因为它在主循环选控制器之前及会话最终关闭后触发。这里的 8N 仅是额外的执行器订单/成交读取，不包括结算 SDK 的请求数，也不包括主循环最终关闭本身已有的那轮 4N。

## 8. 两套独立后台读取

### 原有网络保温

`_run_obi_session.warm_network()` 在整个策略阶段独立循环：

```text
GET /time → 等请求完成 → sleep(0.5s) → 下一次 GET
```

因此不是固定每 500ms 开始一轮。若每次 HTTP 约 200ms，理论开始间隔约 700ms（约 1.4 次/秒）；这仅为调度示例，不是本次代码盘点重新测出的速率。`executor.network_health()` 自身只读本地样本，不会再发探测。

### 新同步旁路

目前仍与旧策略查询同时存在，策略不会读取它的快照：

| 通道 | 目标周期 | 每轮逻辑读取 |
| --- | --- | --- |
| 订单通道 | 开始到开始 1 秒 | 1 次 `/time` + 1 次全账户挂单 + 1 次本场成交 + K 次待核验详情，K≤4 |
| 资金通道 | 开始到开始 2 秒 | 1 次余额/allowance + 1 次持仓 |
| 模块初始化 | 每 Session 一次 | derive-api-key 与首次 `/time` 各 1 次 |

单页且守住目标周期时，常态约 `4 + 平均K` 次 GET/秒；无待查详情时约 4 次/秒。分页、失败退避、慢轮及退出排空另算。这部分不分别归入 NORMAL 和 REBALANCE，也未包含在 `9+4N` 中。

## 9. 启动阶段，单独计账

健康情况、入口发现时已过 T15、无当前市场恢复 fence、无重试/额外 tick 验证、各集合单页：

| 阶段 | 逻辑读取 |
| --- | --- |
| 发现市场 | 1 次 `/time` + 1 次 Gamma 市场，合计 2 |
| `SMTradingLoop._run` 首次预检 | 1 次市场、1 次 derive、首次双侧 tick 2 次、持仓/挂单/余额/成交各 1 次、冷 `/version` 1 次、时间 1 次，合计 10 |
| `SMWindowExecutor.open` 再次预检 | 复用 WS feed，不再做首次 tick，其余同上，合计 8 |
| 最终执行器新客户端连接 | 另 1 次 derive；该客户端的冷 `/version` 在首个运行期账户读取时发生 |
| **运行期主循环前健康基数** | **21 次**；不包括新旁路初始化及 T15 前的时间等待 |

这不是每个窗口重复的 21 次，也不是启动全分支上限。发现/预检失败会重试；当前市场恢复 fence 存在时会查询候选订单和成交并做清理，属于异常重启路径。本次不以旧 Session 日志作为新市场运行依赖。

## 10. 后续迁移需要覆盖的实际入口

按代码盘点，整合工作应覆盖共同读取链路，单独改两个 controller 不会消除这些请求：

1. 主循环 `_snapshot()` 里的账户/订单/成交读取，以及不下单时反复走的同一链路。
2. `_verify_order()`、FillTruth 及整场 known-ID 关闭循环里的重复查询。
3. `_wait_for_confirmation()`、UNKNOWN 查找、严格拒单证明刷新、分页恢复、外部占用和持仓等待分支。
4. 原有 `/time` 保温、运行期校时和新旁路 `/time` 的重复；保持恢复探测语义。
5. Session 最终关闭及 merge 前后的关闭入口。

WS 盘口保持现有推送与本地读法；tick/version/市场静态元数据的缓存策略应单列，不把它们混同于资金/订单快照的新鲜度。POST/DELETE 的写入路径与操作日志仍由执行器负责。

本报告仅盘点，没有实施整合或更改缓存语义。短命订单不必为了审计而出现在当前挂单快照中；后续消费规则应结合执行器本地操作结果和服务器当前观测。

## 可复核证据

- [结构化数量、条件分支和 19 个来源文件哈希](evidence.json)
- [AST 调用点索引](call_sites.csv)：129 行是源码调用/方法引用索引，包含条件分支、辅助函数和 legacy 分支，不能当作 129 次 HTTP。
- [复算程序](audit.py)：不导入交易执行器，不加载凭证或发网络；检查两个控制器确实共用生命周期、7 次基础读取组成、3+1 关闭核验及 SDK 版本。
