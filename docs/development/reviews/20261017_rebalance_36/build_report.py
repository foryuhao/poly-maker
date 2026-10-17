"""Author a read-only performance study and execute its embedded notebook."""
import contextlib
import io
import json
import sys
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]


def build():
    x=json.loads((OUT/'analysis.json').read_text());s=x['summary'];ws=x['windows']
    all_group=s['groups']['all']
    labels=dict(poll_sleep='已识别的固定确认轮询休眠',post_return='关闭时 POST 尚未返回',
                get_current_order='当前补仓单的详情读取',get_old_order='其他历史订单的详情读取',
                get_trades='市场完整成交集合读取',get_other='其他 GET（主要为拒单核验）',
                delete_only='只有 DELETE 活动、无上述操作的区间',local_or_unobserved='本地处理、调度及其余未分解间隙')
    table=['|关闭请求到窗口收尾的墙钟分解|合计秒|占比|','|---|---:|---:|']
    for k in labels:table.append(f"|{labels[k]}|{all_group['wall_attribution_s'][k]:.2f}|{all_group['wall_attribution_share'][k]:.1%}|")
    case=['|16:05 场补仓|已知订单数|订单详情 GET|成交集合 GET|收尾秒|','|---|---:|---:|---:|---:|']
    for w in ws:
        if w['market']=='16:05':case.append(f"|[{w['window']}]({w['source']}:{w['source_line']})|{w['known_orders']}|{w['get_order_requests']}|{w['get_trade_requests']}|{w['close_s']:.3f}|")
    budget=['|一轮已知订单 N|当前 GET 数|共享快照保守 GET 数|当前串行秒|共享后串行秒|再加 4 路并发的理想读取阶段秒|',
            '|---:|---:|---:|---:|---:|---:|']
    for r in s['request_budget']:budget.append(f"|{r['known_orders']}|{r['current_gets']}|{r['shared_snapshot_gets']}|{r['current_serial_s']:.2f}|{r['shared_serial_s']:.2f}|{r['bounded_4_read_phases_s']:.2f}|")
    archive=['|场次|轮次|结果|历史已知订单数|POST 至关闭秒|收尾秒|详情 / 成交 GET|DELETE|已记录本单终态后的 watchdog DELETE|',
             '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for w in ws:
        status='成交' if w['filled'] else ('未成交' if w['outcome']=='ACK' else '拒单')
        archive.append(f"|{w['market']}|[{w['window']}]({w['source']}:{w['source_line']})|{status}|{w['known_orders']}|{w['lease_s']:.3f}|{w['close_s']:.3f}|{w['get_order_requests']} / {w['get_trade_requests']}|{w['delete_requests']}|{w['watcher_cancel_after_terminal']}|")
    confirmation=['|场次/轮次|其他订单的完整 REST 记录已含本笔 CONFIRMED|本地本笔 CONFIRMED|可证明的应用滞后下界|',
                  '|---|---|---|---:|']
    for r in s['confirmation_page_lag_positive']:
        confirmation.append(f"|{r['market']} / {r['window']}|[{r['earliest_logged_page']}]({r['source']}:{r['page_line']})|[{r['local_confirmed']}]({r['source']}:{r['local_line']})|{r['page_to_local_lag_s']:.3f}s|")
    report="""# REBALANCE 效率研究与改进方案：36 个市场、71 次补仓

研究日期：2026-10-17，北京时间。用户要求先找原因、出方案。本研究不修改执行代码、配置、日志结构或交易进程。

## 结论

**首要瓶颈是重复且串行的关闭核验，以及整份成交数据按单独订单处理造成的确认应用延迟。不是撤单请求发得慢，也不能把约 10 秒收尾全部归因于交易所确认。**

建议先做同轮快照共享与整批成交状态更新，再做有界并发。重复撤单减负和确认轮询可随后调整。历史终态缓存涉及证明失效规则，作为后续选项，不宜在首轮直接跳过旧订单。

首轮保持买卖方向、报价、数量、确认要求和期限：继续 post-only、缺口侧 Best Bid、最低 1 USDC、当前最小数量/上限、REBALANCE 3 秒、盘口过期规则、NORMAL 信号规则、市场截止、资金上限与跨市场 Session 独立性。

## 1. 样本和耗时分解

取已完成两批复盘的固定样本：13:00–15:00 的 24 场，加 15:40–16:40 的 12 场，共 36 场；中间 40 分钟不属于样本。18 场发生过 REBALANCE，共 71 次真实提交，其中 60 ACK、11 REJECTED，16 张订单有确认成交、55 次提交没有成交。补仓确认成交按唯一 `(trade_id,maker_order_id)` 计有 17 条记录，其中一张订单分两笔成交。

|分组|样本|收尾中位数|经验 p95|最长|
|---|---:|---:|---:|---:|
|全部 REBALANCE|71|9.302s|15.763s|23.428s|
|已 ACK 且未成交|44|8.908s|13.270s|14.923s|
|有确认成交|16|10.120s|21.365s|23.428s|
|前 24 场中的补仓|56|8.998s|14.577s|21.365s|
|后 12 场中的补仓|15|10.258s|17.824s|23.428s|

收尾定义为 `sm_obi_close_requested.requested_monotonic` 到 `sm_window_result.end_time`，不是整个补仓周期，也不是单个 HTTP RTT。71 次收尾合计 **686.394 秒**。

这与上次修复的位置不同：上次去掉的是**账户快照前**额外重复全量复核，本研究测量的是仍然保留的**每个交易窗口收尾**。市场循环内的账户快照、窗口结束后的等待，以及旧 bug 持续重试到市场截止的时间不在本表中，不能把已修复的成本再次当成待优化收益。

{table}

这些是**不重复相加的墙钟区间**：先识别固定休眠和未返回 POST；GET 活动时归相应 GET，重叠的 DELETE 不再加一次；剩余 DELETE 和间隙另列。`/time` 背景保温请求不计入关闭查询。这个分解不是说各项都能全部删除，也没有把“查询正在等待确认”再额外叠加为平台等待。

已 ACK 且未成交的 44 单收尾仍需中位 8.908 秒，且没有固定确认轮询休眠。这是本地读取成本可以优化的强证据。实际有成交时必须继续等待权威 CONFIRMED，现有日志不能精确分离交易所首次可查确认时刻与采样延迟。

## 2. 原因一：每轮对全部历史订单执行 4N 次串行读取

[`_verify_close()`](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_execution.py:1489) 清空证明后，对 `sorted(self.known)` 逐个 await [`_verify_order()`](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_execution.py:1296)。单张订单的路径是：

1. SM 先读订单详情，校验精确身份与签名经济条件。
2. FillTruth [再次读同一订单详情](/home/ubuntu/poly-maker/src/polymaker/execution/filltruth.py:77)，然后读取该市场完整成交集合并只处理这个订单的腿。
3. 回到 SM，再次读该订单详情，检查终态和数量。
4. 换下一张历史订单，重新读一遍同一市场成交集合。

因此每个正常关闭轮次约为 **3N 次订单详情 + N 次市场成交集合 = 4N 组读取**；分页多时实际 HTTP 更多。这里的 N 包括以前已经关闭的订单，通常只有一张是本轮新补仓。

日志完全吻合该结构：**1878 次订单详情 = 3 × 626 个“订单×核验轮次”**；634 次成交集合读取中，626 次随逐订单核验发生，另 8 次来自拒单证明复核。1878 次详情中只有 225 次读当前补仓单，**1653 次（88.0%）读其他历史订单**。非保温关闭 GET 的最大并发数在所有 71 个窗口中均为 **1**。

不是某张订单一定越来越慢，而是要检查的订单数越来越多。仅比较已 ACK 未成交补仓：

|已知订单数|样本|收尾中位数|
|---|---:|---:|
|1–4|8|3.290s|
|5–8|14|7.580s|
|9 及以上|22|11.012s|

这不只是一项相关性：相同代码中的串行调用数量与原始 HTTP 计数精确匹配。

唯一残仓的 16:05 场是直接例子：

{case}

四次补仓从 POST 到关闭请求共 **9.141 秒**，收尾共 **42.810 秒**。虽然这意味着有明显可优化的调度时间，但不能据此推断如果多发几次单就必然成交、避免残仓。

## 3. 原因二：已经读到 CONFIRMED，但没有及时应用到该订单

FillTruth 取到的是完整市场成交集合，但每次只处理当前 `order_id` 的 maker legs。它会在核验历史 A 订单时读到新 B 订单的 CONFIRMED，却把 B 留到轮到 B 时才更新。B 若在本轮较早处理，随后其他订单查询读到的更新也不会自动回写 B，于是下一轮又重查全部订单。

17 条补仓确认成交中，有 **12 条**能直接证明：其他订单的完整 REST 核验记录先已包含该笔 CONFIRMED，本地该笔成交却稍后才更新。滞后下界为 **0.676–9.766 秒**，这 12 条的中位数 **4.112 秒**。其余 5 条没有可证明的更早页面，不代表真实延迟必然为零。

16:10 场 w12 的时间线尤其清楚：

- 16:12:17.277 提交 NO 补仓，20.283 发起关闭。
- 24.241 已记录本单终态；在本轮核验时，本笔状态尚未确认。
- **[27.004 的另一订单核验记录](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1131)已包含本笔 CONFIRMED**。
- **[30.881](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1173)** 完成第一轮，仍报本单 `TRADE_CONFIRMATION_UNRESOLVED`，因为这笔本地状态没有从其他订单取回的页面中更新。
- 等待 2 秒，再次全量读取；**[36.769](/home/ubuntu/poly-maker-data/journal/rdp_sm_0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d_eecf5ee3966e475a99dfe8fd168f0366.jsonl:1222)** 才把本笔更新为 CONFIRMED。
- 43.382 完整关闭证明通过，43.711 窗口收尾结束。

从 27.004 已拿到确认数据到结束，又过去 **16.707 秒**。其中部分完整一致性核验仍必要，不能把 16.707 秒全部承诺为可省；但显然不能把它全称为平台确认慢。

全部有证据的确认应用滞后：

{confirmation}

## 4. 原因三：持续重复撤单；原因四：确认重试仍是全量扫描

关闭请求到首个 DELETE HTTP 启动，中位约 **0.328 ms**、最大约 **2.94 ms**；DELETE HTTP 本身中位 **216.9 ms**。主要问题不是撤单没有及时发出去。

当前 watchdog 会一直对当前签名 ID 重复 DELETE，直到整轮核验结束；完整关闭还会撤一次全部 known IDs。在 71 次补仓内共有 **1549 次 DELETE**。其中 **632 次 watchdog DELETE** 在 POST 已返回、且已记录本单身份匹配的官方终态之后才启动。它们是减负候选；终态之后出现矛盾事实仍须重新启用取消和核验。

这些 DELETE 大多与 GET 重叠，**不能将减少 632 次 DELETE 直接折算成等量收尾时间收益**。取消使用独立线程池，当前证据也不支持“DELETE 占满读取线程池”这一归因。

此前的撤单“去重”修复统一的是网络门控的逻辑操作身份，保留了实际 HTTP。这里拟减少的是已知终态后的实际重复请求，属于另一个层面。

[`_wait_for_confirmation()`](/home/ubuntu/poly-maker/src/polymaker/rdp_sm_execution.py:1719) 每次等待 2 秒后，又执行完整 `_verify_close()`。本批有 12 个窗口做了多轮核验，合计 15 个固定等待区间、**30 秒**，占全部收尾约 **4.4%**。仅缩短 2 秒间隔，远不如先解决每次重试的全量读取消耗。

当前单市场入口未发现启动账户成交 WS 订阅；行情 Market WS 不能当作账户成交确认通知。以后增加账户成交 WS 可以另议，但不作为首轮优化的前置条件。

## 5. 建议的实施顺序

### 第一轮：共享同轮快照，整批更新成交事实

目标：保留完整一致性核验，去掉按订单重复取得相同数据的路径。

- 将网络读取与 FillTruth 校验分开，给 SM 增加“使用本轮已获取并验证的不可变快照”的入口，复用原有身份、数量、成交状态及隔离规则；其他 Engine 默认路径保持。
- 每轮对订单取得必要的前后状态，去掉 SM 与 FillTruth 连续重复的一次详情读取；保留前后身份和数量检查。
- 每轮完整市场成交分页统一读取、验证并分发给本 Session 所有相关订单。需要防止末尾竞态时，统一再读一次成交集合，而不是每个 ID 都取一次。
- 已获得的 CONFIRMED 及时作用于所有对应订单，避免因为处理顺序错误再进入下一轮全量扫描。
- 末尾仍核验每个 ID 的终态、成交集合与数量、经济条件、资本证据/外部事件版本及分页完整性。发现新事实或不一致仍关闭屏障，不能为了复用强行忽略。

保守请求预算由 **4N 降为 2N+2**，第二个成交读取是竞态检查预算，并非假定只看一次就足够。N=12 时从 48 组 GET 降为约 26 组，减少约 **45.8%**；这不等于直接承诺收益或墙钟同幅改善。额外拒单/分页恢复分支仍单独完整处理。

首轮内部验收目标：健康、单页样本下读取数量不再为 4N；已 ACK 未成交补仓的收尾中位数至少比同一离线延迟模型下降约 30%。这些是待验证目标，当前尚未修改或测出新实现的真实耗时。

### 第二轮：在隔离状态后，增加 2–4 路有界读取并发

接口目前已经通过 `await + 线程池` 执行；问题在于上层逐个 await。单纯把 SDK 换成原生 async 不会自动消除 4N。

**不能直接 `asyncio.gather(_verify_order(...))`。** 当前客户端共享 `trade_cursors`、`last_trades`、`last_open_rows`、分页失败账本和 Processor 状态；多个订单同时查成交会互相覆盖，破坏完整分页及证明的一致性。

- 先采用不可变的本轮读取结果，避免核验函数从可变 `client.last_trades` 取数据。
- 独立订单详情请求可用 2–4 路有界并发，读取结果汇总后由单一协调者验证、更新 Tracker。
- 同一成交集合的分页保持有序、完整读取；同一 scope 不允许并发分页流互相覆盖。若未来要并行不同 scope，先隔离游标与结果归属。
- 取消继续保留专用执行容量，读取并发上限是实验参数，不是官方限额；429、传输失败、分页错误和新事实仍阻断并退避。

在单次 GET 250 ms、单页且无失败的纯预算模型下：

{budget}

表中“并发”仍保留前态→成交→后态→末尾成交的阶段顺序，仅同一前态或后态阶段内并发。未包含撤单、服务端状态滞后、分页、调度和失败重试，**不是历史盈利回测，也不是已经测得的优化效果**。

两轮完成后，可把样本覆盖的订单规模（N 不超过 15）、健康、未成交补仓的收尾中位 **2–4 秒**设为初始工程目标；实际能否达到须离线集成测试和随后实盘统计确认。提速后历史订单数可能进一步增加，还需检查更大 N 的增长曲线，不能无限提高并发。已有成交的样本不能以统一时限跳过权威确认。

### 第三轮：让撤单与确认等待分工

- 对 POST 尚未返回、not-found、未知接受结果、未证明终态的 ID 继续可靠撤单。
- POST 已返回且新鲜官方详情证明终态后，停止持续重复 DELETE，继续查询和核对成交；出现 LIVE、迟到事实或其他矛盾时，恢复取消与完整核验。
- 确认重试优先处理待确认和被新事实影响的 ID，同时用新完整市场成交集合检查其他订单变化；不因一个 pending ID 无条件重做整场全部详情。
- 先保持 2 秒间隔，测量单轮缩短后的剩余确认滞后，再决定是否有必要改为更短且有预算的轮询。未来账户 WS 可用于唤醒，但 REST 和断线兜底仍必要。

### 后续选项：历史终态证明按 ID 缓存并明确失效

这是进一步去掉历史订单重复详情的办法，但改变证明复用契约，风险高于前两轮。

缓存仅限本 Session、逐 ID 带证据版本；每轮仍读新的完整市场成交/开放订单，核对全部已观察事实。新增成交、状态变化、重现 LIVE、分页不完整、隔离或证据版本变化必须使相关证明失效，无法定位时回退完整核验。新市场不能继承旧 Session 阻断状态。不能仅因“以前取消过”就永久跳过。

建议先完成快照共享和有界并发，再判断是否需要这一步。

## 6. 保持的约束与验证要求

- 不提前将 MATCHED/MINED 当作确认库存；不在未知 Placement、未确认成交、未完成分页或资本不一致时开放下一单。
- 保留精确签名经济条件、终态与累计成交数量、所有已观察 maker legs、`associate_trades`、隔离及失败成交规则。
- 保留 Already canceled 严格回执和唯一签名 ID 校验；完整分页反证、迟到事实恢复 UNKNOWN 与最大资金承诺继续生效。
- 不靠放宽 10 秒时限绕过查询成本，不改报价、数量、100 ms 盘口规则、3 秒租期、交易截止或资本上限。
- 不制造新鲜度：不同轮次的快照不能混用；事件版本变化时旧快照不能直接放行。现有日志记录真实使用的数据，不伪造按单独立请求；本研究没有修改日志格式。

实施时应覆盖的关键集成场景：0/5/15 张历史订单及 30/50 张增长压力；零成交/部分成交/完整成交；读取前后累计数变化；成交在别的订单读取中转为 CONFIRMED；页中段失败及循环游标；不同 market/ID/经济字段矛盾；观察状态晚到或回退；未知 POST 与提前 not-found；并发期间外部事件、分页失败或日志故障；市场截止及中断；同 Session 继续、跨 Session 独立。

性能验证必须用真实 Gateway/SDK/Tracker/FillTruth 的离线边界替换，注入 200/250/500 ms、慢请求和错误，检查读取数、墙钟及“下一单何时被允许”。不能只测纯函数或把核验函数替换成永远成功。随后保持交易参数运行约一小时，比较收尾中位/p95、旧订单数增长斜率、实际补仓间隔、请求数、成交及残仓。

## 7. 数据质量和解释边界

原始 36 份日志 SHA-256 已与两批原复盘的哈希逐一核对，未变。用 `batch_id`、请求真实 monotonic 开始/结束、窗口关闭请求及结果对齐；不以异步写入行号作为发生顺序。分解各项逐窗口相加严格等于该窗口收尾时间。

前后两批分别报告：旧批历史订单详情占收尾约 58.0%，新批仍为 56.0%，同一收尾机制在两批都明显存在。两批的报价行情、成交与拒单触发不同，不能把分组差异直接解释为上一修复的收益。

CONFIRMED 早出现于其他核验记录，证明数据当时已到程序，不证明是交易所最早确认时刻；从该记录到本地应用的差值只是可证明的应用滞后下界。固定休眠分解也不代表外部确认总共只需 30 秒。

上述方案还未实现，未向交易所发送 API 请求，未重放真实订单，也没有保证更快核验必然带来更多成交或更高收益。

## 可复核材料

[逐窗口分解](windows.csv)、[逐请求归类及原始行链接](requests.csv)、[核验轮次](rounds.csv)、[确认应用滞后](confirmation.csv)、[撤单事件](cancellations.csv)、[请求预算模型](request_budget.csv)、[分析及源哈希](analysis.json)、[已执行 Notebook](study.ipynb)、[分析脚本](analyze.py)。

## 附：全部 71 个补仓窗口

{archive}
"""
    (OUT/'README.md').write_text(report.format(table='\n'.join(table),case='\n'.join(case),confirmation='\n'.join(confirmation),budget='\n'.join(budget),archive='\n'.join(archive)))
    snippets=[f"from pathlib import Path\nimport os,sys\nos.chdir({str(ROOT)!r})\n__file__={str(OUT/'analyze.py')!r}\n",(OUT/'analyze.py').read_text()]
    cells=[dict(cell_type='markdown',id='scope',metadata={},source='36 个固定已结束市场，18 个发生 REBALANCE，共71次真实提交。只读日志；逐窗口对齐 batch_id/monotonic；关闭墙钟区间去重分解；旧订单与当前订单分开；确认的可观测应用滞后不等于交易所确认延迟；请求预算只是模型。')]
    ns={'__name__':'__main__'}
    for n,source in enumerate(snippets,1):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):exec(compile(source,f'study_cell_{n}','exec'),ns)
        cells.append(dict(cell_type='code',id=f'code-{n}',metadata={},execution_count=n,source=source,outputs=[dict(output_type='stream',name='stdout',text=output.getvalue())] if output.getvalue() else []))
    notebook=dict(nbformat=4,nbformat_minor=5,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3'),language_info=dict(name='python',version=sys.version.split()[0])),cells=cells)
    (OUT/'study.ipynb').write_text(json.dumps(notebook,ensure_ascii=False,indent=2))
    print('Built study, proposal and executed notebook. No production code or live API changes.')


if __name__=='__main__':build()
