"""Gamma's default closed=false must not hide historical markets."""

from types import SimpleNamespace

import httpx
import pytest

from polymaker.rdp_sm import _Issue
from polymaker.rdp_sm_entry import exact_market
from test_rdp_sm_entry import OLD_CID


@pytest.mark.parametrize("is_closed", [True, False])
async def test_history_lookup_explicitly_checks_both_market_sets(is_closed):
    calls = []
    market = {"conditionId": OLD_CID, "closed": is_closed}

    def response(request):
        assert request.method == "GET" and request.url.path == "/markets"
        assert request.url.params["condition_ids"] == OLD_CID
        assert request.url.params["limit"] == "2"
        closed = request.url.params.get("closed", "false")
        calls.append(closed)
        rows = [market] if (closed == "true") == is_closed else []
        return httpx.Response(200, json=rows, request=request)

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(response)
    ) as client:
        result = await exact_market(SimpleNamespace(_client=client), condition_ids=OLD_CID)
    assert result == market
    assert calls == (["true"] if is_closed else ["true", "false"])


@pytest.mark.parametrize("rows", [[], [{"conditionId": OLD_CID}] * 2, {"unexpected": []}])
async def test_missing_duplicate_or_malformed_history_stays_blocked(rows):
    calls = []

    def response(request):
        calls.append(request.url.params.get("closed"))
        return httpx.Response(200, json=rows, request=request)

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(response)
    ) as client:
        with pytest.raises(_Issue, match="MARKET_IDENTITY_AMBIGUOUS"):
            await exact_market(SimpleNamespace(_client=client), condition_ids=OLD_CID)
    assert calls == (["true", "false"] if rows == [] else ["true"])


async def test_history_http_error_does_not_fall_back_as_if_market_were_absent():
    calls = []

    def response(request):
        calls.append(request.url.params.get("closed"))
        return httpx.Response(503, request=request)

    async with httpx.AsyncClient(
        base_url="https://gamma-api.polymarket.com", transport=httpx.MockTransport(response)
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await exact_market(SimpleNamespace(_client=client), condition_ids=OLD_CID)
    assert calls == ["true"]
