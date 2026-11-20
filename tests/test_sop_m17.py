"""Hand-calculated M17 formation/recheck/parent-publication boundaries."""

from decimal import Decimal as D

import pytest

from sop_signals.m17 import M17, Parameters


class Driver:
    def __init__(self, *, covered=True):
        self.events, self.seq = [], 1
        self.core = M17(lambda k, p: self.events.append((k, p)))
        self.covered = lambda *_: covered
        self.quantities = {t: D(100) for t in ("yes", "no")}
        self.provenance = {t: (.9, .9, (1, 0, 0)) for t in self.quantities}

    def begin(self, received, *, available=None):
        self.seq += 1
        self.received, self.available = received, received if available is None else available
        self.change = 0
        self.core.clock(self.available, self.seq, 0)
        self.core.close(received, self.covered)

    def finish(self):
        self.core.close(self.available, self.covered)
        self.core.publish()

    def sell(self, t, quantity=20, *, identity=None, token="yes", price=".40", best_bid=".40",
             available=None, source=None):
        self.begin(t, available=available)
        self.core.trade(token=token, side="SELL", price=D(price), quantity=D(quantity),
                        received=t, q0=self.quantities[token], generation=1,
                        input_ref=(self.seq, 0, 0), source=t if source is None else source,
                        economic_identity=(identity or str(self.seq),),
                        initial_quantity_ref=self.provenance[token],
                        pre_event_best_bid=D(best_bid))
        self.finish()

    def raw_depth(self, quantity, *, token="yes", source=None, kind="BID_PRICE_CHANGE", generation=1):
        ref = (self.seq, 0, self.change)
        self.change += 1
        source = self.received if source is None else source
        self.core.depth(token, "BID", D(".40"), D(quantity), self.received,
                        generation=generation, input_ref=ref, source=source, kind=kind)
        self.quantities[token] = D(quantity)
        self.provenance[token] = self.received, source, ref

    def depth(self, t, quantity=95, *, token="yes", source=None, available=None, kind="BID_PRICE_CHANGE"):
        self.begin(t, available=available)
        self.raw_depth(quantity, token=token, source=source, kind=kind)
        self.finish()

    def timer(self, t):
        self.begin(t)
        self.finish()

    def formed_path(self, sizes=(20, 20, 20)):
        for t, qty in zip((1., 1.26, 1.51), sizes):
            self.sell(t, qty)
            self.depth(round(t + .01, 3))
        return self.core.roots.get("yes")

    def signal(self):
        return next(iter(self.core.signals.values()))

    def events_of(self, kind):
        return [p for k, p in self.events if k == kind]


def test_three_real_buckets_can_confirm_without_rf_loss_and_residual_is_not_trigger():
    d = Driver()
    r = d.formed_path()
    assert r.hits == 60 and r.minimum_quantity == 95 and r.rechecked_records == 3
    assert not d.core.signals
    d.timer(1.75)
    s = d.signal()
    assert s["confirmed"] == 1.75 and s["deadline"] == 4.75
    assert s["formation_H"] == 60 and s["subwindow_H"] == [20, 20, 20]
    assert s["floor"] == 70 and s["can_reference_for_new_action"]
    assert s["quantity_residual_diagnostic"] == 55 and not s["residual_is_independent_evidence"]
    assert len(d.events_of("SIGNAL_CONFIRMED")) == len(d.events_of("SIGNAL_REFERENCE_READY")) == 1


def test_sell_at_window_end_is_pending_after_confirmation_and_never_added_to_formation_H():
    d = Driver()
    d.formed_path()
    d.sell(1.75, 9)
    s = d.signal()
    assert s["formation_H"] == 60 and not s["can_reference_for_new_action"]
    assert s["pending_rechecks"] == 1 and not s["ever_reference_ready"]
    d.depth(1.8, 96)
    assert s["can_reference_for_new_action"] and s["ever_reference_ready"]
    assert s["confirmed"] == 1.75 and s["deadline"] == 4.75 and s["first_reference_ready"] == 1.8
    assert s["end_quantity"] == 95 and s["quantity_residual_diagnostic"] == 55


def test_new_pending_temporarily_blocks_new_reference_but_does_not_end_existing_signal():
    d = Driver()
    d.formed_path()
    d.timer(1.75)
    s = d.signal()
    d.sell(1.8, 7)
    assert not s["can_reference_for_new_action"] and not d.events_of("SIGNAL_ENDED")
    d.depth(1.85, 90)
    assert s["can_reference_for_new_action"] and s["deadline"] == 4.75
    assert len(d.events_of("SIGNAL_REFERENCE_PENDING")) == 1
    assert s["pending_exposure_seconds"] == D(".08")  #3x.01 formation +.05 current.


@pytest.mark.parametrize("available", [1.1, 1.101])
def test_received_timely_depth_available_at_or_after_deadline_cannot_repair(available):
    d = Driver()
    d.sell(1.)
    d.depth(1.05, available=available)
    assert not d.core.roots and d.core.quality["yes"] == "TRADE_DEPTH_RECHECK_UNAVAILABLE"
    assert not d.events_of("TRADE_DEPTH_RECHECKED")


def test_recheck_received_and_available_before_deadline_is_required_no_new_budget_from_processing():
    d = Driver()
    d.sell(1., available=1.1)
    assert not d.core.roots and d.core.quality["yes"] == "TRADE_DEPTH_RECHECK_UNAVAILABLE"


def test_equal_source_depth_is_not_recheck_but_strictly_later_source_is():
    d = Driver()
    d.sell(1.)
    d.depth(1.01, source=1.)
    assert len(d.core.roots["yes"].pending) == 1
    d.depth(1.02, source=1.001)
    assert not d.core.roots["yes"].pending


def test_one_later_quantity_record_can_recheck_multiple_distinct_prints_without_extra_count():
    d = Driver()
    d.sell(1., 20)
    d.sell(1.02, 20)
    d.depth(1.03)
    r = d.core.roots["yes"]
    assert r.hits == 40 and r.forming_records == r.rechecked_records == 2 and not r.pending


def test_duplicate_identity_never_increases_H_or_distinct_print_count():
    d = Driver()
    d.sell(1., identity="same")
    d.sell(1.01, identity="same")
    r = d.core.roots["yes"]
    assert r.hits == 20 and r.forming_records == 1 and len(r.pending) == 1


def test_anchor_must_be_pre_event_best_bid_only_initially_then_stays_fixed():
    d = Driver()
    d.sell(1., price=".39")
    assert not d.core.roots
    d.sell(1.01)
    d.depth(1.02)
    d.sell(1.05, 5, best_bid=".41")
    assert d.core.roots["yes"].price == D(".40")


def test_same_source_pre_hit_quantity_is_unknown_no_better_later_initial_depth():
    d = Driver()
    d.provenance["yes"] = (.9, 1., (1, 0, 0))
    d.sell(1.)
    assert d.core.quality["yes"] == "INITIAL_DEPTH_ORDER_UNKNOWN" and not d.core.roots


@pytest.mark.parametrize("sizes", [(10, 10, 10), (50, 5, 5)])
def test_insufficient_quantity_or_dominant_single_print_does_not_form(sizes):
    d = Driver()
    d.formed_path(sizes)
    d.timer(1.75)
    assert not d.core.signals and d.events_of("QR_WINDOW_REVIEWED")


def test_three_records_in_one_subwindow_do_not_substitute_for_three_occupied_subwindows():
    d = Driver()
    for t in (1., 1.05, 1.1):
        d.sell(t)
        d.depth(round(t + .01, 3))
    d.timer(1.75)
    assert not d.core.signals


def test_any_intermediate_below_70pct_ends_root_and_same_parent_cannot_reselect():
    d = Driver()
    d.sell(1.)
    d.depth(1.01, 69)
    assert not d.core.roots
    d.sell(1.01)
    assert not d.core.roots
    assert d.events_of("QR_WINDOW_ENDED")[-1]["reason"] == "ORIGINAL_QUANTITY_FLOOR_BROKEN"


def test_final_continuous_hold_is_100ms_not_an_earlier_high_quantity_sample():
    d = Driver()
    d.formed_path()
    d.depth(1.64, 80)
    d.depth(1.651, 95)
    d.timer(1.75)
    assert not d.core.signals


def test_first_publication_current_90pct_failure_ends_root_no_later_retry():
    d = Driver()
    d.formed_path()
    d.depth(1.76, 80)
    assert not d.core.signals and not d.core.roots
    d.depth(1.77, 95)
    assert not d.core.signals


def test_handoff_only_70pct_but_publication_90pct_all_current_parent_guards_run():
    d = Driver()
    d.formed_path()
    d.begin(1.76)
    d.raw_depth(80)
    d.raw_depth(95)
    d.finish()
    assert d.signal()["confirmed"] == 1.76
    assert d.signal()["quantity_residual_diagnostic"] == 55


def test_intermediate_hard_failure_in_publication_parent_is_not_hidden_by_final_recovery():
    d = Driver()
    d.formed_path()
    d.begin(1.76)
    d.raw_depth(69)
    d.raw_depth(100)
    d.finish()
    assert not d.core.signals


def test_forming_print_recheck_cannot_be_completed_outside_the_received_window():
    d = Driver()
    d.formed_path()
    d.sell(1.72, 2)
    d.depth(1.75, 99)
    assert not d.core.signals and d.core.quality["yes"] == "TRADE_DEPTH_RECHECK_UNAVAILABLE"


def test_received_in_window_recheck_can_become_available_after_end_within_both_hard_budgets():
    d=Driver();d.formed_path();d.sell(1.72,2)
    d.depth(1.749,99,available=1.76)
    s=d.signal()
    assert s['formation_H']==62 and s['confirmed']==1.76 and s['can_reference_for_new_action']


def test_pending_budget_does_not_extend_earlier_original_signal_expiry():
    d=Driver();d.formed_path();d.timer(1.75);d.sell(4.7,1);d.timer(4.75)
    assert not d.core.signals and d.events_of('SIGNAL_ENDED')[-1]['reason']=='SIGNAL_TTL_EXPIRED'


def test_missing_coverage_and_late_publication_are_unknown_not_no_signal_success():
    d = Driver(covered=False)
    d.formed_path()
    d.timer(1.75)
    assert d.core.quality["yes"] == "WINDOW_COVERAGE_UNAVAILABLE"
    d = Driver()
    d.formed_path()
    d.timer(1.801)
    assert d.core.quality["yes"] == "PUBLICATION_LATENESS_EXCEEDED"


def test_post_window_pending_timeout_ends_signal_and_original_ttl_never_renews():
    d = Driver()
    d.formed_path()
    d.timer(1.75)
    d.sell(1.8, 5)
    d.timer(1.9)
    assert not d.core.signals and d.core.quality["yes"] == "TRADE_DEPTH_RECHECK_UNAVAILABLE"
    d = Driver()
    d.formed_path()
    d.timer(1.75)
    d.timer(4.75)
    assert not d.core.signals and d.events_of("SIGNAL_ENDED")[-1]["reason"] == "SIGNAL_TTL_EXPIRED"


def test_other_token_quantity_never_rechecks_this_token_print():
    d = Driver()
    d.sell(1.)
    d.depth(1.01, token="no")
    assert len(d.core.roots["yes"].pending) == 1


def test_reconstructed_generation_ends_previous_root_and_release_clears_all_graphs():
    d = Driver()
    d.formed_path()
    d.timer(1.75)
    d.core.new_generation("yes", 2)
    assert not d.core.roots and not d.core.signals
    d.core.release()
    assert not any((d.core.roots, d.core.signals, d.core.last_end, d.core.quality))


def test_fixed_window_and_timeout_are_not_unreviewed_frequency_knobs():
    with pytest.raises(ValueError, match="FIXED_CAUSAL"):
        M17(lambda *_: None, Parameters(recheck_seconds=.2))
