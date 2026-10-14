"""Actual one-market strategy/time/SDK path; fake HTTP and clocks only."""

import pytest

from polymaker.rdp_sm_entry import run_current_market
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import SLUG, START
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def advance(v, elapsed):
    new_time = START + elapsed
    v.mono += new_time - v.time
    v.time = new_time


async def run(env):
    v, cfg = env
    return await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)


@pytest.mark.parametrize("elapsed", [0, 10, 14.999])
async def test_early_start_waits_without_credentials_or_orders_until_15(env, monkeypatch, elapsed):
    v, _ = env
    v.time = START + elapsed
    auth = []
    posts = []
    on_windows(v, lambda *_: None, stop_after=1)
    def observe(request):
        if request.url.path == "/auth/derive-api-key":
            auth.append(v.time - START)
        if request.method == "POST" and request.url.path == "/orders":
            posts.append(v.time - START)
        return None
    with boundary(monkeypatch, v, observe):
        result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert posts == [15] and len(v.post_requests[0]) == 2
    assert auth and min(auth) >= 15
    assert all(params.get("slug", SLUG) == SLUG for _, _, params in v.calls)


@pytest.mark.parametrize("elapsed", [180, 200, 239, 240, 270, 299])
async def test_normal_holds_until_300_without_requiring_quotable_books(env, monkeypatch, elapsed):
    v, _ = env
    v.time = START + elapsed
    late_books = []
    def remove_book(request):
        if v.executor is not None:
            v.books["2"]["asks"] = []
            if request.url.path == "/book":
                late_books.append(True)
        return None
    with boundary(monkeypatch, v, remove_book):
        result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.stop_elapsed_s == 300 and result.total_windows == 0
    assert not v.post_requests and not late_books
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_normal_still_submits_just_before_180(env):
    v, _ = env
    v.time = START + 179.999
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run(env)
    assert result.execution_status == "COMPLETED"
    assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 2
    assert all(15 <= t < 180 for t in v.post_times)


async def test_normal_quote_refresh_crossing_180_skips_without_post(env, monkeypatch):
    v, _ = env
    v.time = START + 179
    books = []
    def cross_phase(request):
        if (request.url.path == "/book" and request.url.params["token_id"] == "1"
                and v.executor is not None):
            books.append(True)
            if len(books) == 2:
                advance(v, 180)
        return None
    with boundary(monkeypatch, v, cross_phase):
        result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert not v.post_requests and not v.cancels
    assert result.skipped_quotes[0]["reason"] == "MARKET_SUBMISSION_CUTOFF"
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


@pytest.mark.parametrize("cutoff", [180, 300])
async def test_sdk_signing_crossing_phase_cutoff_aborts_before_http(env, monkeypatch, cutoff):
    v, executor = await open_executor(env)
    if cutoff == 300:
        def fill(venue):
            venue.fill("o-1", "initial", 5)
        v.on_cancel = fill
        first = await executor.execute_window("w1", quotes())
        assert first.close_barrier == "SAFE_TO_CONTINUE"
        v.on_cancel = None
        v.positions = [{"token_id": "1", "current_size": 5, "avg_price": .49}]
    advance(v, cutoff - .1)
    client = executor.gateway._client
    create = client.create_order
    def sign_then_advance(*args, **kwargs):
        signed = create(*args, **kwargs)
        advance(v, cutoff)
        return signed
    monkeypatch.setattr(client, "create_order", sign_then_advance)
    from dataclasses import replace
    submitted = quotes("w2") if cutoff == 180 else [replace(quotes("w2")[1], price=.48)]
    before = len(v.post_requests)
    result = await executor.execute_window("w2", submitted)
    assert len(v.post_requests) == before
    assert all(p["outcome"] == "ABORTED" and not p["post_started"] for p in result.placement_results)
    assert result.close_barrier == "SAFE_TO_CONTINUE"


@pytest.mark.parametrize("elapsed", [180, 239, 240, 270, 299.9, 300])
async def test_only_short_side_rebalance_continues_to_hard_market_end(env, elapsed):
    v, _ = env
    v.time = START + 170
    def action(venue, index):
        if index == 1:
            venue.fill(current_orders(venue)["YES"], "initial", 5)
            advance(venue, elapsed)
            if elapsed == 300:
                venue.raw["closed"] = True
                venue.raw["acceptingOrders"] = False
        else:
            advance(venue, 300)
    on_windows(v, action)
    result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert result.confirmed_yes == 5 and result.confirmed_no == 0
    if elapsed < 300:
        assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE"]
        assert [len(p) for p in v.post_requests] == [2, 1]
        order = v.post_requests[1][0]
        assert order["order"]["tokenId"] == "2" and order["order"]["side"] == "BUY"
        assert order["postOnly"] is True
        assert v.post_times[-1] == pytest.approx(elapsed)
    else:
        assert [w["state"] for w in result.windows] == ["NORMAL"]
        assert len(v.post_requests) == 1
    assert all(t < 300 for t in v.post_times)
    assert result.stop_elapsed_s >= 300


@pytest.mark.parametrize("initial_side", ["YES", "NO"])
async def test_repaired_inventory_after_240_waits_to_300_without_new_normal_window(env, initial_side):
    v, _ = env
    v.time = START + 175
    def action(venue, index):
        if index == 1:
            venue.fill(current_orders(venue)[initial_side], "initial", 5)
            advance(venue, 241)
        else:
            venue.fill(current_orders(venue)["NO" if initial_side == "YES" else "YES"], "repair", 5)
    on_windows(v, action)
    result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE"]
    assert [len(p) for p in v.post_requests] == [2, 1]
    assert v.post_requests[1][0]["order"]["tokenId"] == ("2" if initial_side == "YES" else "1")
    assert result.confirmed_yes == result.confirmed_no == 0 and result.merged_pairs == 5
    assert result.stop_elapsed_s == 300
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_late_rebalance_still_requires_actual_available_capital(env):
    v, _ = env
    v.time = START + 170
    def action(venue, index):
        if index == 1:
            venue.fill(current_orders(venue)["YES"], "initial", 5)
            venue.capital["balance"] = "900000"
            advance(venue, 250)
    on_windows(v, action)
    result = await run(env)
    assert result.execution_status == "BLOCKED"
    assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert len(v.post_requests) == 1
    assert result.windows[-1]["state"] == "REBALANCE"
    assert result.windows[-1]["result"]["placement_results"] == []
    assert result.confirmed_yes == 5 and result.confirmed_no == 0
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"


async def test_late_unknown_rebalance_stays_blocked_and_retains_commitment(env):
    import httpx
    v, _ = env
    v.time = START + 170
    def action(venue, index):
        if index == 1:
            venue.fill(current_orders(venue)["YES"], "initial", 5)
            advance(venue, 250)
            venue.replies = [httpx.ReadTimeout("offline unknown rebalance")]
    on_windows(v, action)
    result = await run(env)
    assert result.execution_status == "INCOMPLETE"
    assert result.final_close_barrier == "BLOCKED"
    assert len(v.post_requests) == 2
    last = result.windows[-1]["result"]
    assert last["placement_results"][0]["outcome"] == "UNKNOWN"
    assert last["capital_committed"] == pytest.approx(2.4 + 2.4)
