"""Public Polymarket GETs only, with connection and official-route failover."""

import httpx

from polymaker.dashboard.settlement import validate_resolution
from polymaker.rdp_sm_m1_observe import PublicClob, public_market


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport=None):
        self.transport = transport or httpx.AsyncHTTPTransport(http2=False, retries=0)

    async def handle_async_request(self, request):
        if (request.method != "GET" or request.url.scheme != "https"
                or request.url.host not in {"clob.polymarket.com", "gamma-api.polymarket.com"}):
            raise RuntimeError("M1_VIRTUAL_NETWORK_WRITE_OR_HOST_DENIED")
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
                raise ValueError("M1_PUBLIC_MARKET_IDENTITY_UNAVAILABLE")
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
                raise ValueError("M1_PUBLIC_MARKET_IDENTITY_INVALID") from exc
            return rows[0]

    async def market(self, slug):
        return public_market(await self.raw_market(slug), slug)

    async def resolution(self, market):
        return validate_resolution(await self.raw_market(market["slug"], purpose="settlement"), market)
