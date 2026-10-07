from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import FrozenInstanceError
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import respx

from polymaker.config import Config, Secrets, WalletConfig
from polymaker.domain import MarketMeta, OpenOrder, Side, TokenMeta
from polymaker.execution.gateway import (
    ExactOpenOrder,
    ExecutionGateway,
    GatewayReadError,
)
from polymaker.rdp_account import RDPAccountError, RDPAccountSnapshot, _collateral, account_snapshot
from polymaker.rdp_live import RDPBootstrap, TokenBookMetadata

PRIVATE_KEY = "0x" + "1" * 64


def account_config(**changes):
    from eth_account import Account

    signer = Account.from_key(PRIVATE_KEY).address
    cfg = Config(secrets=Secrets(PK=PRIVATE_KEY, BROWSER_ADDRESS=signer),
                 wallet=WalletConfig(signature_type=0))
    for key, value in changes.items():
        setattr(cfg.wallet, key, value)
    return cfg, signer


def account_bootstrap(*, end_epoch=None):
    from datetime import UTC, datetime

    end_epoch = end_epoch or int(time.time()) + 120
    market = MarketMeta(
        condition_id="condition-account", question="BTC Up or Down", slug="btc-updown-5m-2000000100",
        tokens=(TokenMeta("up", "Up"), TokenMeta("down", "Down")), tick_size=.01,
        neg_risk=False, min_order_size=5, rewards_min_size=0, rewards_max_spread=0,
        rewards_daily_rate=0, maker_fee_bps=0, taker_fee_bps=0, fees_enabled=False,
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat(), event_id=None,
    )
    return RDPBootstrap(market, end_epoch - 300, end_epoch,
                        (TokenBookMetadata("Up", "up", .01, 5, False),
                         TokenBookMetadata("Down", "down", .01, 5, False)), time.time())


class FakeGateway:
    def __init__(self, cfg, *, paper):
        self.cfg = cfg
        self.paper = paper
        self.address = cfg.secrets.browser_address
        self.funder = cfg.secrets.browser_address
        self.creds = SimpleNamespace(api_key="key", api_secret="secret", api_passphrase="passphrase")
        self.closed = False
        self.calls = []
        self.connect_wait = None
        self.orders_value = [
            ExactOpenOrder("selected-order", "up", Side.BUY, Decimal(".42"), Decimal("3")),
            ExactOpenOrder("other-order", "foreign", Side.SELL, Decimal(".77"), Decimal("2")),
        ]
        self.positions_value = {"up": (2.5, .4)}
        self.collateral_value = {
            "balance": "12345678",
            "allowances": {"0x" + "a" * 40: "9000001"},
        }
        self.spenders_value = ("0x" + "A" * 40,)

    async def connect(self, *, derive_only=False):
        self.calls.append(("connect", derive_only))
        if self.connect_wait is not None:
            await self.connect_wait.wait()

    async def open_orders(self, *, strict_account=False, exact_decimal=False):
        self.calls.append(("open_orders", strict_account, exact_decimal))
        assert strict_account is True
        assert exact_decimal is True
        if isinstance(self.orders_value, Exception):
            raise self.orders_value
        return self.orders_value

    async def positions(self, *, strict_account=False):
        self.calls.append(("positions", strict_account))
        assert strict_account is True
        if isinstance(self.positions_value, Exception):
            raise self.positions_value
        return self.positions_value

    async def required_collateral_spenders(self, _market):
        self.calls.append(("spenders",))
        return self.spenders_value

    async def balance_allowance(self):
        self.calls.append(("balance_allowance",))
        return self.collateral_value

    def close(self):
        self.closed = True

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_valid_account_snapshot_reuses_reads_and_preserves_selected_state():
    cfg, signer = account_config()
    made = []

    def factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    snapshot = await account_snapshot(cfg, account_bootstrap(), gateway_factory=factory)
    assert isinstance(snapshot, RDPAccountSnapshot)
    assert snapshot.signer == signer == snapshot.funder
    assert snapshot.balance_usd == Decimal("12.345678")
    assert snapshot.allowance_usd == Decimal("9.000001")
    assert snapshot.required_spenders == ("0x" + "A" * 40,)
    assert [(row.token_id, row.quantity, row.average_price) for row in snapshot.selected_positions] == [
        ("up", Decimal("2.5"), Decimal("0.4")), ("down", Decimal("0"), Decimal("0"))]
    assert [row.order_id for row in snapshot.selected_open_orders] == ["selected-order"]
    assert snapshot.total_open_order_count == 2
    assert snapshot.condition_id == "condition-account"
    assert [row.order_id for row in snapshot.all_open_orders] == ["selected-order", "other-order"]
    assert snapshot.orders_receipt_ts <= snapshot.positions_receipt_ts <= snapshot.collateral_receipt_ts
    assert made[0].calls == [("connect", True), ("open_orders", True, True), ("positions", True),
                             ("spenders",), ("balance_allowance",)]
    assert made[0].closed
    with pytest.raises(FrozenInstanceError):
        snapshot.balance_usd = Decimal("0")


@pytest.mark.asyncio
async def test_account_config_is_captured_before_await_and_requests_stay_official():
    cfg, _signer = account_config()
    made = []

    def factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    async def mutate_during_connect(self, *, derive_only=False):
        self.calls.append(("connect", derive_only))
        cfg.wallet.data_api_host = "https://evil.example"
        cfg.wallet.clob_host = "https://evil.example"

    original_factory = factory

    def mutating_factory(config, *, paper):
        gateway = original_factory(config, paper=paper)
        gateway.connect = lambda **kwargs: mutate_during_connect(gateway, **kwargs)
        return gateway

    await account_snapshot(cfg, account_bootstrap(), gateway_factory=mutating_factory)
    assert made[0].cfg.wallet.clob_host == "https://clob.polymarket.com"
    assert made[0].cfg.wallet.data_api_host == "https://data-api.polymarket.com"
    assert cfg.wallet.clob_host == "https://evil.example"


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "value", "category"), [
    ("clob_host", "https://clob.polymarket.com.evil.test", "UNAPPROVED_ACCOUNT_ORIGIN"),
    ("data_api_host", "http://data-api.polymarket.com", "UNAPPROVED_ACCOUNT_ORIGIN"),
    ("signature_type", 9, "UNSUPPORTED_SIGNATURE_TYPE"),
])
async def test_invalid_config_rejected_before_gateway_connect(field, value, category):
    cfg, _signer = account_config(**{field: value})
    created = []

    def factory(*_args, **_kwargs):
        created.append(True)
        return None

    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(), gateway_factory=factory)
    assert caught.value.category == category
    assert created == []


@pytest.mark.asyncio
async def test_missing_wallet_fails_before_gateway_creation():
    cfg = Config(secrets=Secrets(_env_file=None))
    created = []
    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(),
                               gateway_factory=lambda *_a, **_k: created.append(True))
    assert caught.value.category == "ACCOUNT_IDENTITY_MISSING"
    assert created == []


@pytest.mark.parametrize("raw,spenders,category", [
    ({"balance": True, "allowances": {"spender": "1"}}, ("spender",), "INVALID_ACCOUNT_EVIDENCE"),
    ({"balance": "-1", "allowances": {"spender": "1"}}, ("spender",), "INVALID_ACCOUNT_EVIDENCE"),
    ({"balance": "NaN", "allowances": {"spender": "1"}}, ("spender",), "INVALID_ACCOUNT_EVIDENCE"),
    ({"balance": "1", "allowances": {"spender": True}}, ("spender",), "INVALID_ACCOUNT_EVIDENCE"),
    ({"balance": "1", "allowances": {"spender": "-1"}}, ("spender",), "INVALID_ACCOUNT_EVIDENCE"),
    ({"balance": "1", "allowances": {"spender": "Infinity"}}, ("spender",), "INVALID_ACCOUNT_EVIDENCE"),
    ({"allowances": {"spender": "1"}}, ("spender",), "COLLATERAL_UNAVAILABLE"),
    ({"balance": "1"}, ("spender",), "ALLOWANCE_UNAVAILABLE"),
    ({"balance": "1", "allowances": {}}, ("spender",), "ALLOWANCE_UNAVAILABLE"),
    ({"balance": "1", "allowances": {"SPENDER": "1", "spender": "2"}},
     ("spender",), "CONTRADICTORY_ALLOWANCE_EVIDENCE"),
    ({"balance": "1", "allowances": {"other": "1"}}, ("spender",), "ALLOWANCE_UNAVAILABLE"),
])
def test_collateral_rejects_invalid_balance_and_allowance_evidence(raw, spenders, category):
    with pytest.raises(RDPAccountError) as caught:
        _collateral(raw, spenders)
    assert caught.value.category == category


def test_micro_usd_conversion_is_exact_beyond_default_decimal_precision():
    balance, allowance = _collateral(
        {"balance": "123456789012345678901234567890",
         "allowances": {"spender": "123456789012345678901234567890"}},
        ("spender",),
    )
    expected = Decimal("123456789012345678901234.567890")
    assert balance == expected and allowance == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("read", ["orders", "positions", "balance"])
async def test_account_read_errors_fail_closed_and_gateway_closes(read):
    cfg, _signer = account_config()
    made = []

    def factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        if read == "orders":
            gateway.orders_value = GatewayReadError("secret text must not escape")
        elif read == "positions":
            gateway.positions_value = GatewayReadError("secret text must not escape")
        else:
            gateway.collateral_value = {}
        made.append(gateway)
        return gateway

    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(), gateway_factory=factory)
    assert caught.value.category in ("ACCOUNT_READ_FAILED", "COLLATERAL_UNAVAILABLE",
                                     "OPEN_ORDERS_UNAVAILABLE")
    assert "secret text" not in str(caught.value)
    assert made[0].closed


@pytest.mark.asyncio
async def test_expiry_and_cancellation_close_gateway():
    cfg, _signer = account_config()
    made = []

    def factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(end_epoch=int(time.time()) - 1), gateway_factory=factory)
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert made == []

    cfg, _signer = account_config()
    entered = asyncio.Event()

    def blocking_factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.connect_wait = asyncio.Event()
        gateway.entered = entered
        async def connect(*, derive_only=False):
            gateway.calls.append(("connect", derive_only))
            entered.set()
            await gateway.connect_wait.wait()
        gateway.connect = connect
        made.append(gateway)
        return gateway

    task = asyncio.create_task(account_snapshot(cfg, account_bootstrap(), gateway_factory=blocking_factory))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert made[-1].closed


@pytest.mark.asyncio
async def test_market_expiry_during_sequential_reads_fails_and_closes():
    cfg, _signer = account_config()
    made = []

    def factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        made.append(gateway)
        return gateway

    end = int(time.time()) + 20
    times = iter([end - 2, end - 1, end, end])
    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(end_epoch=end), now=lambda: next(times),
                              gateway_factory=factory)
    assert caught.value.category == "MARKET_INTERVAL_ELAPSED"
    assert made[0].closed
    assert ("balance_allowance",) not in made[0].calls


@pytest.mark.asyncio
async def test_missing_derived_credentials_and_identity_mismatch_fail_closed():
    cfg, _signer = account_config()
    made = []

    def incomplete(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.creds = SimpleNamespace(api_key="", api_secret="s", api_passphrase="p")
        made.append(gateway)
        return gateway

    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(), gateway_factory=incomplete)
    assert caught.value.category == "DERIVED_CREDENTIALS_INCOMPLETE"
    assert made[-1].closed

    def mismatch(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        gateway.funder = "0x" + "2" * 40
        made.append(gateway)
        return gateway

    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(), gateway_factory=mismatch)
    assert caught.value.category == "ACCOUNT_IDENTITY_MISMATCH"
    assert made[-1].calls == [("connect", True)] and made[-1].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,category", [
    ("spenders", "REQUIRED_SPENDERS_UNRESOLVED"),
    ("invalid_spender", "REQUIRED_SPENDERS_UNRESOLVED"),
    ("duplicate_spenders", "REQUIRED_SPENDERS_UNRESOLVED"),
    ("allowance", "ALLOWANCE_UNAVAILABLE"),
])
async def test_missing_required_spender_or_allowance_fails_and_closes(failure, category):
    cfg, _signer = account_config()
    made = []

    def factory(config, *, paper):
        gateway = FakeGateway(config, paper=paper)
        if failure == "spenders":
            gateway.spenders_value = ()
        elif failure == "invalid_spender":
            gateway.spenders_value = ("not-an-address",)
        elif failure == "duplicate_spenders":
            gateway.spenders_value = ("0x" + "a" * 40, "0x" + "A" * 40)
        else:
            gateway.collateral_value = {"balance": "12000000", "allowances": {}}
        made.append(gateway)
        return gateway

    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(), gateway_factory=factory)
    assert caught.value.category == category
    assert made[0].closed


@pytest.mark.asyncio
async def test_signature_type_zero_config_identity_mismatch_fails_before_gateway():
    cfg, _signer = account_config()
    cfg.secrets.browser_address = "0x" + "2" * 40
    made = []

    def factory(*_args, **_kwargs):
        made.append(True)
        return None

    with pytest.raises(RDPAccountError) as caught:
        await account_snapshot(cfg, account_bootstrap(), gateway_factory=factory)
    assert caught.value.category == "ACCOUNT_IDENTITY_MISMATCH"
    assert made == []


@pytest.mark.asyncio
async def test_gateway_derive_only_uses_derive_and_default_keeps_create_or_derive(monkeypatch):
    from py_clob_client_v2 import client as sdk_client

    cfg, signer = account_config()
    calls = []
    creds = SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p")

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs["host"], kwargs["signature_type"]))
        def derive_api_key(self):
            calls.append(("derive",))
            return creds
        def create_or_derive_api_key(self):
            calls.append(("create_or_derive",))
            return creds
        def create_api_key(self):
            raise AssertionError("derive-only must never create credentials")
        def set_api_creds(self, value):
            assert value is creds
        def get_address(self):
            return signer

    monkeypatch.setattr(sdk_client, "ClobClient", FakeClient)
    async def no_clock():
        return None
    gateway = ExecutionGateway(cfg)
    gateway._check_clock_drift = no_clock
    try:
        await gateway.connect(derive_only=True)
        assert ("derive",) in calls and ("create_or_derive",) not in calls
    finally:
        gateway.close()

    calls.clear()
    gateway = ExecutionGateway(cfg)
    gateway._check_clock_drift = no_clock
    try:
        await gateway.connect()
        assert ("create_or_derive",) in calls and ("derive",) not in calls
    finally:
        gateway.close()


@pytest.mark.asyncio
async def test_gateway_derive_failure_never_falls_back_to_creation(monkeypatch):
    from py_clob_client_v2 import client as sdk_client

    cfg, signer = account_config()
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def derive_api_key(self):
            calls.append("derive")
            raise RuntimeError("credential-sensitive error")
        def create_or_derive_api_key(self):
            calls.append("create")
            raise AssertionError("must not fall back")
        def set_api_creds(self, _creds): pass
        def get_address(self): return signer

    monkeypatch.setattr(sdk_client, "ClobClient", FakeClient)
    gateway = ExecutionGateway(cfg)
    gateway._check_clock_drift = lambda: asyncio.sleep(0)
    try:
        with pytest.raises(RuntimeError):
            await gateway.connect(derive_only=True)
        assert calls == ["derive"]
    finally:
        gateway.close()


@pytest.mark.asyncio
async def test_gateway_incomplete_derived_credentials_fail_without_creation(monkeypatch):
    from py_clob_client_v2 import client as sdk_client

    cfg, signer = account_config()
    calls = []

    class FakeClient:
        def __init__(self, **_kwargs): pass
        def derive_api_key(self):
            calls.append("derive")
            return SimpleNamespace(api_key="k", api_secret="", api_passphrase="p")
        def create_or_derive_api_key(self):
            calls.append("create")
            raise AssertionError("must not fall back")
        def create_api_key(self):
            calls.append("create_api_key")
            raise AssertionError("must not create credentials")
        def set_api_creds(self, _creds): pass
        def get_address(self): return signer

    monkeypatch.setattr(sdk_client, "ClobClient", FakeClient)
    gateway = ExecutionGateway(cfg)
    try:
        with pytest.raises(RuntimeError, match="credentials are incomplete"):
            await gateway.connect(derive_only=True)
        assert calls == ["derive"]
    finally:
        gateway.close()


class ActualParserGateway(ExecutionGateway):
    def __init__(self, config, signer, order_rows):
        super().__init__(config)
        self._test_signer = signer
        self._order_rows = order_rows
        self.closed_for_test = False

    async def connect(self, *, derive_only=False):
        assert derive_only is True
        self._derive_only = True
        self._address = self._test_signer
        self._funder = self._test_signer
        self._creds = SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p")

        class ReadOnlySDK:
            def get_open_orders(inner_self): return inner_self.owner._order_rows
        client = ReadOnlySDK()
        client.owner = self
        self._client = client

        async def inline(fn, *args):
            return fn(*args)
        self._io = inline

    async def required_collateral_spenders(self, _market):
        return ("0x" + "a" * 40,)

    async def balance_allowance(self):
        return {"balance": "10000000", "allowances": {"0x" + "a" * 40: "10000000"}}

    def close(self):
        self.closed_for_test = True
        super().close()


def positions_response(rows):
    return {"data": rows, "pagination": {"next_cursor": None}}


@pytest.mark.asyncio
async def test_actual_gateway_order_parser_requires_quantities_only_in_strict_mode():
    cfg, signer = account_config()
    malformed_rows = [
        {"id": "missing-original", "asset_id": "up", "side": "BUY", "price": "0.42",
         "size_matched": "0"},
        {"id": "missing-matched", "asset_id": "up", "side": "BUY", "price": "0.42",
         "original_size": "3"},
    ]
    gateway = ExecutionGateway(cfg)
    gateway._client = SimpleNamespace(get_open_orders=lambda: malformed_rows[:1])
    async def inline(fn, *args): return fn(*args)
    gateway._io = inline
    try:
        # Default legacy behavior remains unchanged for existing callers.
        legacy = await gateway.open_orders()
        assert legacy[0].size == 0
        with pytest.raises(GatewayReadError):
            await gateway.open_orders(strict_account=True)
    finally:
        gateway.close()

    for malformed in malformed_rows:
        strict_gateway = ActualParserGateway(cfg, signer, [malformed])
        with respx.mock(assert_all_called=False), pytest.raises(RDPAccountError) as caught:
            await account_snapshot(cfg, account_bootstrap(), gateway_factory=lambda *_a, _gateway=strict_gateway, **_k: _gateway)
        assert caught.value.category == "ACCOUNT_READ_FAILED"
        assert strict_gateway.closed_for_test


@pytest.mark.asyncio
async def test_gateway_exact_order_mode_preserves_decimal_remaining_and_legacy_float_results():
    cfg, _signer = account_config()
    row = {"id": "partial", "asset_id": "foreign", "side": "BUY", "price": "0.5",
           "original_size": "1.1", "size_matched": "0.2"}
    gateway = ExecutionGateway(cfg)
    gateway._client = SimpleNamespace(get_open_orders=lambda: [row])

    async def inline(fn, *args):
        return fn(*args)

    gateway._io = inline
    try:
        legacy = await gateway.open_orders(strict_account=True)
        exact = await gateway.open_orders(strict_account=True, exact_decimal=True)
        assert isinstance(legacy[0], OpenOrder)
        assert legacy[0].size == .9000000000000001
        assert isinstance(exact[0], ExactOpenOrder)
        assert exact[0].price == Decimal("0.5")
        assert exact[0].remaining_size == Decimal("0.9")
        with pytest.raises(GatewayReadError, match="require strict"):
            await gateway.open_orders(exact_decimal=True)
    finally:
        gateway.close()


@pytest.mark.asyncio
async def test_gateway_exact_order_mode_accepts_decimal_and_integer_source_numerics():
    cfg, _signer = account_config()
    row = {"id": "decimal-int", "asset_id": "foreign", "side": "BUY",
           "price": Decimal("0.4"), "original_size": 2, "size_matched": Decimal("0.25")}
    gateway = ExecutionGateway(cfg)
    gateway._client = SimpleNamespace(get_open_orders=lambda: [row])

    async def inline(fn, *args):
        return fn(*args)

    gateway._io = inline
    try:
        exact = await gateway.open_orders(strict_account=True, exact_decimal=True)
        assert exact[0].price == Decimal("0.4")
        assert exact[0].remaining_size == Decimal("1.75")
    finally:
        gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [
    {"id": "missing-original", "asset_id": "up", "side": "BUY", "price": "0.5",
     "size_matched": "0"},
    {"id": "missing-matched", "asset_id": "up", "side": "BUY", "price": "0.5",
     "original_size": "1"},
    {"id": "float-source", "asset_id": "up", "side": "BUY", "price": .5,
     "original_size": "1", "size_matched": "0"},
    {"id": "bool-source", "asset_id": "up", "side": "BUY", "price": True,
     "original_size": "1", "size_matched": "0"},
    {"id": "nan-source", "asset_id": "up", "side": "BUY", "price": "NaN",
     "original_size": "1", "size_matched": "0"},
    {"id": "negative", "asset_id": "up", "side": "BUY", "price": "0.5",
     "original_size": "1", "size_matched": "-0.1"},
    {"id": "overmatched", "asset_id": "up", "side": "BUY", "price": "0.5",
     "original_size": "1", "size_matched": "1.1"},
])
async def test_gateway_exact_order_mode_rejects_untrustworthy_source_fields(row):
    cfg, _signer = account_config()
    gateway = ExecutionGateway(cfg)
    gateway._client = SimpleNamespace(get_open_orders=lambda: [row])

    async def inline(fn, *args):
        return fn(*args)

    gateway._io = inline
    try:
        with pytest.raises(GatewayReadError):
            await gateway.open_orders(strict_account=True, exact_decimal=True)
    finally:
        gateway.close()


@pytest.mark.asyncio
async def test_actual_gateway_position_parser_checks_zero_size_price_before_filter():
    cfg, signer = account_config()
    gateway = ActualParserGateway(cfg, signer, [])
    raw = positions_response([{"token_id": "up", "current_size": "0", "avg_price": "-1"}])
    with respx.mock(assert_all_called=True) as router:
        router.get("https://data-api.polymarket.com/v2/positions").mock(
            return_value=httpx.Response(200, json=raw))
        with pytest.raises(RDPAccountError) as caught:
            await account_snapshot(cfg, account_bootstrap(), gateway_factory=lambda *_a, **_k: gateway)
    assert caught.value.category == "ACCOUNT_READ_FAILED"
    assert gateway.closed_for_test

    # The existing default reader keeps its prior zero-row filtering behavior.
    legacy = ExecutionGateway(cfg)
    legacy._funder = signer
    with respx.mock(assert_all_called=True) as router:
        router.get("https://data-api.polymarket.com/v2/positions").mock(
            return_value=httpx.Response(200, json=raw))
        assert await legacy.positions() == {}
    legacy.close()

    valid_zero = ActualParserGateway(cfg, signer, [])
    valid_raw = positions_response([{"token_id": "up", "current_size": "0", "avg_price": "0.4"}])
    with respx.mock(assert_all_called=True) as router:
        router.get("https://data-api.polymarket.com/v2/positions").mock(
            return_value=httpx.Response(200, json=valid_raw))
        snapshot = await account_snapshot(cfg, account_bootstrap(),
                                          gateway_factory=lambda *_a, **_k: valid_zero)
    assert snapshot.selected_positions[0].quantity == Decimal("0")
    assert snapshot.selected_positions[0].average_price == Decimal("0.4")


@pytest.mark.asyncio
@pytest.mark.parametrize(("original", "matched", "balance", "allowance", "eligible", "reason"), [
    ("1.1", "0.2", "30450000", "30450000", True, None),
    ("1.1", "0.2", "30449999", "30450000", False, "INSUFFICIENT_CASH_BACKING"),
    ("1.1", "0.2", "30450000", "30449999", False, "INSUFFICIENT_ALLOWANCE_BACKING"),
    ("1.123456789123456789", "0.223456789123456789",
     "30450000", "30450000", True, None),
])
async def test_actual_parser_account_capture_and_backing_preserve_partial_fill_decimals(
    original, matched, balance, allowance, eligible, reason,
):
    from polymaker.risk.rdp_capital import evaluate_initial_rdp_backing

    cfg, signer = account_config()
    row = {"id": "foreign-partial", "asset_id": "foreign", "side": "BUY", "price": "0.5",
           "original_size": original, "size_matched": matched}
    gateway = ActualParserGateway(cfg, signer, [row])

    async def collateral():
        spender = "0x" + "a" * 40
        return {"balance": balance, "allowances": {spender: allowance}}

    gateway.balance_allowance = collateral
    with respx.mock(assert_all_called=True) as router:
        router.get("https://data-api.polymarket.com/v2/positions").mock(
            return_value=httpx.Response(200, json=positions_response([])))
        snapshot = await account_snapshot(
            cfg, account_bootstrap(),
            gateway_factory=lambda *_args, _gateway=gateway, **_kwargs: _gateway,
        )
    captured = snapshot.all_open_orders[0]
    assert captured.price == Decimal("0.5")
    assert captured.remaining_size == Decimal("0.9")
    assert snapshot.total_open_order_count == 1
    now = time.time()
    backing = evaluate_initial_rdp_backing(
        account_bootstrap(end_epoch=int(now) + 120), snapshot, now=now, max_age_s=15,
    )
    assert backing.eligible is eligible
    assert backing.reason == reason
    assert backing.account_buy_commitment == Decimal("0.45")
    assert backing.observed_cash_backing == Decimal(balance) / Decimal("1000000") - Decimal("0.45")


@pytest.mark.asyncio
async def test_account_cancellation_drains_owned_executor_without_blocking_event_loop():
    cfg, signer = account_config()
    started = threading.Event()
    release = threading.Event()

    class BlockingGateway(ExecutionGateway):
        def __init__(self, config, *, paper):
            super().__init__(config, paper=paper)
            self._address = signer
            self._funder = signer
            self._creds = SimpleNamespace(api_key="k", api_secret="s", api_passphrase="p")
            self.closed_for_test = False

        async def connect(self, *, derive_only=False):
            assert derive_only is True
            self._derive_only = True
            self._client = SimpleNamespace(get_open_orders=self._read_orders)

        def _read_orders(self):
            started.set()
            assert release.wait(timeout=3)
            return []

        async def required_collateral_spenders(self, _market):
            raise AssertionError("cancelled read must not advance to collateral")

        def close(self):
            self.closed_for_test = True
            super().close()

    gateways = []

    def factory(config, *, paper):
        gateway = BlockingGateway(config, paper=paper)
        gateways.append(gateway)
        return gateway

    task = asyncio.create_task(account_snapshot(cfg, account_bootstrap(), gateway_factory=factory))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done(), "account cleanup must wait for the already-running SDK read"
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done(), "repeated cancellation must not detach the owned executor read"
    assert not gateways[0].closed_for_test
    assert gateways[0]._account_io
    event_loop_responsive = asyncio.Event()
    asyncio.get_running_loop().call_soon(event_loop_responsive.set)
    await asyncio.wait_for(event_loop_responsive.wait(), timeout=0.2)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gateways[0].closed_for_test
    assert not gateways[0]._account_io


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["response_body", "request_exception"])
async def test_sdk_raw_diagnostics_are_suppressed_only_for_derive_only_worker(failure, monkeypatch, caplog):
    from py_clob_client_v2.http_helpers import helpers

    sentinel = "ACCOUNT_SECRET_SENTINEL"

    class FakeHTTP:
        def request(self, **_kwargs):
            if failure == "request_exception":
                raise httpx.ConnectError(sentinel)
            return SimpleNamespace(status_code=503, text=sentinel)

    monkeypatch.setattr(helpers, "_http_client", FakeHTTP())
    cfg, _signer = account_config()
    logger = logging.getLogger("py_clob_client_v2.http_helpers.helpers")
    caplog.set_level(logging.ERROR, logger=logger.name)

    strict = ExecutionGateway(cfg)
    strict._derive_only = True
    try:
        with pytest.raises(helpers.PolyApiException):
            await strict._io(helpers.get, "https://clob.polymarket.com/test")
    finally:
        await strict.aclose()
    assert sentinel not in caplog.text

    caplog.clear()
    legacy = ExecutionGateway(cfg)
    try:
        with pytest.raises(helpers.PolyApiException):
            await legacy._io(helpers.get, "https://clob.polymarket.com/test")
    finally:
        legacy.close()
    assert sentinel in caplog.text
