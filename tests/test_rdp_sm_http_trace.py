"""Observe the actual SDK client without changing its requests or retry policy."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from py_clob_client_v2.exceptions import PolyApiException
from py_clob_client_v2.http_helpers import helpers

from polymaker.config import RDPObiConfig
from polymaker.rdp_sm_execution import ExecutionClobClient
from polymaker.rdp_sm_http_trace import sdk_write_timing
from polymaker.rdp_sm_network import NetworkHealth


def test_sdk_timing_preserves_body_and_logs_only_safe_phase_metadata(monkeypatch):
    body = '[{"signed-order":"PRIVATE_BODY"}]'

    def handle(request):
        assert request.content.decode() == body
        assert request.headers['authorization'] == 'PRIVATE_HEADER'
        trace = request.extensions['trace']
        trace('http2.send_request_headers.started', {'request': request})
        trace('http2.send_request_headers.complete', {'return_value': request})
        trace('http2.receive_response_headers.started', {'request': request})
        trace('http2.receive_response_headers.complete', {'return_value': 'PRIVATE_RESPONSE'})
        return httpx.Response(200, json={'ok': True}, headers={'cf-ray': 'PRIVATE_RAY-SIN'},
                              extensions={'http_version': b'HTTP/2'})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(helpers, '_http_client', client)
        began, wall = time.monotonic(), time.time()
        with sdk_write_timing() as timing:
            assert helpers.post('https://clob.polymarket.com/orders', data=body,
                                headers={'authorization': 'PRIVATE_HEADER'}) == {'ok': True}
        summary = timing.snapshot()
        assert summary['http_version'] == 'HTTP/2' and summary['edge_pop'] == 'SIN'
        assert summary['request_count'] == 1 and summary['trace_attached']
        assert summary['stage_ms']['http2.receive_response_headers'] >= 0
        network = NetworkHealth(RDPObiConfig())
        ExecutionClobClient._observe_http(SimpleNamespace(network=network), 'POST',
            'https://clob.polymarket.com/orders', began, wall, transport=summary)
        payload = next(p for k, p, _ in network.drain_events() if k == 'sm_network_request')
        assert payload['transport'] == summary and payload['outcome'] == 'SUCCESS'
        assert 'PRIVATE' not in json.dumps(payload)


def test_sdk_failure_records_phase_and_resets_context_without_retry(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            trace = request.extensions['trace']
            trace('connection.connect_tcp.started', {'request': request})
            trace('connection.connect_tcp.failed', {'exception': RuntimeError('PRIVATE')})
            raise httpx.ConnectError('PRIVATE', request=request)
        assert 'trace' not in request.extensions
        return httpx.Response(200, json={'ok': True})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(helpers, '_http_client', client)
        with pytest.raises(PolyApiException), sdk_write_timing() as timing:
            helpers.post('https://clob.polymarket.com/orders', data='[]', retry_on_error=False)
        assert len(calls) == 1
        assert timing.snapshot()['failed_stages'] == ['connection.connect_tcp']
        assert 'PRIVATE' not in json.dumps(timing.snapshot())
        assert helpers.get('https://clob.polymarket.com/time') == {'ok': True}
        assert timing.request_count == 1


def test_parallel_sdk_writes_keep_timing_contexts_separate_and_hooks_unique(monkeypatch):
    barrier = Barrier(2)

    def handle(request):
        barrier.wait(timeout=5)
        pop = request.content.decode()
        return httpx.Response(200, json={'ok': True}, headers={'cf-ray': 'opaque-' + pop},
                              extensions={'http_version': b'HTTP/1.1'})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(helpers, '_http_client', client)

        def run(pop):
            with sdk_write_timing() as timing:
                helpers.delete('https://clob.polymarket.com/orders', data=pop)
            return timing.snapshot()

        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(run, ['SIN', 'LHR']))
        assert a['edge_pop'] == 'SIN' and b['edge_pop'] == 'LHR'
        assert a['request_count'] == b['request_count'] == 1
        assert len(client.event_hooks['request']) == len(client.event_hooks['response']) == 1


def test_existing_trace_callback_is_preserved(monkeypatch):
    calls = []

    def handle(request):
        request.extensions['trace']('http11.send_request_body.started', {})
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(helpers, '_http_client', client)
        with sdk_write_timing() as timing:
            client.post('https://clob.polymarket.com/orders',
                        extensions={'trace': lambda name, info: calls.append(name)})
        assert calls == ['http11.send_request_body.started']
        assert not timing.trace_attached and not timing.stage_ms
