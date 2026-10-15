"""Selected-market discovery never looks up another Session or resolution."""

from types import SimpleNamespace

import httpx
import pytest

from polymaker.rdp_sm import _Issue
from polymaker.rdp_sm_entry import exact_market
from test_rdp_sm_m1 import CID, SLUG


@pytest.mark.parametrize("is_closed", [True, False])
async def test_selected_market_lookup_has_no_historical_fallback(is_closed):
    calls = []
    market = {"conditionId": CID, "slug": SLUG, "closed": is_closed}

    def response(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=[market], request=request)

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(response)
    ) as client:
        result = await exact_market(SimpleNamespace(_client=client), slug=SLUG)
    assert result == market
    assert calls == [{"slug": SLUG, "limit": "2"}]


@pytest.mark.parametrize("rows", [[], [{"conditionId": CID}] * 2, {"unexpected": []}])
async def test_missing_duplicate_or_malformed_current_market_stays_blocked(rows):
    calls = []

    def response(request):
        calls.append(request.url.params.get("closed"))
        return httpx.Response(200, json=rows, request=request)

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(response)
    ) as client:
        with pytest.raises(_Issue, match="MARKET_NOT_AVAILABLE|MARKET_IDENTITY_AMBIGUOUS"):
            await exact_market(SimpleNamespace(_client=client), slug=SLUG)
    assert calls == [None]


async def test_current_market_http_error_does_not_select_another_market():
    calls = []

    def response(request):
        calls.append(request.url.params.get("closed"))
        return httpx.Response(503, request=request)

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(response)
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await exact_market(SimpleNamespace(_client=client), slug=SLUG)
    assert calls == [None]
