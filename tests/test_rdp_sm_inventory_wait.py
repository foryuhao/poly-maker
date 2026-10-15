"""Data API discrepancies pause production admission, preserving fills until T=300."""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import InventoryMismatch, RiskJournal
from test_rdp_sm_cli_logging import invoke
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import ADDRESS, START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_merge import paired
from test_rdp_sm_type2_account import type2


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("token", ["1", "2"])
@pytest.mark.parametrize("actual,allowed", [
    (4.98999, False), (4.99, True), (4.995, True), (5, True),
    (5.005, True), (5.01, True), (5.01001, False),
])
async def test_each_side_inclusive_point_zero_one_tolerance_preserves_exact_fills(env, token, actual, allowed):
    v, ex = await paired(env)
    next(p for p in v.positions if p["token_id"] == token)["current_size"] = actual
    records = [asdict(f) for f in ex.confirmed()]
    rpc = list(v.merge_rpc_calls)
    if allowed:
        await ex._snapshot(include_books=False)
        assert ex.inventory_mismatch is None
    else:
        with pytest.raises(InventoryMismatch) as error:
            await ex._snapshot(include_books=False)
        side = "YES" if token == "1" else "NO"
        assert error.value.context["inventory"][side]["actual"] == actual
        assert error.value.context["inventory"][side]["expected"] == 5
    assert [asdict(f) for f in ex.confirmed()] == records
    assert ex.committed() == Decimal("4.85")
    assert ex.safe_to_continue and v.merge_rpc_calls == rpc


def gap_reply(request, *, yes, no=0):
    return httpx.Response(200, request=request, json={
        "data": [{"token_id": "1", "current_size": yes, "avg_price": .49},
                 {"token_id": "2", "current_size": no, "avg_price": .48}],
        "pagination": {"next_cursor": None},
    })


@pytest.mark.parametrize("at_final_admission", [False, True])
async def test_transient_gap_recovers_and_resumes_rebalance_in_same_market(env, monkeypatch, at_final_admission):
    v, cfg = env
    v.time = START + 60
    observations = []
    submissions = []
    after_fill_reads = []
    def fills(venue, index):
        ids = current_orders(venue)
        venue.fill(ids["YES" if index == 1 else "NO"], f"fill-{index}", 5)
    on_windows(v, fills, stop_after=2)

    def delayed_index(request):
        if request.method == "POST":
            submissions.append(v.time - START)
        if request.url.path == "/v2/positions" and v.trades and len(v.posts) == 2:
            after_fill_reads.append(v.time)
            if v.time < START + 90 and (not at_final_admission or len(after_fill_reads) >= 2):
                return gap_reply(request, yes=4.98)
        return None

    class Observer:
        def emit(self, kind, payload):
            if kind == "inventory_check":
                observations.append(deepcopy(payload))

    with boundary(monkeypatch, v, delayed_index):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono,
                                          observer=Observer())
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.stop_elapsed_s >= 300 and result.merged_pairs == 5
    assert submissions[0] == 60 and submissions[1] >= 90 and len(v.posts) == 3
    assert [e["status"] for e in observations][-1] == "RESUMED"
    assert observations[0]["inventory"]["YES"]["difference"] == -.02
    assert observations[-1]["inventory"]["YES"]["difference"] == 0
    assert all(timestamp >= START + 90 for _, timestamp in v.merge_rpc_calls)
    fills = [r["data"]["payload"] for r in journal_records(v.executor.journal.path)
             if r["kind"] == "sm_trade" and r["data"]["payload"]["status"] == "CONFIRMED"]
    assert {f["maker_order_id"] for f in fills} == {"o-1", "o-3"}
    if at_final_admission:
        blocked = result.windows[1]["result"]
        assert blocked["block_reason"] == "ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM"
        assert blocked["placement_results"] == [] and blocked["close_barrier"] == "SAFE_TO_CONTINUE"


def test_persistent_gap_blocks_until_300_closes_orders_and_logs_real_difference(env, monkeypatch, tmp_path):
    v, cfg = env
    type2(env)
    v.time = START + 60
    def fills(venue, index):
        assert index == 1, "another order was submitted during the persistent gap"
        ids = current_orders(venue)
        venue.fill(ids["YES"], "yes", 5)
        venue.fill(ids["NO"], "no", 5)
    on_windows(v, fills)

    def stale_index(request):
        if request.url.path == "/v2/positions" and v.trades:
            return gap_reply(request, yes=4.98, no=5)
        return None

    with boundary(monkeypatch, v, stale_index):
        output = invoke(env, monkeypatch, tmp_path)
    result = json.loads(output.stdout)
    assert output.exit_code == 2 and result["execution_status"] == "BLOCKED"
    assert result["stop_reason"] == "ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM"
    assert result["stop_elapsed_s"] == 300
    assert result["confirmed_yes"] == result["confirmed_no"] == 5
    assert result["confirmed_fills"] == 2 and result["merged_pairs"] == 0
    assert result["final_close_barrier"] == "SAFE_TO_CONTINUE"
    assert len(v.posts) == 2 and not v.merge_submissions and not v.merge_rpc_calls
    assert all(order["status"] == "CANCELED" for order in v.rows.values())
    assert "BLOCKED_WAIT_UNTIL_300" in output.stderr
    assert "Tolerance=0.01 shares" in output.stderr
    assert "YES: Expected=5 Actual=4.98 Difference=-0.02 shares" in output.stderr
    path = Path(v.executor.journal.path)
    original = path.read_bytes()
    journal = RiskJournal(cfg.paths.journal_dir, v.funder)
    journal.close()
    assert path.read_bytes() == original


async def test_zero_order_gap_wait_preserves_safe_historical_session(env, monkeypatch):
    v, cfg = env
    v.time = START + 60
    def unexplained_position(request):
        if request.url.path == "/v2/positions" and v.executor is not None:
            return gap_reply(request, yes=.02)
        return None
    with boundary(monkeypatch, v, unexplained_position):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "BLOCKED" and result.stop_elapsed_s == 300
    assert result.confirmed_fills == 0 and not v.posts and not v.merge_rpc_calls
    path = Path(v.executor.journal.path)
    original = await asyncio.to_thread(path.read_bytes)
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS)
    journal.close()
    assert await asyncio.to_thread(path.read_bytes) == original


async def test_after_merge_stale_index_waits_then_recovers_without_chain_position_reads(env, monkeypatch):
    v, cfg = env
    v.time = START + 60
    rpc_count = []
    def fills(venue, index):
        assert index == 1
        ids = current_orders(venue)
        venue.fill(ids["YES"], "yes", 5)
        venue.fill(ids["NO"], "no", 5)
        venue.time = START + 239
    on_windows(v, fills)

    def lag_after_burn(request):
        if request.url.path == "/v2/positions" and v.chain_merged:
            if not rpc_count:
                rpc_count.append(len(v.merge_rpc_calls))
            assert len(v.merge_rpc_calls) == rpc_count[0]
            if v.time < START + 260:
                return gap_reply(request, yes=5, no=5)
        return None

    with boundary(monkeypatch, v, lag_after_burn):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.stop_elapsed_s == 300 and len(v.posts) == 2
    assert len(v.merge_submissions) == 1 and result.merged_pairs == 5
    assert result.confirmed_yes == result.confirmed_no == 0 and result.confirmed_fills == 2
    assert v.executor.inventory_mismatch is None and rpc_count
