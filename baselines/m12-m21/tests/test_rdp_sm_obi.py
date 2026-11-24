"""OBI invariants; public feed inputs are injected at the socket boundary."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from polymaker.config import RDPObiConfig, RDPSMConfig
from polymaker.rdp_sm import _market
from polymaker.rdp_sm_marketdata import SessionFeed
from polymaker.rdp_sm_network import NetworkHealth
from polymaker.rdp_sm_normal import normal_quotes
from polymaker.rdp_sm_rebalance import rebalance_quotes
from polymaker.rdp_sm_recovery import preflight_pause, retry_read
from polymaker.rdp_sm_signal import Episodes, Signal, evaluate
from test_rdp_sm_m1 import CID, SLUG, START, Venue


def feed_fixture():
    v = Venue()
    meta, _ = _market(v.raw, SLUG)
    clock = SimpleNamespace(now=0.0)
    feed = SessionFeed(
        meta, RDPObiConfig(), monotonic=lambda: clock.now, wall=lambda: START + 20 + clock.now
    )
    feed.verify_ticks({t: v.ticks[t] for t in ("1", "2")})
    feed.connect()
    rows = [
        {
            "event_type": "book",
            "market": CID,
            "asset_id": t,
            "timestamp": str((START + 20) * 1000),
            "bids": [{"price": ".50" if t == "1" else ".49", "size": "80" if t == "1" else "20"}],
            "asks": [{"price": ".52" if t == "1" else ".51", "size": "20" if t == "1" else "80"}],
        }
        for t in ("1", "2")
    ]
    feed.apply(rows)
    return feed, clock, rows


@pytest.mark.parametrize(
    "quantities,label",
    [
        ((65, 35, 35, 65), "VALID_YES"),
        ((35, 65, 65, 35), "VALID_NO"),
        ((64, 36, 36, 64), "NONE"),
        ((1, 1, 1, 1), "NONE"),
    ],
)
def test_l1_threshold_boundaries(quantities, label):
    feed, _, _ = feed_fixture()
    snapshot = feed.snapshot()
    by, ay, bn, an = quantities
    yes = replace(snapshot.yes, bids=((Decimal(".5"), by),), asks=((Decimal(".52"), ay),))
    no = replace(snapshot.no, bids=((Decimal(".49"), bn),), asks=((Decimal(".51"), an),))
    assert evaluate(replace(snapshot, yes=yes, no=no)).label == label


@pytest.mark.parametrize("quantity", [True, False, 0, -1, "NaN", "Infinity", None])
def test_bad_l1_never_becomes_none(quantity):
    feed, _, _ = feed_fixture()
    snap = feed.snapshot()
    assert (
        evaluate(replace(snap, yes=replace(snap.yes, bids=((Decimal(".5"), quantity),)))).label
        == "INVALID"
    )


def test_duplicate_book_refreshes_receipt_without_manufacturing_signal():
    feed, clock, rows = feed_fixture()
    seq = feed.sequence
    clock.now = 0.09
    feed.apply(rows)
    feed.apply({"event_type": "last_trade_price", "market": CID, "asset_id": "1"})
    assert feed.sequence == seq + 1 and feed.snapshot().yes.received_mono == .09
    assert feed.signal_event == 0
    feed.apply(
        {
            "event_type": "tick_size_change",
            "market": CID,
            "asset_id": "1",
            "old_tick_size": ".01",
            "new_tick_size": ".001",
        }
    )
    assert feed.snapshot().reason == "WS_TICK_UNVERIFIED"
    assert feed.snapshot().yes.received_mono == .09
    clock.now = 0.101
    assert not feed.snapshot().valid


@pytest.mark.parametrize("reason", ["WS_NOT_CONNECTED", "WS_DISCONNECTED", "WS_RECONNECT_REQUIRED"])
def test_preflight_connection_loss_pauses_until_rebuilt(reason):
    from polymaker.rdp_sm import SessionStatus, _Issue

    assert preflight_pause({"checks": [{"name": "freshness", "outcome": "BLOCKED", "detail": reason}]})
    assert retry_read(_Issue(SessionStatus.BLOCKED, reason))
    assert not retry_read(_Issue(SessionStatus.BLOCKED, "WS_MARKET_IDENTITY"))


def test_ws_root_failure_survives_disconnect_and_reconnect_diagnostics():
    feed, _, rows = feed_fixture()
    rows[0]["bids"][0]["size"] = "-1"
    with pytest.raises(ValueError, match="WS_LEVEL_INVALID"):
        feed.apply(rows)
    feed.invalidate("WS_DISCONNECTED")
    feed.invalidate("WS_RECONNECT_REQUIRED")
    diagnostic = feed.diagnostics()
    assert diagnostic["reason"] == "WS_RECONNECT_REQUIRED"
    assert diagnostic["last_failure"]["reason"] == "WS_LEVEL_INVALID"
    assert diagnostic["failure_count"] == 1 and not feed.snapshot().valid


def test_transport_diagnostics_exclude_credentials():
    feed, _, _ = feed_fixture()
    feed.record_failure(RuntimeError("proxy https://user:secret@example.invalid failed"))
    diagnostic = str(feed.diagnostics())
    assert "RuntimeError" in diagnostic and "secret" not in diagnostic and "user:" not in diagnostic


async def test_market_text_heartbeat_repeats_and_stops_with_connection(monkeypatch):
    feed, _, _ = feed_fixture()
    sent, options, closed = [], [], []
    enough = asyncio.Event()
    monkeypatch.setattr("polymaker.rdp_sm_marketdata.WS_HEARTBEAT_SEC", 0.01)

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            closed.append(True)

        async def send(self, payload):
            sent.append(payload)
            if sent.count("PING") >= 3:
                enough.set()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Future()

    def connect(*args, **kwargs):
        options.append(kwargs)
        return Socket()

    monkeypatch.setattr("polymaker.rdp_sm_marketdata.websockets.connect", connect)
    task = asyncio.create_task(feed._connection())
    try:
        await asyncio.wait_for(enough.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    count = sent.count("PING")
    await asyncio.sleep(0.03)
    assert sent.count("PING") == count and count >= 3
    assert options[0]["close_timeout"] == 1.0
    assert closed and feed.ws is None


@pytest.mark.parametrize(
    "fault", ["token", "market", "side", "boolean", "nan", "partial"]
)
def test_bad_frame_invalidates_entire_pair_and_delta_cannot_recover(fault):
    feed, clock, _ = feed_fixture()
    clock.now = 0.01
    ch = {"asset_id": "1", "price": ".50", "size": "90", "side": "BUY"}
    row = {
        "event_type": "price_change",
        "market": CID,
        "timestamp": str((START + 20) * 1000),
        "price_changes": [ch],
    }
    if fault == "token":
        ch["asset_id"] = "3"
    if fault == "market":
        row["market"] = "wrong"
    if fault == "side":
        ch["side"] = "BID"
    if fault == "boolean":
        ch["size"] = True
    if fault == "nan":
        ch["size"] = "NaN"
    if fault == "cross":
        ch["price"] = ".53"
    if fault == "top":
        ch["best_bid"] = ".49"
    if fault == "partial":
        row["price_changes"].append({"asset_id": "2"})
    with pytest.raises(ValueError):
        feed.apply(row)
    assert not feed.snapshot().valid
    row = {
        "event_type": "price_change",
        "market": CID,
        "timestamp": str((START + 20) * 1000),
        "price_changes": [{"asset_id": "1", "side": "BUY", "price": ".5", "size": "100"}],
    }
    with pytest.raises(ValueError):
        feed.apply(row)
    feed.connect()
    feed.apply(row)
    assert feed.connected and feed.snapshot().reason == 'WS_REBUILD_REQUIRED'


def test_empty_opposite_book_allows_repair_without_obi_or_favorite():
    feed, _, rows = feed_fixture()
    rows[0] = {**rows[0], "bids": [], "asks": []}
    feed.apply(rows)
    assert evaluate(feed.snapshot()).label == "INVALID"
    snapshot = feed.snapshot(repair_token="2")
    assert snapshot.valid
    qs = rebalance_quotes(feed.meta, snapshot, Decimal(5), Decimal(0), "w", RDPSMConfig())
    assert [(q.outcome, q.price, q.size) for q in qs] == [("NO", 0.49, 5)]


@pytest.mark.parametrize("direction,price,size", [("YES", 0.50, 5), ("NO", 0.49, 7)])
def test_only_signal_side_uses_its_bid_and_original_skew(direction, price, size):
    feed, _, _ = feed_fixture()
    evidence = {}
    qs = normal_quotes(
        feed.meta, feed.snapshot(), Decimal(2), Decimal(0), "w", RDPSMConfig(), direction,
        sigma=0.01, evidence=evidence,
    )
    assert [(q.outcome, q.price, q.size) for q in qs] == [(direction, price, size)]
    opposite = "no" if direction == "YES" else "yes"
    assert Decimal(evidence[f"planned_{opposite}_size"]) == 0
    assert evidence["order_policy"] == "SIGNAL_SIDE_ONLY"


def test_low_price_signal_order_needs_only_share_minimum():
    feed, _, _ = feed_fixture()
    snapshot = feed.snapshot()
    snapshot = replace(
        snapshot,
        yes=replace(snapshot.yes, bids=((Decimal('.87'), 80),), asks=((Decimal('.88'), 20),)),
        no=replace(snapshot.no, bids=((Decimal('.12'), 20),), asks=((Decimal('.13'), 80),)),
    )
    quotes = normal_quotes(feed.meta, snapshot, Decimal(0), Decimal(0), 'w', RDPSMConfig(), 'YES', sigma=0.01)
    assert [(q.outcome, q.price, q.size) for q in quotes] == [('YES', .87, 5)]
    quotes = normal_quotes(feed.meta, snapshot, Decimal(0), Decimal(0), 'w', RDPSMConfig(), 'NO', sigma=0.01)
    assert [(q.outcome, q.price, q.size) for q in quotes] == [('NO', .12, 5)]


@pytest.mark.parametrize('direction', ['YES', 'NO'])
def test_favorite_sizing_with_nonzero_target_does_not_reintroduce_opposite_leg(direction):
    feed, _, _ = feed_fixture()
    snapshot = feed.snapshot()
    snapshot = replace(
        snapshot,
        yes=replace(snapshot.yes, bids=((Decimal('.75'), 80),), asks=((Decimal('.76'), 20),)),
        no=replace(snapshot.no, bids=((Decimal('.24'), 20),), asks=((Decimal('.25'), 80),)),
    )
    evidence = {}
    quotes = normal_quotes(
        feed.meta, snapshot, Decimal(0), Decimal(0), 'w',
        RDPSMConfig(base_size=Decimal(7), target_delta=Decimal(1)), direction,
        sigma=0.01, now=snapshot.yes.source_time,
        official_time=snapshot.yes.source_time, evidence=evidence,
    )
    assert evidence['favorite_side'] == 'YES'
    assert len(quotes) == 1 and quotes[0].outcome == direction
    assert quotes[0].size == (7 if direction == 'YES' else 6)


def test_episode_invalid_reconnect_phase_switch_cannot_rearm_consumed_signal():
    tracker = Episodes()
    yes = Signal("VALID_YES", "1")
    tracker.observe(yes)
    claim = tracker.claim(yes)
    assert claim and tracker.claim(yes) is None
    tracker.consume(claim)
    tracker.release(claim)
    for s in (Signal("INVALID", "2"), Signal("VALID_YES", "3")):
        tracker.observe(s)
        assert tracker.claim(s) is None
    tracker.observe(Signal("NONE", "4"))
    tracker.observe(Signal("VALID_YES", "5"))
    assert tracker.claim(Signal("VALID_YES", "5"))


def test_aborted_claim_requires_a_new_real_event_and_opposite_invalidates_old_claim():
    t = Episodes()
    s = Signal("VALID_YES", "1")
    t.observe(s)
    c = t.claim(s)
    t.release(c)
    assert t.claim(s) is None
    t.observe(Signal("VALID_YES", "2"))
    c = t.claim(Signal("VALID_YES", "2"))
    t.observe(Signal("VALID_NO", "3"))
    with pytest.raises(ValueError):
        t.consume(c)


def test_network_isolated_slow_write_does_not_veto_future_signals():
    clock = SimpleNamespace(now=0.0)
    n = NetworkHealth(RDPObiConfig(), lambda: clock.now)
    assert not n.snapshot().allowed
    for _ in range(10):
        n.observe("GET", 0.1)
    assert n.snapshot().allowed and n.snapshot().write_proxy
    n.observe("POST", 0.6)
    for _ in range(100):
        n.observe("GET", 0.01)
    assert n.snapshot().allowed and n.snapshot().p95_ms == 600
    clock.now = 31
    for _ in range(10):
        n.observe("GET", 0.01)
    assert n.snapshot().allowed and n.snapshot().write_proxy
    assert not n.snapshot(450).allowed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_book_age_ms": float("nan")},
        {"max_pair_skew_ms": 0},
        {"signal_horizon_sec": 0.05},
        {"repeat_same_signal": True},
        {"network_sample_max_age_sec": 31},
    ],
)
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        RDPObiConfig(**kwargs)


def test_socket_receipt_age_and_queue_lag_are_independent_of_processing_time():
    feed, clock, rows = feed_fixture()
    clock.now = 0.2
    rows[0]["bids"][0]["size"] = "90"
    feed.apply(rows, received_mono=0.01, received_wall=START + 20.01)
    snapshot = feed.snapshot()
    assert snapshot.yes.applied_mono == 0.2 and snapshot.yes.received_mono == 0.01
    assert snapshot.reason == "WS_BOOK_STALE"


@pytest.mark.parametrize("fault", ["wall", "monotonic", "nan"])
def test_clock_fault_requires_rebuild(fault):
    feed, clock, rows = feed_fixture()
    clock.now = 0.01
    mono, wall = 0.01, START + 20.01
    if fault == "wall":
        wall += 3
    if fault == "monotonic":
        mono = -0.01
    if fault == "nan":
        mono = float("nan")
    with pytest.raises(ValueError):
        feed.apply(rows, received_mono=mono, received_wall=wall)
    assert not feed.snapshot().valid
