import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from paired_study import PairedReplay

META = [
    dict(asset="a", market="m", question="Q", outcome="Y", tick=0.01),
    dict(asset="b", market="m", question="Q", outcome="N", tick=0.01),
]


def book(t, asset, bid, ask):
    return dict(
        received=t,
        generation=1,
        event=dict(
            event_type="book",
            asset_id=asset,
            timestamp=str(int(t * 1000)),
            bids=[dict(price=str(bid), size="100")],
            asks=[dict(price=str(ask), size="100")],
        ),
    )


def trade(t, asset, side, price, quantity=5):
    return dict(
        received=t,
        generation=1,
        event=dict(
            event_type="last_trade_price",
            asset_id=asset,
            side=side,
            price=str(price),
            size=str(quantity),
            timestamp=str(int(t * 1000)),
        ),
    )


def entered():
    e = PairedReplay(META, {"m": dict(r=0, e=1)}, 1300, 1000)
    e.event(book(999.9, "b", 0.56, 0.60))
    e.event(book(1000, "a", 0.4, 0.44))
    e.event(trade(1001, "a", "SELL", 0.4))
    return e


def test_two_makers_lock_pair_without_selling_unconfirmed_tokens():
    e = entered()
    e.event(trade(1002, "b", "SELL", 0.56))
    e.advance(1008, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5)
    assert r["exit_status"] == "PAIRED_MAKERS"
    assert abs(r["entry_price"] - 0.41) < 1e-8
    assert abs(r["hedge_price"] - 0.57) < 1e-8
    assert abs(r["net_per_share"] - 0.016) < 1e-6
    assert r["exit_time"] == 1007
    assert r["max_cash_required"] < 5


def test_primary_buy_is_complementary_maker_hedge_fill():
    e = entered()
    e.event(trade(1002, "a", "BUY", 0.44))
    e.advance(1008, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5)
    assert r["hedge_strict"] == 5
    assert r["exit_status"] == "PAIRED_MAKERS"


def test_no_hedge_fill_still_pays_forced_exit_spread():
    e = entered()
    e.advance(1100, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5)
    assert r["exit_status"] == "MINIMUM_SIZE_FORCED_HEDGE"
    assert abs(r["net_per_share"] + 0.014) < 1e-6
    assert r["possible_cash_branches"][0]["fallback_quantity"] == 5


def test_tiny_unpaired_remainder_not_hedged_by_dominated_oversize_purchase():
    e = entered()
    e.event(trade(1002, "b", "SELL", 0.56, 4.99))
    e.advance(1100, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5)
    assert r["exit_status"] == "RESIDUAL_WRITTEN_OFF"
    assert r["possible_cash_branches"][0]["fallback_quantity"] == 0
    assert r["net_usdc"] > 0


def test_unknown_partial_hedge_includes_interior_worst_case():
    e = entered()
    e.advance(1001.6, True)
    s = e.states[("a", "neutral_inside", 30, 0.5)]
    s["hedge_possible"] = 5
    e.advance(1100, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5)
    assert r["net_usdc"] < -1
    assert any(0 < b["hedge_quantity"] < 5 for b in r["possible_cash_branches"])


def test_fok_fallback_does_not_use_profitable_average_above_limit_last_level():
    e = entered()
    e.advance(1001.6, True)
    s = e.states[("a", "neutral_inside", 30, 0.5)]
    s["hedge_strict"] = 2.9
    e.books["b"].asks = {
        0.30: 4,
        0.80: 1,
    }  # Average .40 is affordable for remainder2.1; final .80 is not.
    e.advance(1100, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5)
    assert r["exit_status"] == "RESIDUAL_WRITTEN_OFF"


def test_taker_buy_share_fee_does_not_invent_five_net_tokens():
    from paired_study import fallback_gross_quantity

    assert fallback_gross_quantity(0, 1) == 5
    assert fallback_gross_quantity(0.07, 1) == 5.82
    assert fallback_gross_quantity(0.07, 1) * (1 - 2 * 0.07) >= 5
    assert fallback_gross_quantity(0.07, 0.5) is None


def test_completed_pair_evidence_cannot_grow_after_order_owner_closed():
    e = entered()
    e.event(trade(1002, "b", "SELL", 0.56))
    e.advance(1008, True)
    row = next(r for r in e.rows if r["horizon"] == 30 and r["delay"] == 0.5)
    count = len(row["hedge_evidence"])
    e.event(trade(1009, "b", "SELL", 0.56))
    assert len(row["hedge_evidence"]) == count


def test_market_movement_that_blocks_hedge_is_not_a_free_profitable_exit():
    e = entered()
    e.event(book(1001.4, "b", 0.7, 0.74))
    e.advance(1100, True)
    rows, _ = e.finish()
    r = next(r for r in rows if r["horizon"] == 30 and r["delay"] == 0.5)
    assert r["net_per_share"] < -0.15
    assert r["hedge_strict"] == 0
