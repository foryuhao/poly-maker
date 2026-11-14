"""Render the engineering handoff only from completed cohort replay evidence."""

import argparse
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

GROUPS = tuple(f"M{i}" for i in range(5, 11))


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def amount(value):
    return f"{Decimal(value):+.4f}"


def selection_for_run(root, new_name, plan, source):
    """Do not attach an earlier calibration's parameters to a later replay."""
    combined = read(root / "combined-selection-v6.json", {})
    parameter_sha = plan.get("frequency_parameters_input_sha256")
    matches = (bool(source) and bool(parameter_sha)
               and combined.get("source_root") == source
               and combined.get("parameters_sha256") == parameter_sha)
    if matches:
        return combined, "combined-selection-v6.json", True
    if new_name == "selected25-v4":
        return read(root / "final-parameter-selection.json", {}), "final-parameter-selection.json", False
    return {}, None, False


def render(root, comparison_file, new_name):
    comparison = read(root / comparison_file)
    if comparison is None or comparison["status"] != "COMPLETE":
        raise ValueError("COMPLETE_25_MARKET_COMPARISON_REQUIRED")
    legacy_memory = read(root / "memory-review.json", {})
    current_memory = read(root / "memory288-release-phase3-review.json", {})
    memory = current_memory or legacy_memory
    native_run = "native288-"+new_name.replace("25-", "-")
    native = read(root / native_run / "result.json", {})
    final_memory_name = "memory288-"+new_name.replace("25-", "-")
    final_memory = read(root / (final_memory_name+"-review.json"), {})
    same_configuration = read(root / ("invariance-"+new_name+".json"), {})
    selected_plan = read(root / new_name / "plan.json", {})
    selected_source = read(root / new_name / "import-proof.json", {}).get("expected_source_root")
    release_native = read(root / "native288-release-v3b/result.json", {})
    old_native = read(root / "native288-gc-v1/result.json", {})
    ledger = read(root / f"ledger-validation-{new_name}.json", {})
    grid = read(root / "frequency-grid-review.json", {})
    quality = read(root / "cohort-quality.json", {})
    data_scope_decision = read(root / "data-scope-decision.json", {})
    attrition_name = (new_name if (root / f"frequency-diagnostics-{new_name}.json").exists()
                      else "next25-v3b")
    attrition = read(root / f"frequency-diagnostics-{attrition_name}.json", {})
    opportunities = read(root / "m7-causal-opportunity-review.json", {})
    probe = read(root / "memory-release-real1-v3/comparison-to-source.json", {})
    selected, selection_file, selection_matches_run = selection_for_run(
        root, new_name, selected_plan, selected_source)
    selected_review = read(root / f"{new_name}-signature-review.json", {})
    selected_completed = (selected_review.get("status") == "PASS_MATCHED_CALIBRATION"
                          and selected_review.get("checked_markets") == 25
                          and (selection_matches_run or new_name == "selected25-v4"))
    selected_state = ("该组合版本已完成25场，候选逐场与独立校准结果一致。" if selected_completed else
                      "该组合版本的完整回放和候选核对仍未完成。")
    pending_probe = read(root / "pending12-selected-v4/result.json", {})
    runs = comparison["runs"]
    old, new = runs["oldsignals-through25"]["results"], runs[new_name]["results"]
    target_met = all(new[f"{s}/research/trade-through"]["signals"] >= 10 for s in ("M7", "M9", "M10"))
    clean = quality.get("cohort25_status_counts", {}).get("HAS_AMBIGUOUS_INTERVALS", 25) == 0
    interval_scope_accepted = data_scope_decision.get("interval_qualified_acceptance") is True
    source_scope_accepted = clean or interval_scope_accepted
    strategy_pass = target_met and source_scope_accepted and selected_completed
    legacy_memory_pass = (memory.get("status") == "PASS"
                          and memory.get("retirement_mode") == "GC_FREE_DURABLE_RETIREMENT")
    final_memory_pass = (selection_matches_run and final_memory.get("status") == "PASS"
                         and final_memory.get("configuration_from_run") == new_name
                         and final_memory.get("execution") == "trade-through"
                         and final_memory.get("checked_markets") == 288
                         and final_memory.get("source_root") == selected_source
                         and final_memory.get("frequency_parameters_input_sha256")
                         == selected_plan.get("frequency_parameters_input_sha256"))
    invariance_pass = (same_configuration.get("status") == "PASS"
                       and same_configuration.get("optimized_run") == new_name
                       and same_configuration.get("checked_markets") == 25
                       and same_configuration.get("implementation_and_frequency_identical") is True)
    native_pass = (selection_matches_run and native.get("status") == "PASS"
                    and native.get("source_root") == selected_source
                    and native.get("frequency_parameters_input_sha256")
                    == selected_plan.get("frequency_parameters_input_sha256"))
    memory_pass = legacy_memory_pass and final_memory_pass and invariance_pass and native_pass
    fill_pass = ledger.get("status") == "PASS"
    memory_failed = any(v.get("status") == "FAIL" for v in (
        memory, native, final_memory, same_configuration))
    cohort = read(root / "cohort25.json")["markets"]
    start = int(cohort[0]["market"].rsplit("-", 1)[1])
    end = int(cohort[-1]["market"].rsplit("-", 1)[1])+300
    local = ZoneInfo("Asia/Shanghai")
    lines = ["# M5–M10 下一版实现与离线验证", "",
        "生成时间（北京时间）："+datetime.now(local).isoformat(), "",
        f"市场窗口（北京时间）：{datetime.fromtimestamp(start, local).isoformat()} 至 {datetime.fromtimestamp(end, local).isoformat()}。", "",
        "本报告按审计证据核对实现与实验结果。上一轮真实运行的失败事实保留；本次没有启动长期测试，没有恢复巡检，没有操作真实订单。", "",
        f"本次固定 25 场的三个离线身份为 baseline25（旧信号/旧队列）、oldsignals-through25（旧信号/穿价）和 {new_name}（候选参数/穿价）。它们共享场次，不合并为一次连续运行。参数探索尚未结束时，本文件仅为阶段草稿。", "",
        f"本表回放身份：{new_name}；冻结源码：{selected_source or '未核实'}；启动参数SHA：{selected_plan.get('frequency_parameters_input_sha256') or '本历史版本未使用外部参数文件'}。选型证据：{selection_file or '尚无匹配本次源码与参数的选型记录'}；独立校准计数：{selected.get('expected_counts', selected.get('expected_confirmed_counts', '—'))}。{selected_state} 已核对 {selected_review.get('checked_markets', '—')} /25 场，签名状态 {selected_review.get('status', '未完成')}。", "",
        "只使用完整网格、事前冻结的优先级和信号数量选择参数，不按PnL选型。先保留已达标的公共买攻/支持种子配置，再考虑明确的局部时间分辨率试验；未达10个的最高计数组合仅供审阅。配置文件只允许覆盖M7/M9/M10，解析值写入DEFINITION，原文件SHA与冻结源码一起核对。", "",
        f"本次实际频次参数：{selected.get('frequency_parameters', selected.get('resolved_parameters', '尚未核实'))}。各阶段的原始试验、主动停止、中断及更正证据单独保留，不把部分运行拼接为通过；过程说明见开发文档。", "",
        "| 目标 | 状态 | 依据 |", "|---|---|---|",
        f"| 长期内存管理 | {'已完成离线验收' if memory_pass else '部分完成，验证失败需处理' if memory_failed else '部分完成，验证未收尾'} | 旧规则隔离 {memory.get('checked_markets', '—')} /288；最终配置 {final_memory.get('checked_markets', '—')} /288；同配置前后 {same_configuration.get('status', '未完成')}；原生协调器 {native.get('status', '未完成')} |",
        f"| 统一穿价成交与账本 | {'已完成离线验收' if fill_pass else '部分完成，账本一致性待验证'} | 已确认数量封顶、允许部分成交；同一批 SUBMIT 比较 |",
        f"| M7/M9/M10 频次 | {'已完成频次门槛' if strategy_pass else '部分完成，未满足全部验收条件'} | {'三策略各至少 10 个已确认信号' if target_met else '至少一策略未达到 10 个信号'}；{'全窗口质量条件已证实' if clean else '用户已接受可证明区间比较，保留未知' if interval_scope_accepted else '固定 25 场含不可证明区间，验收范围待确认'} |", "",
        "## 规则与口径", "",
        "调参基线是任务开始时已在运行的版本：M7 短歇攻与一轮新承接可重叠、M9 一轮新复验、M10 两轮回流；这些阶段数在本次任务前已经确定。本次没有进一步删减阶段，不能将其称为历史 SOP v1.1 的严格串行/多轮版本。语意与数值边界见 signal-semantics.md。", "",
        "若选定M7/M10局部AP窗口，则仅改变该策略初始买攻的观察时间分辨率；仍要求多笔、方向占比、分窗分布、最小量、完整覆盖及水位封口，且保留后续承接/歇攻/新攻击或两轮严格递减回流。公共AP与M5/M6/M8门槛不变。此试验不主张与原750ms信号集合经济等价；M7允许买速不低于AP1的早期歇攻试验不进入最终选型。", "",
        "模拟 LIVE 生效后，到冻结的 T240 截止前，同一 token 的直接 SELL 成交价格须 ≤ 实际提交价 − 提交时已核验 tick。符合条件的源时间必须处于此窗口；晚到记录保留源时间与可用时间。超出可信时间或覆盖不足单列未知。", "",
        "按记录成交量逐笔分配，最多抵扣订单剩余量，允许部分成交。同一策略账户按价格优先、LIVE 时间、提交顺序分配，不能重复使用成交量。不同策略是独立反事实账户，不能相加为一个投资组合。成交按原提交价记账。", "",
        "无证据且覆盖完整则在该模式下记未成交，T240 撤单。覆盖不足的未决订单可能已有已知部分成交。挂价 0.01 且冻结 tick 为 0.01 时触发价为 0，不会被正价格成交触发。假设成交使用同一批已提交 SUBMIT 的全额数量，包含随后到达时被拒的订单，不代表所有信号。", "",
        "所有 PnL 均为官方结算毛收益（USDC），不含未经核验的手续费或返佣；已知模拟成交 PnL 不是未决余量的确定最终结果。", "",
        "## 固定 25 场：旧信号与新信号在相同穿价模式下", "",
        "每行只计一个策略账户。已确认模拟成交按正数 MODELLED_FILL 的唯一订单计数。交易场数分别对应假设成交 / 已知模拟成交。", "",
        "| 策略/版本 | 信号 | 发单 | 假设成交 | 模拟成交 | 未决 | 交易场数 假设/模拟 | 假设 PnL | 已知模拟 PnL | 未决余量计入后的理论区间 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for s in GROUPS:
        for label, values in (("旧", old), ("新", new)):
            a = values[f"{s}/research/trade-through"]
            lines.append(f"| {s}/{label} | {a['signals']} | {a['submitted']} | {a['assumed_filled']} | {a['modelled_filled']} | {a['unresolved']} | {a['assumed_trading_market_count']} / {a['modelled_trading_market_count']} | {amount(a['assumed_pnl'])} | {amount(a['modelled_pnl'])} | [{amount(a['modelled_pnl_lower'])}, {amount(a['modelled_pnl_upper'])}] |")
    lines.append("")
    for s in GROUPS:
        a = new[f"{s}/research/trade-through"]
        if a["rule_unfillable_orders"]:
            lines.append(f"{s} 有 {a['rule_unfillable_orders']} 单触发价 ≤ 0，其中 {a['unresolved_rule_unfillable_orders']} 单仍保留覆盖质量未决标记。它们不被赋予正数模拟成交或未知成交收益。")
    lines += ["", "区间对未知剩余量作零至全量的端点界定，不是概率区间，也不代表可以忽略行情缺口。触发价 ≤ 0 的单按规则无法由正价格成交触发，未知区间不赋予其虚构的成交收益；覆盖质量的未决标记仍保留。", "",
        "## 新增候选对应的订单结果", "",
        comparison["incremental_signal_cohorts"]["definition"], "",
        "按同一物理起始攻击及承接/歇攻证据配对，忽略注册计数器和TTL的差异。下表订单列仅统计新版新增候选对应的已提交订单；共同候选即使发单时刻或价格改变，也不归入新增。未决与已知部分成交可以重叠。", "",
        "| 策略 | 新增/消失/共同候选 | 新增候选发单 | 已知成交 | 未决 | 假设 PnL | 已知模拟 PnL |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in ("M7", "M9", "M10"):
        v = comparison["incremental_signal_cohorts"]["results"][s]
        a = v["orders"].get("new/added", {})
        lines.append(f"| {s} | {v['added_candidates']} / {v['lost_candidates']} / {v['common_candidates']} | {a.get('submitted', 0)} | {a.get('modelled_filled', 0)} | {a.get('unresolved', 0)} | {amount(a.get('assumed_pnl', 0))} | {amount(a.get('known_modelled_pnl', 0))} |")
    lines += ["", "这些是同一批样本内调参后的归因结果，不能视为独立样本的盈利验证，也不用于回头改变已固定的数量优先选型规则。", "",
        "## 同一旧信号：更换成交规则的影响", "",
        "T/C 为历史 trades-only / cancel-half；下一版只运行穿价模式。历史模式仅用于本次离线对照。PnL 列都是已知成交的毛结算收益，未决余量不按确定未成交处理。", "",
        "| 策略 | 旧发单 T/C | 穿价发单 | 旧模拟成交 T/C | 穿价模拟成交 | 旧未决 T/C | 穿价未决 | 旧已知 PnL T/C | 穿价已知 PnL |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|"]
    for s in GROUPS:
        t = runs["baseline25"]["results"][s+"/research/trades-only"]
        c = runs["baseline25"]["results"][s+"/research/cancel-half"]
        a = old[s+"/research/trade-through"]
        lines.append(f"| {s} | {t['submitted']} / {c['submitted']} | {a['submitted']} | {t['modelled_filled']} / {c['modelled_filled']} | {a['modelled_filled']} | {t['unresolved']} / {c['unresolved']} | {a['unresolved']} | {amount(t['modelled_pnl'])} / {amount(c['modelled_pnl'])} | {amount(a['modelled_pnl'])} |")
    lines += ["", "提交群体逐笔一致性："+str(comparison["submission_cohort_equivalence_oldqueue_to_through"]["strategies"])+"。", "",
        "## 新版收益对尾部盈利的依赖", "",
        "分别移除最高盈利 1 场和 3 场的全部净贡献，保留所有亏损场；盈利场不足时仅移除现有正贡献。0.01–0.02 列包含两端价格的已提交订单贡献。", "",
        "| 策略/账本 | 全样本 PnL | 剔除最高盈利 1 场 | 剔除最高盈利 3 场 | 0.01–0.02 贡献 |",
        "|---|---:|---:|---:|---:|",
    ]
    for s in GROUPS:
        a = new[f"{s}/research/trade-through"]
        for mode, label in (("assumed", "假设"), ("modelled", "已知模拟")):
            lines.append(f"| {s}/{label} | {amount(a[mode+'_pnl'])} | {amount(a[mode+'_without_best_1'])} | {amount(a[mode+'_without_best_3'])} | {amount(a[mode+'_low_price_pnl'])} |")
    lines += ["", "## 工程一致性与资源", "",
        f"内存优化在同一输入、同一旧信号和撮合规则下单独验证。公共行情已核对 {memory.get('checked_markets', '—')} 场；峰值 RSS {memory.get('peak_rss_mib', '—')} MiB，最后 RSS {memory.get('last_rss_mib', '—')} MiB，退休后保留研究对象图最大数 {memory.get('maximum_retained_research_graphs_after_retirement', '—')}。逐项检查以 memory288-release-phase3-review.json 为准。", "",
        "候选、订单、成交、结算及经济审计作直接相等核对。完整审计另外核对：版本DEFINITION和候选run_id单列，新增的旧轮次0.5秒/保持0.1秒/恢复50%注释必须先验证数值再归一化；同一输入失效的FACT_ENDED按完整身份、时间和原因作多重集合比较，其余记录仍逐项保序比较。源实现遍历集合可能改变兄弟失效记录的写入顺序，因此不声称审计文件逐字节相同。详见m510_audit_equivalence.py及memory288-release-phase3-review.json。", "",
        "原生协调器验证与真实公共录制回放分开记录。原生验证使用人工加速输入，公共录制压力样本来自两个独立运行，均不能称为连续 24 小时实盘或实时延迟通过。原始 512 MiB 限制与队列上限未提高。未决订单和待结算状态保留在已封口审计中。", "",
        f"研究对象图释放后，协调器仍保留轻量市场身份、覆盖摘要和审计路径，以生成最终报告；旧规则已核对末场的身份数为 {memory.get('last_retained_engine_identity_count', '—')}。这些身份随本次配置的最大市场数增长；288场验证证明的是该有限运行范围内的资源表现，不等于无限期运行的严格常量内存保证。", "",
        f"memory288-v3b 的冻结版本由回放器显式回收循环引用，已核对 {legacy_memory.get('checked_markets', '—')} /288场；该证据不冒充当前主动解除引用版本。主动释放版本的真实录制验证为 memory288-release-phase3，状态 {current_memory.get('status', '尚未生成验收证据')}。后续 native288-gc-v1 虽完成 288 场人工生命周期验证，却测得最大强制全局回收耗时 {old_native.get('maximum_retirement_gc_seconds', '—')} 秒，因此没有将它作为最终内存实现。", "",
        f"frozen-release-v3 在市场持久化、备份、关闭成功后，主动解除该市场的 Registry、策略与观察器回调引用，包括已脱离活跃列表的观察器；不强制扫描全局堆。样本检查和备份目的库连接明确关闭。GC 关闭的构造测试发现 v2 最初创建的共享观察器未被追踪，修正初始化顺序后68项定向检查通过；v2 主动停止并保留 superseded.json。v3 又在256场后进程被终止，退出码143且没有最终结果，原因未证实；保留 interruption.json，不计为288场通过。随后从头的 native288-release-v3b 完整结果为 {release_native.get('status', '未完成')}，峰值 RSS {release_native.get('peak_rss_mib', '—')} MiB，最大主动释放墙钟 {release_native.get('maximum_retirement_release_seconds', '—')} 秒。各次中断不拼接为通过。", "",
        f"本表经济版本 {new_name} 对应的独立原生协调器验证为 {native_run}，状态 {native.get('status', '未完成')}，峰值 RSS {native.get('peak_rss_mib', '—')} MiB。真实录制资源回放、人工输入和各冻结阶段分开记录，不能冒称来自同一个二进制或一次实时长跑。", "",
        f"最终配置还需独立真实行情288场：{final_memory_name}，状态 {final_memory.get('status', '未完成')}，已核对 {final_memory.get('checked_markets', '—')} /288场；同一源码、参数和穿价规则的25场释放前后核对状态 {same_configuration.get('status', '未完成')}。这两个范围明确列入final-validation-scope.json，均未完成时不将旧规则或人工输入证据替代最终配置验收。", "",
        f"另以生产已有待结算上限12进行16场人工延期结算测试，状态 {pending_probe.get('status', '未完成')}：保留 {pending_probe.get('pending_states_preserved', '—')} 个待结算和 {pending_probe.get('deferred_states_preserved', '—')} 个已落盘延后结算状态。上限12没有调大。资源采样统计未关闭的市场Store及活跃录制线程，不包含瞬时备份/只读报告连接；后者另以生命周期测试验证关闭。这16场人工输入没有提供已结算结果，不能将资源测试说成完成官方结算。", "",
        "内存计量还需区分启动前历史高水位：真实行情资源探针在首次处理行情前就读到 ru_maxrss=391.37890625 MiB，但当时 /proc VmHWM=78.25 MiB。独立的分配、释放、exec 对照复现了 ru_maxrss 跨 exec 保留、VmHWM 随新程序重置的差异；不能将前者直接解释成本次行情已驻留391 MiB。详见 resource-counter-note.json 和逐输入采样。现有512 MiB守卫没有改动或放宽。", "",
        f"当前 v3 的一场完整真实行情资源探针：{probe.get('input_seq', '—')} 条输入，{probe.get('samples', '—')} 个场内水位；候选/订单/成交/结算审计核对 {probe.get('status', '未完成')}，本次程序最高 VmHWM {probe.get('maximum_runtime_vmhwm_mib', '—')} MiB。主动释放耗时 CPU {probe.get('retirement_cpu_seconds', '—')} 秒、墙钟 {probe.get('retirement_wall_seconds', '—')} 秒。墙钟包含进程调度等待；这一场不是288场验收，也不是实时延迟验证。", "",
        f"M5/M6/M8 候选核心语意一致：{comparison['unchanged_M5_M6_M8_candidate_semantics']}；同一穿价模式下订单/账户一致：{comparison['unchanged_M5_M6_M8_execution']['strategies']}。", "",
        "## 样本与剩余限制", "",
        f"固定 25 场中有源顺序或 BBO/深度歧义的市场：{quality.get('cohort25_status_counts', {}).get('HAS_AMBIGUOUS_INTERVALS', '—')} 场。完整录制可确定性回放，不等于全窗口盘口/成交均可可靠重建。信号计数只覆盖可证明区间；歧义区间不能解释为没有信号。未按收益更换样本，也未绕过质量检查。", "",
        ("用户已明确接受按各场可证明区间比较并保留未知（data-scope-decision.json）。这确认了比较范围，没有将全窗口重建标为已证实，也没有放松质量门槛。" if interval_scope_accepted else "尚无用户接受区间比较的确认；全窗口重建条件仍未证实。"), "",
        "首场纯观测插桩与原回放的 broker、覆盖、marks 和结算一致；主要源顺序拒绝发生在盘口增量与成交之间。该场 69 个 BBO 异常输入每个 token 只有一条变化，不能由同批多条更新的中间/最终状态混淆解释。该取证仅覆盖首场。", "",
        "已核对[官方事件结构](https://docs.polymarket.com/market-data/realtime-data)及[官方 SDK 类型](https://github.com/Polymarket/ts-sdk/blob/main/packages/bindings/src/subscriptions/clob.ts)。结合本批录制，这些所示字段不足以证明跨盘口与成交记录的完整全序，这是本次取证的判断，并非声称平台所有接口都不存在序号。没有仅因为倒退幅度很小就放宽时序检查。", "",
        f"参数网格完成状态：{grid.get('status', '未完成')}。参数选择只使用预先固定的优先顺序、信号数与证据完整性，不使用 PnL。25 场用于样本内调参，新增信号的盈亏不能当作样本外有效性证明。", "",
        f"{attrition_name} 的阶段审计，已核对 {attrition.get('counted_markets', '—')} /25场（形成尝试不是信号数）：", "",
        "| 策略 | 形成尝试 | 已发布候选 | 候选事实确认前结束 | 确认后结束 | UNKNOWN 结束 | 其他结束 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *[f"| {s} | {a['starts']} | {a['published_candidates']} | {a.get('ended_before_candidate_fact', '—')} | {a.get('ended_after_candidate_fact', '—')} | {a['end_reason_classes'].get('UNKNOWN', 0)} | {a['ended']-a['end_reason_classes'].get('UNKNOWN', 0)} |"
          for s, a in attrition.get('strategies', {}).items()], "",
        "UNKNOWN 是已提交审计中的结束分类，包含候选确认后的结束，不能改写为阈值未通过或漏掉的信号；这些次数也不表示若补齐行情就一定产生候选。候选事实确认本身也不保证发布或发单。其余结束含行为失效、数值筛选及候选确认后的自然结束，不能全部归因于参数严格。", "",
        f"M7另有必要机会诊断：状态 {opportunities.get('status', '未完成')}，已核对 {opportunities.get('counted_markets', '—')} /25场，当前成员内的宽松AP2组合计数为 {opportunities.get('observed_necessary_opportunity_upper_bound', '—')}。它仅检查同token/代次、买攻因果先后和明确公共质量失效，忽略RF、歇攻、锚点、寿命及并发约束，不能计作信号或可交易机会。每个已发布M7必须被此必要条件覆盖，并核对独立控制的交织公共证据顺序。现有局部结果不支持‘原始买攻机会不够，所以局部参数必然无法达标’的结论；仍须分析具体形成失败。", "",
        "各冻结版本的测试明细分别保留，最终回放须核对实际模块导入路径、源码清单和参数SHA；不以旧版通过代替新版验收。局部AP版本的定向回归证据为tests-local-ap-final-regression.xml。真实时间长跑另行安排：持续网络缺口、长时间调度与磁盘写入、公开结算接口延迟和实际手续费未由加速回放证明。M11 与 M6 ≥0.20 专项继续暂缓。", "",
        "## 证据", "",
    ]
    for name in (comparison_file, "cohort25.json", "cohort288.json", "cohort288-final.json", "cohort-quality.json",
                 "frequency-selection-rule.json", "frequency-grid-review.json", "memory-review.json",
                 "memory288-release-phase3-review.json", "audit-comparison-scope.json",
                 "final-validation-scope.json", final_memory_name+"-review.json",
                 "invariance-"+new_name+".json", "m10-parameter-coverage-review.json",
                 "control25-config-v5-economic-check.json",
                 "data-scope-decision.json", "memory-validation-priority.json",
                 "phase4-failed-root-rule.json", "phase4-freeze.json", "phase4-grid-review.json",
                 "combined-selection-rule-v6.json", "combined-selection-v6.json",
                 "combined-parameters-v6.json", f"{new_name}-signature-review.json",
                 "phase5-window-rule.json", "phase5-grid-review.json", "phase5-freeze.json",
                 "phase6-local-ap-rule.json", "phase6-grid-review.json", "phase6-freeze.json",
                 "local-ap-design-v7.json", "m7-pause-rate-screen.json",
                 "tests-local-ap-final-regression.xml", "tests-report-provenance.xml",
                 "tests-failed-root-regression.xml", "tests-final-selection.xml",
                 "tests-audit-equivalence.xml",
                 "frequency-selection-rule-expanded.json", "protocol-evidence-note.json",
                 "signal-semantics.md", "frequency-diagnostics-next25-v3b.json",
                 f"frequency-diagnostics-{attrition_name}.json", "m7-causal-opportunity-plan.json",
                 "m7-causal-opportunity-review.json", "tests-m7-opportunity-bound.xml",
                 "resource-counter-note.json", "rusage-exec-diagnostic.jsonl",
                 "memory-release-real1-v3/comparison-to-source.json",
                 "final-economic-selection-rule.json",
                 "forward-preflight-release-v3.json", "next-run-preparation.md",
                 "forward-preflight-selected-v7.json", "forward-preflight-selected-v7-command.json",
                 "code-review-scope-v7.json", "changes-v7-from-available-baselines.diff",
                 "final-v7-first-committed-review.json", "final-v7-validation-launch.json",
                 "code-review-scope.json", "changes-from-available-baselines.diff",
                 "requirements-audit.json", "tests-through-report.xml",
                 "pending12-selected-v4/result.json", "phase3-frequency-rule.json",
                 "phase3-implementation-design.md",
                 "phase3-freeze.json", "phase3-grid-review.json", "tests-phase3-regression.xml",
                 "phase3-isolation-fix.json", "tests-phase3-v2-final.xml",
                 "isolation-scope-correction.json", "tests-frequency-scope-regression.xml",
                 "frequency-grid-phase3/superseded.json", "phase3-v2-freeze.json",
                 "frequency-configuration-freeze.json", "tests-frequency-configuration-regression.xml",
                 "frequency-configuration-code-review-scope.json", "frequency-configuration-changes.diff",
                 "forward-preflight-frequency-configuration.json", "frequency-configuration-old-source-guard.json",
                 "control25-config-v5-review.json", "frequency-control-v5-plan.json",
                 "source-diagnostic-v1/diagnosis.json", "bbo-batch-diagnostic.json",
                 "native288-gc-v1/result.json", "native288-release-v2/superseded.json",
                 "native288-release-v3/interruption.json", "native288-release-v3b/result.json",
                 "native288-selected-v4/result.json", "selected-v4b-freeze.json",
                 "final-parameter-selection.json", "final-expected-signatures.json",
                 "selected25-v4/import-proof.json", "selected25-v4-signature-review.json",
                 "tests-frozen-import-guards.json", "forward-preflight-selected-v4.json",
                 "tests-selected-v4.xml",
                 "tests-release-v3.xml", "frequency-grid-v1/committed-cohort-review.json",
                 f"ledger-validation-{new_name}.json", "tests-acceptance.xml", "tests-grid.log",
                 "tests-reference-release-and-db.xml", "tests-native-reference-release.xml",
                 "tests-release-persistence-correct-output.xml"):
        lines.append(f"- [{name}]({root / name})" if (root / name).exists() else f"- {name}（尚未生成）")
    filename = "report.md" if memory_pass and fill_pass and strategy_pass else "report-draft.md"
    (root / filename).write_text("\n".join(lines)+"\n")
    return {"file": str(root / filename), "memory_verified": memory_pass, "fill_verified": fill_pass,
            "frequency_target_met": target_met, "whole_window_reconstruction_verified": clean,
            "legacy_memory_isolation_verified": legacy_memory_pass,
            "final_configuration_resource_verified": final_memory_pass,
            "same_configuration_invariance_verified": invariance_pass,
            "selection_matches_run_source_and_parameters": selection_matches_run,
            "interval_qualified_scope_accepted": interval_scope_accepted,
            "source_scope_accepted": source_scope_accepted}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--new-run", default="next25-v3b")
    parser.add_argument("--comparison", default="comparison.json")
    args = parser.parse_args()
    print(json.dumps(render(args.directory.resolve(), args.comparison, args.new_run), indent=2))
