"""The explicit250ms candidate keeps strict100ms stability, causal handoff and expiry."""

from decimal import Decimal as D

import pytest
from sop_signals.parameters import load_revision

from test_sop_m13 import Tape


def selected():
    return load_revision({"strategy": "M13", "revision_round": 3,
                          "hypothesis": "DIRECTED_SELECTED_WINDOW_CHECK_NOT_AN_ADDITIONAL_SEARCH_ROUND",
                          "pnl_used_for_selection": False,
                          "explicit_short_round_candidate": "M13_250MS_KEEP_100MS_HALF_RECOVERY_R3",
                          "parameters": {"round_seconds": .25, "lw_loss_min": ".10",
                                         "lw_residual_min": ".10", "lw_residual_loss_min": ".25"}})[0]


def test_selected_quarter_window_forms_but_keeps_original_history_and_root_deadline():
    tape = Tape(selected())
    root = tape.seed()
    tape.retest(start=2.3)
    assert root.native_ended and len(tape.s.signals) == 1
    signal = next(iter(tape.s.signals.values()))
    assert signal["deadline"] == pytest.approx(4.701)
    assert signal["bid"]["window_end"] == pytest.approx(2.55)
    assert signal["bid"]["guard"] >= signal["prior_support"]["guard"]
    tape.at(signal["deadline"])
    assert not tape.s.signals and not tape.s.withdrawals


def test_quarter_window_late_refill_does_not_shorten_required100ms_stability():
    tape = Tape(selected())
    tape.seed()
    tape.hit(1.72, "5", q0="94")
    tape.depth(1.73, "89")
    tape.depth(1.95, "93")
    tape.close(1.971)
    closed = [fact for kind, fact in tape.events if kind == "ROUND_CLOSED"][-1]
    assert closed["qualified"] and not closed["stable"] and closed["recovery"] >= D(".50")
    assert closed["end"] - closed["stable_since"] < .1
    assert not tape.s.signals and "SUBSEQUENT_ROUND_COUNTEREVIDENCE" in tape.reasons()


def test_quarter_window_first_failed_retest_cannot_be_replaced():
    tape = Tape(selected())
    tape.seed()
    tape.hit(1.72, ".1", q0="94")
    tape.depth(1.73, "94")
    tape.close(1.971)
    tape.retest(start=2.0)
    assert not tape.s.signals and not tape.s.withdrawals


def test_quarter_window_other_token_cannot_complete_seeded_retest():
    tape = Tape(selected())
    root = tape.seed()
    tape.hit(1.72, "5", q0="94", token="other")
    tape.depth(1.73, "89", token="other")
    tape.depth(1.84, "93", token="other")
    tape.close(1.971)
    assert root.retest_id is None and not tape.s.signals


def test_quarter_window_received_at_seed_is_not_a_new_retest():
    tape = Tape(selected())
    root = tape.seed()
    tape.hit(1.701, "5", q0="94")
    assert root.retest_id is None and not tape.s.signals


def test_quarter_window_original_lw_qualification_deadline_remains_strict():
    tape = Tape(selected())
    tape.seed()
    tape.hit(2.4, ".1", q0="94")
    tape.depth(2.43, "89")
    tape.hit(2.451, "5", q0="94")
    assert "RETEST_NOT_QUALIFIED_BEFORE_ORIGINAL_DEADLINE" in tape.reasons()
    assert not tape.s.signals and not tape.s.withdrawals


def test_quarter_window_deferred_publication_cannot_ignore_new_floor_break():
    tape = Tape(selected())
    tape.seed()
    tape.retest(publish=False)
    assert not tape.s.signals
    tape.depth(1.971, "90.5")
    tape.s.publish()
    assert not tape.s.signals
    assert D("90.5") < D("91")
