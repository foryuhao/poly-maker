"""Bootstrap recovery uses the real SDK, GET-only fake HTTP and existing waits."""

import asyncio
import threading
import time

import httpx
import pytest

from test_rdp_sm_sync import make_service, wait_rounds
from test_rdp_sm_sync import sync_env as sync_env


@pytest.mark.parametrize("path", ["/auth/derive-api-key", "/time"])
@pytest.mark.parametrize("failure", ["connect", "read", 429, 503])
def test_transient_bootstrap_recovers_in_same_worker(sync_env, monkeypatch, path, failure):
    venue = sync_env[0]
    service, readers = make_service(sync_env)
    failed, clients, waits = [], [], []

    def hook(request):
        if request.url.path == "/auth/derive-api-key":
            if clients:
                assert clients[-1].is_closed
            clients.append(readers[0].http)
        if request.url.path != path or len(failed) == 3:
            return None
        failed.append(request.url.path)
        if failure == "connect":
            raise httpx.ConnectTimeout("transient", request=request)
        if failure == "read":
            raise httpx.ReadTimeout("transient", request=request)
        return httpx.Response(failure, headers={"Retry-After": "11"} if failure == 429 else {})

    native_sleep = service._sleep_until

    async def fast_startup_sleep(target):
        if service.latest().status == "CREATED":
            waits.append(target-time.monotonic())
            assert readers[0].http.is_closed
            await asyncio.sleep(0)
        else:
            await native_sleep(target)

    venue.hook = hook
    monkeypatch.setattr(service, "_sleep_until", fast_startup_sleep)
    service.start()
    try:
        events = wait_rounds(service, lane="account")
        assert service.latest().status == "RUNNING"
        assert len(readers) == 1 and len(clients) == 4
        assert waits == pytest.approx([11, 11, 11] if failure == 429 else [2, 4, 8], abs=.1)
        retries = [event for event in events if event["kind"] == "sync_startup_retry"]
        assert [event["attempt"] for event in retries] == [1, 2, 3]
        assert all(event["scope"] == "bootstrap" for event in events
                   if event["kind"] == "sync_http" and event["endpoint"] == "/auth/derive-api-key")
        assert all(event["scope"] == "account" for event in events
                   if event["kind"] == "sync_http" and event["endpoint"] == "/time")
        assert all(event["reads_complete"] for event in events if event["kind"] == "sync_round")
        assert any(endpoint == "/balance-allowance" for endpoint, _ in venue.calls)
    finally:
        assert service.stop()
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("path", ["/auth/derive-api-key", "/time"])
@pytest.mark.parametrize("status", [401, 403])
def test_bootstrap_auth_failure_remains_hard(sync_env, path, status):
    venue = sync_env[0]
    service, readers = make_service(sync_env)
    venue.hook = lambda request: httpx.Response(status) if request.url.path == path else None
    before = len(venue.calls)
    service.start()
    try:
        deadline = time.monotonic()+2
        while service.latest().status != "FAILED" and time.monotonic() < deadline:
            service.wait_after(service.latest().version, .02)
        snapshot = service.latest()
        assert snapshot.status == "FAILED" and snapshot.fatal_type == "HTTPStatusError"
        assert sum(endpoint == path for endpoint, _ in venue.calls[before:]) == 1
        assert not any(event["kind"] in {"sync_startup_retry", "sync_round"}
                       for event in service.drain_events())
    finally:
        assert service.stop()
    assert readers[0].http.is_closed


def test_persistent_startup_failure_stop_interrupts_backoff(sync_env):
    venue = sync_env[0]
    service, readers = make_service(sync_env)

    def hook(request):
        raise httpx.ConnectTimeout("persistent", request=request)

    venue.hook = hook
    before = len(venue.calls)
    service.start()
    try:
        deadline = time.monotonic()+2
        events = []
        while not any(event["kind"] == "sync_startup_retry" for event in events):
            assert time.monotonic() < deadline
            events.extend(service.drain_events())
            service.wait_after(service.latest().version, .01)
        assert service.latest().status == "CREATED"
        began = time.monotonic()
        assert service.stop(timeout=.5)
        assert time.monotonic()-began < .5
        assert service.latest().status == "STOPPED"
        assert len(venue.calls)-before == 1 and readers[0].http.is_closed
        assert not any(event["kind"] == "sync_round" for event in service.drain_events())
    finally:
        service.stop()


def test_stop_during_bootstrap_drains_native_read_without_starting_lanes(sync_env):
    venue = sync_env[0]
    service, readers = make_service(sync_env)
    entered, release = threading.Event(), threading.Event()

    def hook(request):
        entered.set()
        assert release.wait(2)
        raise httpx.ConnectTimeout("in flight", request=request)

    venue.hook = hook
    before = len(venue.calls)
    service.start()
    try:
        assert entered.wait(1)
        assert not service.stop(timeout=.01)
        assert not readers[0].http.is_closed
        release.set()
        assert service.stop(timeout=1)
        assert service.latest().status == "STOPPED"
        assert len(venue.calls)-before == 1 and readers[0].http.is_closed
        assert not any(event["kind"] in {"sync_round", "sync_startup_retry"}
                       for event in service.drain_events())
    finally:
        release.set()
        service.stop()
