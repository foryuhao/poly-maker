# 11:00、11:05、11:10 三场与 Inventory Check 排查

只读分析，没有修改交易代码、配置或门槛，也没有发送或取消真实订单。时间为北京时间；T 为日志 wall time 减市场起点。用户提供的 T+135.285144806 使用官方时间代理，不能与 wall elapsed 完全等同。

| 市场 | Session | NORMAL / REPAIR | Merge | 已实现 Merge PnL | 退出账本残仓 |
| --- | --- | --- | --- | --- | --- |
| 11:00–11:05，1792378800 | 5c3bdb0c62fe45ca907799546b13ef9a | 16 / 1 | 5 对 | -0.4001996008 | YES 0.01 |
| 11:05–11:10，1792379100 | 9d33197d4ddc4c6f809a6155a312d02a | 11 / 8 | 15 对 | +1.10 | 0 |
| 11:10–11:15，1792379400 | 0d356940bbe64fe5bd4df3f08d4718f9 | 14 / 3 | 15 对 | +1.05 | 0 |

三场 run_result 都是 COMPLETED / SAFE_TO_CONTINUE，无 blocked window，合计已实现 Merge PnL 约 +1.7498004 USDC，不含 0.01 股后续结算。第一场仍发生账户核对等待，不能只凭 COMPLETED 判断过程中完全没有阻断。

## 用户引用的 T+135.285：整笔 5 股的 Data API 延迟

对应 11:10 场最后一轮：YES 5 @ 0.61，TP NO 5 @ 0.30。最后 NO 成交在 wall T135.413 升级 CONFIRMED，窗口 T135.451 关闭，经济账本 YES=15、NO=15。

随后 account 引用原日志第 4025 行（wall T135.502873），固定快照 version 198；第 4029 行（wall T136.509989）仍引用 version 199。两个快照的 positions 都来自 read_id `ebb7b7dcf1e74f3db392899971590bcd`，返回 YES=15、NO=10。因此核对差异为 YES 0、NO -5，严格 1e-6 和放宽到 0.2 都会阻断。

version 198 的 time 原始值为市场 T135，完成 wall T135.222153；用户显示的官方代理 elapsed 135.285144806 对应 wall 约 T135.507298，与上述 account 检查紧接的等待位置吻合。终端 `inventory_check` 本身没有持久写入策略 journal，本报告通过前后的账户引用和固定缓存源还原具体库存，不声称原日志保存了该整行终端文本。

wall T137.220152（快照 version 201，positions read_id `cfb4ecd1904f4e0488bd6302d5e014bd`）Data API 更新为 YES=15、NO=15，T137.515921 的下一次 account 检查已一致。最终 Merge 15 对，获利 1.05 USDC。

`BLOCKED_WAIT_UNTIL_300` 的实际逻辑是每秒复查，在一致后恢复，最多等到市场 T300；它不表示一旦发生就必须停满到 T300。此处命名会让人误解。

## 11:00 场：确实存在已知 0.01 尾仓与 API 显示差异

- YES 5 @ 0.53，路径 SL 买 NO 5 @ 0.55，先形成 5 对。
- 后续 NORMAL 窗口部分成交 YES 0.01 @ 0.55，按当前严格小于 0.1 的策略规则直接完成 episode，未为它发额外 REPAIR 单。
- Merge 5 对后，链上读取 `after_yes_raw=10000`、`after_no_raw=0`，明确保留 YES 0.01。账本成本和份额也保留。
- Data API 在 wall T255.046583 后的 positions 列表不再返回本市场 token；不能把列表缺席当作链上残仓已经消失。当前 positions 请求没有显式设置 `sizeThreshold`，API 小仓位过滤是可能原因，但仅凭日志不能确认服务端具体过滤策略。
- wall T256.938816 到 T300.187849，account 快照引用持续出现 Expected YES=0.01、Actual YES=0、Difference=-0.01；原有 1e-6 精确核对因此无法通过。

这里不是“策略需要补 0.01 股”；当前策略已经忽略它。问题是账户核对没有容纳“已经有成交和链上证据、但 Data API 列表未展示的尾仓”。

还有一个状态报告缺口：OBI 主循环在 T300 分支直接 MARKET_CUTOFF，最终执行状态仅按 failed/close proof 计算，没有把仍存在的 inventory_mismatch 独立反映进结果；所以本场 COMPLETED 与尾仓核对持续等待同时出现。关闭订单证明和账户一致性是两个事实。

## 0.2 股偏好的应用范围

用户提出小于 0.2 股不再主动补仓，可以作为策略尾仓门槛。但不能把每个“账本与 Data API 差额小于 0.2”的情况一律当作事实一致，或把账本真实数量、成本和 Delta 清零。

建议后续改动：

1. 策略统一使用严格小于 0.2 的免 REPAIR 门槛，包括 episode 完成、NORMAL/REPAIR 选择和残仓计划；保留精确账本和真实 session Delta 限额。
2. 对已有成交/链上证明、没有未解决订单或迟到成交的已知小尾仓，允许 Data API 列表不展示，单独记录留待结算；不把此例外扩大到未知订单、未知成交或整笔 5 股差异。
3. 整笔数据晚到显示为 WAITING_FOR_POSITION_SYNC，明确列出 expected/actual/difference；一致后自动恢复。尾仓留待结算使用不同状态，不继续打印等待数据一致直到 T300。
4. 退出结果分别反映订单已关闭、账户核对状态、留待结算尾仓，避免 COMPLETED 或 SAFE_TO_CONTINUE 被误认为零持仓。

本命令的当前流程在 T240 后做 Merge，在市场截止退出；没有在该路径中自动赎回单边尾仓。留待到期应保留为后续结算/赎回事项，不应在本 session 直接视为已经回收。

## 复核方式

运行 `extract.py`，从三份原始执行日志及固定缓存版本提取 `summary.json`。逐账户引用按此前已确认或实际仓位证明的经济成交还原账本，再对同一 version 的 positions 做比较。提取包括 SHA256、原日志行号、positions read_id、去重成交、REPAIR 决策、Merge 链上数量和三场结果。核对读取期间原日志/缓存未变化。
