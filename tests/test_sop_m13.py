"""M13 original-SOP causal paths, infeasible headroom and immutable roots."""

from decimal import Decimal as D

import pytest
from sop_signals.m13 import M13


class Tape:
    def __init__(self, parameters=None):
        self.events, self.seq = [], 0
        self.s = M13(lambda kind, payload: self.events.append((kind, payload)), parameters)
        self.asks = {D(".42"): D(100), D(".43"): D(100), D(".44"): D(100)}
        self.book(0.5, self.asks)

    def at(self, now, subindex=-1):
        self.seq += 1
        self.s.clock(now, self.seq, subindex)

    def hit(self, now, quantity="10", *, received=None, token="yes", side="SELL", price=".40", q0="100"):
        self.at(now)
        self.s.trade(token=token, side=side, price=D(price), quantity=D(quantity),
                     received=now if received is None else received, q0=D(q0), generation=1)

    def depth(self, now, quantity, *, received=None, token="yes", price=".40"):
        self.at(now)
        self.s.depth(token, "BID", D(price), D(quantity), now if received is None else received)

    def book(self, now, asks, *, received=None, covered=True, token="yes"):
        self.at(now)
        self.asks = dict(asks)
        self.s.book(token, asks, now if received is None else received, 1, lambda *_: covered)

    def close(self, now, *, covered=True, publish=True):
        self.at(now)
        self.s.close(now, lambda *_: covered)
        if publish:
            self.s.publish()

    def support(self, end_quantity="94"):
        self.hit(1)
        self.depth(1.01, "80")
        self.depth(1 + min(.20, self.s.p.round_seconds - .125), end_quantity)
        self.close(1 + self.s.p.round_seconds + .001)

    def seed(self, end_quantity="94"):
        self.support(end_quantity)
        self.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
        self.close(1.701)
        return self.s.withdrawals["yes"]

    def retest(self, start=1.72, end_quantity="93", publish=True):
        self.hit(start, "5", q0="94")
        self.depth(start + .01, "89")
        self.depth(start + min(.20, self.s.p.round_seconds - .125), end_quantity)
        self.close(start + self.s.p.round_seconds + .001, publish=publish)

    def reasons(self):
        return [v.get("reason") for _, v in self.events]


def test_baseline_first_new_sell_retest_and_native_lw_historical_handoff():
    t = Tape()
    w = t.seed()
    assert w.seed["support"]["r"] == D(".7")
    t.retest(start=2.3)  # Qualified before native TTL; closes after it.
    assert w.native_ended and len(t.s.signals) == 1
    signal = next(iter(t.s.signals.values()))
    assert signal["anchor_price"] == D(".40")
    assert signal["deadline"] == pytest.approx(4.701)
    assert signal["bid"]["guard"] == D(91)
    assert signal["prior_support"]["guard"] == D(88)
    assert all(v.get("strategy") == "M13" for _, v in t.events)
    assert signal["id"].startswith("M13:")
    t.at(signal["deadline"])
    assert not t.s.signals and not t.s.withdrawals


@pytest.mark.parametrize("q0,reason", [
    ("88.1", "RETEST_NO_DEPLETION_HEADROOM"),
    ("90", "RETEST_REFERENCE_COMPARISON_NO_HEADROOM"),
])
def test_headroom_constraints_end_root_without_waiting_for_replenishment(q0, reason):
    t = Tape()
    t.seed()
    t.hit(1.72, "5", q0=q0)
    assert not t.s.withdrawals and reason in t.reasons()
    t.depth(1.8, "100")
    t.close(2.3)
    assert not t.s.signals


def test_exact_headroom_equality_is_feasible_not_automatic_confirmation():
    t = Tape()
    t.hit(1, ".5", q0="10")
    t.depth(1.01, "9.5")
    t.depth(1.2, "10.2")
    t.close(1.501)
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(1.701)
    t.hit(1.72, ".5", q0="10.2")  # Old floor 9.7, headroom=min hit=.5.
    assert t.s.withdrawals and not t.s.signals
    assert "RETEST_REFERENCE_COMPARISON_NO_HEADROOM" not in t.reasons()


def test_old_floor_cannot_break_then_recover_during_retest():
    t = Tape()
    t.seed()
    t.hit(1.72, "5", q0="94")
    t.depth(1.73, "87.99")
    t.depth(1.8, "100")
    t.close(2.221)
    assert not t.s.signals and not t.s.withdrawals


def test_first_failed_round_cannot_be_replaced_by_a_later_success():
    t = Tape()
    t.seed()
    t.hit(1.72, ".1", q0="94")
    t.depth(1.73, "94")
    t.close(2.221)
    assert "FIRST_RETEST_FAILED_OR_EXPIRED" in t.reasons()
    t.hit(2.23, "5", q0="94")
    t.depth(2.24, "89")
    t.depth(2.4, "93")
    t.close(2.731)
    assert not t.s.signals


def test_received_before_seed_but_processed_after_seed_is_not_new_retest():
    t = Tape()
    w = t.seed()
    t.hit(1.73, "5", received=1.70, q0="94")
    assert w.retest_id is None
    t.depth(1.74, "89")
    t.depth(1.9, "93")
    t.close(2.201)
    assert not t.s.signals and w.retest_id is None


def test_same_received_time_as_seed_is_not_strictly_new():
    t = Tape()
    w = t.seed()
    t.hit(1.701, "5", q0="94")
    assert w.retest_id is None


def test_qualification_must_precede_native_deadline_not_just_round_start():
    t = Tape()
    t.seed()
    t.hit(2.40, ".1", q0="94")
    t.depth(2.43, "89")
    t.hit(2.451, "5", q0="94")
    assert not t.s.withdrawals
    assert "RETEST_NOT_QUALIFIED_BEFORE_ORIGINAL_DEADLINE" in t.reasons()


def test_completed_retest_is_not_published_before_parent_batch_guard():
    t = Tape()
    t.seed()
    t.retest(publish=False)
    assert not t.s.signals
    t.depth(2.221, "90")  # New floor=91; later row in same raw batch.
    t.s.publish()
    assert not t.s.signals and t.s.counts["SIGNAL_CONFIRMED"] == 0


def test_retest_publication_checks_post_window_breach_even_after_recovery():
    t = Tape()
    t.seed()
    t.hit(1.72, "5", q0="94")
    t.depth(1.73, "89")
    t.depth(1.9, "93")
    t.depth(2.225, "90")
    t.depth(2.23, "93")
    t.close(2.24)
    assert not t.s.signals
    assert "POST_WINDOW_GUARD" in t.reasons()


def test_cumulative_buy_prehistory_does_not_expire_with_rolling_cache():
    t = Tape()
    t.support()
    t.hit(1.51, "50", side="BUY", price=".43")
    t.book(1.6, {D(".42"): D(100), D(".43"): D(20), D(".44"): D(20)})
    t.close(1.701)
    w = t.s.withdrawals["yes"]
    assert w.buy == D(50)
    t.retest()
    t.at(4.5)
    assert not t.s.buy_history["yes"] and w.buy == D(50)
    t.hit(4.51, "80", side="BUY", price=".43")
    assert not t.s.signals and "LW_SUPPLY_RESTORED" in t.reasons()


def test_uncovered_pre_start_flow_is_unknown_not_zero_buy():
    t = Tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)}, covered=False)
    assert not t.s.withdrawals and t.s.counts["LW_UNKNOWN"] == 1


def test_lw_fixed_levels_and_original_front_not_dynamic_new_best_three():
    t = Tape()
    w = t.seed()
    t.book(1.8, {D(".41"): D(10000), **t.asks})
    assert w.levels == (D(".42"), D(".43"), D(".44"))
    t.book(1.9, {D(".41"): D(10000), D(".43"): D(40), D(".44"): D(40)})
    assert not t.s.withdrawals and "LW_ORIGINAL_FRONT_ASK_LOST" in t.reasons()


def test_support_publication_rejects_post_window_breach_before_recovery():
    t = Tape()
    t.hit(1)
    t.depth(1.01, "80")
    t.depth(1.2, "94")
    t.depth(1.505, "87")
    t.depth(1.51, "94")
    t.close(1.52)
    assert not t.s.components


@pytest.mark.parametrize("failure", ["TICK_CHANGED", "CONNECTION_GAP", "BOOK_RECONSTRUCTED"])
def test_quality_and_reconstruction_end_all_paths_and_signals(failure):
    t = Tape()
    t.seed()
    t.retest()
    t.s.invalidate("yes", failure)
    assert not any((t.s.signals, t.s.components, t.s.rounds, t.s.withdrawals, t.s.ask_books, t.s.buy_history))


def test_other_token_cannot_start_retest_or_consume_buy_volume():
    t = Tape()
    w = t.seed()
    t.hit(1.72, "5", token="no", q0="94")
    t.hit(1.73, "100", token="no", side="BUY", price=".43")
    assert w.retest_id is None and w.buy == 0 and not t.s.signals


def test_release_drops_old_supports_roots_and_flow_buffers():
    t = Tape()
    t.seed()
    t.retest()
    t.s.release()
    assert not any((t.s.signals, t.s.components, t.s.rounds, t.s.withdrawals,
                    t.s.ask_books, t.s.buy_history, t.s.lw_last_end, t.s.ready_retests))


def test_latest_support_at_first_overlap_is_frozen_no_fallback_after_failure():
    t = Tape()
    t.support()
    old_id = next(iter(t.s.components))
    t.hit(1.51, "5", q0="94")
    t.depth(1.52, "89")
    t.depth(1.72, "93")
    t.close(2.011)
    newest = max(t.s.components.values(), key=lambda c: c.key)
    t.book(2.1, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(2.201)
    assert t.s.withdrawals["yes"].seed["support"]["id"] == newest.id != old_id
    t.depth(2.22, "90")  # Latest guard=91 fails; older guard=88 still valid.
    t.s.publish()
    assert old_id in t.s.components and not t.s.withdrawals


def test_successful_retest_replaces_old_support_deadline_without_renewing_lw():
    t = Tape()
    t.support()
    old_deadline = next(iter(t.s.components.values())).deadline
    t.book(8.5, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(8.601)
    t.retest(start=8.62)
    assert len(t.s.signals) == 1
    deadline = next(iter(t.s.signals.values()))["deadline"]
    assert deadline == pytest.approx(11.601)
    t.at(old_deadline)
    assert len(t.s.signals) == 1
    t.s.publish()
    assert next(iter(t.s.signals.values()))["deadline"] == deadline


def test_lw_formation_and_retest_publication_lateness_are_unknown():
    t = Tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(1.751)
    assert not t.s.withdrawals and t.s.counts["LW_UNKNOWN"] == 1
    t = Tape()
    t.seed()
    t.hit(1.72, "5", q0="94")
    t.depth(1.73, "89")
    t.depth(1.9, "93")
    t.close(2.271)
    assert not t.s.signals and t.s.counts["ROUND_UNKNOWN"] == 1


def test_new_low_resets_strict_stability_even_with_unchanged_old_floor():
    t = Tape()
    t.seed()
    t.hit(1.72, "5", q0="94")
    t.depth(1.73, "89")
    t.depth(1.9, "93")
    t.depth(2.14, "88.5")
    t.depth(2.15, "93")  # Only 70ms recovery before 2.22 close.
    t.close(2.221)
    assert not t.s.signals


def test_late_buy_cannot_rewrite_closed_lw_formation():
    t = Tape()
    t.seed()
    t.hit(1.72, "1", side="BUY", price=".43", received=1.65)
    assert not t.s.withdrawals and "LATE_INPUT_TO_CLOSED_LW_FORMATION" in t.reasons()


def test_common_lw_does_not_import_standalone_exact_three_profile():
    t = Tape()
    t.support()
    t.book(1.51, {D(".42"): D(100), D(".43"): D(100)})
    # The previous three-level root was triggered by removing .44, and is
    # still the binding original root; resetting it would choose a new base.
    w = t.s.withdrawals["yes"]
    assert len(w.levels) == 3
    t.s.end_withdrawal("yes", "SYNTHETIC_PROFILE_BOUNDARY")
    t.book(1.52, {D(".42"): D(100), D(".43"): D(20)})
    assert len(t.s.withdrawals["yes"].levels) == 2


def test_lw_confirmation_waits_for_same_batch_buy_deduction():
    t = Tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(1.701, publish=False)
    assert t.s.counts["LW_CONFIRMED"] == 0
    # Loss120, BUY80 leaves40: maintenance passes but formation fails. This
    # later row belongs to the original publishing batch, not a post-signal hit.
    t.hit(1.701, "80", side="BUY", price=".43")
    t.s.publish()
    assert t.s.counts["LW_CONFIRMED"] == 0 and not t.s.withdrawals["yes"].seed


def test_tied_latest_support_keys_remain_unknown_no_arbitrary_price_choice():
    t = Tape()
    t.hit(1, price=".40")
    t.hit(1, price=".39")
    t.depth(1.01, "80", price=".40")
    t.depth(1.01, "80", price=".39")
    t.depth(1.2, "94", price=".40")
    t.depth(1.2, "94", price=".39")
    t.close(1.501)
    assert len(t.s.components) == 2
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(1.701)
    assert not t.s.withdrawals and "LATEST_SUPPORT_KEY_TIED_UNPROVEN_SELECTION" in t.reasons()
