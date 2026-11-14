"""Trade history keeps submitted instructions, confirmed fills and pending PnL distinct."""

import copy
import fcntl
import io
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from decimal import Decimal

import pytest
from rich.console import Console

from polymaker.dashboard.collector import Collector, public_snapshot
from polymaker.dashboard.m1_trades import trade_pagination, trade_records
from polymaker.dashboard.model import summarize
from polymaker.dashboard.view import render
from polymaker.m1_virtual_state import VirtualStore
from test_dashboard import event, fill, header, placement, source
from test_dashboard_m1 import NOW, metadata, prepare


def snapshot_at(root):
    directory = prepare(root)
    store = VirtualStore(directory)
    store.issue({"slug": metadata(NOW)["slug"], "episode_id": "skip", "rf_id": "skip-rf",
        "ex_id": "skip-ex", "outcome": "NO"}, price=".8", quantity=0,
        eligible=False, reason="PRICE_OUTSIDE_TEST_BAND", elapsed=40, wall=NOW+40)
    store.close()
    collector = Collector(root, root / "derived")
    try:
        return public_snapshot(collector.poll(now=NOW+60))
    finally:
        collector.close()


def test_trade_records_use_stored_baseline_cost_and_pnl_exclude_skips_and_deduplicate(tmp_path):
    snapshot = snapshot_at(tmp_path)
    records = trade_records(snapshot["sessions"]*2)
    assert len(records) == 2
    current, old = records
    assert current["pnl"] is None and old["pnl"] == "9.0"
    assert current["cost"] == "1.0" and current["filled"] == "10"
    assert current["ts"] == NOW+30 and current["status"] == "待结算"
    assert old["status"] == "已结算"


def test_native_posts_do_not_become_fills_or_have_pnl_before_confirmation():
    h = header()
    h["normal_entry_mode"] = "m1_v1"
    rows = [event("sm_session", h), event("sm_placement_result", placement("native", "YES"), 1)]
    session = summarize("real", rows, [source()])
    record = trade_records([session])[0]
    assert record["post_type"] == "REAL" and Decimal(record["filled"]) == 0
    assert Decimal(record["cost"]) == 0 and record["pnl"] is None
    rows.append(event("sm_trade", fill("native", "YES", ".4"), 2))
    filled = trade_records([summarize("real", rows, [source()])])[0]
    assert Decimal(filled["filled"]) == 5 and Decimal(filled["cost"]) == 2
    assert filled["pnl"] is None
    session["orders"][0]["post_started"] = False
    assert not trade_records([session])


@pytest.mark.parametrize("width,height", [(80, 24), (140, 50)])
def test_trade_page_filters_history_and_shows_every_trade_across_pages(tmp_path, width, height):
    snapshot = snapshot_at(tmp_path)
    original = snapshot["sessions"][0]
    older = copy.deepcopy(original)
    older.update(session_id="older", market_start=NOW-6*3600)
    snapshot["sessions"].append(older)
    current = snapshot["sessions"][1]
    template = current["orders"][0]
    current["orders"] = [{**template, "order_id": f"order-{i}", "submitted_at": NOW+i+1}
                         for i in range(24)]
    records = trade_records(snapshot["sessions"][:2])
    limit, pages, _ = trade_pagination(len(records), height, 0)
    for page in range(pages):
        console = Console(width=width, file=io.StringIO(), record=True)
        console.print(render(snapshot, now=NOW+60, width=width, height=height,
                             hours=4, trade_view=True, archive_page=page, archive_type="VIRTUAL"))
        text = console.export_text()
        assert "M1 交易记录" in text and f"共 {len(records)} 笔" in text
        assert f"第 {page+1}/{pages} 页" in text
        assert "已结算基线 PnL +9.000" in text
        assert "虚拟" not in text and "足额成交" in text
        assert len(text.splitlines()) <= height
        for row in records[page*limit:(page+1)*limit]:
            from polymaker.dashboard.archive import clock

            assert clock(row["ts"])[11:19] in text


def test_terminal_switches_trade_page_paginates_and_returns_without_changing_snapshot(tmp_path):
    snapshot = snapshot_at(tmp_path)
    current = snapshot["sessions"][1]
    template = current["orders"][0]
    current["orders"] = [{**template, "order_id": f"order-{i}", "submitted_at": NOW+i+1}
                         for i in range(10)]
    for i in range(8):
        real = copy.deepcopy(current)
        real.update(session_id=f"real-{i}", post_type="REAL", market_start=NOW-i*300,
                    market_end=NOW-i*300+300, orders=[], known_pnl="0")
        snapshot["sessions"].append(real)
    output = tmp_path / "view"
    output.mkdir()
    path = output / "latest.json"
    path.write_text(json.dumps(snapshot))
    original = path.read_bytes()
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 120, 0, 0))
    child = subprocess.Popen([sys.executable, "-m", "polymaker.dashboard", "--output-dir", str(output)],
        stdin=slave, stdout=slave, stderr=slave, env={**os.environ, "TERM": "xterm-256color"})

    def expect(text):
        received = b""
        until = time.monotonic()+5
        while text.encode() not in received and time.monotonic() < until:
            if select.select([master], [], [], .1)[0]:
                received += os.read(master, 65536)
        assert text.encode() in received, received.decode(errors="replace")

    try:
        expect("POLYMAKER")
        for key, label in ((b"t", "M1 交易记录 · 真实"), (b"\x1b[C", "M1 交易记录 · 基线"),
                           (b"\x1b[B", "第 2/4 页"), (b"\x1bOA", "第 1/4 页"),
                           (b"h", "历史 8h"), (b"a", "M1 场次记录 · 基线"),
                           (b"\x1b[D", "M1 场次记录 · 真实"), (b"\x1b[B", "第 2/2 页"),
                           (b"\x1b[C", "M1 场次记录 · 基线"), (b"\x1b[B", "第 1/1 页"),
                           (b"\x1bOD", "M1 场次记录 · 真实"), (b"\x1b[B", "第 2/2 页"),
                           (b"t", "M1 交易记录 · 真实"), (b"\x1b[B", "第 1/1 页"),
                           (b"\x1bOC", "M1 交易记录 · 基线"), (b"\x1b[B", "第 2/4 页"),
                           (b"\x1b[D", "M1 交易记录 · 真实"), (b"\x1b[B", "第 1/1 页"),
                           (b"t", "基线持仓")):
            os.write(master, key)
            expect(label)
        os.write(master, b"q")
        assert child.wait(timeout=5) == 0
        assert path.read_bytes() == original
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)
