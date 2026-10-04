"""Polygon token-balance failover tests; no RPC traffic is sent."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import web3

from polymaker.config import Config
from polymaker.execution.gateway import ExecutionGateway
from polymaker.favorite_live import _account_baseline


def fake_rpc(monkeypatch, balances_by_host):
    calls = []
    events = []

    class FakeWeb3:
        @staticmethod
        def is_address(_address):
            return True

        @staticmethod
        def HTTPProvider(url, *, request_kwargs):
            calls.append((url, request_kwargs["timeout"]))
            return url

        @staticmethod
        def to_checksum_address(address):
            return address

        def __init__(self, url):
            self.url = url
            self.middleware_onion = SimpleNamespace(inject=lambda *_args, **_kwargs: None)
            self.eth = SimpleNamespace(contract=lambda **_kwargs: SimpleNamespace(
                functions=SimpleNamespace(balanceOf=self.balance_of)))

        def balance_of(self, _funder, token_id):
            def read():
                from urllib.parse import urlsplit

                response = balances_by_host[urlsplit(self.url).hostname]
                if isinstance(response, Exception):
                    raise response
                return response[token_id]

            return SimpleNamespace(call=read)

    monkeypatch.setattr(web3, "Web3", FakeWeb3)
    monkeypatch.setattr("polymaker.execution.gateway.log", SimpleNamespace(
        warning=lambda event, **fields: events.append((event, fields))))
    return calls, events


def gateway_with_rpc(url):
    cfg = Config()
    cfg.secrets.polygon_rpc = url
    gw = ExecutionGateway(cfg, paper=True)
    gw._funder = "0xFunder"
    return gw


async def test_token_balances_failover_returns_actual_balances_and_safe_log(monkeypatch):
    secret = "sensitive-api-key"
    url = f"https://user:{secret}@custom.example/rpc?key={secret}"
    calls, events = fake_rpc(monkeypatch, {
        "custom.example": TimeoutError(f"request failed: {url}"),
        "polygon-bor-rpc.publicnode.com": {1: 1_250_000, 2: 750_000},
    })
    gw = gateway_with_rpc(url)
    try:
        assert await gw.token_balances(["1", "2"]) == {"1": 1.25, "2": 0.75}
    finally:
        gw.close()
    assert calls == [(url, 5), ("https://polygon-bor-rpc.publicnode.com", 5)]
    assert events == [("token_balance_rpc_failed", {
        "host": "custom.example", "error_type": "TimeoutError"})]
    assert secret not in repr(events)
    assert url not in repr(events)


async def test_token_balances_all_rpcs_fail_closed(monkeypatch):
    calls, events = fake_rpc(monkeypatch, {
        "polygon-bor-rpc.publicnode.com": TimeoutError("timed out"),
        "polygon.llamarpc.com": ConnectionError("unavailable"),
        "rpc.ankr.com": RuntimeError("failed"),
    })
    gw = gateway_with_rpc("https://polygon-bor-rpc.publicnode.com")
    try:
        assert await gw.token_balances(["1", "2"]) is None
    finally:
        gw.close()
    assert len(calls) == 3  # configured default is deduplicated
    assert all(timeout == 5 for _, timeout in calls)
    assert [fields["host"] for _, fields in events] == [
        "polygon-bor-rpc.publicnode.com", "polygon.llamarpc.com", "rpc.ankr.com"]
    assert [fields["error_type"] for _, fields in events] == [
        "TimeoutError", "ConnectionError", "RuntimeError"]


async def test_token_balances_zero_is_valid_read(monkeypatch):
    calls, events = fake_rpc(monkeypatch, {
        "polygon-bor-rpc.publicnode.com": {1: 0, 2: 0},
    })
    gw = gateway_with_rpc("https://polygon-bor-rpc.publicnode.com")
    try:
        assert await gw.token_balances(["1", "2"]) == {"1": 0.0, "2": 0.0}
    finally:
        gw.close()
    assert calls == [("https://polygon-bor-rpc.publicnode.com", 5)]
    assert events == []


async def test_preflight_baseline_uses_data_api_without_rpc(meta):
    gateway = SimpleNamespace(
        positions=AsyncMock(return_value={}),
        token_balances=AsyncMock(side_effect=AssertionError("RPC MUST NOT BE CALLED")),
    )
    engine = SimpleNamespace(gateway=gateway)
    assert await _account_baseline(engine, meta) == (0.0, 0.0)
    gateway.positions.assert_awaited_once_with()
    gateway.token_balances.assert_not_awaited()
