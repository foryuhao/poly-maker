import asyncio
import json
from copy import deepcopy

import pytest
from m234_sim_main import MainEngine
from m511_sim import GROUPS, M5_M10_GROUPS, NEXT_RESEARCH_GROUPS, ResearchEngine, ResearchOnlyEngine, definition
from m511_sim_instant_fill import ResearchInstantFill
from m511_sim_metrics import collect
from maker_signals.strategies import M910_DENSITY, SIGNAL_VARIANTS, strategy_version

from m1234_fixtures import fixture_events
from polymaker.m1234_sim.report import summary
from test_m511_feed import Tape, play_m10_book_path


@pytest.mark.parametrize("signal_variant", [None, *SIGNAL_VARIANTS])
def test_additive_extension_preserves_every_old_broker_and_economic_order_id(
    tmp_path, signal_variant
):
    async def run():
        tape = fixture_events()
        original = MainEngine(tmp_path / "original", derive=True)
        new = ResearchEngine(tmp_path / "new", derive=True, signal_variant=signal_variant)
        for event in tape:
            await original.ingest(deepcopy(event))
            await new.ingest(deepcopy(event))
        assert set(original.brokers) == set(new.brokers) - {
            "research/trades-only",
            "research/cancel-half",
        }
        for name, broker in original.brokers.items():
            assert broker.snapshot() == new.brokers[name].snapshot(), name
        for scenario in ("research/trades-only", "research/cancel-half"):
            assert set(new.brokers[scenario].accounts) == set(GROUPS)
        assert new.definition["settings"]["m5_m11"].get("signal_variant") == signal_variant
        versions = new.definition["settings"]["m5_m11"]["strategy_versions"]
        assert {s: versions[s] for s in ("M7", "M9", "M10")} == {
            s: strategy_version(s, signal_variant) for s in ("M7", "M9", "M10")}
        await original.close()
        await new.close()
        result = summary(tmp_path / "new/simulation.sqlite3")
        assert any(x["strategy"] == "M10" for x in result["accounts"])
        assert all(x["strategy"] != "M1" for x in result["accounts"] if x["profile"] == "M10")

    asyncio.run(run())


@pytest.mark.parametrize('enabled_strategies,variant', [(None,None),(NEXT_RESEARCH_GROUPS,M910_DENSITY)])
def test_direct_m5_m10_captures_executes_and_validates_only_the_requested_strategies(tmp_path, enabled_strategies, variant):
    async def run():
        fixture = fixture_events()
        init = fixture[0]
        cfg = init["definition"]["settings"]
        cfg["profiles"] = {}
        # The forward runner ingests the INIT object before reading it back as JSON.
        cfg["m5_m11"] = definition(variant, only_m5_m10=True, enabled_strategies=enabled_strategies)
        from m510_trade_through import SCENARIO_SETTINGS
        cfg["scenarios"] = SCENARIO_SETTINGS
        meta = init["definition"]["market"]
        condition = meta["condition_id"]
        tokens = dict(zip(("up", "down"), (t["token_id"] for t in meta["tokens"]), strict=True))
        events = [init, fixture[1]]
        wall_zero = init["available_wall"]

        def append(at, kind, **payload):
            events.append({
                **fixture[1], "seq": len(events) + 1, "kind": kind,
                "available_mono": at, "available_wall": wall_zero + at, **payload,
            })

        class RecordedTape(Tape):
            def __init__(self):
                self.seq = 0

            def apply(self, at, rows, available=None):
                self.seq += 1
                at += 16
                raw = []
                for row in rows if isinstance(rows, list) else [rows]:
                    raw.append({
                        **row, "asset_id": tokens[row["asset_id"]], "market": condition,
                        "timestamp": str((wall_zero + at) * 1000),
                    })
                append(at, "FRAME", raw=raw, received_mono=at, received_wall=wall_zero + at)

            def tick(self, at, pending=()):
                self.seq += 1
                append(at + 16, "TIMER")

        tape = RecordedTape()
        play_m10_book_path(tape)
        tape.trade(2.34, "SELL", 200, price=".39")
        tape.books(3.35)
        tape.tick(3.35)
        append(240, "TIMER")
        append(300.1, "END", reason="T300_OBSERVED")
        append(305, "SETTLEMENT", raw=fixture[-1]["raw"])

        source = tmp_path / "direct"
        engine = ResearchOnlyEngine(source / meta["slug"], signal_variant=variant,
                                    enabled_strategies=enabled_strategies)
        expected_groups = enabled_strategies or M5_M10_GROUPS
        engine.store.run_owner = "direct-fixture"
        (source / "plan.json").write_text(json.dumps({
            "mode": "SIMULATION_ONLY", "real_order_transport": "ABSENT",
            "run_identity": "direct-fixture", "settings": cfg,
        }))
        for event in events:
            await engine.ingest(event)
        assert not (engine.directory / "prepost").exists()
        assert set(engine.brokers) == {"research/trade-through"}
        assert all(tuple(broker.accounts) == expected_groups for broker in engine.brokers.values())
        assert engine.coverage["strategy_health"].keys() == {"M5_M10"}
        assert engine.research_extension.research_signals.m11 == {}
        assert any(mark["token"] == tokens["up"] and mark["mid"] == "0.41" for mark in engine.marks)
        await engine.close()
        report = summary(engine.store.path)
        assert {row["strategy"] for row in report["accounts"]} == set(expected_groups)
        assert set(report["fill_models"]) == {"research/trade-through"}
        assert report["fill_models"]["research/trade-through"]["quantity"].startswith("min(")
        assert all("trades-only" not in value for value in report["limitations"])
        metrics = collect(source, tmp_path / "metrics-cache.json")
        assert set(metrics["modes"]) == {"research"}
        assert set(metrics["modes"]["research"]["strategies"]) == set(expected_groups)
        ledger = ResearchInstantFill(source, tmp_path / "assumed", "research/trade-through")
        try:
            result = ledger.refresh()
            assert result["status"] == "OK"
            assert set(result["strategies"]) == set(expected_groups)
            assert result["strategies"]["M10"]["signals"] >= 1
            assert result["strategies"]["M10"]["assumed_filled_orders"] >= 1
            assert result["strategies"]["M10"]["settled_orders"] >= 1
        finally:
            ledger.close()

    asyncio.run(run())
