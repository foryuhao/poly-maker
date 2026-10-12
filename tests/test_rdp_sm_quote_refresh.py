"""A quote invalidated before POST is skipped only after a fresh safe close."""

import asyncio
from copy import deepcopy

import httpx
import pytest

from polymaker.userstream.parse import normalize_order
from test_rdp_sm_entry import boundary
from test_rdp_sm_m3_loop import current_orders, on_windows, runner
from test_rdp_sm_m3_loop import env as loop_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("change", ["cross", "tick", "unsafe_close"])
async def test_rebalance_refreshes_moved_quote_without_bypassing_close(env, monkeypatch, change):
    v, _ = env
    if change == "tick":
        v.books["2"]["bids"][0]["price"] = ".479"
    reads = [0]
    moved = [False]
    loop = asyncio.get_running_loop()

    def fills(venue, index):
        if index == 1:
            venue.fill(current_orders(venue)["YES"], "initial-yes", 5)
        elif index == 2:
            venue.fill(current_orders(venue)["NO"], "repair-no", 5)

    on_windows(v, fills, stop_after=3)

    def mutate(request):
        if (change == "tick" and request.url.path == "/tick-size"
                and request.url.params["token_id"] == "2"
                and v.executor is not None and len(v.executor.attempts) == 2
                and reads[0] == 1):
            # Tick is now fetched immediately before its book. Move both venue
            # facts at this boundary, rather than contradicting the tick read.
            v.ticks["2"] = v.books["2"]["tick_size"] = ".01"
            v.books["2"]["bids"][0]["price"] = ".48"
        if (
            request.url.path == "/book"
            and request.url.params["token_id"] == "2"
            and v.executor is not None
            and len(v.executor.attempts) == 2
        ):
            reads[0] += 1
            # First book is the M3 decision; second is M2 pre-submit validation.
            if reads[0] == 2:
                moved[0] = True
                if change == "unsafe_close":
                    # A terminal contradiction delivered during final book reads
                    # invalidates the prior close proof before any new placement.
                    v.rows["o-1"]["status"] = "LIVE"
                    loop.call_soon_threadsafe(v.executor.processor.on_order,
                                             normalize_order(v.rows["o-1"]), v.rows["o-1"]["market"])
                if change == "tick":
                    v.ticks["2"] = v.books["2"]["tick_size"] = ".01"
                    v.books["2"]["bids"][0]["price"] = ".48"
                else:
                    v.books["2"]["bids"][0]["price"] = ".46"
                    v.books["2"]["asks"][0]["price"] = ".47"
        if change == "unsafe_close" and moved[0] and request.url.path == "/data/order/o-1":
            row = deepcopy(v.rows["o-1"])
            row["status"] = "LIVE"
            return httpx.Response(200, json=row, request=request)
        return None

    with boundary(monkeypatch, v, mutate):
        result = await runner(env).run(offline=True)
    assert moved[0]
    refreshed = result.windows[1]["result"]
    if change == "unsafe_close":
        assert refreshed["placement_results"] == []
        assert refreshed["close_barrier"] == "BLOCKED"
        assert result.execution_status == "INCOMPLETE"
        assert len(v.post_requests) == 1 and result.confirmed_no == 0
        return
    assert refreshed["close_barrier"] == "SAFE_TO_CONTINUE"
    assert len(refreshed["placement_results"]) == 1
    assert result.skipped_quotes == []
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    repair = refreshed["requested_quotes"]
    assert len(repair) == 1 and repair[0]["outcome"] == "NO" and repair[0]["size"] == 5
    assert repair[0]["price"] == (0.48 if change == "tick" else 0.46)
    assert [len(request) for request in v.post_requests] == [2, 1, 2]
    assert result.confirmed_yes == result.confirmed_no == 5
    assert result.blocked_windows == 0 and result.final_close_barrier == "SAFE_TO_CONTINUE"
