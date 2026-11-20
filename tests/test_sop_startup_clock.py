"""Startup transport recovery cannot manufacture clocks or move fixed samples."""

import asyncio

import httpx
import pytest
from m1223_sim_forward import startup_public_clock


@pytest.mark.asyncio
async def test_two_transient_failures_return_actual_third_clock_with_bounded_delays():
    events, delays, calls = [], [], []
    actual = (1000., 23., 23.2)

    async def clock():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectTimeout("synthetic TLS timeout")
        return actual

    async def sleep(t):
        delays.append(t)

    result = await startup_public_clock(clock, first_epoch=1200, wall_now=lambda: 1000,
                                        sleep=sleep, log=lambda event, **p: events.append((event, p)))
    assert result is actual and len(calls) == 3 and delays == [.25, .5]
    assert [p["attempt"] for _, p in events] == [1, 2]
    assert all(p["fixed_first_epoch"] == 1200 for _, p in events)


@pytest.mark.asyncio
async def test_transport_attempts_exhaust_without_fourth_request_or_fallback_clock():
    calls, delays = [], []

    async def clock():
        calls.append(1)
        raise httpx.ReadTimeout("synthetic")

    async def sleep(t):
        delays.append(t)

    with pytest.raises(httpx.ReadTimeout):
        await startup_public_clock(clock, first_epoch=1200, wall_now=lambda: 1000,
                                   sleep=sleep, log=lambda *a, **kw: None)
    assert len(calls) == 3 and delays == [.25, .5]


@pytest.mark.asyncio
async def test_error_with_insufficient_original_startup_margin_does_not_retry():
    calls = []

    async def clock():
        calls.append(1)
        raise httpx.ConnectTimeout("synthetic")

    async def no_sleep(t):
        pytest.fail("no retry is allowed after original startup margin")

    with pytest.raises(httpx.ConnectTimeout):
        await startup_public_clock(clock, first_epoch=1200, wall_now=lambda: 1180,
                                   sleep=no_sleep, log=lambda *a, **kw: None)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("invalid official clock"), asyncio.CancelledError(),
    httpx.HTTPStatusError("bad status", request=httpx.Request("GET", "https://clob.polymarket.com/time"),
                          response=httpx.Response(503))])
async def test_nontransport_errors_and_cancellation_propagate_without_retry(error):
    calls = []

    async def clock():
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        await startup_public_clock(clock, first_epoch=1200, wall_now=lambda: 1000,
                                   log=lambda *a, **kw: None)
    assert len(calls) == 1
