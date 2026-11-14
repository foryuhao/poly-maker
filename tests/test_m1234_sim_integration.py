"""RW05 interactions and strict version boundaries; no live order transport."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from m1234_fixtures import fixture_events
from polymaker.m1234_sim.engine import Engine, market_meta
from polymaker.m1234_sim.events import Recorder
from polymaker.m1234_sim.store import read_audit
from test_m1234_sim_core import broker, intent, sell
from test_m1234_sim_engine import policy_setup
from test_m1234_sim_ticks import change, fresh, initial, request, result, send


async def test_clock_control_records_both_input_queue_watermarks_before_attribution(tmp_path, monkeypatch):
    from polymaker.m1234_sim import runner

    e = await initial(tmp_path / "engine")
    orders = [b.submit(intent()) for b in e.brokers.values()]
    await send(e, "RESOURCE", .1)
    await send(e, "FRAME", .2, raw={
        "event_type": "book", "market": e.meta.condition_id, "asset_id": "1",
        "timestamp": str((e.test_wall + .2) * 1000),
        "bids": [{"price": ".49", "size": "10"}], "asks": [{"price": ".52", "size": "80"}],
    })
    queue = asyncio.Queue(2048)
    queue.put_nowait(("FRAME", {"received_mono": .22}))
    capture = SimpleNamespace(m1_pending_received=[.23])
    record = Recorder(tmp_path / "control.gz")
    record.seq = e.seq  # isolated control recorder continues this artificial stream
    run = runner.LiveRun.__new__(runner.LiveRun)
    run.active_input = (e, capture, queue)
    run.recorders = {e.directory.name: record}
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: 2.0,
                                                       time=lambda: e.test_wall + 2))
    try:
        row = await run.record(e, "CLOCK", official_clock=e.official_clock)
        assert row["pending"] == [.22, .23]
        assert all(b.attribution_frontier < .2 for b in e.brokers.values())
        await send(e, "FRAME", 2.01, received_mono=.22, received_wall=e.test_wall + .22,
                   pending=[.22, .23], raw={
                       "event_type": "last_trade_price", "market": e.meta.condition_id,
                       "asset_id": "1", "price": ".50", "size": "85", "side": "SELL",
                       "trade_id": "received-before-clock", "timestamp": str((e.test_wall + .21) * 1000),
                   })
        assert all(o.filled == 5 and not o.uncertain for o in orders)
    finally:
        await e.close()
        record.close()


async def test_stale_tick_result_and_post_arrival_at_same_time_cannot_accept_invalid_price(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, .005)
        orders = [b.submit(intent(price=".095")) for b in e.brokers.values()]
        old = await request(e, .02)
        await change(e, .055, old=".001", new=".01")
        arrival = next(at for at, _, kind, _ in next(iter(e.brokers.values())).timers if kind == "LIVE")
        await result(e, old, arrival)
        assert e.tick_verification.stats["stale_results"] == 1
        assert all(o.state == "REJECTED" and o.reject_reason == "PRICE_INCREMENT" for o in orders)
        await send(e, "FRAME", .12, raw={
            "event_type": "last_trade_price", "market": e.meta.condition_id, "asset_id": "1",
            "price": ".09", "size": "100", "side": "SELL", "trade_id": "late-after-reject",
            "timestamp": str((e.test_wall + .12) * 1000),
        })
        assert all(o.filled == o.reserved == 0 for o in orders)
        new = await request(e, .13)
        await fresh(e, .14)
        await result(e, new, .15)
        assert e.tick_verification.state == "VERIFIED"
    finally:
        await e.close()


async def test_unknown_m1_retry_keeps_first_risk_when_second_post_rejects_precision(tmp_path):
    feed, clock, b, p, _, tick = await policy_setup(tmp_path, price=".095", fault="UNKNOWN")
    try:
        feed.books["1"] = replace(feed.books["1"], tick=D(".001"))
        feed.tick_verified["1"] = D(".001")
        b.update_books(feed.books)
        for at in [0, .01, .21, .22, .33, .34]:
            await tick(at)
        assert len(b.orders) == 2
        first, second = list(b.orders.values())
        clock.now = .35
        b.advance(.35)
        feed.books["1"] = replace(feed.books["1"], tick=D(".01"),
                                   bids=((D(".09"), D(80)),), asks=((D(".10"), D(80)),))
        feed.tick_verified["1"] = D(".01")
        b.update_books(feed.books)
        b.advance(.45)
        b.trade(sell("85", price=".09"), "first-accepted-only", source_mono=.4)
        for at in [.54, .55, .7]:
            await tick(at)
        assert first.client == "UNKNOWN" and first.filled == 5
        assert second.state == "REJECTED" and second.client == "PRECISION_REJECT"
        assert second.filled == second.reserved == 0 and len(b.orders) == 2
        assert len(p.e.ledger.uncertain_posts()) == 1
        assert b.account("M1-test").reserved == first.reserved
        assert first.reserved > 0 and p.e.ledger.committed() >= first.reserved
    finally:
        await p.close()


def test_tick_change_during_cancel_does_not_remove_existing_live_order_without_fact():
    b, _ = broker()
    book = replace(b.books["1"], tick=D(".001"), bids=((D(".095"), D(80)),),
                   asks=((D(".10"), D(80)),))
    b.update_books({**b.books, "1": book})
    order = b.submit(intent(price=".095"))
    b.advance(.1)
    b.advance(.15)
    b.cancel(order.id)
    b.advance(.2)
    b.update_books({**b.books, "1": replace(book, tick=D(".01"))})
    b.advance(.25)
    b.trade(sell("85", price=".09"), "cancel-in-flight", source_mono=.25)
    assert order.filled == 5 and order.state == "CANCEL_IN_FLIGHT"
    b.advance(.36)
    assert order.state == "CANCELLED" and order.reserved == 0


@pytest.mark.parametrize("corruption,expected", [
    ("schema", "INPUT_SCHEMA_VERSION"), ("model", "MODEL_OR_INPUT_VERSION"),
    ("missing_identity", "IMPLEMENTATION_IDENTITY_INCOMPLETE"),
    ("wrong_hash", "IMPLEMENTATION_VERSION_MISMATCH"),
])
async def test_input_and_source_identity_are_enforced_without_empty_map_bypass(tmp_path, corruption, expected):
    event = deepcopy(fixture_events()[0])
    if corruption == "schema":
        event["schema"] = 1
    elif corruption == "model":
        event["definition"]["model"] = "m1234-queue-v1"
    elif corruption == "missing_identity":
        event["definition"]["settings"]["implementation"] = {}
    else:
        key = next(iter(event["definition"]["settings"]["implementation"]))
        event["definition"]["settings"]["implementation"][key] = "0" * 64
    e = Engine(tmp_path)
    with pytest.raises(ValueError, match=expected):
        await e.ingest(event)
    await e.close()
    assert not list(read_audit(e.store.path))


async def test_failed_tick_finalization_cannot_leave_the_new_public_socket_running(tmp_path, monkeypatch):
    from polymaker.m1234_sim import runner

    tape = fixture_events()
    created = []

    class Feed:
        def __init__(self, meta, queue):
            self.stop_event = asyncio.Event()
            self.failure = None
            self.m1_pending_received = []
            self.task = None
            created.append(self)

        async def run(self):
            self.task = asyncio.current_task()
            await asyncio.Event().wait()

    class API:
        async def ticks(self, tokens):
            return tape[0]["definition"]["ticks"]

    run = runner.LiveRun.__new__(runner.LiveRun)
    run.directory, run.cfg, run.api = tmp_path, tape[0]["definition"]["settings"], API()
    run.cfg["writer_queue_records"], run.cfg["input_queue_frames"] = 8192, 2048
    run.stop, run.stop_at = asyncio.Event(), runner.time.monotonic() + 30
    run.engines, run.recorders, run.pending = [], {}, []
    run.active_input, run.tick_task = None, None
    now = runner.time.monotonic()
    run.clock = [tape[0]["definition"]["official_clock"][0], now, now]
    run.capacity = lambda: None
    run.log = lambda *a, **k: None
    run.update_report = lambda: None

    async def refresh(engine):
        await asyncio.sleep(0)
        run.stop.set()

    async def fail_finalization(engine):
        raise OSError("ARTIFICIAL_TICK_RESULT_WRITE_FAILURE")

    run.refresh = refresh
    run.finish_tick_request = fail_finalization
    monkeypatch.setattr(runner, "CaptureFeed", Feed)
    try:
        with pytest.raises(OSError, match="TICK_RESULT_WRITE_FAILURE"):
            await run.market(market_meta(tape[0]["definition"]["market"]))
        assert created[0].stop_event.is_set() and created[0].task.done()
        assert created[0].task.cancelled()
    finally:
        if created and created[0].task is not None:
            created[0].task.cancel()
            await asyncio.gather(created[0].task, return_exceptions=True)
        for e in run.engines:
            await e.close()
        for record in run.recorders.values():
            record.close()


async def test_early_pending_tick_recovery_preserves_monotonic_clock(tmp_path, monkeypatch):
    import gzip
    import json

    from polymaker.m1234_sim import runner
    from polymaker.m1234_sim.events import encode, read_events
    from polymaker.m1234_sim.report import read_state

    e = await initial(tmp_path / "online")
    await change(e, .001)
    await request(e, .001)
    prefix = deepcopy(e.test_tape)
    await e.close()
    tape = tmp_path / "early-pending.gz"
    with gzip.open(tape, "wt") as output:
        for row in prefix:
            output.write(encode(row) + "\n")
    monkeypatch.setattr(runner, "output_path", lambda p: p)
    monkeypatch.setattr(runner, "VirtualPublicAPI", lambda: pytest.fail("offline recovery queried REST"))
    recovered = await runner.recover(tape, tmp_path / "recovered")
    assert recovered["status"] == "PASS"
    events = list(read_events(tmp_path / "recovered/input.jsonl.gz"))
    assert events[:len(prefix)] == json.loads(encode(prefix))
    assert all(row["available_mono"] == .001 for row in events[len(prefix):])
    assert not any(row["kind"] == "TICK_RESULT" for row in events)
    state = read_state(tmp_path / "recovered/simulation.sqlite3")
    assert state["tick_verification"]["active_request"]["request_id"]
    assert state["tick_verification"]["state"] == "UNVERIFIED"
    assert any(gap["reason"] == "RESTART_MISSING_INTERVAL_UNCERTAIN" for gap in state["coverage"]["gaps"])
