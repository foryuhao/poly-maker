import json
from decimal import Decimal as D

import pytest
from sop_signals.feed import Feed


class Wire:
    def __init__(self, strategy="M12"):
        self.rows = []
        self.f = Feed(
            "condition",
            ("yes", "no"),
            {"yes": ".01", "no": ".01"},
            lambda k, v: self.rows.append((k, v)),
            strategy=strategy,
        )
        self.seq = 0
        self.f.connect(now=0, wall=1000, seq=0)

    def send(self, received, row, *, available=None):
        self.seq += 1

        def envelope(r):
            return {"market": "condition", "timestamp": str(int((1000 + received) * 1000)), **r}

        row = [envelope(r) for r in row] if isinstance(row, list) else envelope(row)
        self.f.apply(
            json.dumps(row),
            now=received if available is None else available,
            wall=1000 + (received if available is None else available),
            received=received,
            seq=self.seq,
        )

    def book(self, t=1.0, token="yes"):
        self.send(
            t,
            {
                "event_type": "book",
                "asset_id": token,
                "bids": [{"price": ".40", "size": "100"}],
                "asks": [{"price": ".42", "size": "100"}],
            },
        )

    def hit(self, t, side="SELL", token="yes", identity="trade1"):
        self.send(
            t,
            {
                "event_type": "last_trade_price",
                "asset_id": token,
                "price": ".40" if side == "SELL" else ".42",
                "size": "10",
                "side": side,
                "trade_id": identity,
            },
        )


def test_only_direct_origin_is_used_no_complementary_trade_pollution():
    w = Wire()
    w.book()
    w.book(1.0, "no")
    w.hit(1.01)
    assert len(w.f.core.rounds) == 1
    assert next(iter(w.f.core.rounds.values())).token == "yes"


def test_explicit_unique_trade_retransmit_does_not_double_quantity():
    w = Wire()
    w.book()
    w.hit(1.01)
    w.hit(1.01)
    assert next(iter(w.f.core.rounds.values())).hits == D(10)
    assert w.f.counts["DUPLICATE_DIRECT_TRADE"] == 1


def test_same_transaction_tuple_without_unique_match_id_is_unknown_not_extra_hit():
    w = Wire()
    w.book()
    r = {
        "event_type": "last_trade_price",
        "asset_id": "yes",
        "side": "SELL",
        "price": ".40",
        "size": "10",
        "transaction_hash": "0x" + "a" * 64,
    }
    w.send(1.01, r)
    w.send(1.01, r)
    assert not w.f.core.rounds
    assert w.f.reasons["yes"] == "DIRECT_TRADE_IDENTITY_AMBIGUOUS"


def test_pre_hit_same_millisecond_book_is_unknown():
    w = Wire()
    w.book()
    w.hit(1.0)
    assert not w.f.core.rounds
    assert w.f.reasons["yes"] == "PRE_HIT_LEVEL_ORDER_UNPROVEN"


def test_missing_direction_is_unknown_and_never_defaults_to_buy():
    w = Wire()
    w.book()
    w.send(
        1.01,
        {
            "event_type": "last_trade_price",
            "asset_id": "yes",
            "price": ".4",
            "size": "10",
            "trade_id": "bad",
        },
    )
    assert not w.f.core.rounds
    assert w.f.reasons["yes"].startswith("PARSE_OR_SOURCE_GAP")


def test_tick_change_and_reconnect_prevent_old_root_revival():
    w = Wire()
    w.book()
    w.hit(1.01)
    w.send(1.02, {"event_type": "tick_size_change", "asset_id": "yes", "new_tick_size": ".001"})
    assert not w.f.core.rounds and not w.f.books["yes"].tick_verified
    w.f.gap("DISCONNECT")
    w.f.connect(now=1.03, wall=1001.03, seq=4)
    assert not w.f.core.rounds and not w.f.books["yes"].complete
    assert w.f.generation == 2


def test_parser_preserves_internal_depth_break_before_restore():
    w = Wire()
    w.book()
    w.hit(1.01)
    w.send(
        1.02,
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "0"},
                {"asset_id": "yes", "side": "BUY", "price": ".40", "size": "100"},
            ],
        },
    )
    assert next(iter(w.f.core.rounds.values())).missing


def test_coverage_is_not_inferred_from_different_token_or_timer():
    w = Wire()
    w.book()
    w.book(1.0, "no")
    w.hit(1.01)
    w.book(1.15, "no")
    assert not w.f.covered("yes", 1.01, 1.51)


def test_backwards_source_time_requires_snapshot_rebuild():
    w = Wire()
    w.book()
    w.hit(1.01)
    w.send(
        1.02,
        {
            "event_type": "best_bid_ask",
            "asset_id": "yes",
            "best_bid": ".40",
            "best_ask": ".42",
            "timestamp": "1001005",
        },
    )
    assert w.f.reasons["yes"] == "UNRECONSTRUCTABLE_SOURCE_ORDER"
    assert not w.f.books["yes"].complete


def test_raw_frame_and_lateness_clocks_do_not_fabricate_timely_facts():
    w = Wire()
    w.book()
    w.hit(1.01)
    w.f.timer(now=1.57, wall=1001.57, seq=3)
    assert not w.f.core.components
    assert w.f.counts["QUALITY_UNKNOWN"] > 0


def test_expiry_publication_cursor_matches_the_input_that_made_it_available():
    w = Wire()
    w.book()
    w.hit(1.01, identity="bid-hit")
    w.hit(1.01, side="BUY", identity="ask-hit")
    for t, bid, ask in ((1.02, "80", "80"), (1.20, "95", "82")):
        w.send(
            t,
            {
                "event_type": "price_change",
                "price_changes": [
                    {"asset_id": "yes", "side": "BUY", "price": ".40", "size": bid},
                    {"asset_id": "yes", "side": "SELL", "price": ".42", "size": ask},
                ],
            },
        )
    for t in (1.3, 1.4, 1.5):
        w.send(
            t,
            {"event_type": "best_bid_ask", "asset_id": "yes", "best_bid": ".40", "best_ask": ".42"},
        )
    w.f.timer(now=1.511, wall=1001.511, seq=20)
    signal = next(iter(w.f.core.signals.values()))
    w.rows.clear()
    w.f.timer(now=signal["deadline"], wall=1000 + signal["deadline"], seq=21)
    ended = next(v for _, v in w.rows if v["event"] == "SIGNAL_ENDED")
    assert ended["input_seq"] == 21
    assert ended["available"] == ended["payload"]["available"] == signal["deadline"]


def test_release_breaks_runtime_graph():
    w = Wire()
    w.book()
    w.hit(1.01)
    w.f.release_runtime()
    assert not w.f.books and not w.f.core.rounds
    assert w.f.emit is None and w.f.core.emit is None


def test_snapshot_rebuild_has_a_new_evidence_generation_without_socket_reconnect():
    w = Wire()
    w.book()
    w.hit(1.01)
    old = next(iter(w.f.core.rounds.values()))
    w.f.unavailable("yes", "UNRECONSTRUCTABLE_SOURCE_ORDER", rebuild=True)
    w.book(1.02)
    w.hit(1.03, identity="new-hit")
    new = next(iter(w.f.core.rounds.values()))
    assert new.generation > old.generation
    assert new.id != old.id and w.f.generation == 1


def test_ordinary_healthy_snapshot_does_not_invent_a_reconnect_or_restart_root():
    w = Wire()
    w.book()
    w.hit(1.01)
    old = next(iter(w.f.core.rounds.values()))
    w.book(1.02)
    assert next(iter(w.f.core.rounds.values())).id == old.id


@pytest.mark.parametrize("raw", ["null", "[null]", "{", "42"])
def test_malformed_raw_events_become_unknown_without_none_get_crash(raw):
    w = Wire()
    w.book()
    w.f.apply(raw, now=1.01, wall=1001.01, received=1.01, seq=2)
    assert not w.f.good["yes"]
    assert w.f.reasons["yes"].startswith("PARSE_OR_SOURCE_GAP")
