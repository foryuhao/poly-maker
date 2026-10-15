"""Single-command composition: fake HTTP/processes only; no real trading/WS/RPC."""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_loop import RunResult
from polymaker.risk_shadow import ExecutionJournalReader, observe
from polymaker.risk_shadow_launch import launch_shadow
from test_rdp_sm_entry import env as entry_env
from test_rdp_sm_m1 import CID, SLUG, START
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_risk_shadow import MARKET, MemoryWriter, ShadowModel, market_with_inventory, snap


@pytest.fixture(autouse=True)
def isolated_logging():
    root = logging.getLogger()
    handlers, level, config = root.handlers[:], root.level, structlog.get_config().copy()
    try:
        yield
    finally:
        root.handlers[:], root.level = handlers, level
        structlog.configure(**config)


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from entry_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_switch_defaults_off_and_enables_composition(monkeypatch, enabled):
    calls = []
    cfg = SimpleNamespace(
        secrets=SimpleNamespace(pk="", builder_key="", builder_secret="", builder_passphrase="")
    )
    monkeypatch.setattr("polymaker.cli.Config.load", lambda _: cfg)

    async def run(config, **kwargs):
        assert config is cfg
        calls.append(kwargs)
        return RunResult(CID, "session", execution_status="COMPLETED")

    monkeypatch.setattr("polymaker.rdp_sm_entry.run_current_market", run)
    result = CliRunner().invoke(
        app, ["rdp-sm-run", "--json-only", *(["--shadow"] if enabled else [])]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["session_id"] == "session"
    assert calls == [{"observer": None, **({"shadow_mode": True} if enabled else {})}]


@pytest.mark.parametrize("failure", [False, True])
def test_launch_detached_process_has_only_public_identity_no_credentials(
    tmp_path, monkeypatch, caplog, failure
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PK", "dummy-private-key")
    monkeypatch.setenv("API_SECRET", "dummy-api-secret")
    cfg = SimpleNamespace(
        paths=SimpleNamespace(journal_dir="journal"),
        secrets=SimpleNamespace(browser_address="0xABC", pk="unused-key"),
    )
    meta = SimpleNamespace(
        slug=MARKET.slug,
        condition_id=MARKET.condition_id,
        yes=SimpleNamespace(token_id="1"),
        no=SimpleNamespace(token_id="2"),
    )
    calls = []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        if failure:
            raise OSError("mock spawn failure")
        return SimpleNamespace(pid=123)

    monkeypatch.setattr("polymaker.risk_shadow_launch.subprocess.Popen", popen)
    worker = launch_shadow(cfg, meta, "session")
    worker.join(timeout=2)
    assert not worker.is_alive()
    argv, kwargs = calls[0]
    assert argv[argv.index("--session-id") + 1] == "session"
    assert argv[argv.index("--slug") + 1] == MARKET.slug
    assert argv[argv.index("--expected-condition-id") + 1] == MARKET.condition_id
    assert argv[argv.index("--journal") + 1] == str(tmp_path / "journal/rdp_sm_0xabc_session.jsonl")
    assert "--journal-from-start" in argv
    assert kwargs["start_new_session"] is True and kwargs["close_fds"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert not set(kwargs["env"]) - {"PATH", "LANG"}
    assert "dummy-private-key" not in str(calls) and "dummy-api-secret" not in str(calls)
    assert ("SHADOW_ERROR" if failure else "Risk Shadow STARTED") in caplog.text


@pytest.mark.parametrize("mode", ["off", "on", "launch_error"])
async def test_production_orders_cancels_fill_window_identical_with_shadow(env, monkeypatch, mode):
    venue, cfg = env
    venue.time = START + 60
    launches = []

    def launch(config, meta, session):
        launches.append((config, meta, session))
        if mode == "launch_error":
            raise OSError("mock launch failure")

    monkeypatch.setattr("polymaker.risk_shadow_launch.launch_shadow", launch)

    def fills(v, index):
        ids = current_orders(v)
        if index == 1:
            v.fill(ids["YES"], "first", 5)
        elif index == 2:
            v.fill(ids["NO"], "repair", 5)

    on_windows(venue, fills, stop_after=3)
    result = await run_current_market(
        cfg, sleep=venue.sleep, monotonic=lambda: venue.mono, shadow_mode=mode != "off"
    )
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    qs = [a.quote for a in venue.executor.attempts]
    assert [(q.outcome, q.price, q.size) for q in qs] == [
        ("YES", 0.48, 5),
        ("NO", 0.479, 5),
        ("NO", 0.48, 5),
        ("YES", 0.48, 5),
        ("NO", 0.479, 5),
    ]
    assert venue.post_times == [60, 60, 63, 66, 66]
    assert venue.cancels == [
        ["o-1", "o-2"],
        ["o-1", "o-2", "o-3"],
        ["o-1", "o-2", "o-3", "o-4", "o-5"],
        ["o-1", "o-2", "o-3", "o-4", "o-5"],
    ]
    assert result.confirmed_yes == result.confirmed_no == 0
    assert result.merged_pairs == 5
    if mode != "off":
        assert launches[0][1].slug == SLUG and launches[0][1].condition_id == result.market_id
        assert launches[0][2] == result.session_id
    else:
        assert not launches


def test_follower_waits_for_journal_and_catches_startup_rows_without_cross_session(
    tmp_path, monkeypatch
):
    model = ShadowModel(MARKET, "session")
    writer = MemoryWriter()
    path = tmp_path / "pending.jsonl"
    reader = ExecutionJournalReader(path, model, writer, deque(), from_start=True)
    reader.poll(360)
    assert model.inventory.validity == "UNKNOWN"
    rows = [
        {
            "ts": 359,
            "kind": "sm_window_result",
            "data": {
                "session_id": session,
                "payload": {
                    "market_id": MARKET.condition_id,
                    "yes_inventory": qty,
                    "no_inventory": 0,
                    "close_barrier": "SAFE_TO_CONTINUE",
                },
            },
        }
        for session, qty in [("other", 99), ("session", 5)]
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr("polymaker.risk_shadow.time.time", lambda: 360)
    reader.poll(360)
    assert model.inventory.delta == 5
    assert len(writer.rows) == 1
    reader.poll(360)
    assert len(writer.rows) == 1


def test_immediate_rebalance_uses_prior_authoritative_inventory_not_future(tmp_path):
    model = market_with_inventory(60, 0.595)
    model.inventory_update()  # preceding market slot cannot know the latest fill
    history = deque([snap(model, 60)])
    assert history[0][1]["delta"] == "UNKNOWN"
    path = tmp_path / "journal"
    path.write_text("")
    writer = MemoryWriter()
    reader = ExecutionJournalReader(path, model, writer, history)

    def accept(kind, payload, stamp):
        reader.accept(
            {"ts": stamp, "kind": kind, "data": {"session_id": "session", "payload": payload}},
            360.5,
        )

    accept(
        "sm_window_result",
        {
            "market_id": MARKET.condition_id,
            "yes_inventory": 5,
            "no_inventory": 0,
            "close_barrier": "SAFE_TO_CONTINUE",
        },
        360.1,
    )
    accept(
        "sm_strategy_window",
        {
            "state": "REBALANCE",
            "delta": "5",
            "window_id": "session:w2",
            "quotes": [
                {
                    "market_id": MARKET.condition_id,
                    "token_id": "2",
                    "side": "BUY",
                    "outcome": "NO",
                    "price": 0.4,
                    "size": 5,
                }
            ],
        },
        360.2,
    )
    comparison = [r for name, r in writer.rows if name == "decision_comparison"][-1]
    risk = [r for name, r in writer.rows if name == "risk_snapshots"][-1]
    assert comparison["delta"] == 5 and comparison["shadow_quote"] == pytest.approx(0.38)
    assert comparison["inventory_source_timestamp"] == 360.1
    assert risk["risk_snapshot_id"] == comparison["risk_snapshot_id"]
    assert risk["r_settle"] == pytest.approx(25 * 0.595 * 0.405)
    assert history[0][1]["delta"] == "UNKNOWN"  # old snapshots remain immutable
    accept(
        "sm_window_result",
        {
            "market_id": MARKET.condition_id,
            "yes_inventory": 10,
            "no_inventory": 0,
            "close_barrier": "SAFE_TO_CONTINUE",
        },
        360.3,
    )
    assert risk["delta"] == 5


@pytest.mark.asyncio
async def test_child_rejects_wrong_parent_market_before_writer_or_ws(tmp_path, monkeypatch):
    raw = {
        "slug": MARKET.slug,
        "conditionId": MARKET.condition_id,
        "eventStartTime": "1970-01-01T00:05:00Z",
        "endDate": "1970-01-01T00:10:00Z",
        "clobTokenIds": '["1","2"]',
        "outcomes": '["Up","Down"]',
        "orderPriceMinTickSize": 0.01,
        "orderMinSize": 5,
    }

    class Gamma:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def market_by_slug(self, slug):
            return raw

    monkeypatch.setattr("polymaker.risk_shadow.GammaClient", Gamma)
    with pytest.raises(ValueError, match="differs"):
        await observe(
            MARKET.slug, tmp_path / "absent", enabled=True, expected_identity=("wrong", "1", "2")
        )
    assert not (tmp_path / "absent").exists()


def test_execution_strategy_and_entry_control_flow_preserved():
    root = Path(__file__).resolve().parents[1]

    def tree(source):
        return ast.parse(source)

    path = "src/polymaker/rdp_sm_entry.py"
    base = tree(subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=root, text=True))
    current = tree((root / path).read_text())
    before = next(
        n
        for n in base.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_current_market"
    )
    after = next(
        n
        for n in current.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_current_market"
    )
    after.args.kwonlyargs = [a for a in after.args.kwonlyargs if a.arg != "shadow_mode"]
    after.args.kw_defaults = after.args.kw_defaults[:-1]
    main_try = next(n for n in after.body if isinstance(n, ast.Try))
    main_try.body = [
        n
        for n in main_try.body
        if not (
            isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "shadow_mode"
        )
    ]
    assert ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False)
    assert "--shadow" in CliRunner().invoke(app, ["rdp-sm-run", "--help"]).output
    assert os.path.exists(root / "src/polymaker/risk_shadow_launch.py")


@pytest.mark.parametrize("tick_time,expected_quote", [(360.1, 0.398), (360.3, "UNKNOWN")])
def test_decision_tick_updated_between_slots_never_reuses_stale_or_future_tick(
    tmp_path, tick_time, expected_quote
):
    from dataclasses import replace

    model = market_with_inventory(60, 0.595)
    history = deque([snap(model, 60)])
    model.quotes["NO"] = replace(model.quotes["NO"], tick=0.001)
    model.tick_updated_at["NO"] = tick_time
    path = tmp_path / "journal"
    path.write_text("")
    writer = MemoryWriter()
    reader = ExecutionJournalReader(path, model, writer, history)
    reader.accept(
        {
            "ts": 360.2,
            "kind": "sm_strategy_window",
            "data": {
                "session_id": "session",
                "payload": {
                    "state": "REBALANCE",
                    "delta": "5",
                    "window_id": "session:w1",
                    "quotes": [
                        {
                            "market_id": MARKET.condition_id,
                            "token_id": "2",
                            "side": "BUY",
                            "outcome": "NO",
                            "price": 0.4,
                            "size": 5,
                        }
                    ],
                },
            },
        },
        360.4,
    )
    comparison = [r for name, r in writer.rows if name == "decision_comparison"][-1]
    if expected_quote == "UNKNOWN":
        assert comparison["shadow_quote"] == "UNKNOWN"
        assert comparison["reason_code"] == "TICK_UNKNOWN"
    else:
        assert comparison["shadow_quote"] == pytest.approx(expected_quote)
        assert comparison["official_tick_size"]["NO"] == 0.001
