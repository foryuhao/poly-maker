"""Late BA frames preserve current books and causal evidence within tolerance."""
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPBAConfig
from polymaker.rdp_sm_ba import BidAbsorption
from test_rdp_sm_ba import ba_feed, depth, execution_setup, trade
from test_rdp_sm_m1 import CID
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption, setup_exit
from test_rdp_sm_repair import env as env


def ordering_feed(mode, **settings):
    feed, clock, rows = ba_feed(max_book_age_ms=200, **settings)
    if mode == "exit":
        feed.opposite_ba = BidAbsorption(feed, feed.ba.cfg)
        feed.ba = None
    detector = feed.ba if mode == "normal" else feed.opposite_ba
    return feed, detector, clock, rows


def late_row(feed, stamp, kind):
    common = {"event_type": kind, "market": CID, "asset_id": "1", "timestamp": str(stamp)}
    if kind == "book":
        return common | {"bids": [{"price": ".48", "size": "999"}],
                         "asks": [{"price": ".49", "size": "999"}]}
    if kind == "price_change":
        return depth(feed, 999, price=".51") | {"timestamp": str(stamp)}
    if kind == "best_bid_ask":
        return common | {"best_bid": ".51", "best_ask": ".52"}
    return trade(feed, size="999", timestamp=str(stamp))


@pytest.mark.parametrize("mode", ["normal", "exit"])
@pytest.mark.parametrize("kind", ["book", "price_change", "last_trade_price", "best_bid_ask"])
@pytest.mark.parametrize("rollback", ["1", "10", "10.001"])
def test_tolerance_boundary_before_book_update_preserves_or_retires_claim(mode, kind, rollback):
    feed, detector, clock, _ = ordering_feed(mode)
    clock.now = .05
    absorption(feed, "1")
    clock.now = .06
    feed.apply(depth(feed, feed.books["1"].best_bid_size))
    signal = detector.evaluate(feed.snapshot())
    claim = detector.claim(signal)
    assert claim is not None
    book, ep = feed.books["1"], detector.selected
    before = asdict(ep)
    watermark = detector.source_times["1"]
    tops = dict(detector.tops)
    clock.now = .061
    feed.apply(late_row(feed, watermark * 1000 - D(rollback), kind))
    assert feed.books["1"] == book  # Price, quantities, source and receipt clocks.
    assert detector.source_times["1"] == watermark
    assert detector.tops == tops
    if D(rollback) <= 10:
        assert asdict(ep) == before and detector.current(claim)
        assert detector.active_reason(claim, feed.snapshot()) is None
        reason = "BA_EVENT_OUT_OF_ORDER_IGNORED"
    else:
        assert ep.invalid_reason == "BA_EVENT_OUT_OF_ORDER"
        assert not detector.current(claim)
        reason = "BA_EVENT_OUT_OF_ORDER"
    event_type = "depth" if kind in {"book", "price_change"} else kind
    diagnostic = detector.diagnostics()["last_rejections"][event_type + ":" + reason]
    assert D(str(diagnostic["rollback_ms"])) == D(rollback)
    assert diagnostic["out_of_order_tolerance_ms"] == 10


@pytest.mark.parametrize("mode", ["normal", "exit"])
@pytest.mark.parametrize("stage", ["depletion", "refill"])
def test_ignored_depth_cannot_manufacture_absorption(mode, stage):
    feed, detector, clock, _ = ordering_feed(mode)
    clock.now = .05
    feed.apply(trade(feed))
    if stage == "refill":
        feed.apply(depth(feed, 75))
    clock.now = .06
    feed.apply(depth(feed, feed.books["1"].best_bid_size))
    ep = detector.states["1"]
    before, book = asdict(ep), feed.books["1"]
    old = depth(feed, 75 if stage == "depletion" else 80)
    old["timestamp"] = str(detector.source_times["1"] * 1000 - 5)
    feed.apply(old)
    assert asdict(ep) == before and feed.books["1"] == book
    assert detector.evaluate(feed.snapshot()).direction is None
    clock.now = .07
    feed.apply(depth(feed, 80))
    assert (detector.evaluate(feed.snapshot()).direction == "YES") is (stage == "refill")


@pytest.mark.parametrize("mode", ["normal", "exit"])
def test_ignored_sell_cannot_start_an_episode(mode):
    feed, detector, clock, _ = ordering_feed(mode)
    clock.now = .02
    feed.apply(depth(feed, 80))
    feed.apply(trade(feed, timestamp=str(detector.source_times["1"] * 1000 - 5)))
    assert not detector.states
    assert detector.source_times["1"] == D(str(feed.books["1"].source_time))


def test_small_regressions_cannot_walk_the_watermark_backwards():
    feed, detector, clock, _ = ordering_feed("normal")
    clock.now = .05
    absorption(feed, "1")
    ep, watermark = detector.states["1"], detector.source_times["1"]
    for rollback in range(1, 12):
        feed.apply(depth(feed, 100) | {"timestamp": str(watermark * 1000 - rollback)})
        assert detector.source_times["1"] == watermark
        assert (ep.invalid_reason is None) is (rollback <= 10)
    assert ep.invalid_reason == "BA_EVENT_OUT_OF_ORDER"


@pytest.mark.parametrize("mode", ["normal", "exit"])
def test_late_frames_cannot_refresh_book_age_or_extend_ba_deadline(mode):
    feed, detector, clock, rows = ordering_feed(mode)
    clock.now = .05
    absorption(feed, "1")
    ep = detector.states["1"]
    original = asdict(ep)
    book = feed.books["1"]
    clock.now = .251
    feed.apply(late_row(feed, detector.source_times["1"] * 1000 - 1, "book"))
    assert feed.books["1"] == book and asdict(ep) == original
    assert feed.snapshot(ba_cfg=detector.cfg).reason == "WS_BOOK_STALE"
    feed.connect()
    assert not detector.source_times and not detector.states
    fresh = deepcopy(rows)
    for row in fresh:
        row["timestamp"] = str(feed.wall() * 1000)
    feed.apply(fresh)
    assert feed.snapshot(ba_cfg=detector.cfg).valid


@pytest.mark.parametrize("tolerance", [0, 5, 20])
def test_configured_tolerance_is_inclusive(tolerance):
    feed, detector, clock, _ = ordering_feed("normal", out_of_order_tolerance_ms=tolerance)
    clock.now = .05
    absorption(feed, "1")
    ep, watermark = detector.states["1"], detector.source_times["1"]
    feed.apply(depth(feed, 90) | {"timestamp": str(watermark * 1000 - tolerance)})
    assert ep.invalid_reason is None
    # Equal timestamps still apply in source/socket order, even with tolerance=0.
    assert feed.books["1"].best_bid_size == (90 if tolerance == 0 else 80)
    feed.apply(depth(feed, 95) | {"timestamp": str(watermark * 1000 - tolerance - 1)})
    assert ep.invalid_reason == "BA_EVENT_OUT_OF_ORDER"


def test_entry_and_exit_tolerances_are_independent_on_a_shared_book():
    feed, clock, _ = ba_feed(out_of_order_tolerance_ms=0)
    feed.opposite_ba = BidAbsorption(feed, RDPBAConfig(out_of_order_tolerance_ms=10))
    clock.now = .05
    absorption(feed, "1")
    book = feed.books["1"]
    feed.apply(late_row(feed, feed.ba.source_times["1"] * 1000 - 5, "book"))
    assert feed.ba.states["1"].invalid_reason == "BA_EVENT_OUT_OF_ORDER"
    assert feed.opposite_ba.states["1"].invalid_reason is None
    assert feed.books["1"] == book


def test_local_configs_expose_both_tolerances():
    assert RDPBAConfig().out_of_order_tolerance_ms == 10
    for folder, tolerance in (("config", 20), ("livecfg", 10)):
        cfg = Config.load(folder).rdp_sm
        assert cfg.ba.out_of_order_tolerance_ms == tolerance
        assert cfg.repair.opposite_ba.out_of_order_tolerance_ms == tolerance


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_tolerance_is_rejected(value):
    with pytest.raises(ValidationError):
        RDPBAConfig(out_of_order_tolerance_ms=value)


@pytest.mark.parametrize("phase", ["NORMAL", "REPAIR"])
@pytest.mark.parametrize("stage", ["final_guard", "after_ack"])
async def test_tolerated_frame_during_sdk_post_preserves_order_ownership(env, monkeypatch, phase, stage):
    if phase == "NORMAL":
        setup = await execution_setup(env, monkeypatch)
        controller, token, expected_posts = setup[4], "1", 1
    else:
        setup, controller = await setup_exit(env, monkeypatch)
        token, expected_posts = "2", 2
    v, e, feed = setup[:3]
    # Exercise ordering, with room for SDK signing and the async journal worker.
    controller.episodes.cfg.signal_lifetime_sec = .5
    controller.episodes.cfg.max_sell_age_ms = 400
    controller.episodes.cfg.max_entry_age_ms = 450
    native = e.gateway.send_prepared
    native_record = e.record
    injected = []

    def inject():
        detector = controller.episodes
        watermark = max(detector.source_times[token], D(str(feed.books[token].source_time)))
        old = depth(feed, 999, token) | {"timestamp": str(watermark * 1000 - 10)}
        book = feed.books[token]
        feed.apply(old)
        assert feed.books[token] == book
        assert detector.states[token].invalid_reason is None
        injected.append(True)

    async def delayed(*args, **kwargs):
        if stage == "final_guard":
            inject()
        return await native(*args, **kwargs)

    def record(kind, payload, *, event_wall=None):
        native_record(kind, payload, event_wall=event_wall)
        if stage == "after_ack" and kind == "sm_placement_result" and payload["outcome"] == "ACK":
            inject()

    try:
        monkeypatch.setattr(e.gateway, "send_prepared", delayed)
        monkeypatch.setattr(e, "record", record)
        absorption(feed, token)
        method = controller.enter if phase == "NORMAL" else controller.repair
        result, _ = await method(*e.economic_inventory(), "tolerated-sdk")
        assert injected == [True], result.to_dict()
        assert len(v.post_requests) == expected_posts
        assert result.placement_results[0]["outcome"] == "ACK"
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.fill_deadline_monotonic - result.t0_monotonic == pytest.approx(
            controller.episodes.cfg.signal_lifetime_sec)
        closes = [r["data"]["payload"] for r in journal_records(e.journal.path)
                  if r["kind"] in {"sm_ba_close_requested", "sm_obi_close_requested"}
                  and r["data"]["payload"]["window_id"] == "tolerated-sdk"]
        assert closes[0]["reason"] in {"ORDER_DEADLINE", "BA_ORDER_DEADLINE"}
        assert closes[0]["requested_monotonic"] >= result.fill_deadline_monotonic
    finally:
        await cleanup(setup)
