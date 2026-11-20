"""One immutable raw parent, unchanged error boundary, bounded decode lifetime."""

import json
from copy import deepcopy

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m12 import Parameters as M12Parameters
from sop_signals.m13 import Parameters as M13Parameters

from m1234_fixtures import fixture_events


async def initialized(tmp_path, parameters):
    initial = deepcopy(fixture_events()[0])
    initial["definition"]["settings"].update(
        profiles={}, sop=definition(parameters), sop_execution=execution_configuration(),
        scenarios={"trade-through": {"post_ms": 100, "ack_ms": 200,
                                      "cancel_ms": 200, "ahead_cancel_fraction": "0"}})
    engine = SopEngine(tmp_path, parameters=parameters)
    await engine.ingest(initial)
    wall = initial["available_wall"]
    await engine.ingest({"schema": initial["schema"], "seq": 2, "kind": "CONNECT",
                         "available_mono": 0, "available_wall": wall, "generation": 1})
    return engine, initial


def frame(initial, raw):
    return {"schema": initial["schema"], "seq": 3, "kind": "FRAME", "raw": raw,
            "available_mono": .01, "available_wall": initial["available_wall"] + .01,
            "received_mono": .01, "received_wall": initial["available_wall"] + .01,
            "generation": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [M12Parameters(), M13Parameters()])
@pytest.mark.parametrize("encoded", [True, False])
async def test_one_decode_shared_by_both_feeds_and_matching_without_mutating_parent(
        tmp_path, monkeypatch, parameters, encoded):
    engine, initial = await initialized(tmp_path, parameters)
    parent = [{"event_type": "book", "asset_id": token, "market": engine.meta.condition_id,
               "timestamp": str(round((initial["available_wall"] + .01) * 1000)),
               "bids": [{"price": ".50", "size": "100"}],
               "asks": [{"price": p, "size": "100"} for p in (".52", ".53", ".54")]}
              for token in ("1", "2")]
    original = deepcopy(parent)
    raw = json.dumps(parent) if encoded else parent
    native_loads, calls = json.loads, []

    def counted(value, *args, **kwargs):
        if value is raw:
            calls.append(value)
        return native_loads(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", counted)
    await engine.ingest(frame(initial, raw))
    assert len(calls) == (1 if encoded else 0)
    assert parent == original
    assert engine.sop_runtime.books["1"].complete and engine.signals.m1.books["1"].bids
    assert engine._decoded_parent_frame is None
    assert engine.validity["status"] == "VALID" and not engine.needs_reconnect
    await engine.close()
    assert engine._decoded_parent_frame is None


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["{", "[1]", '"not-an-event"'])
async def test_parse_errors_keep_native_gap_semantics_and_clear_parent(tmp_path, raw):
    engine, initial = await initialized(tmp_path, M13Parameters())
    await engine.ingest(frame(initial, raw))
    assert engine.seq == 3 and engine.needs_reconnect
    assert engine.validity["status"] == "VALID"  # Bad input is a GAP, not an engine failure.
    assert engine._decoded_parent_frame is None
    assert not engine.sop_runtime.core.signals
    await engine.close()
