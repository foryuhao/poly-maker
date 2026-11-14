"""Each shadow evaluates only its declared strategy, with independent evidence."""

from types import SimpleNamespace

import pytest
from m510_frequency_grid import attach_profiles, target_map
from maker_signals.model import D
from maker_signals.strategies import M5_M10_FAILED_ROOT, Strategies

from test_m511_feed import Tape, play_m10_book_path
from test_m511_strategies import H


def physical(context, manager, strategy=None):
    return sorted((f.values["strategy"], f.token, f.confirmed, f.deadline, f.anchor)
                  for ident in manager.published
                  if (f := context.registry.facts[ident]) and
                  (strategy is None or f.values["strategy"] == strategy))


def matrix(h):
    primary = h.s
    managers = attach_profiles(h.c, primary,
        {"control": {}, "m7": {}, "m9": {}, "m10": {}}, "control",
        profile_targets={"m7": ["M7"], "m9": ["M9"], "m10": ["M10"]})
    h.s = h.c.strategies
    return primary, managers


@pytest.mark.parametrize("strategy", ["M7", "M9", "M10"])
def test_positive_patterns_reach_the_eligible_shadow_only(strategy):
    h = H(M5_M10_FAILED_ROOT)
    primary, managers = matrix(h)
    if strategy == "M9":
        ap = h.ap("SELL", volume=10)
        support = h.rnd(.1)
        h.c.strategies.on_round(support)
        h.fact("EX_SELL", ttl=.75, refs=[ap.id], values={"volume": D(10)}, dispatch=True)
        h.at(.15)
        for manager in (primary, managers["m9"]):
            p = manager.active("M9", "up")
            assert p is not None
            manager.retest_qualified(p, SimpleNamespace(minimum=D(".5")))
        result = h.rnd(.3, start=.15, hit=1, loss=1)
        for manager in (primary, managers["m9"]):
            manager.retest_round(manager.active("M9", "up"), result)
    else:
        h.ap()
        if strategy == "M7":
            h.at(.5, tick=True)
            result = h.rnd(.6, start=.05)
            for manager in (primary, managers["m7"]):
                manager.reattack_round(manager.active("M7", "up"), result)
            h.at(1.5)
            ap2 = h.fact("AP_BUY", ttl=.75,
                         values={"volume": D(10), "start_key": (.75, h.c.seq-1, -1)})
            h.c.strategies.on_ap_verdict("up", {"side": "BUY", "start_key": (.75, h.c.seq-1, -1)}, ap2)
            h.c.strategies.on_fact(ap2)
        else:
            for at, hit in ((.5, 10), (1., 5)):
                result = h.rnd(at, hit=hit, loss=hit)
                for manager in (primary, managers["m10"]):
                    manager.decay_round(manager.active("M10", "up"), result)
    h.c.strategies.publish()
    expected = physical(h.c, primary, strategy)
    assert expected and physical(h.c, managers[strategy.lower()]) == expected
    assert all(f.values["strategy"] in manager.targeted_strategies
               for name, manager in managers.items() if name != "control"
               for ident in manager.published if (f := h.c.registry.facts[ident]))
    assert all(set(manager.facts["up"]) <= {"AP_BUY", "AP_SELL", "EX_SELL"}
               for name, manager in managers.items() if name != "control")


def test_mixed_shadows_preserve_raw_primary_candidates_and_quality():
    standalone, shared = Tape(), Tape()
    one = Strategies(standalone.feed, signal_variant=M5_M10_FAILED_ROOT, parent_filters=False)
    primary = Strategies(shared.feed, signal_variant=M5_M10_FAILED_ROOT, parent_filters=False)
    managers = attach_profiles(shared.feed, primary,
        {"control": {}, "m7": {}, "m9": {}, "m10": {}}, "control",
        profile_targets={"m7": ["M7"], "m9": ["M9"], "m10": ["M10"]})
    for tape in (standalone, shared):
        play_m10_book_path(tape)
    assert physical(standalone.feed, one) == physical(shared.feed, primary)
    assert physical(shared.feed, managers["m10"]) == physical(shared.feed, primary, "M10")
    assert physical(shared.feed, managers["m10"])
    assert standalone.feed.good == shared.feed.good
    assert standalone.feed.reasons == shared.feed.reasons


@pytest.mark.parametrize("targets", [{"a": []}, {"a": ["M8"]}, {"b": ["M7"]},
                                    {"a": ["M7", "M7"]}])
def test_profile_eligibility_rejects_invalid_or_missing_scope(targets):
    with pytest.raises(ValueError):
        target_map({"control": {}, "a": {}}, "control", profile_targets=targets)


def test_profile_cannot_change_an_unevaluated_strategy_or_set_two_target_sources():
    with pytest.raises(ValueError, match="BELONG"):
        target_map({"control": {}, "a": {"M10": {"hold_seconds": .01}}}, "control",
                   profile_targets={"a": ["M7"]})
    with pytest.raises(ValueError, match="EXACT"):
        target_map({"control": {}, "a": {}}, "control", ["M7"], {"a": ["M7"]})
