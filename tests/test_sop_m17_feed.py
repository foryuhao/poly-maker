"""M17 runs through the actual direct-trade and explicit-depth parent parser."""

from decimal import Decimal as D

from sop_signals.feed import Feed
from test_sop_feed import Wire as BaseWire


class Wire(BaseWire):
    def __init__(self):
        self.rows, self.seq = [], 0
        self.f = Feed("condition", ("yes", "no"), {"yes": ".01", "no": ".01"},
                      lambda k, p: self.rows.append((k, p)), strategy="M17")
        self.f.connect(now=0, wall=1000, seq=0)

    def delta(self, t, quantity="95", *, source=None, available=None, side="BUY", price=".40"):
        row = {"event_type": "price_change", "price_changes": [
            {"asset_id": "yes", "side": side, "price": price, "size": quantity}]}
        if source is not None:
            row["timestamp"] = str(round((1000 + source) * 1000))
        self.send(t, row, available=available)

    def refresh(self, t):
        self.send(t, [{"event_type": "best_bid_ask", "asset_id": token,
                       "best_bid": ".40", "best_ask": ".42"} for token in ("yes", "no")])

    def hit(self, t, side="SELL", token="yes", identity=None, quantity="20", *, available=None):
        self.send(t, {"event_type": "last_trade_price", "asset_id": token, "price": ".40",
                      "size": quantity, "side": side, "trade_id": identity or str(t)}, available=available)

    def timer(self, t):
        self.seq += 1
        self.f.timer(now=t, wall=1000+t, seq=self.seq)

    def path(self):
        self.book(.99)
        self.book(.99, "no")
        self.hit(1.)
        self.delta(1.01)
        self.refresh(1.14)
        self.hit(1.26)
        self.delta(1.27)
        self.refresh(1.39)
        self.hit(1.51)
        self.delta(1.52)
        self.refresh(1.65)


def test_actual_parser_keeps_original_quantity_source_and_confirms_three_buckets():
    w = Wire()
    w.path()
    r = w.f.core.roots["yes"]
    assert r.quantity_ref["source"] == D("1000.99") and r.rechecked_records == 3
    w.timer(1.75)
    s = next(iter(w.f.core.signals.values()))
    assert s["formation_H"] == 60 and s["anchor_price"] == D(".40")
    assert s["can_reference_for_new_action"] and s["deadline"] == 4.75


def test_end_to_publication_new_sell_is_monitored_and_pending_does_not_reset_confirmation():
    w = Wire()
    w.path()
    w.hit(1.75, quantity="2")
    s = next(iter(w.f.core.signals.values()))
    assert s["formation_H"] == 60 and not s["can_reference_for_new_action"]
    w.delta(1.8)
    assert s["can_reference_for_new_action"] and s["confirmed"] == 1.75 and s["deadline"] == 4.75


def test_ask_and_bbo_refreshes_cannot_recheck_original_bid_quantity():
    w = Wire()
    w.book(.99)
    w.hit(1.)
    w.delta(1.02, "100", side="SELL", price=".42")
    w.refresh(1.04)
    assert len(w.f.core.roots["yes"].pending) == 1
    w.timer(1.1)
    assert not w.f.core.roots and w.f.reasons["yes"] == "TRADE_DEPTH_RECHECK_UNAVAILABLE"
    assert not w.f.books["yes"].complete


def test_equal_source_later_received_quantity_is_not_recheck_and_timeout_requires_new_snapshot():
    w = Wire()
    w.book(.99)
    w.hit(1.)
    w.delta(1.02, source=1.)
    assert len(w.f.core.roots["yes"].pending) == 1
    w.timer(1.1)
    w.delta(1.11)
    w.hit(1.12)
    assert not w.f.core.roots and not w.f.good["yes"]
    w.book(1.13)
    w.hit(1.14)
    assert w.f.core.roots["yes"].start == D("1.14")


def test_processing_late_recheck_cannot_use_received_only_to_pass():
    w = Wire()
    w.book(.99)
    w.hit(1.)
    w.delta(1.04, available=1.1)
    assert not w.f.core.roots and not w.f.good["yes"]


def test_same_source_pre_hit_and_missing_direct_trade_identity_are_unknown():
    w = Wire()
    w.book(1.)
    w.hit(1.)
    assert not w.f.core.roots and w.f.reasons["yes"] == "INITIAL_DEPTH_ORDER_UNKNOWN"
    w = Wire()
    w.book(.99)
    w.send(1., {"event_type":"last_trade_price", "asset_id":"yes", "price":".40",
                "side":"SELL", "size":"60"})
    assert not w.f.core.roots and w.f.reasons["yes"] == "DIRECT_TRADE_IDENTITY_MISSING"


def test_repeated_print_identity_not_split_into_three_distinct_records():
    w = Wire()
    w.book(.99)
    for t in (1., 1.01, 1.02):
        # A retransmission has the original economic fields/source timestamp.
        w.send(t, {"event_type":"last_trade_price", "asset_id":"yes", "price":".40",
                   "side":"SELL", "size":"20", "trade_id":"same", "timestamp":"1001000"})
    r = w.f.core.roots["yes"]
    assert r.hits == 20 and r.forming_records == 1 and w.f.counts["DUPLICATE_DIRECT_TRADE"] == 2


def test_interleaved_parent_updates_keep_raw_global_change_index():
    w = Wire()
    w.book(.99)
    w.book(.99, "no")
    w.hit(1.)
    w.send(1.01, {"event_type":"price_change", "price_changes":[
        {"asset_id":"no","side":"SELL","price":".42","size":"100"},
        {"asset_id":"yes","side":"SELL","price":".42","size":"100"},
        {"asset_id":"yes","side":"BUY","price":".40","size":"95"}]})
    row=next(p for k,p in w.rows if k=="SOP_EVIDENCE" and p["event"]=="TRADE_DEPTH_RECHECKED")
    assert row["payload"]["quantity_ref"][-1] == 2


def test_same_parent_recheck_can_use_proven_row_order_and_strictly_later_raw_source():
    w=Wire();w.book(.99)
    w.send(1.,[
        {"event_type":"last_trade_price","asset_id":"yes","price":".40","size":"20",
         "side":"SELL","trade_id":"same-parent","timestamp":"1001000"},
        {"event_type":"price_change","timestamp":"1001001","price_changes":[
            {"asset_id":"yes","side":"BUY","price":".40","size":"95"}]}])
    r=w.f.core.roots['yes']
    assert r.rechecked_records==1 and not r.pending


def test_ordinary_full_snapshot_rechecks_without_rebuilding_generation():
    w = Wire()
    w.book(.99)
    w.hit(1.)
    before=w.f.book_epochs["yes"]
    w.book(1.02)
    assert w.f.book_epochs["yes"] == before and not w.f.core.roots["yes"].pending


def test_complete_snapshot_missing_original_level_ends_it_not_a_quantity_endpoint():
    w = Wire()
    w.book(.99)
    w.hit(1.)
    w.send(1.02,{"event_type":"book","asset_id":"yes", "bids":[{"price":".39","size":"100"}],
                 "asks":[{"price":".42","size":"100"}]})
    assert not w.f.core.roots
    assert not any(p["event"]=="TRADE_DEPTH_RECHECKED" for k,p in w.rows if k=="SOP_EVIDENCE")


def test_pending_signal_does_not_survive_tick_change_or_reconnect():
    for kind in ("tick", "gap"):
        w=Wire();w.path();w.hit(1.75,quantity="2")
        assert w.f.core.signals
        if kind=="tick":
            w.send(1.76,{"event_type":"tick_size_change","asset_id":"yes","new_tick_size":".02"})
        else:
            w.f.gap("WS_DISCONNECTED")
        assert not w.f.core.signals and not w.f.core.roots


def test_parent_intermediate_hard_floor_and_publication_floor_are_both_checked():
    for low in ("69","80"):
        w=Wire();w.path()
        changes=[{"asset_id":"yes","side":"BUY","price":".40","size":low}]
        if low=="69":changes.append({"asset_id":"yes","side":"BUY","price":".40","size":"95"})
        w.send(1.76,{"event_type":"price_change","price_changes":changes})
        assert not w.f.core.signals


def test_nonbest_first_sell_ignored_and_other_token_depth_never_rechecks_yes():
    w=Wire();w.book(.99);w.book(.99,"no")
    w.send(1.,{"event_type":"last_trade_price","asset_id":"yes","price":".39","size":"20","side":"SELL","trade_id":"lower"})
    assert not w.f.core.roots and w.f.good["yes"]
    w.hit(1.01)
    w.book(1.02,"no")
    assert len(w.f.core.roots["yes"].pending)==1
