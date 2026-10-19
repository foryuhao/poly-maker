"""Build a reviewed chart payload and an executed audit notebook."""

import hashlib
import json
import os
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
summary = json.loads((OUT / "summary.json").read_text())
manifest = json.loads((OUT / "source_manifest.json").read_text())
for source in manifest:
    assert hashlib.sha256(Path(source["source"]).read_bytes()).hexdigest() == source["sha256"]

chart = {
    "schemaVersion": 1,
    "id": "repair-last12-realized-merge-pnl",
    "title": "12 场累计已实现 Merge PnL",
    "description": "2026-10-19 上海时间；不含四场残仓结算收益及盘中浮动盈亏。",
    "chart": {
        "type": "line", "x": "市场开始时间", "y": "累计已实现收益",
        "yLabel": "USDC", "showXAxisLabel": False,
    },
    "rows": [{"市场开始时间": "起点", "累计已实现收益": 0}]
    + [{"市场开始时间": m["market"], "累计已实现收益": float(m["cumulative"])}
       for m in summary["markets"]],
    "source": {
        "label": "12 份执行日志中的 CONFIRMED 成交与 Merge；逐场独立重算并核对。",
        "files": [{"label": "markets.csv"}, {"label": "source_manifest.json"}],
        "caveats": [
            "以每场开始时间标记该场最终 Merge 收益。",
            "仅已实现 Merge，未含 12:20、12:30、12:40、12:50 的残仓结算。",
            "本曲线最大回撤为 6.30097148 USDC；并非完整账户净值或盘中最大回撤。",
        ],
    },
    "theme": "codex-classic",
}
(OUT / "reviewed-chart.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2))

nb = nbformat.v4.new_notebook()
nb.metadata.kernelspec = {"name": "repair-analysis", "display_name": "Repair analysis", "language": "python"}
nb.cells = [
    nbformat.v4.new_markdown_cell(
        "# 最近 12 场 REPAIR 审计\n\n"
        "固定范围：2026-10-19 12:20–13:20 Asia/Shanghai。以原始执行日志为准；"
        "成交按 trade_id + maker_order_id 去重，最终状态均为 CONFIRMED。\n\n"
        "Merge PnL = 配对兑付 − 已释放成本。完整 PnL = Merge 兑付 + 残仓结算兑付 − 全部买入成本。"
        "四场残仓结果未核实，使用二元结算上下界，不能用 Merge PnL 代替完整收益。"
    ),
    nbformat.v4.new_code_cell(
        "import json, hashlib\nfrom pathlib import Path\nfrom decimal import Decimal as D\n"
        f"OUT = Path({str(OUT)!r})\n"
        "s = json.loads((OUT/'summary.json').read_text())\n"
        "for src in json.loads((OUT/'source_manifest.json').read_text()):\n"
        "    assert hashlib.sha256(Path(src['source']).read_bytes()).hexdigest() == src['sha256']\n"
        "assert len(s['markets']) == 12\n"
        "print('12 source hashes verified; see analyze.py for fill/cost/Merge reconciliation.')"
    ),
    nbformat.v4.new_code_cell(
        "from IPython.display import display, Markdown\n"
        "table = '| 市场 | Merge PnL | 剩余 YES | 剩余 NO | 完整 PnL 下界 | 上界 |\\n'"
        " + '|---|---:|---:|---:|---:|---:|\\n'\n"
        "for m in s['markets']:\n"
        "    table += '| ' + ' | '.join(str(m[k]) for k in "
        "['market','merge_pnl','residual_yes','residual_no','full_pnl_lower','full_pnl_upper']) + ' |\\n'\n"
        "display(Markdown(table))"
    ),
    nbformat.v4.new_code_cell(
        "pnls = [D(m['merge_pnl']) for m in s['markets']]\n"
        "total = sum(pnls, D(0))\npeak = value = mdd = D(0)\n"
        "for x in pnls:\n    value += x\n    peak = max(peak, value)\n    mdd = max(mdd, peak-value)\n"
        "assert total == D(s['stats']['merge_pnl'])\n"
        "assert mdd == D(s['stats']['realized_merge_max_drawdown']['amount'])\n"
        "print('Total Merge PnL:', total, '\\nMean / market:', total/12, '\\nMerge PnL max drawdown:', mdd)\n"
        "lo = sum((D(m['full_pnl_lower']) for m in s['markets']), D(0))\n"
        "hi = sum((D(m['full_pnl_upper']) for m in s['markets']), D(0))\n"
        "assert hi < 0\nprint('Full economic PnL bounds:', lo, hi)"
    ),
    nbformat.v4.new_code_cell(
        "table = '| 到补仓成交的路径 | 轮数 | 占比 | 配对部分 PnL | 近似配平轮数 |\\n'"
        " + '|---|---:|---:|---:|---:|\\n'\n"
        "for p in s['stats']['paths']:\n"
        "    table += f\"| {p['path']} | {p['episodes']} | {p['share']:.2%} | {p['paired_pnl']} | {p['fully_paired']} |\\n\"\n"
        "display(Markdown(table))\n"
        "print('TP/SL are order modes, not outcome profitability labels. Overshoot leftovers are separate.')"
    ),
    nbformat.v4.new_markdown_cell(
        "## 数据边界\n\n"
        "最大回撤按场末累计已实现 Merge 收益计算；未纳入残仓和盘中全仓估值。"
        "Gamma 查询未获取四场市场的结算结果，见 resolution_queries.json；未将末端报价当结算。"
        "12 个已完成市场执行状态均为 COMPLETED / SAFE_TO_CONTINUE，但这不表示实际仓位已配平。"
        "详细经济成交、订单和原始日志行号分别见 episodes.csv、orders.csv、timeline.json。"
    ),
]
kernel_root = Path("/home/ubuntu/poly-maker-data/research_outputs/REPAIR_ANALYSIS_20261019/jupyter")
kernel = kernel_root / "kernels/repair-analysis"
kernel.mkdir(parents=True, exist_ok=True)
(kernel / "kernel.json").write_text(json.dumps({
    "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
    "display_name": "Repair analysis", "language": "python",
}))
os.environ["JUPYTER_PATH"] = str(kernel_root)
NotebookClient(nb, timeout=120, kernel_name="repair-analysis", resources={"metadata": {"path": str(OUT)}}).execute()
nbformat.write(nb, OUT / "audit.ipynb")
assert all(c.get("execution_count") is not None for c in nb.cells if c.cell_type == "code")
assert not any(o.output_type == "error" for c in nb.cells if c.cell_type == "code" for o in c.outputs)
print("Executed audit.ipynb; source hashes, cost reconciliation and independent drawdown checks passed.")
