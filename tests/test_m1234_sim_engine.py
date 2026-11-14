"""Full-path replay, original-observer equivalence, admission and safety tests."""

import asyncio
import re
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D

import pytest

from m1234_fixtures import fixture_events
from polymaker.config import RDPM1Config, RDPObiConfig
from polymaker.m1_live import M1Market
from polymaker.m1_live_config import M1LiveConfig
from polymaker.m1_live_signal import M1LiveSignals
from polymaker.m2_config import M2Config
from polymaker.m234_signal import M234Feed
from polymaker.m1234_sim.audit import reconcile
from polymaker.m1234_sim.broker import SimBroker
from polymaker.m1234_sim.engine import Engine, market_meta
from polymaker.m1234_sim.events import Clock, digest
from polymaker.m1234_sim.policy import M1Policy
from polymaker.m1234_sim.report import summary
from polymaker.m1234_sim.signals import Signals
from polymaker.m1234_sim.store import read_audit
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_m1_strategy import m1_feed, seed_opportunity


async def drive(events, directory):
    engine = Engine(directory)
    for event in events:
        await engine.ingest(event)
    state = engine.snapshot()
    await engine.close()
    return state


async def test_full_tape_replays_identically_and_independently_reconciles(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "socket", lambda *a, **k: pytest.fail("offline replay accessed network")
    )
    events = fixture_events()
    one = await drive(events, tmp_path / "one")
    two = await drive(events, tmp_path / "two")
    assert digest(one) == digest(two)
    assert list(read_audit(tmp_path / "one/simulation.sqlite3")) == list(
        read_audit(tmp_path / "two/simulation.sqlite3")
    )
    assert reconcile(tmp_path / "one/simulation.sqlite3")["status"] == "PASS"
    report = summary(tmp_path / "one/simulation.sqlite3")
    assert len(report["accounts"]) == 10
    assert report["calibration"] == "UNCALIBRATED"
    assert not report["coverage"]["complete_window"]  # joined T20, never call full
    assert {a["strategy"] for a in report["accounts"] if a["opportunities"]} >= {
        "M1",
        "M2",
        "M3",
        "M4",
    }
    assert any(D(a["filled_shares"]) > 0 for a in report["accounts"])
    assert any(
        mark["horizons"]["1"]["status"] == "AVAILABLE"
        for account in report["accounts"]
        for mark in account["markouts"]
    )


def normalize(value):
    # A bijection preserves namespace multiplicity and every opportunity's
    # identity relationships; constant replacement would hide ID reuse.
    namespaces = {}

    def rename(match):
        name = match.group()
        if name not in namespaces:
            namespaces[name] = f"namespace-{len(namespaces) + 1}"
        return namespaces[name]

    def visit(item):
        if isinstance(item, str):
            return re.sub(r"(?:(?<=:)|^)(?:[0-9a-f]{32}|sim-m4-[^:]+)(?=:)", rename, item)
        if isinstance(item, dict):
            return {k: visit(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(v) for v in item]
        return item

    return visit(value)


def test_each_original_observer_matches_every_event_not_just_counts():
    tape = fixture_events()
    definition = tape[0]["definition"]
    meta = market_meta(definition["market"])
    clock = Clock()
    cfg1 = M1LiveConfig.model_validate(definition["settings"]["profiles"]["M1-virtual"])
    cfg234 = M2Config.model_validate(definition["settings"]["m234"])
    observed = []
    signals = Signals(meta, clock, cfg1, cfg234, lambda k, p: observed.append(deepcopy(p)))
    common = {
        "monotonic": clock.monotonic,
        "wall": clock.time,
        "normal_entry_mode": "m1_v1",
        "m1_config": RDPM1Config(record_events=False),
    }
    reference1 = SessionFeed(meta, RDPObiConfig(), **common)
    rows1 = []
    reference1.m1 = M1LiveSignals(
        reference1, signal_config=cfg1.signal, record=lambda r: rows1.append(deepcopy(r)) or True
    )
    reference1.m1.namespace = "sim-m1"
    reference234 = M234Feed(meta, RDPObiConfig(), research_settings=cfg234, **common)
    reference234.m1.namespace = "sim-m2"
    reference234.hub.legacy_record = lambda r: reference234.hub.emit("M2", r["event"], r)
    reference_rows = []
    signals.ticks(definition["ticks"])
    for feed in (reference1, reference234):
        feed.verify_ticks(definition["ticks"])
    for event in tape[1:]:
        kind = event["kind"]
        if kind in {"END", "SETTLEMENT"}:
            break
        clock.set(event["available_mono"], event["available_wall"])
        if kind == "CONNECT":
            signals.connect()
            reference1.connect()
            reference234.connect()
        elif kind == "FRAME":
            signals.apply(event["raw"], event["received_mono"], event["received_wall"])
            for feed in (reference1, reference234):
                feed.apply(
                    deepcopy(event["raw"]),
                    received_mono=event["received_mono"],
                    received_wall=event["received_wall"],
                )
        elif kind == "TIMER":
            signals.tick()
            reference1.m1.tick()
            reference1.m1.evaluate(reference1.snapshot())
            reference234.m1.tick()
            reference234.hub.observe_new(
                lambda now: {
                    "elapsed_lower": clock.wall - int(meta.slug.rsplit("-", 1)[1]),
                    "elapsed_upper": clock.wall - int(meta.slug.rsplit("-", 1)[1]),
                    "clock_fresh": True,
                    "planned_shutdown": False,
                }
            )
        elif kind == "GAP":
            signals.gap(event["reason"])
            reference1.invalidate(event["reason"])
            reference234.invalidate(event["reason"])
        signals.drain()
        for strategy in ("M2", "M3", "M4"):
            while reference234.hub.events[strategy]:
                reference_rows.append(reference234.hub.events[strategy].popleft())
        assert signals.m1.books == reference1.books
        assert signals.m234.books == reference234.books
        assert normalize([r for r in observed if r["strategy"] == "M1"]) == normalize(
            [
                {"strategy": "M1", "event": r["event"], "payload": r}
                for r in rows1
                if r["event"] != "EVENT_DECISION"
            ]
        )
        assert normalize([r for r in observed if r["strategy"] != "M1"]) == normalize(
            reference_rows
        )
    assert all(any(r["strategy"] == s for r in observed) for s in ("M1", "M2", "M3", "M4"))


async def policy_setup(tmp_path, *, price=".05", settings=None, fault=None):
    feed, clock = m1_feed()
    feed.m1 = M1LiveSignals(feed, signal_config=M1LiveConfig().signal)
    feed.m1.namespace = "sim-m1"
    feed.books["1"] = replace(
        feed.books["1"],
        bids=((D(price), D(80)),),
        asks=((D(price) + D(".01"), D(80)),),
        reported_bid=None,
        reported_ask=None,
    )
    trace = []
    b = SimBroker("test", lambda k, p: trace.append((k, p)))
    b.update_books(feed.books)
    p = M1Policy(
        feed, settings or M1LiveConfig(), b, "M1-test", tmp_path, lambda k, p: trace.append((k, p))
    )
    if fault:
        p.e.gateway.faults = [fault]
    seed_opportunity(feed)

    async def tick(at):
        clock.now = at
        # The artificial execution sample explicitly supplies fresh books.
        feed.books = {t: replace(book, received_mono=at) for t, book in feed.books.items()}
        for oid, client in b.advance(at, delivery=True):
            p.e.gateway.deliver(oid, client)
        await p.poll()
        for _ in range(10):
            await asyncio.sleep(0)

    return feed, clock, b, p, trace, tick


async def test_m1_uses_original_prepost_and_physical_unknown_retry_keeps_both_risks(tmp_path):
    feed, clock, b, p, trace, tick = await policy_setup(tmp_path, fault="UNKNOWN")
    try:
        assert type(p.market) is M1Market
        await tick(0)
        await tick(0.01)
        await tick(0.21)
        await tick(0.22)
        await tick(0.33)
        await tick(0.34)
        assert len(b.orders) == 2
        assert len(p.e.ledger.uncertain_posts()) == 1
        assert b.account("M1-test").reserved == D(2)
        assert p.e.ledger.committed() == D(2)
        b.advance(0.45)
        b.trade(
            {"asset_id": "1", "side": "SELL", "price": ".05", "size": "110", "trade_id": "both"},
            "direct",
        )
        assert sum(o.filled for o in b.orders.values()) == 30  # 80 external + 20 + 10
        assert b.account("M1-test").inventory["YES"] == 30
        b.check()
    finally:
        await p.close()


@pytest.mark.parametrize("maximum,expected", [(".09", 0), (".19", 1)])
async def test_m1_profiles_keep_their_price_filter(tmp_path, maximum, expected):
    _, _, b, p, _, tick = await policy_setup(
        tmp_path, price=".15", settings=M1LiveConfig(maximum_price=maximum)
    )
    try:
        await tick(0)
        await tick(0.01)
        assert len(b.orders) == expected
    finally:
        await p.close()


async def test_m1_explicit_reject_retry_and_admission_does_not_release_on_fill(tmp_path):
    _, _, b, p, _, tick = await policy_setup(tmp_path, fault="REJECT")
    try:
        await tick(0)
        await tick(0.01)
        await tick(0.21)
        await tick(0.22)
        assert len(b.orders) == 1  # invalid order is nonretryable
        assert next(iter(b.orders.values())).state == "REJECTED"
    finally:
        await p.close()


async def test_observer_failure_propagates_and_marks_incomplete(tmp_path):
    tape = fixture_events()
    e = Engine(tmp_path)
    await e.ingest(tape[0])
    e.signals.m234.hub.m4_v2.tick = lambda: 1 / 0
    with pytest.raises(RuntimeError, match="OBSERVER_FAILED"):
        await e.ingest({**tape[1], "kind": "TIMER"})
    assert e.coverage["gaps"] and not e.coverage["complete_window"]
    await e.close()


async def test_live_object_and_serialized_recording_have_identical_action_order(tmp_path):
    import json

    from polymaker.m1234_sim.events import encode

    events = fixture_events()
    recorded = [json.loads(encode(event)) for event in events]
    first = await drive(events, tmp_path / "direct")
    second = await drive(recorded, tmp_path / "serialized")
    assert digest(first) == digest(second)
    assert list(read_audit(tmp_path / "direct/simulation.sqlite3")) == list(
        read_audit(tmp_path / "serialized/simulation.sqlite3")
    )


def test_m4_rebuilt_queues_keep_distinct_opportunity_namespaces():
    tape = fixture_events()
    definition = tape[0]["definition"]
    signals = Signals(
        market_meta(definition["market"]),
        Clock(),
        M1LiveConfig(),
        M2Config.model_validate(definition["settings"]["m234"]),
        lambda *a: None,
    )
    first = signals.m234.hub.m4.pair("1")["BID"].identity("ROUND")
    signals.m234.hub.m4.invalidate("SOURCE_AGE", "1")
    second = signals.m234.hub.m4.pair("1")["BID"].identity("ROUND")
    assert first != second
