"""Selected count-only profiles reach the audited engine without changing defaults."""

import asyncio
import json
from copy import deepcopy

import pytest
from m510_trade_through import SCENARIO_SETTINGS
from m511_sim import ResearchEngine, ResearchOnlyEngine, definition
from maker_signals.strategies import (
    M5_M10_CALIBRATION,
    M5_M10_NEXT,
    NEXT_PARAMETERS,
    resolve_frequency_parameters,
)

from m1234_fixtures import fixture_events
from test_m510_memory import (
    test_native_research_retirement_and_deferred_settlements as native_probe,
)
from test_m510_phase3 import PRIVATE, play_private_m9
from test_m511_feed import Tape


def test_selected_overrides_are_resolved_without_mutating_global_or_input_parameters():
    parameters = deepcopy(PRIVATE)
    expected = resolve_frequency_parameters(M5_M10_CALIBRATION, parameters)
    value = definition(M5_M10_CALIBRATION, only_m5_m10=True, frequency_parameters=parameters)
    assert value["frequency_parameters"] == expected
    parameters["M9"]["private_seed"] = False
    assert value["frequency_parameters"]["M9"]["private_seed"] is True
    assert definition(only_m5_m10=True)["frequency_parameters"] == NEXT_PARAMETERS
    assert set(value["frequency_parameters"]) == {"M7", "M9", "M10"}


@pytest.mark.parametrize("parameters", [
    {"M5": {"hold_seconds": .01}},
    {"M7": {"unpublished_knob": 1}},
    {"M7": {"hold_seconds": .25}},
    {"M10": {"decay_fraction": "1"}},
    {"M9": {"private_seed": 1}},
    {"M9": {"seed_hold_seconds": .5}},
    {"M9": {"seed_stable_recovery": "NaN"}},
    {"M9": {"support_ttl_seconds": "Infinity"}},
])
def test_invalid_parameters_are_rejected_before_creating_output(tmp_path, parameters):
    output = tmp_path / "not-created"
    with pytest.raises(ValueError):
        ResearchOnlyEngine(output, signal_variant=M5_M10_CALIBRATION,
                           frequency_parameters=parameters)
    assert not output.exists()


def test_private_seed_requires_explicit_variant_and_parameters_cannot_enter_additive_scope(tmp_path):
    with pytest.raises(ValueError, match="UNKNOWN_FREQUENCY_PARAMETER"):
        ResearchOnlyEngine(tmp_path / "v4", signal_variant=M5_M10_NEXT, frequency_parameters=PRIVATE)
    with pytest.raises(ValueError, match="REQUIRE_M5_M10_SCOPE"):
        ResearchEngine(tmp_path / "additive", derive=True, signal_variant=M5_M10_CALIBRATION,
                       frequency_parameters=PRIVATE)
    assert not (tmp_path / "v4").exists() and not (tmp_path / "additive").exists()


def test_audited_configuration_reaches_real_strategy_and_causal_candidate(tmp_path):
    async def run():
        fixture = fixture_events()
        init, connected = fixture[:2]
        parameters = deepcopy(PRIVATE)
        expected = definition(M5_M10_CALIBRATION, only_m5_m10=True,
                              frequency_parameters=parameters)
        cfg = init["definition"]["settings"]
        cfg.update(profiles={}, scenarios=SCENARIO_SETTINGS, m5_m11=expected)
        meta = init["definition"]["market"]
        tokens = dict(zip(("up", "down"), (t["token_id"] for t in meta["tokens"]), strict=True))
        events = [init, connected]
        wall_zero = init["available_wall"]

        def append(at, kind, **payload):
            events.append({**connected, "seq": len(events)+1, "kind": kind,
                           "available_mono": at, "available_wall": wall_zero+at, **payload})

        class Recorded(Tape):
            def __init__(self):
                self.seq = 0

            def apply(self, at, rows, available=None):
                self.seq += 1
                at += 16
                rows = rows if isinstance(rows, list) else [rows]
                raw = [{**r, "asset_id": tokens[r["asset_id"]], "market": meta["condition_id"],
                        "timestamp": str((wall_zero+at)*1000)} for r in rows]
                append(at, "FRAME", raw=raw, received_mono=at, received_wall=wall_zero+at)

            def tick(self, at, pending=()):
                self.seq += 1
                append(at+16, "TIMER")

        tape = Recorded()
        play_private_m9(tape)
        append(240, "TIMER")
        append(300.1, "END", reason="T300_OBSERVED")
        append(305, "SETTLEMENT", raw=fixture[-1]["raw"])
        engine = ResearchOnlyEngine(tmp_path / "configured", signal_variant=M5_M10_CALIBRATION,
                                    frequency_parameters=parameters)
        parameters["M9"]["private_seed"] = False
        for event in events:
            await engine.ingest(event)
        assert engine.definition["settings"]["m5_m11"] == expected
        assert engine.research_extension.research_signals.frequency_parameters == expected["frequency_parameters"]
        candidates = [json.loads(p)["payload"] for p, in engine.store.db.execute(
            "SELECT payload FROM audit WHERE kind='SIGNAL' AND json_extract(payload,'$.strategy')='M9'")]
        assert len(candidates) == 1
        assert candidates[0]["strategy_parameters"] == expected["frequency_parameters"]["M9"]
        assert candidates[0]["confirmed"] == pytest.approx(17.77)
        assert set(engine.brokers) == {"research/trade-through"}
        await engine.close()
        engine.retire_runtime()

    asyncio.run(run())


@pytest.mark.parametrize("unresolved", [False, True])
def test_native_coordinator_accepts_the_same_explicit_configuration(tmp_path, monkeypatch, unresolved):
    asyncio.run(native_probe(tmp_path, monkeypatch, unresolved, 4,
                            signal_variant=M5_M10_CALIBRATION, frequency_parameters=PRIVATE))
