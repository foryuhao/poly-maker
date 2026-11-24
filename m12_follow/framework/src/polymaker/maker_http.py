"""Process-scoped SDK transport for the standalone Maker live runner."""

from contextlib import contextmanager

import httpx
from py_clob_client_v2.http_helpers import helpers


@contextmanager
def sdk_http1_transport():
    """Install before SDK workers start; restore after the runner drains them.

    The pinned SDK shares a synchronous HTTP/2 client across workers. In
    httpcore 1.0.9, concurrent send_headers calls can mutate the same HPACK
    deque while another call searches it. HTTP/1.1 keeps concurrent requests
    on separate connections, without serializing reads behind a POST or adding
    retries. Match the SDK's httpx defaults except for HTTP/2 negotiation.
    In-memory test transports stay installed, so offline runs cannot escape.
    """
    previous = helpers._http_client
    if isinstance(previous._transport, httpx.MockTransport):
        yield
        return
    with httpx.Client(http2=False, event_hooks=previous.event_hooks) as client:
        helpers._http_client = client
        try:
            yield
        finally:
            helpers._http_client = previous
