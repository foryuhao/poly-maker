"""The new numerical thresholds must preserve the original evidence sequence."""

from decimal import Decimal as D

import pytest
from maker_signals.strategies import M5_M10_NEXT, Strategies

from test_m511_feed import Tape, play_m10_book_path
from test_m511_strategies import H, R


def test_next_raw_tape_forms_m10_from_two_actual_returns():
    t = Tape()
    Strategies(t.feed, signal_variant=M5_M10_NEXT, parent_filters=False)
    play_m10_book_path(t)
    signals = [p["payload"] for k, p in t.rows if k == "SIGNAL" and p["strategy"] == "M10"]
    assert len(signals) == 1
    assert len(signals[0]["round_ids"]) == 2
    assert signals[0]["confirmed"] == pytest.approx(1.81)
    assert signals[0]["strategy_version"] == "m10-two-return-decay-v4"


@pytest.mark.parametrize("hit,depth,expected", [(9.9, 100, True), (9.95, 100, False), (9.9, 90, False)])
def test_next_m10_requires_both_absolute_and_normalized_decay(hit, depth, expected):
    h = H(M5_M10_NEXT)
    h.ap()
    p = h.s.active("M10", "up")
    h.s.decay_round(p, h.rnd(.25, hit=10))
    assert not h.signals("M10")
    h.s.decay_round(p, h.rnd(.5, hit=hit, q0=depth))
    assert bool(h.signals("M10")) is expected
    if not expected:
        h.s.decay_round(p, h.rnd(.75, hit=5))
        assert not h.signals("M10")


def test_next_m7_cannot_relabel_early_attack_as_new_confirmation():
    h = H(M5_M10_NEXT)
    first = h.ap()
    p = h.s.active("M7", "up")
    h.at(.1)
    second = h.ap()
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (.01, 1, 0)}, second)
    assert p.closed and p.root == first.id and not h.signals("M7")


def test_next_m9_expired_seed_cannot_be_extended_by_small_first_hit():
    h = H(M5_M10_NEXT)
    h.s.on_round(h.rnd(1))
    h.at(1.1)
    ap = h.ap("SELL", volume=10)
    h.at(1.85)
    h.fact("EX_SELL", ttl=.75, refs=[ap.id], dispatch=True)
    p = h.s.active("M9", "up")
    h.at(p.data["seed_deadline"])
    h.s.retest_qualified(p, R())
    assert p.closed and not h.signals("M9")


def m9_seed(ttl=1.5):
    h = H(M5_M10_NEXT)
    h.s.frequency_parameters["M9"]["seed_ex_ttl_seconds"] = ttl
    h.s.on_round(h.rnd(1))
    h.at(1.1)
    ap = h.ap("SELL", volume=10)
    h.at(1.85)
    ex = h.fact("EX_SELL", ttl=.75, refs=[ap.id], dispatch=True)
    return h, ex, h.s.active("M9", "up")


def test_m9_local_seed_budget_is_frozen_and_does_not_extend_shared_ex():
    h, ex, p = m9_seed()
    assert ex.deadline == pytest.approx(2.60)
    assert p.data["seed_deadline"] == pytest.approx(3.35)
    h.at(2.70)
    assert not h.c.registry.valid(ex, h.c.now)
    h.s.retest_qualified(p, R())
    assert p.data["qualified"] and not p.closed
    h.s.retest_round(p, h.rnd(3.1))
    assert len(h.signals("M9")) == 1


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_m9_new_attack_cannot_revive_a_soft_expired_source_seed(side):
    h, _, p = m9_seed()
    h.at(2.70)
    h.ap(side)
    assert p.closed and not h.signals("M9")


def test_m9_local_budget_expiry_and_hard_source_end_still_win():
    h, ex, p = m9_seed(3)
    h.at(2)
    h.c.registry.end(ex, h.c.now, "PRESSURE_RESUMED")
    h.s.retest_guard(p)
    assert p.closed
    h, _, p = m9_seed(3)
    h.at(p.data["seed_deadline"])
    h.s.retest_qualified(p, R())
    assert p.closed and not h.signals("M9")


def test_m9_extended_wait_preserves_original_half_volume_pressure_guard():
    h, _, p = m9_seed(3)
    h.at(2.7)
    h.c.flows["up"].volume = lambda *args, **kwargs: D(5)
    h.s.retest_guard(p)
    assert p.closed and not h.signals("M9")


def test_m9_local_ex_pressure_guard_lasts_until_its_declared_expiry():
    h, _, p = m9_seed()
    h.at(2.7)
    h.s.retest_qualified(p, R())
    h.c.flows["up"].volume = lambda *args, **kwargs: D(5)
    h.s.retest_guard(p)
    assert p.closed
    h, _, p = m9_seed()
    h.at(2.7)
    h.s.retest_qualified(p, R())
    h.at(3.4)
    h.c.flows["up"].volume = lambda *args, **kwargs: D(5)
    h.s.retest_guard(p)
    assert not p.closed  # After qualification and TTL, the seed is historical.
