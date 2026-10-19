"""Create an executed companion notebook and reviewed inline chart input."""

import json
import os
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient

OUT = Path(__file__).resolve().parent
summary = json.loads((OUT / "summary.json").read_text())
tp = [e for e in summary["episodes"] if e["first_tp"]]
stats = summary["stats"]
rows = [
    {"阶段": "已记录峰值合计", "USDC": float(stats["tp_to_sl_peak_mark_sum"])},
    {"阶段": "SL条件点浮盈合计", "USDC": float(stats["tp_to_sl_sl_trigger_mark_sum"])},
    {"阶段": "最终配对收益", "USDC": float(stats["tp_to_sl_actual_paired_pnl"])},
]
chart = {
    "schemaVersion": 1,
    "id": "tp-sl-profit-stages",
    "title": "5 轮 TP→SL：峰值、触发与成交",
    "chart": {"type": "bar", "x": "阶段", "y": "USDC",
              "yLabel": "USDC", "showXAxisLabel": False},
    "rows": rows,
    "source": {
        "label": "2026-10-19 12:20–13:20 的固定 12 场执行日志；5 轮 TP→SL。",
        "files": [{"label": "episodes.csv"}, {"label": "price_paths.json"}, {"label": "source_manifest.json"}],
        "caveats": [
            "浮盈峰值是首次TP之后、首次补仓成交被本地观察到之前的原始腿mid估值峰值，不能当可成交收益。",
            "最终收益只计配对部分，三轮反向残仓尚未计入最终结算；Maker价差也可能使配对收益高于mid浮盈峰值。",
            "峰值、触发估值和配对收益是同一组5轮交易的诊断比较，不是可恢复利润或反事实回测。",
        ],
    },
    "theme": "codex-classic",
}
(OUT / "reviewed-chart.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2))

nb = nbformat.v4.new_notebook()
nb.metadata.kernelspec = {"name": "repair-analysis", "display_name": "Repair analysis", "language": "python"}
nb.cells = [
    nbformat.v4.new_markdown_cell(
        "# TP 逐轮兑现分析\n\n"
        "固定来源：2026-10-19 12:20–13:20 Asia/Shanghai 的12场，14轮实际REPAIR，其中9轮先TP。"
        "NORMAL保持不变；本次只分析历史，未修改策略。\n\n"
        "原始腿浮盈 = 最终实际开仓数量 × 持仓方向mid − 实际开仓成本。"
        "仅观测到补仓首笔MATCHED之前；不是全账户实际逐时仓位，也不把峰值当成可成交PnL。"
        "报价配对收益按同一数量、实际开仓成本与限价计算；三轮反向残仓成本和结算另行保留。"
    ),
    nbformat.v4.new_code_cell(
        "import json, hashlib\nfrom pathlib import Path\nfrom decimal import Decimal as D\n"
        "from IPython.display import display, Markdown\n"
        f"OUT = Path({str(OUT)!r})\n"
        "s=json.loads((OUT/'summary.json').read_text())\n"
        "paths=json.loads((OUT/'price_paths.json').read_text())\n"
        "tp=[e for e in s['episodes'] if e['first_tp']]\n"
        "for src in json.loads((OUT/'source_manifest.json').read_text()):\n"
        "    assert hashlib.sha256(Path(src['source']).read_bytes()).hexdigest()==src['sha256']\n"
        "assert len(tp)==9 and len(s['episodes'])==14\n"
        "assert s['stats']['max_unmapped_path_batches']==0\n"
        "print('12 unchanged source hashes; 14 episodes, 9 TP-first; all raw path batches mapped.')"
    ),
    nbformat.v4.new_markdown_cell("## 逐轮收益\n\n首次TP、已记录峰值、首张实际TP报价下的配对收益、实际配对收益分开列示。"),
    nbformat.v4.new_code_cell(
        "table='|市场|开仓窗口|首次TP浮盈|峰值|首张TP报价配对收益|最终配对收益|\\n|---|---|---:|---:|---:|---:|\\n'\n"
        "for e in tp:\n"
        "    values=[e['market'],e['opening_window'].split(':')[-1],e['first_tp']['mark_pnl'],"
        "e['peak_original_leg_mark']['original_leg_mark_pnl'],e['first_tp_post']['paired_pnl_at_quote'],e['actual_paired_pnl']]\n"
        "    table+='| '+' | '.join(str(x) for x in values)+' |\\n'\n"
        "display(Markdown(table))"
    ),
    nbformat.v4.new_markdown_cell(
        "## TP 路径\n\n统一横轴为首次TP条件点之后的秒数，统一纵轴为USDC。"
        "曲线是原始腿估值，横向虚线是首张TP报价收益，菱形为实际配对收益；它们不是同一个会计量。"
        "SL竖线是历史条件点，POST竖线是实际下单时点。所有时点均在本地观察坐标中投影，非交易所撮合时刻。"
    ),
    nbformat.v4.new_code_cell(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "from matplotlib.lines import Line2D\n"
        "fig,axes=plt.subplots(3,3,figsize=(14,10),sharex=True,sharey=True,layout='constrained')\n"
        "for ax,e in zip(axes.flat,tp):\n"
        "    begin=e['first_tp']['projected_monotonic']\n"
        "    points=sorted([p for p in paths if p['opening_window']==e['opening_window']],key=lambda p:p['projected_monotonic'])\n"
        "    xs=[p['projected_monotonic']-begin for p in points]\n"
        "    ys=[float(p['original_leg_mark_pnl']) for p in points]\n"
        "    ax.step(xs,ys,where='post',color='#2166ac',linewidth=1.3)\n"
        "    anchor=e['first_tp']['wall_elapsed_projection']\n"
        "    ax.axvline(e['first_tp_post']['post_wall_elapsed']-anchor,color='#b76c12',linewidth=1,linestyle=':')\n"
        "    if e['first_sl']:\n"
        "        ax.axvline(e['first_sl']['wall_elapsed_projection']-anchor,color='#555555',linewidth=1,linestyle='--')\n"
        "    target=float(e['first_tp_post']['paired_pnl_at_quote'])\n"
        "    ax.axhline(target,color='#b76c12',linewidth=1,linestyle='--')\n"
        "    ax.scatter(e['first_observed_repair_fill']['first_observed_elapsed']-anchor,float(e['actual_paired_pnl']),"
        "marker='D',s=28,color='#222222',zorder=4)\n"
        "    ax.axhline(0,color='#999999',linewidth=.7)\n"
        "    ax.grid(axis='y',color='#dddddd',linewidth=.5)\n"
        "    ax.set_title(e['market']+' '+e['opening_window'].split(':')[-1]+' | '+e['original_quantity']+' shares',fontsize=11)\n"
        "    ax.spines[['top','right']].set_visible(False)\n"
        "    ax.tick_params(labelsize=10)\n"
        "for ax in axes[-1]: ax.set_xlabel('Seconds after first TP condition',fontsize=10)\n"
        "for ax in axes[:,0]: ax.set_ylabel('USDC',fontsize=10)\n"
        "fig.legend(handles=[Line2D([],[],color='#2166ac',label='Original-leg mark'),"
        "Line2D([],[],color='#b76c12',linestyle='--',label='First TP quote target'),"
        "Line2D([],[],color='#b76c12',linestyle=':',label='First TP POST'),"
        "Line2D([],[],color='#555555',linestyle='--',label='SL condition'),"
        "Line2D([],[],color='#222222',marker='D',linestyle='none',label='Actual paired PnL')],"
        "loc='outside upper center',ncol=5,frameon=False,fontsize=10)\n"
        "fig.savefig(OUT/'tp-paths.png',dpi=160,bbox_inches='tight')\n"
        "from IPython.display import Image\ndisplay(Image(filename=str(OUT/'tp-paths.png')))\nplt.close(fig)"
    ),
    nbformat.v4.new_markdown_cell(
        "## 验证与解释边界\n\n峰值是事后诊断基准，不是可实现的反事实收益；Maker成交可能优于mid估值。"
        "原始5股中途部分成交时，全量开仓固定投影与当时实际数量不同。主表9轮TP已逐点核对记录估值，"
        "均在微美元容差内；直接SL控制组有一个开仓分笔锚点，保留为固定原始腿估值，不伪称实时全仓PnL。"
        "以下固定订单篮子的报价计算不假设真正会成交，也不模拟提前成交后新的NORMAL轮次。"
    ),
    nbformat.v4.new_code_cell(
        "tp_windows={e['opening_window'] for e in tp}\nby={e['opening_window']:e for e in tp}\n"
        "for p in paths:\n"
        "    if p['opening_window'] not in tp_windows:continue\n"
        "    q=D(by[p['opening_window']]['original_quantity'])\n"
        "    assert abs(D(p['original_leg_mark_pnl'])-q*D(str(p['pnl_per_share'])))<D('.000001')\n"
        "actual=sum((D(e['actual_paired_pnl']) for e in tp),D(0))\n"
        "targets=sum((D(e['first_tp_post']['paired_pnl_at_quote']) for e in tp),D(0))\n"
        "assert actual==D(s['stats']['tp_first_actual_paired_pnl'])\n"
        "assert targets==D(s['stats']['first_tp_quote_fixed_basket_paired_pnl'])\n"
        "print('Actual TP-first paired PnL:',actual,'\\nFirst quote basket:',targets)\n"
        "subset=[e for e in tp if e['first_sl']]\n"
        "peak=sum((D(e['peak_original_leg_mark_before_sl']['original_leg_mark_pnl']) for e in subset),D(0))\n"
        "sl=sum((D(e['first_sl']['mark_pnl']) for e in subset),D(0))\n"
        "final=sum((D(e['actual_paired_pnl']) for e in subset),D(0))\n"
        "assert peak-final==(peak-sl)+(sl-final)\n"
        "print('TP->SL: peak marks',peak,'SL-trigger marks',sl,'actual paired',final)\n"
        "print('Stage differences:',peak-sl,sl-final,'(diagnostic comparisons, not recoverable profit)')"
    ),
]
kernel_root = Path('/home/ubuntu/poly-maker-data/research_outputs/REPAIR_ANALYSIS_20261019/jupyter')
kernel = kernel_root / 'kernels/repair-analysis'
kernel.mkdir(parents=True, exist_ok=True)
(kernel / 'kernel.json').write_text(json.dumps({
    'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
    'display_name': 'Repair analysis', 'language': 'python',
}))
os.environ['JUPYTER_PATH'] = str(kernel_root)
NotebookClient(nb, timeout=120, kernel_name='repair-analysis', resources={'metadata': {'path': str(OUT)}}).execute()
nbformat.write(nb, OUT / 'audit.ipynb')
assert all(c.get('execution_count') is not None for c in nb.cells if c.cell_type == 'code')
assert not any(o.output_type == 'error' for c in nb.cells if c.cell_type == 'code' for o in c.outputs)
print('Executed audit.ipynb; independent mark checks and stage reconciliation passed.')
