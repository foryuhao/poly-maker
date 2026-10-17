"""Original single-market entry with one-way companion hooks; no live I/O."""

import asyncio
import json

import pytest
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.rdp_sm_entry import run_current_market
from test_rdp_sm_entry import cli_config
from test_rdp_sm_entry import env as env
from test_rdp_sm_m1 import CID, SLUG
from test_rdp_sm_m3_loop import on_windows
from test_rdp_sm_type2_account import type2


def install_spy(monkeypatch, *, fault=None):
    instances = []
    class Companion:
        def __init__(self, cfg, session):
            self.session, self.events, self.markets = session, [], []
            self.started = self.closed = False
            instances.append(self)

        def start(self):
            self.started = True
            if fault == "start":
                raise RuntimeError("private exception must not affect trading")

        def bind_market(self, meta, start):
            self.markets.append((meta.slug, meta.condition_id, start))
            if fault == "bind":
                raise RuntimeError("bind failed")

        def capture(self, kind, payload, **kwargs):
            self.events.append((kind, kwargs))
            if fault == "capture":
                raise RuntimeError("capture failed")

        def close(self):
            self.closed = True
            if fault == "close":
                raise RuntimeError("close failed")
            return True

    monkeypatch.setattr("polymaker.rdp_sm_sync_companion.SyncCompanion", Companion)
    return instances


async def test_original_entry_starts_binds_captures_and_closes_one_companion(env, monkeypatch):
    v, cfg, _ = type2(env)
    cfg.rdp_sm.sync_observe.enabled = True
    instances = install_spy(monkeypatch)
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(instances) == 1
    companion = instances[0]
    assert companion.started and companion.closed
    assert companion.session == result.session_id
    assert len(companion.markets) == 1 and companion.markets[0][:2] == (SLUG, CID)
    kinds = {kind for kind, _ in companion.events}
    assert {"sm_placement_intent", "sm_cancel_ack", "sm_close_barrier", "sm_run_result", "entry_result"} <= kinds
    assert all("monotonic" in kwargs for kind, kwargs in companion.events if kind != "entry_result")


@pytest.mark.parametrize("fault", ["start", "bind", "capture", "close"])
async def test_observer_failure_does_not_change_execution_result(env, monkeypatch, fault):
    v, cfg, _ = type2(env)
    cfg.rdp_sm.sync_observe.enabled = True
    instances = install_spy(monkeypatch, fault=fault)
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert instances[0].closed
    assert v.posts and v.cancels  # Original strategy still executed its own path.


async def test_discovery_failure_still_stops_companion(env, monkeypatch):
    _, cfg = env
    cfg.rdp_sm.sync_observe.enabled = True
    instances = install_spy(monkeypatch)
    async def fail(*_args, **_kwargs):
        raise ValueError("invalid discovery")
    monkeypatch.setattr("polymaker.rdp_sm_entry.discover_current_market", fail)
    result = await run_current_market(cfg)
    assert result.execution_status != "COMPLETED"
    assert instances[0].started and instances[0].closed and not instances[0].markets


async def test_main_cancellation_stops_companion_without_starting_another_market(env, monkeypatch):
    _, cfg = env
    cfg.rdp_sm.sync_observe.enabled = True
    instances = install_spy(monkeypatch)
    entered = asyncio.Event()
    async def running(*_args, **_kwargs):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr("polymaker.rdp_sm_loop.SMTradingLoop.run_production", running)
    task = asyncio.create_task(run_current_market(cfg))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(instances) == 1 and instances[0].closed


def test_unchanged_cli_command_uses_enabled_config_without_extra_flags(env, monkeypatch, tmp_path):
    v, cfg, _ = type2(env)
    config = cli_config(monkeypatch, tmp_path, v, cfg)
    with (config / "config.toml").open("a") as stream:
        stream.write('\n[rdp_sm.sync_observe]\nenabled = true\n')
    instances = install_spy(monkeypatch)
    on_windows(v, lambda *_: None, stop_after=1)
    output = CliRunner().invoke(app, ["rdp-sm-run", "--config-dir", str(config)])
    assert output.exit_code == 0, output.output
    result = json.loads(output.stdout)
    assert result["execution_status"] == "COMPLETED"
    assert len(instances) == 1 and instances[0].closed
    assert instances[0].session == result["session_id"]


async def test_disabled_config_preserves_original_entry(env, monkeypatch):
    v, cfg, _ = type2(env)
    assert not cfg.rdp_sm.sync_observe.enabled
    instances = install_spy(monkeypatch)
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED" and not instances
