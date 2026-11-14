"""An unused new attack may seed a fresh pattern; prior evidence never carries."""

import pytest
from m510_frequency_grid import attach_profiles
from maker_signals.model import D, Trade
from maker_signals.strategies import (
    M5_M10_CALIBRATION,
    M5_M10_FAILED_ROOT,
    Strategies,
    key,
    resolve_frequency_parameters,
)

from test_m511_feed import Tape, play_m10_book_path
from test_m511_strategies import H


def harness(enabled):
    h = H(M5_M10_FAILED_ROOT)
    h.s.frequency_parameters["M7"]["restart_after_failed_attack"] = enabled
    return h


@pytest.mark.parametrize("stage", ["WAIT_EX", "RF"])
@pytest.mark.parametrize("enabled", [False, True])
def test_only_opt_in_restarts_a_failed_unconfirmed_pattern(stage, enabled):
    h = harness(enabled)
    first = h.ap()
    old = h.s.active("M7", "up")
    if stage == "RF":
        h.at(.5, tick=True)
    assert old.stage == stage
    h.at(1.55)
    current = h.fact("AP_BUY", ttl=.75,
                     values={"volume": D(10), "start_key": (.8, h.c.seq-1, -1)})
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (.8, h.c.seq-1, -1)}, current)
    h.s.on_fact(current)
    assert old.closed
    ended = [p["payload"] for k, p in h.events
             if k == "RESEARCH_EVIDENCE" and p["event"] == "CAMPAIGN_ENDED"
             and p["payload"]["strategy"] == "M7"]
    assert ended[-1]["reason"] == "EARLY_REATTACK"
    active = h.s.active("M7", "up")
    if not enabled:
        assert active is None
        return
    assert active.root == current.id != first.id
    assert active.data == {"ap1": current}
    assert active.observer.gate == key(current) and active.observer.round is None
    prior = Trade("old", "up", "SELL", D(".4"), D(10), 1.4, 1001.4, 1.4,
                  current.input_seq-1, -1, "up", False)
    active.observer.trade(prior)
    assert active.observer.round is None
    assert not h.signals("M7")


def play_restart_path(tape):
    books = {round(i*.05, 3) for i in range(78)} | {2.31, 2.34}
    buys = {.01, .30, .60, 1.52, 1.80, 2.11, 3.03, 3.32, 3.63}
    ticks = {.76, 1.26, 2.27, 2.56, 2.77, 3.79}
    rows = ([(t, 0) for t in books] + [(t, 1) for t in buys]
            + [(2.30, 2)] + [(t, 3) for t in ticks])
    for at, kind in sorted(rows):
        if kind == 0:
            tape.books(at, bid=90 if 2.31 <= at < 2.34 else 100)
        elif kind == 1:
            tape.trade(at, "BUY", 10)
        elif kind == 2:
            tape.trade(at, "SELL", 10)
        else:
            tape.tick(at)


def test_raw_pattern_needs_new_refill_pause_and_wholly_new_final_attack():
    tapes, managers = [], []
    for enabled in (False, True):
        tape = Tape()
        manager = Strategies(tape.feed, signal_variant=M5_M10_FAILED_ROOT,
                             parent_filters=False,
                             frequency_parameters={"M7": {"restart_after_failed_attack": enabled}})
        play_restart_path(tape)
        tapes.append(tape)
        managers.append(manager)
    assert not [p for k, p in tapes[0].rows if k == "SIGNAL" and p["strategy"] == "M7"]
    candidate, = [p["payload"] for k, p in tapes[1].rows if k == "SIGNAL" and p["strategy"] == "M7"]
    assert candidate["confirmed"] == pytest.approx(3.79)
    p = managers[1].active("M7", "up")
    assert p.data["ap1"].confirmed == pytest.approx(2.27)
    assert p.rounds[0].start == pytest.approx(2.30)
    assert p.data["ex"].end == pytest.approx(2.77)
    assert p.refs[-1].values["start_key"][0] == pytest.approx(3.03)
    assert not any(f.start < 2.27 for f in p.rounds)
    assert tapes[0].feed.good == tapes[1].feed.good
    assert tapes[0].feed.reasons == tapes[1].feed.reasons


def test_confirmed_candidate_is_not_restarted_or_reused():
    h = harness(True)
    h.ap()
    p = h.s.active("M7", "up")
    h.at(.5, tick=True)
    h.s.reattack_round(p, h.rnd(.6, start=.05))
    h.at(1.5)
    ap2 = h.fact("AP_BUY", ttl=.75, values={"volume": D(10), "start_key": (.75, 10, -1)})
    h.s.on_ap_verdict("up", {"side": "BUY", "start_key": (.75, 10, -1)}, ap2)
    h.s.on_fact(ap2)
    assert p.candidate is not None and h.s.active("M7", "up") is p
    assert len(h.signals("M7")) == 1
    assert p.root != ap2.id


def test_opt_in_keeps_other_strategies_physical_candidates_unchanged():
    outputs = []
    for enabled in (False, True):
        tape = Tape()
        Strategies(tape.feed, signal_variant=M5_M10_FAILED_ROOT, parent_filters=False,
                   frequency_parameters={"M7": {"restart_after_failed_attack": enabled}})
        play_restart_path(tape)
        for at in (3.90, 3.95, 4.00, 4.05, 4.10, 4.15):
            tape.books(at)
        tape.trade(4.15, "SELL", 5)
        tape.books(4.16, bid=95)
        tape.books(4.19)
        for at in (4.20, 4.25, 4.30, 4.35, 4.40, 4.45, 4.50, 4.55, 4.60, 4.65, 4.70):
            tape.books(at)
        candidates = [p for k, p in tape.rows if k == "SIGNAL" and p["strategy"] != "M7"]
        assert any(p["strategy"] == "M10" for p in candidates)
        outputs.append(sorted((p["strategy"], p["payload"]["token"], p["payload"]["confirmed"],
                               p["payload"]["deadline"], p["payload"]["anchor_price"])
                              for p in candidates))
    assert outputs[0] == outputs[1]


def test_restart_key_is_versioned_and_requires_a_boolean():
    assert resolve_frequency_parameters(M5_M10_FAILED_ROOT)["M7"]["restart_after_failed_attack"] is False
    with pytest.raises(ValueError, match="UNKNOWN_FREQUENCY_PARAMETER"):
        resolve_frequency_parameters(M5_M10_CALIBRATION, {"M7": {"restart_after_failed_attack": True}})
    with pytest.raises(ValueError, match="REQUIRES_BOOLEAN"):
        resolve_frequency_parameters(M5_M10_FAILED_ROOT, {"M7": {"restart_after_failed_attack": 1}})


def test_m7_only_shadows_preserve_primary_and_do_not_claim_other_strategy_samples():
    standalone, shared = Tape(), Tape()
    one = Strategies(standalone.feed, signal_variant=M5_M10_FAILED_ROOT, parent_filters=False)
    primary = Strategies(shared.feed, signal_variant=M5_M10_FAILED_ROOT, parent_filters=False)
    managers = attach_profiles(shared.feed, primary,
        {"control_v6": {}, "restart": {"M7": {"restart_after_failed_attack": True}}},
        "control_v6", shadow_targets=("M7",))
    for tape in (standalone, shared):
        play_m10_book_path(tape)
    def physical(tape, manager):
        return sorted((f.values["strategy"], f.token, f.confirmed, f.deadline, f.anchor)
                      for i in manager.published if (f := tape.feed.registry.facts[i]))
    assert physical(standalone, one) == physical(shared, primary)
    assert shared.feed.good == standalone.feed.good and shared.feed.reasons == standalone.feed.reasons
    assert managers["restart"].targeted_strategies == {"M7"}
    assert all(s == "M7" for s, _ in managers["restart"].campaigns)
