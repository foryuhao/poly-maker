from collections import Counter

import pytest

from test_sop_feed import Wire as BaseWire


class Wire(BaseWire):
    def __init__(self):
        super().__init__("M24")
        self.model = self.f.core.variants[0]

    def delta(self, at, *, bid="98", ask="94", **extra):
        self.send(at, {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "yes", "side": "BUY", "price": ".40", "size": bid},
                {"asset_id": "yes", "side": "SELL", "price": ".42", "size": ask},
            ], **extra,
        })

    def trade(self, at, side, *, size="20", token="yes", key=None, **extra):
        self.send(at, {
            "event_type": "last_trade_price", "asset_id": token, "side": side,
            "price": ".40" if side == "SELL" else ".42", "size": size,
            "trade_id": key or side, **extra,
        })

    def tick(self, at, pending=()):
        self.seq += 1
        self.f.timer(now=at, wall=1000 + at, seq=self.seq, pending=pending)

    def prepare(self, first="SELL", second="BUY", bid="98", ask="94"):
        self.book(.985)
        self.trade(1.0, first)
        self.trade(1.115, second)
        self.delta(1.17, bid=bid, ask=ask)
        self.delta(1.35, bid=bid, ask=ask)
        self.delta(1.48, bid=bid, ask=ask)

    def events(self, name):
        return [r["payload"] for _, r in self.rows if r["event"] == name]


@pytest.mark.parametrize(("first", "second"), [("SELL", "BUY"), ("BUY", "SELL")])
def test_m24_accepts_either_side_first_and_needs_no_trough(first, second):
    w = Wire()
    w.prepare(first, second, bid="103", ask="101")
    w.tick(1.5)
    signal, = w.events("SIGNAL_CONFIRMED")
    assert signal["values"]["JB"] == "23"
    assert signal["values"]["JA"] == "21"
    assert signal["ask_net_sign"] == 1
    assert signal["price"] == "0.40"
    assert signal["d"] == pytest.approx(4.5)


def test_m24_other_token_does_not_supply_the_missing_buy():
    w = Wire()
    w.book(.985)
    w.book(.985, "no")
    w.trade(1.0, "SELL")
    w.trade(1.115, "BUY", token="no")
    for at in (1.17, 1.35, 1.48):
        w.delta(at)
    w.tick(1.501)
    assert not w.events("SIGNAL_CONFIRMED")
    assert w.model.counts["END:DUAL_MATERIALITY_FAILED"] == 1


def test_retransmission_does_not_inflate_either_side():
    w = Wire()
    w.book(.985)
    w.trade(1.0, "SELL", size="0.3")
    w.trade(1.08, "SELL", size="0.3", timestamp="1001000")
    w.trade(1.115, "BUY")
    for at in (1.17, 1.35, 1.48):
        w.delta(at, bid="110")
    w.tick(1.501)
    assert w.f.counts["DUPLICATE_DIRECT_TRADE"] == 1
    assert w.model.counts["END:DUAL_MATERIALITY_FAILED"] == 1


def test_tied_source_bbo_cannot_prove_pretrade_best_levels():
    w = Wire()
    w.book(1.0)
    w.trade(1.001, "SELL", timestamp="1001000")
    assert not w.model.roots
    assert w.model.counts["UNKNOWN:PRE_TRADE_FIXED_LEVEL_ORDER_UNPROVEN"] == 1


def test_outside_window_depth_cannot_supply_a_missing_formation_endpoint():
    w = Wire()
    w.book(.985)
    w.trade(1.0, "SELL")
    w.delta(1.18)
    w.delta(1.35)
    w.trade(1.45, "BUY")
    w.delta(1.501)
    assert not w.events("SIGNAL_CONFIRMED")
    assert w.model.counts["UNKNOWN:FORMATION_ENDPOINT_UNALIGNED"] == 1


def test_equality_veto_inside_publication_batch_cannot_be_overwritten():
    w = Wire()
    w.prepare()
    w.delta(1.501, bid="98", ask="98")
    w.delta(1.502)
    assert not w.events("SIGNAL_CONFIRMED")
    assert w.model.counts["END:MAINTENANCE_COMPARISON_FAILED"] == 1


def test_pending_input_does_not_publish_and_guard_wait_cannot_extend_deadline():
    w = Wire()
    w.prepare()
    w.tick(1.501, pending=[1.499])
    assert not w.events("SIGNAL_CONFIRMED")
    w.tick(1.5501)
    assert w.model.counts["UNKNOWN:PUBLICATION_WATERMARK_LATE"] == 1
    assert not w.events("SIGNAL_CONFIRMED")


def test_post_confirmation_trade_with_no_proved_intermediate_quantity_suspends():
    w = Wire()
    w.prepare()
    w.tick(1.5)
    w.trade(1.55, "BUY", key="after")
    assert len(w.events("SIGNAL_SUSPENDED")) == 1
    w.delta(1.57, bid="125")
    w.tick(1.601)
    ended, = w.events("SIGNAL_ENDED")
    assert ended["unknown"]
    assert ended["reason"] == "MAINTENANCE_PATH_UNPROVEN"


@pytest.mark.parametrize("kind", ["gap", "tick", "disappearance"])
def test_m24_ends_frozen_pair_on_quality_or_level_loss(kind):
    w = Wire()
    w.prepare()
    w.tick(1.5)
    if kind == "gap":
        w.f.gap("SOURCE_GAP")
    elif kind == "tick":
        w.send(1.53, {"event_type": "tick_size_change", "asset_id": "yes",
                      "new_tick_size": ".001"})
    else:
        w.delta(1.53, bid="0")
    assert not w.model.roots
    assert len(w.events("SIGNAL_ENDED")) == 1


def test_m24_frequency_funnel_stays_bounded_after_release():
    w = Wire()
    w.prepare()
    w.tick(1.5)
    w.f.release_runtime()
    assert not w.model.roots and not w.model.ended_at
    assert isinstance(w.model.counts, Counter)
