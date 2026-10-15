"""Recover complete reads, never erase observed exposure or bypass close proofs."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from py_clob_client_v2.clob_types import TradeParams
from py_clob_client_v2.exceptions import PolyApiException

from polymaker.execution.gateway import GatewayReadError
from polymaker.rdp_sm import _Issue
from polymaker.rdp_sm_entry import AccountRiskPolicy, run_current_market
from polymaker.rdp_sm_execution import _ExecutionReads
from test_rdp_sm_entry import boundary, old_trade
from test_rdp_sm_m1 import CID, START
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m2_h01_commitment import env as execution_env
from test_rdp_sm_m2_h01_pagination import page
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_recovery import Observer
from test_rdp_sm_recovery import env as recovery_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from recovery_env.__wrapped__(monkeypatch, tmp_path)


@pytest.fixture
def exec_env(monkeypatch, tmp_path):
    yield from execution_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("path", ["/data/trades", "/data/orders"])
@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "cursor_cycle", "missing_cursor"])
async def test_full_session_page_failure_recovers_only_after_complete_reads(
    env, monkeypatch, path, persistent, failure
):
    v, cfg = env
    v.time = START + 60
    pages = []

    def fills(venue, index):
        venue.fill(current_orders(venue)["NO" if index == 1 else "YES"], f"fill-{index}", 5)

    on_windows(v, fills, stop_after=2)

    def fault(request):
        if request.url.path != path or not v.trades:
            return None
        if request.url.params.get("next_cursor") == "next":
            if failure == "cursor_cycle":
                return page(request, [], "next")
            if failure == "missing_cursor":
                return httpx.Response(200, request=request, json={"data": []})
            raise httpx.ReadTimeout("offline page two unavailable")
        if v.executor.safe_to_continue and (not pages or persistent):
            pages.append(True)
            rows = v.trades if path.endswith("trades") else []
            return page(request, rows, "next")
        if persistent and pages:
            return page(request, v.trades if path.endswith("trades") else [], "next")
        return None

    observer = Observer()
    with boundary(monkeypatch, v, fault):
        result = await run_current_market(
            cfg, sleep=v.sleep, monotonic=lambda: v.mono, observer=observer)
    assert pages and result.stop_elapsed_s == 300
    assert any(e["status"] == "PAUSED" for e in observer.events)
    if persistent:
        assert result.execution_status == "INCOMPLETE"
        assert result.confirmed_no == 5 and result.confirmed_fills == 1
        assert len(v.posts) == 2 and result.final_close_barrier == "BLOCKED"
        assert v.executor.gateway._client.pagination_failure is not None
    else:
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
        assert result.confirmed_fills == 2 and result.merged_pairs == 5
        assert len(v.posts) == 3 and result.delta == 0
        assert v.executor.gateway._client.pagination_failure is None
        assert any(e["status"] == "RESUMED" for e in observer.events)


async def test_failures_are_isolated_by_collection_and_query_scope(exec_env, monkeypatch):
    v, executor = await open_executor(exec_env)
    original, failing = v.response, True

    def fault(request):
        if failing and request.url.path in {"/data/trades", "/data/orders"}:
            raise httpx.ReadTimeout("offline scoped read failure")
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(fault)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        for market in [CID, "other"]:
            with pytest.raises(GatewayReadError):
                await executor.gateway.get_trades(market=market)
        with pytest.raises(GatewayReadError):
            await executor.gateway.open_orders()
        failing = False
        await executor.gateway.get_trades(market=CID)
        assert executor.gateway._client.pagination_failure is not None
        await executor.gateway.open_orders()
        assert executor.gateway._client.pagination_failure == "TRADES_PAGINATION_INCOMPLETE"
        await executor.gateway.get_trades(market="other")
        assert executor.gateway._client.pagination_failure is None
        assert not v.posts


async def test_first_page_or_cursor_suffix_cannot_clear_failed_full_read(exec_env, monkeypatch):
    v, executor = await open_executor(exec_env)
    sdk = executor.gateway._client
    original, failing = v.response, True

    def fault(request):
        if failing and request.url.path == "/data/trades":
            raise httpx.ReadTimeout("offline full read unavailable")
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(fault)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        with pytest.raises(PolyApiException):
            sdk.get_trades(TradeParams(market=CID))
        failing = False
        sdk.get_trades(TradeParams(market=CID), only_first_page=True)
        sdk.get_trades(TradeParams(market=CID), next_cursor="suffix")
        assert sdk.pagination_failure == "TRADES_PAGINATION_INCOMPLETE"
        sdk.get_trades(TradeParams(market=CID))
        assert sdk.pagination_failure is None


@pytest.mark.parametrize("recovery", ["snapshot", "close"])
async def test_empty_owned_set_can_recover_orders_before_next_snapshot(exec_env, monkeypatch, recovery):
    v, executor = await open_executor(exec_env)
    original, failed = v.response, []

    def fault(request):
        if request.url.path == "/data/orders" and not failed:
            failed.append(True)
            raise httpx.ReadTimeout("offline initial open orders read")
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(fault)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        with pytest.raises(GatewayReadError):
            await executor._snapshot()
        if recovery == "snapshot":
            await executor._snapshot()
        else:
            assert await executor.verify_close()
            assert executor.safe_to_continue
        result = await executor.execute_window("w1", [quotes()[0]])
    assert result.close_barrier == "SAFE_TO_CONTINUE" and len(v.posts) == 1


async def test_read_success_cannot_revalidate_an_old_close_proof(exec_env, monkeypatch):
    v, executor = await open_executor(exec_env)
    result = await executor.execute_window("w1", [quotes()[0]])
    assert result.close_barrier == "SAFE_TO_CONTINUE" and executor.safe_to_continue
    original, failing = v.response, True

    def fault(request):
        if failing and request.url.path == "/data/trades":
            raise httpx.ReadTimeout("offline invalidates old proof")
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(fault)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        with pytest.raises(GatewayReadError):
            await executor.gateway.get_trades(market=CID)
        assert not executor.safe_to_continue
        failing = False
        await executor.gateway.get_trades(market=CID)
        assert executor.gateway._client.pagination_failure is None
        assert not executor.safe_to_continue
        assert await executor.verify_close()
        assert executor.safe_to_continue
    assert len(v.posts) == 1


async def test_reader_handoff_retains_each_failed_scope_and_observed_evidence(exec_env, monkeypatch):
    v, cfg = exec_env
    _, executor = await open_executor(exec_env)
    original, failing = v.response, True

    def fault(request):
        if failing and request.url.path in {"/data/trades", "/data/orders"}:
            raise httpx.ReadTimeout("offline before reader handoff")
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(fault)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        with pytest.raises(GatewayReadError):
            await executor.gateway.get_trades(market=CID)
        with pytest.raises(GatewayReadError):
            await executor.gateway.open_orders()
        previous = executor.gateway._client
        replacement = _ExecutionReads(cfg, risk_reads=executor.reads)
        try:
            await replacement.connect_account()
            sdk = replacement.gateway._client
            assert sdk._pagination_failures == previous._pagination_failures
            assert sdk.pagination_revision == previous.pagination_revision
            assert sdk.observed_open_rows == previous.observed_open_rows
            failing = False
            await replacement.gateway.get_trades(market=CID)
            assert sdk.pagination_failure == "OPEN_ORDERS_PAGINATION_INCOMPLETE"
            await replacement.gateway.open_orders()
            assert sdk.pagination_failure is None
            assert previous.pagination_failure is not None
        finally:
            replacement.close()


@pytest.mark.parametrize("terminal", ["CONFIRMED", "FAILED"])
async def test_pending_terminal_runs_account_admission_again_before_any_buy(env, monkeypatch, terminal):
    v, cfg = env
    v.time = START + 60
    v.trades = [old_trade(v, current=True)]
    on_windows(v, lambda *_: None, stop_after=1)
    post_times = []

    def transition(request):
        if request.method == "POST":
            post_times.append(v.time - START)
        if request.url.path == "/data/trades" and v.time >= START + 70:
            v.trades[0]["status"] = terminal
            if terminal == "CONFIRMED":
                v.positions = [{"token_id": "1", "current_size": 2, "avg_price": .5}]
        return None

    with boundary(monkeypatch, v, transition):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    if terminal == "FAILED":
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert len(v.posts) == 2 and post_times[0] >= 70
    else:
        assert result.execution_status == "BLOCKED" and not v.posts
        assert result.total_windows == 0


@pytest.mark.parametrize("later", ["omitted", "FAILED", "CONFIRMED"])
async def test_unknown_pending_on_partial_page_is_retained_until_proven_terminal(
    env, monkeypatch, later
):
    v, cfg = env
    v.time = START + 60
    v.trades = [old_trade(v, current=True)]
    prefix, failed, posts = [], [], []
    on_windows(v, lambda *_: None, stop_after=1)

    def fault(request):
        if request.method == "POST":
            posts.append(v.time)
        if request.url.path != "/data/trades":
            return None
        if not prefix:
            prefix.append(True)
            return page(request, v.trades, "next")
        if request.url.params.get("next_cursor") == "next":
            failed.append(True)
            if later == "omitted":
                v.trades = []
            else:
                v.trades[0]["status"] = later
                if later == "CONFIRMED":
                    v.positions = [{"token_id": "1", "current_size": 2, "avg_price": .5}]
            raise httpx.ReadTimeout("offline after unknown pending prefix")
        return None

    with boundary(monkeypatch, v, fault):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert prefix and failed
    if later == "FAILED":
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert len(posts) == 1 and len(v.posts) == 2
    else:
        assert result.execution_status == "BLOCKED" and not posts and not v.posts
        assert result.total_windows == 0


@pytest.mark.parametrize("terminal", ["CONFIRMED", "FAILED"])
async def test_current_market_pending_progress_and_terminal_evidence_can_clear(env, terminal):
    v, _ = env
    policy = AccountRiskPolicy()
    row = old_trade(v, current=True)

    async def trades(**kwargs):
        assert kwargs == {"market": CID}
        return deepcopy(rows)

    rows = [row]
    reads = SimpleNamespace(gateway=SimpleNamespace(
        _client=SimpleNamespace(known_ids=set()), funder=v.funder, get_trades=trades))
    meta = SimpleNamespace(condition_id=CID)
    for status in ["MATCHED", "MINED", "RETRYING"]:
        row["status"] = status
        row["timestamp"] = str(START + len(status))
        with pytest.raises(_Issue, match="CURRENT_MARKET_PENDING_EXPOSURE"):
            await policy.inspect(reads, meta, [], [])
    rows = []
    with pytest.raises(_Issue, match="CURRENT_MARKET_PENDING_EXPOSURE"):
        await policy.inspect(reads, meta, [], [])
    rows = [{**row, "status": terminal}]
    with pytest.raises(_Issue, match="CURRENT_MARKET_PENDING_EXPOSURE"):
        await policy.inspect(reads, meta, [], [])
    await policy.inspect(reads, meta, [], [])
    assert policy.pending == {}


@pytest.mark.parametrize("change", ["price", "matched_amount", "maker_address"])
async def test_pending_terminal_changed_economics_remains_blocked(env, change):
    v, _ = env
    policy = AccountRiskPolicy()
    row = old_trade(v, current=True)

    async def trades(**kwargs):
        return deepcopy([row])

    reads = SimpleNamespace(gateway=SimpleNamespace(
        _client=SimpleNamespace(known_ids=set()), funder=v.funder, get_trades=trades))
    meta = SimpleNamespace(condition_id=CID)
    with pytest.raises(_Issue, match="CURRENT_MARKET_PENDING_EXPOSURE"):
        await policy.inspect(reads, meta, [], [])
    row["status"] = "FAILED"
    row["maker_orders"][0][change] = "0.6" if change != "maker_address" else "different"
    with pytest.raises(_Issue, match="PENDING_TRADE_EVIDENCE_CHANGED"):
        await policy.inspect(reads, meta, [], [])
    assert policy.pending
