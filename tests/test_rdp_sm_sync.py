"""Phase-one observer with the real pinned SDK and private fake HTTP only."""

from __future__ import annotations

import copy
import json
import socket
import threading
import time
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import httpx
import pytest

from polymaker.config import Config, Secrets
from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_sm_sync import SyncObserver, SyncSettings
from polymaker.rdp_sm_sync_reads import ObservedMarket, ObserverReader
from test_rdp_sm_m1 import ADDRESS, CID, DUMMY_KEY, SPENDER, START, Venue


def order(oid="order-1", *, status="LIVE", matched=0):
    return {"id": oid, "asset_id": "1", "market": CID, "side": "BUY", "price": ".4",
            "original_size": "5", "size_matched": str(matched), "status": status,
            "associate_trades": ["trade-1"] if matched else []}


def trade(*, status="CONFIRMED", maker=ADDRESS):
    return {"id": "trade-1", "market": CID, "status": status, "timestamp": START + 60,
            "maker_orders": [{"order_id": "order-1", "asset_id": "1", "side": "BUY",
                              "price": ".4", "matched_amount": "5", "maker_address": maker}]}


class SyncVenue:
    def __init__(self):
        self.time = START + 60
        self.opens = [order()]
        self.orders = {"order-1": order()}
        self.trades = []
        self.calls = []
        self.hook = None
        self.lock = threading.Lock()

    def response(self, request):
        assert request.method == "GET", "observer attempted a write"
        path, params = request.url.path, dict(request.url.params)
        with self.lock:
            self.calls.append((path, params))
        if self.hook is not None:
            result = self.hook(request)
            if result is not None:
                return result
        if path == "/auth/derive-api-key":
            data = {"apiKey": "dummy-key", "secret": "c2FmZS10ZXN0", "passphrase": "dummy-pass"}
        elif path == "/time":
            data = self.time
        elif path == "/markets":
            epoch = int(params["slug"].rsplit("-", 1)[1])
            raw = copy.deepcopy(Venue().raw)
            raw.update(slug=params["slug"], conditionId=CID if epoch == START else "0x" + "cd" * 32,
                       endDate=datetime.fromtimestamp(epoch + 300, UTC).isoformat())
            data = [raw]
        elif path == "/data/orders":
            data = {"data": copy.deepcopy(self.opens), "next_cursor": "LTE="}
        elif path == "/data/trades":
            data = {"data": copy.deepcopy(self.trades) if params["market"] == CID else [],
                    "next_cursor": "LTE="}
        elif path.startswith("/data/order/"):
            row = self.orders.get(path.rsplit("/", 1)[1])
            if row is None:
                return httpx.Response(404, json={"error": "missing"})
            data = copy.deepcopy(row)
        elif path == "/balance-allowance":
            data = {"balance": "15000000", "allowances": {SPENDER: "15000000"},
                    "api_secret": "must-not-be-exported"}
        elif path == "/v2/positions":
            data = {"data": [{"token_id": "1", "current_size": "0", "avg_price": "0"}],
                    "pagination": {"next_cursor": None}}
        else:
            raise AssertionError("unexpected GET path")
        return httpx.Response(200, json=data)


@pytest.fixture
def sync_env(monkeypatch, tmp_path):
    def deny(*_args, **_kwargs):
        raise AssertionError("observer tests forbid real network and production Gateway")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(ExecutionGateway, "connect", deny)
    monkeypatch.setattr(ExecutionGateway, "place", deny)
    monkeypatch.setattr(ExecutionGateway, "cancel", deny)
    venue = SyncVenue()
    cfg = Config(secrets=Secrets.model_construct(pk=DUMMY_KEY, browser_address=ADDRESS))
    events = []
    reader = ObserverReader(cfg, emit=events.append, transport=httpx.MockTransport(venue.response))
    reader.open()
    try:
        yield venue, cfg, reader, events
    finally:
        reader.close()


def test_private_transport_real_sdk_get_only_and_redaction(sync_env):
    venue, _, reader, events = sync_env
    from py_clob_client_v2.http_helpers import helpers
    original = helpers._http_client
    reader.observed_ids = frozenset({"order-1"})
    market = reader.market(START)
    for name, fn in (("open_orders", reader.open_orders), ("trades", lambda: reader.trades(market)),
                     ("order", lambda: reader.order("order-1", market)), ("capital", reader.capital),
                     ("positions", reader.positions)):
        evidence = reader.read(name, CID, fn)
        assert evidence.complete, evidence
        assert evidence.requests == 1
        assert "must-not-be-exported" not in json.dumps(evidence.to_dict())
    before = len(venue.calls)
    for fn in (lambda: reader.client._post("https://clob.polymarket.com/order"),
               lambda: reader.client._delete("https://clob.polymarket.com/orders"),
               reader.client.create_api_key, reader.client.update_balance_allowance,
               lambda: reader.client.get_order("unknown"),
               lambda: reader.http_get("https://clob.polymarket.com/balance-allowance/update"),
               lambda: reader.http_get("https://attacker.invalid/time")):
        with pytest.raises((RuntimeError, ValueError)):
            fn()
    assert len(venue.calls) == before
    assert helpers._http_client is original
    assert not any("secret" in json.dumps(e) for e in events)


def test_full_pagination_failure_keeps_partial_and_scope(sync_env):
    venue, _, reader, events = sync_env
    def pages(request):
        if request.url.path != "/data/orders":
            return None
        if request.url.params["next_cursor"] == "MA==":
            return httpx.Response(200, json={"data": [order()], "next_cursor": "page2"})
        return httpx.Response(429, headers={"Retry-After": "3"}, json={"secret": "do-not-copy"})
    venue.hook = pages
    failed = reader.read("open_orders", "account", reader.open_orders)
    assert not failed.complete and failed.data is None
    assert json.loads(failed.partial_json)[0]["order_id"] == "order-1"
    assert failed.requests == 2 and failed.http_status == 429 and failed.retry_after_s == 3
    assert "do-not-copy" not in json.dumps(failed.to_dict())
    recorded = [e for e in events if e["read_id"] == failed.read_id]
    assert len(recorded) == 2 and all(e["scope"] == "account" for e in recorded)
    venue.hook = None
    recovered = reader.read("open_orders", "account", reader.open_orders)
    assert recovered.complete and recovered.requests == 1
    data = recovered.data
    data[0]["price"] = 99
    assert recovered.data[0]["price"] == .4
    with pytest.raises(FrozenInstanceError):
        recovered.complete = False


@pytest.mark.parametrize("kind", ["cursor_cycle", "missing_cursor", "position_cursor", "bad_identity"])
def test_bad_pages_never_become_empty_success(sync_env, kind):
    venue, _, reader, _ = sync_env
    def corrupt(request):
        path = request.url.path
        if kind == "position_cursor" and path == "/v2/positions":
            return httpx.Response(200, json={"data": [], "pagination": {}})
        if path != "/data/orders":
            return None
        row = order()
        if kind == "bad_identity":
            row["asset_id"] = " 1"
        page = {"data": [row]}
        if kind == "cursor_cycle":
            page["next_cursor"] = "MA=="
        return httpx.Response(200, json=page)
    venue.hook = corrupt
    fn = reader.positions if kind == "position_cursor" else reader.open_orders
    result = reader.read(kind, "account", fn)
    assert not result.complete and result.data is None and result.requests <= 2


def wait_rounds(service, lane="orders", count=1, timeout=4):
    events, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        events.extend(service.drain_events())
        if sum(e["kind"] == "sync_round" and (lane is None or e["lane"] == lane) for e in events) >= count:
            return events
        snapshot = service.latest()
        assert snapshot.status != "FAILED", snapshot
        service.wait_after(snapshot.version, .02)
    raise AssertionError("observer round timeout")


def make_service(sync_env, settings=None):
    venue, cfg, _, _ = sync_env
    readers = []
    def factory(emit, stop):
        reader = ObserverReader(cfg, emit=emit, stop=stop, transport=httpx.MockTransport(venue.response))
        readers.append(reader)
        return reader
    return SyncObserver(factory, settings or SyncSettings(.05, .1)), readers


def test_service_consumers_do_not_trigger_refresh_or_confer_authority(sync_env):
    venue, _, _, _ = sync_env
    service, _ = make_service(sync_env, SyncSettings(10, 20))
    service.start()
    try:
        wait_rounds(service, lane=None, count=2)
        previous = service.latest()
        calls = len(venue.calls)
        for _ in range(100):
            assert service.latest() is previous
            assert service.wait_after(previous.version, 0) is previous
        assert len(venue.calls) == calls
        assert previous.to_dict()["trading_authorized"] is False
        assert previous.status == "RUNNING"
        assert "trades" in {r.name for r in previous.last_good}
    finally:
        assert service.stop()


def test_slow_rounds_do_not_overlap_and_collections_are_shared(sync_env):
    venue, _, _, _ = sync_env
    venue.opens = [order("order-" + str(i)) for i in range(6)]
    venue.orders = {r["id"]: r for r in venue.opens}
    def slow(request):
        if request.url.path.startswith("/data/order/"):
            time.sleep(.04)
    venue.hook = slow
    service, _ = make_service(sync_env, SyncSettings(.02, .04))
    service.start()
    try:
        events = wait_rounds(service, count=3)
        rounds = [e for e in events if e["kind"] == "sync_round" and e["lane"] == "orders"]
        assert all(a["completed_monotonic"] <= b["started_monotonic"]
                   for a, b in zip(rounds, rounds[1:], strict=False))
        assert all(e["overrun"] for e in rounds)
        assert any(e["kind"] == "sync_detail_coverage" and e["unread_pending"] == 2 for e in events)
        for row in rounds:
            assert sum(e["kind"] == "sync_http" and e["endpoint"] == "/data/trades"
                       and row["started_monotonic"] <= e["started_monotonic"] < row["completed_monotonic"]
                       for e in events) == 1
        assert sum(path.startswith("/data/order/") for path, _ in venue.calls) >= 6
    finally:
        assert service.stop()


def test_last_good_is_retained_without_freshening_failure(sync_env):
    venue, _, _, _ = sync_env
    service, _ = make_service(sync_env)
    service.start()
    try:
        wait_rounds(service)
        old = next(r for r in service.latest().last_good if r.name == "open_orders")
        venue.hook = lambda r: httpx.Response(500, json={}) if r.url.path == "/data/orders" else None
        events = wait_rounds(service)
        assert any(e["kind"] == "sync_round" and not e["complete"] for e in events)
        snapshot = service.latest()
        current = next(r for r in snapshot.attempts if r.name == "open_orders")
        retained = next(r for r in snapshot.last_good if r.name == "open_orders")
        assert not current.complete
        assert retained.read_id == old.read_id
        assert retained.started_monotonic < current.started_monotonic
    finally:
        assert service.stop()


def test_stop_drains_inflight_thread_before_closing_transport(sync_env):
    venue, _, _, _ = sync_env
    entered, release = threading.Event(), threading.Event()
    def blocked(request):
        if request.url.path.startswith("/data/order/"):
            entered.set()
            assert release.wait(3)
    venue.hook = blocked
    service, readers = make_service(sync_env)
    service.start()
    try:
        assert entered.wait(3)
        assert service.stop(.01) is False
        assert not readers[0].http.is_closed
    finally:
        release.set()
        assert service.stop(3)
    assert readers[0].http.is_closed
    assert service.latest().status == "STOPPED"


def test_market_rollover_clears_market_cache_not_global_account(sync_env):
    venue, _, _, _ = sync_env
    service, _ = make_service(sync_env)
    service.start()
    try:
        wait_rounds(service)
        old = service.latest()
        venue.time = START + 305
        wait_rounds(service, count=3)
        snapshot = service.latest()
        assert json.loads(snapshot.market_json)["start"] == START + 300
        assert json.loads(old.market_json)["start"] == START
        assert not any(r.name.startswith("order:") for r in snapshot.last_good)
        assert any(r.name == "capital" for r in snapshot.last_good)
        assert all(r.scope != CID for r in snapshot.last_good if r.name == "trades")
    finally:
        assert service.stop()


def test_wrong_market_detail_and_foreign_maker_do_not_become_account_observations(sync_env):
    venue, _, reader, _ = sync_env
    reader.observed_ids = frozenset({"order-1"})
    wrong = ObservedMarket("other", "0x" + "cd" * 32, START, ("1", "2"))
    evidence = reader.read("order:order-1", wrong.condition_id, lambda: reader.order("order-1", wrong))
    assert not evidence.complete
    venue.opens = []
    venue.trades = [trade(maker="0x" + "12" * 20)]
    service, _ = make_service(sync_env)
    service.start()
    try:
        wait_rounds(service)
        assert service.latest().pending_ids == ()
        assert not any(r.name.startswith("order:") for r in service.latest().last_good)
    finally:
        assert service.stop()


def test_slow_account_lane_does_not_block_order_publication(sync_env):
    venue, _, _, _ = sync_env
    entered, release = threading.Event(), threading.Event()
    def blocked(request):
        if request.url.path == "/v2/positions":
            entered.set()
            assert release.wait(3)
    venue.hook = blocked
    service, _ = make_service(sync_env)
    service.start()
    try:
        assert entered.wait(3)
        wait_rounds(service, count=3)
        assert any(r.name == "trades" for r in service.latest().last_good)
        assert not any(r.name == "positions" for r in service.latest().last_good)
    finally:
        release.set()
        assert service.stop(3)


def test_late_fill_requeries_previously_observed_terminal_order(sync_env):
    venue, _, _, _ = sync_env
    venue.orders["order-1"] = order(status="CANCELED")
    service, _ = make_service(sync_env)
    service.start()
    try:
        wait_rounds(service)
        first = next(r for r in service.latest().last_good if r.name == "order:order-1")
        venue.opens = []
        wait_rounds(service)
        assert not service.latest().pending_ids
        venue.orders["order-1"] = order(status="CANCELED", matched=5)
        venue.trades = [trade()]
        events = wait_rounds(service, count=2)
        current = next(r for r in service.latest().last_good if r.name == "order:order-1")
        assert current.read_id != first.read_id and current.data["size_matched"] == 5
        assert any(e["kind"] == "sync_trade_observed" and e["status"] == "CONFIRMED" for e in events)
        assert any(e["kind"] == "sync_consistency" and e["observations_agree"] for e in events)
        assert not service.latest().to_dict()["trading_authorized"]
    finally:
        assert service.stop()


def test_fault_backoff_does_not_avalanche_orders_gets(sync_env):
    venue, _, _, _ = sync_env
    service, _ = make_service(sync_env)
    service.start()
    try:
        wait_rounds(service)
        venue.hook = lambda r: httpx.Response(429, json={}) if r.url.path == "/data/orders" else None
        events = wait_rounds(service)
        assert any(e["kind"] == "sync_round" and not e["reads_complete"] for e in events)
        before = sum(path == "/data/orders" for path, _ in venue.calls)
        time.sleep(.12)
        assert sum(path == "/data/orders" for path, _ in venue.calls) == before
    finally:
        assert service.stop()


def test_bad_account_host_is_rejected_before_any_request(sync_env):
    venue, cfg, _, _ = sync_env
    changed = cfg.model_copy(deep=True)
    changed.wallet.clob_host = "https://attacker.invalid"
    reader = ObserverReader(changed, transport=httpx.MockTransport(venue.response))
    before = len(venue.calls)
    with pytest.raises(ValueError, match="SYNC_OFFICIAL_HOSTS_REQUIRED"):
        reader.open()
    assert len(venue.calls) == before and reader.http is None
