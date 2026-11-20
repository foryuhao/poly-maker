"""Contract examples exercise paths and causal boundaries, not implementation mirrors."""

from decimal import Decimal as D

import pytest
from sop_signals.m12 import M12


class Tape:
    def __init__(self):
        self.events = []
        self.s = M12(lambda kind, payload: self.events.append((kind, payload)))
        self.seq = 0

    def at(self, now):
        self.seq += 1
        self.s.clock(now, self.seq)

    def hit(self, now, direction, token="yes", price=None, quantity="10"):
        self.at(now)
        self.s.trade(
            token=token,
            side=direction,
            price=D(price or (".40" if direction == "SELL" else ".42")),
            quantity=D(quantity),
            received=now,
            q0=D(100),
            generation=1,
        )

    def depth(self, now, side, q, token="yes", received=None):
        self.at(now)
        self.s.depth(
            token,
            side,
            D(".40" if side == "BID" else ".42"),
            D(q),
            now if received is None else received,
        )

    def pair_path(self, bid_close="95", ask_close="82"):
        self.hit(1.0, "SELL")
        self.hit(1.0, "BUY")
        self.depth(1.01, "BID", "80")
        self.depth(1.01, "ASK", "80")
        self.depth(1.2, "BID", bid_close)
        self.depth(1.2, "ASK", ask_close)

    def close(self, now=1.501, covered=True):
        self.at(now)
        self.s.close(now, lambda *_: covered)
        self.s.publish()


def test_baseline_two_independent_hits_confirm_and_expire_without_renewal():
    t = Tape()
    t.pair_path()
    t.close()
    assert len(t.s.signals) == 1
    candidate = next(iter(t.s.signals.values()))
    assert candidate["anchor_price"] == D(".40")
    assert candidate["deadline"] == pytest.approx(4.501)
    t.close(1.502)
    assert t.s.counts["SIGNAL_CONFIRMED"] == 1
    t.at(candidate["deadline"])
    assert not t.s.signals


@pytest.mark.parametrize("q,expected", [("90", True), ("89.99", False)])
def test_strict_bid_close_recovery(q, expected):
    t = Tape()
    t.pair_path(bid_close=q)
    t.close()
    assert bool(t.s.signals) is expected


def test_strict_stable_cannot_use_legacy_hysteresis():
    t = Tape()
    t.pair_path()
    t.depth(1.42, "BID", "89")  # 45% recovery resets strict hold.
    t.depth(1.45, "BID", "95")  # Only 50ms at window close.
    t.close()
    assert not t.s.signals


@pytest.mark.parametrize("side,q", [("ASK", "90"), ("BID", "87.99"), ("ASK", "0"), ("BID", "0")])
def test_atomic_guard_break_cannot_be_hidden_by_recovery(side, q):
    t = Tape()
    t.pair_path()
    t.close()
    t.depth(1.52, side, q)
    t.depth(1.52, side, "82" if side == "ASK" else "95")
    t.s.publish()
    assert not t.s.signals
    assert t.s.counts["SIGNAL_CONFIRMED"] == 1


@pytest.mark.parametrize("side,q", [("ASK", "90"), ("BID", "87.99")])
def test_post_window_publication_checks_entire_known_path(side, q):
    t = Tape()
    t.pair_path()
    t.depth(1.51, side, q)
    t.depth(1.52, side, "82" if side == "ASK" else "95")
    t.close(1.53)
    assert not t.s.signals
    assert t.s.counts["PUBLICATION_REJECTED"] >= 1


def test_late_processed_forming_event_is_not_post_window_guard():
    t = Tape()
    t.pair_path()
    # Received within formation, processed before publication, still >=100ms hold.
    t.depth(1.51, "BID", "85", received=1.3)
    t.depth(1.52, "BID", "95", received=1.35)
    t.close(1.53)
    assert len(t.s.signals) == 1


@pytest.mark.parametrize("lateness,covered", [(0.051, True), (0.001, False)])
def test_missing_coverage_or_late_close_is_unknown(lateness, covered):
    t = Tape()
    t.pair_path()
    t.close(1.5 + lateness, covered)
    assert not t.s.signals
    assert t.s.counts["ROUND_UNKNOWN"] == 2


def test_available_clock_regression_is_rejected():
    t = Tape()
    t.at(1.2)
    with pytest.raises(ValueError, match="CLOCK_REGRESSION"):
        t.at(1.1)


def test_sides_of_different_tokens_never_pair():
    t = Tape()
    t.hit(1.0, "SELL", token="yes")
    t.hit(1.0, "BUY", token="no")
    t.depth(1.01, "BID", "80")
    t.depth(1.01, "ASK", "80", token="no")
    t.depth(1.2, "BID", "95")
    t.depth(1.2, "ASK", "82", token="no")
    t.close(1.5)
    assert t.s.counts["ROUND_CLOSED"] == 0
    t.close()
    assert not t.s.signals


def test_late_depth_cannot_rewrite_a_published_round():
    t = Tape()
    t.pair_path()
    t.close()
    t.depth(1.52, "ASK", "82", received=1.49)
    assert not t.s.signals
    assert t.s.quality["yes"] == "LATE_INPUT_TO_CLOSED_ROUND"


def test_reconnect_or_tick_change_ends_all_old_evidence():
    t = Tape()
    t.pair_path()
    t.close()
    t.s.invalidate("yes", "GENERATION_CHANGED")
    assert not t.s.signals and not t.s.components and not t.s.rounds
    t.s.publish()
    assert t.s.counts["SIGNAL_CONFIRMED"] == 1


def test_release_drops_paths_and_callbacks_are_not_retained_in_objects():
    t = Tape()
    t.pair_path()
    t.close()
    t.s.release()
    assert not any((t.s.signals, t.s.rounds, t.s.components, t.s.used_asks, t.s.last_close))
