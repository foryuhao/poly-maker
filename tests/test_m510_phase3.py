"""Private numeric calibration preserves public evidence and causal stages."""

import gc
import weakref

import pytest
from maker_signals.strategies import M5_M10_CALIBRATION, M5_M10_NEXT, Strategies

from test_m511_feed import Tape
from test_m511_strategies import H

PRIVATE = {"M9": {"private_seed": True, "seed_round_seconds": .25,
                   "seed_hold_seconds": .025, "seed_stable_recovery": ".40"}}


def play_private_m9(tape):
    books = {round(i*.05, 3) for i in range(37)} | {
        .02, .28, .31, .58, .61, 1.53}
    trades, ticks = {.01, .30, .60, 1.52}, {.26, .51, .55, .76, .85, 1.1, 1.51, 1.77}

    def quantity(at):
        for end, value in ((.02, 100), (.10, 90), (.28, 94), (.31, 100),
                           (.40, 90), (.58, 94), (.61, 100), (.70, 90),
                           (1.50, 94), (1.53, 100), (1.60, 90)):
            if at < end:
                return value
        return 94

    for at, kind in sorted([(t, 0) for t in books]+[(t, 1) for t in trades]+[(t, 2) for t in ticks]):
        if kind == 0:
            tape.books(at, bid=quantity(at))
        elif kind == 1:
            tape.trade(at, "SELL", 10)
        else:
            tape.tick(at)


def private_tape():
    tape = Tape()
    manager = Strategies(tape.feed, signal_variant=M5_M10_CALIBRATION,
                         parent_filters=False, frequency_parameters=PRIVATE)
    return tape, manager


def test_private_support_and_real_post_seed_retest_form_causal_m9():
    tape, manager = private_tape()
    play_private_m9(tape)
    candidate, = [p["payload"] for k, p in tape.rows
                   if k == "SIGNAL" and p["strategy"] == "M9"]
    assert candidate["confirmed"] == pytest.approx(1.77)
    p = manager.active("M9", "up")
    assert p.data["seed_rf"].confirmed == pytest.approx(.26)
    assert p.data["seed_ex"].confirmed == pytest.approx(1.51)
    assert p.rounds[0].start == pytest.approx(1.52)
    assert p.data["first_qualified"] == pytest.approx(1.53)
    assert p.data["first_qualified"] < p.data["seed_deadline"]
    assert not any(f.values["stable"] for f in tape.facts("ROUND")
                   if f.values["scope"] == "shared-rf")
    assert tape.feed.good == {"up": True, "down": True}


def test_private_seed_does_not_change_shared_facts_other_strategies_or_control():
    private, _ = private_tape()
    control = Tape()
    Strategies(control.feed, signal_variant=M5_M10_NEXT, parent_filters=False)
    for tape in (private, control):
        play_private_m9(tape)
    def public_facts(tape):
        return [(f.kind, f.token, f.confirmed, f.start, f.end, f.anchor, f.values)
                for f in tape.feed.registry.facts.values()
                if f.kind in {"AP_BUY", "AP_SELL", "EX_BUY", "EX_SELL"} or f.kind == "ROUND"
                and f.values["scope"] == "shared-rf"]
    assert public_facts(private) == public_facts(control)
    for strategy in ("M5", "M6", "M8"):
        assert [p for k, p in private.rows if k == "SIGNAL" and p["strategy"] == strategy] == [
            p for k, p in control.rows if k == "SIGNAL" and p["strategy"] == strategy]
    assert not any(k == "SIGNAL" and p["strategy"] == "M9" for k, p in control.rows)
    assert private.feed.reasons == control.feed.reasons


def test_private_grid_observers_do_not_change_primary_candidates_or_quality():
    from m510_frequency_grid import attach_profiles

    standalone, shared = private_tape()[0], Tape()
    primary = Strategies(shared.feed, signal_variant=M5_M10_CALIBRATION,
                         parent_filters=False)
    attach_profiles(shared.feed, primary,
                    {"control_v4": {}, "private_seed": PRIVATE}, "control_v4")
    control = Tape()
    Strategies(control.feed, signal_variant=M5_M10_CALIBRATION, parent_filters=False)
    for tape in (standalone, shared, control):
        play_private_m9(tape)
    def candidates(tape, manager):
        return sorted((f.values["strategy"], f.token, f.confirmed, f.deadline, f.anchor)
                      for i in manager.published if (f := tape.feed.registry.facts[i]))
    assert candidates(shared, primary) == candidates(control, control.feed.strategies)
    assert shared.feed.reasons == control.feed.reasons
    assert shared.feed.good == control.feed.good
    assert any(f.values["strategy"] == "M9" for i in
               shared.feed.strategies.managers["private_seed"].published
               if (f := shared.feed.registry.facts[i]))


@pytest.mark.parametrize("case", ["no_sell", "no_loss", "no_hold", "gap"])
def test_private_seed_requires_actual_hit_loss_hold_and_coverage(case):
    tape, manager = private_tape()
    tape.books(0)
    if case != "no_sell":
        tape.trade(.01, "SELL", 10)
    tape.books(.02, bid=100 if case == "no_loss" else 90)
    for at in (.10, .20):
        tape.books(at, bid=90 if case == "no_hold" else 100 if case == "no_loss" else 94)
    if case == "gap":
        tape.feed.gap("TEST_PUBLIC_GAP")
    tape.books(.255, bid=100 if case == "no_loss" else 94)
    tape.tick(.26)
    assert manager.m9_support.get("up") is None


def test_private_window_failure_and_anchor_loss_do_not_break_other_public_evidence():
    tape, manager = private_tape()
    play_private_m9(tape)
    support = manager.m9_support["up"]
    tape.books(1.81, bid=0)
    assert not tape.feed.registry.valid(support, tape.feed.now)
    assert tape.feed.registry.endings[support.id]["reason"] == "ANCHOR_LOST"
    assert tape.feed.good == {"up": True, "down": True}


def test_private_observer_graph_releases_without_global_gc():
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        tape, manager = private_tape()
        play_private_m9(tape)
        ref = weakref.ref(tape.feed)
        tape.feed.release_runtime()
        assert manager.m9_seed_observers == {} and manager.c is None
        del tape
        assert ref() is None
    finally:
        if was_enabled:
            gc.enable()


def test_short_pause_still_needs_complete_window_and_wholly_new_attack():
    h = H(M5_M10_CALIBRATION)
    h.s.frequency_parameters["M7"]["pause_seconds"] = .25
    first = h.ap()
    p = h.s.active("M7", "up")
    h.at(.249, tick=True)
    assert "ex" not in p.data
    h.at(.25, tick=True)
    assert p.data["ex"].end == pytest.approx(first.confirmed+.25)
    h.s.reattack_round(p, h.rnd(.3))
    h.at(.4)
    early = h.fact("AP_BUY", ttl=.75)
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (.29, 1, -1)}, early)
    assert p.closed and not h.signals("M7")


@pytest.mark.parametrize("hit,expected", [(9.99, True), (10, False), (10.1, False)])
def test_decay999_still_rejects_equal_or_increasing_return(hit, expected):
    h = H(M5_M10_CALIBRATION)
    h.s.frequency_parameters["M10"]["decay_fraction"] = ".999"
    h.ap()
    p = h.s.active("M10", "up")
    h.s.decay_round(p, h.rnd(.25, hit=10))
    h.s.decay_round(p, h.rnd(.5, hit=hit))
    assert bool(h.signals("M10")) is expected


def test_experimental_seed_knobs_cannot_silently_change_v4():
    tape = Tape()
    with pytest.raises(ValueError, match="UNKNOWN_FREQUENCY_PARAMETER"):
        Strategies(tape.feed, signal_variant=M5_M10_NEXT, frequency_parameters=PRIVATE)


@pytest.mark.parametrize("strategy", ["M7", "M10"])
def test_short_local_window_failure_cannot_invalidate_other_campaigns(strategy):
    from m510_frequency_grid import attach_profiles

    tape = Tape()
    primary = Strategies(tape.feed, signal_variant=M5_M10_CALIBRATION, parent_filters=False)
    managers = attach_profiles(tape.feed, primary,
                              {"control_v4": {}, "short": {strategy: {"round_seconds": .15}}},
                              "control_v4")
    tape.books(0)
    campaigns = []
    for manager in managers.values():
        p = manager.start(strategy, "up", "test-root", 10)
        p.observe(lambda _: None)
        campaigns.append(p)
    tape.trade(.01, "SELL", 10)
    tape.books(.02, bid=90)
    tape.books(.10, bid=94)
    # The .15s shadow round is late, while the primary .25s round and
    # source book/flow are still within their own validity windows.
    tape.books(.24, bid=94)
    assert not campaigns[0].closed
    assert campaigns[1].closed
    assert not any(k == "RESEARCH_EVIDENCE" and p["event"] == "QUALITY_UNAVAILABLE"
                   and p["payload"]["reason"].startswith("ROUND_")
                   for k, p in tape.rows)
    tape.feed.gap("TRUE_PUBLIC_GAP")
    assert campaigns[0].closed


def test_default_defense_does_not_change_untargeted_m8_source_failure_scope():
    tape = Tape()
    manager = Strategies(tape.feed, signal_variant=M5_M10_CALIBRATION, parent_filters=False)
    tape.books(0)
    target = manager.start("M7", "up", "target", 10)
    target.observe(lambda _: None)
    unchanged = manager.start("M8", "up", "unchanged", 10)
    unchanged.observe(lambda _: None)
    unchanged.observer.unavailable("ROUND_WINDOW_UNAVAILABLE")
    assert target.closed and unchanged.closed
    assert any(k == "RESEARCH_EVIDENCE" and p["event"] == "QUALITY_UNAVAILABLE"
               and p["payload"]["reason"] == "ROUND_WINDOW_UNAVAILABLE"
               for k, p in tape.rows)
