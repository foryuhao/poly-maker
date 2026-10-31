"""Generate an evidence-linked research note from saved, reproducible inputs."""

import collections
import datetime as dt
import json
import statistics
import sys
from pathlib import Path


def generate(root):
    root = Path(root)
    meta = {m["asset"]: m for m in json.loads((root / "markets.json").read_text())}
    events = [json.loads(line) for line in (root / "events.jsonl").open()]
    mature = [e for e in events if "impact_120" in e]
    quotes = [json.loads(line) for line in (root / "quotes.jsonl").open()]
    quality = json.loads((root / "quality.json").read_text())
    collection = json.loads((root / "collection.json").read_text())
    audit = json.loads((root / "replay_audit.json").read_text())
    discovery = json.loads((root / "discovery_summary.json").read_text())
    tz = dt.timezone(dt.timedelta(hours=8))

    def stamp(t):
        return dt.datetime.fromtimestamp(t, tz).isoformat(timespec="seconds")

    lines = [
        "# 跨市场盘口微结构研究：首轮观察",
        "",
        f"采集窗口（北京时间）：{stamp(min(q['received'] for q in quotes))} 至 {stamp(max(q['received'] for q in quotes))}。",
        "",
        "## 可得出的结论",
        "",
        "本轮寻找的是成交后的持续价格冲击与暂时冲击回归。它们分别与信息型、流动性型订单流假说相容，但不能据此识别交易者身份、私有信息或证明可盈利。",
        "",
        f"发现 {discovery['unique_markets']} 个去重市场，其中 {discovery['eligible']} 个通过筛选；观察 {discovery['selected']} 个市场、{len(meta)} 个结果代币。该发现集合由成交额前 300 和最新 ID 前 300 组成，不代表全站普查。",
        f"收到成交消息 {quality.get('raw_trade_messages', 0)} 条，签名去重 {quality.get('duplicate_signature', 0)} 条，接受 {len(events)} 条；其中 {len(mature)} 条具有 120 秒后观测。相同市场两个结果代币并不独立。",
        "",
        "## 各市场观测",
        "",
        "每行合并同一市场两个结果代币。方向归一化冲击 = 成交方向符号 ×（后续中间价 − 成交消息到达时盘口中间价）。正值表示沿主动成交方向继续移动。单位为每份合约美元；0.01 为 1 美分。",
        "",
        "| 市场 | 合格成交 / 120秒成熟 | 5秒平均冲击 | 120秒平均冲击 | 持续 / 回归路径 |",
        "|---|---:|---:|---:|---:|",
    ]
    by_market = collections.defaultdict(list)
    for e in events:
        by_market[meta[e["asset"]]["market"]].append(e)
    market_rows = []
    for mid, es in by_market.items():
        m = meta[es[0]["asset"]]
        ms = [e for e in es if "impact_120" in e]

        def mean(h, es=es):
            vals = [e[f"impact_{h}"] for e in es if f"impact_{h}" in e]
            return f"{statistics.mean(vals):+.5f}" if vals else "未成熟"

        persistent = [
            e
            for e in ms
            if e.get("impact_5", 0) >= float(meta[e["asset"]]["tick"]) * 0.5
            and e["impact_120"] >= float(meta[e["asset"]]["tick"])
        ]
        reverting = [
            e
            for e in ms
            if e.get("impact_5", 0) >= float(meta[e["asset"]]["tick"]) * 0.5
            and e["impact_120"] <= 0
        ]
        market_rows.append(
            dict(
                market=mid,
                question=m["question"],
                slug=m["slug"],
                events=len(es),
                mature=len(ms),
                persistent=len(persistent),
                reverting=len(reverting),
            )
        )
        lines.append(
            f"| [{m['question']}](https://clob.polymarket.com/markets/{mid}) | {len(es)} / {len(ms)} | {mean(5)} | {mean(120)} | {len(persistent)} / {len(reverting)} |"
        )
    lines += [
        "",
        "持续路径：5 秒冲击 ≥ 半个 tick，120 秒冲击 ≥ 一个 tick；回归路径：5 秒冲击 ≥ 半个 tick，120 秒冲击 ≤ 0。阈值用于描述已发生的路径，不是可实时使用的预测信号。多个成交可能属于同一次冲击，表中数量不能当独立样本数。",
        "",
        "## 逐笔证据",
        "",
    ]
    # At most one event per market/type; retain candidate examples, not estimates of an edge.
    cases = []
    for label, pred in [
        ("持续冲击候选", lambda e, t: e.get("impact_5", 0) >= t * 0.5 and e["impact_120"] >= t),
        ("暂时冲击回归候选", lambda e, t: e.get("impact_5", 0) >= t * 0.5 and e["impact_120"] <= 0),
    ]:
        chosen = set()
        for e in sorted(
            mature, key=lambda e: abs(e["impact_120"] - e.get("impact_5", 0)), reverse=True
        ):
            m = meta[e["asset"]]
            if m["market"] in chosen or not pred(e, float(m["tick"])):
                continue
            chosen.add(m["market"])
            cases.append(dict(label=label, **e))
            lines += [
                f"### {label}：{m['question']} / {e['outcome']}",
                "",
                f"北京时间 {stamp(e['received'])}，{e['side']} {e['size']:.4f} 份 @ {e['price']:.4f}。消息到达时 bid/ask = {e['pre_bid']:.4f}/{e['pre_ask']:.4f}，近端 bid/ask 深度 = {e['pre_bid_depth']:.2f}/{e['pre_ask_depth']:.2f} 份（最优价外 0.02 范围）。",
                "",
                f"方向归一化冲击：5 秒 {e.get('impact_5', float('nan')):+.4f}，30 秒 {e.get('impact_30', float('nan')):+.4f}，120 秒 {e['impact_120']:+.4f}。被交易侧 120 秒后近端深度 / 初始深度 = {e['depth_ratio_120']:.2f}。",
                "",
                f"假设在消息到达时立即反向吃单，120 秒后吃单平仓，毛价格收益为 {e['fade_gross_120']:+.4f}/份。此值未扣费且假设瞬时执行，不能作为实盘收益。",
                "",
                f"证据：`trades.jsonl` 第 {e['raw_trade_line']} 行；asset `{e['asset']}`；connection generation {e['generation']}。完整盘口在 `quotes.jsonl`，原始消息在 `raw.jsonl`。",
                "",
            ]
            if len(chosen) >= 3:
                break
        if not chosen:
            lines += [f"本轮没有满足预设阈值的{label}；不补造案例。", ""]
    lines += [
        "## 数据质量与解释边界",
        "",
        f"- WebSocket 连接世代：{collection['generations']}；断线次数：{collection['counts'].get('disconnects', 0)}。跨断线的前后观测不匹配。",
        f"- 独立重放比较最优价 {audit['counts'].get('bbo_checks', 0)} 次，其中不一致 {audit['counts'].get('bbo_mismatches', 0)} 次，形成 {len(audit['bad_intervals'])} 段不可靠时段。剔除不可靠时段前后各 1 秒内的成交基准和前视端点；基准剔除 {quality.get('bbo_audit_baseline_rejected', 0)} 条。保留原始数据，不用报价修补掩盖差异。",
        "- 成交消息仅允许源时间与本地接收时间相差 −0.25 至 2 秒；签名重复剔除。同时间、同价、同量的真实多笔交易可能被保守合并。",
        "- 所谓初始盘口是成交消息到达之前已经应用的最新盘口；交易所可能先推送盘口变化，所以它可能已经反映该成交，不能称为严格的执行前盘口。",
        "- 盘口每秒采样；前视目标使用目标时刻之后首个样本，最多晚 2 秒。中间价冲击并非可成交收益；深度恢复不证明同一位挂单者补单。",
        "- 近端深度随最优价移动重新计算，比值同时混合价格迁移与订单增减，不能解释为纯补单率。",
        "- 只观察有限时间、有限市场，且候选主要偏向高成交量；分类类别由关键词生成。当前没有外部新闻、赛事进度或交易者级证据，无法排除公开信息解释。",
        "- 5/30/120 秒样本重叠、二元两侧相关；不提供把每笔交易当独立观测的显著性或胜率。未证明任何可交易优势。",
        "",
        "## 下一阶段验证标准",
        "",
        "固定市场集合和采集时段，跨多个交易日观察；将同市场相隔至少 120 秒的事件作为初步独立窗口，并进一步按事件/市场聚类。训练段只使用当时可见的深度变化、成交方向与金额、价差、盘口恢复速度；以之后的路径作为标签，按时间切分验证，禁止用未来价格定义当时信号。",
        "",
        "策略候选必须在后续独立样本中同时通过：相对无条件订单流基线的区分能力、费用后可执行收益、延迟和深度容量压力测试、不同市场/日期的稳定性。持续价格变化仍只支持信息冲击假说；要辨别公开信息和私有信息，需另行对齐可靠外部事件时间。",
        "",
        "## 复现与来源",
        "",
        "公共 Gamma markets、CLOB market WebSocket；仅观察，无订单发送、无账户凭证。源文件哈希见 `manifest.json`；筛选结果见 `discovery_summary.json`；逐笔计算见 `events.jsonl`。",
        "",
        "- https://docs.polymarket.com/market-data/realtime-data",
        "- https://gamma-api.polymarket.com/markets",
        "- `research/microstructure_discovery/study.py` 与 `report.py`",
        "",
    ]
    (root / "REPORT.md").write_text("\n".join(lines))
    (root / "cases.json").write_text(json.dumps(cases, indent=2))
    (root / "market_summary.json").write_text(json.dumps(market_rows, indent=2))
    print(root / "REPORT.md")


if __name__ == "__main__":
    generate(sys.argv[1])
