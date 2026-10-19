"""Volatility comes from real event seconds; quiet books retain protocol health."""
from types import SimpleNamespace

import pytest

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm_repair import RepairController
from test_rdp_sm_obi import feed_fixture


def test_volatility_uses_seconds_not_local_reads_and_resets_after_reconnect():
    feed, clock, rows = feed_fixture()
    e = SimpleNamespace(cfg=SimpleNamespace(rdp_sm=RDPSMConfig()), monotonic=lambda: clock.now)
    controller = RepairController(e, SimpleNamespace(feed=feed))
    assert controller.sigma() is None
    for second in range(25):
        clock.now = second
        for row in rows:
            row["timestamp"] = str(feed.wall()*1000)
        rows[0]["bids"][0]["price"] = ".30" if second % 2 else ".31"
        rows[0]["asks"][0]["price"] = ".32" if second % 2 else ".33"
        feed.apply(rows)
    assert controller.sigma() == pytest.approx(.01)
    samples = len(feed.probability_points)
    for _ in range(100):
        feed.snapshot(repair=True)
        feed.apply("PONG")
    assert len(feed.probability_points) == samples
    assert controller.sigma() == pytest.approx(.01)
    feed.connect()
    assert controller.sigma() is None


def test_quiet_book_is_usable_only_while_transport_and_both_books_are_valid():
    feed, clock, _ = feed_fixture()
    clock.now = .5
    feed.apply("PONG")
    assert feed.snapshot().reason == "WS_BOOK_STALE"
    assert feed.snapshot(repair=True).valid
    clock.now = 21
    assert feed.snapshot(repair=True).reason == "WS_TRANSPORT_STALE"
    feed.invalidate("WS_DISCONNECTED")
    assert not feed.snapshot(repair=True).valid


def test_inconsistent_probability_breaks_same_generation_price_path():
    feed, clock, rows = feed_fixture()
    first = feed.repair_price_points[-1]
    clock.now = 1
    rows[1]['bids'][0]['price'] = '.29'
    rows[1]['asks'][0]['price'] = '.31'
    feed.apply(rows)  # p_yes .51 vs p_from_no .70: cannot bridge this interval
    assert feed.repair_price_points[-1] == first
    clock.now = 2
    rows[1]['bids'][0]['price'] = '.49'
    rows[1]['asks'][0]['price'] = '.51'
    feed.apply(rows)
    last = feed.repair_price_points[-1]
    assert last.generation == first.generation and last.p == first.p
    assert last.path_segment != first.path_segment


def test_transport_gap_breaks_path_even_when_the_price_returns_unchanged():
    feed, clock, rows = feed_fixture()
    first = feed.repair_price_points[-1]
    clock.now = 21
    feed.apply('PONG')
    feed.apply(rows)
    last = feed.repair_price_points[-1]
    assert last.generation == first.generation and last.path_segment != first.path_segment
