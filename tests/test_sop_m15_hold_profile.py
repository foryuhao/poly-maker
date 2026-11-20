"""Longer LW formation proof preserves first events and nonrenewable deadlines."""

from decimal import Decimal as D

import pytest
from sop_signals.m15 import Parameters
from test_sop_m15 import Tape


def tape():
    return Tape(Parameters(lw_hold_seconds=.250))


def test_first_confirmation_waits_for_continuous_250ms_not_original_100ms():
    t = tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    w = t.s.withdrawals["yes"]
    t.close(1.701)
    assert w.key is None and w.seed is None and t.s.counts["LW_CONFIRMED"] == 0
    t.close(1.849)
    assert w.key is None and w.seed is None
    t.close(1.851)
    assert w.start == 1.6 and w.key[0] == pytest.approx(1.851)
    assert w.seed and t.s.counts["LW_CONFIRMED"] == 1
    t.close(1.9)
    assert w.key[0] == pytest.approx(1.851) and t.s.counts["LW_CONFIRMED"] == 1


def test_preseed_physical_buy_stays_ineligible_under_longer_formation():
    t = tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.hit(1.75, ".1", side="BUY", price=".42")
    t.close(1.851)
    w = t.s.withdrawals["yes"]
    t.hit(1.86, ".1", side="BUY", price=".42")
    assert w.seed and w.retest_id is None and w.buy == D(".2")
    t.close(2.251)
    t.retest(start=2.3)
    assert w.retest_id and len(t.s.signals) == 1
    signal = next(iter(t.s.signals.values()))
    assert signal["deadline"] == pytest.approx(4.851)
    assert signal["prior_support"]["deadline"] == pytest.approx(9.501)


def test_interrupted_formation_does_not_reset_original_750ms_root_expiry():
    t = tape()
    t.support()
    t.book(1.6, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    w = t.s.withdrawals["yes"]
    t.book(1.75, {D(".42"): D(100), D(".43"): D(100), D(".44"): D(100)})
    t.book(2.2, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(2.351)
    assert w.start == 1.6 and t.s.counts["LW_CONFIRMED"] == 0
    assert not t.s.withdrawals and not t.s.signals


def test_longer_lw_hold_never_extends_old_strict_bid_deadline():
    t = tape()
    t.support()
    old_deadline = next(iter(t.s.components.values())).deadline
    t.book(8.5, {D(".42"): D(100), D(".43"): D(40), D(".44"): D(40)})
    t.close(8.751)
    t.retest(start=8.77)
    signal = next(iter(t.s.signals.values()))
    assert signal["deadline"] == old_deadline == pytest.approx(9.501)
    t.at(old_deadline)
    assert not t.s.signals and not t.s.withdrawals
