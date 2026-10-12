"""Targeted production-path regressions; only official HTTP/time are faked."""

import asyncio
import threading
import time
from copy import deepcopy

import httpx
import pytest

from polymaker.rdp_sm_execution import SMWindowExecutor
from test_rdp_sm_m2_execution import (
    SLUG,
    START,
    open_executor,
    quotes,
)
from test_rdp_sm_m2_execution import (
    env as execution_env,
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from execution_env.__wrapped__(monkeypatch, tmp_path)


def external_order(price=".9"):
    return {
        "id": "external",
        "asset_id": "3",
        "market": "other",
        "side": "BUY",
        "price": price,
        "original_size": "5",
        "size_matched": "0",
        "status": "LIVE",
        "associate_trades": [],
    }


@pytest.mark.parametrize("funds_key", ["balance", "allowance"])
async def test_h01_insufficient_pair_budget_blocks_both_orders_before_post(env, funds_key):
    v, executor = await open_executor(env)
    if funds_key == "balance":
        v.capital["balance"] = "3000000"
    else:
        v.capital["allowances"] = {k: "3000000" for k in v.capital["allowances"]}
    v.open_rows_override = []  # official replica has not caught up with local ACK
    at_post = []
    v.after_post = lambda venue: at_post.append(executor.committed())
    result = await executor.execute_window("w1", quotes())
    assert not v.posts and not v.post_requests and not at_post
    assert result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert result.close_barrier == "BLOCKED"
    assert result.known_order_ids == [] and not v.cancels
    assert result.capital_committed == 0


@pytest.mark.parametrize("visible_read", [1, 2])
async def test_h01_either_admission_read_reserves_newly_observed_order(
    env, monkeypatch, visible_read
):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    original = v.response
    reads = []

    def response(request):
        if request.url.path == "/data/orders":
            reads.append(len(reads) + 1)
            v.open_rows_override = [external_order()] if len(reads) == visible_read else []
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", [quotes()[0]])
    assert len(reads) == 2  # Account admission plus final open-order listing, before books.
    assert not v.posts and not v.cancels
    assert result.close_barrier == "BLOCKED"
    assert result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"


async def test_h01_official_and_local_identity_reserve_once_with_sufficient_funds(env):
    v, executor = await open_executor(env)
    v.capital["balance"] = "7400000"
    v.rows["external"] = external_order(".49")
    result = await executor.execute_window("w1", quotes())
    # $2.45 external + $2.45 own first + $2.40 second = $7.30. Repeated
    # official observations and local ACK must not double-charge either ID.
    assert len(v.posts) == 2 and result.close_barrier == "SAFE_TO_CONTINUE"
    assert result.capital_committed == 0 and v.rows["external"]["status"] == "LIVE"


async def test_h01_partial_pending_fill_does_not_release_first_order_budget(env):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    v.open_rows_override = []

    def first_fill(venue):
        venue.fill("o-1", "pending", 1, "MATCHED")
        venue.positions = [{"token_id": "1", "current_size": 1, "avg_price": 0.49}]

    v.after_post = first_fill
    result = await executor.execute_window("w1", quotes())
    assert len(v.posts) == 2 and len(v.post_requests) == 1 and v.cancels == [["o-1", "o-2"]]
    assert result.close_barrier == "BLOCKED" and result.yes_inventory == 0
    assert result.capital_committed == 2.45
    await executor.execute_window("w2", quotes("w2"))
    assert len(v.posts) == 2


async def test_h01_conflicting_snapshot_economics_fail_closed(env, monkeypatch):
    v, executor = await open_executor(env)
    original = v.response
    read_number = 0

    def response(request):
        nonlocal read_number
        if request.url.path == "/data/orders":
            read_number += 1
            v.open_rows_override = [external_order(".9" if read_number % 2 else ".8")]
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", quotes())
    assert not v.posts and result.close_barrier == "BLOCKED"
    assert result.block_reason == "OPEN_ORDER_SNAPSHOTS_CONFLICT"


@pytest.mark.parametrize("pages", [1, 6])
async def test_h02_optional_slow_reads_never_precede_deadline_cancellation(env, monkeypatch, pages):
    v, executor = await open_executor(env)
    v.time = START + 170
    original = v.response
    active_reads = []
    events = []

    def response(request):
        path = request.url.path
        if request.method == "DELETE":
            events.append(("CANCEL", v.mono))
        if path == "/data/trades":
            events.append(("TRADES", v.mono))
            if not v.cancels:
                active_reads.append(path)
            cursor = request.url.params.get("next_cursor")
            i = 0 if cursor == "MA==" else int(cursor[1:])
            v.mono += 5 if pages == 1 else 4
            v.time += 5 if pages == 1 else 4
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": f"foreign-{i}",
                            "status": "CONFIRMED",
                            "market": quotes()[0].market_id,
                            "maker_orders": [
                                {
                                    "order_id": f"foreign-order-{i}",
                                    "asset_id": "3",
                                    "side": "BUY",
                                    "price": ".5",
                                    "matched_amount": "1",
                                }
                            ],
                        }
                    ],
                    "next_cursor": "LTE=" if i == pages - 1 else f"p{i + 1}",
                },
                request=request,
            )
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", [quotes()[0]])
    assert not active_reads
    assert events[0] == ("CANCEL", 3.0) and any(x[0] == "TRADES" for x in events)
    assert result.fill_deadline_monotonic == 3
    assert v.time == START + 173 + (5 if pages == 1 else 24)
    assert result.yes_inventory == result.no_inventory == 0
    assert len(v.posts) == 1 and result.close_barrier == "SAFE_TO_CONTINUE"


@pytest.mark.parametrize("terminal,interrupt", [(True, False), (False, False), (True, True)])
async def test_h02_deadline_cancels_batch_before_blocked_close_read_and_drains(
    env, terminal, interrupt
):
    v, cfg = env
    # Real event-loop/monotonic clock for the concurrent timer; fake HTTP only.
    executor = await SMWindowExecutor.open(cfg, SLUG, "timer-session")
    v.executor = executor
    v.cancel_terminal = terminal
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    cancellation_times = []
    blocked_once = False

    def hold_read(venue, oid):
        nonlocal blocked_once
        if not blocked_once:
            blocked_once = True
            assert venue.cancels  # no order/account read between the two submitted legs
            entered.set()
            assert release.wait(5), "test's SDK read was not released"

    def on_cancel(venue):
        cancellation_times.append(time.monotonic())
        assert not release.is_set()  # actual DELETE precedes read completion
        cancelled.set()

    v.on_order_read, v.on_cancel = hold_read, on_cancel
    task = asyncio.create_task(executor.execute_window("w1", deepcopy(quotes())))
    try:
        assert await asyncio.to_thread(cancelled.wait, 4)
        assert await asyncio.to_thread(entered.wait, 1)
        if interrupt:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # do not abandon the still-running SDK read
        assert not task.done()  # read remains owned/awaited; no premature result
        release.set()
        result = await task
    finally:
        release.set()
        if not task.done():
            await task
    assert len(v.posts) == 2 and len(v.post_requests) == 1 and v.cancels == [["o-1", "o-2"]]
    assert result.t0_monotonic + 3 <= cancellation_times[0] <= result.t0_monotonic + 3.4
    assert cancellation_times[0] < result.t0_monotonic + 5
    assert all(job.done() for job in executor._cancel_jobs.values())
    if interrupt:
        assert result.close_barrier == "BLOCKED" and result.block_reason == "ABNORMAL_EXIT"
        assert result.capital_committed == 0 and v.rows["o-1"]["status"] == "CANCELED"
    elif terminal:
        assert result.close_barrier == "SAFE_TO_CONTINUE" and result.capital_committed == 0
    else:
        assert result.close_barrier == "BLOCKED" and result.capital_committed == 4.85
        await executor.execute_window("w2", quotes("w2"))
        assert len(v.posts) == 2


@pytest.mark.parametrize("elapsed", [222, 240])
async def test_h02_cutoff_still_prevents_all_new_posts(env, elapsed):
    v, executor = await open_executor(env)
    v.time = START + elapsed
    result = await executor.execute_window("w1", quotes())
    assert not v.posts and result.close_barrier == "BLOCKED"
    assert result.block_reason == "MARKET_SUBMISSION_CUTOFF"
