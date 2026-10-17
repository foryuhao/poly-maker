"""Classify pauses in the existing one-market loop; never grant order authority."""

import httpx

from polymaker.execution.gateway import GatewayReadError
from polymaker.rdp_sm import _Issue

PAUSE_CODES = frozenset(
    {
        "WS_READINESS_TIMEOUT",
        "WS_NOT_CONNECTED",
        "WS_DISCONNECTED",
        "WS_RECONNECT_REQUIRED",
        "WS_BOOK_STALE",
        "WS_PAIR_TIME_SKEW",
        "WS_EMPTY_BOOK",
        "WS_CROSSED_BOOK",
        "WS_TOP_PENDING",
        "WS_REBUILD_REQUIRED",
        "WS_TICK_UNVERIFIED",
        "WS_OFFICIAL_TICK_CONFLICT",
        "ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM",
        "OFFICIAL_CAPITAL_INSUFFICIENT",
        "TEST_CAPITAL_LIMIT_EXCEEDED",
        "BOOK_SIDE_MISSING",
        "BOOK_STALE_OR_FUTURE",
        "BOOK_INVALID_OR_CROSSED",
        "BOOK_LEVEL_INVALID",
        "BOOK_TIMESTAMP_UNKNOWN",
        "DYNAMIC_TICK_UNKNOWN_OR_CHANGED",
        "EXECUTION_DATA_STALE",
        "PREFLIGHT_SNAPSHOT_EXPIRED",
        "VERSION_UNKNOWN",
        "OFFICIAL_TIME_INVALID",
        "DISCOVERY_TIME_OR_DATA_UNRELIABLE",
        "MARKET_NOT_AVAILABLE",
        "MARKET_NOT_TRADABLE",
        "CAPITAL_READ_UNKNOWN",
        "ORDER_STATE_UNKNOWN",
        "ORDER_NOT_TERMINAL",
        "TRADE_CONFIRMATION_UNRESOLVED",
        "FILLTRUTH_NOT_COMPLETE",
        "TRACKER_UNRESOLVED",
        "FINAL_FILL_SET_NOT_PROVEN",
        "OBSERVED_PAGE_FILL_NOT_RECONCILED",
        "PRE_SUBMIT_FILL_FACTS_UNRESOLVED",
        "PREVIOUS_WINDOW_TRADE_EVIDENCE_CHANGED",
        "CROSS_ORDER_TRADE_SET_CHANGED",
        "EVENT_DURING_CLOSE_REQUIRES_RECHECK",
        "CLOSE_EVIDENCE_INVALIDATED",
        "CAPITAL_EVIDENCE_CHANGED_DURING_READ",
        "CAPITAL_EVIDENCE_CHANGED_BEFORE_SUBMIT",
        "STRATEGY_QUOTE_EVIDENCE_CHANGED",
        "TRADE_PAGE_UNKNOWN",
        "TRADES_PAGINATION_INCOMPLETE",
        "TRADE_PAGINATION_UNRESOLVED",
        "OPEN_ORDER_PAGINATION_UNRESOLVED",
        "OPEN_ORDER_PAGE_UNKNOWN",
        "OPEN_ORDERS_PAGINATION_INCOMPLETE",
        "ACCOUNT_PAGINATION_UNRESOLVED",
        "CURRENT_MARKET_PENDING_EXPOSURE",
        "MERGE_CLOSE_RECHECK_BLOCKED",
    }
)
READ_ERRORS = frozenset({
    "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "ConnectError",
    "ReadError", "RemoteProtocolError", "HTTPStatusError", "PolyApiException",
    "GatewayReadError", "JSONDecodeError",
})
UNCERTAIN_MUTATIONS = frozenset({"UNKNOWN_PLACEMENT_EXPOSURE", "MERGE_UNKNOWN"})


def reason_code(exc):
    if isinstance(exc, (_Issue, GatewayReadError)):
        return str(exc)
    if isinstance(exc, str):
        return exc
    return f"READ_OR_PARSE_FAILED:{type(exc).__name__}"


def pause_reason(reason):
    parts = str(reason).split(";")
    return bool(parts) and all(
        part.rsplit(":", 1)[-1] in PAUSE_CODES
        or part.rsplit(":", 1)[-1].startswith(("order read ", "trade history ", "open orders ", "positions "))
        or (part.startswith("READ_OR_PARSE_FAILED:") and part.rsplit(":", 1)[-1] in READ_ERRORS)
        for part in parts)


def retry_read(exc):
    # Walk wrapped SDK/Gateway errors; authentication and identity conflicts
    # remain hard failures, and this function is never applied to BUY submission.
    current = exc
    while current is not None:
        if isinstance(current, _Issue) and not pause_reason(str(current)):
            return False
        status = getattr(current, "status_code", None)
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
        if status is not None and status != 429 and status < 500:
            return False
        current = current.__cause__
    return (isinstance(exc, (GatewayReadError, httpx.TransportError, httpx.HTTPStatusError))
            or type(exc).__name__ in READ_ERRORS or isinstance(exc, _Issue) and pause_reason(str(exc)))


def preflight_pause(result):
    checks = result["checks"] if isinstance(result, dict) else [vars(c) for c in result.checks]
    failed = [c for c in checks if c["outcome"] != "PASS"]
    return bool(failed) and all(c["name"] in {"capital", "trading_window"}
                               or pause_reason(c["detail"]) for c in failed)
