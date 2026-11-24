import asyncio
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from m12_follow import trial


EPOCH = 1800000000


def simulated_ledger(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE opportunities(id TEXT PRIMARY KEY);
        CREATE TABLE taker_attempts(seq INTEGER PRIMARY KEY,order_id TEXT UNIQUE,started INTEGER,state TEXT);
        CREATE TABLE taker_trade_evidence(trade_id TEXT PRIMARY KEY,order_id TEXT,status TEXT,economics TEXT);
    """)
    return db


def test_fixed_two_market_eligibility_and_restart(monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(trial, "time", SimpleNamespace(time=lambda: clock.now))
    source = SimpleNamespace(read=lambda: "LIVE_INTENT")
    for offset, allowed_index in ((-1, None), (0, 0), (299.9, 0), (300, 1), (599.9, 1), (600, None)):
        clock.now = EPOCH + offset
        # Drop all process state between checks. The published first epoch is immutable.
        window = trial.TwoMarketWindow(EPOCH)
        for i in range(-1, 4):
            reader = window.scoped_reader(source, f"btc-updown-5m-{EPOCH + 300*i}")
            assert reader.read() == ("LIVE_INTENT" if i == allowed_index else None)
    with pytest.raises(ValueError):
        trial.TwoMarketWindow(EPOCH + 1)


@pytest.mark.asyncio
async def test_no_fills_or_outside_orders_exit_without_network(tmp_path):
    window = trial.TwoMarketWindow(EPOCH)
    directory = tmp_path / "m421-ledgers"
    directory.mkdir()
    for index, state in ((0, "UNSENT"), (1, "TERMINAL"), (2, "UNKNOWN")):
        path = directory / f"btc-updown-5m-{EPOCH + 300*index}.sqlite3"
        with simulated_ledger(path) as db:
            db.execute("INSERT INTO taker_attempts(order_id,started,state) VALUES('owned',1,?)", (state,))
    result = await window.finish(tmp_path, None, None, None, asyncio.Event(), None)
    assert result["status"] == "TWO_MARKET_WINDOW_CLOSED"
    assert result["remaining_orders"] == {}
    assert [row["post_count"] for row in result["market_results"].values()] == [0, 1]
    assert all(row["confirmed_fill_count"] == 0 for row in result["market_results"].values())
    saved = json.loads((tmp_path / f"two-market-trials/{EPOCH}.json").read_text())
    assert saved["market_results"] == result["market_results"]


def test_two_market_report_known_fills_unconfirmed_and_missing(tmp_path):
    window = trial.TwoMarketWindow(EPOCH)
    first, second = window.slugs()
    directory = tmp_path / "m421-ledgers"
    directory.mkdir()
    path = directory / f"{first}.sqlite3"
    with simulated_ledger(path) as db:
        db.executemany("INSERT INTO opportunities VALUES(?)", [("id1",), ("id2",)])
        db.executemany("INSERT INTO taker_attempts(order_id,state,started) VALUES(?,?,?)", [
            ("done", "TERMINAL", 1), ("partial", "SETTLING", 1), ("unknown", "UNKNOWN", 1),
            ("veto", "UNSENT", 1), ("signed", "PREPARED", 0)])
        economics = json.dumps({"size": "1.50", "principal_usdc": "1.125", "fee_usdc": "0.005"})
        db.executemany("INSERT INTO taker_trade_evidence VALUES(?,?,?,?)", [
            (key, order, state, economics) for key, order, state in [
                ("d1", "done", "CONFIRMED"), ("d2", "done", "CONFIRMED"),
                ("p1", "partial", "CONFIRMED"), ("p2", "partial", "MINED"),
                ("p3", "partial", "FAILED")]])
    metrics, pending = window.market_results(tmp_path)
    assert metrics[first] == {"ledger_status": "PRESENT", "intent_count": 2, "post_count": 3,
        "unknown_post_count": 1, "confirmed_order_count": 2, "confirmed_fill_count": 3,
        "confirmed_shares": "4.50", "confirmed_cost_with_fees_usdc": "3.390",
        "unconfirmed_order_count": 2}
    assert pending == {path: ["partial", "unknown"]}
    assert metrics[second]["ledger_status"] == "MISSING"
    assert all(v is None for k, v in metrics[second].items() if k != "ledger_status")
    assert not (directory / f"{second}.sqlite3").exists()


@pytest.mark.asyncio
async def test_expired_startup_terminates_even_if_official_clock_is_unavailable(tmp_path, monkeypatch):
    from polymaker import m4_live_startup

    wall = SimpleNamespace(now=EPOCH + 599)
    calls = []

    async def offline():
        calls.append("public clock")
        raise httpx.ConnectTimeout("offline mock")

    async def retry_window_ends(_):
        wall.now += 1

    monkeypatch.setattr(m4_live_startup, "time", SimpleNamespace(time=lambda: wall.now))
    startup = m4_live_startup.LiveStartup(tmp_path, SimpleNamespace(clock=offline),
                                          SimpleNamespace(inhibited=lambda: False), asyncio.Event())
    startup.trial_end = trial.TwoMarketWindow(EPOCH).end
    startup.pause = retry_window_ends
    assert await startup.prepare(None, None, None) is None
    assert calls == ["public clock"]
    assert not (tmp_path / "ledgers").exists()
