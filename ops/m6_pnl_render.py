"""Render a frozen read-only snapshot. Public-result-only PnL; gaps never become zero."""

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--directory", type=Path, required=True)
parser.add_argument("--project-dir", type=Path, required=True)
args = parser.parse_args()
OUT = args.directory.resolve()
BJ = ZoneInfo("Asia/Shanghai")
S = json.loads((OUT / "snapshot.json").read_text())
FIRST = S["first_epoch"]
INITIAL = FIRST - 300
NEW = "M6_BEST_ASK_ANCHOR_MINUS_1TICK_UNTIL_T240_V1"
LEGACY = "M6_BEST_ASK_ANCHOR_MINUS_1TICK_UNTIL_T240_V1"
OLD = "FULL_FILL_AT_INSTRUCTION_PRICE"
EVIDENCE_POLICY = "VALID_TOUCH_REQUIRED_NO_EVIDENCE_NO_FILL_V1"
C = {
    "new": "#2563EB",
    "old": "#B45309",
    "text": "#1E293B",
    "profit": "#218653",
    "loss": "#CB4048",
    "gap": "#E8EAED",
    "muted": "#5B6674",
}


def dt(e):
    return datetime.fromtimestamp(e, BJ)


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


def pnl(records, winner):
    return sum(
        (
            D(str(r["quote"]["size"]))
            * ((1 if r["quote"]["outcome"] == winner else 0) - D(str(r["quote"]["price"])))
            for r in records
        ),
        D(0),
    )


rows = []
ids = set()
all_counts = Counter({"UNKNOWN": 0, "LEGACY_UNKNOWN": 0, "GAP_NO_FILL": 0})
comparisons = []
for record in S["records"]:
    epoch = record["epoch"]
    meta = record["metadata"]
    facts = record["facts"]
    posts = facts.get("virtual_post", [])
    observations = facts.get("virtual_observation", [])
    fills = facts.get("virtual_fill", [])
    states = {r["order_id"]: r["status"] for r in observations}
    source_fills, source_states = fills, states.copy()
    assert len(states) == len(observations)
    assert set(states.values()) <= {"FILLED", "NOT_TOUCHED", "UNKNOWN"}
    assert set(states) <= {p["order_id"] for p in posts}
    assert {r["order_id"] for r in fills} == {
        oid for oid, state in states.items() if state == "FILLED"
    }
    for p in posts:
        assert p["order_id"] not in ids
        ids.add(p["order_id"])
        q = p["quote"]
        price, size = D(str(q["price"])), D(str(q["size"]))
        assert D(".02") <= price < D(".20") and price * size <= 5 and size % D(".1") == 0
        assert size >= D(str(meta["market"]["min_order_size"]))
        offset = 1 if p.get("cancel_unfilled_at_cutoff") else 2
        assert D(p["target_price"]) == D(p["anchor_price"]) - offset * D(p["tick_at_post"])
    legacy_posts = [p for p in posts if p.get("model") == LEGACY and not p.get("cancel_unfilled_at_cutoff")]
    replay_cancels = 0
    if legacy_posts:
        receipts = {r["order_id"]: r for r in source_fills}
        legacy_ids = {p["order_id"] for p in legacy_posts}
        fills = [r for r in source_fills if r["order_id"] not in legacy_ids]
        cutoff = epoch + meta["settings"]["cutoff_sec"]
        for post in legacy_posts:
            oid = post["order_id"]
            receipt = receipts.get(oid)
            touch = receipt.get("touch") if receipt else None
            if (touch and post["wall"] < touch["received_wall"] <= cutoff
                    and D(touch["best_ask"]) <= D(post["anchor_price"])-D(post["tick_at_post"])):
                fills.append(receipt)
                states[oid] = "FILLED"
            elif S["snapshot_at"] >= cutoff:
                states[oid] = "NOT_TOUCHED"
                replay_cancels += 1
            else:
                states.pop(oid, None)
    unknown = sum(v == "UNKNOWN" for v in source_states.values())
    pending = len(posts) - len(states)
    comparable_posts = [
        p for p in posts if p["order_id"] in states
    ]
    gap_no_fill = sum(
        r["status"] == "UNKNOWN" or (r["status"] == "NOT_TOUCHED" and bool(r.get("gaps")))
        for r in observations
    )
    all_counts.update("NOT_TOUCHED" if state == "UNKNOWN" else state for state in states.values())
    all_counts["LEGACY_UNKNOWN"] += unknown
    all_counts["GAP_NO_FILL"] += gap_no_fill
    all_counts["HISTORICAL_REPLAY_POSTS"] += len(legacy_posts)
    all_counts["SIMULATED_CANCELS"] += replay_cancels + len(facts.get("virtual_cancel", []))
    all_counts["POSTED"] += len(posts)
    all_counts["PENDING"] += pending
    if not meta:
        coverage = "MISSING"
    elif epoch + 300 > S["snapshot_at"]:
        coverage = "OPEN"
    elif not meta.get("complete") or meta.get("completion_reason"):
        coverage = "INCOMPLETE"
    elif not record["resolution"]:
        coverage = "UNSETTLED"
    elif pending:
        coverage = "PENDING_OBSERVATION"
    else:
        coverage = "COMPLETE_SETTLED"
    resolved = record["resolution"]
    new_pnl = old_pnl = None
    if resolved:
        winner = resolved["winner"]
        new_pnl = pnl(fills, winner)
        old_pnl = pnl(comparable_posts, winner)
        saved = meta.get("modeled_settlement")
        if saved:
            assert D(saved["modeled_pnl_usdc"]) == pnl(source_fills, winner)
            assert D(saved["instant_fill_comparison"]["modeled_pnl_usdc"]) == pnl(posts, winner)
            if "comparable_pnl_usdc" in saved["instant_fill_comparison"]:
                saved_posts = posts if saved.get("fill_evidence_policy") == EVIDENCE_POLICY else [
                    p for p in posts if source_states.get(p["order_id"]) in {"FILLED", "NOT_TOUCHED"}
                ]
                assert D(saved["instant_fill_comparison"]["comparable_pnl_usdc"]) == pnl(saved_posts, winner)
            assert saved["modeled_receipts"] == len(source_fills)
    eligible = coverage == "COMPLETE_SETTLED"
    rows.append(
        {
            "epoch": epoch,
            "market_open_bj": dt(epoch).isoformat(),
            "coverage": coverage,
            "posts": len(posts),
            "comparable_posts": len(comparable_posts),
            "fills": len(fills),
            "not_touched": sum(v in {"NOT_TOUCHED", "UNKNOWN"} for v in states.values()),
            "unknown": 0,
            "legacy_unknown": unknown,
            "gap_no_fill": gap_no_fill,
            "historical_replay_posts": len(legacy_posts),
            "pending_observation": pending,
            "new_pnl_usdc": new_pnl if eligible else None,
            "old_pnl_usdc": old_pnl if eligible else None,
        }
    )

metrics = {}
for key in ["new", "old"]:
    total = peak = mdd = D(0)
    peak_e = INITIAL
    pair = ((INITIAL, D(0)), (INITIAL, D(0)))
    sequence = [D(0)]
    trades = markets = 0
    for row in rows:
        change = row[key + "_pnl_usdc"]
        row[key + "_before_usdc"] = total
        if change is None:
            row[key + "_cumulative_usdc"] = None
            continue
        total += change
        sequence.append(total)
        row[key + "_cumulative_usdc"] = total
        if total > peak:
            peak = total
            peak_e = row["epoch"]
        if peak - total > mdd:
            mdd = peak - total
            pair = ((peak_e, peak), (row["epoch"], total))
        count = row["fills"] if key == "new" else row["comparable_posts"]
        trades += count
        markets += count > 0
    lowest_future = sequence[-1]
    reverse_drawdown = D(0)
    for value in reversed(sequence):
        lowest_future = min(lowest_future, value)
        reverse_drawdown = max(reverse_drawdown, value - lowest_future)
    assert mdd == reverse_drawdown
    has_settled = len(sequence) > 1
    metrics[key] = {
        "pnl_usdc": total if has_settled else None,
        "maximum_drawdown_usdc": mdd if has_settled else None,
        "current_drawdown_usdc": peak - total if has_settled else None,
        "settled_trades": trades,
        "trading_markets": markets,
        "pending_settlement_trades": sum(
            r["fills" if key == "new" else "comparable_posts"]
            for r in rows
            if r["coverage"] in {"OPEN", "UNSETTLED"}
        ),
        "maximum_drawdown_points": pair if mdd else None,
    }
settled = [r for r in rows if r["new_pnl_usdc"] is not None]
complete_settled = [r for r in rows if r["coverage"] == "COMPLETE_SETTLED"]
summary = {
    "snapshot_at_bj": S["snapshot_at_bj"],
    "first_market_open_bj": dt(FIRST).isoformat(),
    "last_market_open_bj": rows[-1]["market_open_bj"],
    "last_settled_market_open_bj": settled[-1]["market_open_bj"] if settled else None,
    "models": {"new": NEW, "old": OLD},
    "metrics": metrics,
    "observations": all_counts,
    "coverage": Counter(r["coverage"] for r in rows),
    "comparison_grain": "POST",
    "fill_evidence_policy": EVIDENCE_POLICY,
    "pnl_completeness": "PENDING_OBSERVATION" if all_counts["PENDING"] else "COMPLETE",
    "partial_settled_markets": 0,
    "scope": "逐笔同批 POST；新口径在POST后至T240（含）有效Best ask触及锚定价减POST时1tick即按原价原量全量模拟成交，无证据记未成交、收益0，T240模拟撤销未成交单。历史场次仅依据留存触价证据重算，采集不足保留诊断。旧口径同批POST立即全量模拟成交。仅完整且官方已结算、观察结束场次计收益。maker费为0；回撤包含初始0。",
    "new_settled_zero_trade_markets": sum(r["fills"] == 0 for r in complete_settled),
    "old_settled_zero_trade_markets": sum(r["posts"] == 0 for r in complete_settled),
}
write("summary.json", summary)
write("market-pnl.json", rows)
with (OUT / "market-pnl-coverage.csv").open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
font = args.project_dir.resolve() / "research_outputs/shared_assets/NotoSansCJKsc-Regular.otf"
font_manager.fontManager.addfont(font)
plt.rcParams.update(
    {
        "font.family": "Noto Sans CJK SC",
        "font.size": 12,
        "axes.unicode_minus": False,
        "text.color": C["text"],
        "axes.labelcolor": C["text"],
        "xtick.color": C["muted"],
        "ytick.color": C["muted"],
        "savefig.facecolor": "white",
    }
)
fig, ax = plt.subplots(figsize=(17, 9.3), dpi=170)
fig.subplots_adjust(left=0.075, right=0.97, top=0.70, bottom=0.30)
fig.text(0.075, 0.95, ("离线合成验证｜" if S.get("synthetic") else "") + "M6 基线｜逐场 PnL 瀑布与累计收益回撤图", fontsize=22)
fig.text(
    0.075,
    0.906,
    f"M6锚定价[0.02,0.20) · 同批POST · {dt(FIRST):%Y-%m-%d %H:%M} 起 · 北京时间 · 瀑布柱仅展示新口径",
    fontsize=12,
    color=C["muted"],
)
labels = [
    ("累计 PnL", "pnl_usdc"),
    ("最大回撤", "maximum_drawdown_usdc"),
    ("当前回撤", "current_drawdown_usdc"),
    ("已结算交易 / 有交易市场", None),
    ("待结算模拟单", "pending_settlement_trades"),
]
for x, (label, key) in zip([0.075, 0.26, 0.445, 0.63, 0.84], labels, strict=True):
    fig.text(x, 0.85, label, fontsize=11, color=C["muted"])
    for y, k in [(0.811, "new"), (0.775, "old")]:
        m = metrics[k]
        if key is None:
            value = f"{m['settled_trades']} 笔 / {m['trading_markets']} 场"
        elif key == "pending_settlement_trades":
            value = f"{m[key]} 笔"
        elif m[key] is None:
            value = "—"
        else:
            value = f"{m[key]:+.3f}" if key == "pnl_usdc" else f"{m[key]:.3f}"
        fig.text(x, y, f"{'新' if k == 'new' else '旧'}  {value}", fontsize=14, color=C[k])
for row in rows:
    if row["coverage"] != "COMPLETE_SETTLED":
        ax.axvspan(
            dt(row["epoch"] - 150), dt(row["epoch"] + 150), color=C["gap"], alpha=0.92, zorder=0
        )
    change = row["new_pnl_usdc"]
    if change is not None and change:
        before = row["new_before_usdc"]
        after = row["new_cumulative_usdc"]
        ax.bar(
            dt(row["epoch"]),
            float(abs(change)),
            bottom=float(min(before, after)),
            width=270 / 86400,
            color=C["profit"] if change > 0 else C["loss"],
            zorder=2,
        )
drawdown_label_offsets = {"new": 8, "old": -18}
if all(metrics[key]["maximum_drawdown_points"] for key in ["new", "old"]):
    midpoint_values = {
        key: sum(point[1] for point in metrics[key]["maximum_drawdown_points"]) / 2
        for key in ["new", "old"]
    }
    lower, upper = sorted(midpoint_values, key=midpoint_values.get)
    drawdown_label_offsets.update({lower: -20, upper: 12})
for key in ["new", "old"]:
    values = [0.0] + [
        float(r[key + "_cumulative_usdc"])
        if r["coverage"] == "COMPLETE_SETTLED" and r[key + "_cumulative_usdc"] is not None
        else np.nan
        for r in rows
    ]
    ax.plot(
        [dt(INITIAL)] + [dt(r["epoch"]) for r in rows],
        values,
        color=C[key],
        linestyle="-",
        lw=1.2,
        zorder=4,
    )
    pair = metrics[key]["maximum_drawdown_points"]
    if pair:
        (pe, pv), (te, tv) = pair
        ax.scatter(
            [dt(pe), dt(te)],
            [float(pv), float(tv)],
            marker="D",
            s=65,
            color=C[key],
            edgecolors="white",
            zorder=6,
        )
        arrow = dt((pe + te) / 2)
        ax.annotate(
            "",
            xy=(dt(te), float(tv)),
            xytext=(dt(pe), float(pv)),
            arrowprops={
                "arrowstyle": "-|>",
                "connectionstyle": "arc3,rad=0",
                "color": C[key],
                "linestyle": "-",
                "lw": 1.5,
                "shrinkA": 5,
                "shrinkB": 5,
                "mutation_scale": 15,
            },
            zorder=5,
        )
        ax.annotate(
            f"{'新' if key == 'new' else '旧'}口径最大回撤 {pv - tv:.3f}",
            (arrow, float((pv + tv) / 2)),
            xytext=(
                -8 if (pe + te) / 2 > (INITIAL + rows[-1]["epoch"]) / 2 else 8,
                drawdown_label_offsets[key],
            ),
            textcoords="offset points",
            ha="right" if (pe + te) / 2 > (INITIAL + rows[-1]["epoch"]) / 2 else "left",
            color=C[key],
            fontsize=11,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.95, "pad": 2},
            zorder=7,
        )
ax.axhline(0, color="#89929C", lw=0.8, zorder=1)
ax.annotate(
    "初始 0\n两条曲线起点重合",
    (dt(INITIAL), 0),
    xytext=(6, 20),
    textcoords="offset points",
    fontsize=11,
)
if not settled:
    ax.text(0.57, 0.71, "暂无可入账的已结算场次", ha="center", transform=ax.transAxes, fontsize=19)
    ax.text(
        0.57,
        0.60,
        "未结算区间断线；暂不计算累计收益与回撤",
        ha="center",
        transform=ax.transAxes,
        color=C["muted"],
        fontsize=12,
    )
    for row in rows:
        text = {
            "UNSETTLED": "已结束 · 等待官方结果",
            "OPEN": "进行中 · 未结算",
            "PENDING_OBSERVATION": "等待T240观察结束",
            "INCOMPLETE": "窗口不完整",
            "MISSING": "记录缺失",
        }.get(row["coverage"], "已结算")
        ax.text(
            dt(row["epoch"]),
            -0.48,
            f"{dt(row['epoch']):%H:%M} 场\n{text}\nPOST {row['posts']} 笔 / 新口径成交 {row['fills']} 笔",
            ha="center",
            va="center",
            fontsize=11,
            color=C["muted"],
        )
values = [
    float(r[k + "_cumulative_usdc"])
    for r in rows
    for k in ["new", "old"]
    if r[k + "_cumulative_usdc"] is not None
] + [0.0]
spread = max(max(values) - min(values), 1.0)
ax.set_ylim(min(values) - spread * 0.12, max(values) + spread * 0.22)
ax.set_xlim(dt(INITIAL - 90), dt(rows[-1]["epoch"] + 180))
tick_indices = sorted(set(np.linspace(0, len(rows) - 1, min(11, len(rows)), dtype=int)))
tick_rows = [rows[index] for index in tick_indices]
date_format = "%H:%M" if dt(FIRST).date() == dt(rows[-1]["epoch"]).date() else "%m/%d\n%H:%M"
ax.set_xticks([dt(row["epoch"]) for row in tick_rows])
ax.set_xticklabels([dt(row["epoch"]).strftime(date_format) for row in tick_rows])
ax.set_xlabel("市场开场时间（北京时间）", labelpad=10)
ax.set_ylabel("累计 PnL（USDC）", labelpad=12)
ax.grid(axis="y", color="#D7DDE4", lw=0.6)
for side in ["top", "right"]:
    ax.spines[side].set_visible(False)
for side in ["left", "bottom"]:
    ax.spines[side].set_color("#BBC3CD")
handles = [
    Line2D([0], [0], color=C["new"], lw=2, label="新：POST 至 T240 触及锚定价 − 1 tick"),
    Line2D([0], [0], color=C["old"], ls="-", lw=2, label="旧：POST 立即全量模拟成交"),
    Patch(facecolor=C["profit"], label="新口径单场盈利"),
    Patch(facecolor=C["loss"], label="新口径单场亏损"),
    Patch(facecolor=C["gap"], label="不完整 / 待观察 / 未结算"),
]
fig.legend(
    handles=handles,
    loc="lower left",
    bbox_to_anchor=(0.07, 0.16),
    ncol=3,
    frameon=False,
    fontsize=11,
)
cutoff = dt(settled[-1]["epoch"]).strftime("%H:%M 开场") if settled else "暂无"
fig.text(
    0.075,
    0.125,
    f"快照：{dt(S['snapshot_at']):%Y-%m-%d %H:%M:%S}（北京）  |  已结算数据截止：{cutoff}  |  完整且已结算零交易市场：{summary['new_settled_zero_trade_markets']} 场",
    fontsize=10.5,
    color=C["muted"],
)
fig.text(
    0.075,
    0.087,
    f"观察统计：POST {all_counts['POSTED']} 笔；模拟成交 {all_counts['FILLED']} 笔；无有效触价证据 {all_counts['NOT_TOUCHED']} 笔（含缺口 {all_counts['GAP_NO_FILL']} 笔）；待观察 {all_counts['PENDING']} 笔。",
    fontsize=10.5,
    color=C["muted"],
)
fig.text(
    0.075,
    0.047,
    f"口径：POST 至 T240 有效触及锚定价−1 tick即成交；无证据记未成交并模拟撤单。历史 {all_counts['HISTORICAL_REPLAY_POSTS']} 笔仅依留存证据重算；回撤包含初始0。",
    fontsize=10.5,
    color=C["muted"],
)
fig.savefig(OUT / "m6-pnl-waterfall-dual-cumulative-drawdown.png", dpi=170)
plt.close(fig)
write(
    "validation.json",
    {
        "same_post_cohort": True,
        "comparison_grain": "POST",
        "fill_evidence_policy": EVIDENCE_POLICY,
        "legacy_unknown_as_no_fill": True,
        "partial_settled_markets": summary["partial_settled_markets"],
        "unique_order_ids": len(ids),
        "settled_markets": len(settled),
        "drawdown_independent_check": "PASS",
        "missing_values_not_zero": True,
        "snapshot_read_only": True,
        "actual_fills_queried": False,
        "real_orders_sent": 0,
    },
)
print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
