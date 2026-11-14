"""Raw-flow and lifecycle checks for opt-in, strategy-owned AP windows."""

import asyncio
import gc
import gzip
import json
import sqlite3
import weakref
from contextlib import closing

import pytest
from m510_frequency_grid import attach_profiles
from m511_sim import ResearchStore
from maker_signals.strategies import (
    M5_M10_FAILED_ROOT,
    M5_M10_LOCAL_AP,
    Strategies,
    resolve_frequency_parameters,
)

from test_m510_memory import (
    test_native_research_retirement_and_deferred_settlements as native_probe,
)
from test_m511_feed import Tape


def local(tape, **parameters):
    return Strategies(tape.feed, signal_variant=M5_M10_LOCAL_AP, parent_filters=False,
                      frequency_parameters=parameters or {"M7": {"ap_window_seconds": .5}})


def prefix(tape, duplicate=False):
    tape.books(0)
    tape.trade(.01, "BUY", 1)
    for at in (.1, .2, .3):
        tape.books(at)
    tape.trade(.35, "BUY", 1, identity="0x" + "a"*64)
    if duplicate:
        tape.trade(.35, "BUY", 1, identity="0x" + "a"*64)
    tape.books(.4)
    tape.books(.5)


def test_private_ap_waits_for_watermark_and_does_not_backdate_or_count_duplicate():
    tape = Tape()
    local(tape)
    prefix(tape, duplicate=True)
    tape.tick(.51, pending=[.50])
    assert not tape.facts("AP_BUY_M7_PRIVATE")
    tape.tick(.53)
    fact, = tape.facts("AP_BUY_M7_PRIVATE")
    assert fact.confirmed == .53 and fact.end == pytest.approx(.51)
    assert fact.values["volume"] == 2 and fact.values["minimum"] == 1
    assert len(fact.values["event_ids"]) == 2
    assert not tape.facts("AP_BUY")


def test_print_at_window_end_is_excluded():
    tape = Tape()
    local(tape)
    prefix(tape)
    tape.trade(.51, "SELL", 100)
    tape.tick(.51)
    fact, = tape.facts("AP_BUY_M7_PRIVATE")
    assert fact.values["volume"] == 2


@pytest.mark.parametrize("prints", [((.01, "BUY", 2),),
                                   ((.01, "BUY", 1), (.02, "BUY", 1)),
                                   ((.01, "BUY", 1), (.35, "SELL", 10)),
                                   ((.01, "BUY", ".1"), (.35, "BUY", ".1"))])
def test_private_ap_keeps_multitrade_direction_bin_and_minimum_requirements(prints):
    tape = Tape()
    local(tape)
    times = sorted({0, .1, .2, .3, .4, .5, *(p[0] for p in prints)})
    for at in times:
        tape.books(at)
        for when, side, quantity in prints:
            if when == at:
                tape.trade(at, side, quantity)
    tape.tick(.51)
    assert not tape.facts("AP_BUY_M7_PRIVATE")


def test_local_window_timeout_keeps_public_quality_and_other_strategy_evidence():
    tape = Tape()
    manager = local(tape)
    prefix(tape)
    tape.tick(.58, pending=[.50])
    assert tape.feed.good["up"] and not tape.facts("AP_BUY_M7_PRIVATE")
    assert manager.private_ap["M7", "up"].attacks == {}
    assert any(p["event"] == "LOCAL_AP_UNAVAILABLE" for k, p in tape.rows if k == "RESEARCH_EVIDENCE")


def test_real_public_gap_discards_private_pending_evidence():
    tape = Tape()
    manager = local(tape)
    tape.books(0)
    tape.trade(.01, "BUY", 2)
    tape.feed.unavailable("up", "UNRECONSTRUCTABLE_SOURCE_ORDER")
    assert manager.private_ap["M7", "up"].attacks == {}
    tape.books(.3)
    tape.trade(.35, "BUY", 2)
    tape.books(.5)
    tape.tick(.51)
    assert not tape.facts("AP_BUY_M7_PRIVATE")


def path(tape, second_quantity=8):
    trades = {1: ("BUY", 10), 40: ("BUY", 10), 53: ("BUY", 10),
              80: ("SELL", 10), 131: ("SELL", second_quantity), 182: ("SELL", "6.4")}
    depth = {82: 90, 90: 96, 130: 100, 133: 92, 141: "96.8", 181: 100,
             184: "93.6", 192: "97.44"}
    bid = 100
    for i in range(234):
        at = i/100
        bid = depth.get(i, bid)
        if i % 5 == 0 or i in depth:
            tape.books(at, bid=bid)
        if i in trades:
            tape.trade(at, *trades[i])
        tape.tick(at)


def public_physics(tape):
    result = []
    for fact in tape.feed.registry.facts.values():
        if fact.kind not in {"AP_BUY", "AP_SELL", "EX_BUY", "EX_SELL", "RF_BID", "ROUND"}:
            continue
        if fact.kind == "ROUND" and fact.values["scope"] != "shared":
            continue
        scalars = {key: str(value) for key, value in fact.values.items()
                   if key in {"volume", "minimum", "depth", "hit", "loss", "u", "d", "recovery",
                              "qualified", "stable", "subwindow_volumes"}}
        result.append((fact.kind, fact.token, fact.start, fact.end, fact.confirmed, fact.deadline,
                       str(fact.anchor), tuple(sorted(scalars.items()))))
    return sorted(result)


def test_raw_private_m10_requires_two_actual_returns_and_leaves_public_primitives_unchanged():
    baseline, experiment = Tape(), Tape()
    Strategies(baseline.feed, signal_variant=M5_M10_FAILED_ROOT, parent_filters=False)
    local(experiment, M7={"ap_window_seconds": .5}, M10={"ap_window_seconds": .5})
    path(baseline)
    path(experiment)
    candidates = [p["payload"] for k, p in experiment.rows if k == "SIGNAL" and p["strategy"] == "M10"]
    assert len(candidates) == 1 and len(candidates[0]["round_ids"]) == 2
    assert candidates[0]["confirmed"] == pytest.approx(1.81)
    assert candidates[0]["deadline"] == pytest.approx(9.81)
    assert candidates[0]["root_id"].split(":")[2] == "AP_BUY_M10_PRIVATE"
    assert public_physics(baseline) == public_physics(experiment)
    def qualities(tape):
        return [p for k, p in tape.rows if k == "RESEARCH_EVIDENCE" and p["event"] == "QUALITY_UNAVAILABLE"]
    assert qualities(baseline) == qualities(experiment)
    def others(tape):
        return sorted((p["strategy"], p["payload"]["token"], p["payload"]["confirmed"],
                       p["payload"]["deadline"], p["payload"]["anchor_price"])
                      for k, p in tape.rows if k == "SIGNAL" and p["strategy"] in {"M5", "M6", "M8"})
    assert others(baseline) == others(experiment)


def test_new_variant_defaults_keep_shared_ap_and_old_variant_rejects_new_knob():
    tape = Tape()
    assert local(tape, M7={"ap_window_seconds": .75}).private_ap == {}
    with pytest.raises(ValueError, match="UNKNOWN_FREQUENCY_PARAMETER"):
        resolve_frequency_parameters(M5_M10_FAILED_ROOT, {"M7": {"ap_window_seconds": .5}})
    for value in (.1, .751, float("nan"), float("inf"), True, ".5"):
        with pytest.raises(ValueError, match="PRIVATE_AP_WINDOW"):
            resolve_frequency_parameters(M5_M10_LOCAL_AP, {"M10": {"ap_window_seconds": value}})


def test_private_pressure_cycles_detach_without_collecting_gc():
    tape = Tape()
    manager = local(tape)
    prefix(tape)
    reference = weakref.ref(manager)
    pressures = list(manager.private_ap.values())
    assert any(p.attacks for p in pressures)
    enabled = gc.isenabled()
    gc.disable()
    try:
        tape.feed.release_runtime()
        assert all(p.c is None and p.m is None and not p.attacks and not p.active_ap for p in pressures)
        del manager
        assert reference() is None
    finally:
        if enabled:
            gc.enable()


def test_private_ap_does_not_restart_from_a_trade_after_public_invalidation_or_other_token():
    tape = Tape()
    manager = local(tape)
    prefix(tape)
    pressure = manager.private_ap["M7", "up"]
    pressure.reset("GAP")
    pressure.trade(tape.feed.flows["down"].trades[-1])
    assert not pressure.attacks
    tape.feed.good["up"] = False
    pressure.trade(tape.feed.flows["up"].trades[-1])
    assert not pressure.attacks


def m7_path(tape, refill_at=.52, buy_quantity=1):
    refill = round(refill_at*100)
    trades = {1: ("BUY", buy_quantity), 35: ("BUY", buy_quantity), refill: ("SELL", 3),
              130: ("BUY", buy_quantity), 164: ("BUY", buy_quantity)}
    bid = 100
    for i in range(191):
        at = i/100
        if i == refill+1:
            bid = 97
        elif i == refill+8:
            bid = 100
        if i % 5 == 0 or i == refill+1:
            tape.books(at, bid=bid)
        if i in trades:
            tape.trade(at, *trades[i])
        tape.tick(at)


def test_raw_private_m7_requires_new_support_pause_and_wholly_fresh_second_attack():
    tape = Tape()
    local(tape)
    m7_path(tape)
    candidates = [p["payload"] for k, p in tape.rows if k == "SIGNAL" and p["strategy"] == "M7"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["confirmed"] == pytest.approx(1.8)
    assert candidate["deadline"] == pytest.approx(2.55)
    assert len(candidate["round_ids"]) == 1
    refs = [tape.feed.registry.facts[ident] for ident in candidate["current_required_refs"]]
    rf = next(f for f in refs if f.kind == "RF_M7")
    ap2 = next(f for f in refs if f.kind == "AP_BUY_M7_PRIVATE")
    assert tuple(ap2.values["start_key"]) > (rf.confirmed, rf.input_seq, rf.values["available_subindex"])
    ex = [tape.feed.registry.facts[ident] for ident in candidate["historical_refs"]
          if tape.feed.registry.facts[ident].kind == "EX_BUY_M7_SHORT"][0]
    assert ap2.start > ex.confirmed


def test_raw_private_m10_cannot_skip_an_equal_pressure_second_return():
    tape = Tape()
    local(tape, M10={"ap_window_seconds": .5})
    path(tape, second_quantity=10)
    assert not [p for k, p in tape.rows if k == "SIGNAL" and p["strategy"] == "M10"]
    assert any(p["event"] == "CAMPAIGN_ENDED" and p["payload"].get("strategy") == "M10"
               and p["payload"]["reason"] == "RETURN_DECAY_FAILED"
               for k, p in tape.rows if k == "RESEARCH_EVIDENCE")


def test_raw_private_m7_early_attack_is_not_relabelled_as_confirmation():
    tape = Tape()
    local(tape)
    trades = {1: ("BUY", 1), 35: ("BUY", 1), 130: ("BUY", 1),
              164: ("BUY", 1), 200: ("SELL", 3)}
    bid = 100
    for i in range(271):
        at = i/100
        if i == 201:
            bid = 97
        elif i == 210:
            bid = 100
        if i % 5 == 0 or i == 201:
            tape.books(at, bid=bid)
        if i in trades:
            tape.trade(at, *trades[i])
        tape.tick(at)
    assert not [p for k, p in tape.rows if k == "SIGNAL" and p["strategy"] == "M7"]
    assert any(p["event"] == "CAMPAIGN_ENDED" and p["payload"].get("strategy") == "M7"
               and p["payload"]["reason"] == "EARLY_REATTACK"
               for k, p in tape.rows if k == "RESEARCH_EVIDENCE")


def test_private_attack_starting_before_rf_cannot_confirm_even_if_it_closes_after_rf():
    tape = Tape()
    local(tape)
    m7_path(tape, refill_at=1.4, buy_quantity=10)
    assert not [p for k, p in tape.rows if k == "SIGNAL" and p["strategy"] == "M7"]
    assert any(p["event"] == "CAMPAIGN_ENDED" and p["payload"].get("strategy") == "M7"
               and p["payload"]["reason"] == "REATTACK_STARTED_BEFORE_RF"
               for k, p in tape.rows if k == "RESEARCH_EVIDENCE")


@pytest.mark.parametrize("unresolved,markets", [(False, 4), (True, 16)])
def test_native_private_ap_retirement_keeps_pending_settlement(tmp_path, monkeypatch, unresolved, markets):
    asyncio.run(native_probe(tmp_path, monkeypatch, unresolved, markets,
        signal_variant=M5_M10_LOCAL_AP,
        frequency_parameters={"M7": {"ap_window_seconds": .5}, "M10": {"ap_window_seconds": .5}}))


@pytest.mark.parametrize("strategy,play", [("M7", m7_path), ("M10", path)])
def test_private_ap_shadow_matches_standalone_and_preserves_independent_primary(strategy, play):
    standalone, shared, control = Tape(), Tape(), Tape()
    one = local(standalone, **{strategy: {"ap_window_seconds": .5}})
    primary = Strategies(shared.feed, signal_variant=M5_M10_LOCAL_AP, parent_filters=False)
    control_manager = Strategies(control.feed, signal_variant=M5_M10_LOCAL_AP, parent_filters=False)
    managers = attach_profiles(shared.feed, primary, {
        "control": {}, "m7": {"M7": {"ap_window_seconds": .5}},
        "m10": {"M10": {"ap_window_seconds": .5}}}, "control",
        profile_targets={"m7": ["M7"], "m10": ["M10"]})
    for tape in (standalone, shared, control):
        play(tape)

    def candidates(tape, manager, selected=None):
        return sorted((f.values["strategy"], f.token, f.confirmed, f.deadline, f.anchor)
                      for ident in manager.published if (f := tape.feed.registry.facts[ident])
                      and (selected is None or f.values["strategy"] == selected))

    assert candidates(standalone, one, strategy)
    assert candidates(standalone, one, strategy) == candidates(shared, managers[strategy.lower()])
    assert candidates(shared, primary) == candidates(control, control_manager)
    assert public_physics(shared) == public_physics(control)


def test_private_diagnostics_are_preserved_in_the_committed_compressed_archive(tmp_path):
    database = tmp_path / "simulation.sqlite3"
    store = ResearchStore(database, scope="M5_M10")
    payloads = [{"event": event, "payload": {"strategy": "M7", "window_seconds": .5,
                 "reason": "TEST_LOCAL_WINDOW", "volume": "1.25"}}
                for event in ("LOCAL_AP_NOT_CONFIRMED", "LOCAL_AP_UNAVAILABLE")]
    try:
        store.input_seq = 7
        for payload in payloads:
            store.emit("RESEARCH_EVIDENCE", payload)
    finally:
        store.close()
    with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        meta = json.loads(db.execute("SELECT payload FROM metadata WHERE name='diagnostic_archive'").fetchone()[0])
        assert db.execute("SELECT count(*) FROM audit").fetchone()[0] == 0
    with gzip.open(tmp_path / meta["file"], "rt") as stream:
        rows = [json.loads(line) for line in stream]
    assert [row["payload"] for row in rows] == payloads
    assert [row["input_seq"] for row in rows] == [7, 7]
    assert meta["closed"] and meta["rows"] == meta["research_rows"] == 2


def test_shared_opposite_ap_still_invalidates_a_confirmed_private_buy_reference():
    tape = Tape()
    manager = local(tape)
    m7_path(tape)
    candidate = manager.active("M7", "up")
    assert candidate and candidate.candidate and not candidate.closed
    ap2 = next(f for f in candidate.refs if f.kind == "AP_BUY_M7_PRIVATE")
    public = tape.feed.fact("AP_SELL", "up", tape.feed.now+.75, tape.feed.now-.75,
                            tape.feed.now, values={"volume": 20, "side": "SELL"})
    quality = dict(tape.feed.good)
    manager.on_fact(public)
    manager.tick()
    assert candidate.closed and tape.feed.registry.endings[ap2.id]["reason"] == "REVERSE_AP"
    assert tape.feed.registry.valid(public, tape.feed.now)
    assert quality == tape.feed.good
