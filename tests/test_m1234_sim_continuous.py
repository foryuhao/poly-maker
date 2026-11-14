"""Accelerated native coordinator checks; these are artificial, not public samples."""
import asyncio
import importlib.util
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from m1234_rr1_fixtures import rr1_events
from polymaker.m1234_sim import runner
from polymaker.m1234_sim.engine import market_meta
from polymaker.m1234_sim.events import Clock, digest
from polymaker.m1234_sim.report import read_state
from polymaker.m1234_sim.runstate import inspect_run


def verifier():
    spec = importlib.util.spec_from_file_location("continuous_verify", "ops/m1234_sim_verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify


def test_day_configuration_and_disk_preflight(monkeypatch):
    cfg = runner.settings("config/m1234-sim-24h.toml", "config/m1234-sim-contract.json")
    assert cfg["max_markets"] == 288 and cfg["max_seconds"] == 87300
    assert cfg["m234"]["m4_frequency_study"] is True
    assert set(cfg["profiles"]) == {"M1-virtual", "M1-live-mirror"}
    old = runner.settings("config/m1234-sim.toml", "config/m1234-sim-contract.json")
    assert old["profiles"] == cfg["profiles"] and old["m234"] == cfg["m234"]
    assert old["scenarios"] == cfg["scenarios"]
    for changes in ({"continuous": False}, {"max_markets": 289}, {"max_seconds": 86400},
                    {"max_seconds": 87301}, {"max_pending_markets": 13}, {"max_markets": True}):
        with pytest.raises(ValueError):
            runner.validate_run_limits({**cfg, **changes})
    required = (cfg['max_output_mib'] + cfg['min_free_disk_mib']) * 1024**2
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(free=required - 1))
    check = runner.preflight("config/m1234-sim-24h.toml", "config/m1234-sim-contract.json")
    assert check["status"] == "FAIL" and check["network_requests"] == 0
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(free=required))
    assert runner.preflight("config/m1234-sim-24h.toml", "config/m1234-sim-contract.json")['status'] == 'PASS'


def coordinator(tmp_path, monkeypatch, *, markets, unresolved=False, stop=None, backup_fault=False):
    tape = rr1_events("normal")
    definition = tape[0]["definition"]
    epoch = int(definition["market"]["slug"].rsplit("-", 1)[1])
    base_wall = tape[0]["available_wall"]
    clock = Clock(0, epoch)
    created = []

    def shifted(index):
        offset = index * 300
        slug = f"btc-updown-5m-{epoch + offset}"
        condition = "0x" + format(index + 1, "064x")
        rows = json.loads(json.dumps(tape).replace(definition["market"]["slug"], slug)
                          .replace(definition["market"]["condition_id"], condition))

        def change(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.endswith("_mono") and isinstance(item, (int, float)):
                        value[key] += offset
                    elif key.endswith("_wall") and isinstance(item, (int, float)):
                        value[key] += offset + epoch - base_wall
                    elif key == "timestamp":
                        value[key] = str(float(item) + (offset + epoch - base_wall) * 1000)
                    elif key == "official_clock":
                        value[key] = [item[0] + offset + epoch - base_wall,
                                      item[1] + offset, item[2] + offset]
                    elif key == "pending":
                        value[key] = [t + offset for t in item]
                    else:
                        change(item)
            elif isinstance(value, list):
                for item in value:
                    change(item)
        change(rows)
        return rows

    class API:
        async def clock(self):
            return [epoch + clock.mono, clock.mono, clock.mono]

        async def market(self, slug):
            index = (int(slug.rsplit("-", 1)[1]) - epoch) // 300
            return market_meta(shifted(index)[0]["definition"]["market"])

        async def ticks(self, tokens):
            return definition["ticks"]

        async def raw_market(self, slug, **kwargs):
            index = (int(slug.rsplit("-", 1)[1]) - epoch) // 300
            raw = next(r["raw"] for r in shifted(index) if r["kind"] == "SETTLEMENT")
            if unresolved:
                raw.update(closed=False, resolutionStatus="pending", outcomePrices=[".5", ".5"])
            return raw

        async def close(self):
            pass

    class Feed:
        def __init__(self, meta, queue):
            self.stop_event = asyncio.Event()
            self.m1_pending_received, self.failure = [], None

        async def run(self):
            await asyncio.Event().wait()

    monkeypatch.setattr(runner, "VirtualPublicAPI", API)
    monkeypatch.setattr(runner, "CaptureFeed", Feed)
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=clock.monotonic, time=clock.time))
    monkeypatch.setattr(runner, "OUTPUT", tmp_path)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *a: None)
    cfg = deepcopy(definition["settings"])
    cfg.update(continuous=True, max_markets=markets, max_seconds=max(900, markets * 300 + 300),
               max_pending_markets=2, max_output_mib=20480, min_free_disk_mib=512,
               max_rss_mib=512, input_queue_frames=2048, writer_queue_records=8192,
               markout_seconds=[1, 5, 30])
    run = runner.LiveRun(tmp_path / "run", cfg)
    original_refresh = run.refresh
    original_record = run.record

    async def record(engine, kind, **payload):
        if kind == "INIT":
            payload["definition"]["artificial_fixture"] = "ACCELERATED_CONTINUOUS_COORDINATOR"
        return await original_record(engine, kind, **payload)

    async def refresh(engine=None):
        run.clock = await run.api.clock()
        await original_refresh(None)
        if engine is None:
            return
        index = (engine.epoch - epoch) // 300
        assert index not in created
        created.append(index)
        rows = shifted(index)
        # First/last windows exercise native submissions/fills. Middle windows
        # exercise real engine/recorder/database churn with a minimal public tape.
        events = rows[1:] if index in {0, markets - 1} else rows[1:4]
        for row in events:
            if row["kind"] in {"END", "SETTLEMENT"}:
                continue
            clock.set(row["available_mono"], row["available_wall"])
            payload = {k: v for k, v in row.items() if k not in
                       {"schema", "seq", "kind", "available_mono", "available_wall", "generation"}}
            await run.record(engine, row["kind"], **payload)
        clock.set((index + 1) * 300, epoch + (index + 1) * 300)
        await run.record(engine, "RESOURCE")
        if backup_fault:
            def fail(*a, **k):
                raise OSError("ARTIFICIAL_RETIRE_BACKUP_FAILURE")
            engine.store.backup = fail
        if stop == "manual":
            run.stop.set()
        elif stop == "duration":
            run.stop_at = clock.mono
        await asyncio.sleep(0)

    run.record, run.refresh = record, refresh
    return run


async def test_288_windows_release_resources_and_replay(tmp_path, monkeypatch):
    run = coordinator(tmp_path, monkeypatch, markets=288)
    result = await run.run()
    assert result["status"] == "PASS" and result["complete_windows"] == 288
    assert not result["deferred_settlements"]
    assert len(run.retired_sizes) >= 286
    assert all(e.store.closed for e in run.engines)
    assert all(not r.thread.is_alive() for r in run.recorders.values())
    assert max(s["open_databases"] for s in run.resources) <= 3
    assert max(s["active_recorders"] for s in run.resources) <= 3
    assert inspect_run(run.directory)["status"] == "PASS"
    assert verifier()(run.directory)["status"] == "PASS"
    for engine in (run.engines[0], run.engines[-1]):
        online = read_state(engine.store.path)
        out = tmp_path / ("replay-" + engine.meta.slug)
        replayed = await runner.replay(engine.directory / "input.jsonl.gz", out)
        assert replayed["state_hash"] == digest(online)
        assert len(online["brokers"]["trades-only"]["accounts"]) == 5


async def test_unresolved_bound_preserves_pending_and_continuation(tmp_path, monkeypatch):
    run = coordinator(tmp_path, monkeypatch, markets=16, unresolved=True)
    result = await run.run()
    assert result["status"] == "PASS" and result["complete_windows"] == 16
    assert len(result["deferred_settlements"]) == 14 and len(run.pending) == 2
    assert max(s["open_databases"] for s in run.resources) <= 3
    report = json.loads((run.directory / "latest.json").read_text())
    assert all(a["gross_pnl_usdc"] is None for m in report["markets"] for a in m["accounts"])
    first = run.engines[0]
    state = read_state(first.store.path)
    assert state["resolution"] is None
    out = tmp_path / "pending-replay"
    replayed = await runner.replay(first.directory / "input.jsonl.gz", out)
    assert replayed["state_hash"] == digest(state)
    assert inspect_run(run.directory)["status"] == "PASS"


@pytest.mark.parametrize("stop,reason", [("manual", "MANUAL_STOP"), ("duration", "MAX_DURATION")])
async def test_continuous_early_stop_remains_failure(tmp_path, monkeypatch, stop, reason):
    run = coordinator(tmp_path, monkeypatch, markets=3, stop=stop)
    result = await run.run()
    assert result["status"] == "FAIL" and result["reason"] == reason
    assert inspect_run(run.directory)["status"] == "FAIL"
    assert all(e.store.closed for e in run.engines)
    assert all(not r.thread.is_alive() for r in run.recorders.values())


async def test_retirement_failure_cannot_be_hidden_by_finalization(tmp_path, monkeypatch):
    run = coordinator(tmp_path, monkeypatch, markets=4, unresolved=True, backup_fault=True)
    with pytest.raises(OSError, match="RETIRE_BACKUP"):
        await run.run()
    assert run.terminal["status"] == "FAIL"
    assert inspect_run(run.directory)["status"] == "FAIL"
    assert all(e.store.closed for e in run.engines)
    assert all(not r.thread.is_alive() for r in run.recorders.values())
