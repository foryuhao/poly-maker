"""R01–R15: actual CLI/M1/M3/M2/FL; fake HTTP, clocks and dummy config only."""

import asyncio
import importlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from py_clob_client_v2.http_helpers import helpers
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.rdp_sm_entry import discover_current_market, run_current_market
from test_rdp_sm_m1 import CID, DUMMY_KEY, SLUG, START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_type2_account import type2

OLD_CID = "0x" + "cd" * 32


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def boundary(monkeypatch, v, mutate):
    original = v.response
    # The existing fixture already supplies the async MockTransport; replace
    # both with the same official API fixture without mocking any SM component.
    actual_async_client = importlib.import_module("httpx._client").AsyncClient

    def response(request):
        altered = mutate(request)
        return altered if altered is not None else original(request)

    transport = httpx.MockTransport(response)
    client = httpx.Client(transport=transport)
    monkeypatch.setattr(helpers, "_http_client", client)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: actual_async_client(transport=transport, **kw)
    )
    return client


def old_trade(v, *, current=False, status="MATCHED"):
    return {
        "id": "old-trade",
        "status": status,
        "market": CID if current else OLD_CID,
        "trader_side": "MAKER",
        "match_time": str(START - 1000),
        "timestamp": str(START - 1000),
        "maker_orders": [
            {
                "order_id": "old-order",
                "asset_id": "1" if current else "3",
                "side": "BUY",
                "price": ".5",
                "matched_amount": "2",
                "maker_address": v.funder,
            }
        ],
    }


def history_boundary(monkeypatch, v, *, resolved=True, omit_after=None):
    calls = []

    def mutate(request):
        if request.url.host == "gamma-api.polymarket.com" and "condition_ids" in request.url.params:
            calls.append(dict(request.url.params))
            raw = {
                "conditionId": OLD_CID,
                "closed": resolved,
                "acceptingOrders": not resolved,
                "umaResolutionStatus": "resolved" if resolved else "proposed",
                "endDate": datetime.fromtimestamp(START - 900, UTC).isoformat(),
                "clobTokenIds": '["3", "4"]',
            }
            return httpx.Response(200, json=[raw], request=request)
        if request.url.path == "/data/trades" and omit_after is not None:
            omit_after[0] += 1
            if omit_after[0] > 1:
                return httpx.Response(
                    200, json={"data": [], "next_cursor": "LTE="}, request=request
                )
        return None

    return boundary(monkeypatch, v, mutate), calls


async def test_r01_current_discovery_uses_official_time_and_token_mapping(env):
    v, cfg = env
    meta, start, now, books = await discover_current_market(cfg)
    assert meta.slug == SLUG and meta.condition_id == CID and start == START
    assert meta.yes.token_id == "1" and meta.no.token_id == "2"
    assert now - start == 60 and books["1"]["tick"] == 0.01 and books["2"]["tick"] == 0.001
    assert not v.posts and not v.cancels


@pytest.mark.parametrize("elapsed", [10, 250])
async def test_r02_r04_early_and_late_starts_wait_in_same_market(env, elapsed):
    v, cfg = env
    v.time = START + elapsed
    before = v.mono
    if elapsed < 15:
        on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED"
    if elapsed < 15:
        assert v.post_times == [15, 15] and v.mono - before >= 5
        assert result.stop_reason == "MARKET_CUTOFF"
    else:
        assert result.stop_reason == "MARKET_CUTOFF" and v.mono - before >= 50
        assert not v.posts and not v.cancels
    assert result.stop_elapsed_s == 300
    assert all(params.get("slug", SLUG) == SLUG for _, _, params in v.calls)


async def test_r03_production_path_normal_rebalance_normal(env):
    v, cfg, _ = type2(env)

    def fills(v, index):
        ids = current_orders(v)
        if index == 1:
            v.fill(ids["YES"], "first", 5)
        if index == 2:
            v.fill(ids["NO"], "repair", 5)

    on_windows(v, fills, stop_after=3)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    assert result.confirmed_yes == result.confirmed_no == 5 and result.active_cycles == 2
    assert len(v.posts) == 5 and result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_r05_near_cutoff_no_unsafe_window(env):
    v, cfg = env
    v.time = START + 239
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED" and result.total_windows == 0
    assert result.stop_elapsed_s == 300 and not v.posts


@pytest.mark.parametrize(
    "fault", ["duplicate", "wrong_slug", "duration", "token", "bad_tick", "future_time"]
)
async def test_r06_invalid_or_ambiguous_market_fails_closed(env, monkeypatch, fault):
    v, cfg = env
    if fault == "wrong_slug":
        v.raw["slug"] = "eth-updown-5m-1"
    if fault == "duration":
        v.raw["endDate"] = datetime.fromtimestamp(START + 600, UTC).isoformat()
    if fault == "token":
        v.raw["clobTokenIds"] = '["1", "1"]'
    if fault == "bad_tick":
        v.ticks["1"] = ".02"
    counter = [0]

    def mutate(request):
        if fault == "duplicate" and request.url.host == "gamma-api.polymarket.com":
            return httpx.Response(200, json=[v.raw, v.raw], request=request)
        if fault == "future_time" and request.url.path == "/time":
            counter[0] += 1
            return httpx.Response(
                200, json=START + 60 if counter[0] == 1 else START + 301, request=request
            )
        return None

    with boundary(monkeypatch, v, mutate):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "BLOCKED" and not v.posts and not v.cancels


@pytest.mark.parametrize("mismatch", [False, True])
async def test_r07_type2_production_identity(env, mismatch):
    v, cfg, safe = type2(env, "0x" + "33" * 20 if mismatch else None)
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    if mismatch:
        assert result.execution_status == "BLOCKED" and not v.posts
    else:
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert result.live_authority == "NOT_VERIFIED_FOR_LIVE"
        assert all(
            p[0]["order"]["maker"] == safe and p[0]["order"]["signatureType"] == 2 for p in v.posts
        )


async def test_r08_resolved_unrelated_matched_archived_without_startup_deduction(env, monkeypatch):
    v, cfg, _ = type2(env)
    v.trades = [old_trade(v)]
    on_windows(v, lambda *_: None, stop_after=1)
    with history_boundary(monkeypatch, v, omit_after=[0])[0]:
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.confirmed_fills == 0
    assert v.trades[0]["status"] == "MATCHED"
    records = journal_records(v.executor.journal.path)
    session = next(r["data"]["payload"] for r in records if r["kind"] == "sm_session")
    assert session["historical_pending_cost_reserved_usd"] == 0
    assert session["historical_pending_cost_observed_usd"] == 1
    assert session["capital_basis"] == "OFFICIAL_BALANCE_AT_SESSION_START"
    assert (
        session["historical_matched"]["old-trade"]["decision"] == "HISTORICAL_MATCHED_NONBLOCKING"
    )
    assert session["historical_matched"]["old-trade"]["status"] == "MATCHED"


@pytest.mark.parametrize("current,resolved", [(True, True), (False, False)])
async def test_r09_current_or_unresolved_other_market_pending_blocks(
    env, monkeypatch, current, resolved
):
    v, cfg = env
    v.trades = [old_trade(v, current=current)]
    with history_boundary(monkeypatch, v, resolved=resolved)[0]:
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "BLOCKED" and not v.posts


async def test_r09_pending_appears_after_batch_blocks_next_window(env, monkeypatch):
    v, cfg = env

    def after(v):
        v.trades = [old_trade(v, current=True)]

    v.after_post = after
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert len(v.posts) == 2 and len(v.post_requests) == 1 and result.total_windows == 1
    assert result.execution_status in ("BLOCKED", "INCOMPLETE")
    assert result.stop_reason == "CURRENT_MARKET_PENDING_EXPOSURE"
    assert not any(r["status"] == "LIVE" for r in v.rows.values())


async def test_r10_capital_exhaustion_safe_close_reason_retained(env):
    v, cfg = env

    def fills(v, index):
        for name, oid in current_orders(v).items():
            v.fill(oid, f"{index}-{name}", 5)

    on_windows(v, fills)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.stop_reason == "TEST_CAPITAL_LIMIT_EXCEEDED"
    assert result.execution_status == "BLOCKED" and result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert v.executor.committed() <= 30 and all(r["status"] != "LIVE" for r in v.rows.values())
    assert result.total_windows == 7 and len(v.posts) == 12


def cli_config(monkeypatch, tmp_path, v, cfg):
    # Actual config loading; only the credential source is a public dummy .env.
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text(
        f'[wallet]\nsignature_type = {cfg.wallet.signature_type}\n[paths]\njournal_dir = "{cfg.paths.journal_dir}"\n'
    )
    (tmp_path / ".env").write_text(
        f"PK={DUMMY_KEY}\nBROWSER_ADDRESS={cfg.secrets.browser_address}\n"
    )
    monkeypatch.setenv("PK", DUMMY_KEY)
    monkeypatch.setenv("BROWSER_ADDRESS", cfg.secrets.browser_address)
    monkeypatch.setattr("dotenv.main.find_dotenv", lambda *a, **kw: str(tmp_path / ".env"))
    clock = SimpleNamespace(time=lambda: v.time, monotonic=lambda: v.mono)
    for module in ("polymaker.rdp_sm_entry", "polymaker.rdp_sm_loop", "polymaker.rdp_sm_execution"):
        monkeypatch.setattr(f"{module}.time", clock)
    actual_sleep = asyncio.sleep

    async def sleep(seconds):
        if seconds == 0:
            return await actual_sleep(0)
        return await v.sleep(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return config


def test_r11_r12_full_cli_single_market_and_exit(env, monkeypatch, tmp_path):
    v, cfg, _ = type2(env)
    config = cli_config(monkeypatch, tmp_path, v, cfg)

    def fills(v, index):
        ids = current_orders(v)
        if index == 1:
            v.fill(ids["YES"], "first", 5)
        if index == 2:
            v.fill(ids["NO"], "repair", 5)

    on_windows(v, fills)
    output = CliRunner().invoke(app, ["rdp-sm-run", "--config-dir", str(config)])
    assert output.exit_code == 0, output.output
    result = json.loads(output.stdout)
    assert result["execution_status"] == "COMPLETED" and result["active_cycles"] == 2
    assert result["final_close_barrier"] == "SAFE_TO_CONTINUE" and result["stop_elapsed_s"] == 300
    assert all(15 <= stamp < 240 for stamp in v.post_times)
    assert all(params.get("slug", SLUG) == SLUG for _, _, params in v.calls)
    assert result["confirmed_yes"] == result["confirmed_no"] == 5
    assert v.executor.closed and all(r["status"] != "LIVE" for r in v.rows.values())


def test_r15_help_import_preflight_and_startup_error_never_place(env, monkeypatch, tmp_path):
    v, cfg = env
    config = cli_config(monkeypatch, tmp_path, v, cfg)
    for args in (["--help"], ["rdp-sm-run", "--help"]):
        output = CliRunner().invoke(app, args)
        assert output.exit_code == 0 and not v.calls
    importlib.reload(importlib.import_module("polymaker.rdp_sm_entry"))
    assert not v.calls
    output = CliRunner().invoke(app, ["rdp-sm-preflight", SLUG, "--config-dir", str(config)])
    assert output.exit_code == 0, output.output
    assert not v.posts and not v.cancels
    v.fail_path = "/time"
    output = CliRunner().invoke(app, ["rdp-sm-run", "--config-dir", str(config)])
    assert output.exit_code == 2 and not v.posts and not v.cancels
    count = len(v.calls)
    assert json.loads(output.stdout)["execution_status"] == "BLOCKED"
    assert len(v.calls) == count


async def test_r14_actual_journal_reconstructs_minimal_metrics(env, monkeypatch):
    v, cfg, _ = type2(env)
    clock = SimpleNamespace(time=lambda: v.time, monotonic=lambda: v.mono)
    for module in ("polymaker.rdp_sm_entry", "polymaker.rdp_sm_loop", "polymaker.rdp_sm_execution"):
        monkeypatch.setattr(f"{module}.time", clock)

    def fills(v, index):
        ids = current_orders(v)
        if index == 1:
            v.fill(ids["YES"], "yes", 5)
        elif index == 2:
            v.fill(ids["NO"], "no-partial", 3)
        else:
            v.fill(ids["NO"], "no-repair", 2)

    on_windows(v, fills, stop_after=3)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    rows = journal_records(v.executor.journal.path)

    def payloads(kind):
        return [r["data"]["payload"] for r in rows if r["kind"] == kind]

    session, final = payloads("sm_session")[0], payloads("sm_run_result")[0]
    assert session["starting_spendable_usdc"] == 100 and session["test_capital_limit_usdc"] == "30"
    intents = payloads("sm_placement_intent")
    maximum_committed = max(float(p["capital_committed_usd"]) for p in intents)
    assert (
        maximum_committed <= 30
        and maximum_committed > final["confirmed_yes"] * final["average_yes_cost"]
    )
    assert final["stop_reason"] == "MARKET_CUTOFF"  # exhaustion = NOT_REACHED
    windows = payloads("sm_window_result")
    orders = {
        p["order_id"]: p["quote"]["window_id"]
        for w in windows
        for p in w["placement_results"]
        if p["outcome"] == "ACK"
    }
    fills = {(f["trade_id"], f["maker_order_id"]): f for f in windows[-1]["confirmed_fills"]}
    assert len(orders) == 5 and len(fills) == 3
    active = {orders[f["maker_order_id"]] for f in fills.values()}
    fill_rate = len({f["maker_order_id"] for f in fills.values()}) / len(orders)
    assert len(active) == final["active_cycles"] == 3 and fill_rate == 0.6
    assert final["normal_windows"] == 2 and final["rebalance_windows"] == 1
    cancels = payloads("sm_cancel_ack")
    assert all("request_ts" in p and p["duration_s"] >= 0 for p in cancels)
    acknowledgements = {
        r["data"]["payload"]["order_id"]: r["ts"] for r in rows if r["kind"] == "sm_placement_ack"
    }
    lifetimes = [
        min(p["request_ts"] for p in cancels if oid in p["ids"]) - stamp
        for oid, stamp in acknowledgements.items()
    ]
    assert all(lifetime == pytest.approx(3) for lifetime in lifetimes)
    assert all(w["close_monotonic"] >= w["fill_deadline_monotonic"] for w in windows)
    # Confirmed economics + reliable official fill timestamps reconstruct a
    # single exposure interval across three quote windows. No pending inventory.
    ordered = sorted(fills.values(), key=lambda f: f["ts"])
    assert all(f["ts"] > 0 and f["status"] == "CONFIRMED" for f in ordered)
    yes = no = 0
    maximum_residual = maximum_residual_cost = 0
    intervals, opened = [], None
    for f in ordered:
        if f["token_id"] == "1":
            yes += f["size"]
        else:
            no += f["size"]
        residual = abs(yes - no)
        maximum_residual = max(maximum_residual, residual)
        maximum_residual_cost = max(maximum_residual_cost, residual * f["price"])
        if residual and opened is None:
            opened = f["ts"]
        if not residual and opened is not None:
            intervals.append((opened, f["ts"]))
            opened = None
    assert maximum_residual == 5 and maximum_residual_cost == pytest.approx(2.4)
    assert len(intervals) == 1 and intervals[0][1] - intervals[0][0] == 6
    assert final["residual_shares"] == 0 and final["confirmed_yes"] == final["confirmed_no"] == 5
    assert final["completed_pairs"] == 5 and final["theoretical_pair_pnl"] == pytest.approx(0.202)
    assert (
        final["execution_status"] == "COMPLETED"
        and final["final_close_barrier"] == "SAFE_TO_CONTINUE"
    )


async def test_official_balance_below_actual_pair_cost_denies_submission(env, monkeypatch):
    v, cfg = env
    v.trades = [old_trade(v)]
    v.capital["balance"] = "3000000"  # Official $3 itself cannot cover the ~$4.85 pair.
    with history_boundary(monkeypatch, v, omit_after=[0])[0]:
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert not v.posts and result.execution_status == "BLOCKED"
    assert v.executor is not None and v.executor.closed
    assert v.executor.reads.account_policy.pending["old-trade"]["status"] == "MATCHED"
    assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"


@pytest.mark.parametrize("conflict_at", [2, 4, 7])
async def test_independent_r06_dynamic_ambiguity_never_selects_first_row(
    env, monkeypatch, conflict_at
):
    v, cfg = env
    calls = [0]

    def mutate(request):
        if request.url.host == "gamma-api.polymarket.com" and "slug" in request.url.params:
            calls[0] += 1
            if calls[0] >= conflict_at:
                conflict = {**v.raw, "conditionId": OLD_CID}
                return httpx.Response(200, json=[v.raw, conflict], request=request)
        return None

    with boundary(monkeypatch, v, mutate):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "BLOCKED", result.stop_reason
    assert len(v.posts) == (2 if conflict_at == 7 else 0)
    assert len(v.post_requests) == (1 if conflict_at == 7 else 0)
    assert all(row["status"] != "LIVE" for row in v.rows.values())
    if conflict_at == 7:
        assert result.stop_reason == "MARKET_IDENTITY_AMBIGUOUS"
