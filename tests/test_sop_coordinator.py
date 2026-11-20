"""Exercise native run reporting, settlement, retirement and final cleanup."""

import json
from functools import partial

import pytest
from m1223_report import install_report_compatibility
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m12 import Parameters as M12Parameters
from sop_signals.m13 import Parameters as M13Parameters
from sop_signals.m16 import Parameters as M16Parameters

import test_m1234_sim_continuous as native
from m1234_fixtures import fixture_events
from polymaker.m1234_sim import runner
from polymaker.m1234_sim.runstate import inspect_run


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [M12Parameters(), M13Parameters(), M16Parameters()])
async def test_sop_report_namespace_survives_entire_native_coordinator(tmp_path, monkeypatch, parameters):
    # Public frames and settlement, without legacy M1/M234 tick-request IDs.
    tape = fixture_events()
    tape[0]["definition"]["settings"].update(
        profiles={},
        sop=definition(parameters),
        sop_execution=execution_configuration(),
        scenarios={
            "trade-through": {
                "post_ms": 100,
                "ack_ms": 200,
                "cancel_ms": 200,
                "ahead_cancel_fraction": "0",
            }
        },
    )
    if isinstance(parameters, M16Parameters):
        from test_sop_m16_feed import synthetic_clock_evidence

        tape[0]["definition"]["settings"]["sop_receiver_clock"] = synthetic_clock_evidence()
    monkeypatch.setattr(native, "rr1_events", lambda _mode: tape)
    monkeypatch.setattr(runner, "Engine", partial(SopEngine, parameters=parameters))
    install_report_compatibility()
    run = native.coordinator(tmp_path, monkeypatch, markets=3)
    result = await run.run()
    assert result["status"] == "PASS" and result["complete_windows"] == 3
    assert result["primary_error"] is None and not result["cleanup_errors"]
    assert all(e.store.closed for e in run.engines)
    assert all(not recorder.thread.is_alive() for recorder in run.recorders.values())
    assert run.logs.closed
    assert inspect_run(run.directory)["status"] == "PASS"
    report = json.loads((run.directory / "latest.json").read_text())
    assert report["status"] == "PASS"
    assert len(report["markets"]) == 3
    assert all(a["profile"] == definition(parameters)["strategy"] for m in report["markets"] for a in m["accounts"])
