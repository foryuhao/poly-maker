"""Raw input causality and primitive boundary tests for the frozen SOP."""

from decimal import Decimal as D

import pytest
from maker_signals.feed import ResearchFeed
from maker_signals.model import Registry


class Tape:
    def __init__(self):
        self.rows, self.seq = [], 0
        self.feed = ResearchFeed(
            "market",
            ("up", "down"),
            {"up": ".01", "down": ".01"},
            lambda k, p: self.rows.append((k, p)),
        )
        self.feed.connect(now=0, wall=1000, seq=0)

    def apply(self, at, rows, available=None):
        self.seq += 1
        if isinstance(rows, dict):
            rows = [rows]
        rows = [{"market": "market", "timestamp": str((1000 + at) * 1000), **row} for row in rows]
        self.feed.apply(
            rows,
            now=at if available is None else available,
            wall=1000 + (at if available is None else available),
            received=at,
            seq=self.seq,
        )

    def books(self, at, bid=100, ask=100):
        self.apply(
            at,
            [
                {
                    "event_type": "book",
                    "asset_id": token,
                    "bids": [{"price": p, "size": str(q)} for p, q in bids],
                    "asks": [{"price": p, "size": str(q)} for p, q in asks],
                }
                for token, bids, asks in (
                    (
                        "up",
                        [(".40", bid), (".39", 100), (".38", 100)],
                        [(".42", ask), (".43", 100), (".44", 100)],
                    ),
                    (
                        "down",
                        [(".58", ask), (".57", 100), (".56", 100)],
                        [(".60", bid), (".61", 100), (".62", 100)],
                    ),
                )
            ],
        )

    def trade(self, at, side, quantity=1, price=None, identity=None):
        self.apply(
            at,
            {
                "event_type": "last_trade_price",
                "asset_id": "up",
                "side": side,
                "price": price or (".40" if side == "SELL" else ".42"),
                "size": str(quantity),
                "transaction_hash": identity or "0x" + f"{self.seq + 1:064x}",
            },
        )

    def tick(self, at, pending=()):
        self.seq += 1
        self.feed.timer(now=at, wall=1000 + at, seq=self.seq, pending=pending)

    def facts(self, kind):
        return [f for f in self.feed.registry.facts.values() if f.kind == kind and f.token == "up"]


def test_same_direction_replacement_does_not_invalidate_or_renew_frozen_fact():
    r = Registry(lambda *_: None)
    a = r.add(
        kind="RF", token="up", generation=1, confirmed=1, deadline=9, start=0, end=1, input_seq=1
    )
    b = r.add(
        kind="RF", token="up", generation=1, confirmed=2, deadline=10, start=1, end=2, input_seq=2
    )
    assert r.valid(a, 2) and r.valid(b, 2) and a.deadline == 9
    r.end(a, 3, "FAILED")
    assert not r.valid(a, 3) and r.valid(b, 3)


def test_ap_does_not_confirm_before_sealed_window_and_counts_both_directions():
    t = Tape()
    t.books(0)
    t.trade(0.01, "SELL", 2)
    for at in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70):
        if at == 0.60:
            t.tick(0.51)
        t.books(at)
        if at in (0.30, 0.60):
            t.trade(at + 0.001, "SELL", 2)
    t.tick(0.759)
    assert not t.facts("AP_SELL")
    t.tick(0.76)
    assert len(t.facts("AP_SELL")) == 1
    f = t.facts("AP_SELL")[0]
    assert f.start == 0.01 and abs(f.end - 0.76) < 1e-8 and f.confirmed == 0.76
    assert f.deadline == 1.51 and f.values["volume"] == 6
    assert any(f.kind == "AP_BUY" and f.token == "down" for f in t.feed.registry.facts.values())


def test_late_window_processing_is_unavailable_not_backdated_confirmation():
    t = Tape()
    t.books(0)
    t.trade(0.01, "SELL", 2)
    for at in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        if at == 0.60:
            t.tick(0.51)
        t.books(at)
        if at in (0.3, 0.6):
            t.trade(at + 0.001, "SELL", 2)
    t.tick(0.811, pending=[0.75])
    assert not t.facts("AP_SELL")
    assert not t.feed.good["up"]


def test_duplicate_direct_and_mirrored_trade_is_one_economic_view_per_token():
    t = Tape()
    t.books(0)
    tx = "0x" + "a" * 64
    t.trade(0.01, "SELL", 2, identity=tx)
    t.apply(
        0.01,
        {
            "event_type": "last_trade_price",
            "asset_id": "down",
            "side": "BUY",
            "price": ".60",
            "size": "2",
            "transaction_hash": tx,
        },
    )
    assert len(t.feed.flows["up"].trades) == len(t.feed.flows["down"].trades) == 1
    assert t.feed.counts["DUPLICATE_ECONOMIC_VIEW_IGNORED"] == 2


def test_missing_identity_and_book_coverage_never_mean_zero_flow():
    t = Tape()
    t.books(0)
    t.apply(
        0.01,
        {
            "event_type": "last_trade_price",
            "asset_id": "up",
            "side": "SELL",
            "price": ".40",
            "size": "2",
        },
    )
    assert not any(t.feed.good.values())
    t.books(0.10)
    t.apply(
        0.40,
        {"event_type": "best_bid_ask", "asset_id": "down", "best_bid": ".58", "best_ask": ".60"},
    )
    assert not t.feed.good["up"]


def test_round_starts_on_first_small_hit_and_cannot_use_later_depth_for_old_window():
    t = Tape()
    t.books(0)
    t.trade(0.01, "SELL", ".1")
    assert t.feed.refills["up"].observer.round.start == 0.01
    t.books(0.1, bid=90)
    t.trade(0.11, "SELL", "10")
    t.books(0.2, bid=96)
    t.books(0.3, bid=96)
    t.books(0.4, bid=96)
    t.books(0.5, bid=96)
    t.tick(0.509)
    assert not t.facts("ROUND")
    t.books(0.51, bid=91)
    f = t.facts("ROUND")[0]
    assert f.values["stable"] and f.values["recovery"] == D(".6")
    assert f.values["initial"] == 100 and f.values["hit"] == D("10.1")


def test_restore_removed_anchor_does_not_restore_old_round():
    t = Tape()
    t.books(0)
    t.trade(0.01, "SELL", 1)
    t.apply(
        0.1,
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "up", "side": "BUY", "price": ".40", "size": "0"},
                {"asset_id": "up", "side": "BUY", "price": ".40", "size": "100"},
            ],
        },
    )
    assert t.feed.refills["up"].observer.round is None
    assert not t.facts("RF_BID")


def play_m10_book_path(t):
    trades = {
        1: ("BUY", 10),
        27: ("BUY", 10),
        53: ("BUY", 10),
        80: ("SELL", 10),
        131: ("SELL", 8),
        182: ("SELL", "6.4"),
    }
    depth = {82: 90, 90: 96, 130: 100, 133: 92, 141: "96.8", 181: 100, 184: "93.6", 192: "97.44"}
    bid = 100
    for i in range(234):
        at = i / 100
        if i in depth:
            bid = depth[i]
        if i % 5 == 0 or i in depth:
            t.books(at, bid=bid)
        if i in trades:
            t.trade(at, *trades[i])
        t.tick(at)


def test_raw_book_and_order_flow_form_m10_two_returns_without_injected_candidates():
    from maker_signals.strategies import Strategies

    t = Tape()
    Strategies(t.feed)
    play_m10_book_path(t)
    candidates = [p["payload"] for k, p in t.rows if k == "SIGNAL" and p["strategy"] == "M10"]
    assert len(candidates) == 1
    f = candidates[0]
    assert f["deadline"] == pytest.approx(9.81)
    assert f["confirmed"] == pytest.approx(1.81)
    assert len(f["round_ids"]) == 2
    assert f["feature_values"]["R2_confirmed_available_at"] == f["confirmed"]


def test_bisected_coverage_is_equivalent_to_interval_definition_after_trim():
    import random

    from maker_signals.primitives import Flow

    rng = random.Random(511)
    for _ in range(200):
        flow = Flow()
        values = [rng.randrange(0, 4000) / 1000 for i in range(100)]
        flow.coverage.extend(values)
        flow.trim(4)
        start = rng.randrange(1000, 3000) / 1000
        end = start + 0.75
        expected = all(
            any(start + i * 0.25 <= x < start + (i + 1) * 0.25 for x in values if x >= 1)
            for i in range(3)
        )
        assert flow.covered(start, end) == expected


def test_symmetric_ex_requires_complete_quiet_window_with_token_coverage():
    t = Tape()
    for i in range(153):
        at = i / 100
        if i % 5 == 0:
            t.books(at)
        if i in (1, 27, 53):
            t.trade(at, "BUY", 2)
        t.tick(at)
        if i == 150:
            assert not t.facts("EX_BUY")
    assert len(t.facts("EX_BUY")) == 1
    assert any(f.kind == "EX_SELL" and f.token == "down" for f in t.feed.registry.facts.values())


def test_reverse_ap_ends_ex_formation_without_erasing_historical_ap():
    t = Tape()
    for i in range(153):
        at = i / 100
        if i % 5 == 0:
            t.books(at)
        if i in (1, 27, 53):
            t.trade(at, "BUY", 100)
        if i in (60, 86, 112):
            t.trade(at, "SELL", 10)
        t.tick(at)
    assert t.facts("AP_BUY") and t.facts("AP_SELL")
    assert not t.facts("EX_BUY")
    assert any(p["event"] == "EX_FORMATION_ENDED" for k, p in t.rows if k == "RESEARCH_EVIDENCE")


def test_book_parse_gap_requires_full_snapshot_not_just_a_later_delta():
    t = Tape()
    t.books(0)
    t.apply(
        0.01, {"event_type": "best_bid_ask", "asset_id": "up", "best_bid": ".39", "best_ask": ".42"}
    )
    assert not t.feed.books["up"].complete
    t.apply(
        0.02,
        {
            "event_type": "price_change",
            "price_changes": [{"asset_id": "up", "side": "BUY", "price": ".40", "size": "100"}],
        },
    )
    assert not t.feed.good["up"]
    t.books(0.03)
    assert t.feed.good["up"] and t.feed.books["up"].complete
