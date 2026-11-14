"""Classify positive rejection evidence, separately from uncertain transport."""

from polymaker.rdp_sm_execution import _explicit_maker_rejection


def rejection(raw, notional):
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if not isinstance(raw, dict):
        return None
    message = raw.get("errorMsg") or raw.get("error") or ""
    if not isinstance(message, str):
        return None
    lower = message.lower()
    if any(word in lower for word in ("duplicat", "already exists", "context canceled")):
        return None
    if (any(raw.get(k) not in (None, "") for k in ("orderID", "order_id", "orderId", "id"))
            or any(raw.get(k) not in (None, []) for k in (
                "tradeIDs", "tradeIds", "trade_ids", "transactionsHashes", "transactionHashes"))
            or str(raw.get("status", "")).upper() in {"LIVE", "MATCHED", "DELAYED"}):
        return None
    known = any(word in lower for word in (
        "post only", "post-only", "crosses", "balance", "allowance", "signature", "minimum",
        "min size", "min_size", "min_tick", "tick size", "invalid amount", "invalid order",
        "expired", "market closed", "not accepting", "orderbook does not exist"))
    maker_rejection = _explicit_maker_rejection(raw, notional)
    if not (maker_rejection or raw.get("success") is False and known):
        return None
    retryable = maker_rejection or any(word in lower for word in ("post only", "post-only", "crosses", "balance", "allowance"))
    reprice = ("cross" in lower and any(word in lower for word in ("post only", "post-only", "book"))
               or lower.strip() in {"post only", "post-only"})
    return {"kind": "REJECTED_RESPONSE", "message": message, "retryable": retryable, "reprice": reprice}


def retryable_http_rejection(exc):
    # Official 425/429 mean unavailable/rate limited before acceptance. A 5xx
    # or timeout can have accepted the order and therefore remains UNKNOWN.
    # https://docs.polymarket.com/resources/error-codes
    status = getattr(exc, "status_code", None)
    if status in {425, 429}:
        return {"kind": f"HTTP_{status}", "retryable": True}
    return None


def post_exception_rejection(exc, notional):
    proof = retryable_http_rejection(exc)
    if proof is not None:
        return proof
    # SDK raises non-2xx before returning its body. A normal validation reject
    # still permits the same handling as its equivalent decoded JSON response.
    if getattr(exc, "status_code", None) != 400:
        return None
    payload = getattr(exc, "error_msg", None)
    if isinstance(payload, str):
        payload = {"success": False, "errorMsg": payload}
    elif isinstance(payload, dict):
        payload = {"success": False, **payload}
    return rejection(payload, notional)


def uncertain_kind(exc):
    import httpx

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            return "UNCERTAIN_TIMEOUT"
        exc = exc.__cause__ or exc.__context__
    return "UNCERTAIN_RESPONSE"
