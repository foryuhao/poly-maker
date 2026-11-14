import asyncio
import gc
import weakref
from copy import deepcopy
from functools import partial

import pytest
from m510_trade_through import SCENARIO_SETTINGS
from m511_sim import ResearchOnlyEngine, definition

from m1234_fixtures import fixture_events
from polymaker.m1234_sim import runner
from polymaker.m1234_sim.events import digest
from polymaker.m1234_sim.report import read_state
from test_m1234_sim_continuous import coordinator


@pytest.mark.parametrize("connect", [False, True])
def test_feed_created_with_gc_disabled_releases_even_first_shared_observers(connect):
    from maker_signals.feed import ResearchFeed
    from maker_signals.strategies import Strategies

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        feed = ResearchFeed("market", ("up", "down"), {"up": ".01", "down": ".01"},
                            lambda *_: None)
        Strategies(feed, parent_filters=False)
        if connect:
            feed.connect(now=0, wall=1000, seq=0)
        references = [weakref.ref(feed), weakref.ref(feed.registry)]
        feed.release_runtime()
        del feed
        assert all(r() is None for r in references)
    finally:
        if was_enabled:
            gc.enable()


def test_research_retirement_releases_graph_after_durable_close(tmp_path, monkeypatch):
    async def run():
        events = deepcopy(fixture_events())
        cfg = events[0]["definition"]["settings"]
        cfg.update(profiles={}, scenarios=SCENARIO_SETTINGS, m5_m11=definition(only_m5_m10=True))
        e = ResearchOnlyEngine(tmp_path / "market")
        for event in events:
            await e.ingest(event)
        references = [weakref.ref(x) for x in (
            e.research_extension, e.research_extension.research,
            e.research_extension.research.registry, e.research_extension.research_signals)]
        with pytest.raises(RuntimeError, match="DURABLE_CLOSE"):
            e.retire_runtime()
        await e.close()
        before = digest(read_state(e.store.path))
        # Include an already detached observer kept alive only by its callback
        # cycle; it is no longer in the feed's active observer list.
        from maker_signals.primitives import Rounds
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            observer = Rounds(e.research_extension.research, "up", "orphan", None)
            observer.callback = observer.close
            references.append(weakref.ref(observer))
            del observer
            with monkeypatch.context() as guarded:
                def forbidden_global_collection(*args, **kwargs):
                    raise AssertionError("retirement must not scan the global heap")
                guarded.setattr(gc, "collect", forbidden_global_collection)
                e.retire_runtime()
            assert all(r() is None for r in references)
        finally:
            if was_enabled:
                gc.enable()
        assert all(r() is None for r in references)
        assert e.retirement_gc_seconds == e.retirement_collected_objects == 0
        assert e.retirement_release_seconds >= 0 and e.retirement_release_cpu_seconds >= 0
        e.brokers.clear()
        e.last_books = None
        e.tick_verification = None
        gc.collect()
        assert all(r() is None for r in references)
        assert digest(read_state(e.store.path)) == before
    asyncio.run(run())


async def test_retirement_preserves_partial_unknown_orders_and_pending_settlement(tmp_path):
    from decimal import Decimal

    from m510_fixtures import partial_unsettled_events

    engine = ResearchOnlyEngine(tmp_path / "artificial-partial-unsettled")
    for event in partial_unsettled_events():
        await engine.ingest(event)
    await engine.close()
    before = read_state(engine.store.path)
    broker = before["brokers"]["research/trade-through"]
    orders = list(broker["orders"].values())
    assert any(0 < Decimal(o["filled"]) < Decimal(o["intent"]["quantity"])
               and o["uncertain"] for o in orders)
    assert before["resolution"] is None
    assert Decimal(broker["accounts"]["M10"]["spent"]) > 0
    assert Decimal(broker["accounts"]["M10"]["payout"]) == 0
    ref = weakref.ref(engine.research_extension)
    engine.retire_runtime()
    engine.brokers.clear()
    gc.collect()
    assert ref() is None
    assert read_state(engine.store.path) == before


@pytest.mark.parametrize("unresolved,markets", [(False, 4), (True, 16)])
async def test_native_research_retirement_and_deferred_settlements(
    tmp_path, monkeypatch, unresolved, markets, *, signal_variant=None, frequency_parameters=None
):
    original = runner.LiveRun.__init__

    def init(self, directory, cfg):
        cfg.update(profiles={}, scenarios=SCENARIO_SETTINGS,
                   m5_m11=definition(signal_variant, only_m5_m10=True,
                                     frequency_parameters=frequency_parameters))
        original(self, directory, cfg)

    monkeypatch.setattr(runner.LiveRun, "__init__", init)
    factory = (partial(ResearchOnlyEngine, signal_variant=signal_variant,
                       frequency_parameters=frequency_parameters)
               if signal_variant is not None or frequency_parameters is not None else ResearchOnlyEngine)
    monkeypatch.setattr(runner, "Engine", factory)
    run = coordinator(tmp_path, monkeypatch, markets=markets, unresolved=unresolved)
    original_record = run.record

    async def record(engine, kind, **payload):
        # Legacy fixture REST target names refer to M1/M234 views. This resource
        # test uses only PUBLIC_BOOK and intentionally leaves its changed tick
        # unverified; production public replay retains all original tick events.
        if kind not in {"TICK_REQUEST", "TICK_RESULT"}:
            return await original_record(engine, kind, **payload)

    run.record = record
    result = await run.run()
    assert result["status"] == "PASS"
    retired = [e for e in run.engines if getattr(e, "retired", False)]
    assert retired and all(e.research_extension is None for e in retired)
    assert all(e.store.closed for e in run.engines)
    if unresolved:
        assert len(run.pending) == 2 and len(result["deferred_settlements"]) == markets-2
        assert read_state(run.engines[0].store.path)["resolution"] is None
