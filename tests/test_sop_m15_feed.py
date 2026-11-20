"""Pre-seed/direct fixed-set BUY remains flow, never an invented weak round."""

from decimal import Decimal as D

from test_sop_feed import Wire
from test_sop_m13_feed import delta, refresh


def wire():
    w = Wire(strategy="M15")
    w.send(1, {"event_type": "book", "asset_id": "yes",
               "bids": [{"price": ".40", "size": "100"}],
               "asks": [{"price": p, "size": "100"} for p in (".42", ".43", ".44")]})
    for t in (1.1, 1.2, 1.3, 1.4, 1.5):
        refresh(w, t)
    return w


def test_preseed_buy_with_same_timestamp_quantity_is_flow_not_proven_round():
    w = wire()
    delta(w, 1.6, ".43", "0")
    refresh(w, 1.701)
    root = w.f.core.withdrawals["yes"]
    assert root.key and not root.seed
    # There is no seed/support: no pre-hit test is due, but every BUY is counted.
    w.send(1.72, [
        {"event_type": "price_change", "price_changes": [
            {"asset_id": "yes", "side": "SELL", "price": ".42", "size": "99"}]},
        {"event_type": "last_trade_price", "asset_id": "yes", "side": "BUY",
         "price": ".42", "size": "10", "trade_id": "pre-seed-flow"},
    ])
    assert w.f.good["yes"] and root.buy == 10 and not w.f.core.rounds
    assert not root.retest_id and not w.f.core.signals


def test_rear_deleted_level_buy_is_still_counted_once_and_never_a_target_test():
    w = wire()
    delta(w, 1.6, ".43", "0")
    refresh(w, 1.701)
    root = w.f.core.withdrawals["yes"]
    row = {"event_type": "last_trade_price", "asset_id": "yes", "side": "BUY",
           "price": ".43", "size": "10", "trade_id": "rear-direct"}
    w.send(1.72, row)
    w.send(1.72, row)
    assert w.f.good["yes"] and root.buy == 10
    assert w.f.counts["DUPLICATE_DIRECT_TRADE"] == 1
    assert not w.f.core.rounds and root.levels == (D(".42"), D(".43"), D(".44"))
    w.f.connect(now=1.73, wall=1001.73, seq=w.seq + 1)
    assert not any((w.f.core.withdrawals, w.f.core.buy_history, w.f.core.buy_intervals))
