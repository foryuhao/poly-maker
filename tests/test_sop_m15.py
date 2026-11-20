"""M15 causal BUY handoff, residual capacity and nonrenewable old support."""

from decimal import Decimal as D

import pytest
from sop_signals.m15 import M15
from test_sop_m13 import Tape as SupportTape


class Tape(SupportTape):
    def __init__(self, parameters=None, asks=None):
        self.events, self.seq = [], 0
        self.s = M15(lambda kind, payload: self.events.append((kind, payload)), parameters)
        self.asks = asks or {D(".42"): D(100), D(".43"): D(100), D(".44"): D(100)}
        self.book(.5, self.asks)

    def depth(self, now, quantity, *, received=None, token="yes", price=".40", side="BID"):
        self.at(now)
        received = now if received is None else received
        self.s.depth(token, side, D(price), D(quantity), received)
        if side == "ASK":
            if D(quantity):
                self.asks[D(price)] = D(quantity)
            else:
                self.asks.pop(D(price), None)
            self.s.book(token, self.asks, received, 1, lambda *_: True)

    def retest(self, start=1.72, *, quantity="10", low="90", end="92", publish=True):
        self.hit(start, quantity, side="BUY", price=".42", q0="100")
        self.depth(start + .01, low, side="ASK", price=".42")
        self.depth(start + .2, end, side="ASK", price=".42")
        self.close(start + .501, publish=publish)


def test_first_new_weak_buy_keeps_old_bid_anchor_floor_and_original_clocks():
    t = Tape()
    w = t.seed()
    old = w.seed["support"].copy()
    t.retest(start=2.3)  # Qualifies before native LW expiry, finishes after it.
    assert w.native_ended and len(t.s.signals) == 1
    signal = next(iter(t.s.signals.values()))
    assert signal["anchor_price"] == old["price"] == D(".40")
    assert signal["bid"] == signal["prior_support"] == old
    assert signal["ask"]["side"] == "ASK" and signal["ask"]["r"] == D(".2")
    assert signal["deadline"] == pytest.approx(4.701)
    assert old["id"] in signal["component_ids"]
    assert all(p.get("strategy") == "M15" for _, p in t.events)
    assert w.id.startswith("M15:") and signal["id"].startswith("M15:")
    t.depth(2.9, "87.99")
    assert not t.s.signals and not t.s.withdrawals


def test_new_ask_does_not_extend_original_eight_second_support_lifetime():
    t = Tape()
    t.support()
    old_deadline = next(iter(t.s.components.values())).deadline
    t.book(8.5, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(8.601)
    t.retest(start=8.62)
    signal = next(iter(t.s.signals.values()))
    assert signal["deadline"] == old_deadline == pytest.approx(9.501)
    t.at(old_deadline)
    assert not t.s.signals and not t.s.withdrawals


@pytest.mark.parametrize("received", [1.70, 1.701])
def test_receipt_before_or_equal_seed_cannot_be_new_despite_later_input_key(received):
    t = Tape()
    w = t.seed()
    t.hit(1.73, "1", side="BUY", price=".42", received=received)
    assert w.retest_id is None and w.buy == 1


def test_preseed_buy_round_cannot_be_relabelled_by_its_next_buy():
    t = Tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.hit(1.65, "1", side="BUY", price=".42")
    t.close(1.701)
    w = t.s.withdrawals["yes"]
    t.hit(1.72, "1", side="BUY", price=".42")
    assert w.seed and w.retest_id is None and w.buy == 2
    assert t.s.counts["ROUND_STARTED"] == 1  # Only the proven strict Bid round.
    t.close(2.151)
    t.retest(start=2.16)
    assert w.retest_id and len(t.s.signals) == 1


def test_sell_or_buy_at_other_fixed_ask_is_not_the_front_ask_test():
    t = Tape()
    w = t.seed()
    t.hit(1.72, ".1")
    t.hit(1.73, "7", side="BUY", price=".43")
    assert w.retest_id is None and w.buy == 7
    t.hit(1.74, "50", side="BUY", price=".42", token="no")
    assert w.retest_id is None and w.buy == 7 and not t.s.signals


def test_first_failed_buy_attempt_ends_root_without_skipping_to_later_success():
    t = Tape()
    w = t.seed()
    t.retest(quantity=".1", low="100", end="100")
    assert "FIRST_RETEST_FAILED_OR_EXPIRED" in t.reasons()
    assert w.id not in [x.id for x in t.s.withdrawals.values()]
    t.hit(2.23, "10", side="BUY", price=".42")
    t.depth(2.24, "90", side="ASK", price=".42")
    t.close(2.731)
    assert t.s.counts["SIGNAL_CONFIRMED"] == 0


def capacity_seed():
    t = Tape(asks={D(".42"): D(40), D(".43"): D(30), D(".44"): D(30)})
    t.support()
    t.hit(1.51, "10", side="BUY", price=".43")
    t.book(1.6, {D(".42"): D(30), D(".43"): D(20), D(".44"): D(20)})
    t.close(1.701)
    return t, t.s.withdrawals["yes"]


def test_buy_first_and_depth_first_same_endpoint_have_different_causal_results():
    t, w = capacity_seed()  # A0=100,L=30,V=10,U=20,C=10.
    t.hit(1.72, "15", side="BUY", price=".42", q0="30")
    assert not t.s.withdrawals and w.buy == 25
    failed = [p for k, p in t.events if k == "LW_MAINTENANCE_FAILED"][-1]
    assert failed["loss"] == 30 and failed["residual"] == 5
    t.depth(1.73, "5", side="ASK", price=".43")
    assert not t.s.signals  # A later endpoint cannot revive the ended root.
    t, w = capacity_seed()
    t.depth(1.72, "5", side="ASK", price=".43")  # L=45 before BUY15.
    t.hit(1.73, "15", side="BUY", price=".42", q0="30")
    assert t.s.withdrawals["yes"] is w
    assert t.s.withdrawal_values(w) == (D(45), D(20)) and w.buy == 25
    assert w.retest_id and not t.s.signals  # Capacity is not confirmation.


def test_exact_remaining_buy_capacity_passes_but_is_not_automatic_signal():
    t, w = capacity_seed()
    t.hit(1.72, "10", side="BUY", price=".42", q0="30")
    assert t.s.withdrawals["yes"] is w and w.buy == 20
    assert t.s.withdrawal_values(w) == (D(30), D(10))
    assert w.qualified_key is None and not t.s.signals


def test_zero_capacity_boundary_cannot_borrow_a_future_depth_decrease():
    t = Tape(asks={D(".42"): D(40), D(".43"): D(30), D(".44"): D(30)})
    t.support()
    t.book(1.6, {D(".42"): D(30), D(".43"): D(15), D(".44"): D(15)})
    t.close(1.701)
    w = t.s.withdrawals["yes"]
    t.hit(1.72, "30", side="BUY", price=".43")
    assert w.buy == 30 and t.s.withdrawal_values(w) == (D(40), D(10))
    t.hit(1.73, ".5", side="BUY", price=".42", q0="30")
    assert not t.s.withdrawals
    t.depth(1.74, "29.5", side="ASK", price=".42")
    assert not t.s.signals


def test_all_fixed_set_buy_consumption_survives_rolling_history_eviction():
    t = Tape()
    t.support()
    t.hit(1.51, "50", side="BUY", price=".43")
    t.book(1.6, {D(".42"): D(100), D(".43"): D(20), D(".44"): D(20)})
    t.close(1.701)
    w = t.s.withdrawals["yes"]
    t.retest()
    t.at(4.5)
    assert len(t.s.signals) == 1 and not t.s.buy_history["yes"] and w.buy == 60
    t.hit(4.51, "80", side="BUY", price=".43")
    assert not t.s.signals and not t.s.withdrawals


def test_qualification_available_at_native_deadline_is_too_late():
    t = Tape()
    t.seed()
    t.hit(2.4, ".1", side="BUY", price=".42")
    t.depth(2.43, "90", side="ASK", price=".42")
    t.hit(2.451, "1", side="BUY", price=".42")
    assert not t.s.withdrawals and not t.s.signals
    assert "RETEST_NOT_QUALIFIED_BEFORE_ORIGINAL_DEADLINE" in t.reasons()


def test_post_window_ask_breach_cannot_be_hidden_by_recovery_before_publication():
    t = Tape()
    t.seed()
    t.hit(1.72, "10", side="BUY", price=".42")
    t.depth(1.73, "90", side="ASK", price=".42")
    t.depth(1.92, "92", side="ASK", price=".42")
    t.depth(2.225, "96", side="ASK", price=".42")
    t.depth(2.23, "92", side="ASK", price=".42")
    t.close(2.24)
    assert "POST_WINDOW_GUARD" in t.reasons() and not t.s.signals


def test_parent_batch_failure_before_publication_cannot_issue_transient_signal():
    t = Tape()
    t.seed()
    t.retest(publish=False)
    assert not t.s.signals
    t.depth(2.221, "96", side="ASK", price=".42")
    t.s.publish()
    assert t.s.counts["SIGNAL_CONFIRMED"] == 0 and not t.s.withdrawals


@pytest.mark.parametrize("failure", ["TICK_CHANGED", "CONNECTION_GAP", "BOOK_RECONSTRUCTED"])
def test_quality_loss_ends_support_root_flow_and_already_started_buy_intervals(failure):
    t = Tape()
    t.seed()
    t.retest()
    assert t.s.signals
    t.s.invalidate("yes", failure)
    assert not any((t.s.signals, t.s.components, t.s.rounds, t.s.withdrawals,
                    t.s.buy_history, t.s.ask_books, t.s.buy_intervals, t.s.closed_buy_intervals))


def test_late_buy_into_closed_r_marks_unknown_and_preserves_past_signal_fact():
    t = Tape()
    t.seed()
    t.retest()
    t.hit(2.3, "1", side="BUY", price=".42", received=2.0)
    assert not t.s.signals and not t.s.withdrawals
    assert t.s.counts["SIGNAL_CONFIRMED"] == 1
    assert "LATE_BUY_TO_CLOSED_ASK_WINDOW" in t.reasons()


def test_front_ask_loss_and_new_best_do_not_move_frozen_anchor_or_lw_set():
    t = Tape()
    w = t.seed()
    t.book(1.8, {D(".41"): D(10000), **t.asks})
    assert w.levels == (D(".42"), D(".43"), D(".44"))
    t.book(1.9, {D(".41"): D(10000), D(".43"): D(40), D(".44"): D(40)})
    assert not t.s.withdrawals and "LW_ORIGINAL_FRONT_ASK_LOST" in t.reasons()


def test_uncovered_lw_prehistory_is_unknown_not_zero_buy():
    t = Tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)}, covered=False)
    assert not t.s.withdrawals and t.s.counts["LW_UNKNOWN"] == 1


def test_core_release_clears_historical_and_physical_interval_state():
    t = Tape()
    t.seed()
    t.retest()
    t.s.release()
    assert not any((t.s.signals, t.s.components, t.s.rounds, t.s.withdrawals,
                    t.s.ask_books, t.s.buy_history, t.s.lw_last_end, t.s.ready_retests,
                    t.s.buy_intervals, t.s.closed_buy_intervals))
