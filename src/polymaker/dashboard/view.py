"""Rich terminal presentation of the observer's atomic local snapshot."""

from __future__ import annotations

import json
import os
import select
import sys
import termios
import time
import tty
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import cast

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from polymaker.dashboard.archive import clock
from polymaker.dashboard.model import Record

COLORS = {"TOTAL": "cyan", "TP": "green", "SL": "yellow", "OTHER": "magenta"}
HISTORY_PERIODS = (4, 12, 24)


def archive_pagination(count: int, height: int | None, page: int) -> tuple[int, int, int]:
    limit = min(20, max(1, (height - 12) // 2)) if height else 20
    pages = max(1, (count + limit - 1) // limit)
    return limit, pages, min(max(0, page), pages - 1)


def fmt(value: object, places: int = 3) -> str:
    if value is None or value == "UNKNOWN":
        return "PENDING"
    try:
        return f"{Decimal(str(value)):+.{places}f}"
    except Exception:
        return str(value)


def read_snapshot(output: Path) -> Record:
    try:
        value = json.loads((output / "latest.json").read_text(encoding="utf-8"))
        if not isinstance(value.get("sessions"), list):
            raise ValueError("invalid snapshot")
        # A view can start while the observer is still migrating a v2 cache.
        # Legacy BA is OTHER, never evidence of a TP-expiry SL.
        for session in value["sessions"]:
            pnl = session.get("pnl_by_role", {})
            if "SL" not in pnl and "BA" in pnl:
                session["pnl_by_role"] = {"TP": pnl.get("TP", "0"), "SL": "0",
                    "OTHER": str(Decimal(pnl.get("OTHER", "0")) + Decimal(pnl["BA"]))}
                for field in ("pairs", "pnl_events"):
                    if field in session:
                        session[field] = [{**p, "category": "OTHER"} if p.get("category") == "BA" else p
                                          for p in session[field]]
        return cast(Record, value)
    except (OSError, ValueError, TypeError, AttributeError):
        return {"observed_at": 0, "sessions": [], "error": "尚无可读取快照；请先启动 dashboard-archive"}


def history_chart(sessions: list[Record], width: int, height: int = 9) -> Text:
    width = max(12, width - 12)
    points: list[tuple[float, ...]] = [(0., 0., 0., 0.)]
    totals = [Decimal(0), Decimal(0), Decimal(0), Decimal(0)]
    for s in sessions:
        totals[0] += sum((Decimal(v) for v in s["pnl_by_role"].values()), Decimal(0))
        totals[1] += Decimal(s["pnl_by_role"]["TP"])
        totals[2] += Decimal(s["pnl_by_role"]["SL"])
        totals[3] += Decimal(s["pnl_by_role"]["OTHER"])
        points.append(tuple(float(v) for v in totals))
    low, high = min(v for p in points for v in p), max(v for p in points for v in p)
    if high - low < 1e-9:
        low, high = low - 1, high + 1
    pad = (high - low) * .06
    low, high = low - pad, high + pad
    grid = [[(" ", "dim") for _ in range(width)] for _ in range(height)]
    zero_y = round((high / (high - low)) * (height - 1))
    if 0 <= zero_y < height:
        grid[zero_y] = [("─", "dim") for _ in range(width)]
    # Text-only projection; Decimal ledger values never depend on chart rounding.
    for series, color, char in [(1, "green", "·"), (2, "yellow", ":"), (3, "magenta", "+"), (0, "cyan", "●")]:
        for x in range(width):
            index = x / (width - 1) * (len(points) - 1)
            a = min(int(index), len(points) - 2)
            weight = index - a
            v = points[a][series] + (points[a + 1][series] - points[a][series]) * weight
            y = min(height - 1, max(0, round((high - v) / (high - low) * (height - 1))))
            grid[y][x] = (char, color)
    result = Text()
    for y, row in enumerate(grid):
        value = high - y / (height - 1) * (high - low)
        result.append(f"{value:>8.2f} │", style="dim")
        for char, color in row:
            result.append(char, style=color)
        result.append("\n")
    result.append(" USDC     ")
    if sessions:
        begin = clock(sessions[0]["market_start"])[11:16] if sessions[0]["market_start"] else "?"
        end = clock(sessions[-1]["market_start"])[11:16] if sessions[-1]["market_start"] else "?"
        result.append(f"{begin} {'─' * max(1, width - 14)} {end}", style="dim")
    return result


def signal_table(current: Record, now: float) -> Table:
    table = Table(box=None, expand=True, padding=(0, 1))
    for heading in ("信号", "方向 / episode", "状态", "Anchor", "有效期"):
        table.add_column(heading)
    for detector in ("CA", "BA"):
        for side in ("YES", "NO"):
            ep = current["signals"][detector].get(side, {})
            state = ep.get("event") or ep.get("state") or "NO_EPISODE"
            if ep.get("invalid_reason") or ep.get("end_reason"):
                state = ep.get("invalid_reason") or ep.get("end_reason")
            deadline = ep.get("wall_deadline")
            ttl = "—"
            if deadline is not None:
                ttl = f"{max(0., deadline - now):.1f}s"
                if now >= deadline and not ep.get("invalid_reason") and not ep.get("end_reason"):
                    state = "EXPIRED"
            table.add_row(Text(detector), Text(f"{side} / {ep.get('episode_id', '—')}"),
                          Text(str(state)), Text(str(ep.get("anchor_bid_price", "—"))), Text(ttl))
    return table


def post_panel(current: Record) -> Panel:
    table = Table(box=None, expand=True, padding=(0, 1))
    table.add_column("类型")
    for side in ("YES", "NO"):
        table.add_column(side, justify="right")
    counts = current.get("post_counts", {})
    roles = ("M1", "CA", "TP", "BA") if "M1" in counts else ("CA", "TP", "BA")
    for role in roles:
        table.add_row(role, *(str(counts.get(role, {}).get(side, 0)) for side in ("YES", "NO")))
    return Panel(Group(table, Text("实际发单笔数 · 含拒单/未知\n不含发送前放弃；不代表成交", style="dim")),
                 title="POST", box=box.SQUARE)


def signal_post_row(signal: RenderableType, current: Record, width: int) -> Table:
    row = Table.grid(expand=True, padding=0)
    row.add_column(ratio=3)
    if width >= 95:
        row.add_column(ratio=2)
        row.add_row(signal, post_panel(current))
    else:
        row.add_row(signal)
        row.add_row(post_panel(current))
    return row


def distribution(sessions: list[Record]) -> Group:
    pairs = [p for s in sessions for p in s.get("pnl_events", s["pairs"])]
    roles = ("TP", "SL", "OTHER")
    table = Table(box=None, expand=True, padding=(0, 1))
    table.add_column("退出归因")
    table.add_column("批次", justify="right")
    table.add_column("PnL / USDC", justify="right")
    for role in roles:
        values = [Decimal(p["pnl"]) for p in pairs if p["category"] == role]
        table.add_row(Text(role, style=COLORS[role]), str(len(values)), fmt(sum(values, Decimal(0))))
    hist = Table(box=None, expand=True, padding=(0, 1))
    for heading in ("PnL / 批", "TP", "SL", "OTHER"):
        hist.add_column(heading)
    # Descending, lower-exclusive / upper-inclusive; both tails stay visible.
    bins = [("P>1.0", Decimal("1"), None), ("0.5<P≤1", Decimal(".5"), Decimal("1")),
            ("0<P≤0.5", Decimal(0), Decimal(".5")), ("−0.5<P≤0", Decimal("-.5"), Decimal(0)),
            ("−1<P≤−0.5", Decimal("-1"), Decimal("-.5")), ("P≤−1.0", None, Decimal("-1"))]
    counts = []
    for label, low, high in bins:
        count = Counter(p["category"] for p in pairs if (low is None or Decimal(p["pnl"]) > low)
                        and (high is None or Decimal(p["pnl"]) <= high))
        counts.append((label, count))
    maximum = max((c[role] for _, c in counts for role in roles), default=1) or 1
    for label, count in counts:
        hist.add_row(Text(label), *(Text(f"{'━' * round(count[role] / maximum * 6)} {count[role]}", style=COLORS[role])
                                   for role in roles))
    return Group(table, Text(""), hist, Text("样本：FIFO 配对批次 + 残仓结算（每场1笔）；费用已分摊", style="dim"))


def render(snapshot: Record, *, width: int = 120, session_id: str | None = None,
           hours: float = 24, archive_view: bool = False, now: float | None = None,
           height: int | None = None, archive_page: int = 0) -> Group:
    now = time.time() if now is None else now
    sessions = [s for s in snapshot["sessions"] if (s.get("market_start") or 0) >= now - hours * 3600]
    selected = session_id or snapshot.get("current_session_id")
    current = next((s for s in snapshot["sessions"] if s["session_id"] == selected), None)
    age = max(0, now - snapshot.get("observed_at", 0))
    header = Text("> POLYMAKER / SESSION MONITOR   ", style="bold")
    header.append(clock(now) + "  UTC+8\n", style="dim")
    header.append(f"只读观察 · 快照延迟 {age:.1f}s · 历史范围 {hours:g}h · {len(sessions)} sessions",
                  style="red" if age > 10 else "green")
    if snapshot.get("catching_up_sources"):
        header.append(f"\n历史加载中：{snapshot['catching_up_sources']} 个日志尚未追平，当前曲线为已读取部分", style="yellow")
    if snapshot.get("error"):
        header.append("\n" + snapshot["error"], style="red")
    if age > 10:
        header.append("\nSTALE：采集快照已过期，不能据此判断当前交易状态", style="red")
    parts: list[RenderableType] = [Panel(header, box=box.SQUARE)]
    if not current:
        parts.append(Text(f"{'指定 session 尚未发现' if session_id else '等待真实 session 日志'}。", style="yellow"))
        return Group(*parts)
    if height is not None and height < 38 and not archive_view:
        return compact_view(snapshot, current, sessions, now, width, height, hours=hours)
    if archive_view:
        table = Table(box=box.SIMPLE, expand=True, title="市场归档索引 / 已观察状态")
        for name in ("市场", "session", "状态", "配对 PnL", "结算 PnL", "数据"):
            table.add_column(name)
        limit, pages, page = archive_pagination(len(sessions), height, archive_page)
        start = page * limit
        for s in sessions[start:start + limit]:
            market = clock(s["market_start"])[5:16] if s["market_start"] else s["slug"]
            table.add_row(Text(market), s["session_id"][:8], Text(s["phase"]),
                          fmt(s["pair_pnl"]), fmt(s["residual_pnl"]),
                          Text(f"{len(s['issues'])} issues" if s["issues"] else "OK"))
        parts.append(table)
        parts.append(Text(f"第 {page + 1}/{pages} 页 · 第 {start + 1 if sessions else 0}–"
                          f"{min(start + limit, len(sessions))} 场 / 共 {len(sessions)} 场"
                          " · [p] 上一页 [n] 下一页 · 按时间从早到晚", style="dim"))
    else:
        live = current.get("market_start") is not None and current["market_start"] <= now < current["market_end"]
        status = f"SESSION {current['session_id']}\n{current['slug']}\n{current['phase']}\n"
        if live:
            status += f"T+{now-current['market_start']:.0f}s / 300s · 剩余 {max(0,current['market_end']-now):.0f}s"
        else:
            status += "当前市场无 session 日志；显示所选历史 session" if not session_id else "所选历史 session"
        lag = max(0, now - current["last_event_at"])
        status += f"\n最后日志 {clock(current['last_event_at']) if current['last_event_at'] else '—'} · {lag:.1f}s 前"
        status += f"\n等待/退出原因：{current['reason'] or '—'}"
        inv = current["inventory"]
        execution = Text(f"YES {inv['YES']} / NO {inv['NO']} sh / Δ {current['delta']}\n"
                         f"POST {current['submitted_orders']} / 成交订单 {current['filled_orders']}\n"
                         f"待确认成交 {current['pending_fills']} / 待核实订单 {current['pending_orders']}\n"
                         f"本场配对 {fmt(current['pair_pnl'])} USDC / 结算 {fmt(current['residual_pnl'])}\n"
                         f"已配未合并 {current['unmerged_pairs']} 对 / 未配残仓 "
                         f"{sum((Decimal(r['quantity']) for r in current['residual']), Decimal(0))} sh")
        for order in current["orders"][-2:]:
            quote = order["quote"]
            entry_mode = current.get("normal_entry_mode", current.get("header", {}).get("normal_entry_mode"))
            role = "CA" if order["role"] == "NORMAL" and entry_mode == "ca_v1" else order["role"]
            if role == "EXIT" and quote.get("window_id", "").endswith(":ca-expiry-exit"):
                role = "SL"
            execution.append(f"\n{role} {quote.get('outcome', quote.get('side', '?'))} "
                             f"@ {quote.get('price', quote.get('limit', '?'))} "
                             f"成交 {order['confirmed_quantity']} / {quote.get('size', quote.get('quantity', '?'))} · {order['status']}")
        top = Table.grid(expand=True, padding=0)
        top.add_column(ratio=1)
        if width >= 95:
            top.add_column(ratio=1)
            top.add_row(Panel(Text(status), title="SESSION", box=box.SQUARE),
                        Panel(execution, title="EXECUTION / INVENTORY", box=box.SQUARE))
        else:
            top.add_row(Panel(Text(status), title="SESSION", box=box.SQUARE))
            top.add_row(Panel(execution, title="EXECUTION / INVENTORY", box=box.SQUARE))
        parts.append(top)
        counts = current["signal_counts"]
        signals = Panel(signal_table(current, now), title=f"SIGNALS · CA {counts['CA']} / BA {counts['BA']} 确认",
                        subtitle="确认 ≠ 准入 ≠ 发单 ≠ 成交", box=box.SQUARE)
        parts.append(signal_post_row(signals, current, width))
        totals = {role: sum((Decimal(s["pnl_by_role"][role]) for s in sessions), Decimal(0))
                  for role in ("TP", "SL", "OTHER")}
        total = sum(totals.values(), Decimal(0))
        headline = Text(f"Total {fmt(total)}   TP {fmt(totals['TP'])}   SL {fmt(totals['SL'])}   OTHER {fmt(totals['OTHER'])}\n")
        headline.append("Total 为已确认 PnL；OTHER 含已核实残仓结算，待结算部分未计入。", style="dim")
        headline.append(f"\n残仓结算待定：{sum(bool(s.get('other_pending')) for s in sessions)} 场", style="dim")
        chart_width = width // 2 - 6 if width >= 95 else width - 6
        curve = Panel(Group(headline, history_chart(sessions, chart_width)), title="HISTORY / 累计已确认 PnL", box=box.SQUARE)
        dist = Panel(distribution(sessions), title="TP / SL / OTHER 分布", box=box.SQUARE)
        row = Table.grid(expand=True)
        row.add_column(ratio=1)
        if width >= 95:
            row.add_column(ratio=1)
            row.add_row(curve, dist)
        else:
            row.add_row(curve)
            row.add_row(dist)
        parts.append(row)
        problems = current["issues"]
        if problems:
            parts.append(Text("数据状态：" + " / ".join(problems), style="yellow"))
        if live and lag > 10:
            parts.append(Text("日志已过期：不能确认交易进程或行情健康，显示最后已知事实。", style="red"))
        events = Text()
        for e in current["recent_events"][-4:]:
            events.append(f"{clock(e['ts'])[11:19]} {e['kind']} {e['reason']}\n")
        parts.append(Panel(events, title="RECENT EVENTS", box=box.SQUARE))
    archive = snapshot.get("archive", {})
    error = archive.get("error") or "无"
    next_time = archive.get("next_boundary", int(now // 300) * 300 + 300)
    parts.append(Text(f"ARCHIVE 下次 {clock(next_time)[11:19]} · {max(0,next_time-now):.0f}s · 错误 {error}\n"
                      "[h] 历史 4h / 12h / 24h   [a] 归档索引 / 总览   [q] 退出看板（后台归档继续）", style="dim"))
    return Group(*parts)


def watch(output: Path, *, once: bool = False, json_output: bool = False,
          session_id: str | None = None, hours: float = 24, refresh: float = 1) -> int:
    console = Console(highlight=False)
    if once or json_output or not console.is_terminal:
        snapshot = read_snapshot(output)
        if json_output:
            print(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
        else:
            console.print(render(snapshot, width=console.width, session_id=session_id, hours=hours))
        return 2 if snapshot.get("error") else 0
    previous = termios.tcgetattr(sys.stdin) if sys.stdin.isatty() else None
    archive_view = False
    archive_page = 0
    try:
        if previous:
            tty.setcbreak(sys.stdin.fileno())
        with Live(console=console, screen=True, auto_refresh=False, vertical_overflow="visible") as live:
            while True:
                snapshot = read_snapshot(output)
                count = sum((s.get("market_start") or 0) >= time.time() - hours * 3600
                            for s in snapshot["sessions"])
                _, pages, archive_page = archive_pagination(count, console.height, archive_page)
                live.update(render(snapshot, width=console.width, session_id=session_id,
                                   hours=hours, archive_view=archive_view, height=console.height,
                                   archive_page=archive_page), refresh=True)
                if previous and select.select([sys.stdin], [], [], refresh)[0]:
                    key = os.read(sys.stdin.fileno(), 1).decode(errors="ignore")
                    if key.lower() == "q":
                        break
                    if key.lower() == "a":
                        archive_view = not archive_view
                        archive_page = 0
                    if key.lower() == "h":
                        hours = next((period for period in HISTORY_PERIODS if period > hours), HISTORY_PERIODS[0])
                        archive_page = 0
                    if archive_view and key.lower() == "n":
                        archive_page = min(archive_page + 1, pages - 1)
                    if archive_view and key.lower() == "p":
                        archive_page = max(0, archive_page - 1)
                elif not previous:
                    time.sleep(refresh)
    except KeyboardInterrupt:
        pass
    finally:
        if previous:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, previous)
    return 0


def compact_view(snapshot: Record, current: Record, sessions: list[Record], now: float,
                 width: int, height: int, *, hours: float = 24) -> Group:
    age = max(0, now - snapshot.get("observed_at", 0))
    title = Text(f"> POLYMAKER  {clock(now)[11:19]} CST  ", style="bold")
    title.append(f"快照 {age:.1f}s" + (" STALE" if age > 10 else ""), style="red" if age > 10 else "green")
    title.append(f" · 历史 {hours:g}h", style="dim")
    top = Text(f"{current['slug']} / {current['session_id'][:12]}\n{current['phase']} · {current['reason'] or '—'}\n"
               f"YES {current['inventory']['YES']} / NO {current['inventory']['NO']} / Δ {current['delta']}\n"
               f"POST {current['submitted_orders']} · 成交订单 {current['filled_orders']} · 待核实 {current['pending_orders']} / {current['pending_fills']}")
    if current.get("market_end") and now >= current["market_end"]:
        top.append("\n当前市场无日志；显示历史 session", style="yellow")
    signal = Text()
    for detector in ("CA", "BA"):
        latest: Record = max(current["signals"][detector].values(), key=lambda e: e.get("ts", 0), default={})
        state = latest.get("invalid_reason") or latest.get("end_reason") or latest.get("event") or latest.get("state", "NO_EPISODE")
        if latest.get("wall_deadline") and now >= latest["wall_deadline"] and not latest.get("end_reason") and not latest.get("invalid_reason"):
            state = "EXPIRED"
        signal.append(f"{detector} {current['signal_counts'][detector]}次 / {latest.get('outcome','—')} {state}\n")
    total = sum((sum((Decimal(v) for v in s["pnl_by_role"].values()), Decimal(0)) for s in sessions), Decimal(0))
    tp = sum((Decimal(s["pnl_by_role"]["TP"]) for s in sessions), Decimal(0))
    sl = sum((Decimal(s["pnl_by_role"]["SL"]) for s in sessions), Decimal(0))
    other = total - tp - sl
    pnl = Text(f"已确认 PnL USDC: Total {fmt(total)} / TP {fmt(tp)} / SL {fmt(sl)} / OTHER {fmt(other)}\n")
    pnl.append(f"OTHER 含已核实残仓结算；待定 {sum(bool(s.get('other_pending')) for s in sessions)} 场；[h] 历史周期 [a] 归档索引 [q] 退出（归档继续）", style="dim")
    pieces = [title, top, signal_post_row(Panel(signal, title="SIGNALS", box=box.SQUARE), current, width), pnl]
    if height >= 22:
        if width >= 95:
            row = Table.grid(expand=True)
            row.add_column(ratio=1)
            row.add_column(ratio=1)
            row.add_row(history_chart(sessions, width // 2 - 2, height=5), distribution(sessions))
            pieces.append(row)
        else:
            pieces.append(history_chart(sessions, width, height=5))
    archive = snapshot.get("archive", {})
    loading = f" · 历史 {snapshot['catching_up_sources']} 源加载中" if snapshot.get("catching_up_sources") else ""
    pieces.append(Text("数据：" + (" / ".join(current["issues"]) or "当前已追平") + loading
                       + (" · 归档错误 " + archive["error"] if archive.get("error") else ""), style="yellow"))
    return Group(*pieces)
