"""Write supporting notes, execute the audit notebook and prepare a reviewed chart."""

import hashlib
import json
import os
from decimal import Decimal as D
from pathlib import Path

import nbformat
from nbclient import NotebookClient

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
BASE = OUT.parent / "20261019_repair_24_markets_1220_1535"
s = json.loads((OUT / "summary.json").read_text())
st, eps, windows, gaps = (s[k] for k in ("stats", "episodes", "windows", "gaps"))
for src in json.loads((OUT / "source_manifest.json").read_text()):
    assert hashlib.sha256(Path(src["source"]).read_bytes()).hexdigest() == src["sha256"]
    assert hashlib.sha256(Path(src["cache_snapshots"]).read_bytes()).hexdigest() == src["cache_snapshots_sha256"]
baseline_sources = json.loads((BASE / "source_manifest.json").read_text())
assert len(baseline_sources) == 24
for src in baseline_sources:
    assert hashlib.sha256(Path(src["source"]).read_bytes()).hexdigest() == src["sha256"]
(OUT / "baseline_receipt.json").write_text(json.dumps({
    "scope": "Fixed prior 24 markets; all raw journal hashes reverified",
    "summary": {"source": str(BASE / "summary.json"), "sha256": hashlib.sha256((BASE / "summary.json").read_bytes()).hexdigest()},
    "source_manifest": {"source": str(BASE / "source_manifest.json"), "sha256": hashlib.sha256((BASE / "source_manifest.json").read_bytes()).hexdigest()},
    "raw_sources": [{"source": x["source"], "sha256": x["sha256"]} for x in baseline_sources]}, indent=2))

labels = {"signal_to_detection": "路径触发至识别", "detection_to_first_post": "识别至首次POST",
          "post_http": "POST网络", "close_to_repost": "撤单至重挂",
          "resting_or_observation": "挂单或回报等待"}
switching = [e for e in eps if e["previous_tp_windows"]]
direct = [e for e in eps if not e["previous_tp_windows"]]
switch_mean = sum(e["time_phases"]["detection_to_first_post"] for e in switching)/len(switching)
direct_mean = sum(e["time_phases"]["detection_to_first_post"] for e in direct)/len(direct)
total_gap = D(st["benchmarks"]["trigger_mark"])-D(st["benchmarks"]["actual_pair_pnl"])
notes = ["# 24场数据：SL识别与执行脱节研究", "",
         "固定样本：2026-10-19 上海时间12:20–13:20、14:35–15:35，共24场，沿用上一轮审计的session及文件哈希。",
         "固定HD/TP/SL规则和参数；本次只读分析，没有修改生产策略、配置、实时订单或进程。",
         "主要分析：其中16场内的22轮实际SL补仓，47次SL POST，25次撤换；另检查TP成交后的尾仓SL。", "",
         "## 结论", "",
         "执行层存在显著脱节，主要在订单生命周期及证据等待，而非签名/计划计算慢。",
         f"系统识别SL至首次本地观察MATCHED，平均{st['detection_to_first_match_sec']['mean']:.3f}秒，"
         f"中位数{st['detection_to_first_match_sec']['median']:.3f}秒，最长{st['detection_to_first_match_sec']['max']:.3f}秒。",
         f"若从历史价格路径首次满足SL算起，平均{st['signal_to_first_match_sec']['mean']:.3f}秒；"
         "其中历史触发到实际识别的均值0.976秒、中位数约5ms，呈长尾，而非每次固定慢1秒。",
         f"识别后首单延迟集中在模式切换：7轮需要撤销现有TP单，识别SL至首个SL POST平均{switch_mean:.3f}秒；"
         f"其余15轮平均{direct_mean*1000:.1f}ms。应优先检查模式切换/关闭核对路径，而不是优化毫秒级计划计算。",
         "47次发单前的最终SDK证据均满足quote=当时对侧ask−tick；计划到POST中位约15ms，所有47次原始路径as-of中点均与最终盘口一致。"
         "不能把问题概括成系统直接提交数秒前的旧报价。较老的book接收时间也不等于报价内容已经落后，REPAIR允许安静盘口继续有效。",
         "但下单后的价格风险仍然存在：44个ACK订单中，12个在POST到ACK期间出现至少1tick不利中点移动，5个至少2tick，最大3.5tick；"
         "这是中点变化，不是ACK时完整BBO或交易所接收时刻的直接观测。", "",
         "## 时间口径与分解", "",
         "使用原生monotonic时间；历史触发点通过同一次observe的当前点锚定，不能直接把官方策略elapsed与本地wall时间相减。",
         "MATCHED是本地首次收到成交的时刻。交易日志原生venue撮合时间缺失，挂单等待和成交回报延迟无法进一步可靠拆开。",
         "以下均值以22轮为分母，包含零次撤换的轮次；各阶段不重叠，严格加总到触发→首次MATCHED。", "",
         "| 环节 | 平均秒数/轮 | 中位数 | 最大 |",
         "|---|---:|---:|---:|"]
for k, name in labels.items():
    x = st["time_phases"][k]
    notes.append(f"| {name} | {x['mean']:.3f} | {x['median']:.3f} | {x['max']:.3f} |")
notes += ["", "POST网络阶段覆盖直到首次MATCHED之前的所有SL POST；撤单重挂阶段剔除与POST重叠的区间。"
          "剩余时间包括挂单、监控、末次撤单过程及成交回报观察。阶段最大值来自不同轮次，不能相加。",
          "最后一笔MATCHED到CONFIRMED平均6.527秒；它不属于该轮已经成交之前的风险延迟，不能再加进本轮成交损失。", "",
          "## 执行脱节对应的收益差额", "",
          "固定每轮最终实际配对的数量Q=min(原始成交量,补仓成交量)，原始均价保持实际值。"
          "第一点是SL触发时同一篮子原始持仓的中点浮盈；首单点是实际Maker报价对应的配对收益目标；"
          "终点是实际补仓VWAP对应的已配对PnL。原始数量较小或补仓不足时，统一使用相同Q，避免把数量变化混成滑点。",
          "该比较不含最小订单带来的额外方向尾仓结算；也不代表在信号价、首单价必然能成交。", "",
          "| 比较节点 | 22轮相同配对篮子的PnL基准（USDC） |",
          "|---|---:|"]
for name, key in (("路径触发时的中点浮盈", "trigger_mark"), ("系统识别时的中点浮盈", "detection_mark"),
                  ("首次POST时的中点浮盈", "first_post_mark"), ("首次SL报价的配对收益目标", "first_post_quote_pair_target"),
                  ("实际补仓成交的配对PnL", "actual_pair_pnl")):
    notes.append(f"| {name} | {D(st['benchmarks'][key]):+.6f} |")
notes += ["", f"从路径触发基准到实际配对PnL的差额为 **{total_gap:.6f} USDC**，分解如下：",
          "", "| 差额环节（正值为恶化） | USDC |", "|---|---:|"]
for name, key in (("路径触发→实际识别", "signal_to_detection"), ("实际识别→首次POST", "detection_to_first_post"),
                  ("首次POST中点→Maker报价的改善", "post_mark_to_quote"), ("首次SL报价→实际成交", "first_quote_to_fill")):
    notes.append(f"| {name} | {D(st['money_phases'][key]):+.6f} |")
notes += ["", "首单报价→实际成交：10轮恶化合计4.435910，2轮改善合计1.150000，10轮相同，净恶化3.285910 USDC。",
          "这4.879675是不同时间/价格基准的事后会计差额，不是可回收收益预测。"
          "当前24场完整PnL仍为−10.153166，不能用上述差额直接推算新策略收益。", "",
          "## 撤换过程的证据", "",
          f"- 25次实际撤换，平均{st['replacement_gap_sec']['mean']:.3f}秒，中位{st['replacement_gap_sec']['median']:.3f}秒。",
          "- 22次具有明确canceled回执的零成交旧单，回执至下一POST仍平均2.564秒。3次POST被拒绝后也经历关闭核对。",
          "- 25次采用的最终关闭帧中，positions最后完成19次，capital最后完成4次，time和trades各1次。"
          "这指可见公共页面中的最后完成者，不把未展示的订单详情或本地验证时间强行归入该页面。",
          "- 明确撤销ACK之后，仍有40次针对同一旧单的重复DELETE。源代码会在每次写后推进证据水位，"
          "而close scope要求time/open_orders/trades/capital/positions从该水位之后重新读取。"
          "重复写与整账户刷新共同延长撤换通路；不能把全部时间解释成DELETE网络慢。",
          "- 22次撤换均在历史快照中找到更早的窄范围证据：明确撤销ACK之后发起的订单详情=已撤销且size_matched=0、"
          "关联交易为空，同时完整订单页不再有该订单、完整交易页无该订单的maker leg。",
          f"  这些证据实际发布时刻，比下一POST平均早{st['narrow_zero_fill_proof_lead_sec']['mean']:.3f}秒，"
          f"中位{st['narrow_zero_fill_proof_lead_sec']['median']:.3f}秒，范围"
          f"{st['narrow_zero_fill_proof_lead_sec']['min']:.3f}–{st['narrow_zero_fill_proof_lead_sec']['max']:.3f}秒。",
          "- 上述22段剩余等待内，同一配对篮子的中点估值净恶化1.350000 USDC：11段恶化2.257273，6段改善0.907273，5段相同。"
          "这将可观测的等待与价格变化连接起来，但没有假设提前挂Maker就能成交。",
          "- 重要反例：15:05第二轮SL，T108.326请求追价撤单，T108.575才首次观察到5股成交。"
          "不能把撤单请求、not found或空交易页单独当作无成交证明。该轮必须走完整成交核对。", "",
          "## 22轮逐轮审计", "",
          "T为市场开始后的本地观察秒数，历史触发T为锚定投影。配对目标与配对实绩不含额外尾仓结算。", "",
          "| 市场/开仓窗口 | 路径触发T | SL识别T | 首次POST T | 首次MATCHED T | SL价格序列 | 首单配对目标 | 实际配对PnL |",
          "|---|---:|---:|---:|---:|---|---:|---:|"]
for e in eps:
    prices = "→".join(f"{p:.2f}" for p in e["price_sequence"])
    notes.append(f"| {e['market']} / {e['opening_window'].rsplit(':',1)[-1]} | {e['signal']:.3f} | {e['detected']:.3f} | "
                 f"{e['first_post']:.3f} | {e['first_match']:.3f} | {prices} | "
                 f"{D(e['benchmarks']['first_post_quote_pair_target']):+.6f} | {D(e['benchmarks']['actual_pair_pnl']):+.6f} |")
notes += ["", "## 关键案例", "",
          "### 13:05：首单之前和首单之后都发生恶化", "",
          "NO5@0.60；路径T64.536触发，系统T72.515识别。开仓订单T67.441才完成关闭，随后库存核对又等待约5.074秒；"
          "positions快照显示NO仓位到T71.532才可见，下一次循环T72.515进入REPAIR（此前审计已留存对应快照明细）。",
          "T72.534首挂YES5@0.59，之后0.63→0.76→0.79，T87.016首次观察成交。"
          "从路径触发到成交22.480秒；触发浮盈基准−0.20、首单配对目标−0.95、实际配对−1.95。", "",
          "### 15:10：识别及时，但TP撤换和SL追价迟迟未成交", "",
          "YES5@0.30；路径T72.357触发，T72.362识别。撤销TP并完成关闭后，T75.439首挂NO5@0.71。"
          "SL随后0.77→0.85→0.85，T91.383成交。首单目标−0.05，实际−0.75；"
          "仅首单到成交就有0.70 USDC价差。并非重新计算SL信号本身耗时数秒。", "",
          "### 15:30：很短的延迟也可能对应很大的价格变化", "",
          "第一轮NO5@0.43，路径T100.628触发，T100.790识别，只有约162ms；"
          "同一篮子浮盈基准已从−0.10变成−0.725。T100.815首挂YES@0.73，再挂0.81，T106.478成交，实际−1.20。"
          "因此仅按秒数排优化优先级不够，必须同时衡量信号后价格移动与剩余敞口。", "",
          "### 反例：等待并非总是恶化", "",
          "12:35第一轮识别迟3.442秒，但中点基准由−0.575改善为−0.275，最终配对−0.20。"
          "15:15第二轮首挂NO@0.35，实际以0.13成交，配对收益从首单目标−0.70改善为+0.40；"
          "由于额外NO尾仓结算输掉，该轮完整PnL为+0.049。不能将所有等待都算作可避免亏损。", "",
          "## 覆盖与边界", "",
          "- 22轮覆盖本样本全部实际SL补仓，不按亏损与否筛选。7轮实际TP成交未混入SL执行延迟。",
          "- 另有两轮是TP成交之后，反向尾仓再进入SL却未挂出SL单：12:30（NO5）、12:40（NO0.565714）。"
          "它们受尾仓/最小订单规则约束，已单列tail_only_sl_cases，不当成正常首轮SL成交样本，也没有隐去无单情形。",
          "- 原始撮合时刻缺失；MATCHED→CONFIRMED不能当作尚未成交。",
          "- 信号路径可能从历史补处理，部分点当时库存经济证明尚未就绪；不能假设触发时刻已有合法可执行数量。",
          "- 已确认的是时间、证据和价格差；提前挂单的排队、成交量、后续NORMAL轮次和最终收益尚无可识别反事实。", "",
          "## 在保持状态机规则不变下的执行改进方向", "",
          "1. 优先研究零成交撤换的证据通路。对已明确撤销、详情匹配且size_matched=0、完整交易页一致的旧单，"
          "让下一笔REPAIR不必串行等待全账户positions/capital重新发布；仍保留session预算、官方资金依据、Delta预占、"
          "所有迟到成交及未知状态隔离。其他情况继续原有完整关闭流程。当前22次更早窄证据证明此方向可研究，尚不是生产准入验证。",
          "2. 同时减少明确cancel ACK后的重复DELETE，避免无必要地移动最新写水位；不把ACK单独当完整无成交证明。",
          "3. 另一个需要验证的执行候选：当前SL价格追价检查排在reconcile_owned_order之后。可研究先用本地盘口发起追价撤单，"
          "随后继续完整核对，重挂仍由经济数量与关闭证明约束；与已经提前执行的TP→SL模式撤单检查区分。"
          "本次缺少该检查全过程的BBO记录，尚未量化提前撤单能节约多少时间或收益。",
          "4. 将开仓后SL路径跟踪与开仓关闭/库存核对解耦。先持续记录并准备SL意图，待经济数量得到证明即用最新盘口执行；"
          "不通过超时把MATCHED提升为CONFIRMED，不提前对未经证明的数量下单。",
          "5. 用最新ask−tick持续跟踪仍是Maker约束下的执行选择，无法保证立刻成交。新增监控应分开记录路径触发、状态识别、"
          "取消请求、首次明确撤销、窄证据齐备、全证据齐备、实际POST、MATCHED、CONFIRMED，并记录这些时点的BBO和剩余敞口。",
          "不改TP/SL门槛、不改状态转换，不把4.879675或1.35 USDC差额当作上线后的收益承诺。", "",
          "## 复核与文件", "",
          "analyze.py重算22轮时钟/分段时间和金额桥接、47次最终报价、25次撤换；audit.ipynb再次独立核对总和及源哈希。",
          "source_manifest.json保留16份执行日志及16份cache snapshots哈希；基准24场来自此前固定审计。",
          "episodes.csv、sl_windows.csv、replacement_gaps.csv、price_paths.json保留可检查的时点、价格、原始行号和快照read ID。",
          "代码对照：src/polymaker/repair_policy.py:162；rdp_sm_path_repair.py:455；rdp_sm_lifecycle.py:788；"
          "rdp_sm_cached_reads.py:203。当前源码与日志路径一致，但哈希审计不声称恢复每场历史完整运行镜像。", ""]
(OUT / "README.md").write_text("\n".join(notes))

chart = {"schemaVersion": 1, "id": "sl-execution-time-components-24", "title": "SL路径触发到首次成交：平均耗时分解",
         "description": "24场内22轮实际SL补仓；不含成交后的CONFIRMED等待。",
         "chart": {"type": "bar", "x": "环节", "y": "平均耗时", "yLabel": "秒 / 轮", "showXAxisLabel": False},
         "rows": [{"环节": name, "平均耗时": st["time_phases"][key]["mean"]} for key, name in labels.items()],
         "source": {"label": "固定24场样本中的22轮SL，原始执行日志monotonic时间逐段重算。",
                    "files": [{"label": "episodes.csv"}, {"label": "sl_windows.csv"}],
                    "caveats": ["路径触发时刻可能由系统稍后补处理，已区别实际状态识别时刻。",
                                "成交为本地首次观察MATCHED；挂单等待与回报延迟无法进一步拆开。",
                                "每轮分段不重叠，均值合计9.046746秒；零撤换轮次包含在分母22。"]}, "theme": "codex-classic"}
(OUT / "reviewed-chart.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2))

nb = nbformat.v4.new_notebook()
nb.metadata.kernelspec = {"name": "repair-analysis", "display_name": "Repair analysis", "language": "python"}
nb.cells = [nbformat.v4.new_markdown_cell("# SL执行脱节：固定24场审计\n\n"
    "保持状态机规则，研究22轮SL的47次POST。识别到MATCHED平均8.071秒；撤换平均2.679秒。"
    "触发中点基准到实际配对PnL差额4.879675 USDC，不是可回收收益预测。\n\n"
    "## 方法\n\n统一monotonic时钟；历史触发用同次observe当前点锚定。"
    "配对篮子数量固定为实际匹配数量，不含额外尾仓结算。窄证明提前可见不是新订单成交回放。"),
    nbformat.v4.new_code_cell("import json,hashlib\nfrom pathlib import Path\nfrom decimal import Decimal as D\n"
        f"OUT=Path({str(OUT)!r})\ns=json.loads((OUT/'summary.json').read_text())\n"
        "for src in json.loads((OUT/'source_manifest.json').read_text()):\n"
        "    assert hashlib.sha256(Path(src['source']).read_bytes()).hexdigest()==src['sha256']\n"
        "    assert hashlib.sha256(Path(src['cache_snapshots']).read_bytes()).hexdigest()==src['cache_snapshots_sha256']\n"
        "eps=s['episodes']; ws=s['windows']; gaps=s['gaps']\nassert (len(eps),len(ws),len(gaps))==(22,47,25)\n"
        "assert all(abs(sum(e['time_phases'].values())-e['signal_to_first_match_sec'])<1e-6 for e in eps)\n"
        "assert all(w['guard_price_equals_ask_minus_tick'] for w in ws)\n"
        "assert all(abs(float(w['post_yes_mid'])-w['raw_asof_post_yes_mid'])<1e-8 for w in ws)\n"
        "print('Source hashes, 22 timing bridges and 47 final-price/path checks passed.')"),
    nbformat.v4.new_code_cell("from IPython.display import display,Markdown,Image\n"
        "table='|Market/episode|Trigger|Detected|POST|MATCHED|First quote target|Actual paired PnL|\\n|---|---:|---:|---:|---:|---:|---:|\\n'\n"
        "for e in eps:\n"
        "    table+=f\"|{e['market']}/{e['opening_window'].split(':')[-1]}|{e['signal']:.3f}|{e['detected']:.3f}|{e['first_post']:.3f}|{e['first_match']:.3f}|{e['benchmarks']['first_post_quote_pair_target']}|{e['benchmarks']['actual_pair_pnl']}|\\n\"\n"
        "display(Markdown(table))"),
    nbformat.v4.new_code_cell("from statistics import mean\n"
        "amounts={k:sum((D(e['money_phases'][k]) for e in eps),D(0)) for k in eps[0]['money_phases']}\n"
        "gap=sum((D(e['benchmarks']['trigger_mark'])-D(e['benchmarks']['actual_pair_pnl']) for e in eps),D(0))\n"
        "assert abs(sum(amounts.values(),D(0))-gap)<D('1e-12')\nassert gap==D('4.879675065')\n"
        "assert len([g for g in gaps if 'narrow_proof_monotonic' in g])==22\n"
        "assert sum((D(g.get('fair_mark_drift_during_remaining_wait_usdc','0')) for g in gaps),D(0))==D('1.35')\n"
        "print('Monetary bridge:',amounts,'Total benchmark difference:',gap)\n"
        "print('Detection to first MATCHED mean:',mean(e['first_match']-e['detected'] for e in eps))\n"
        "print('Narrow evidence lead mean:',mean(g['narrow_zero_fill_proof_available_sec_before_actual_post'] for g in gaps if 'narrow_proof_monotonic' in g))"),
    nbformat.v4.new_code_cell("import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nimport numpy as np\n"
        "keys=list(eps[0]['time_phases']); names=['Path backlog','Detected to first POST','POST network','Close / replacement','Resting / observation']\n"
        "fig,ax=plt.subplots(figsize=(11,8.5),layout='constrained'); left=np.zeros(len(eps)); y=np.arange(len(eps))\n"
        "for key,label in zip(keys,names):\n"
        "    values=np.array([e['time_phases'][key] for e in eps]);ax.barh(y,values,left=left,label=label);left+=values\n"
        "ax.set_yticks(y,[e['market']+' / '+e['opening_window'].split(':')[-1] for e in eps]);ax.invert_yaxis()\n"
        "ax.set(xlabel='Seconds from historical path trigger to first locally observed MATCHED',title='22 SL episodes: exclusive execution-time components')\n"
        "ax.legend(loc='lower right',fontsize=9);ax.grid(axis='x',alpha=.15)\n"
        "fig.savefig(OUT/'sl-time-components.png',dpi=150);plt.close(fig);display(Image(filename=str(OUT/'sl-time-components.png')))"),
    nbformat.v4.new_code_cell("fig,ax=plt.subplots(figsize=(10,8.5),layout='constrained');y=np.arange(len(eps))\n"
        "fields=['trigger_mark','first_post_quote_pair_target','actual_pair_pnl'];labels=['SL-trigger mid mark','First SL quote pair target','Actual paired PnL']\n"
        "for key,label,marker in zip(fields,labels,['o','s','x']):\n"
        "    ax.scatter([float(e['benchmarks'][key]) for e in eps],y,label=label,marker=marker,s=32)\n"
        "ax.set_yticks(y,[e['market']+' / '+e['opening_window'].split(':')[-1] for e in eps]);ax.invert_yaxis()\n"
        "ax.axvline(0,color='gray',linewidth=.7);ax.set(xlabel='USDC; same actual matched quantity per episode',title='Price benchmarks versus actual paired PnL (quotes are not assumed fills)')\n"
        "ax.legend(loc='lower left',fontsize=9);ax.grid(axis='x',alpha=.15)\n"
        "fig.savefig(OUT/'sl-pnl-benchmarks.png',dpi=150);plt.close(fig);display(Image(filename=str(OUT/'sl-pnl-benchmarks.png')))"),
    nbformat.v4.new_markdown_cell("## 解释\n\n25次撤换中23个最终帧的公开页面最后完成项是positions/capital。"
        "22次零成交旧单的窄证据实际比下一POST平均早1.794秒。该段中点漂移净恶化1.35 USDC，不能假设提前Maker必成交。"
        "信号触发价到成交4.879675 USDC也是基准差额，不是可回收收益预测。"
        "例外包括等待期间改善的12:35及15:15，和15:05撤单请求后才观察到成交。\n\n"
        "优先验证零成交撤换证据通路与重复DELETE水位；保持预算/Delta/未知成交保护。"
        "状态机参数和生产代码均未改动。完整时点、缓存read ID和源码位置见README.md及summary.json。")]
os.environ["JUPYTER_PATH"] = "/home/ubuntu/poly-maker-data/research_outputs/REPAIR_ANALYSIS_20261019/jupyter"
NotebookClient(nb, timeout=120, kernel_name="repair-analysis", resources={"metadata": {"path": str(OUT)}}).execute()
nbformat.validate(nb)
assert not any(o.output_type == "error" for c in nb.cells if c.cell_type == "code" for o in c.outputs)
nbformat.write(nb, OUT / "audit.ipynb")
(OUT / "validation.json").write_text(json.dumps({"fixed_base_markets": 24, "sl_episodes": 22, "sl_posts": 47,
    "replacement_gaps": 25, "final_price_checks": "47/47", "raw_path_post_mid_checks": "47/47",
    "unmapped_path_batches": 0, "time_and_money_bridges": "PASS", "narrow_evidence_cases": 22,
    "executed_notebook": "PASS", "production_changes": False, "browser_qa": "NOT_AVAILABLE"}, indent=2))
print("SL diagnostic notes, executed notebook and reviewed chart payload ready.")
