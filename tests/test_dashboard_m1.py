"""M1 dashboard reads public simulation data without turning it into real fills."""

import hashlib
import io
import json
import time
from decimal import Decimal

import pytest
from rich.console import Console

from polymaker.dashboard.archive import Archiver
from polymaker.dashboard.collector import Collector, public_snapshot
from polymaker.dashboard.model import summarize
from polymaker.dashboard.view import render
from polymaker.m1_virtual_state import VirtualStore
from test_dashboard import event, fill, header, placement, source, write_risk

NOW = int(time.time() // 300) * 300


def metadata(epoch):
    return {
        "slug": f"btc-updown-5m-{epoch}",
        "condition_id": "0x" + "a" * 64,
        "tokens": [{"token_id": "1", "outcome": "Up"}, {"token_id": "2", "outcome": "Down"}],
    }


def prepare(root, *, settled=True):
    directory = root / "research_outputs/m1_virtual"
    store = VirtualStore(directory)
    for epoch in (NOW - 300, NOW):
        store.add_market(metadata(epoch), 1)
        store.issue(
            {
                "slug": metadata(epoch)["slug"],
                "episode_id": f"op{epoch}",
                "rf_id": f"rf{epoch}",
                "ex_id": f"ex{epoch}",
                "outcome": "YES",
            },
            price=".1",
            quantity=10,
            eligible=True,
            reason=None,
            elapsed=30,
            wall=epoch + 30,
        )
    store.finish_market(
        metadata(NOW - 300)["slug"], full_window=True, counts={"RF_CONFIRMED": 1, "EX_CONFIRMED": 1}
    )
    if settled:
        store.settle(
            metadata(NOW - 300)["slug"],
            {
                "winner": "YES",
                "source": "https://gamma-api.polymarket.com/markets/slug/test",
                "observed_at": NOW,
            },
        )
    store.close()
    (directory / "latest.json").write_text(
        json.dumps(
            {
                "observed_at": NOW + 50,
                "runtime": {
                    "slug": metadata(NOW)["slug"],
                    "state": "OBSERVING",
                    "connected": True,
                    "signal_counts": {"RF_CONFIRMED": 3, "EX_CONFIRMED": 4},
                },
            }
        )
    )
    return directory


def test_m1_virtual_read_only_source_and_accounting_separation(tmp_path):
    directory = prepare(tmp_path)
    original = hashlib.sha256((directory / "state.sqlite3").read_bytes()).hexdigest()
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        snapshot = collector.poll(now=NOW + 60)
        old, current = snapshot["sessions"]
        assert snapshot["current_session_id"] == current["session_id"]
        assert current["strategy"] == "M1" and current["post_type"] == "VIRTUAL"
        assert current["post_types"] == {
            "VIRTUAL": {"YES": 1, "NO": 0},
            "REAL": {"YES": 0, "NO": 0},
        }
        assert current["filled_orders"] == current["submitted_orders"] == 0
        assert current["simulated_inventory"] == {"YES": "10", "NO": "0"}
        assert current["inventory"] == {"YES": "0", "NO": "0"}
        assert current["trading_pnl"] is None and current["simulated_pnl"] is None
        assert old["simulated_pnl"] == "9.0" and old["trading_pnl"] is None
        assert current["m1_components"] == {"RF": 3, "EX": 4}
        assert current["last_event_at"] >= NOW + 50
        assert hashlib.sha256((directory / "state.sqlite3").read_bytes()).hexdigest() == original
        public = public_snapshot(snapshot)
        assert public["sessions"][-1]["post_type"] == "VIRTUAL"
        archived = Archiver(collector).market(old, NOW + 60)
        assert json.loads((archived / "manifest.json").read_text())["status"] == "FINAL"
        assert "基线" in (archived / "summary.md").read_text()
        assert "VIRTUAL_SUCCESS" in (archived / "orders-fills.jsonl").read_text()
    finally:
        collector.close()


def test_m1_official_resolution_updates_archive_revision_without_zero_filling(tmp_path):
    directory = prepare(tmp_path, settled=False)
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        before = collector.poll(now=NOW + 60)
        assert before["sessions"][0]["simulated_pnl"] is None
        archiver = Archiver(collector)
        archiver.tick(before)
        store = VirtualStore(directory)
        store.settle(
            metadata(NOW - 300)["slug"],
            {"winner": "NO", "source": "public", "observed_at": NOW + 61},
        )
        store.close()
        after = collector.poll(now=NOW + 62)
        assert Decimal(after["sessions"][0]["simulated_pnl"]) == -1
        archiver.tick(after)
        revisions = list((tmp_path / "derived/archives").rglob("v0002/snapshot.json"))
        assert revisions and json.loads(revisions[0].read_text())["simulated_pnl"] == "-1.0"
    finally:
        collector.close()


@pytest.mark.parametrize("width,height", [(80, 24), (120, 50), (140, 24)])
def test_m1_view_has_only_m1_signals_typed_posts_and_no_pnl_attribution(tmp_path, width, height):
    prepare(tmp_path)
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        snapshot = public_snapshot(collector.poll(now=NOW + 60))
        console = Console(width=width, file=io.StringIO(), record=True)
        console.print(render(snapshot, now=NOW + 60, width=width, height=height))
        text = console.export_text()
        assert "M1" in text and "SIGNALS" in text and "RF / EX" in text
        assert "基线" in text and "真实" in text
        if height >= 30:
            assert "+9.000" in text
        assert "基线为指令成功" not in text
        assert "虚拟" not in text
        assert "分布" not in text and "退出归因" not in text
        assert "CA" not in text and "OTHER" not in text and "TP / SL" not in text
        console.print(render(snapshot, now=NOW + 60, width=width, height=height, archive_view=True))
        assert "M1 场次记录" in console.export_text()
    finally:
        collector.close()


def test_m1_real_attempts_remain_real_without_simulating_fills():
    h = header()
    h["normal_entry_mode"] = "m1_v1"
    unknown = placement("native", "YES")
    unknown["outcome"] = "UNKNOWN"
    rows = [
        event("sm_session", h),
        event("sm_placement_result", unknown, 1),
        event(
            "sm_m1_heartbeat",
            {
                "signal_counts": {"RF_CONFIRMED": 5, "EX_CONFIRMED": 7},
                "signals": {
                    "signals": 1,
                    "by_signal": [
                        {"id": "episode", "outcome": "YES", "state": "UNKNOWN", "posts": 2}
                    ],
                }
            },
            2,
        ),
    ]
    s = summarize("session-test", rows, [source()])
    assert s["post_type"] == "REAL" and s["real_submitted_orders"] == 2
    assert s["post_types"]["VIRTUAL"] == {"YES": 0, "NO": 0}
    assert s["post_types"]["REAL"] == {"YES": 2, "NO": 0}
    assert s["signal_counts"]["M1"] == 1 and s["filled_orders"] == 0
    assert s["m1_components"] == {"RF": 5, "EX": 7}
    assert [r["id"] for r in s["m1_signals"]] == ["episode"]
    assert s["known_pnl"] == "0" and s["trading_pnl"] is None
    rows.append(event("sm_trade", fill("native", "YES", ".4"), 3))
    assert summarize("session-test", rows, [source()])["filled_orders"] == 1


@pytest.mark.parametrize("width,height", [(80, 24), (140, 50)])
def test_all_session_signals_listed_and_skipped_zero_quantity_is_not_an_instruction(tmp_path, width, height):
    directory = prepare(tmp_path)
    store = VirtualStore(directory)
    for n, side, price, quantity, reason in (
        (2, "YES", ".05", 20, None),
        (3, "NO", ".80", 0, "PRICE_OUTSIDE_TEST_BAND"),
    ):
        store.issue(
            {"slug": metadata(NOW)["slug"], "episode_id": f"extra{n}",
             "rf_id": f"new-rf{n}", "ex_id": f"new-ex{n}", "outcome": side},
            price=price, quantity=quantity, eligible=reason is None,
            reason=reason, elapsed=n*30, wall=NOW+n*30,
        )
    store.close()
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        snapshot = public_snapshot(collector.poll(now=NOW + 100))
        session = snapshot["sessions"][-1]
        assert [r["outcome"] for r in session["m1_signals"]] == ["YES", "YES", "NO"]
        assert [r["quote"]["size"] for r in session["orders"]] == ["10", "20", "0"]
        console = Console(width=width, file=io.StringIO(), record=True)
        console.print(render(snapshot, now=NOW + 100, width=width, height=height))
        text = console.export_text()
        for index in (1, 2, 3):
            assert f"M1 #{index}" in text
        assert "已跳过" in text and "虚拟" not in text
        if height >= 34:
            orders = text.split("最近开仓指令 / 订单", 1)[1].split("RECENT EVENTS", 1)[0]
            assert "0.1" in orders and "0.05" in orders
            assert "0.80" not in orders and "SKIPPED" not in orders
    finally:
        collector.close()


def test_native_repeated_same_side_signals_remain_individual_rows():
    h = header()
    h["normal_entry_mode"] = "m1_v1"
    rows = [event("sm_session", h), event("sm_m1_heartbeat", {"signals": {
        "signals": 3, "by_signal": [
            {"id": identity, "outcome": side, "state": "SKIPPED", "posts": 0}
            for identity, side in (("first", "YES"), ("second", "YES"), ("third", "NO"))
        ]}}, 1)]
    summary = summarize("session-test", rows, [source()])
    assert [r["id"] for r in summary["m1_signals"]] == ["first", "second", "third"]
    assert summary["real_submitted_orders"] == 0


def test_virtual_and_native_pnl_are_never_aggregated_into_one_return(tmp_path):
    prepare(tmp_path)
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        snapshot = collector.poll(now=NOW + 60)
        h = header()
        h["normal_entry_mode"] = "m1_v1"
        real = summarize(
            "real-only",
            [
                event("sm_session", h),
                event("sm_placement_result", placement("yes-order", "YES"), 1),
                event("sm_placement_result", placement("no-order", "NO"), 2),
                event("sm_trade", fill("yes-order", "YES", ".4"), 3),
                event("sm_trade", fill("no-order", "NO", ".65"), 4),
            ],
            [source()],
        )
        assert Decimal(real["known_pnl"]) == Decimal("-.25")
        snapshot["sessions"].append(real)
        console = Console(width=140, file=io.StringIO(), record=True)
        console.print(render(snapshot, now=NOW + 60, width=140, height=50))
        text = console.export_text()
        assert "基线累计 PnL" in text and "真实累计 PnL" in text
        assert "-0.250" in text
        assert "累计基线 PnL" not in text and "累计真实已确认 PnL" not in text
        assert "+8.750" not in text
    finally:
        collector.close()


def test_native_m1_underscore_session_id_and_old_observer_cache_repair(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    sid = "m1_live_" + "a" * 32
    path = journal / f"rdp_sm_wallet_{sid}.jsonl"
    h = header()
    h.update(session_id=sid, normal_entry_mode="m1_v1")
    rows = [event("sm_session", h), event("sm_m1_heartbeat", {"signals": {"signals": 2}}, 1)]
    # Reuse the journal envelope but write its correct full identity.
    write_risk(path, rows)
    data = path.read_text().replace('"session-test"', json.dumps(sid))
    path.write_text(data)
    original = path.read_bytes()
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        old_id = "a" * 32
        collector.add_source(path, old_id, "risk")
        snapshot = collector.poll(now=NOW + 60)
        assert snapshot["current_session_id"] == sid
        assert snapshot["sessions"][0]["signal_counts"]["M1"] == 2
        assert not snapshot["sessions"][0]["issues"]
        assert path.read_bytes() == original
        assert collector.db.execute("SELECT sid FROM sources").fetchone()[0] == sid
    finally:
        collector.close()


def test_virtual_source_disappears_and_recovers_without_erasing_facts(tmp_path):
    directory = prepare(tmp_path)
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        before = collector.poll(now=NOW + 60)
        path = directory / "state.sqlite3"
        other = directory / "offline.sqlite3"
        path.rename(other)
        bad = collector.poll(now=NOW + 61)
        assert bad["error"] and "M1_SOURCE_UNAVAILABLE" in bad["sessions"][-1]["issues"]
        assert bad["sessions"][-1]["virtual_submitted_orders"] == 1
        other.rename(path)
        good = collector.poll(now=NOW + 62)
        assert (
            good["error"] is None and "M1_SOURCE_UNAVAILABLE" not in good["sessions"][-1]["issues"]
        )
        assert good["sessions"][0]["simulated_pnl"] == before["sessions"][0]["simulated_pnl"]
    finally:
        collector.close()


def test_component_window_keeps_latest_rf_when_recent_events_are_dominated_by_ex(tmp_path):
    from polymaker.dashboard.archive import clock
    from polymaker.dashboard.view import post_panel

    directory = prepare(tmp_path)
    store = VirtualStore(directory)
    slug = metadata(NOW)["slug"]
    store.event("RF_CONFIRMED", {"token_id": "1"}, slug, wall=NOW+1)
    for i in range(21):
        store.event("EX_CONFIRMED", {"token_id": "2"}, slug, wall=NOW+2+i)
    store.close()
    path = directory / "latest.json"
    producer = json.loads(path.read_text())
    producer["runtime"]["signal_counts"] = {"RF_CONFIRMED": 1, "EX_CONFIRMED": 21}
    path.write_text(json.dumps(producer))
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        current = collector.poll(now=NOW+60)["sessions"][-1]
        assert not any(e["kind"] == "RF_CONFIRMED" for e in current["recent_events"])
        assert current["m1_component_latest"] == {
            "RF": {"ts": NOW+1, "outcome": "YES"},
            "EX": {"ts": NOW+22, "outcome": "NO"},
        }
        console = Console(width=60, file=io.StringIO(), record=True)
        console.print(post_panel(current))
        text = console.export_text()
        assert "RF / EX" in text and "POST" not in text
        assert "YES" in text and "NO" in text and "21" in text
        assert clock(NOW+1)[11:19] in text and clock(NOW+22)[11:19] in text
    finally:
        collector.close()


def test_component_counts_survive_missing_heartbeat_fields_and_reset_only_for_new_market(tmp_path):
    directory = prepare(tmp_path)
    store = VirtualStore(directory)
    slug = metadata(NOW)["slug"]
    for kind, count in (("RF_CONFIRMED", 2), ("EX_CONFIRMED", 3)):
        for i in range(count):
            store.event(kind, {"token_id": "1"}, slug, wall=NOW+i+1)
    report = directory / "latest.json"
    producer = json.loads(report.read_text())
    producer["runtime"]["signal_counts"] = {"RF_CONFIRMED": 2, "EX_CONFIRMED": 3}
    report.write_text(json.dumps(producer))
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        assert collector.poll(now=NOW+60)["sessions"][-1]["m1_components"] == {"RF": 2, "EX": 3}
        for counter in (None, {}):
            producer["runtime"].pop("signal_counts", None)
            if counter is not None:
                producer["runtime"]["signal_counts"] = counter
            producer["observed_at"] += 1
            report.write_text(json.dumps(producer))
            assert collector.poll(now=NOW+62)["sessions"][-1]["m1_components"] == {"RF": 2, "EX": 3}
        store.event("RF_CONFIRMED", {"token_id": "1"}, slug, wall=NOW+63)
        assert collector.poll(now=NOW+64)["sessions"][-1]["m1_components"] == {"RF": 3, "EX": 3}
        store.add_market(metadata(NOW+300), 0)
        producer["runtime"] = {"slug": metadata(NOW+300)["slug"], "state": "OBSERVING"}
        producer["observed_at"] = NOW+301
        report.write_text(json.dumps(producer))
        sessions = collector.poll(now=NOW+301)["sessions"]
        assert sessions[-1]["m1_components"] == {"RF": 0, "EX": 0}
        assert sessions[-2]["m1_components"] == {"RF": 3, "EX": 3}
    finally:
        collector.close()
        store.close()


@pytest.mark.parametrize("width", [80, 140])
@pytest.mark.parametrize("kind,label", [("REAL", "真实"), ("VIRTUAL", "基线")])
def test_archive_separates_modes_and_puts_newest_market_first(tmp_path, width, kind, label):
    import copy

    prepare(tmp_path)
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        snapshot = public_snapshot(collector.poll(now=NOW+60))
        for s, phase in zip(snapshot["sessions"], ("BASE_OLD", "BASE_NEW"), strict=True):
            s["phase"] = phase
            real = copy.deepcopy(s)
            real.update(session_id="real-"+s["session_id"], post_type="REAL",
                        phase=phase.replace("BASE", "REAL"), known_pnl="0")
            snapshot["sessions"] = [*snapshot["sessions"], real]
        console = Console(width=width, file=io.StringIO(), record=True)
        console.print(render(snapshot, now=NOW+60, width=width, height=24,
                             archive_view=True, archive_type=kind))
        text = console.export_text()
        wanted, excluded = ("REAL", "BASE") if kind == "REAL" else ("BASE", "REAL")
        assert f"M1 场次记录 · {label}" in text
        assert text.index(wanted+"_NEW") < text.index(wanted+"_OLD")
        assert excluded+"_NEW" not in text and excluded+"_OLD" not in text
        assert "共 2 场" in text and "第 1/1 页" in text
        assert "[←]真实 [→]基线" in text
    finally:
        collector.close()
