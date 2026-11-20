"""Retained terminal Engine identities must not pin sealed runtime graphs."""

import gc
import weakref

import pytest
from sop_signals.m12 import Parameters as M12Parameters
from sop_signals.m13 import Parameters as M13Parameters

from polymaker.m1234_sim.report import read_state
from test_sop_parent_decode import frame, initialized


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters", [M12Parameters(), M13Parameters()])
async def test_retirement_frees_public_book_and_policy_graph_but_keeps_durable_evidence(tmp_path, parameters):
    engine, initial = await initialized(tmp_path, parameters)
    await engine.ingest(frame(initial, [
        {"event_type": "book", "asset_id": token, "market": engine.meta.condition_id,
         "timestamp": str(round((initial["available_wall"] + .01) * 1000)),
         "bids": [{"price": ".50", "size": "100"}],
         "asks": [{"price": p, "size": "100"} for p in (".52", ".53", ".54")]}
        for token in ("1", "2")]))
    signal_ref = weakref.ref(engine.signals)
    book_ref = weakref.ref(engine.signals.base.m1)
    policy_ref = weakref.ref(engine.extra_policies[0])
    broker_ref = weakref.ref(engine.extra_policies[0].broker)
    with pytest.raises(RuntimeError, match="SOP_RETIRE_BEFORE_DURABLE_CLOSE"):
        engine.retire_runtime()
    assert signal_ref() is engine.signals and not engine.store.closed
    await engine.ingest({"schema": initial["schema"], "seq": 4, "kind": "END",
                         "available_mono": 300, "available_wall": initial["available_wall"] + 300,
                         "generation": 1, "reason": "SYNTHETIC_RETIREMENT_TEST"})
    with pytest.raises(RuntimeError, match="SOP_RETIRE_BEFORE_DURABLE_CLOSE"):
        engine.retire_runtime()
    await engine.close()
    before = read_state(engine.store.path)
    market, coverage, resolution = engine.meta, dict(engine.coverage), engine.resolution
    engine.retire_runtime()
    gc.collect()
    assert engine.signals is None and engine.sop_runtime is None
    assert not engine.extra_policies and not engine.m1_policies and not engine.brokers
    assert all(ref() is None for ref in (signal_ref, book_ref, policy_ref, broker_ref))
    assert engine.meta == market and engine.coverage == coverage and engine.resolution == resolution
    assert read_state(engine.store.path) == before
    # Identity and the closed audit remain usable for official/pending recovery.
    assert engine.store.path.exists() and engine.store.closed and engine.ended
    engine.retire_runtime()  # Idempotent after sealing, without reviving a feed.
