"""Transport causes must be observable without changing receipts or quality gaps."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from m1223_capture_diagnostics import VERSION, with_transport_diagnostics
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from m1234_fixtures import fixture_events
from polymaker.m1234_sim.engine import market_meta
from polymaker.m1234_sim.runner import CaptureFeed


def make_feed(base=CaptureFeed, capacity=2048):
    queue = asyncio.Queue(maxsize=capacity)
    feed = base(market_meta(fixture_events()[0]["definition"]["market"]), queue)
    feed.monotonic = lambda: 10.0
    return feed, queue


def test_safe_close_codes_and_fixed_timeout_category_are_retained():
    feed, queue = make_feed(with_transport_diagnostics(CaptureFeed))
    feed.record_failure(ConnectionClosedError(None, Close(1011, "keepalive ping timeout")))
    feed.invalidate("WS_RECONNECT_REQUIRED")
    kind, payload = queue.get_nowait()
    assert kind == "GAP" and payload["reason"] == "WS_RECONNECT_REQUIRED"
    diagnostic = payload["capture_transport_diagnostics"]
    assert diagnostic["version"] == VERSION
    assert diagnostic["error_type"] == "ConnectionClosedError"
    assert diagnostic["category"] == "KEEPALIVE_PING_TIMEOUT"
    assert diagnostic["sent_close_code"] == 1011
    assert diagnostic["received_close_code"] is None
    assert diagnostic["failure_count"] == 1


def test_credentials_and_arbitrary_close_reasons_are_not_persisted():
    feed, queue = make_feed(with_transport_diagnostics(CaptureFeed))
    feed.ws = SimpleNamespace(close_code=1006)
    secret = "https://user:password@proxy.invalid/private"
    feed.record_failure(ConnectionClosedError(None, Close(1011, secret)))
    feed.invalidate("WS_RECONNECT_REQUIRED")
    _, payload = queue.get_nowait()
    assert secret not in json.dumps(payload)
    assert payload["capture_transport_diagnostics"]["category"] == "ERROR_TYPE_ONLY"
    feed.record_failure(OSError(secret))
    feed.invalidate("WS_RECONNECT_REQUIRED")
    _, payload = queue.get_nowait()
    assert secret not in json.dumps(payload)
    assert payload["capture_transport_diagnostics"]["error_type"] == "OSError"


def test_unchanged_receipts_gaps_queue_size_and_exception_behavior():
    original, oq = make_feed()
    wrapped, wq = make_feed(with_transport_diagnostics(CaptureFeed))
    for feed in (original, wrapped):
        feed.connect()
        feed.apply("FRAME", received_mono=9.0, received_wall=1000.0)
        feed.record_failure(ValueError("WS_QUEUE_OVERFLOW"))
        feed.invalidate("WS_RECONNECT_REQUIRED")
    assert oq.maxsize == wq.maxsize == 2048
    while not oq.empty():
        kind, before = oq.get_nowait()
        wk, after = wq.get_nowait()
        after.pop("capture_transport_diagnostics", None)
        assert (kind, before) == (wk, after)
    assert wq.empty()
    assert original.failure_count == wrapped.failure_count
    assert original.last_failure["reason"] == wrapped.last_failure["reason"]


def test_capture_overflow_remains_a_failure_and_is_not_hidden():
    for cls in (CaptureFeed, with_transport_diagnostics(CaptureFeed)):
        feed, queue = make_feed(cls, capacity=1)
        feed.connect()
        with pytest.raises(RuntimeError, match="SIM_CAPTURE_QUEUE_OVERFLOW"):
            feed.invalidate("WS_DISCONNECTED")
        assert queue.maxsize == 1 and feed.failure == "SIM_CAPTURE_QUEUE_OVERFLOW"


def test_bad_diagnostic_property_does_not_suppress_original_gap():
    class BadSocket:
        @property
        def close_code(self):
            raise ValueError("private diagnostic text")

    feed, queue = make_feed(with_transport_diagnostics(CaptureFeed))
    feed.ws = BadSocket()
    feed.invalidate("WS_DISCONNECTED")
    kind, payload = queue.get_nowait()
    assert kind == "GAP" and payload["reason"] == "WS_DISCONNECTED"
    assert payload["capture_transport_diagnostics"]["diagnostic_error_type"] == "ValueError"
    assert "private diagnostic text" not in json.dumps(payload)


def test_repeated_install_does_not_nest_wrappers():
    wrapped = with_transport_diagnostics(CaptureFeed)
    assert with_transport_diagnostics(wrapped) is wrapped
