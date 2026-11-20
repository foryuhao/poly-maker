"""Hand-calculated M18 pair/lifecycle cases, without a feed or broker."""
from decimal import Decimal as D

import pytest

from sop_signals.m18 import M18


class Tape:
    def __init__(self, parameters=None):
        self.events, self.seq = [], 0
        self.core = M18(lambda kind, payload: self.events.append((kind, payload)), parameters)

    def at(self, available):
        self.seq += 1
        self.core.clock(available, self.seq, 0)

    def hit(self, t, size="10", q0="100", *, received=None, token="UP", price=".40", bid=".40"):
        self.at(t)
        self.core.trade(token=token, side="SELL", price=D(price), quantity=D(size), q0=D(q0),
                        received=t if received is None else received, generation=1,
                        input_ref=(self.seq, 0, 0), source=1000+t, pre_event_best_bid=D(bid))

    def depth(self, t, q, *, received=None, token="UP"):
        self.at(t)
        self.core.depth(token, "BID", D(".40"), D(q), t if received is None else received,
                        generation=1, input_ref=(self.seq, 0, 0), source=1000+t,
                        kind="BID_PRICE_CHANGE")

    def close(self, t, *, publish=True, covered=True):
        self.at(t)
        self.core.close(t, lambda *_: covered)
        if publish:
            self.core.publish()

    def reference(self):
        self.hit(1)
        self.depth(1.02, "80")
        self.depth(1.30, "95")
        self.close(1.501)
        return self.core.campaigns.get("UP")

    def second(self, *, start=1.6, hits="25", q0="95", low="77", final="95", close=None, publish=True):
        self.hit(start, hits, q0)
        self.depth(round(start+.02, 6), low)
        self.depth(round(start+.30, 6), final)
        self.close(round(start+.501, 6) if close is None else close, publish=publish)


def test_original_pair_has_more_hit_without_larger_loss_and_keeps_old_floor_historical():
    t = Tape()
    c = t.reference()
    assert c.reference["guard"] == 88 and not t.core.signals
    t.second()
    s = next(iter(t.core.signals.values()))
    assert s["root_id"] == c.id and s["anchor_price"] == D(".40")
    assert s["confirmed"] == 2.101 and s["deadline"] == 5.101
    assert s["bid"]["qmin"] == 77 < s["prior_reference"]["guard"]
    assert s["bid"]["guard"] == D("84.20")
    assert s["strength_ratios"]["hit"] == D("2.5")
    assert "tau_low" not in s["bid"] and "t_plus" not in s["bid"]
    assert all(p["strategy"] == "M18" for _, p in t.events)


def material_reference_candidate(*, revision_round=1, **overrides):
    from sop_signals.parameters import load_revision
    values = {"reference_hit_shares_min":"2.5", "reference_hit_fraction_min":".05",
              "reference_loss_fraction_min":".025"}
    values.update(overrides)
    parameters, _ = load_revision({
        "strategy":"M18", "revision_round":revision_round, "parameters":values,
        "pnl_used_for_selection":False, "hypothesis":"Weaker material reference, same two-round test",
        "numeric_candidate_review":{
            "profile":"M18_MATERIAL_REFERENCE_AND_ESCALATION_NUMERICAL_V1",
            "parameter_change_notes":{
                name:("Predeclared c1-based finite gap, not a renewed live deadline" if name == "pair_gap"
                      else "Explicit materiality floor; stability and proof unchanged") for name in values}}})
    return parameters


def smaller_reference(tape, covered=True):
    tape.hit(1, "6")
    tape.depth(1.02, "97")
    tape.depth(1.30, "100")
    tape.close(1.501, covered=covered)


def test_declared_materiality_candidate_changes_eligibility_without_removing_the_second_round():
    original = Tape()
    smaller_reference(original)
    assert not original.core.campaigns and not original.core.signals
    candidate = Tape(material_reference_candidate())
    smaller_reference(candidate)
    assert candidate.core.campaigns and not candidate.core.signals
    candidate.second(hits="15", q0="100", low="97.5", final="100")
    signal = next(iter(candidate.core.signals.values()))
    assert signal["strength_ratios"]["hit"] == D("2.5")
    assert signal["bid"]["loss"] <= signal["prior_reference"]["loss"]
    assert signal["deadline"] == 5.101


def test_declared_candidate_does_not_make_missing_coverage_a_reference():
    candidate = Tape(material_reference_candidate())
    smaller_reference(candidate, covered=False)
    assert not candidate.core.campaigns and not candidate.core.signals
    assert any(kind == "ROUND_UNKNOWN" for kind, _ in candidate.events)


def test_declared_candidate_still_consumes_the_first_failed_second_round():
    candidate = Tape(material_reference_candidate())
    smaller_reference(candidate)
    root = candidate.core.campaigns["UP"].id
    candidate.second(hits="7", q0="100", low="97.5", final="100")
    candidate.second(start=2.3, hits="20", q0="100", low="97.5", final="100")
    assert all(payload.get("root_id") != root for kind, payload in candidate.events
               if kind == "SIGNAL_CONFIRMED")


def extended_reference_candidate():
    return material_reference_candidate(
        revision_round=2, reference_hit_shares_min="1", reference_hit_fraction_min=".01",
        reference_loss_fraction_min=".005", pair_gap=4.)


def test_predeclared_extended_pair_gap_keeps_the_original_reference_and_signal_clocks():
    candidate = Tape(extended_reference_candidate())
    smaller_reference(candidate)
    root = candidate.core.campaigns["UP"].id
    assert candidate.core.campaigns["UP"].pair_deadline == 5.501
    candidate.second(start=3.6, hits="15", q0="100", low="97.5", final="100")
    signal = next(iter(candidate.core.signals.values()))
    assert signal["root_id"] == root and signal["confirmed"] == 4.101
    assert signal["deadline"] == 7.101
    candidate.close(5.5)
    assert candidate.core.signals[signal["id"]]["deadline"] == 7.101


def test_extended_candidate_still_expires_at_the_exact_original_c1_deadline():
    candidate = Tape(extended_reference_candidate())
    smaller_reference(candidate)
    root = candidate.core.campaigns["UP"].id
    candidate.second(start=5., hits="15", q0="100", low="97.5", final="100", close=5.501)
    assert not candidate.core.signals
    assert any(kind == "AMPLITUDE_CAMPAIGN_ENDED" and payload["id"] == root
               for kind, payload in candidate.events)


def quarter_reference(tape):
    tape.hit(1)
    tape.depth(1.02, "80")
    tape.depth(1.14, "95")
    tape.close(1.251)


def test_explicit_quarter_pair_keeps_two_proven_rounds_and_original_c2_expiry():
    from test_sop_m18_parameters import short_document
    from sop_signals.parameters import load_revision
    p, _ = load_revision(short_document())
    tape = Tape(p)
    quarter_reference(tape)
    assert tape.core.campaigns and not tape.core.signals
    tape.hit(1.30, "25", "95")
    tape.depth(1.32, "77")
    tape.depth(1.44, "95")
    tape.close(1.551)
    signal = next(iter(tape.core.signals.values()))
    assert signal["confirmed"] == 1.551 and signal["deadline"] == 4.551
    assert signal["strength_ratios"]["hit"] == D("2.5")
    assert D(signal["bid"]["end"]) - D(signal["bid"]["start"]) == D(".25")
    assert signal["bid"]["qmin"] < signal["prior_reference"]["guard"]
    assert "explicit-bid250-stable100" in signal["version"]


def test_quarter_first_second_still_requires_received_after_reference_available():
    from test_sop_m18_parameters import short_document
    from sop_signals.parameters import load_revision
    tape = Tape(load_revision(short_document())[0])
    quarter_reference(tape)
    campaign = tape.core.campaigns["UP"]
    tape.hit(1.26, "25", "95", received=1.250)
    assert campaign.second_id is None
    assert next(iter(tape.core.rounds.values())).role == "INELIGIBLE"


@pytest.mark.parametrize("overrides,failed", [
    ({"q0": "50", "low": "40", "final": "50"}, "initial_depth"),
    ({"q0": "140", "low": "120", "final": "140"}, "initial_depth"),
    ({"hits": "19.999"}, "hit"), ({"hits": "40.001"}, "hit"),
    ({"q0": "120", "low": "98", "final": "120"}, "absolute_loss"),
    ({"q0": "80", "low": "62", "final": "80"}, "normalized_loss"),
    ({"final": "90"}, "recovery_not_worse"),
])
def test_each_independent_amplitude_comparison_remains_required(overrides, failed):
    t = Tape()
    t.reference()
    t.second(**overrides)
    reviews = [p for k, p in t.events if k == "AMPLITUDE_PAIR_REVIEW"]
    assert reviews and reviews[-1]["checks"][failed] is False
    assert not t.core.signals and not t.core.campaigns


@pytest.mark.parametrize("hits", ["2", "9.999"])
def test_reference_requires_material_first_hit(hits):
    t = Tape()
    t.hit(1, hits)
    t.depth(1.02, "80")
    t.depth(1.30, "95")
    t.close(1.501)
    assert not t.core.campaigns


def test_only_pre_event_best_bid_starts_a_reference_and_one_group_per_token():
    t = Tape()
    t.hit(1, price=".39")
    assert not t.core.rounds
    t.hit(1.01)
    t.hit(1.02, price=".39")
    assert len(t.core.rounds) == 1


def test_a_first_failed_second_cannot_be_replaced_by_a_better_third():
    t = Tape()
    root = t.reference().id
    t.second(hits="15")
    t.second(start=2.3)
    assert not t.core.signals
    assert all(p.get("root_id") != root for k, p in t.events if k == "SIGNAL_CONFIRMED")


@pytest.mark.parametrize("receipt", [1.5, 1.501])
def test_received_before_or_equal_to_reference_is_not_a_new_second(receipt):
    t = Tape()
    c = t.reference()
    t.hit(1.51, "25", "95", received=receipt)
    assert c.second_id is None
    t.hit(1.55, "1", "95")
    assert c.second_id is None
    assert next(iter(t.core.rounds.values())).role == "INELIGIBLE"


@pytest.mark.parametrize("start,close,passed", [(2.999, 3.5, True), (3., 3.501, False)])
def test_second_confirmation_deadline_is_exclusive(start, close, passed):
    t = Tape()
    t.reference()
    t.second(start=start, close=close)
    assert bool(t.core.signals) is passed


def test_closed_window_to_publication_floor_breach_is_not_hidden_by_recovery():
    t = Tape()
    t.reference()
    t.hit(1.6, "25", "95")
    t.depth(1.62, "77")
    t.depth(1.90, "95")
    t.depth(2.105, "83")
    t.depth(2.11, "95")
    t.close(2.12)
    assert not t.core.signals
    assert any(p.get("reason") == "POST_WINDOW_GUARD" for _, p in t.events)


def test_post_close_same_parent_breach_invalidates_before_publication():
    t = Tape()
    t.reference()
    t.second(publish=False)
    t.depth(2.101, "83")
    t.depth(2.101, "95")
    t.core.publish()
    assert not t.core.signals and not t.core.campaigns


def test_later_stable_round_needs_no_more_hit_and_never_renews_original_deadline():
    t = Tape()
    t.reference()
    t.second()
    t.hit(2.2, "1", "95")
    t.depth(2.22, "89")
    t.depth(2.40, "95")
    t.close(2.701)
    assert next(iter(t.core.signals.values()))["deadline"] == 5.101
    t.at(5.101)
    assert not t.core.signals


def test_followup_without_terminal_quantity_is_unknown_and_ends_support():
    t = Tape()
    t.reference()
    t.second()
    t.hit(2.2, "1", "95")
    t.depth(2.22, "89")
    t.depth(2.40, "95")
    t.hit(2.69, "1", "95")
    t.close(2.701)
    assert not t.core.signals
    assert any("SUBSEQUENT_ROUND_UNKNOWN" in str(p.get("reason")) for _, p in t.events)


def test_release_removes_all_campaign_and_raw_event_references():
    t = Tape()
    t.reference()
    t.second()
    t.hit(2.2, "1", "95")
    p = next(iter(t.core.rounds.values())).path
    t.core.release()
    assert not t.core.rounds and not t.core.campaigns and not t.core.signals
    assert p.last is p.last_trade is p.last_depth is None
