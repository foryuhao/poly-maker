# NORMAL 挂单存续与有效反转撤单（2026-10-17）

用户确认：去掉“盘口超过 100 ms 立即撤单”，将“挂单期间忽略方向反转”改为“有效反转就撤单”，保留原定 2 秒期限。

本次适用于 `obi_v1` NORMAL。下单前准备、激活、最终 HTTP 提交检查保持 100 ms 盘口与信号年龄门槛。
发单后的存续规则：

- `WS_BOOK_STALE` 单独出现时保留原单，等待行情更新或原期限到达。
- 有效同向信号或 NONE 保留原单；同向价格变化也不自动改价或续期。
- 有效反转必须来自较原始信号更新的、当前有效的双侧盘口，信号事件年龄不超过 `max_signal_age_ms`（当前 100 ms）。
  YES 订单遇到 OBI ≤ −0.30 的 VALID_NO，NO 订单遇到 OBI ≥ +0.30 的 VALID_YES，触发 `SIGNAL_REVERSED`。
  无效快照和过期反向信号不构成有效反转。
- watcher 沿用约 5 ms 检查间隔；撤单启动后不因信号再次变化而撤销关闭。
- 截止时间始终为原始信号事件 + 2 秒，不随更新、NONE、反转或 HTTP 回执而重置。
- 明确断线、盘口重建/交叉等其他无效原因、网络执行故障、日志失败、fatal 和市场截止仍保留关闭保护。
- 信号反转可以在 POST 回执之前启动撤单；原请求迟到接受后仍继续撤单和官方核对。
- 不在 watcher 内发反向单；原订单安全关闭后，下一轮重新检查库存、信号、期限和网络准入。
- REBALANCE 保留独立于 OBI 的原有规则，包括盘口过期关闭与 POST 起算 3 秒期限。

Session 记录 `normal_active_order_policy=FIXED_DEADLINE_CANCEL_ON_VALID_REVERSAL`，便于识别新进程采用的规则。
`sm_obi_close_requested` 的 `SIGNAL_REVERSED` 事件包含原方向、反向信号、事件年龄和触发盘口快照；
`sm_obi_signal_changes.policy` 区分 NORMAL 与 REBALANCE，历史日志不修改。

离线测试覆盖双向反转、反转发生于 POST 回执前、迟到接受后继续关闭、中性/同向保留、
盘口静默过期仍等原截止时间、过期反向证据不触发反转、明确断线仍提前关闭，以及原有 REBALANCE 回归。
测试不连接真实交易接口；未启动或重启交易进程。

验证结果：行情、执行及故障相关回归 119 passed（32.60 秒）；全量回归
`PYTHONPATH=src:tests .venv/bin/pytest -q --disable-warnings --maxfail=5`：
1798 passed、2 skipped（160.24 秒）。涉及的 Python 文件通过 Ruff，`git diff --check` 通过。
