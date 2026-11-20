"""GAP and tick-result events must never inherit a previous frame's clock."""

from copy import deepcopy

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration

from m1234_fixtures import fixture_events


@pytest.mark.asyncio
async def test_disconnect_quality_evidence_uses_current_input_availability(tmp_path):
    initial = deepcopy(fixture_events()[0])
    initial["definition"]["settings"].update(
        profiles={},
        sop=definition(),
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
    engine = SopEngine(tmp_path)
    await engine.ingest(initial)
    start = initial["available_mono"]
    wall = initial["available_wall"]
    await engine.ingest(
        {
            "schema": initial["schema"],
            "seq": 2,
            "kind": "CONNECT",
            "available_mono": start,
            "available_wall": wall,
            "generation": 1,
        }
    )
    await engine.ingest(
        {
            "schema": initial["schema"],
            "seq": 3,
            "kind": "GAP",
            "available_mono": start + 0.123,
            "available_wall": wall + 0.123,
            "generation": 1,
            "reason": "WS_DISCONNECTED",
        }
    )
    engine.store.commit()
    import json

    evidence = [
        json.loads(raw)
        for (raw,) in engine.store.db.execute(
            "SELECT payload FROM audit WHERE kind='SOP_EVIDENCE' AND input_seq=3"
        )
    ]
    assert evidence
    assert all(row["input_seq"] == 3 for row in evidence)
    assert all(row["available"] == start + 0.123 for row in evidence)
    assert all(row["payload"]["available"] == start + 0.123 for row in evidence)
    await engine.close()
