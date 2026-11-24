"""M1 terminal view: virtual instructions and native submissions stay separate."""

from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from polymaker.dashboard.archive import clock
from polymaker.dashboard.m1_trades import trade_records
from polymaker.dashboard.model import Record, number
from polymaker.dashboard.view import archive_pagination, fmt, signal_post_row


def pnl(s: Record):
    if s.get("m1_fill_tracking") == "NOT_TRACKED":
        return s.get("trading_pnl")
    return s.get("simulated_pnl") if s.get("post_type") == "VIRTUAL" else s.get("known_pnl")


def component_panel(current: Record) -> Panel:
    table = Table(box=None, expand=True, padding=(0, 1))
    for name in ("信号", "本场次数", "最近确认", "方向"):
        table.add_column(name)
    for kind in ("RF", "EX"):
        latest = current.get("m1_component_latest", {}).get(kind, {})
        count = current.get("m1_components", {}).get(kind)
        table.add_row(kind, str(count) if count is not None else "—",
            clock(latest["ts"])[11:19] if latest.get("ts") is not None else "—", latest.get("outcome", "—"))
    return Panel(Group(table, Text("最近为识别确认时间，不代表仍有效", style="dim")),
                 title="RF / EX", box=box.SQUARE)


def comparison_start(sessions: list[Record]) -> float | None:
    starts = [min(s["market_start"] for s in sessions if s.get("post_type") == kind)
              for kind in ("REAL", "VIRTUAL") if any(s.get("post_type") == kind for s in sessions)]
    return max(starts) if starts else None


def curve_points(sessions: list[Record], start: float, end: float) -> list[tuple[float, float | None]]:
    points: list[tuple[float, float | None]] = [(start, 0.)]
    total = Decimal(0)
    for s in sorted(sessions, key=lambda s: s["market_start"]):
        if not start <= s["market_start"] <= end:
            continue
        if pnl(s) is not None:
            total += Decimal(pnl(s))
        points.append((min(end, s.get("market_end") or s["market_start"]+300),
                       float(total) if pnl(s) is not None else None))
    return points


def curve(points: list[tuple[float, float | None]], width: int, *, start: float, end: float,
          limits: tuple[float, float], height=6) -> Text:
    low, high = limits
    if high-low < 1e-9:
        low, high = low-1, high+1
    width = max(4, width-13)
    grid = [[" " for _ in range(width)] for _ in range(height)]
    times = [t for t, _ in points]
    for x in range(width):
        timestamp = start+(end-start)*x/(width-1)
        i = max(0, bisect_right(times, timestamp)-1)
        value = points[i][1]
        if value is None:
            continue
        y = min(height-1, max(0, round((high-value)/(high-low)*(height-1))))
        grid[y][x] = "●"
    result = Text()
    for y, row in enumerate(grid):
        result.append(f"{high-y/(height-1)*(high-low):>9.2f} │", style="dim")
        result.append("".join(row)+"\n", style="cyan")
    left, right = clock(start)[11:16], clock(end)[11:16]
    result.append(" "*11+left+" "*max(1, width-len(left)-len(right))+right, style="dim")
    return result


def price_bucket_sessions(sessions: list[Record]) -> list[tuple[int, int]]:
    """Positive bucket PnL / traded sessions; unknown bucket PnL is not a win."""
    records = trade_records(sessions)
    account_sessions = {r["session_id"] for r in records if r.get("record_type") == "ACCOUNT_TRADE"}
    groups = defaultdict(list)
    bands = ((Decimal(".01"), Decimal(".10")), (Decimal(".10"), Decimal(".20")))
    for row in records:
        # Public fills and native receipts can describe the same purchases.
        # Use public fills alone whenever that session has account trades.
        if row["session_id"] in account_sessions and row.get("record_type") != "ACCOUNT_TRADE":
            continue
        price, quantity = number(row["price"]), number(row["filled"])
        if row["side"] != "BUY" or price is None or quantity is None or quantity <= 0:
            continue
        for i, (low, high) in enumerate(bands):
            if low <= price < high:
                groups[(i, row["session_id"])].append(number(row["pnl"]))
    counts = []
    for i in range(len(bands)):
        values = [v for (band, _sid), v in groups.items() if band == i]
        wins = sum(all(v is not None for v in rows) and sum(rows, Decimal(0)) > 0 for rows in values)
        counts.append((wins, len(values)))
    return counts


def comparison_charts(sessions: list[Record], width: int, now: float) -> Group:
    start = comparison_start(sessions)
    table = Table.grid(expand=True, padding=0)
    widths = (width//2, width-width//2)
    for size in widths:
        table.add_column(width=size)
    selected = [s for s in sessions if start is not None and start <= s["market_start"] <= now]
    grouped = {kind: [s for s in selected if s.get("post_type") == kind] for kind in ("REAL", "VIRTUAL")}
    points = {kind: curve_points(rows, start, now) for kind, rows in grouped.items()} if start is not None else {}
    values = [v for rows in points.values() for _, v in rows if v is not None] or [0.]
    limits = min(values), max(values)
    panels = []
    for (kind, label), size in zip((("REAL", "真实"), ("VIRTUAL", "基线")), widths, strict=True):
        rows = grouped[kind]
        known = [s for s in rows if pnl(s) is not None]
        message = ""
        if not rows:
            message = f"暂无{label}数据，等待记录。"
        elif not known:
            message = ("真实收益待成交与结算证据确认，暂不绘制为零收益。" if kind == "REAL"
                       else "基线收益待官方结算，不补为零。")
        graph = (curve(points[kind] if known else [(start, None)], size-4,
                       start=start, end=now, limits=limits) if start is not None else Text("等待数据"))
        total = fmt(sum((Decimal(pnl(s)) for s in known), Decimal(0))) if known else "待定"
        levels = [v for _, v in points[kind] if v is not None] if known else []
        highest = fmt(max(levels)) if levels else "待定"
        lowest = fmt(min(levels)) if levels else "待定"
        caption = Text(f"对照区间累计 {total} · 区间最高 {highest} · 区间最低 {lowest}"
                       f" · 待定 {len(rows)-len(known)} 场", style="dim")
        lower, upper = price_bucket_sessions(rows)
        buckets = Text(f"0.01–0.09：{lower[0]}/{lower[1]} · 0.10–0.19：{upper[0]}/{upper[1]}", style="dim")
        panels.append(Panel(Group(graph, caption, buckets, Text(message, style="dim")),
                            title=f"{label}累计 PnL / USDC", box=box.SQUARE, height=16))
    table.add_row(*panels)
    both = all(any(s.get("post_type") == kind for s in sessions) for kind in ("REAL", "VIRTUAL"))
    note = (f"共同起点 {clock(start)[5:16].replace('T', ' ')}，取真实与基线较晚开始时间；两图时间轴、金额刻度一致。"
            if both else "另一侧暂无场次，起点待对齐。")
    return Group(table, Text(note+" 仅累计已确认收益，待定不补零。\n"
        "价格段统计：盈利场次／有交易场次；同段一场只计一次，待定仅计分母。", style="dim"))


def archive_sessions(sessions: list[Record], kind: str) -> list[Record]:
    return sorted((s for s in sessions if s.get("post_type") == kind
        and (s.get("strategy") == "M1" or s.get("normal_entry_mode") == "m1_v1")),
        key=lambda s: (s["market_start"], s.get("last_event_at", 0), s["session_id"]), reverse=True)


def account_panel(current: Record, now: float) -> Panel:
    account = current.get("pm_account") or {}
    available = account.get("state") == "AVAILABLE"
    balance = current.get("account_balance_usdc")
    missing_balance = "当前只读接口未提供" if available else "日志未记录"
    cash = current.get("pm_balance") or {}
    if cash.get("state") == "REQUESTED":
        missing_balance = "读取中（本场一次）"
    elif cash.get("state") == "UNAVAILABLE":
        missing_balance = "读取失败（本场不重试）"
    lines = [f"账户余额：{str(balance)+' USDC' if balance is not None else missing_balance}"]
    if balance is not None and current.get("account_balance_at") is not None:
        age = max(0, now-current["account_balance_at"])
        if cash:
            lines.append(f"本场一次快照 {clock(current['account_balance_at'])} · {age:.0f}s 前")
        else:
            lines.append(f"余额快照 {age:.0f}s 前{'，已过期' if age > 10 else ''}")
    if cash.get("error"):
        lines.append(cash["error"])
    value = account.get("market_value") if available else None
    floating = account.get("unrealized_pnl") if available else None
    lines.append(f"持仓估值（本场）：{str(value)+' USDC' if value is not None else '未提供'}")
    lines.append(f"浮动 PnL（本场）：{fmt(floating)+' USDC' if floating is not None else '未提供'}")
    if account.get("observed_at") is not None:
        age = max(0, now-account["observed_at"])
        lines.append(f"PM 快照 {age:.0f}s 前{'，已过期' if age > 45 and pnl(current) is None else ''}")
    return Panel(Group(Text("\n".join(lines)), Text("浮动收益不另计入累计图", style="dim")),
                 title="账户 / USDC", box=box.SQUARE)


def render_m1(snapshot: Record, current: Record, sessions: list[Record], *, now: float,
              width: int, height: int | None, hours: float, archive_view: bool, archive_page: int,
              trade_view: bool = False, archive_type: str = "REAL") -> Group:
    mode = current.get("post_type", "REAL")
    label = "基线" if mode == "VIRTUAL" else "真实"
    sessions = [s for s in sessions if s.get("strategy") == "M1" or s.get("normal_entry_mode") == "m1_v1"]
    if archive_view or trade_view:
        sessions = archive_sessions(sessions, archive_type)
        label = "基线" if archive_type == "VIRTUAL" else "真实"
    compact = height is not None and height < 34
    age = max(0, now-snapshot.get("observed_at", 0))
    header = Text(f"> POLYMAKER / M1 · {label}    {clock(now)}\n", style="bold")
    header.append(f"只读看板 · 快照 {age:.1f}s{' · STALE' if age>10 else ''} · 历史 {hours:g}h · {len(sessions)} 场", style="red" if age>10 else "green")
    if snapshot.get("display_since") is not None:
        header.append(f" · 展示自 {clock(snapshot['display_since'])[5:16].replace('T', ' ')}", style="dim")
    if snapshot.get("catching_up_sources"):
        header.append(f" · 历史加载中 {snapshot['catching_up_sources']} 个数据源", style="yellow")
    if snapshot.get("error"):
        header.append("\n"+snapshot["error"], style="red")
    parts = [header if compact else Panel(header, box=box.SQUARE)]
    if trade_view:
        from polymaker.dashboard.m1_trades import trade_page

        parts.append(trade_page(sessions, height, archive_page, kind=archive_type))
    elif archive_view:
        table = Table(title=f"M1 场次记录 · {label}", box=box.SIMPLE, expand=True)
        for name in ("市场", "状态", "M1信号", "POST", "PnL / USDC", "数据"):
            table.add_column(name)
        limit, pages, page = archive_pagination(len(sessions), height, archive_page)
        for s in sessions[page*limit:(page+1)*limit]:
            virtual = s.get("post_type") == "VIRTUAL"
            table.add_row(clock(s["market_start"])[5:16], s["phase"],
                str(s["signal_counts"].get("M1", 0)), str(s.get("virtual_submitted_orders" if virtual else "real_submitted_orders", 0)),
                "待结算" if pnl(s) is None else fmt(pnl(s)), " / ".join(s["issues"]) or ("完整" if s["ended"] else "观察中"))
        if not sessions:
            parts.append(Text(f"当前历史范围内暂无{label}场次。", style="yellow"))
        parts += [table, Text(f"共 {len(sessions)} 场 · 第 {page+1}/{pages} 页 · [↑]上一页 [↓]下一页\n"
                              "[←]真实 [→]基线 · 新场次在前", style="dim")]
    else:
        elapsed = now-current["market_start"]
        live = 0 <= elapsed < 300
        status = f"{current['slug']} · {current['phase']}"
        status += f" · T+{elapsed:.0f}s" if live else " · 历史场次"
        inventory = current.get("simulated_inventory", current["inventory"]) if mode == "VIRTUAL" else current["inventory"]
        untracked = current.get("m1_fill_tracking") == "NOT_TRACKED"
        account = current.get("pm_account") or {}
        account_available = account.get("state") == "AVAILABLE"
        if mode == "REAL" and account_available:
            status += f"\nPM 账户持仓 YES {inventory['YES']} / NO {inventory['NO']} shares"
        elif mode == "REAL" and untracked:
            status += "\n真实持仓：未核实（当前实盘未跟踪成交）"
        else:
            status += f"\n{'基线持仓' if mode=='VIRTUAL' else '真实持仓'} YES {inventory['YES']} / NO {inventory['NO']} shares"
        if mode == "VIRTUAL":
            status += f" · 假设支出 {current.get('simulated_spend', '0')} USDC"
        else:
            if untracked:
                status += f"\n真实提交 {current.get('real_submitted_orders', 0)} 次；接单回执不代表成交"
            else:
                status += f" · 已确认成交订单 {current['filled_orders']} · 待核对 {current['pending_orders']} 单 / {current['pending_fills']} 笔成交"
            metric = (current.get("m1_progress") or {}).get("signals") or {}
            if metric.get("attainment") is not None:
                status += f"\n信号资金达成率 {Decimal(str(metric['attainment']))*100:.2f}% / 目标100%"
        pending_label = "未核实" if untracked else "待结算"
        pnl_label = "PM 账户已结算" if account_available else "基线" if mode == "VIRTUAL" else "已确认"
        status += f"\n本场{pnl_label} PnL：{pending_label if pnl(current) is None else fmt(pnl(current))} USDC"
        if account_available and account.get("settled_pnl") is not None and any(Decimal(v) > 0 for v in inventory.values()):
            status += " · 含已确定赔付的未关闭持仓"
        if account.get("observed_at") is not None:
            status += f"\nPM 实际成交 {len(account.get('trades', []))} 笔"
        if account.get("error"):
            status += "\nPM 账户读取失败，保留上次记录："+account["error"]
        elif account.get("resolution_error") and account.get("settled_pnl") is None:
            status += "\n结算结果暂未读到，稍后重试："+account["resolution_error"]
        if current.get("settlement"):
            status += f" · 官方胜方 {current['settlement']['winner']}"
        lag = max(0, now-current["last_event_at"])
        if live and lag > 30:
            status += f"\nM1 心跳已过期 {lag:.0f}s，显示最后已知状态"
        session_panel = Panel(Text(status), title="M1 SESSION", box=box.SQUARE)
        row = Table.grid(expand=True, padding=0)
        row.add_column(ratio=3)
        if width >= 80:
            row.add_column(ratio=2)
            row.add_row(session_panel, account_panel(current, now))
        else:
            row.add_row(session_panel)
            row.add_row(account_panel(current, now))
        parts.append(row)
        table = Table(box=None, expand=True)
        for name in ("信号", "时间", "方向", "价格", "状态 / 原因"):
            table.add_column(name)
        for i, signal in enumerate(current.get("m1_signals", []), 1):
            state = signal.get("state", "—")
            reason = signal.get("reason")
            if state == "VIRTUAL_SUCCESS":
                state = "已生成基线指令"
            elif state == "SKIPPED":
                state = "已跳过"
            if reason:
                state += "：" + {"PRICE_OUTSIDE_TEST_BAND": "超出测试价格范围",
                    "SESSION_BUDGET": "本场额度已用完",
                    "SESSION_INSTRUCTION_LIMIT": "本场已达5个机会上限"}.get(reason, reason)
            timestamp = clock(signal["ts"])[11:19] if signal.get("ts") is not None else "—"
            table.add_row(f"M1 #{i}", timestamp, signal["outcome"],
                          str(signal.get("price") or "—"), state)
        if not current.get("m1_signals"):
            table.add_row("—", "—", "—", "—", "本场尚无 M1 信号")
        title = f"SIGNALS · M1 {current['signal_counts'].get('M1', 0)}"
        parts.append(signal_post_row(Panel(table, title=title, box=box.SQUARE), current, width))
        real_sessions = [s for s in sessions if s.get("post_type") == "REAL"]
        parts.append(Text(f"基线待结算 {sum(s.get('post_type')=='VIRTUAL' and pnl(s) is None for s in sessions)} 场 · "
            f"真实待核实 {sum(pnl(s) is None for s in real_sessions)} 场 · 基线与真实收益分开统计"))
        if not compact or height >= 30:
            parts.append(comparison_charts(sessions, width, now))
        if not compact:
            orders = Table(title="最近开仓指令 / 订单", box=box.SIMPLE, expand=True)
            for name in ("POST类型", "方向", "价格", "股数", "状态"):
                orders.add_column(name)
            instructions = [o for o in current["orders"] if o["status"] != "SKIPPED"]
            for o in instructions[-5:]:
                from polymaker.dashboard.m1_trades import order_state

                q = o["quote"]
                orders.add_row(label, str(q.get("outcome", "—")), str(q.get("price", "—")),
                               str(q.get("size", q.get("quantity", "—"))),
                               "已生成基线指令" if o["status"] == "VIRTUAL_SUCCESS" else order_state(o["status"], untracked))
            if not instructions:
                orders.add_row("—", "—", "—", "—", "本场尚无开仓指令")
            parts.append(orders)
            parts.append(Panel(Text("\n".join(f"{clock(e['ts'])[11:19]} {e['kind']} {e['reason']}"
                for e in current["recent_events"][-4:])), title="RECENT EVENTS", box=box.SQUARE))
        if current["issues"]:
            parts.append(Text("数据状态："+" / ".join(current["issues"]), style="yellow"))
    parts.append(Text("基线：按指令价足额成交并持有至官方结算的假设；不代表真实成交。\n"
        "[h]历史周期 [a]场次记录 [t]交易记录（再按返回总览） [q]退出看板（策略继续运行）", style="dim"))
    return Group(*parts)
