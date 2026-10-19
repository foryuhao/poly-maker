"""Read-only presentation and independently executed validation of the locked 24 markets."""

import hashlib
import json
import os
import sys
from collections import Counter
from decimal import Decimal as D
from pathlib import Path

import nbformat
from nbclient import NotebookClient

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
s = json.loads((OUT / "summary.json").read_text())
stats, markets = s["stats"], s["markets"]
manifest = json.loads((OUT / "source_manifest.json").read_text())
for src in manifest:
    for item in [src, *src["supporting_sources"]]:
        assert hashlib.sha256(Path(item["source"]).read_bytes()).hexdigest() == item["sha256"]


def money(value):
    return f"{D(str(value)):+.6f}"


def cohort(mm):
    ee = [e for m in mm for e in m["episodes"] if e["repair_fill_mode"]]
    total = sum((D(m["merge_pnl"]) for m in mm), D(0))
    return {"markets": len(mm), "merge_pnl": str(total), "mean": str(total / len(mm)),
            "full_pnl": str(sum((D(m["full_pnl"]) for m in mm),D(0))),
            "mean_full_pnl": str(sum((D(m["full_pnl"]) for m in mm),D(0))/len(mm)),
            "first_exit_modes": dict(Counter(e["first_exit_mode"] for e in ee)),
            "repair_fill_modes": dict(Counter(e["repair_fill_mode"] for e in ee)),
            "paths": dict(Counter(e["path_to_repair_fill"] for e in ee)),
            "full_pnl_bounds": [str(sum((D(m[key]) for m in mm), D(0)))
                                for key in ("full_pnl_lower", "full_pnl_upper")]}


ops = []
max_observed_delta = max_session_buy_cost = D(0)
for m in markets:
    delta = D(0)
    for fill in sorted(m["actual_fills"], key=lambda f: f["journal_line"]):
        delta += D(str(fill["size"])) * (1 if fill["side"] == "YES" else -1)
        max_observed_delta = max(max_observed_delta, abs(delta))
    max_session_buy_cost = max(max_session_buy_cost, D(m["buy_cost"]))
    rows = [json.loads(line) for line in Path(next(x["source"] for x in manifest
                                                if x["session_id"] == m["session_id"])).read_text().splitlines()]
    blocked = []
    block_times = {}
    unknowns = []
    for line, r in enumerate(rows, 1):
        p = r["data"]["payload"]
        if r["kind"] == "sm_window_result" and p["close_barrier"] != "SAFE_TO_CONTINUE":
            blocked.append({"window": p["window_id"], "reason": p["block_reason"], "journal_line": line})
        if r["kind"] == "sm_repair_execution_blocked":
            g = block_times.setdefault(p["reason"], {"count": 0, "first_wall_elapsed": r["ts"] - m["start_epoch"]})
            g["count"] += 1
            g["last_wall_elapsed"] = r["ts"] - m["start_epoch"]
        if r["kind"] == "sm_unknown_official_lookup":
            unknowns.append({"journal_line": line, "payload": p})
    ops.append({"market": m["market"], "blocked_windows": blocked, "execution_blocks": block_times,
                "unknown_lookups": unknowns, "small_exposure_events": m["small_exposure_events"]})

cohorts = {"old12": cohort(markets[:12]), "new12": cohort(markets[12:]), "all24": cohort(markets)}
(OUT / "cohorts.json").write_text(json.dumps(cohorts, indent=2))
(OUT / "operations.json").write_text(json.dumps(ops, indent=2))

lines = ["# 24 场 REPAIR 运行审计", "",
         "固定范围：2026-10-19 上海时间 12:20–13:20、14:35–15:35，各12场；中间间隔不补样本。",
         "原始执行日志为成交事实源，trade_id + maker_order_id 去重，66条经济成交最终均CONFIRMED。",
         "8场残仓的赢家由CLOB市场接口逐一核实condition ID、slug、token ID及winner标记。",
         "本次只分析，没有修改生产策略、配置或实时订单。", "",
         "## 收益与口径", "",
         f"完整经济PnL **{money(stats['full_pnl'])} USDC**，每场平均 **{money(stats['mean_market_full_pnl'])} USDC**。",
         "完整PnL = 已Merge兑付 + 残仓到期兑付权益 − 全部实际买入成本；不等于redeem现金已经到账。",
         f"场末按最终收益累计的最大回撤 **{D(stats['full_pnl_max_drawdown']['amount']):.6f} USDC**，"
         "从12:20后的+1.280834到15:30后的−10.153166；不含盘中全仓估值，未按结算到账时刻排序。",
         "6场盈利、14场亏损、4场无成交；有成交20场的盈利比例30%，全部24场为25%。",
         f"已实现Merge PnL为 **{money(stats['merge_pnl'])} USDC**，其自身最大回撤 **{D(stats['realized_merge_max_drawdown']['amount']):.6f} USDC**。",
         f"尾仓成本合计{D(stats['residual_cost']):.6f}，结算兑付权益{D(stats['settlement_payout']):.6f}，"
         "净影响−1.00538935 USDC。不能仅用Merge收益判断完整表现。",
         "Gamma查询HTTP403后，CLOB接口成功返回结算赢家；证据见clob_resolution_queries.json。"
         "此前12场的完整PnL现在可确认为−5.618766，替代先前只报告Merge −6.27390492及未知结算上下界。", "",
         "| 范围 | 完整PnL | 每场平均 | Merge PnL | 首次TP/SL | 成交TP/SL |",
         "|---|---:|---:|---:|---:|---:|"]
for label, key in (("前12场", "old12"), ("新增12场", "new12"), ("全部24场", "all24")):
    c = cohorts[key]
    lines.append(f"| {label} | {money(c['full_pnl'])} | {money(c['mean_full_pnl'])} | {money(c['merge_pnl'])} | "
                 f"{c['first_exit_modes'].get('TP', 0)} / {c['first_exit_modes'].get('SL', 0)} | "
                 f"{c['repair_fill_modes'].get('TP', 0)} / {c['repair_fill_modes'].get('SL', 0)} |")
lines += ["", "前后两批不是随机对照，且新增场次已出现 ≤2.5 share 小敞口暂停修复行为；不能把收益差直接归因于改动。", "",
          "## TP / SL 分布", "",
          "共有 29 轮实际 REPAIR 成交，另有 1 轮仅 0.01 share 的 NORMAL 尾仓。",
          "首次退出 HD：TP 15/29（51.72%），SL 14/29（48.28%）。",
          "最终补仓成交模式：TP 7/29（24.14%），SL 22/29（75.86%）。",
          "首次 TP 的 15 轮中，仅 7 轮在 TP 模式成交，8 轮升级 SL；TP 转化率 46.67%。",
          "每轮完整PnL含本轮尾仓结算；同时展示独立均价的配对部分。TP/SL是下单模式，不是收益正负标签。", "",
          "| 到首次补仓成交的路径 | 轮数 | 完整PnL | 每轮平均完整PnL | 配对部分PnL |",
          "|---|---:|---:|---:|---:|"]
for g in stats["paths"]:
    lines.append(f"| {g['path']} | {g['episodes']} | {money(g['full_pnl'])} | "
                 f"{money(g['mean_full_pnl'])} | {money(g['paired_pnl'])} |")
lines += [f"| NORMAL仅小尾仓 | 1 | {money(stats['dust_only_full_pnl'])} | — | 0 |",
          f"| session成本池归因差额 | — | 0 | — | {money(stats['session_pool_cost_adjustment'])} |",
          f"| 合计 | 30 | {money(stats['full_pnl'])} | — | {money(stats['merge_pnl'])} |", "",
          "15:15 与 15:30 有多轮不同买价并留下残仓。执行账本按 session 同一 token 平均成本释放，"
          "逐轮统计按本轮成本；两种配对部分归因相差 +0.41241265 USDC，差额对应留在残仓中的成本变化，不是额外交易收益。",
          "首次TP的全部15轮完整PnL合计−0.955366；即使首次TP略多，利润兑现仍不足。", "",
          "## 逐市场时间线", "",
          "T 为市场开始后的秒数，采用日志首次观察到 MATCHED 的本地时间，不等于交易所真实撮合时间。"
          "同一订单相邻部分成交合并展示，完整记录保留在 summary.json / timeline.json。", "",
          "| 市场开始 | 成交时间线与路径 | 完整PnL | Merge PnL | 到期前剩余库存 |",
          "|---|---|---:|---:|---|"]
timeline_rows = []
for m in markets:
    descriptions = []
    for e in m["episodes"]:
        fs = []
        for f in e["actual_fills"]:
            if fs and fs[-1]["order"] == f["maker_order_id"]:
                fs[-1]["q"] += D(str(f["size"]))
                fs[-1]["cost"] += (D(str(f["size"])) * D(str(f["price"]))).quantize(D(".000001"))
                fs[-1]["last"] = f["first_observed_elapsed"]
            else:
                fs.append({"order": f["maker_order_id"], "side": f["side"], "q": D(str(f["size"])),
                           "cost": (D(str(f["size"])) * D(str(f["price"]))).quantize(D(".000001")),
                           "first": f["first_observed_elapsed"], "last": f["first_observed_elapsed"]})
        text = " → ".join(f"T{x['first']:.1f} {x['side']} {x['q']:f}@{x['cost']/x['q']:.2f}" for x in fs)
        descriptions.append(text + "（" + e["path_to_repair_fill"] + "）")
    desc = "；".join(descriptions) if descriptions else f"{m['normal_windows']} 次 NORMAL 窗口，无成交"
    residual = ", ".join(f"{side} {m['residual_'+side.lower()]}" for side in ("YES", "NO")
                         if D(m['residual_'+side.lower()])) or "0"
    lines.append(f"| {m['market']} | {desc} | {money(m['full_pnl'])} | {money(m['merge_pnl'])} | {residual} |")
    timeline_rows.append({"market": m["market"], "description": desc, "residual": residual})
(OUT / "timeline_digest.json").write_text(json.dumps(timeline_rows, ensure_ascii=False, indent=2))
lines += ["", "有配对兑付的 19 场，Merge 确认约发生在 T254.1–257.8；4 场无成交，1 场仅尘量仓位未 Merge。", "",
          "## 运行状况与需要跟进的事实", "",
          "- 24 场均 COMPLETED / MARKET_CUTOFF，最终关闭屏障均 SAFE_TO_CONTINUE；这表示订单安全关闭，不表示库存归零。",
          f"- 按去重成交重建，session净敞口绝对值峰值{max_observed_delta} share，单场累计买入成本最高{max_session_buy_cost} USDC。"
          "这是已观察成交口径，不是对未成交挂单的预算预占审计。",
          "- 324 个窗口（NORMAL 262 / REPAIR 62），318 次实际 POST；初始结果 ACK 310、UNKNOWN 4、REJECTED 4，另 6 次 ABORTED 未发 POST。"
          "66 条去重经济成交均最终 CONFIRMED。24 份同步缓存 failure/fatal 均空、dropped_events 均 0、io_drained 均 true。",
          "- 15:25 的 w4 临时 BLOCKED，原因 SYNC_CACHE_NOT_READY；之后恢复，整场完成。不能把最终安全关闭理解成过程中没有阻塞。",
          "- ≤2.5 share 暂停修复实际出现 3 场：14:50 剩 YES 2.136341，15:20 剩 YES 0.27，15:30 剩 NO 0.27；库存与成本仍保留，未伪装为已配平。",
          "- 15:15 最小订单把 NO 买到 7.7 share，session 留 NO 2.708305，仍高于 2.5 阈值。"
          "随后 MINIMUM_FORCED_REVERSE_RESIDUAL 在 T109.75–203.63 重复 9206 次，未再补仓成交。"
          "源代码在补仓方向回到原始方向、且 Delta 未超 5 时拒绝计划；这说明阈值之外还有原来的反向尾仓保护。"
          "重复记录是同一库存/保护规则反复评估，不是 9206 个独立止损信号。",
          "- 路径时钟倒序保护 REPAIR_PATH_TIME_REVERSED：15:15 15 次、15:25 25 次。"
          "15:25 集中在 T46.145–46.235（约 90ms）；不能据此直接推定显著收益影响，需单独核对时间锚与历史点处理。",
          "- 14:50 TP_VOLATILITY_UNKNOWN 阻塞 25 次，T30.06–37.46；首个实际补仓已是 SL，随后部分成交至剩 2.136341 股停止。"
          "这延续了早盘已记录的 TP 波动率预热缺口。",
          "- 15:10 TP→SL 最终亏 −0.75、15:25 第二轮 TP→SL 最终亏 −0.50；反例是 15:15 直接 SL 的一轮配对收益 +0.40。"
          "SL 标签不等于必然亏损，TP 标签也不保证盈利。", "",
          "## 已核实的残仓结算", "",
          "| 市场 | 尾仓 | 赢家 | 尾仓兑付权益 | 完整PnL |",
          "|---|---|---:|---:|---:|"]
for m, t in zip(markets, timeline_rows, strict=True):
    if D(m["residual_yes"]) or D(m["residual_no"]):
        lines.append(f"| {m['market']} | {t['residual']} | {m['settlement_winner']} | "
                     f"{D(m['settlement_payout']):.6f} | {money(m['full_pnl'])} |")
lines += ["", "## 结论与证据", "",
          "当前观察继续支持优先检查 TP 收益兑现与 TP→SL 的利润回吐；新增样本没有证明此前矛盾已经消失。"
          "新增每场完整亏损由−0.4682305减至−0.377867，但没有随机对照，不据此宣称策略已改善EV。",
          "收益、订单和库存均从原始日志重算，并与 sm_run_result / sm_merge_result 对账；"
          "audit.ipynb 独立重算总收益、回撤与模式归因桥接，source_manifest.json 保留源文件哈希。",
          "summary.json、markets.csv、episodes.csv、orders.csv、timeline.json、operations.json 为明细。",
          "渲染器生成交互曲线并验证数据输入；当前无浏览器工具，未宣称浏览器交互验收。", ""]
(OUT / "README.md").write_text("\n".join(lines))

chart = {"schemaVersion": 1, "id": "repair-24-settled-pnl", "title": "24 场累计 PnL：完整收益与 Merge 收益",
         "description": "按场次顺序；完整收益含已核实的8场尾仓到期兑付权益。",
         "chart": {"type": "line", "x": "场次与开始时间", "y": "累计PnL", "series": "收益口径", "yLabel": "USDC", "showXAxisLabel": False},
         "rows": [{"场次与开始时间": "起点", "收益口径": label, "累计PnL": 0}
                  for label in ("完整经济PnL", "Merge PnL")]
                 + [{"场次与开始时间": f"{i+1:02d} {m['market']}", "收益口径": label,
                     "累计PnL": float(m[key])}
                    for i, m in enumerate(markets)
                    for label, key in (("完整经济PnL", "full_cumulative"), ("Merge PnL", "cumulative"))],
         "source": {"label": "24份原始执行日志；CONFIRMED成交与Merge对账，CLOB赢家核对尾仓兑付权益。",
                    "files": [{"label": "markets.csv"}, {"label": "source_manifest.json"}],
                    "caveats": ["场次等距排列，中间13:20–14:35没有测试场次。", "完整PnL含到期权益，不代表redeem现金到账；两条曲线均非盘中估值。",
                                "完整PnL最大回撤11.434 USDC；Merge曲线最大回撤9.23642531 USDC。"]}, "theme": "codex-classic"}
(OUT / "reviewed-chart.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2))

nb = nbformat.v4.new_notebook()
nb.metadata.kernelspec = {"name": "repair-analysis", "display_name": "Repair analysis", "language": "python"}
nb.cells = [
    nbformat.v4.new_markdown_cell("# 24场 REPAIR 审计\n\n"
        "范围：2026-10-19 12:20–13:20、14:35–15:35（上海时间）。\n\n"
        "完整PnL−10.153166 USDC，平均−0.42304858；按场末最终收益累计的最大回撤11.434。"
        "29轮实际补仓：TP7、SL22；首次TP15，其中8轮转SL。8场尾仓赢家由CLOB接口核实。"),
    nbformat.v4.new_markdown_cell("## 数据与方法\n\n按交易ID+订单ID去重；全部成交最终CONFIRMED。"
        "逐市场精确核对数量、USDC六位成本与Merge释放成本。逐轮成本与session平均成本的归因差额单列。"
        "无成交市场保留在均值分母；没有将未知结算或零API结果当成0收益。"),
    nbformat.v4.new_code_cell("import json, hashlib\nfrom pathlib import Path\nfrom decimal import Decimal as D\n"
        f"OUT=Path({str(OUT)!r})\ns=json.loads((OUT/'summary.json').read_text())\n"
        "for src in json.loads((OUT/'source_manifest.json').read_text()):\n"
        "    for item in [src,*src['supporting_sources']]:\n"
        "        assert hashlib.sha256(Path(item['source']).read_bytes()).hexdigest()==item['sha256']\n"
        "assert len(s['markets'])==24\nprint('24 journal hashes and 48 supporting source hashes verified')"),
    nbformat.v4.new_code_cell("from IPython.display import display,Markdown,Image\n"
        "table='|市场|完整PnL|剩YES|剩NO|\\n|---|---:|---:|---:|\\n'\n"
        "for m in s['markets']:\n"
        "    table+='| '+' | '.join(str(m[k]) for k in ['market','full_pnl','residual_yes','residual_no'])+' |\\n'\n"
        "display(Markdown(table))"),
    nbformat.v4.new_code_cell("total=sum((D(m['merge_pnl']) for m in s['markets']),D(0))\n"
        "value=peak=mdd=D(0)\n"
        "for m in s['markets']:\n"
        "    value+=D(m['merge_pnl'])\n    peak=max(peak,value)\n    mdd=max(mdd,peak-value)\n"
        "assert total==D(s['stats']['merge_pnl'])\n"
        "assert mdd==D(s['stats']['realized_merge_max_drawdown']['amount'])\n"
        "ep=[e for m in s['markets'] for e in m['episodes'] if e['repair_fill_mode']]\n"
        "assert len(ep)==29\n"
        "from collections import Counter\nassert Counter(e['repair_fill_mode'] for e in ep)=={'TP':7,'SL':22}\n"
        "assert Counter(e['first_exit_mode'] for e in ep)=={'TP':15,'SL':14}\n"
        "local_pairs=sum((D(e['paired_pnl']) for e in ep),D(0))\n"
        "assert abs(local_pairs+D(s['stats']['session_pool_cost_adjustment'])-total)<D('1e-9')\n"
        "print('Merge total:',total,'Mean:',total/24,'Max drawdown:',mdd)\n"
        "full_total=sum((D(m['full_pnl']) for m in s['markets']),D(0))\n"
        "value=peak=full_dd=D(0)\n"
        "for m in s['markets']:\n"
        "    value+=D(m['full_pnl'])\n    peak=max(peak,value)\n    full_dd=max(full_dd,peak-value)\n"
        "assert full_total==D('-10.153166') and full_dd==D('11.434')\n"
        "assert sum((D(e['full_pnl']) for m in s['markets'] for e in m['episodes']),D(0))==full_total\n"
        "ev=s['stats']['settlement_evidence']\nassert hashlib.sha256(Path(ev['source']).read_bytes()).hexdigest()==ev['sha256']\n"
        "print('Full PnL:',full_total,'Full max drawdown:',full_dd)"),
    nbformat.v4.new_code_cell("import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig,ax=plt.subplots(figsize=(11,4.2),layout='constrained')\n"
        "x=list(range(25)); y=[0]+[float(m['cumulative']) for m in s['markets']]\n"
        "ax.plot(x,y,marker='o',markersize=3,label='Merge PnL')\n"
        "ax.plot(x,[0]+[float(m['full_cumulative']) for m in s['markets']],marker='s',markersize=3,label='Full PnL with settlement rights')\nax.legend()\n"
        "ax.axhline(0,color='gray',linewidth=.7); ax.axvline(12.5,color='gray',linestyle=':',linewidth=.8)\n"
        "ax.set(xlabel='Market sequence (12:20-13:20; 14:35-15:35, Shanghai)',ylabel='Cumulative PnL (USDC)',title='24 markets: full economic PnL and realized Merge PnL')\n"
        "ax.set_xticks(range(0,25,2));ax.grid(axis='y',alpha=.2)\n"
        "fig.savefig(OUT/'cumulative-pnl.png',dpi=160);plt.close(fig)\n"
        "display(Image(filename=str(OUT/'cumulative-pnl.png')))"),
    nbformat.v4.new_markdown_cell("## 解释与边界\n\n"
        "TP初次出现15轮，实际TP成交7轮，另外8轮转SL。模式不是盈利标签。"
        "session成本池与逐轮配对归因差额+0.4124126506 USDC，对应残仓成本变化。"
        "8场尾仓赢家与token经CLOB核实，完整PnL−10.153166。"
        "累计场末PnL回撤不是盘中或现金账户回撤，也不代表redeem已到账。"
        "旧新12场不是随机对照，不作策略改动因果或长期EV判断。\n\n"
        "运行异常细节见operations.json；逐场和逐轮时间线见README.md及timeline.json。"),
]
kernel_root = Path("/home/ubuntu/poly-maker-data/research_outputs/REPAIR_ANALYSIS_20261019/jupyter")
kernel = kernel_root / "kernels/repair-analysis"
kernel.mkdir(parents=True, exist_ok=True)
(kernel / "kernel.json").write_text(json.dumps({"argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                                               "display_name": "Repair analysis", "language": "python"}))
os.environ["JUPYTER_PATH"] = str(kernel_root)
NotebookClient(nb, timeout=120, kernel_name="repair-analysis", resources={"metadata": {"path": str(OUT)}}).execute()
nbformat.validate(nb)
assert not any(o.output_type == "error" for c in nb.cells if c.cell_type == "code" for o in c.outputs)
nbformat.write(nb, OUT / "audit.ipynb")
(OUT / "validation.json").write_text(json.dumps({"market_count": 24, "economic_fills": 66,
    "all_final_fill_statuses_confirmed": True, "source_hashes_verified": 73,
    "independent_total_drawdown_and_path_bridge_checks": "PASS", "executed_notebook": "PASS",
    "settlement_results": "8_CLOB_WINNERS_VERIFIED", "full_pnl": "-10.153166", "full_pnl_max_drawdown": "11.434", "browser_interaction_qa": "NOT_AVAILABLE"}, indent=2))
print("24-market report, source validation, executed notebook and chart payload ready.")
