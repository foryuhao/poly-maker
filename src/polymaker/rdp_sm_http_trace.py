"""Request-local SDK write timing; never retain headers, bodies or trace info."""

import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from py_clob_client_v2.http_helpers import helpers

_active = ContextVar("sm_http_timing", default=None)
_hook_lock = Lock()
_stages = frozenset({
    "connection.connect_tcp", "connection.start_tls",
    "http11.send_request_headers", "http11.send_request_body",
    "http11.receive_response_headers", "http11.receive_response_body", "http11.response_closed",
    "http2.send_connection_init", "http2.send_request_headers", "http2.send_request_body",
    "http2.receive_remote_settings", "http2.receive_response_headers",
    "http2.receive_response_body", "http2.response_closed",
})


class WriteTiming:
    def __init__(self, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.request_at = self.first_event_at = None
        self.started, self.stage_ms, self.failed_stages = {}, {}, []
        self.http_version = self.edge_pop = None
        self.request_count = 0
        self.trace_attached = False

    def trace(self, name, _info):
        # _info can contain authenticated requests and response headers. Do not
        # copy, serialize, or include it in exception diagnostics.
        stage, _, suffix = name.rpartition(".")
        if stage not in _stages:
            return
        now = self.monotonic()
        if self.first_event_at is None:
            self.first_event_at = now
        if suffix == "started":
            self.started[stage] = now
        elif suffix in {"complete", "failed"}:
            began = self.started.pop(stage, None)
            if began is not None:
                self.stage_ms[stage] = self.stage_ms.get(stage, 0.) + (now - began) * 1000
            if suffix == "failed":
                self.failed_stages.append(stage)

    def snapshot(self):
        return {"request_count": self.request_count, "trace_attached": self.trace_attached,
                "http_version": self.http_version, "edge_pop": self.edge_pop,
                "request_to_first_trace_ms": (
                    (self.first_event_at - self.request_at) * 1000
                    if self.request_at is not None and self.first_event_at is not None else None),
                "stage_ms": {k: round(v, 3) for k, v in self.stage_ms.items()},
                "failed_stages": list(self.failed_stages)}


def _request_hook(request):
    timing = _active.get()
    if timing is None or request.method not in {"POST", "DELETE"}:
        return
    timing.request_count += 1
    timing.request_at = timing.monotonic()
    if "trace" not in request.extensions:
        request.extensions["trace"] = timing.trace
        timing.trace_attached = True


def _response_hook(response):
    timing = _active.get()
    if timing is None or response.request.method not in {"POST", "DELETE"}:
        return
    version = response.extensions.get("http_version")
    timing.http_version = {b"HTTP/1.0": "HTTP/1.0", b"HTTP/1.1": "HTTP/1.1",
                           b"HTTP/2": "HTTP/2"}.get(version)
    pop = response.headers.get("cf-ray", "").rsplit("-", 1)[-1]
    timing.edge_pop = pop if re.fullmatch(r"[A-Z]{3}", pop) else None


@contextmanager
def sdk_write_timing():
    # Reuse the SDK's actual client and pool. Hooks are inert outside this
    # context, and ContextVar isolates simultaneous SDK worker operations.
    with _hook_lock:
        for key, hook in (("request", _request_hook), ("response", _response_hook)):
            hooks = helpers._http_client.event_hooks[key]
            if hook not in hooks:
                hooks.append(hook)
    timing = WriteTiming()
    token = _active.set(timing)
    try:
        yield timing
    finally:
        _active.reset(token)
