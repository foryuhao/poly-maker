"""Raw M13 fixed-set BUY accounting before normalization and mirror dedup."""

from decimal import Decimal as D

from test_sop_feed import Wire


def prepared_wire():
    w = Wire(strategy="M13")
    w.send(1, {"event_type": "book", "asset_id": "yes",
               "bids": [{"price": ".40", "size": "100"}],
               "asks": [{"price": p, "size": "100"} for p in (".42", ".43", ".44")]})
    for t in (1.1, 1.2, 1.3, 1.4, 1.5):
        refresh(w, t)
    return w


def refresh(w, t):
    w.send(t, {"event_type": "best_bid_ask", "asset_id": "yes",
               "best_bid": ".40", "best_ask": ".42"})


def delta(w, t, price, size):
    w.send(t, {"event_type": "price_change", "price_changes": [
        {"asset_id": "yes", "side": "SELL", "price": price, "size": size}]})


def test_fixed_rear_level_zero_does_not_discard_later_known_direct_buy():
    w = prepared_wire()
    delta(w, 1.6, ".43", "0")
    refresh(w, 1.701)
    root = w.f.core.withdrawals["yes"]
    assert root.key and root.levels == (D(".42"), D(".43"), D(".44"))
    buy = {"event_type": "last_trade_price", "asset_id": "yes", "price": ".43",
           "size": "10", "side": "BUY", "trade_id": "rear-direct"}
    w.send(1.72, buy)
    w.send(1.72, buy)
    assert w.f.good["yes"] and root.buy == 10
    assert w.f.counts["DUPLICATE_DIRECT_TRADE"] == 1
    assert not any(r.side == "ASK" for r in w.f.core.rounds.values())
    assert root.depth_ref == (w.seq - 3, 0, 0)


def test_first_small_decrease_keeps_original_group_start_and_quantity():
    w = prepared_wire()
    delta(w, 1.6, ".43", "99")
    root = w.f.core.withdrawals["yes"]
    delta(w, 1.65, ".43", "40")
    delta(w, 1.65, ".44", "40")
    refresh(w, 1.751)
    assert root.key and root.start == 1.6 and root.q0 == 300
    assert w.f.core.counts["LW_STARTED"] == 1


def test_rebuild_cannot_reuse_withdrawal_or_its_buy_accumulator():
    w = prepared_wire()
    delta(w, 1.6, ".43", "0")
    refresh(w, 1.701)
    assert w.f.core.withdrawals
    w.f.connect(now=1.72, wall=1001.72, seq=w.seq + 1)
    assert not w.f.core.withdrawals and not w.f.core.buy_history
    assert not w.f.core.ask_books and not w.f.core.signals
