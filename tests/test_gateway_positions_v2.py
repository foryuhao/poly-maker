"""Data API v2 positions snapshot adapter tests (no network access)."""

import httpx
import pytest

from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway, GatewayReadError


def page(data, cursor=None):
    return {"data": data, "pagination": {"limit": 100, "offset": 0,
                                           "has_more": cursor is not None,
                                           "next_cursor": cursor}}


def row(token, size, avg):
    return {"token_id": token, "current_size": size, "avg_price": avg}


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, *, params):
        self.calls.append((url, params.copy()))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def install_http(monkeypatch, responses):
    fake = FakeHttp(responses)
    monkeypatch.setattr("polymaker.execution.gateway.httpx.AsyncClient",
                        lambda **_kwargs: fake)
    return fake


def gateway(paper=False):
    gw = ExecutionGateway(Config(), paper=paper)
    gw._funder = "0xLiveFunder"
    return gw


async def test_v2_empty_response_is_authoritative_empty(monkeypatch):
    fake = install_http(monkeypatch, [FakeResponse(page([]))])
    gw = gateway()
    assert await gw.positions() == {}
    assert fake.calls == [("https://data-api.polymarket.com/v2/positions",
                           {"user": "0xLiveFunder"})]
    gw.close()


async def test_v2_single_position_maps_to_existing_representation(monkeypatch):
    install_http(monkeypatch, [FakeResponse(page([row("tok-a", 12.5, 0.42)]))])
    gw = gateway()
    assert await gw.positions() == {"tok-a": (12.5, 0.42)}
    gw.close()


async def test_v2_multiple_positions_map_to_existing_representation(monkeypatch):
    install_http(monkeypatch, [FakeResponse(page([row("tok-a", 2, .1),
                                                   row("tok-b", 3, .8)]))])
    gw = gateway()
    assert await gw.positions() == {"tok-a": (2.0, .1), "tok-b": (3.0, .8)}
    gw.close()


async def test_v2_pagination_repeats_user_and_follows_cursor(monkeypatch):
    fake = install_http(monkeypatch, [
        FakeResponse(page([row("tok-a", 2, .1)], "opaque-cursor")),
        FakeResponse(page([row("tok-b", 3, .8)])),
    ])
    gw = gateway()
    assert await gw.positions() == {"tok-a": (2.0, .1), "tok-b": (3.0, .8)}
    assert fake.calls == [
        ("https://data-api.polymarket.com/v2/positions", {"user": "0xLiveFunder"}),
        ("https://data-api.polymarket.com/v2/positions",
         {"user": "0xLiveFunder", "cursor": "opaque-cursor"}),
    ]
    gw.close()


async def test_later_page_failure_invalidates_whole_snapshot(monkeypatch):
    install_http(monkeypatch, [
        FakeResponse(page([row("tok-a", 2, .1)], "next")),
        FakeResponse(error=httpx.HTTPStatusError("503", request=httpx.Request("GET", "https://x"),
                                                  response=httpx.Response(503))),
    ])
    gw = gateway()
    with pytest.raises(GatewayReadError):
        await gw.positions()
    gw.close()


@pytest.mark.parametrize("status", [429, 503])
async def test_v2_http_failures_raise_gateway_read_error(monkeypatch, status):
    install_http(monkeypatch, [FakeResponse(error=httpx.HTTPStatusError(
        str(status), request=httpx.Request("GET", "https://x"),
        response=httpx.Response(status)))])
    gw = gateway()
    with pytest.raises(GatewayReadError):
        await gw.positions()
    gw.close()


@pytest.mark.parametrize("error", [httpx.TimeoutException("timeout"),
                                   httpx.ConnectError("transport")])
async def test_v2_timeout_and_transport_errors_raise_gateway_read_error(monkeypatch, error):
    install_http(monkeypatch, [error])
    gw = gateway()
    with pytest.raises(GatewayReadError):
        await gw.positions()
    gw.close()


@pytest.mark.parametrize("payload", [[], {}, {"data": []},
                                      {"data": [], "pagination": {}},
                                      {"data": {}, "pagination": {"next_cursor": None}}])
async def test_v2_malformed_envelope_raises_gateway_read_error(monkeypatch, payload):
    install_http(monkeypatch, [FakeResponse(payload)])
    gw = gateway()
    with pytest.raises(GatewayReadError):
        await gw.positions()
    gw.close()


@pytest.mark.parametrize("bad_row", [None, {}, {"token_id": "tok", "current_size": 1},
                                      {"token_id": "", "current_size": 1, "avg_price": .5},
                                      {"token_id": "tok", "current_size": "bad", "avg_price": .5},
                                      {"token_id": "tok", "current_size": False, "avg_price": .5},
                                      {"token_id": "tok", "current_size": 1, "avg_price": True}])
async def test_v2_malformed_position_row_raises_gateway_read_error(monkeypatch, bad_row):
    install_http(monkeypatch, [FakeResponse(page([bad_row]))])
    gw = gateway()
    with pytest.raises(GatewayReadError):
        await gw.positions()
    gw.close()


async def test_paper_positions_remain_empty_without_http(monkeypatch):
    def unexpected_client(**_kwargs):
        raise AssertionError("paper positions must not make an HTTP request")

    monkeypatch.setattr("polymaker.execution.gateway.httpx.AsyncClient", unexpected_client)
    gw = gateway(paper=True)
    assert await gw.positions() == {}
    gw.close()
