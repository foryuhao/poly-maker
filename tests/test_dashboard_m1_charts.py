"""Compare the same time interval without including earlier baseline profits."""

import io

import pytest
from rich.console import Console

from polymaker.dashboard.archive import clock
from polymaker.dashboard.m1_view import (
    comparison_charts,
    comparison_start,
    curve,
    curve_points,
    price_bucket_sessions,
)
from test_dashboard import NOW


def session(kind, offset, value):
    return {"post_type": kind, "market_start": NOW+offset, "market_end": NOW+offset+300,
            "simulated_pnl": value if kind == "VIRTUAL" else None,
            "known_pnl": value if kind == "REAL" else None,
            "trading_pnl": value if kind == "REAL" else None,
            "m1_fill_tracking": "NOT_TRACKED" if kind == "REAL" and value is None else "CONFIRMED_FILLS"}


def test_comparison_starts_at_later_side_and_excludes_earlier_profits():
    sessions = [session("VIRTUAL", 0, "1000"), session("VIRTUAL", 300, "1.5"),
                session("REAL", 300, "2"), session("REAL", 600, None),
                session("VIRTUAL", 900, ".5"), session("REAL", 900, "-1")]
    start = comparison_start(sessions)
    assert start == NOW+300
    baseline = curve_points([s for s in sessions if s["post_type"] == "VIRTUAL"], start, NOW+1500)
    real = curve_points([s for s in sessions if s["post_type"] == "REAL"], start, NOW+1500)
    assert baseline == [(NOW+300, 0), (NOW+600, 1.5), (NOW+1200, 2)]
    assert real == [(NOW+300, 0), (NOW+600, 2), (NOW+900, None), (NOW+1200, 1)]
    assert comparison_start([session("REAL", 0, "100"), session("VIRTUAL", 600, "1")]) == NOW+600


@pytest.mark.parametrize("width", [80, 140])
def test_two_charts_are_side_by_side_and_share_time_and_amount_scales(width):
    sessions = [session("VIRTUAL", 0, "1000"), session("VIRTUAL", 300, "1.5"),
                session("REAL", 300, "2"), session("VIRTUAL", 900, ".5"), session("REAL", 900, "-1")]
    console = Console(width=width, file=io.StringIO(), record=True)
    console.print(comparison_charts(sessions, width, NOW+1500))
    text = console.export_text()
    title = next(line for line in text.splitlines() if "真实累计 PnL" in line)
    assert "基线累计 PnL" in title and title.index("真实累计 PnL") < title.index("基线累计 PnL")
    assert "+1.000" in text and "+2.000" in text and "1000" not in text
    assert text.count(clock(NOW+300)[11:16]) >= 2 and text.count(clock(NOW+1500)[11:16]) == 2
    assert any(line.count("2.00") == 2 for line in text.splitlines())
    assert "共同起点" in text


def test_unknown_real_pnl_does_not_get_a_zero_curve():
    console = Console(width=140, file=io.StringIO(), record=True)
    console.print(comparison_charts([session("REAL", 300, None), session("VIRTUAL", 300, "1")], 140, NOW+900))
    text = console.export_text()
    assert "暂不绘制为零收益" in text and "●" in text
    assert not any("●" in line[:70] for line in text.splitlines())


def test_unresolved_interval_is_a_gap_and_not_zero_pnl():
    points = [(NOW, 0), (NOW+300, 1), (NOW+600, None)]
    text = curve(points, 80, start=NOW, end=NOW+900, limits=(0, 1)).plain
    assert all("●" not in line[-15:] for line in text.splitlines()[:6])


def test_missing_counterpart_is_explicit_and_does_not_invent_a_start():
    console = Console(width=120, file=io.StringIO(), record=True)
    console.print(comparison_charts([session("VIRTUAL", 0, "1")], 120, NOW+600))
    text = console.export_text()
    assert "暂无真实数据" in text and "起点待对齐" in text


def baseline(offset, trades, *, total="1"):
    s = session("VIRTUAL", offset, total)
    s.update(strategy="M1", session_id=f"baseline-{offset}", orders=[{
        "order_id": str(i), "status": "VIRTUAL_SUCCESS", "simulated_quantity": "10",
        "simulated_cost": "1", "simulated_pnl": value,
        "quote": {"price": price, "side": "BUY", "outcome": "YES"}}
        for i, (price, value) in enumerate(trades)])
    return s


def test_price_buckets_count_session_net_not_profitable_trades_or_whole_session():
    rows = [baseline(0, [(".01", "2"), (".09", "-3"), (".10", "4")], total="3"),
            baseline(300, [(".05", "3"), (".05", "-2"), (".19", "-1")], total="0"),
            baseline(600, [(".06", None), (".06", "5")], total=None),
            baseline(900, [(".10", "0"), (".20", "100"), (".009", "100")])]
    # Multiple trades count once per band/session; the first band's loss
    # stays a loss even when the whole session is profitable. Zero is no win.
    assert price_bucket_sessions(rows) == [(1, 3), (1, 3)]
    rows[2]["orders"][0]["simulated_pnl"] = "-1"
    assert price_bucket_sessions(rows) == [(2, 3), (1, 3)]


def test_price_buckets_ignore_skipped_unfilled_sell_and_duplicate_orders():
    s = baseline(0, [(".09", "1"), (".12", "1"), (".12", "1"), (".12", "1")])
    s["orders"].append(s["orders"][0])
    s["orders"][1]["status"] = "SKIPPED"
    s["orders"][2]["simulated_quantity"] = "0"
    s["orders"][3]["quote"]["side"] = "SELL"
    assert price_bucket_sessions([s]) == [(1, 1), (0, 0)]


def test_real_buckets_use_actual_fill_price_and_never_count_ack_as_trade():
    s = session("REAL", 0, "2")
    s.update(strategy="M1", session_id="real", orders=[{
        "order_id": "receipt", "status": "ACK", "post_started": True,
        "confirmed_quantity": None, "confirmed_cost": None,
        "quote": {"price": ".15", "side": "BUY", "outcome": "YES"}}])
    assert price_bucket_sessions([s]) == [(0, 0), (0, 0)]
    s["pm_account"] = {"settled_pnl": "2", "trades": [{
        "id": "fill", "token_id": "token", "side": "BUY", "ts": NOW+1,
        "quantity": "10", "amount": ".9", "price": ".09", "outcome": "YES"}],
        "positions": [{"token_id": "token", "current_size": "0", "current_value": "0",
            "total_size": "10", "total_pnl": "2", "status": "CLOSED"}]}
    # Simulate a native receipt also knowing its quantity: it must still not
    # be added to the public fill or classified at the earlier quote price.
    s["orders"][0]["confirmed_quantity"] = "10"
    s["orders"][0]["confirmed_cost"] = ".9"
    assert price_bucket_sessions([s]) == [(1, 1), (0, 0)]
    s["pm_account"]["settled_pnl"] = None
    assert price_bucket_sessions([s]) == [(0, 1), (0, 0)]


@pytest.mark.parametrize("width", [80, 140])
def test_bucket_line_below_cumulative_uses_each_side_and_common_start(width):
    old = baseline(0, [(".01", "1000")], total="1000")
    current = baseline(300, [(".01", "1"), (".10", None)], total=None)
    real = session("REAL", 300, None)
    console = Console(width=width, file=io.StringIO(), record=True)
    console.print(comparison_charts([old, current, real], width, NOW+900))
    text = console.export_text()
    lines = text.splitlines()
    caption = next(i for i, line in enumerate(lines) if "对照区间累计" in line)
    assert any("0.01–0.09：0/0" in line for line in lines[caption+1:])
    assert "0.01–0.09：1/1" in text and "0.10–0.19：0/1" in text
    assert "1000" not in text and "待定仅计分母" in text
