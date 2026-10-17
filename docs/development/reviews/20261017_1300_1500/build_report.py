"""Build the review and an executed, self-contained analysis notebook."""
import contextlib
import io
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]


def build():
    x = json.loads((OUT / "analysis.json").read_text())
    e = json.loads((OUT / "evidence.json").read_text())
    table = ["|市场（北京时间）|首单信号 / OBI|NORMAL / REBALANCE 实发单|成交订单数|合并毛收益 USDC|期末残仓|",
             "|---|---|---:|---:|---:|---|"]
    for m in x["markets"]:
        first = f"{m['first_side']} / {float(m['first_obi']):+.3f}" if m["first_side"] else "无首单"
        residual = f"YES {m['residual_yes']:g}" if m["residual_yes"] else (f"NO {m['residual_no']:g}" if m["residual_no"] else "0")
        table.append(f"|[{m['market']}]({m['source']}:{m['result_line']})|{first}|{m['normal']} / {m['rebalance']}|{m['filled_orders']}|{m['pair_pnl']:+.6f}|{residual}|")
    proof_table = ["|市场|拒单证明复核开始|关闭核验安全|后续账户订单读取结束|快照耗时下界|日志行（请求起点／账户终点）|",
                   "|---|---|---|---|---:|---|"]
    for c in e["snapshot_examples"]:
        proof_table.append(f"|{c['market']}|{c['proof_start']}|{c['proof_safe']}|{c['account_orders_end']}|{c['snapshot_lower_bound_s']:.3f}s|[{c['first_line']}]({c['source']}:{c['first_line']}) / [{c['final_line']}]({c['source']}:{c['final_line']})|")
    initial_table = ["|市场|日志可还原的最早有效信号事件|方向 / OBI|对应日志|", "|---|---|---|---|"]
    for item, m in zip(e["first_recorded_valid_signals"], x["markets"]):
        f = item["first"]
        if f:
            initial_table.append(f"|{m['market']}|{f['time']}|{f['signal']} / {float(f['obi']):+.3f}|[{f['kind']}]({m['source']}:{f['source_line']})|")
        else:
            initial_table.append(f"|{m['market']}|无|无|无交易窗口|")
    report = """# 2026-10-17 最近 24 个完整市场初步复盘

范围冻结于本次取数开始：北京时间 **13:00–15:00**，市场开盘时间从 13:00 到 14:55，连续 24 场，包含零成交和启动较晚的场次。不是从全部历史中挑选 24 场盈利或交易活跃市场。最后一场清理在 15:00:50 结束。此后新结束的市场不混入本批。

**初步判断：单侧 NORMAL、有效反转撤单及 Session 独立运行已生效，但补仓的执行效率仍有明显问题。优先排查上次拒单修复引入的全量复核耗时回归，其次评估 REBALANCE 的 100 ms 盘口过期撤单。当前证据还不足以评价 OBI 的预测收益。**

## 汇总与收益口径

- 24 场均为 `COMPLETED / SAFE_TO_CONTINUE`，但这只表示执行及关闭证明完成，**不代表配平或零残仓**。11 场有成交，13 场无成交，5 场有残仓。
- 227 个执行窗口，实际 POST 226 单：NORMAL 170、REBALANCE 56；另有 1 个窗口在 HTTP 发出前 ABORTED。最终 214 ACK、12 REJECTED，没有遗留 UNKNOWN。
- 24 张订单有确认成交、25 条唯一确认成交记录；同一成交的 MATCHED/MINED/CONFIRMED 观察没有重复计算。NORMAL 成交订单率 15/170（8.8%），REBALANCE 为 9/56（16.1%）。
- 合并 47.885246 对，**已验证合并毛收益 −0.44229514 USDC**。8 场有合并，3 场盈利、5 场亏损。
- 5 场残仓合计成本 **14.65280486 USDC**：13:10 YES 5（2.75）、13:30 NO 5（3.10）、13:55 NO 5（2.40）、14:10 NO 5（3.45）、14:55 NO 5.004754（2.95280486）。这是尚未在本批日志中验证结算结果的成本，不能直接列为亏损，也不表示账户此刻仍持有它们。
- 本批买入与合并对应现金流 −15.0951 USDC。首场起始余额 22.738322，末场合并后余额 22.643222，差额 −0.0951；两者相差 **+15 USDC**。跨 Session 余额核对发现三次 +5（13:15→13:20、13:35→13:40、13:55→14:00），本批日志没有足够来源证明，不能直接认作本批交易收入。余额变化不能代替策略净收益。
- 残仓最终胜负、赎回、实际返佣及其他费用未形成完整权威闭环，**整批最终净收益仍未知**。

## 逐场概览

“首单信号”指实际发出首单所用的最终信号，不保证是这一场第一次出现的信号。每场所有挂单、反转 OBI、原始报价、成交及收尾时间见 [完整逐单时间线](timeline.md)。

{market_table}

14:00 场 Session 首条记录已经是 **14:01:59.190（T+119.190）**，后续没有 NORMAL 机会，也没有库存需要补仓，因此零单。现有 Session 日志不能解释为什么启动这么晚；不能把它算成“全场没有信号”。

## 1. 优先问题：拒单后的全量复核与 10 秒新鲜度限制相互阻塞

5 次 `Already canceled` 都被当前修复归为最终 REJECTED，关闭核验安全，原先 UNKNOWN 持续封锁退出的问题没有再现。但新的代价出现在 **13:10、13:55、14:55** 三场。

代码链路：

1. [`_snapshot` 开始计时后，发现拒单证明即调用完整 `_verify_close()`](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_execution.py:1010)。
2. [`_verify_close` 重验拒单，并串行遍历全部历史 known 订单](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_execution.py:1488)；[每张订单涉及多次详情及成交读取](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_execution.py:1295)。
3. 随后继续读取账户、市场、时间；[快照末尾要求整段耗时不超过 `ws_stale_halt_s`](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_execution.py:1202)，当前配置为 [10 秒](/home/ubuntu/poly-maker/config/config.toml:38)。此处即使 `include_books=False`，整段耗时限制仍生效。
4. 超时产生 `EXECUTION_DATA_STALE`，被归为[可重试](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_recovery.py:31)，[主循环暂停后重读快照](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_loop.py:490)。历史订单没有减少，下一轮再次超时。

原始 HTTP 时戳给出的保守耗时下界如下；尚未包含开头准备和末尾官方时间请求：

{proof_table}

在这三场最后一次发单之后，分别可识别至少 10、10、12 个耗时下界超过 10 秒的周期。14:55 场另一个周期从 14:57:52.887 到 14:58:03.043，仅这些读取就耗时 10.156 秒，每个单次请求多数仍只有约 200–310 ms。

**证据等级：长串行读取、SAFE 复核以及不再发单是日志事实；因此触发 `EXECUTION_DATA_STALE` 并耗尽后半场，是结合当前代码的高置信度推断。** `_pause` 当前只[发给终端 observer](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_loop.py:120)，没有同名持久日志事件，所以没有把该错误描述成“日志直接记载的原因”。网络请求日志异步排队写入，行号顺序也不等于请求发生顺序。

三场都在市场尚有约两分钟时停止进一步补仓，最后分别剩 YES 5、NO 5、NO 5.004754。13:00、14:15 也出现过相同业务拒单，但没有形成上述超过 10 秒的持续循环，因此不能说“任何 Already canceled 都会卡住”。

建议先修复此路径：保留拒单矛盾观察与成交安全校验，缩小重复核验范围，避免每次账户快照重跑全部历史订单；将历史订单核验耗时与最终可交易账户/盘口证据的时效明确区分，并验证真实请求延迟下仍能继续补仓。**单纯扩大 10 秒上限会掩盖随历史订单增长的成本，不是优先方案。**本次未修改实现或日志。

## 2. 已确认：REBALANCE 大量挂单没有获得原定 3 秒机会

NORMAL 的实际 170 单关闭原因为：有效反转 96、2 秒到期 73、市场入场截止 1；**没有因 `WS_BOOK_STALE` 直接撤单的 NORMAL 单**，与已确认的修改一致。

REBALANCE 的 56 次提交中，**39 次（69.6%）由 `WS_BOOK_STALE` 首先触发关闭请求**，15 次到期，2 次因提交未获接受而关闭。这 39 次包含 31 次 ACK 和 8 次最终拒单；若只看 46 张获 ACK 的补仓单，31 张（67.4%）由盘口过期关闭。[当前实现仅对 NORMAL 豁免盘口过期](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_lifecycle.py:475)，REBALANCE 仍以 [100 ms 盘口年龄](/home/ubuntu/poly-maker/src/polymaker/config.py:111)为有效性门槛。

47 次未成交补仓提交中，36 次首先由盘口过期触发关闭，9 次到期，2 次提交未获接受。包含最终拒单时，从 POST 到关闭请求的中位数为 0.332 秒、关闭请求到核验结束为 8.758 秒。**仅看获 ACK 但未成交的 37 单，两项中位数仍为 0.728 秒和 8.717 秒**。因此名义上仍是缺口侧、Best Bid、时间优先的配对，实际经常是短暂排队后撤单，再花数秒清理。关闭请求可以早于 POST 返回，关闭原因与最终业务拒单需要分别理解。

- **13:30**：13:30:23 NO 0.62×5 成交；13:30:36.869–13:33:56.234 尝试 16 次 YES 补仓，全部无成交，其中 15 次首先由盘口过期触发关闭、1 次提交未获接受；最终有 2 次拒单。13:34:12.793 出现 `MINIMUM_ORDER_EXCEEDS_MAXIMUM`，13:34:31.336 又记录 `WS_EMPTY_BOOK`，期末 NO 5。
- **14:10**：14:11:43 NO 0.69×5 成交；14:12:12.514–14:14:04.457 共 8 次 YES 补仓均无成交，关闭请求均先由盘口过期触发，其中也包含最终 post-only 拒单。14:14:23.265 记录 `WS_EMPTY_BOOK`，期末 NO 5。

这两场没有 Already canceled 业务回执，因此不能归到第 1 个问题。**提前撤单压缩排队时间是事实，若延长挂单能成交多少、亏损会怎样，现有日志无法作反事实保证。**建议将它作为第二优先级评估项，保留硬断线、盘口异常、到期和市场截止等退出条件；是否对补仓采用类似 NORMAL 的存续规则，需要单独确认。

## 3. 信号与成交：有反复反转及潜在逆向选择，暂不足以下结论

15 张有成交的 NORMAL 订单中，12 张后来以有效反转触发关闭，3 张到期关闭。这意味着“原信号入场后很快遇到反向信号”与成交高度重叠，值得检查逆向选择和信号稳定性。但官方成交时间只有秒级，不能证明每一笔都是在撤单请求之后成交，也不能据此直接判定 OBI 没有效果。

两个对照场：

- **13:05–13:10**：13:05:29.204 按 NO −0.476 挂 NO 0.59×5；29.485 信号反转，官方成交在 29 秒；39.477 完成关闭。49.460 先补 YES 0.51 未成交，55.743 再补 YES 0.62，56 秒成交。配对成本 1.21，5 对合并毛收益 **−1.05 USDC**。时间优先配对会接受锁定亏损，这是现有目标的代价。
- **13:15–13:20**：YES 0.35×5 与 NO 0.55×5 配成 5 对，毛收益 +0.50；随后 YES 0.42×5 与 NO 0.37×5 再配 5 对，毛收益 +1.05，合计 **+1.55 USDC**。相同执行框架也能形成正配对收益。

强 OBI、首单方向与最终胜负不是同一个指标。当前日志缺少本批所有残仓的最终结算证据，样本也小，不建议仅据此调整 ±0.30 阈值。

## 4. 最新一场 14:55–15:00 的完整关键链路

1. **14:55:23.893**：YES +0.682，NORMAL BUY YES 0.39×5。官方 25 秒成交 **2.885246 股**，27.316 关闭核验结束。差额未达 REBALANCE 触发边界，继续 NORMAL。
2. **14:55:53.470**：NORMAL BUY NO 0.59×7.89，54 秒全部成交，14:56:05.738 核验结束。库存成为 YES 2.885246 / NO 7.89，缺 YES **5.004754**，转入 REBALANCE。
3. **14:56:09.667–14:57:27.721**：6 次 YES 补仓，数量 5.01，报价依次 0.40、0.39、0.69、0.67、0.65、0.55，均未成交。其中 0.69 的提交出现 Already canceled，最终核验 REJECTED。最后一单收尾到 **14:57:40.444**。
4. 随后进入第 1 节所述耗时循环，没有新 POST；**15:00:31.985** 发起合并，**15:00:41.519** 确认合并 2.885246 对，毛收益 **+0.05770486**，保留 NO 5.004754，**15:00:50.313** 安全退出。13:55 场也到 **14:00:39.065** 才确认合并。

两次市场结束后合并是清理存量库存，日志没有出现市场结束后继续 BUY。快照失败会在主循环抵达 merge 分支前重试，最后清理仍可进行合并，因此延后合并与相同耗时问题相符。

这里的 NO 多仓首先来自**部分成交之后，NORMAL 根据库存偏移放大反向单**，不是仅因最低 1 美元补仓规则引起。本次只描述现有行为，不改动已确认的最小名义金额或数量规则。

## 5. 网络与日志质量

- 32,792 次 HTTP：GET 29,011、POST 226、DELETE 3,555；32,791 SUCCESS、1 LOCAL_ERROR，没有记录到传输失败或服务器故障。业务 post-only / Already canceled 拒单与传输 SUCCESS 可以同时成立。
- POST 调用到本地收到返回的中位数 **221 ms**、经验 p95 **520 ms**、最大 **1,455 ms**。包含网络、服务端处理与本地调用封装，不能拆解成单向网络耗时。
- 共 28 次 `NETWORK_REPEATED_SLOW` 暂停，网络仍会影响机会，但不能把三个长时间不再补仓的场次全部解释成“网络检查拦单”。此处突出的是许多正常延迟的串行请求累加超过 10 秒。
- 日志无损坏 JSON、信号变更历史覆盖数 0、诊断丢弃数 0，观察到最大写队列深度 138。25 笔确认成交与库存、合并成本/收益均完成逐场核对。
- 原始信号变更 8,091 条，其中 912 条位于对应窗口关闭请求之前。关闭后等待确认期间仍有信号变化，不能将其全部当成策略可入场机会。等待日志有去重，不能用其条数推算每类拦截的准确总时长。
- 确认成交观察会晚于撮合；逐单表区分官方 `match_time`、本地 CONFIRMED、关闭请求及核验结束。网络请求按请求本身的 monotonic/墙钟字段排序，不按异步写入行号排序。

## 日志中最早可还原的有效信号

以下从等待或入场证据取得信号事件时间，并非交易所全市场最早信号。比如 **13:20 和 13:35 最早留存有效信号是 YES，实际首单是 NO**；不能把首单方向说成首次信号。入场详细证据可能在 POST 返回后才写入，事件时间与写入时间已经分开。

{initial_table}

## 复核材料与复现

- [完整逐单时间线](timeline.md)：全部 227 窗口及等待、网络切换、确认成交、合并事件，含原始日志行链接。
- [逐场 CSV](markets.csv)、[逐单 CSV](windows.csv)、[去重成交 CSV](fills.csv)、[信号变更 CSV](signal_changes.csv)、[等待信号 CSV](signal_waits.csv)。
- [快照耗时证据](snapshot_cycles.csv)、[跨场余额差异](balance_gaps.csv)、[附加诊断 JSON](evidence.json)。
- [分析 JSON](analysis.json) 保存 24 个原始日志的路径、大小、行数与 SHA-256；完成时已重新验证哈希未变。
- [已执行分析 Notebook](review.ipynb) 内嵌分析代码与实际输出，可重新运行；单独脚本为 [analyze.py](analyze.py)、[diagnose.py](diagnose.py)。

本次仅进行日志读取和分析产物生成，**未修改交易策略、历史日志、恢复标记，也未启动交易或发送 API 请求**。
"""
    (OUT / "README.md").write_text(report.format(market_table="\n".join(table), proof_table="\n".join(proof_table), initial_table="\n".join(initial_table)))

    # Inline all substantive analysis code, not only links to external scripts.
    cells = [dict(cell_type="markdown", id="scope", metadata={}, source=(
        "# 24 场日志复盘\n\n范围：2026-10-17 13:00–15:00（北京时间）。"
        "成交粒度为 (trade_id, maker_order_id)，只取 CONFIRMED；毛收益不含残仓结算及返佣。"
        "原始日志只读，按 request monotonic/wall 时间排序，所有源 SHA-256 存于 analysis.json。"
        "下方内嵌代码已执行，分析不会向市场 API 发请求。"))]
    setup = f"from pathlib import Path\nimport os, sys\nos.chdir({str(ROOT)!r})\nsys.path.insert(0, {str(OUT)!r})\n__file__ = {str(OUT / 'analyze.py')!r}\n"
    snippets = [setup, (OUT / "analyze.py").read_text(), (OUT / "diagnose.py").read_text(),
                "import json\nreview = json.loads((OUT / 'analysis.json').read_text())\n"
                "print('市场 | 首单 | NORMAL/REBALANCE | 合并毛收益 | 期末YES/NO')\n"
                "for m in review['markets']:\n"
                "    print(m['market'], m['first_side'], m['normal'], m['rebalance'], round(m['pair_pnl'], 8), m['residual_yes'], m['residual_no'])\n"]
    ns = {"__name__": "__main__"}
    for n, snippet in enumerate(snippets, 1):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(compile(snippet, f"review_cell_{n}", "exec"), ns)
        cells.append(dict(cell_type="code", id=f"code-{n}", metadata={}, execution_count=n, source=snippet,
                          outputs=[dict(output_type="stream", name="stdout", text=output.getvalue())] if output.getvalue() else []))
    notebook = dict(nbformat=4, nbformat_minor=5,
                    metadata=dict(kernelspec=dict(display_name="Python 3", language="python", name="python3"),
                                  language_info=dict(name="python", version=sys.version.split()[0])), cells=cells)
    (OUT / "review.ipynb").write_text(json.dumps(notebook, ensure_ascii=False, indent=2))
    print("Built README.md and executed review.ipynb (four code cells).")


if __name__ == "__main__":
    build()
