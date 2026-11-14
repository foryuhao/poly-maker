"""Read-only, chronological M1 virtual acceptance report; never loads a wallet."""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))

from polymaker.m1_virtual_state import VirtualStore  # noqa: E402


def stamp(wall):
    return datetime.fromtimestamp(float(wall), ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M:%S.%f")[:-3]


def money(value):
    return "待确认" if value is None else f"{Decimal(value):+.4f}"


def render(report, *, start_epoch=0, limit=None):
    markets = [m for m in report["markets"] if m["epoch"] >= start_epoch]
    if limit:
        markets = markets[:limit]
    orders = [o for m in markets for o in m["instructions"]]
    roots = [o for o in orders if not json.loads(o["evidence"]).get("duplicate_of")]
    eligible = sum(bool(o["eligible"]) for o in roots)
    successful = [o for o in orders if o["status"] == "VIRTUAL_SUCCESS"]
    settled = [m for m in markets if m["pnl"] is not None]
    pnl = sum((Decimal(m["pnl"]) for m in settled), Decimal(0))
    lines = ["# M1 虚拟后台逐场验收报告", "", f"生成于 {stamp(__import__('time').time())}（北京时间）", "",
        "**真实 POST：0。以下“成功”均为虚拟指令已记录，假设按指令价买满合法股数并持有至官方结算；没有真实 Maker 成交证明。**", "",
        f"窗口 T15–T240；价格 0.01–0.19；单笔目标至少 1 USDC、单场最多 20 USDC；最多 5 个独立机会，异常请求允许原价再试一次。列出 {len(markets)} 场，"
        f"已官方结算 {len(settled)} 场；合规信号 {eligible} 个，虚拟成功 {len(successful)} 个。",
        f"已结算模拟 PnL：{money(pnl)} USDC；待结算场次不计零收益。",
        f"服务严格连续正常场：{report['continuous_normal_markets']}；连续完整正常报告："
        f"{report['consecutive_complete_reports']}/12；本阶段仍未结束，须用户确认。", "",
        "| # | 场次开始 | 完整窗口 | 输入／运行问题数 | 信号／合规／虚拟成功 | 官方胜方 | 模拟 PnL |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    for i, m in enumerate(markets, 1):
        rows = m["instructions"]
        signal_rows = [o for o in rows if not json.loads(o["evidence"]).get("duplicate_of")]
        lines.append(f"| {i} | {stamp(m['epoch'])[:14]} | {bool(m['full_window'])} | {m['issues']} | "
            f"{len(signal_rows)}/{sum(bool(o['eligible']) for o in signal_rows)}/"
            f"{sum(o['status']=='VIRTUAL_SUCCESS' for o in rows)} | {m['winner'] or '待确认'} | {money(m['pnl'])} |")
    lines += ["", "问题数包含按严格口径记录的超容差旧帧、源数据迟到、处理积压、断流及人工演练；"
              "它不等同于程序崩溃数。所有异常保留，不能据完整报告数量宣称已达到正常连续场次门槛。", ""]
    for i, m in enumerate(markets, 1):
        events = m["timeline"]
        lines += [f"## {i}. {stamp(m['epoch'])[:14]} — {m['slug']}", "",
                  f"观察状态 {m['state']}，完整窗口 {bool(m['full_window'])}，问题数 {m['issues']}。", ""]
        components = {}
        success_times = {}
        for event in events:
            data = event["data"]
            for key in ("rf_id", "ex_id"):
                if data.get(key) and event["kind"] in {"RF_CONFIRMED", "EX_CONFIRMED"}:
                    components[data[key]] = event["wall"]
            if event["kind"] == "VIRTUAL_SUCCESS":
                success_times[data["id"]] = event["wall"]
        if not m["instructions"]:
            lines.append("- 未识别到 RF＋EX 组合开仓机会；未生成买入指令。")
        for order in m["instructions"]:
            evidence = json.loads(order["evidence"])
            rf, ex = components.get(evidence.get("rf_id", order["rf"])), components.get(evidence.get("ex_id", order["ex"]))
            references = f"RF {stamp(rf) if rf else '见证据'}；EX {stamp(ex) if ex else '见证据'}"
            label = "同一机会的异常重试" if evidence.get("duplicate_of") else "信号"
            lines.append(f"- {stamp(order['wall'])}，T{order['elapsed']:.3f}，{label} {order['outcome']}；{references}。")
            if order["status"] == "VIRTUAL_SUCCESS":
                successful_at = success_times.get(order["id"])
                lines.append(f"  买入指令 **{order['outcome']} @ {order['price']} × {order['quantity']} shares**，"
                    f"假设支出 {order['cost']} USDC；虚拟成功 {stamp(successful_at) if successful_at else '成功事件缺失，需核对'}；"
                    f"该笔模拟 PnL **{money(order['pnl'])}**。")
            else:
                lines.append(f"  未开仓：{order['reason']}，信号档位 {order['price']}。")
            if evidence.get("signal_to_instruction_ms") is not None:
                lines.append(f"  RF／EX 重叠确认到指令检查 {evidence['signal_to_instruction_ms']:.3f}ms。")
        issues = Counter(e["data"].get("reason", e["kind"]) for e in events
                         if e["data"].get("streak_reset"))
        if issues:
            lines += ["", "- 异常记录："+"；".join(f"{k} × {v}" for k, v in issues.items())+"。"]
        if m["resolution"]:
            r = m["resolution"]
            lines += [f"- {stamp(r['observed_at'])}，官方最终胜方 **{m['winner']}**，该场模拟 PnL **{money(m['pnl'])} USDC**。",
                      f"  官方证据：{r['source']}"]
        else:
            lines.append("- 官方胜负待确认；PnL 暂不计算。")
        lines.append("")
    return "\n".join(lines)+"\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("research_outputs/m1_virtual"))
    parser.add_argument("--start-epoch", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = render(VirtualStore.read_report(args.directory.resolve()), start_epoch=args.start_epoch, limit=args.limit)
    if args.output:
        args.output.write_text(result)
        print(args.output.resolve())
    else:
        print(result)


if __name__ == "__main__":
    main()
