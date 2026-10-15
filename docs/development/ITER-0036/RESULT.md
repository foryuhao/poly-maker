# ITER-0036 — Recover Pre-submit Quote Constraints

日期：2026-10-15。关联：RDP-SIZE-001。
基线：`2898f8f`，分支 `rdp-size-001`。
用户授权：修复 `MINIMUM_ORDER_EXCEEDS_MAXIMUM`，并检查其他会提前终止Session的可恢复异常；
本轮不提交，确认已有订单关闭后，继续观察下一次合法报价机会。

## 原因与修复范围

用户Session `5c6289deba634b659c8c7124bb12d607`，市场 `btc-updown-5m-1792070400`，
在T164进入第12窗口：Delta=-8，SignedTarget=-1，理想买YES7。
初次决策价格0.07，需要14.29股以满足既有1 USDC最低名义金额；
随后价格降低使最低合法量超过15股上限，M2刷新抛出 `MINIMUM_ORDER_EXCEEDS_MAXIMUM`。
拒绝非法订单正确，但遗漏安全跳过分类，导致Session提前退出。

生产代码最小改动：

1. `rdp_sm_execution.py`：将该数量约束加入既有 `SAFE_QUOTE_SKIP_REASONS`。
   仍要求策略revision存在、本轮没有任何新增Placement、fatal为空；
   复用 `_verify_close(cancel=True)` 取消并核对全部本Session已知订单。
   只有新关闭证明为SAFE才记录跳过并允许下一决策。关闭失败则保留未关闭风险。
2. 同一M2提交前边界，复用 `retry_read` / `reason_code` 识别临时GET超时、429及5xx。
   原路径只存str(exc)，丢失异常类型而可能提前退出。现保留类型分类，安全关闭后再读取。
   已有资金/盘口/对账PAUSE分类保留原行为。不重试已创建Placement intent或已开始的POST。
3. `rdp_sm_loop.py`：安全关闭且零Placement的跳过结果统一记录原因，沿用现有1秒等待，
   下一循环重新取得账户、盘口和官方时间；不要求读取异常原因也出现在价格异常白名单。
4. `rdp_sm_recovery.py`：`OFFICIAL_TIME_INVALID` 加入既有暂停分类。
   已建立可信市场截止时间后，等官方时钟恢复或到T300，未恢复时不新增BUY。
   无初始可信时钟和市场身份的启动仍拒绝准入。

## 异常审查

复核M1/M2/M3/Merge显式异常及读取包装，静态登记144个 `_require` 字符串检查点。
完整清单与边界：[exception-audit.json](evidence/exception-audit.json)。

| 类别 | 处理 |
|---|---|
| 最小合法量超过数量上限、既有价格/Tick/名义金额限制 | 未提交且安全关闭后跳过，继续观察 |
| 提交前临时读取超时、429/5xx | 新增分类补齐；关闭后重新读取 |
| 无效官方时间 | 有可信截止时钟时暂停，恢复或T300结束 |
| 资金不足、盘口缺失/过期、订单读失败、待确认、分页不全、库存不同步 | 保留已有暂停/核对路径；未SAFE不得下新单 |
| 鉴权401/403、账户/市场/Token身份冲突、非法配置、签名/数量内部契约错误 | 保留拒绝启动或停止 |
| UNKNOWN Placement/Merge、已有订单未关闭 | 保留原关闭屏障及不可重复提交；未消除不确定性不得下新单 |
| 成交证据永久矛盾、Journal失败、人工取消、重复Session | 保留硬失败与原生命周期 |

没有把所有Exception都吞掉或直接continue。
AST对比仅 `execute_window` 和 `_run_session` 两个既有函数发生变化；
恢复分类函数本身未改。Sizing、Favorite、25 USDC资本上限、15股订单上限、最低1 USDC、
状态阈值、Timing、Pricing、3秒窗口、Gateway、WebSocket、Ledger和Merge源码未改。
已有两份配置的TargetDelta工作区改动未修改或纳入提交。

## 验证

新增 `tests/test_rdp_sm_quote_skip_recovery.py` 共17项：

- Target0/1：初次合法14.29股、最终刷新0.06超量；恢复后才下单，或一直观察至T300。
- 计划14.29、实际仅成交7：库存仅增加实际7股。
- 跳过时关闭读取持续503：不重新下单，等待至T300并保留INCOMPLETE。
- 有/无此前订单：最终准入超时、429、503或无效时间后恢复，无重复POST。
- 最终准入401和Token身份错误仍停止。
- 官方时间无效：恢复后才继续修复，或等待截止；真实确认库存保留。

修改前首批15项复现：13 failed、2 passed；失败覆盖数量约束、提交前读取和时钟边界，
两项硬失败保护已通过。修改后同批15项全部通过，均0 network/DNS attempts。
原始记录：[repro-before.txt](evidence/repro-before.txt)、
[initial-15-tests.txt](evidence/initial-15-tests.txt)。

最终全仓：**1599 passed、13 failed、2 skipped，116.70秒**。
新增17项全部通过。13个失败测试ID与ITER-0034完全相同，无新增失败：
既有凭证隔离冲突1项、CLI日志污染JSON10项、旧冻结源码审计2项。
原始结果：[full-tests.txt](evidence/full-tests.txt)；
精确失败集合比对：[failure-parity.json](evidence/failure-parity.json)。
既有M0隔离器确认0 network/DNS attempts，禁止读取工作区凭证。
控制台收据仅清理行尾空白，原始输出和保存文本的哈希均记录于
[receipt-hashes.json](evidence/receipt-hashes.json)。
未宣称全仓全绿或修复原RDP-SIZE-001的全部历史验收缺口。
Ruff、Git空白与AST范围检查通过；未执行真实交易/MicroLive，未重启运行进程。
按用户现有指令未调用Architect/Reviewer。

STATUS: CODE_READY（本轮异常恢复修复；全仓历史失败保留）
