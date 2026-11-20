"""M18 receives original frame/row/level references through the actual parser."""
from decimal import Decimal as D

from sop_signals.feed import Feed
from test_sop_feed import Wire as BaseWire


class Wire(BaseWire):
    def __init__(self):
        self.rows, self.seq = [], 0
        self.f = Feed("condition", ("yes", "no"), {"yes": ".01", "no": ".01"},
                      lambda kind, payload: self.rows.append((kind, payload)), strategy="M18")
        self.f.connect(now=0, wall=1000, seq=0)

    def depth(self, t, q, *, source=None):
        row = {"event_type": "price_change", "price_changes": [
            {"asset_id": "yes", "side": "BUY", "price": ".40", "size": q}]}
        if source is not None:
            row["timestamp"] = str(round((1000+source)*1000))
        self.send(t, row)

    def refresh(self, t):
        self.send(t, [{"event_type": "best_bid_ask", "asset_id": token,
                       "best_bid": ".40", "best_ask": ".42"} for token in ("yes", "no")])

    def hit(self, t, size="10", identity=None):
        self.send(t, {"event_type": "last_trade_price", "asset_id": "yes", "side": "SELL",
                      "price": ".40", "size": size, "trade_id": identity or str(t)})

    def timer(self, t):
        self.seq += 1
        self.f.timer(now=t, wall=1000+t, seq=self.seq)

    def reference(self):
        self.book(.99)
        self.book(.99, "no")
        self.hit(1.)
        self.depth(1.02, "80")
        self.refresh(1.15)
        self.depth(1.30, "95")
        self.refresh(1.45)
        self.timer(1.501)
        return self.f.core.campaigns.get("yes")

    def second(self, *, trailing_sell=False):
        self.hit(1.6, "25")
        self.depth(1.62, "77")
        self.refresh(1.75)
        self.depth(1.90, "95")
        self.refresh(2.05)
        if trailing_sell:
            self.hit(2.09, "1")
        self.timer(2.101)


def test_parser_confirms_original_pair_without_receiver_clock_or_per_print_acks():
    w = Wire()
    c = w.reference()
    assert c.reference["terminal_depth"]["source"] == 1001.3
    w.second()
    s = next(iter(w.f.core.signals.values()))
    assert s["root_id"] == c.id and s["bid"]["hits"] == 25
    assert s["bid"]["terminal_quantity_proven"] and s["bid"]["guard"] == D("84.20")
    assert not hasattr(w.f, "receiver_clock_check")


def test_bbo_after_last_sell_cannot_replace_explicit_terminal_quantity():
    w = Wire()
    w.reference()
    w.second(trailing_sell=True)
    assert not w.f.core.signals
    assert any(p["payload"].get("reason") == "TERMINAL_ANCHOR_QUANTITY_UNAVAILABLE"
               for _, p in w.rows)


def test_unique_print_retransmission_is_not_double_counted():
    w = Wire()
    w.book(.99)
    w.hit(1., identity="same")
    w.hit(1., identity="same")
    assert next(iter(w.f.core.rounds.values())).path.hits == 10
    assert w.f.counts["DUPLICATE_DIRECT_TRADE"] == 1


def test_same_source_time_pre_hit_quantity_cannot_start_round():
    w = Wire()
    w.book(1.)
    w.hit(1.)
    assert not w.f.core.rounds
    assert w.f.reasons["yes"] == "PRE_HIT_LEVEL_ORDER_UNPROVEN"


def test_nested_multi_token_depth_keeps_actual_parent_change_index():
    w = Wire()
    w.book(.99)
    w.book(.99, "no")
    w.hit(1.)
    w.depth(1.02, "80")
    w.send(1.15, {"event_type": "price_change", "price_changes": [
        {"asset_id": "no", "side": "BUY", "price": ".40", "size": "100"},
        {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "85"},
        {"asset_id": "no", "side": "SELL", "price": ".42", "size": "100"},
        {"asset_id": "yes", "side": "SELL", "price": ".42", "size": "100"},
        {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "86"},
    ]})
    terminal = next(iter(w.f.core.rounds.values())).path.last_depth
    assert terminal.frame == w.seq and terminal.row == 0 and terminal.change == 4


def test_internal_provenance_failure_blocks_new_actions_until_snapshot_rebuild():
    w = Wire()
    w.book(.99)
    w.hit(1.)
    core = w.f.core
    core.clock(1.1, w.seq+1, 0)
    core.depth("yes", "BID", D(".40"), D(90), 1.1,
               generation=1, input_ref=None, source=1001.1, kind="BID_PRICE_CHANGE")
    assert not core.rounds and not w.f.good["yes"] and not w.f.books["yes"].complete
    assert w.f.reasons["yes"] == "EXPLICIT_TIMING_PROVENANCE_MISSING_OR_CONFLICT"


def test_tick_change_and_reconnect_end_old_reference_and_never_reuse_it():
    w = Wire()
    w.reference()
    w.send(1.55, {"event_type": "tick_size_change", "asset_id": "yes",
                  "new_tick_size": ".02", "old_tick_size": ".01"})
    assert not w.f.core.campaigns
    w.f.gap("DISCONNECTED")
    assert not w.f.core.rounds and not w.f.core.signals
