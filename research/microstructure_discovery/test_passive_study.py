import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from passive_study import Replay

META = [
    dict(asset="a", market="m", question="Q", outcome="Y", tick=0.01),
    dict(asset="b", market="m", question="Q", outcome="N", tick=0.01),
]


def book(t, bid=0.4, ask=0.44):
    return dict(
        received=t,
        generation=1,
        event=dict(
            event_type="book",
            asset_id="a",
            timestamp=str(t * 1000),
            bids=[dict(price=str(bid), size="100")],
            asks=[dict(price=str(ask), size="100")],
        ),
    )


def trade(t, size=5, source=None, price=0.4):
    return dict(
        received=t,
        generation=1,
        event=dict(
            event_type="last_trade_price",
            asset_id="a",
            side="SELL",
            price=str(price),
            size=str(size),
            timestamp=str(int((source if source is not None else t) * 1000)),
        ),
    )


def test_passive_strict_trade_through_and_delayed_post():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(trade(1001))
    e.event(book(1002, bid=0.42, ask=0.44))
    e.advance(1032, True)
    rows, _ = e.finish()
    rs = [r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5]
    assert len(rs) == 1
    assert rs[0]["entry_price"] == 0.41  # Never credit historical lower SELL trade price.
    assert abs(rs[0]["exit_price"] - 0.42) < 1e-8
    assert abs(rs[0]["net_per_share"] - 0.008) < 1e-8
    assert not rs[0]["ambiguous_execution"]


def test_receipt_late_pre_post_trade_not_a_fill():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(trade(1001, source=1000.2))
    e.advance(1200, True)
    rows, _ = e.finish()
    assert rows == []


def test_cancel_latency_includes_partial_fill_race():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(trade(1001, size=2))
    e.event(trade(1001.4, size=3))
    e.event(book(1002, bid=0.42, ask=0.44))
    e.advance(1200, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["quantity"] == 5
    assert r["exit_status"] == "OK"


def test_partial_position_is_not_dropped():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(trade(1001, size=2))
    e.event(book(1002, bid=0.42, ask=0.44))
    e.advance(1200, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["quantity"] == 2
    assert r["exit_status"] == "SUBMINIMUM_PARTIAL"
    assert r["net_usdc"] < -0.8


def test_ambiguous_cross_cannot_create_positive_edge():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(book(1001, bid=0.39, ask=0.41))
    e.event(book(1002, bid=0.45, ask=0.46))
    e.advance(1200, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["ambiguous_execution"]
    assert r["net_per_share"] == 0


def test_uncertain_clock_boundary_does_not_select_away_losses():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(trade(1000.8, source=1000.6))
    e.event(book(1002, bid=0.35, ask=0.36))
    e.advance(1200, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["ambiguous_execution"]
    assert r["net_per_share"] < 0


def test_stream_break_bounds_unobserved_active_quote_exposure():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(dict(received=1001, generation=2, event={}))
    e.advance(1200, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["ambiguous_execution"]
    assert r["exit_status"] == "UNTRUSTED_EXIT"
    assert r["net_per_share"] < -0.4


def test_resolved_market_never_reopens_on_full_snapshot():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1200)
    e.event(book(1000))
    e.event(dict(received=1001, generation=1, event=dict(event_type="market_resolved", market="m")))
    e.event(book(1010))
    e.event(book(1020))
    assert "a" in e.resolved
    assert not e.trusted("a")


def test_tick_change_blocks_pending_and_future_posts():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1200)
    e.event(book(1000))
    e.event(
        dict(
            received=1000.25,
            generation=1,
            event=dict(event_type="tick_size_change", asset_id="a", new_tick_size=".001"),
        )
    )
    e.event(book(1016))
    e.event(trade(1017))
    e.advance(1200, True)
    rows, _ = e.finish()
    assert rows == []
    assert "a" in e.no_new_quotes


def test_late_crossing_post_book_removes_optimistic_fill_credit():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(trade(1001))
    late = book(1001.2, bid=0.39, ask=0.41)
    late["event"]["timestamp"] = "1000600"
    e.event(late)
    e.event(book(1002, bid=0.45, ask=0.46))
    e.advance(1200, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["ambiguous_execution"]
    assert r["net_per_share"] == 0


def test_complementary_buy_can_fill_primary_bid_through_mint():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    r = trade(1001, price=0.6)
    r["event"].update(asset_id="b", side="BUY")
    e.event(r)
    e.event(book(1002, bid=0.42, ask=0.44))
    e.advance(1200, True)
    rows, _ = e.finish()
    pos = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert pos["quantity"] == 5
    assert pos["fill_evidence"][0]["complementary_buy"]
    assert pos["fill_evidence"][0]["canonical_primary_price"] == 0.4


def test_mirror_trade_prints_do_not_double_fill_partial():
    e = Replay(META, {"m": dict(r=0, e=1)}, 1200, 1000)
    e.event(book(1000))
    e.event(trade(1001, size=2, price=0.4))
    mirror = trade(1001, size=2, price=0.6)
    mirror["event"].update(asset_id="b", side="BUY")
    e.event(mirror)
    e.event(book(1002, bid=0.42, ask=0.44))
    e.advance(1200, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["delay"] == 0.5 and r["horizon"] == 30)
    assert r["quantity"] == 2
    assert len(r["fill_evidence"]) == 1
