"""Read-only M1 order history; baseline fills and actual fills stay distinct."""

from decimal import Decimal

from rich import box
from rich.console import Group
from rich.table import Table
from rich.text import Text

from polymaker.dashboard.archive import clock
from polymaker.dashboard.fill_pnl import fill_pnls
from polymaker.dashboard.model import Record, number


def order_state(status: str, untracked: bool = False) -> str:
    state = status.rsplit(" / ", 1)[-1]
    if untracked:
        return {"ACK": "已接单，成交未核实", "UNKNOWN": "提交结果不明", "INTENT": "已提交，结果待定",
                "REJECTED": "已拒单", "ABORTED": "未提交"}.get(state, status)
    return status


def trade_records(sessions: list[Record]) -> list[Record]:
    rows = {}
    for session in sessions:
        if session.get("strategy") != "M1" and session.get("normal_entry_mode") != "m1_v1":
            continue
        baseline = session.get("post_type") == "VIRTUAL"
        for order in session.get("orders", []):
            status = order["status"]
            if baseline and status != "VIRTUAL_SUCCESS":
                continue
            filled = order.get("simulated_quantity" if baseline else "confirmed_quantity", "0")
            if not baseline and not order.get("post_started") and not number(filled):
                continue
            quote = order["quote"]
            cost = order.get("simulated_cost" if baseline else "confirmed_cost")
            result = order.get("simulated_pnl") if baseline else None
            if not baseline and session.get("trading_pnl") is not None and session.get("settlement"):
                winner = session["settlement"].get("winner")
                if winner in {"YES", "NO"} and cost is not None and filled is not None and quote.get("outcome") in {"YES", "NO"}:
                    result = str((Decimal(filled) if quote["outcome"] == winner else Decimal(0))-Decimal(cost))
            rows[(session["session_id"], order["order_id"])] = {
                "session_id": session["session_id"], "order_id": order["order_id"],
                "market_start": session["market_start"], "ts": order.get("submitted_at"),
                "post_type": "VIRTUAL" if baseline else "REAL", "outcome": quote.get("outcome", "—"),
                "side": quote.get("side", "BUY"),
                "price": quote.get("price", "—"), "quantity": quote.get("size", quote.get("quantity", "—")),
                "filled": filled, "cost": cost, "pnl": result,
                "pnl_pending": "未归单" if not baseline and session.get("pm_account") else "待定",
                "status": "已结算" if baseline and result is not None else "待结算" if baseline
                    else order_state(status, session.get("m1_fill_tracking") == "NOT_TRACKED")}
        if not baseline:
            attributed = fill_pnls(session)
            for trade in (session.get("pm_account") or {}).get("trades", []):
                rows[(session["session_id"], "account:"+trade["id"])] = {
                    "session_id": session["session_id"], "order_id": "account:"+trade["id"],
                    "market_start": session["market_start"], "ts": trade["ts"],
                    "post_type": "REAL", "record_type": "ACCOUNT_TRADE", "outcome": trade["outcome"],
                    "side": trade["side"],
                    "price": trade["price"], "quantity": trade["quantity"], "filled": trade["quantity"],
                    "cost": trade["amount"], "pnl": attributed[trade["id"]]["pnl"],
                    "pnl_pending": attributed[trade["id"]]["pending"], "status": "PM 成交 "+trade["side"]}
    return sorted(rows.values(), key=lambda r: (r["ts"] or r["market_start"], r["session_id"], r["order_id"]), reverse=True)


def trade_pagination(count: int, height: int | None, page: int) -> tuple[int, int, int]:
    from polymaker.dashboard.view import archive_pagination

    return archive_pagination(count, max(1, height-6) if height is not None else None, page)


def trade_page(sessions: list[Record], height: int | None, page: int, *, kind: str = "REAL") -> Group:
    # Imported lazily because the shared renderer also dispatches to M1.
    from polymaker.dashboard.view import fmt

    sessions = [s for s in sessions if s.get("post_type") == kind]
    rows = trade_records(sessions)
    account_rows = any(r.get("record_type") == "ACCOUNT_TRADE" for r in rows)
    account_markets = any(s.get("m1_account_tracking") == "PM_DATA_API" for s in sessions)
    limit, pages, page = trade_pagination(len(rows), height, page)
    type_label = "基线" if kind == "VIRTUAL" else "真实"
    table = Table(title=f"M1 交易记录 · {type_label}", box=box.SIMPLE, expand=True, padding=(0, 1))
    for name in ("场次 / 时间", "类型", "方向", "价格", "目标 / 完成股数", "支出", "状态", "PnL"):
        table.add_column(name, overflow="fold")
    for r in rows[page*limit:(page+1)*limit]:
        market = clock(r["market_start"])[5:16]
        stamp = clock(r["ts"])[11:19] if r["ts"] is not None else "时间未记录"
        label = "基线" if r["post_type"] == "VIRTUAL" else "真实成交" if r.get("record_type") == "ACCOUNT_TRADE" else "真实订单"
        table.add_row(Text(f"{market}\n{stamp}"), label,
            Text(r["outcome"]), str(r["price"]), f"{r['quantity']} / {r['filled'] if r['filled'] is not None else '—'}",
            str(r["cost"]) if r["cost"] is not None else "未核实", Text(r["status"]),
            fmt(r["pnl"]) if r["pnl"] is not None else r.get("pnl_pending", "待定"))
    totals = {kind: sum((Decimal(r["pnl"]) for r in rows if r["post_type"] == kind and r["pnl"] is not None), Decimal(0))
              for kind in ("VIRTUAL", "REAL")}
    if account_markets:
        totals["REAL"] = sum((Decimal(s["trading_pnl"]) for s in sessions
            if s.get("post_type") == "REAL" and s.get("trading_pnl") is not None), Decimal(0))
    real = [r for r in rows if r["post_type"] == "REAL"]
    has_market_pnl = account_markets and any(s.get("post_type") == "REAL" and s.get("trading_pnl") is not None for s in sessions)
    real_total = "未核实" if real and all(r["pnl"] is None for r in real) and not has_market_pnl else fmt(totals["REAL"])
    real_label = "真实账户场次 PnL" if account_markets else "真实逐单已确认 PnL"
    known_fills = sum(r.get("record_type") == "ACCOUNT_TRADE" and r["pnl"] is not None for r in rows)
    pending = (f"已补 {known_fills} 笔成交 PnL；其余待结算或未归因。合计仍按场次。" if account_markets else
               f"PnL 待定 {sum(r['pnl'] is None for r in rows)} 笔，未计入上述合计")
    total_line = (f"已结算基线 PnL {fmt(totals['VIRTUAL'])} USDC" if kind == "VIRTUAL"
                  else f"{real_label} {real_total} USDC")
    count = Text(f"共 {len(rows)} {'条' if account_rows else '笔'} · 第 {page+1}/{pages} 页 · [↑]上一页 [↓]下一页\n"
        +total_line+"\n"+pending+"\n[←]真实 [→]基线 · 新记录在前")
    note = ("成交 PnL 与仓位核对后补充；无法归单的订单回执不填。PM 场次汇总可能有舍入尾差，不重复相加。"
            if account_rows else "基线按指令价足额成交并持有至官方结算；不代表真实成交。" if kind == "VIRTUAL"
            else "真实完成股数与支出只计确认成交；接单不代表成交，未知 PnL 不记零。")
    return Group(table, Text(f"当前历史范围内暂无{type_label}交易记录。") if not rows else Text(""), count, Text(note, style="dim"))
