"""M16 campaign semantics, first attempts and negative-before-positive ordering."""

from decimal import Decimal as D

import pytest
from sop_signals.m16 import M16


class Tape:
    def __init__(self, *, quantum=".001"):
        self.events, self.seq = [], 0
        self.s = M16(lambda k, p: self.events.append((k, p)), receiver_quantum=quantum)

    def at(self, now, *, row=0):
        self.seq += 1
        self.s.clock(now, self.seq, row)

    def hit(self, now, quantity="10", *, received=None, token="yes", price=".40",
            q0="100", generation=1, side="SELL"):
        self.at(now)
        self.s.trade(token=token, side=side, price=D(price), quantity=D(quantity),
                     q0=D(q0), received=now if received is None else received,
                     generation=generation, input_ref=(self.seq, 0, 0), source=now + 1000)

    def depth(self, now, quantity, *, received=None, token="yes", price=".40", generation=1):
        self.at(now)
        self.s.depth(token, "BID", D(price), D(quantity), now if received is None else received,
                     generation=generation, input_ref=(self.seq, 0, 0), source=now + 1000,
                     kind="BID_PRICE_CHANGE")

    def close(self, now, *, covered=True, publish=True, watermark=None):
        self.at(now)
        self.s.close(now if watermark is None else watermark, lambda *_: covered)
        if publish:
            self.s.publish()

    def reference(self, *, low_at=1.22, high_at=1.24, token="yes", price=".40"):
        self.hit(1, token=token, price=price)
        self.depth(1.02, "80", token=token, price=price)
        self.depth(low_at, "85", token=token, price=price)
        self.depth(high_at, "95", token=token, price=price)
        self.close(1.501)
        return self.s.campaigns.get(token)

    def second(self, *, start=1.6, hits="12", q0="95", minimum="75", low="80", recovered="95",
               low_at=None, high_at=None, close_at=None, publish=True):
        self.hit(start, hits, q0=q0)
        self.depth(round(start + .01, 6), minimum)
        self.depth(round(start + .05, 6) if low_at is None else low_at, low)
        self.depth(round(start + .07, 6) if high_at is None else high_at, recovered)
        self.close(round(start + .501, 6) if close_at is None else close_at, publish=publish)

    def followup(self, *, minimum="85", low="88", recovered="95", low_at=2.26, high_at=2.28,
                 hits="10"):
        self.hit(2.2, hits, q0="95")
        self.depth(2.21, minimum)
        self.depth(low_at, low)
        self.depth(high_at, recovered)
        self.close(2.701)

    def reasons(self):
        return [p.get("reason") for _, p in self.events]


def test_reference_is_history_and_second_new_low_can_cross_the_old_floor():
    t = Tape()
    c = t.reference()
    assert c.reference["tau_low"] == D(".198") and not t.s.signals and not t.s.components
    root = c.id
    t.second()  # R2 trough75 is below old R1 floor88, but P never disappears.
    signal = next(iter(t.s.signals.values()))
    assert signal["root_id"] == root and signal["confirmed"] == 2.101
    assert signal["anchor_price"] == D(".40") and signal["deadline"] == 5.101
    assert signal["prior_reference"]["guard"] == 88 and signal["bid"]["guard"] == 83
    assert signal["prior_reference"]["tau_low"] == D(".198")
    assert signal["bid"]["tau_high"] == D(".062")
    assert signal["reference_only_before_confirmation"]
    assert all(p.get("strategy") == "M16" for _, p in t.events)


def test_old_floor_is_not_applied_between_reference_and_second_start():
    t = Tape()
    t.reference()
    t.depth(1.55, "50")
    assert "yes" in t.s.campaigns
    t.second(q0="50", hits="6", minimum="40", low="43", recovered="50")
    # Rejection is the reduced real SELL, not an accidental old-floor guard.
    reviews = [p for k, p in t.events if k == "SPEED_PAIR_REVIEW"]
    assert reviews and not reviews[-1]["checks"]["absolute_hit"]
    assert "ORIGINAL_LEVEL_GUARD" not in t.reasons()


@pytest.mark.parametrize("values,failed", [
    ({"hits": "9"}, "absolute_hit"), ({"hits": "21"}, "absolute_hit"),
    ({"q0": "200", "hits": "15", "minimum": "160", "low": "170", "recovered": "200"}, "normalized_hit"),
    ({"q0": "50", "hits": "12", "minimum": "40", "low": "43", "recovered": "50"}, "normalized_hit"),
    ({"minimum": "85", "low": "88"}, "normalized_loss"),
    ({"minimum": "60", "low": "70"}, "normalized_loss"),
    ({"recovered": "89"}, "not_shallower"),
    ({"low_at": 1.70, "high_at": 1.72}, "conservative_speed"),
])
def test_strength_recovery_and_conservative_speed_each_remain_required(values, failed):
    t = Tape()
    t.reference()
    t.second(**values)
    reviews = [p for k, p in t.events if k == "SPEED_PAIR_REVIEW"]
    assert reviews and not reviews[-1]["checks"][failed]
    assert not t.s.signals and not t.s.campaigns


@pytest.mark.parametrize("high_at,passed", [(1.707, True), (1.707001, False)])
def test_exact_half_speed_uses_slow_reference_lower_and_second_upper(high_at, passed):
    t = Tape()
    t.reference()
    t.second(low_at=1.69, high_at=high_at)
    assert bool(t.s.signals) == passed


def test_unmeasurable_or_too_fast_first_round_is_not_a_slow_reference():
    t = Tape()
    assert t.reference(low_at=1.08, high_at=1.10) is None
    assert not t.s.signals
    t = Tape(quantum=None)
    assert t.reference() is None
    assert t.s.counts["ROUND_UNKNOWN"] == 1


def test_exact_100ms_reference_lower_bound_is_allowed():
    t = Tape()
    assert t.reference(low_at=1.122, high_at=1.14).reference["tau_low"] == D(".100")


def test_first_failed_or_unmeasurable_second_cannot_be_skipped_for_a_third():
    t = Tape()
    root = t.reference().id
    t.second(recovered="80")
    assert not t.s.campaigns and not t.s.signals
    t.second(start=2.3)
    assert not t.s.signals
    assert not any(p.get("root_id") == root for k, p in t.events if k == "SIGNAL_CONFIRMED")
    t = Tape()
    t.reference()
    t.second(low_at=1.62, high_at=1.80)  # Strictly stable, but crossing gap180ms.
    assert "FIRST_SECOND_ROUND_FAILED_OR_TIMING_UNAVAILABLE" in t.reasons()


@pytest.mark.parametrize("received", [1.500, 1.501])
def test_first_sell_receipt_before_or_equal_reference_cannot_be_new(received):
    t = Tape()
    c = t.reference()
    t.hit(1.51, "12", received=received, q0="95")
    assert c.second_id is None
    t.hit(1.55, "1", q0="95")  # A later hit inside that same physical round.
    assert c.second_id is None
    assert next(iter(t.s.rounds.values())).role == "INELIGIBLE"


def test_round_received_before_reference_close_cannot_be_relabelled_by_later_hits():
    t = Tape()
    t.hit(1)
    t.depth(1.02, "80")
    t.depth(1.22, "85")
    t.depth(1.24, "95")
    t.hit(1.51, "12", received=1.505, q0="95")  # R1 close still pending.
    t.close(1.52)
    c = t.s.campaigns["yes"]
    t.hit(1.60, "1", q0="95")
    assert c.second_id is None and not t.s.rounds
    assert "PHYSICAL_ROUND_STARTED_BEFORE_REFERENCE_AVAILABLE" in t.reasons()
    t.second(start=2.01)
    assert t.s.signals


@pytest.mark.parametrize("start,close_at,passed", [(2.999, 3.5, True), (3.0, 3.501, False)])
def test_second_confirmation_is_strictly_before_reference_plus_two_seconds(start, close_at, passed):
    t = Tape()
    t.reference()
    t.second(start=start, close_at=close_at)
    assert bool(t.s.signals) == passed


def test_post_window_breach_cannot_be_hidden_by_recovery_before_close():
    t = Tape()
    t.reference()
    t.hit(1.6, "12", q0="95")
    t.depth(1.61, "75")
    t.depth(1.65, "80")
    t.depth(1.67, "95")
    t.depth(2.105, "82")
    t.depth(2.11, "95")
    t.close(2.111)
    assert not t.s.signals and not t.s.campaigns
    assert "POST_WINDOW_GUARD" in t.reasons()


def test_same_availability_guard_failure_between_close_and_publication_has_priority():
    t = Tape()
    t.reference()
    t.second(publish=False)
    assert t.s.campaigns["yes"].ready and not t.s.signals
    t.depth(2.101, "82", received=2.1001)
    t.depth(2.101, "95", received=2.1002)
    t.s.publish()
    assert not t.s.signals and not t.s.campaigns


def test_confirmation_after_atomic_close_boundary_does_not_backdate_a_signal():
    t = Tape()
    t.reference()
    t.second(publish=False)
    t.at(2.102)
    t.s.publish()
    assert not t.s.signals and "PUBLICATION_BOUNDARY_NOT_COMPLETED" in t.reasons()


def test_faster_later_round_does_not_renew_original_deadline():
    t = Tape()
    t.reference()
    t.second()
    deadline = next(iter(t.s.signals.values()))["deadline"]
    t.followup()
    assert next(iter(t.s.signals.values()))["deadline"] == deadline
    t.at(deadline)
    assert not t.s.signals and not t.s.campaigns and not t.s.components


@pytest.mark.parametrize("values,reason", [
    ({"low_at": 2.45, "high_at": 2.47}, "RECOVERY_SPEED_ADVANTAGE_ENDED"),
    ({"recovered": "92"}, "SUBSEQUENT_RECOVERY_WEAKENED"),
    ({"low_at": 2.22, "high_at": 2.40}, "SUBSEQUENT_TIMING_UNAVAILABLE"),
])
def test_subsequent_slow_shallow_or_unmeasurable_recovery_ends_wait(values, reason):
    t = Tape()
    t.reference()
    t.second()
    t.followup(**values)
    assert not t.s.signals and not t.s.campaigns
    assert any(reason in str(x) for x in t.reasons())


def test_nonqualified_later_round_provides_no_speed_evidence_and_no_renewal():
    t = Tape()
    t.reference()
    t.second()
    deadline = next(iter(t.s.signals.values()))["deadline"]
    t.followup(minimum="94.9", low="94.9", recovered="95", hits=".1")
    assert next(iter(t.s.signals.values()))["deadline"] == deadline


def test_fixed_second_floor_and_level_loss_end_immediately_without_revival():
    t = Tape()
    t.reference()
    t.second()
    t.depth(2.15, "82.99")
    t.depth(2.16, "100")
    assert not t.s.signals and "ORIGINAL_LEVEL_GUARD" in t.reasons()
    t = Tape()
    t.reference()
    t.depth(1.55, "0")
    t.depth(1.56, "100")
    assert not t.s.campaigns and not t.s.signals


def test_generation_unknown_late_closed_input_and_release_do_not_resurrect_roots():
    t = Tape()
    t.reference()
    t.second()
    t.at(2.15)
    t.s.new_generation("yes", 2)
    assert not t.s.signals and not t.s.campaigns and not t.s.rounds
    t = Tape()
    t.reference()
    t.depth(1.52, "85", received=1.49)
    assert not t.s.campaigns and "LATE_INPUT_TO_CLOSED_ROUND" in t.reasons()
    t.s.release()
    assert not any((t.s.rounds, t.s.components, t.s.signals, t.s.campaigns,
                    t.s.closed_windows, t.s.blocked_rounds, t.s.campaign_ends))


def test_other_token_price_or_buy_does_not_become_the_second_bid_round():
    t = Tape()
    c = t.reference()
    t.hit(1.55, "12", price=".39")
    t.hit(1.56, "12", token="no")
    t.hit(1.57, "12", side="BUY")
    assert c.second_id is None and not t.s.signals
