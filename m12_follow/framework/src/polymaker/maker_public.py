"""Strictly public market discovery, clock, tick and resolution GETs."""
import json
import re
import time
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation

import httpx

from polymaker.catalog.gamma import parse_market
from polymaker.market_protocol import gamma_protocol, market_protocol, position_ids


def public_market(raw, slug, *, allow_v2=False, require_version=False):
    """Map Up/Down by outcome, never Gamma's array order or market price."""
    if require_version and isinstance(raw, dict) and "version" not in raw:
        raise ValueError("GAMMA_MARKET_PROTOCOL_MISSING")
    meta = parse_market(raw, allow_v2=allow_v2) if isinstance(raw, dict) else None
    if meta is None or meta.slug != slug or raw.get("closed") is not False or raw.get("active") is not True:
        raise ValueError("PUBLIC_PUBLIC_MARKET_UNAVAILABLE")
    match = re.fullmatch(r"btc-updown-5m-([0-9]+)", slug)
    if not match or int(match[1]) % 300 or not re.fullmatch(r"0x[0-9a-fA-F]{64}", meta.condition_id):
        raise ValueError("PUBLIC_PUBLIC_MARKET_IDENTITY_INVALID")
    end = datetime.fromisoformat(meta.end_date_iso.replace("Z", "+00:00"))
    if end.tzinfo is None or end.timestamp() != int(match[1]) + 300:
        raise ValueError("PUBLIC_PUBLIC_MARKET_DURATION_INVALID")
    tokens = {t.outcome.casefold(): t for t in meta.tokens}
    if set(tokens) != {"up", "down"} or len({t.token_id for t in meta.tokens}) != 2:
        raise ValueError("PUBLIC_PUBLIC_TOKEN_MAPPING_INVALID")
    if any(not t.token_id.isdecimal() or not 0 < int(t.token_id) < 2**256 for t in meta.tokens):
        raise ValueError("PUBLIC_PUBLIC_TOKEN_ID_INVALID")
    return replace(meta, tokens=(tokens["up"], tokens["down"]))


class PublicClob:
    """Only the two unauthenticated GETs needed by the observer exist here."""

    def __init__(self):
        self.client = httpx.AsyncClient(base_url="https://clob.polymarket.com", timeout=5, trust_env=False)

    async def clock(self):
        started = time.monotonic()
        response = await self.client.get("/time")
        response.raise_for_status()
        now = response.json()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not 0 < now < 10**11:
            raise ValueError("PUBLIC_PUBLIC_TIME_INVALID")
        # Conservative bounds include integer-second precision and GET duration.
        return float(now), started, time.monotonic()

    async def ticks(self, tokens):
        result = {}
        for token in tokens:
            response = await self.client.get("/tick-size", params={"token_id": token})
            response.raise_for_status()
            tick = Decimal(str(response.json()["minimum_tick_size"]))
            if tick not in {Decimal(".1"), Decimal(".01"), Decimal(".001"), Decimal(".0001")}:
                raise ValueError("PUBLIC_PUBLIC_TICK_INVALID")
            result[token] = tick
        return result

    async def close(self):
        await self.client.aclose()


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport=None):
        self.transport = transport or httpx.AsyncHTTPTransport(http2=False, retries=0)

    async def handle_async_request(self, request):
        if (request.method != "GET" or request.url.scheme != "https"
                or request.url.host not in {"clob.polymarket.com", "gamma-api.polymarket.com"}):
            raise RuntimeError("PUBLIC_VIRTUAL_NETWORK_WRITE_OR_HOST_DENIED")
        return await self.transport.handle_async_request(request)

    async def aclose(self):
        await self.transport.aclose()


class VirtualPublicAPI(PublicClob):
    """Reuse proven public clock/tick reads; no SDK, account or credentials."""

    def __init__(self, *, on_failover=None, transport=None):
        self.on_failover = on_failover
        self.client = httpx.AsyncClient(transport=ReadOnlyTransport(transport), timeout=5,
                                       trust_env=False, follow_redirects=False)

    async def clock(self):
        # PublicClob uses relative CLOB URLs. The same client also supports
        # absolute official Gamma URLs, without admitting any additional host.
        self.client.base_url = "https://clob.polymarket.com"
        return await super().clock()

    async def ticks(self, tokens):
        self.client.base_url = "https://clob.polymarket.com"
        return await super().ticks(tokens)

    async def raw_market(self, slug, *, purpose="observation"):
        try:
            # The list route may omit closed markets. Reuse the settlement
            # observer's exact-slug route for both open and resolved identities.
            response = await self.client.get(f"https://gamma-api.polymarket.com/markets/slug/{slug}")
            response.raise_for_status()
            raw = response.json()
            if not isinstance(raw, dict) or raw.get("slug") != slug:
                raise ValueError("PUBLIC_PUBLIC_MARKET_IDENTITY_UNAVAILABLE")
            return raw
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            if self.on_failover:
                self.on_failover("OFFICIAL_ROUTE_FAILOVER", type(exc).__name__,
                                 {"slug": slug, "purpose": purpose})
            response = await self.client.get("https://gamma-api.polymarket.com/markets",
                                             params={"slug": slug, "limit": 2})
            response.raise_for_status()
            rows = response.json()
            if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                    or rows[0].get("slug") != slug):
                raise ValueError("PUBLIC_PUBLIC_MARKET_IDENTITY_INVALID") from exc
            return rows[0]

    async def market(self, slug):
        return public_market(await self.raw_market(slug), slug)

    async def resolution(self, market):
        return validate_resolution(await self.raw_market(market["slug"], purpose="settlement"), market)

BASE = "https://gamma-api.polymarket.com/markets/slug/"

def validate_resolution(market, session):
    """Map the resolved winning token to the journal's YES/NO identities."""
    if (market.get("slug") != session["slug"]
            or not session.get("condition_id")
            or str(market.get("conditionId", "")).lower() != session["condition_id"].lower()):
        raise ValueError("SETTLEMENT_MARKET_IDENTITY_MISMATCH")
    def array(key):
        value = market.get(key)
        return json.loads(value) if isinstance(value, str) else value

    protocol = market_protocol(session)
    if gamma_protocol(market) != protocol:
        raise ValueError("SETTLEMENT_MARKET_PROTOCOL_MISMATCH")
    ids = position_ids(market) if protocol == "v2" else array("clobTokenIds")
    expected = session.get("token_ids", {})
    if (not isinstance(ids, list) or len(ids) != 2 or len(set(ids)) != 2
            or set(expected) != {"YES", "NO"} or set(ids) != set(expected.values())):
        raise ValueError("SETTLEMENT_TOKEN_IDENTITY_MISMATCH")
    if protocol == "v2":
        outcomes = array("outcomes")
        if (not isinstance(outcomes, list) or len(outcomes) != 2
                or any(not isinstance(outcome, str) for outcome in outcomes)
                or dict(zip((outcome.casefold() for outcome in outcomes), ids))
                    != {"up": expected["YES"], "down": expected["NO"]}):
            raise ValueError("SETTLEMENT_V2_OUTCOME_MAPPING_MISMATCH")
        status = market.get("resolutionStatus")
    elif "version" in market:
        status = market.get("umaResolutionStatus") or market.get("resolutionStatus")
    else:
        status = market.get("resolutionStatus") or market.get("umaResolutionStatus")
    if market.get("closed") is not True or status != "resolved":
        return None
    prices = array("outcomePrices")
    if not isinstance(prices, list) or len(prices) != 2:
        raise ValueError("SETTLEMENT_PAYOUT_MISSING")
    try:
        prices = [Decimal(str(p)) for p in prices]
    except InvalidOperation as exc:
        raise ValueError("SETTLEMENT_PAYOUT_NOT_BINARY_FINAL") from exc
    if not all(p.is_finite() for p in prices) or sorted(prices) != [Decimal(0), Decimal(1)]:
        raise ValueError("SETTLEMENT_PAYOUT_NOT_BINARY_FINAL")
    winning_token = ids[prices.index(Decimal(1))]
    return {"slug": session["slug"], "condition_id": session["condition_id"],
            "token_ids": expected, "winner": next(k for k, v in expected.items() if v == winning_token),
            "source": BASE + session["slug"], "resolution_status": status,
            "closed": True, "outcome_prices": [str(p) for p in prices],
            **({"protocol_version": "v2", "position_ids": ids} if protocol == "v2" else {"clob_token_ids": ids}),
            "observed_at": time.time()}
