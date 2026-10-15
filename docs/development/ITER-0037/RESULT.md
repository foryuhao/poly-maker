# ITER-0037 — Recover Complete Pagination Without Losing Exposure

日期：2026-10-15。关联：RDP-SIZE-001 后续运行故障。
基线：`b582525`，分支 `rdp-size-001`。
用户授权：修复本次Session分页故障永久锁死，并检查、修复同类问题。
沿用用户要求，不调用Architect/Reviewer；完成代码自查及离线回归。

## 故障与修复

问题Session：`9a815f1cc7b541619ba55a2afbef8512`，市场
`btc-updown-5m-1792073700`。一次成交分页读取失败后，
`pagination_failure=TRADES_PAGINATION_INCOMPLETE` 永不解除；后来完整读取和
订单Reconciliation成功仍不能恢复。它使库存修复中断，不能证明全部亏损均可避免。
历史事故数据及结算来源差异保留于原分析，不用模拟成交重写真实PnL。

本次修改三个生产文件：

1. `src/polymaker/rdp_sm_execution.py`：复用官方SDK分页和既有Gateway读取线程，
   分别记录成交/订单集合及其完整查询过滤条件的失败。
   只有从首个游标开始完整读取同一集合、同一过滤范围，才解除对应读取故障。
   单页、后缀游标、另一市场或另一集合的成功不能误清除故障。
   保留原 `pagination_failure` 只读判断接口；仍禁止故障未解除时POST。
2. 原 `_snapshot` 在任何恢复读取前拒绝，`_verify_close` 又可能没有订单或没有订单集合读取，
   两者会死锁。现先重读已失败查询，再执行原有账户、资金、订单终态和成交对账检查。
   不重新发送BUY；不把一个空列表当成订单关闭证明。
3. 增加读取故障revision，旧关闭证明不能因成功读取而自动恢复有效。
   新的关闭证明、终态、完整官方成交与本地真实确认库存一致后，才能继续。
   观察过的订单、部分分页成交和资金风险仍保留；自有成交若被后续完整页遗漏，仍阻断。
4. `src/polymaker/rdp_sm_entry.py`：发现当前市场 `AccountRiskPolicy.pending`
   同类问题。原代码把MATCHED→MINED及元数据变化当作证据冲突，并永久保留已正式确认/失败的pending。
   现比较解析后的市场、订单归属和成交经济信息，允许状态和时间元数据更新；
   完整读取明确显示相同经济事实的CONFIRMED/FAILED才解除pending。
   清除当次仍暂停一次，下一次必须重读仓位、余额、订单等完整账户快照。
   因此已确认的外部当前市场库存仍会被既有clean-market/库存一致性检查拦截。
   后续遗漏pending、身份/价格/数量变化和重复trade ID仍不能解除。
   复用这同一份pending策略，在每个官方成交页收到时立即保留当前市场未知pending，
   防止“第一页已见风险、第二页失败、下一次空列表”因策略尚未处理完整结果而漏记。
   逐页观察只能增加风险；只有完整查询及后续账户重检能解除，未建立第二份库存账本。
5. `src/polymaker/rdp_sm_recovery.py`：补齐成交/订单游标循环和订单页缺失游标的暂停分类。
   临时错误可在完整读取后恢复；持续错误观察至当前市场截止，不提前退出或放行下单。

## 同类检查与保留的边界

- 成交分页、订单分页、快照入口、无自有订单的关闭核对、同Session预检reader交接均已检查。
- `pending_confirmations` 原本每次关闭重新构建，保留。
- `observed_open_rows`、`observed_trade_legs` 保留已观察风险，不能当作故障标记清空。
- 既有取消任务在显式 `verify_close(cancel=True)` 时重置，支持重试，保留。
- UNKNOWN下单/Merge、Journal失败、终态与LIVE矛盾等硬失败不自动解除。
- Merge明确失败/准备失败后禁用本轮Merge是既有执行政策，不属于临时GET故障锁死，保留。
- 没有引入上一Session日志、历史订单恢复、跨市场结算或新的外部接口。

AST及未改模块哈希：[recovery-audit.json](evidence/recovery-audit.json)。
原历史分页SOP中“永久锁定、自动恢复延期”的描述，由本次明确人工修复授权替代；
历史报告不重写。

Timing、Pricing、Sizing/Favorite、状态阈值、3秒窗口、Gateway、WS、Ledger、
FillTruth、Merge、配置模型源码未改。资本上限25 USDC和两份用户配置TargetDelta=1保留。
用户配置的既有工作区修改不纳入本次提交。

## 测试

新增 `tests/test_rdp_sm_pagination_recovery.py` 共28项：

- 成交/订单分页 × 超时/游标循环/游标缺失 × 临时/持续故障，共12项。
  临时故障后真实单侧5股库存被后续修复并Merge；持续故障无重复订单，等待T300，
  真实确认成交不丢失，最终INCOMPLETE。
- 不同集合和过滤范围隔离；单页/后缀读取不能清除完整查询失败。
- 无自有订单时，快照或关闭核对都能恢复读取，不再死锁。
- 读取成功不能复活旧关闭证明；同Session reader交接保留所有失败范围和风险观察。
- 当前市场pending状态演进、遗漏、CONFIRMED/FAILED终态及经济/归属冲突。
  FAILED后完整重检可交易；CONFIRMED带来未解释库存时仍禁止任何BUY。
- 部分分页中的未知pending，随后遗漏仍阻断；后续FAILED可重检恢复，
  后续CONFIRMED且已有真实仓位仍禁止启动交易，共3项。

修复前首批12项：10 failed、2 passed；修复后最终28项全部通过。
原有分页风险测试保留，调整其中“故障应永久锁死”的断言：完整读取后，
LIVE外部订单/已观察正成交仍保留全部4.5 USDC风险占用，不得下新单；
只有原有双订单读取+完整成交的零成交终态证明才可释放。
原恢复测试保留并改为持续分页故障场景；临时故障恢复由新增集覆盖。
最终相关7组回归114项通过，最终全仓结果及精确失败集合见下方证据。

最终全仓：**1627 passed、13 failed、2 skipped，131.43秒**。
28项新增测试全部通过。13个失败测试ID与ITER-0036完全相同，无新增失败：
既有凭证隔离冲突1项、CLI日志污染JSON10项、旧冻结源码审计2项。
原始结果：[full-tests.txt](evidence/full-tests.txt)；
精确失败集合：[failure-parity.json](evidence/failure-parity.json)。
相关114项：[focused-tests.txt](evidence/focused-tests.txt)；
修复前复现：[repro-before.txt](evidence/repro-before.txt)。
控制台证据仅清理行尾空白，原始/保存哈希见[receipt-hashes.json](evidence/receipt-hashes.json)。
既有M0隔离器确认0 network/DNS attempts，禁止读取工作区凭证。
Ruff及Git空白检查通过。没有宣称全仓全绿或自动获得实盘授权。

## 运行说明

STATUS: CODE_READY（本轮恢复故障修复；全仓历史失败另列）。
本次未执行MicroLive、未读取工作区凭证、未调用真实账户接口、未发真实订单，未重启进程。
人工下次启动原有 `uv run polymaker ...` 时加载新代码；已运行进程不会自动更新。
无需修改参数或删除历史日志。每个Session仍独立；当前Session故障只在本Session内重检。
实际资金约束、有效盘口、官方时间和订单关闭证明均重新验证，不能保证未来成交或盈利。
