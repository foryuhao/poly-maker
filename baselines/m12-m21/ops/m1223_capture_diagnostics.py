"""Safe transport provenance for future frozen captures; never a signal input."""

VERSION = "m1223-transport-diagnostics-v1"


def close_code(value):
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def safe_failure(exc):
    received, sent = getattr(exc, "rcvd", None), getattr(exc, "sent", None)
    # Only a fixed diagnostic label survives. Arbitrary close reasons and
    # exception text can contain credentials and must never be persisted.
    timeout = any(
        getattr(frame, "reason", None) == "keepalive ping timeout" for frame in (received, sent)
    )
    return {
        "error_type": type(exc).__name__,
        "category": "KEEPALIVE_PING_TIMEOUT" if timeout else "ERROR_TYPE_ONLY",
        "received_close_code": close_code(getattr(received, "code", None)),
        "sent_close_code": close_code(getattr(sent, "code", None)),
    }


def with_transport_diagnostics(base):
    if getattr(base, "transport_diagnostics_version", None) == VERSION:
        return base

    class TransportDiagnosticsFeed(base):
        transport_diagnostics_version = VERSION

        def record_failure(self, exc, context=None):
            super().record_failure(exc, context)
            try:
                self._transport_failure = safe_failure(exc)
            except Exception as error:
                self._transport_failure = {"diagnostic_error_type": type(error).__name__}

        def put(self, kind, **payload):
            if kind == "GAP" and payload.get("reason") in {
                "WS_DISCONNECTED", "WS_RECONNECT_REQUIRED"
            }:
                diagnostic = {
                    "version": VERSION,
                    "scope": "TRANSPORT_PROVENANCE_ONLY_NOT_SIGNAL_INPUT",
                }
                try:
                    ws = getattr(self, "ws", None)
                    diagnostic["connection_close_code"] = close_code(
                        getattr(ws, "close_code", None)
                    )
                    if payload["reason"] == "WS_RECONNECT_REQUIRED":
                        diagnostic.update(getattr(self, "_transport_failure", {}))
                        diagnostic["failure_count"] = self.failure_count
                except Exception as error:
                    diagnostic["diagnostic_error_type"] = type(error).__name__
                payload["capture_transport_diagnostics"] = diagnostic
            return super().put(kind, **payload)

    return TransportDiagnosticsFeed
