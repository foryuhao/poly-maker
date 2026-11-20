"""Explicit P provenance through the actual parent parser, never BBO callbacks."""

import math
from decimal import Decimal as D

from sop_signals.feed import Feed
from sop_signals.m16 import Parameters
from sop_signals.m16_clock import AUDITED_SOURCES, HORIZON_SECONDS, VERSION
from test_sop_feed import Wire as GenericWire


def synthetic_clock_evidence():
    # Synthetic monotonic origin for native fixtures, not a capture attestation
    # or admission document. Actual capture generates its own process proof.
    ulp = D(str(math.ulp(float(HORIZON_SECONDS))))
    return {"version": VERSION, "status": "LOCAL_QUANTIZATION_PROVEN",
            "scope": "SYNTHETIC_CLOCK_FIXTURE_NOT_REALTIME_EVIDENCE",
            "receiver": "time.monotonic", "clock_padding_quanta": 2,
            "source_hashes": dict(AUDITED_SOURCES), "horizon_seconds": HORIZON_SECONDS,
            "serialization_verified": True, "serialization_samples": 16,
            "serialization": "PYTHON_JSON_BINARY64_EXACT_ROUNDTRIP",
            "valid_received_min": "0", "valid_received_max": str(HORIZON_SECONDS),
            "float_ulp_upper": str(ulp), "receiver_quantum_seconds": str(D(".000000001") + ulp),
            "clock_info": {"implementation": "clock_gettime(CLOCK_MONOTONIC)",
                           "resolution": 1e-9, "monotonic": True, "adjustable": False}}


class Wire(GenericWire):
    def __init__(self, *, proof=True):
        self.rows, self.seq = [], 0
        self.f = Feed("condition", ("yes", "no"), {"yes": ".01", "no": ".01"},
                      lambda k, p: self.rows.append((k, p)), strategy="M16", parameters=Parameters(),
                      receiver_clock_evidence=synthetic_clock_evidence() if proof else None)
        self.f.connect(now=0, wall=1000, seq=0)

    def depth(self, t, quantity, *, snapshot=False):
        if snapshot:
            self.send(t, {"event_type": "book", "asset_id": "yes",
                          "bids": [{"price": ".39", "size": "100"}, {"price": ".40", "size": quantity}],
                          "asks": [{"price": ".42", "size": "100"}]})
        else:
            self.send(t, {"event_type": "price_change", "price_changes": [
                {"asset_id": "yes", "side": "BUY", "price": ".40", "size": quantity}]})

    def refresh(self, t):
        self.send(t, {"event_type": "best_bid_ask", "asset_id": "yes", "best_bid": ".40", "best_ask": ".42"})

    def close(self, t):
        self.seq += 1
        self.f.timer(now=t, wall=1000+t, seq=self.seq)

    def reference(self, *, endpoint_kind="delta"):
        self.book(.99)
        self.book(.99, token="no")
        self.hit(1, identity="r1")
        self.depth(1.02, "80")
        self.refresh(1.15)
        if endpoint_kind == "bbo":
            self.refresh(1.22)
        elif endpoint_kind == "snapshot":
            self.depth(1.22, "85", snapshot=True)
        elif endpoint_kind == "interleaved":
            self.send(1.22, {"event_type": "price_change", "price_changes": [
                {"asset_id": "no", "side": "SELL", "price": ".42", "size": "100"},
                {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "85"},
                {"asset_id": "no", "side": "BUY", "price": ".40", "size": "100"},
                {"asset_id": "yes", "side": "SELL", "price": ".42", "size": "100"},
                {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "86"},
            ]})
        else:
            self.depth(1.22, "85")
        self.depth(1.24, "95")
        self.refresh(1.4)
        self.refresh(1.5)
        self.close(1.501)
        return self.f.core.campaigns.get("yes")

    def second_path(self):
        self.send(1.6, {"event_type": "last_trade_price", "asset_id": "yes", "side": "SELL",
                        "price": ".40", "size": "12", "trade_id": "r2"})
        self.depth(1.61, "75")
        self.depth(1.65, "80")
        self.depth(1.67, "95")
        for t in (1.8, 1.95, 2.1):
            self.refresh(t)


def test_actual_feed_can_confirm_two_rounds_from_explicit_quantities():
    w = Wire()
    c = w.reference()
    assert c and c.reference["t_minus"]["kind"] == "BID_PRICE_CHANGE"
    w.second_path()
    w.close(2.101)
    signal = next(iter(w.f.core.signals.values()))
    assert signal["root_id"] == c.id and signal["bid"]["guard"] == 83
    assert signal["bid"]["t_plus"]["frame"] > signal["bid"]["t_minus"]["frame"]
    assert signal["bid"]["t_plus"]["row"] == 0


def test_delta_provenance_keeps_original_global_nested_change_index():
    w = Wire()
    ref = w.reference(endpoint_kind="interleaved").reference
    assert ref["t_minus"]["change"] == 4
    assert ref["t_minus"]["quantity"] == 86
    assert ref["t_plus"]["frame"] > ref["t_minus"]["frame"] and ref["t_minus"]["row"] == 0
    assert ref["status"] == "TIMING_PROVEN"


def test_full_book_provenance_uses_original_bids_index_and_keeps_healthy_generation():
    w = Wire()
    ref = w.reference(endpoint_kind="snapshot").reference
    assert ref["t_minus"]["kind"] == "BID_BOOK" and ref["t_minus"]["change"] == 1
    assert ref["generation"] == 1 and w.f.book_epochs["yes"] == 1


def test_bbo_never_supplies_a_crossing_endpoint():
    w = Wire()
    assert w.reference(endpoint_kind="bbo") is None
    unknown = [p["payload"] for k,p in w.rows if p["event"] == "ROUND_UNKNOWN"]
    assert any(x["reason"] == "EXPLICIT_CROSSING_BRACKET_UNPROVEN_OR_OVER_50MS" for x in unknown)


def test_full_book_omitting_anchor_ends_root_without_inventing_quantity_endpoint():
    w = Wire()
    w.reference()
    w.send(1.55, {"event_type": "book", "asset_id": "yes",
                  "bids": [{"price": ".39", "size": "100"}],
                  "asks": [{"price": ".42", "size": "100"}]})
    assert not w.f.core.campaigns and not w.f.core.signals
    absence = [p["payload"] for _,p in w.rows if p["event"] == "ORIGINAL_LEVEL_ABSENT"]
    assert absence and absence[-1]["quantity_endpoint"] is False


def test_missing_or_out_of_envelope_clock_does_not_create_zero_padding_signals():
    w = Wire(proof=False)
    w.book(1)
    assert not w.f.books["yes"].complete
    assert w.f.reasons["yes"] == "M16_RECEIVER_CLOCK_EVIDENCE_UNAVAILABLE"
    assert w.f.core.receiver_quantum is None
    w = Wire()
    w.book(HORIZON_SECONDS + 1)
    assert w.f.reasons["yes"] == "M16_RECEIVED_OUTSIDE_CLOCK_PROOF_ENVELOPE"


def test_receiver_regression_ends_reference_instead_of_sorting_by_source():
    w = Wire()
    w.reference()
    w.send(1.45, {"event_type": "best_bid_ask", "asset_id": "yes", "best_bid": ".40", "best_ask": ".42"},
           available=1.55)
    assert not w.f.core.campaigns
    assert w.f.reasons["yes"] == "M16_RECEIVED_CLOCK_REGRESSION"


def test_parent_guard_break_restore_after_second_close_prevents_publication():
    w = Wire()
    w.reference()
    w.second_path()
    w.send(2.101, [
        {"event_type": "best_bid_ask", "asset_id": "yes", "best_bid": ".40", "best_ask": ".42"},
        {"event_type": "price_change", "price_changes": [
            {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "82"},
            {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "95"}]},
    ])
    assert not w.f.core.signals and not w.f.core.campaigns
    assert not any(p["event"] == "SIGNAL_CONFIRMED" for _,p in w.rows)


def test_unique_sell_duplicate_is_not_accumulated_twice():
    w = Wire()
    w.book(.99)
    w.hit(1, identity="r1")
    w.hit(1, identity="r1")
    r = next(iter(w.f.core.rounds.values()))
    assert r.path.hits == 10 and w.f.counts["DUPLICATE_DIRECT_TRADE"] == 1


def test_tick_or_reconnect_ends_old_generation_without_reference_revival():
    w = Wire()
    w.reference()
    w.send(1.55, {"event_type": "tick_size_change", "asset_id": "yes", "new_tick_size": ".001"})
    assert not w.f.core.campaigns and not w.f.books["yes"].tick_verified
    w.f.connect(now=1.56, wall=1001.56, seq=w.seq + 1)
    assert not w.f.core.campaigns and not w.f.core.rounds
