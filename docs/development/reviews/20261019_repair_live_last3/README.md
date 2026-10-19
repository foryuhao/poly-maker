# 2026-10-19 最近三个实盘市场复盘

本报告只读取本地执行日志及同步缓存，不查询或操作交易所，不修改交易代码或配置。时间为北京时间，T 为原日志 wall timestamp 减去市场起点；模型 elapsed 使用官方时钟，两者有偏差。成交时间均为本地首次观察时间，原始成交事件的 timestamp 为 0，不能当作交易所实际撮合时间。

## 结果

| 市场 | Session | NORMAL / REPAIR 窗口 | 实际成交 | 结果 |
| --- | --- | --- | --- | --- |
| 10:35–10:40，1792377300 | 053c1d23a60c49dca6fc93798c476592 | 22 / 0 | 0 | COMPLETED；无持仓；PnL 0 |
| 10:40–10:45，1792377600 | 5b44b7731b3640519f7b66c81b62f416 | 16 / 4 | 4 笔，每笔 5 股 | COMPLETED；Merge 10 对；PnL 0 |
| 10:45–10:50，1792377900 | cdfc165b84be4cfcb9154efe427e8129 | 5 / 0 | NO 5 @ 0.39 | T51.563 提前停止，QUOTE_WOULD_TAKE；退出时 Delta -5 |

最后一场有 SL 决策及一个未激活的 REPAIR 准备记录，但没有 REPAIR POST，所以窗口统计为 0。不能把 REPAIR 窗口为零解读为模型未触发 SL。

## 10:35 场

22 次 NORMAL POST，21 ACK、1 Post-only REJECTED；无成交，无 REPAIR，无 Merge。T94.496 的 w17 NO 5 @ 0.59 被明确拒绝，T95.700 已 SAFE_TO_CONTINUE，随后正常继续。

T300.892 写入 COMPLETED / MARKET_CUTOFF，实际持仓为零。缓存无源失败、无致命异常、无事件丢失。

## 10:40 场

| T | 实际经过 |
| --- | --- |
| 32.906 / 33.307 | w3 NO 5 @ 0.68 ACK / 首次观察 MATCHED |
| 37.439 / 37.448 | 实际仓位及余额证明 / 原仓窗口安全关闭 |
| 37.491、37.642 | 已锁定 TP，但 sigma 尚未就绪，暂不挂单（既有 RP-001 问题范围） |
| 39.643 / 40.219 | TP BUY / w4 YES 5 @ 0.28 ACK；sigma 0.019458，对应 2 tick |
| 42.876 | 原 NO 升级 CONFIRMED，记录 confirmation_advanced；原 TP 队列继续 |
| 45.895 | 路径回撤达到 0.06/股，TP→SL，撤原 TP |
| 47.911 / 48.129 | SL BUY / w5 YES 5 @ 0.38 ACK |
| 48.883 | SL_REPRICE，先撤单再重新评估 |
| 51.332 / 51.627 | 最新盘口下仍以 YES 5 @ 0.38 重挂（w6） |
| 52.874 / 59.902 | w6 首次观察 MATCHED / 最终配平；第一轮 PnL -0.30 |
| 70.865 / 71.728 | w9 返回带精确签名 ID 的 Already canceled，先 UNKNOWN；核对后 REJECTED 并安全继续 |
| 110.613 / 111.939 | w19 YES 5 @ 0.57 ACK / 首次观察 MATCHED |
| 115.460 / 115.493 | 实际仓位及余额证明 / 原仓窗口安全关闭 |
| 115.529 / 115.755 | TP BUY / w20 NO 5 @ 0.37 ACK；sigma 0.040022，向上取整为 5 tick |
| 120.273 | 原 YES 升级 CONFIRMED，TP 队列继续，未因状态升级撤单 |
| 124.185 / 127.489 | w20 首次观察 MATCHED / 配平；第二轮 PnL +0.30 |
| 256.971 | Merge 10 对确认；成本 10，回收 10 |
| 301.063 | COMPLETED / SAFE_TO_CONTINUE；实际库存为零 |

两轮经济结果：5×(1−0.68−0.38) = -0.30；5×(1−0.57−0.37) = +0.30；合计 0 USDC。余额从 17.809232 变为 7.809232，Merge 后恢复 17.809232。

w9 初始 UNKNOWN 日志 T70.864623，安全关闭 T71.727559，约 0.863 秒；该 ID 共有两条 DELETE ACK（T70.863903、71.074930，包含响应前已经发出的取消）。没有此前十几秒的取消/缓存循环。两次活动 TP 上的纯 CONFIRMED 升级也均保留了队列。缓存记录一个 order ValueError、一个 open_orders ReadTimeout，均未导致最终退出或未解决窗口。

## 10:45 场异常退出

| T | 实际经过 |
| --- | --- |
| 21.510–36.098 | 前四个 NORMAL 窗口均未成交、安全关闭 |
| 36.778 | w5 NO 5 @ 0.39 ACK |
| 36.874 | SIGNAL_REVERSED，发起取消；随后仍有成交事实，按事实核对 |
| 39.716 | 首次同时观察 MATCHED、MINED；成本 1.95 |
| 45.733 / 45.749 | CONFIRMED / 原仓窗口安全关闭 |
| 46.821–51.505 | HD；可靠路径为 PARTIAL_PATH，尚未达到路径回撤阈值 |
| 51.512249 | HD→SL，累计回撤 0.06/股；pYES 0.735，sigma 0.025495；计划 YES 5 @ 0.73 |
| 51.532062 | w6 准备签名报价 YES 5 @ 0.73，尚未 POST |
| 51.534041 | 激活检查发现 FINAL_QUOTES_OR_DIRECTION_CHANGED，正常放弃该准备单 |
| 51.540218 | 再次 SL BUY，计划更新为 YES 5 @ 0.74；pYES 0.740 |
| 51.544480 | 进入退出清理；无第二条 REPAIR 准备记录，无 REPAIR POST |
| 51.547467 | 全部已知订单完成关闭核对，SAFE_TO_CONTINUE |
| 51.563148 | 写入 BLOCKED / QUOTE_WOULD_TAKE，保留 NO 5，Delta -5 |

模型已识别路径恶化并触发 SL。其 0.06 阈值是可靠路径上的价格贡献回撤，不是相对开仓成本的最大亏损。本轮存在 INVENTORY_PATH_UNKNOWN，可靠路径锚点 pYES 约 0.675；到 0.735 时回撤 0.06，但 NO 相对成本 0.39 的浮动 PnL 已是每股 -0.125。这是现有部分路径口径的效果。

退出时 NO 5 @ 0.39，余额 15.859232 USDC，已有订单均成交或取消。SAFE_TO_CONTINUE 说明订单和成交证据核对完成，不代表仓位配平或 SL 完成。日志没有后续结算结果，不能据此宣布最终亏损 1.95，也不能用 realized_merge_pnl=0 宣布没有风险。

### 代码原因

1. `rdp_sm_path_repair.py:333` 根据 snapshot A 生成计划，记录决策后 `:344` await 日志 flush。
2. `rdp_sm_lifecycle.py:138` 在 prepare 中重新获取 snapshot B。
3. `rdp_sm_path_repair.py:348` 的 candidates 虽接收新 snapshot，却直接使用旧 plan 的价格和数量。
4. `rdp_sm_lifecycle.py:210` 用 B 校验旧价格；`rdp_sm_execution.py:1443` 要求 BUY 价格严格小于 ask，否则抛出 QUOTE_WOULD_TAKE。
5. 这个异常发生在 prepare 阶段，位于 repair 中捕获 ValueError 的范围之外；`rdp_sm_loop.py:733` 最外层捕获后核对订单并终止 session。

因此报价变化本身被正确拦住，但一个可重新报价的市场变化被升级为整场停止，留下本来要修复的残仓。后续 activate 的正常失效逻辑还没机会运行。

第二次 prepare 的具体 snapshot 未写入日志，不能精确声称校验时 ask 是 0.74 或 0.73；异常只证明它不高于计划价 0.74。退出 WS diagnostics 最后一次 top reconciliation 的 reported ask 为 0.74，可作为旁证。第一次 w6 失效和第二次决策间约 6 ms，第二次决策到退出诊断约 4 ms，问题发生在快速盘口变化时的下单准备阶段，没有发送实际吃单请求。

建议修复范围：把计划与产生它的盘口绑定；进入准备阶段后报价已失效时，记录失效原因、放弃该候选、保留 SL 状态并重新评估。保持 Post-only、资金和 Delta 校验；只对明确的可恢复报价变化局部重试，其他身份/资金/证据异常继续走原阻断逻辑。此复盘未修改生产代码。

三个市场均无 SYNC_CACHE_NOT_READY 或日志写入失败。最后一场缓存有一次 trades ValueError，但有后续完整核对，退出原因明确为 QUOTE_WOULD_TAKE。没有日志证据把本次异常归因于此前 /tmp 清理。

## 证据

- `extract.py`：读取上述三个原始 JSONL，生成本目录证据文件；保存原日志 SHA256 并检查读取期间没有变化。
- `summary.json`：每场结果、去重经济成交、成交成本、缓存健康。
- `timeline.json`：关键事件、原日志行号和 wall elapsed。
- `orders.csv`：全部 47 个实际窗口，包括最终 placement outcome 和关闭原因。

运行提取时核对：实际成本依次 0、10、1.95；退出 Delta 依次 0、0、-5；最后场恰有 5 次 POST 且 stop_reason=QUOTE_WOULD_TAKE。未运行交易程序或修改配置。
