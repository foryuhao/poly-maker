"""Read-only study reporter; waits for the frozen evaluation and writes text.

No exchange clients, credentials or strategy imports. Unknown data never
becomes a zero-PnL market. This reporter cannot tune the running study.
"""

import argparse
import json
import os
import time
from decimal import Decimal as D
from pathlib import Path


def report(state):
    evaluation = sorted((r for r in state["sessions"] if r["phase"] == "EVALUATION"), key=lambda r: r.get("epoch", 0))
    calibration = [r for r in state["sessions"] if r["phase"] == "CALIBRATION"]
    lines = ["M1 模拟研究（T15–T240）", f"状态：{state['status']}；阶段：{state['phase']}",
             f"校准场次：{len(calibration)}；固定参数评估：{len(evaluation)}/{state['evaluation_target']}"]
    if state.get("error"):
        lines.append("运行原因："+str(state["error"])[:250])
    rows = evaluation if evaluation else calibration
    missing = [r for r in rows if not r.get("complete_window") or not r.get("audit_valid") or not r.get("paper")]
    known = [r for r in rows if isinstance(r.get("signals"), int)]
    lines += ["当前所列场次："+("固定参数评估" if evaluation else "校准，不作为最终收益结论"),
              "信号分布："+"，".join(f"{k}次={sum(r['signals']==k for r in known)}场" for k in (0, 1, 2))
                  +f"，超过2次={sum(r['signals']>2 for r in known)}场，次数未知={len(rows)-len(known)}场",
              f"模拟提交={sum(r.get('paper', {}).get('post_attempts', 0) for r in rows)}，"
                  f"估计有成交订单={sum(r.get('paper', {}).get('filled_orders', 0) for r in rows)}；"
                  f"数据或审计不完整={len(missing)}场"]
    if len(evaluation) != state["evaluation_target"] or not state["status"].startswith("COMPLETE"):
        return "\n".join([*lines, "尚未完成固定参数评估，不判断策略是否有收益。"])+"\n"
    settled = [r for r in evaluation if r.get("settlement", {}).get("status", "").startswith("RESOLVED")]
    if missing or len(settled) != len(evaluation):
        return "\n".join([*lines, f"已取得官方结算={len(settled)}场。数据不完整，不能给出总体收益结论。"])+"\n"
    if len({r["epoch"] for r in evaluation}) != len(evaluation):
        raise ValueError("REPORT_DUPLICATE_MARKET")
    if any(r.get("config_sha256") != state["frozen_config_sha256"] for r in evaluation):
        raise ValueError("REPORT_CONFIG_NOT_FROZEN")
    for row in evaluation:
        paper = row["paper"]
        if "fills" in paper:
            cost = sum((D(f["price"])*D(f["size"]) for f in paper["fills"]), D(0))
            if cost != D(paper["spent_usdc"]):
                raise ValueError("REPORT_FILL_COST_MISMATCH")
    low = sum((D(r["settlement"]["pnl_lower_usdc"]) for r in evaluation), D(0))
    high = sum((D(r["settlement"]["pnl_upper_usdc"]) for r in evaluation), D(0))
    spent = sum((D(r["paper"]["spent_usdc"]) for r in evaluation), D(0))
    filled = sum(r["paper"]["filled_orders"] for r in evaluation)
    unknown = sum(r["paper"]["uncertain_orders"] for r in evaluation)
    lines += [f"估计买入支出：{spent} USDC；模型内未知成交订单：{unknown}",
              f"模拟结算盈亏范围：[{low}, {high}] USDC（不是统计置信区间）"]
    if filled == 0:
        verdict = "尚无明确的模型成交，无法判断策略成交后的收益。"
    elif low > 0:
        verdict = "当前队列与延迟假设下，模拟结算收益为正。"
    elif high < 0:
        verdict = "当前队列与延迟假设下，模拟结算收益为负。"
    elif low == high == 0:
        verdict = "当前模型下模拟结算收益为零。"
    else:
        verdict = "未知成交使收益范围跨零或触及零，不能判断有正收益。"
    lines.append(verdict)
    exact = [r for r in evaluation if r["settlement"]["pnl_usdc"] is not None]
    if len(exact) == len(evaluation):
        pnl = [D(r["settlement"]["pnl_usdc"]) for r in evaluation]
        cumulative = peak = drawdown = D(0)
        for value in pnl:
            cumulative += value
            peak = max(peak, cumulative)
            drawdown = max(drawdown, peak-cumulative)
        lines += [f"累计模型盈亏：{cumulative} USDC；每场平均：{cumulative/len(pnl)} USDC；最大回撤：{drawdown} USDC"]
        if spent:
            lines.append(f"模型盈亏/估计买入支出：{cumulative/spent*100:.4f}%")
    lines += ["Maker only、Buy Only；单笔预算5 USDC，单场支出加占款不超过20 USDC。",
              "POST到达假设400ms、撤单确认200ms；可见队列前序、仅成交推进，不假定深度减少就是成交。",
              "不计返佣和链上执行费用；模型成交不等于真实交易所成交。没有执行真实下单或资金操作。",
              "校准场次未混入上述评估收益；完整官方结果、参数和逐场订单在 study.json 及 markets/。"]
    return "\n".join(lines)+"\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if hasattr(os, "nice") and os.nice(0) < 19:
        os.nice(19-os.nice(0))
    while True:
        state = json.loads((args.study_dir/"study.json").read_text())
        text = report(state)
        path = args.study_dir/"result.txt"
        temporary = path.with_suffix(".txt.tmp")
        temporary.write_text(text)
        os.replace(temporary, path)
        if args.once or state["status"].startswith("COMPLETE") or state["status"] in {"FAILED", "INTERRUPTED"}:
            print(text, end="")
            return 0
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
