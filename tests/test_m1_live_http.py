"""Offline regression: real protocol code, synthetic bytes, no venue access."""

import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Event, Thread

import httpcore
import httpx
import pytest
from httpcore._sync.http2 import HTTP2Connection
from py_clob_client_v2.exceptions import PolyApiException
from py_clob_client_v2.http_helpers import helpers

from polymaker.m1_live_http import sdk_http1_transport
from polymaker.rdp_sm_http_trace import sdk_write_timing


def test_installed_http2_concurrent_headers_reproduce_deque_error():
    """Pause actual HPACK iteration; a second real send_headers mutates it."""
    class Sink:
        def write(self, data, timeout=None):
            pass

    conn = HTTP2Connection(httpcore.Origin(b'https', b'offline.test', 443), Sink())
    conn._h2_state.initiate_connection()

    def request(header):
        return httpcore.Request('GET', 'https://offline.test/time',
            headers=[(b'host', b'offline.test'), (header, b'value')])

    conn._send_request_headers(request(b'x-seed'), 1)
    entered, resume = Event(), Event()

    def first():
        paused = False

        def trace(frame, event, arg):
            nonlocal paused
            if (not paused and event == 'line' and frame.f_code.co_name == 'search'
                    and frame.f_code.co_filename.endswith('/hpack/table.py')
                    and 'i' in frame.f_locals and frame.f_locals.get('name') == b'x-first'):
                paused = True
                entered.set()
                assert resume.wait(5)
            return trace

        previous_trace = sys.gettrace()
        sys.settrace(trace)
        try:
            conn._send_request_headers(request(b'x-first'), 3)
        finally:
            sys.settrace(previous_trace)

    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(first)
        try:
            assert entered.wait(5)
            conn._send_request_headers(request(b'x-second'), 5)
        finally:
            resume.set()
        with pytest.raises(RuntimeError, match='deque mutated during iteration'):
            future.result(5)


def test_transport_disables_http2_and_restores_sdk_client_on_error(monkeypatch):
    with httpx.Client(http2=True) as original:
        monkeypatch.setattr(helpers, '_http_client', original)
        assert original._transport._pool._http2
        with pytest.raises(ValueError, match='stop'), sdk_http1_transport():
            active = helpers._http_client
            assert active is not original
            assert active._transport._pool._http1
            assert not active._transport._pool._http2
            assert active.timeout == original.timeout
            assert active.headers == original.headers
            assert active.trust_env == original.trust_env
            assert active._transport._pool._max_connections == original._transport._pool._max_connections
            raise ValueError('stop')
        assert helpers._http_client is original
        assert active.is_closed and not original.is_closed


def test_mock_transport_cannot_be_replaced_by_network_transport(monkeypatch):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=1))) as client:
        monkeypatch.setattr(helpers, '_http_client', client)
        with sdk_http1_transport():
            assert helpers._http_client is client
            assert helpers.get('https://offline.test/time') == 1
        assert not client.is_closed


async def test_runner_installs_transport_before_sdk_workers_and_restores_on_failure(monkeypatch):
    from polymaker import m1_live
    from polymaker.config import Config

    closed = []

    class Reads:
        def __init__(self, cfg):
            pass

        async def connect_account(self):
            assert not helpers._http_client._transport._pool._http2
            raise ValueError('offline startup failure')

        def close(self):
            closed.append(True)

    monkeypatch.setattr(m1_live, '_ExecutionReads', Reads)
    with httpx.Client(http2=True) as original:
        monkeypatch.setattr(helpers, '_http_client', original)
        with pytest.raises(ValueError, match='offline startup failure'):
            await m1_live.run_m1(Config(), live=True)
        assert helpers._http_client is original
        assert closed == [True]


def test_sdk_parallel_reads_and_write_preserve_requests_without_retry(monkeypatch):
    """Actual HTTP/1.1 sockets: GETs and POST must all arrive before any returns."""
    barrier = Barrier(3)
    seen = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            seen.append((self.command, self.path, body, self.headers.get('Authorization')))
            if self.path != '/offline-failure':
                barrier.wait(5)
            payload = b'{"ok": true}'
            self.send_response(503 if self.path == '/offline-failure' else 200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    timings = []

    def post():
        with sdk_write_timing() as timing:
            result = helpers.post(url+'/offline-submit',
                headers={'Authorization': 'SYNTHETIC'}, data='{"payload": "test"}',
                retry_on_error=False)
        timings.append(timing.snapshot())
        return result

    try:
        with httpx.Client(http2=True) as original:
            monkeypatch.setattr(helpers, '_http_client', original)
            with sdk_http1_transport(), ThreadPoolExecutor(3) as pool:
                jobs = [pool.submit(helpers.get, url+'/offline-time'),
                        pool.submit(helpers.get, url+'/offline-cash'),
                        pool.submit(post)]
                assert [job.result(8) for job in jobs] == [{'ok': True}]*3
                with pytest.raises(PolyApiException):
                    helpers.post(url+'/offline-failure', data='{}', retry_on_error=False)
            assert helpers._http_client is original
        assert timings[0]['http_version'] == 'HTTP/1.1'
        assert timings[0]['request_count'] == 1
        assert timings[0]['trace_attached']
        assert timings[0]['stage_ms']['http11.send_request_headers'] >= 0
        assert sorted(seen) == sorted([
            ('GET', '/offline-time', b'', None), ('GET', '/offline-cash', b'', None),
            ('POST', '/offline-submit', b'{"payload": "test"}', 'SYNTHETIC'),
            ('POST', '/offline-failure', b'{}', None)])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
