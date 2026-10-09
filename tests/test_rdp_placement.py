from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import socket
import threading
import time
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import respx
import structlog
from structlog.testing import capture_logs

from polymaker.config import Config
from polymaker.domain import (
    MarketMeta,
    OpenOrder,
    OrderState,
    Quote,
    Side,
    TokenMeta,
    TradeState,
)
from polymaker.execution.gateway import (
    ExactOpenOrder,
    ExecutionGateway,
    GatewayReadError,
    PlacementUnknown,
)
from polymaker.execution.rdp_lifecycle import close_rdp_cycle
from polymaker.execution.rdp_placement import RDPPlacementError
from polymaker.logging import configure as configure_logging
from polymaker.marketdata.parse import TradePrint
from polymaker.rdp_account import RDPAccountError
from polymaker.rdp_live import (
    RDPBootstrap,
    RDPBootstrapError,
    RDPMarketStatusEvidence,
    TokenBookMetadata,
    read_rdp_market_status,
)
from polymaker.rdp_marketdata import RDPMarketDataService
from polymaker.rdp_runtime import (
    RDPInitialNormalAdmission,
    RDPInputSession,
    _StrictCloseGateway,
)
from polymaker.risk.rdp_capital import RDPInitialBackingResult, project_rdp_capital
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.strategy.rdp_accounting import snapshot_inventory
from polymaker.strategy.rdp_book_filter import RDPBookFilterResult
from polymaker.strategy.rdp_quotes import RDPQuoteDecision
from polymaker.userstream.parse import normalize_order


@pytest.fixture
def blocked_network(monkeypatch):
    attempts = []

    def blocked(name):
        def deny(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError(f"network blocked in RDP placement test: {name}")
        return deny

    monkeypatch.setattr(socket.socket, "connect", blocked("socket.connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", blocked("socket.connect_ex"))
    monkeypatch.setattr(socket, "getaddrinfo", blocked("getaddrinfo"))
    monkeypatch.setattr(socket, "create_connection", blocked("create_connection"))
    yield attempts
    assert attempts == []


@pytest.fixture
def placement_scope(tmp_path, monkeypatch):
    end_epoch = int(time.time()) + 300
    market = MarketMeta(
        condition_id="rdp-placement-condition",
        question="BTC Up or Down",
        slug="btc-updown-5m-placement",
        tokens=(TokenMeta("yes-token", "Up"), TokenMeta("no-token", "Down")),
        tick_size=.01,
        neg_risk=False,
        min_order_size=5,
        rewards_min_size=0,
        rewards_max_spread=0,
        rewards_daily_rate=0,
        maker_fee_bps=0,
        taker_fee_bps=0,
        fees_enabled=False,
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat(),
        event_id=None,
    )
    bootstrap = RDPBootstrap(
        market, end_epoch - 300, end_epoch,
        (TokenBookMetadata("Up", "yes-token", .01, 5, False),
         TokenBookMetadata("Down", "no-token", .01, 5, False)),
        time.time(),
    )
    quotes = (
        Quote("yes-token", Side.BUY, .39, 5),
        Quote("no-token", Side.BUY, .39, 5),
    )
    admission = RDPInitialNormalAdmission(
        slug=market.slug,
        condition_id=market.condition_id,
        eligible=True,
        reason=None,
        candidate=RDPQuoteDecision(quotes, None),
        backing=RDPInitialBackingResult(True, None, 30, 0, 30, 30, 0, True),
        book_filter=None,
        capital_projection=None,
        capital_admission=None,
        public_generation=3,
        user_generation=4,
        account_receipt_times=(time.time(),) * 3,
        token_ticks=(("yes-token", .01), ("no-token", .01)),
        minimum_sizes=(("yes-token", 5), ("no-token", 5)),
        outstanding_execution_gates=("FINAL_SUBMIT_REVALIDATION",),
    )
    store = StateStore(":memory:")
    processor = UserEventProcessor(store, retain_confirmed_receipts=True)
    def make_session(fill_window_s=3.0, *, monotonic_ns=None, sleep=None):
        public = SimpleNamespace(
            service=SimpleNamespace(connection_generation=3),
            failure_category=lambda _generation: None,
            aclose=_async_noop,
        )
        channel = SimpleNamespace(connection_generation=4)
        user = SimpleNamespace(
            observation=SimpleNamespace(
                account=SimpleNamespace(signer="signer", funder="funder"),
                user_channel=channel,
            ),
            processor=processor,
            store=store,
            gateway=SimpleNamespace(open_order_reads=0),
            failure_category=lambda _generation: None,
            aclose=_async_noop,
        )
        return RDPInputSession(
            bootstrap, public_session=public, user_session=user,
            feed=SimpleNamespace(connection_generation=3),
            backing=admission.backing,
            now=time.time, sleep=asyncio.sleep if sleep is None else sleep, poll_interval_s=.1,
            started_at=time.time(),
            fill_window_s=fill_window_s,
            monotonic_ns=monotonic_ns,
        )

    session = make_session()
    owner = session.placement_owner
    async def fake_market_status(current_bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            slug=current_bootstrap.market.slug,
            condition_id=current_bootstrap.market.condition_id,
            token_ids=tuple(token.token_id for token in current_bootstrap.market.tokens),
            outcomes=tuple(token.outcome for token in current_bootstrap.market.tokens),
            neg_risk=current_bootstrap.market.neg_risk,
            end_epoch=current_bootstrap.end_epoch,
            accepting_orders=True,
            closed=False,
            receipt_ts=now(),
        )
    monkeypatch.setattr("polymaker.rdp_runtime.read_rdp_market_status", fake_market_status)
    yield SimpleNamespace(
        bootstrap=bootstrap,
        market=market,
        quotes=quotes,
        admission=admission,
        store=store,
        processor=processor,
        owner=owner,
        session=session,
        make_session=make_session,
    )
    store.close()


async def _async_noop():
    return None


class FakeAccountReadGateway:
    def __init__(self):
        self.calls = []
        self.address = "fake-signer"
        self.funder = "fake-funder"
        self.creds = SimpleNamespace(
            api_key="fake-key", api_secret="fake-secret", api_passphrase="fake-passphrase",
        )
        self.spender = "0x" + "a" * 40
        self.positions_hook = None

    async def open_orders(self, *, strict_account, exact_decimal):
        self.calls.append("open_orders")
        assert strict_account is True and exact_decimal is True
        return []

    async def positions(self, *, strict_account):
        self.calls.append("positions")
        assert strict_account is True
        if callable(self.positions_hook):
            self.positions_hook()
        return {"yes-token": ("0", "0"), "no-token": ("0", "0")}

    async def required_collateral_spenders(self, _market):
        self.calls.append("required_collateral_spenders")
        return (self.spender,)

    async def balance_allowance(self):
        self.calls.append("balance_allowance")
        return {"balance": "30000000", "allowances": {self.spender: "30000000"}}


class FakeBatchClient:
    def __init__(self, response=None, *, post_error=None, on_create=None):
        self.response = response
        self.post_error = post_error
        self.on_create = on_create
        self.created = []
        self.create_threads = []
        self.posts = []

    def create_order(self, args, **kwargs):
        self.created.append((args, kwargs))
        self.create_threads.append(threading.get_ident())
        if self.on_create is not None:
            self.on_create()
        return {"fake_signature": len(self.created)}

    def post_orders(self, args, *, post_only):
        self.posts.append((args, post_only))
        if self.post_error is not None:
            raise self.post_error
        return self.response


class FakeCloseSDKClient:
    """Fake official SDK surface consumed by the real gateway/read barrier."""

    def __init__(self, rows, *, trades=None):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.trades = list(trades or [])
        self.cancel_calls = []
        self.cancel_error = None
        self.cancel_mutates = True
        self.cancel_hook = None
        self.read_error = None
        self.open_hook = None
        self.open_count = 0
        self.get_order_error = None
        self.get_order_hook = None
        self.get_order_count = 0
        self.trades_error = None
        self.get_trades_hook = None
        self.get_trades_count = 0

    def get_open_orders(self):
        self.open_count += 1
        if self.open_hook is not None:
            self.open_hook(self.open_count)
        if self.read_error is not None:
            raise self.read_error
        return [
            dict(row) for row in self.rows.values()
            if row["status"].upper() not in {"CANCELED", "CANCELLED", "MATCHED"}
            and Decimal(row["original_size"]) > Decimal(row["size_matched"])
        ]

    def cancel_orders(self, order_ids):
        self.cancel_calls.append(tuple(order_ids))
        if self.cancel_hook is not None:
            self.cancel_hook()
        if self.cancel_error is not None:
            raise self.cancel_error
        if self.cancel_mutates:
            for order_id in order_ids:
                self.rows[order_id]["status"] = "CANCELED"

    def get_order(self, order_id):
        self.get_order_count += 1
        if self.get_order_hook is not None:
            self.get_order_hook(order_id, self.get_order_count)
        if self.get_order_error is not None:
            raise self.get_order_error
        return dict(self.rows[order_id])

    def get_trades(self, _params):
        self.get_trades_count += 1
        if self.get_trades_hook is not None:
            self.get_trades_hook(self.get_trades_count)
        if self.trades_error is not None:
            raise self.trades_error
        return list(self.trades)


def _close_gateway(scope, quotes, *, fill=None, extra_rows=(), trades=()):
    rows = []
    for quote in quotes:
        order_id = ("yes-order" if quote.token_id == "yes-token" else "no-order")
        matched = fill[1] if fill is not None and fill[0] == quote.token_id else 0
        status = "MATCHED" if matched == quote.size else "LIVE"
        if fill is not None and fill[0] == quote.token_id and matched < quote.size:
            status = "LIVE"
        rows.append({
            "id": order_id, "asset_id": quote.token_id, "side": "BUY",
            "price": str(quote.price), "original_size": str(quote.size),
            "size_matched": str(matched), "status": status,
            "market": scope.market.condition_id,
        })
    rows.extend(dict(row) for row in extra_rows)
    client = FakeCloseSDKClient(rows, trades=trades)
    gateway = ExecutionGateway(Config(), paper=False)
    gateway._client = client

    async def inline_io(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    gateway._io = inline_io
    scope.session.user_session.gateway = gateway
    return gateway, client


def _close_gateway_real_executor(scope, quotes):
    """Retain ExecutionGateway._io and use only a fake SDK client."""
    temporary, client = _close_gateway(scope, quotes)
    temporary.close()
    gateway = ExecutionGateway(Config(), paper=False)
    gateway._client = client
    gateway._derive_only = True
    scope.session.user_session.gateway = gateway
    return gateway, client


def _rest_trade(order_id, token_id, price, quantity, *, trade_id="close-fill"):
    return {
        "id": trade_id, "status": "CONFIRMED", "market": "rdp-placement-condition",
        "asset_id": "other-token", "side": "SELL",
        "maker_orders": [{
            "order_id": order_id, "asset_id": token_id, "side": "BUY",
            "matched_amount": str(quantity), "price": str(price),
        }],
    }


async def _compose_accepted_initial_pair(scope):
    _service, _account_gateway = _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    assert admission.eligible
    attempt = scope.session.arm_initial_normal_placement(admission)
    outcome, _client = await _gateway_outcome(
        scope, _accepted_rows(), quotes=admission.candidate.quotes,
    )
    resolved = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert resolved.outcome == "COMPLETE"
    return resolved, admission.candidate.quotes


def _send_window_trade(scope, order_id, *, status, trade_id="window-fill",
                       receipt_ns=None, generation=4, receipt_us=1_800_000_000_000_000,
                       size=1.0, price=.39):
    record = scope.processor.owned_record(order_id)
    event = TradeEvent(
        token_id=record["token_id"], our_side=Side.BUY, price=price, size=size,
        trade_id=trade_id, status=status, ts=1_800_000_000.0,
        maker_order_id=order_id, market_id=scope.market.condition_id,
    )
    scope.processor.on_trade(
        event, scope.market.condition_id, received_at_us=receipt_us,
        received_monotonic_ns=receipt_ns, connection_generation=generation,
    )


class _DeterministicMonotonicClock:
    def __init__(self, now_ns):
        self.now_ns = now_ns

    def read(self):
        return self.now_ns

    async def sleep(self, delay_s):
        self.now_ns += round(delay_s * 1_000_000_000)
        await asyncio.sleep(0)


def _send_matched_then_confirmed(
    scope, order_id, *, receipt_ns, trade_id, size=1.0, price=.39,
):
    _send_window_trade(
        scope, order_id, status=TradeState.MATCHED, receipt_ns=receipt_ns,
        trade_id=trade_id, size=size, price=price,
    )
    _send_window_trade(
        scope, order_id, status=TradeState.CONFIRMED, receipt_ns=receipt_ns,
        trade_id=trade_id, size=size, price=price,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [TradeState.MATCHED, TradeState.MINED, TradeState.RETRYING])
async def test_fill_window_ignores_nonconfirmed_lifecycle_states(
    placement_scope, blocked_network, status,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "yes-order", status=status, receipt_ns=100_000_000_000)

    decision = scope.session.initial_fill_window(now_monotonic_ns=102_000_000_000)

    assert decision.reliable, decision.reason
    assert not decision.started
    assert not decision.closure_due and decision.reason == "AWAITING_CONFIRMED_FILL"
    assert decision.attempt_id == attempt.attempt_id
    assert decision.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
async def test_fill_window_starts_on_confirmed_receipt_and_expires_at_exact_boundary(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "yes-order", status=TradeState.MATCHED,
                       trade_id="confirmed", receipt_ns=99_000_000_000)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="confirmed", receipt_ns=100_000_000_000)
    read_count = list(scope.session.user_session.gateway.calls)

    before = scope.session.initial_fill_window(now_monotonic_ns=102_999_999_999)
    boundary = scope.session.initial_fill_window(now_monotonic_ns=103_000_000_000)

    assert before.reliable and before.started and not before.closure_due
    assert before.first_receipt_monotonic_ns == 100_000_000_000
    assert before.deadline_monotonic_ns == 103_000_000_000
    assert boundary.closure_due and boundary.inventory is not None
    assert boundary.inventory.qy == 1 and boundary.inventory.qn == 0
    assert boundary.attempt_id == attempt.attempt_id
    assert scope.session.user_session.gateway.calls == read_count
    assert blocked_network == []
    assert boundary.execution_authorized is False
    with pytest.raises(TypeError):
        type(boundary)(
            boundary.attempt_id, boundary.slug, boundary.condition_id,
            boundary.acknowledged_order_ids, boundary.reliable, boundary.reason,
            boundary.started, boundary.first_receipt_monotonic_ns,
            boundary.duration_s, boundary.deadline_monotonic_ns,
            boundary.closure_due, boundary.inventory, execution_authorized=True,
        )
    with pytest.raises(ValueError):
        replace(boundary, execution_authorized=True)


@pytest.mark.asyncio
async def test_fill_window_uses_configured_duration_for_attempt(
    placement_scope, blocked_network,
):
    scope = placement_scope
    scope.session = scope.make_session(fill_window_s=1.25)
    scope.owner = scope.session.placement_owner
    await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "yes-order", status=TradeState.MATCHED,
                       trade_id="configured", receipt_ns=150_000_000_000)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="configured", receipt_ns=150_000_000_000)

    before = scope.session.initial_fill_window(now_monotonic_ns=151_249_999_999)
    boundary = scope.session.initial_fill_window(now_monotonic_ns=151_250_000_000)

    assert before.duration_s == 1.25 and before.deadline_monotonic_ns == 151_250_000_000
    assert not before.closure_due and boundary.closure_due
    assert blocked_network == []


@pytest.mark.asyncio
async def test_confirmed_before_matched_uses_original_receipt_once_admitted(
    placement_scope, blocked_network,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="out-of-order", receipt_ns=200_000_000_000)

    waiting = scope.session.initial_fill_window(now_monotonic_ns=201_000_000_000)
    assert waiting.reliable and not waiting.started

    _send_window_trade(scope, "yes-order", status=TradeState.MATCHED,
                       trade_id="out-of-order", receipt_ns=202_000_000_000)
    started = scope.session.initial_fill_window(now_monotonic_ns=202_000_000_000)

    assert started.reliable and started.started
    assert started.first_receipt_monotonic_ns == 200_000_000_000
    assert started.deadline_monotonic_ns == 203_000_000_000
    assert blocked_network == []


@pytest.mark.asyncio
async def test_late_admission_of_earlier_confirmation_preserves_deadline_and_fails_closed(
    placement_scope, blocked_network,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    # NO CONFIRMED arrived first but is not yet accepted economic evidence.
    _send_window_trade(scope, "no-order", status=TradeState.CONFIRMED,
                       trade_id="earlier-no", receipt_ns=100_000_000_000)
    waiting = scope.session.initial_fill_window(now_monotonic_ns=101_000_000_000)
    assert waiting.reliable and not waiting.started

    _send_window_trade(scope, "yes-order", status=TradeState.MATCHED,
                       trade_id="yes", receipt_ns=101_000_000_000)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="yes", receipt_ns=102_000_000_000)
    established = scope.session.initial_fill_window(now_monotonic_ns=104_000_000_000)
    assert established.reliable and established.deadline_monotonic_ns == 105_000_000_000

    # The original 100s confirmation becomes accepted only now, after the
    # 102s receipt established a later deadline.
    _send_window_trade(scope, "no-order", status=TradeState.MATCHED,
                       trade_id="earlier-no", receipt_ns=104_000_000_000)
    contradicted = scope.session.initial_fill_window(now_monotonic_ns=104_500_000_000)

    assert contradicted.started
    assert contradicted.first_receipt_monotonic_ns == 102_000_000_000
    assert contradicted.deadline_monotonic_ns == 105_000_000_000
    assert not contradicted.reliable and not contradicted.closure_due
    assert contradicted.reason == "CONFIRMED_RECEIPT_PRECEDES_ESTABLISHED_WINDOW"
    assert blocked_network == []


@pytest.mark.asyncio
async def test_fill_window_uses_earliest_confirmation_across_both_orders_and_never_extends(
    placement_scope, blocked_network,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    for order_id, timestamp in (("no-order", 302_000_000_000),
                                ("yes-order", 300_000_000_000)):
        _send_window_trade(scope, order_id, status=TradeState.MATCHED,
                           trade_id=f"confirmed-{order_id}", receipt_ns=timestamp - 1_000_000)
        _send_window_trade(scope, order_id, status=TradeState.CONFIRMED,
                           trade_id=f"confirmed-{order_id}", receipt_ns=timestamp)
    initial = scope.session.initial_fill_window(now_monotonic_ns=303_000_000_000)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="later-fill", receipt_ns=310_000_000_000)
    _send_window_trade(scope, "no-order", status=TradeState.CONFIRMED,
                       trade_id="confirmed-no-order", receipt_ns=320_000_000_000)
    later = scope.session.initial_fill_window(now_monotonic_ns=302_999_999_999)

    assert initial.first_receipt_monotonic_ns == 300_000_000_000
    assert initial.deadline_monotonic_ns == 303_000_000_000
    assert initial.closure_due is True
    assert later.first_receipt_monotonic_ns == initial.first_receipt_monotonic_ns
    assert later.deadline_monotonic_ns == initial.deadline_monotonic_ns
    assert later.closure_due is False
    assert blocked_network == []


@pytest.mark.asyncio
async def test_fill_window_fails_closed_for_missing_original_receipt(
    placement_scope, blocked_network,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "yes-order", status=TradeState.MATCHED,
                       trade_id="missing", receipt_ns=100_000_000_000)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="missing", receipt_ns=None)
    missing = scope.session.initial_fill_window(now_monotonic_ns=101_000_000_000)
    assert not missing.reliable and not missing.started
    assert missing.reason == "CONFIRMED_RECEIPT_INVALID"
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="missing", receipt_ns=102_000_000_000)
    duplicate = scope.session.initial_fill_window(now_monotonic_ns=103_000_000_000)
    assert not duplicate.reliable and not duplicate.started
    assert duplicate.reason == "CONFIRMED_RECEIPT_INVALID"
    assert blocked_network == []


@pytest.mark.asyncio
async def test_rest_confirmed_recovery_cannot_fabricate_fill_window_receipt(
    placement_scope, blocked_network,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    event = TradeEvent(
        "yes-token", Side.BUY, .39, 1, "rest-confirmed", TradeState.CONFIRMED,
        1_800_000_000.0, "yes-order", scope.market.condition_id,
    )
    scope.processor.on_trade(event, scope.market.condition_id, source="REST")

    decision = scope.session.initial_fill_window(now_monotonic_ns=500_000_000_000)

    assert not decision.reliable and not decision.started
    assert decision.reason == "CONFIRMED_RECEIPT_INVALID"
    assert scope.processor.confirmed_receipts[("rest-confirmed", "yes-order")] == (
        None, None, None, "REST",
    )
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("receipt_ns", "generation", "now_ns", "expected_reason"),
    [
        (201_000_000_000, 4, 200_000_000_000, "CONFIRMED_RECEIPT_IN_FUTURE"),
        (199_000_000_000, 99, 202_000_000_000, "CONFIRMED_RECEIPT_INVALID"),
        (199_000_000_000, 4.0, 202_000_000_000, "CONFIRMED_RECEIPT_INVALID"),
    ],
    ids=["future", "wrong-generation", "float-generation"],
)
async def test_fill_window_rejects_unreliable_confirmation_receipt(
    placement_scope, blocked_network, receipt_ns, generation, now_ns, expected_reason,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "no-order", status=TradeState.MATCHED,
                       trade_id="trade", receipt_ns=receipt_ns - 1_000_000,
                       generation=generation)
    _send_window_trade(scope, "no-order", status=TradeState.CONFIRMED,
                       trade_id="trade", receipt_ns=receipt_ns,
                       generation=generation)
    decision = scope.session.initial_fill_window(now_monotonic_ns=now_ns)
    assert not decision.reliable and not decision.started
    assert decision.reason == expected_reason
    if expected_reason == "CONFIRMED_RECEIPT_INVALID":
        # A later duplicate with valid metadata cannot repair the first receipt.
        _send_window_trade(scope, "no-order", status=TradeState.CONFIRMED,
                           trade_id="trade", receipt_ns=receipt_ns,
                           generation=4)
        duplicate = scope.session.initial_fill_window(now_monotonic_ns=now_ns + 1)
        assert not duplicate.reliable and not duplicate.started
        assert duplicate.reason == "CONFIRMED_RECEIPT_INVALID"
    assert blocked_network == []


@pytest.mark.asyncio
async def test_preack_confirmations_replay_original_receipts_for_both_pair_legs(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    for order_id, token_id, timestamp in (
        # Reversed response rows bind no-order to the yes quote and vice versa.
        ("no-order", "yes-token", 102_000_000_000),
        ("yes-order", "no-token", 100_000_000_000),
    ):
        matched = TradeEvent(
            token_id, Side.BUY, .39, 1, f"preack-{order_id}",
            TradeState.MATCHED, 1_800_000_000.0, order_id,
            scope.market.condition_id,
        )
        confirmed = replace(matched, status=TradeState.CONFIRMED)
        scope.processor.on_trade(
            matched, scope.market.condition_id, received_at_us=1_800_000_000_000_000,
            received_monotonic_ns=timestamp - 1, connection_generation=4,
        )
        scope.processor.on_trade(
            confirmed, scope.market.condition_id, received_at_us=1_800_000_000_000_001,
            received_monotonic_ns=timestamp, connection_generation=4,
        )

    outcome, _client = await _gateway_outcome(
        scope, list(reversed(_accepted_rows())),
    )
    resolved = scope.session.resolve_initial_normal_placement(attempt, outcome)
    decision = scope.session.initial_fill_window(now_monotonic_ns=102_999_999_999)

    assert resolved.outcome == "COMPLETE"
    assert scope.processor.confirmed_receipts[("preack-no-order", "no-order")] == (
        1_800_000_000_000_001, 102_000_000_000, 4, "WS",
    )
    assert scope.processor.confirmed_receipts[("preack-yes-order", "yes-order")] == (
        1_800_000_000_000_001, 100_000_000_000, 4, "WS",
    )
    assert decision.reliable and decision.started
    assert decision.first_receipt_monotonic_ns == 100_000_000_000
    assert decision.deadline_monotonic_ns == 103_000_000_000
    assert blocked_network == []


@pytest.mark.asyncio
async def test_fully_filled_pair_does_not_close_fill_window_early(
    placement_scope, blocked_network,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    for order_id in ("yes-order", "no-order"):
        _send_window_trade(scope, order_id, status=TradeState.MATCHED,
                           trade_id=f"full-{order_id}", receipt_ns=400_000_000_000,
                           size=5)
        _send_window_trade(scope, order_id, status=TradeState.CONFIRMED,
                           trade_id=f"full-{order_id}", receipt_ns=400_000_000_000,
                           size=5)
    decision = scope.session.initial_fill_window(now_monotonic_ns=401_000_000_000)

    assert decision.reliable and decision.started and not decision.closure_due
    assert decision.deadline_monotonic_ns == 403_000_000_000
    assert decision.inventory.qy == 5 and decision.inventory.qn == 5
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("public-generation", "INPUT_GENERATION_CHANGED"),
        ("user-generation", "INPUT_GENERATION_CHANGED"),
        ("public-health", "FEED_UNHEALTHY"),
        ("session-closed", "SESSION_CLOSED"),
        ("market-ended", "MARKET_EXPIRED"),
    ],
)
async def test_fill_window_fails_closed_when_retained_scope_is_unreliable(
    placement_scope, blocked_network, failure, expected,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "yes-order", status=TradeState.MATCHED,
                       trade_id="health-fill", receipt_ns=600_000_000_000)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="health-fill", receipt_ns=601_000_000_000)
    if failure == "public-generation":
        scope.session.public_session.service.connection_generation += 1
    elif failure == "user-generation":
        scope.session.user_session.observation.user_channel.connection_generation += 1
    elif failure == "public-health":
        scope.session.public_session.failure_category = lambda _generation: "FEED_UNHEALTHY"
    elif failure == "session-closed":
        scope.session._closed = True
    else:
        scope.session.bootstrap = replace(scope.bootstrap, end_epoch=time.time() - 1)

    decision = scope.session.initial_fill_window(now_monotonic_ns=602_000_000_000)

    assert not decision.reliable and not decision.closure_due
    assert decision.reason == expected
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [0, -1, True, float("inf"), float("nan"), 1e-12])
async def test_invalid_fill_window_duration_rejected_before_bootstrap(
    blocked_network, duration,
):
    from polymaker.rdp_runtime import RDPObservationError

    bootstrap_called = False

    async def bootstrap_factory(*_args, **_kwargs):
        nonlocal bootstrap_called
        bootstrap_called = True
        raise AssertionError("invalid FillWindow reached market bootstrap")

    with pytest.raises(RDPObservationError) as caught:
        await RDPInputSession.open(
            Config(), "btc-updown-5m-test", fill_window_s=duration,
            bootstrap_factory=bootstrap_factory,
        )

    assert caught.value.category == "INVALID_FILL_WINDOW"
    assert bootstrap_called is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["foreign-observed-id", "wrong-market", "wrong-token", "wrong-economics",
     "zero-size", "failed", "quarantined"],
)
async def test_fill_window_adverse_fill_evidence_fails_closed_or_is_ignored(
    placement_scope, blocked_network, case,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    if case == "foreign-observed-id":
        event = TradeEvent(
            "yes-token", Side.BUY, .39, 1, "observed-only", TradeState.CONFIRMED,
            1_800_000_000.0, "observed-only-id", scope.market.condition_id,
        )
        scope.processor.on_trade(
            event, scope.market.condition_id, received_at_us=1_800_000_000_000_000,
            received_monotonic_ns=700_000_000_000, connection_generation=4,
        )
        decision = scope.session.initial_fill_window(now_monotonic_ns=701_000_000_000)
        assert decision.reliable and not decision.started
        assert decision.reason == "AWAITING_CONFIRMED_FILL"
        assert decision.inventory.qy == decision.inventory.qn == 0
    elif case == "quarantined":
        scope.processor.quarantine_order("yes-order", "synthetic", notify=False)
        decision = scope.session.initial_fill_window(now_monotonic_ns=701_000_000_000)
        assert not decision.reliable and not decision.started
        assert decision.reason == "FILL_EVIDENCE_QUARANTINED"
    else:
        values = {
            "wrong-market": ("yes-token", .39, 1, "other-condition", TradeState.CONFIRMED),
            "wrong-token": ("foreign-token", .39, 1, scope.market.condition_id, TradeState.CONFIRMED),
            "wrong-economics": ("yes-token", .99, 1, scope.market.condition_id, TradeState.CONFIRMED),
            "zero-size": ("yes-token", .39, 0, scope.market.condition_id, TradeState.CONFIRMED),
            "failed": ("yes-token", .39, 1, scope.market.condition_id, TradeState.FAILED),
        }
        token_id, price, size, condition_id, status = values[case]
        event = TradeEvent(
            token_id, Side.BUY, price, size, f"adverse-{case}", status,
            1_800_000_000.0, "yes-order", condition_id,
        )
        scope.processor.on_trade(
            event, condition_id,
            received_at_us=1_800_000_000_000_000,
            received_monotonic_ns=700_000_000_000, connection_generation=4,
        )
        decision = scope.session.initial_fill_window(now_monotonic_ns=701_000_000_000)
        if case == "zero-size":
            assert decision.reliable and not decision.started
            assert decision.reason == "AWAITING_CONFIRMED_FILL"
            assert decision.inventory.qy == decision.inventory.qn == 0
        else:
            assert not decision.reliable and not decision.started
            assert decision.reason == "FILL_EVIDENCE_QUARANTINED"
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome_kind", ["partial", "unknown", "pending-overflow"])
async def test_fill_window_rejects_unresolved_placement_outcomes(
    placement_scope, blocked_network, outcome_kind,
):
    scope = placement_scope
    attempt = _arm(scope)
    if outcome_kind == "partial":
        outcome, _client = await _gateway_outcome(
            scope, [{"success": True, "orderID": "yes-order"}],
        )
    elif outcome_kind == "unknown":
        outcome = PlacementUnknown(
            "synthetic unknown", observed_order_ids=("observed-only",),
            reason="SYNTHETIC_UNKNOWN",
        )
    else:
        scope.processor._pending_limit = 0
        scope.processor.on_trade(
            TradeEvent("yes-token", Side.BUY, .39, 1, "overflow", TradeState.MATCHED,
                       1_800_000_000.0, "unknown-before-ack", scope.market.condition_id),
            scope.market.condition_id,
        )
        outcome, _client = await _gateway_outcome(scope, _accepted_rows())
    resolved = scope.session.resolve_initial_normal_placement(attempt, outcome)
    decision = scope.session.initial_fill_window(now_monotonic_ns=702_000_000_000)

    assert resolved.outcome in {"PARTIAL", "UNKNOWN"}
    assert not decision.reliable and not decision.started
    assert decision.reason == "PLACEMENT_SCOPE_UNRESOLVED"
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_healthy_book_change_and_filter_rejection_do_not_retime_fill_window(
    placement_scope, blocked_network,
):
    scope = placement_scope
    await _compose_accepted_initial_pair(scope)
    _send_window_trade(scope, "yes-order", status=TradeState.MATCHED,
                       trade_id="book-motion", receipt_ns=800_000_000_000)
    _send_window_trade(scope, "yes-order", status=TradeState.CONFIRMED,
                       trade_id="book-motion", receipt_ns=800_000_000_000)
    before = scope.session.initial_fill_window(now_monotonic_ns=801_000_000_000)
    calls_before = list(scope.session.user_session.gateway.calls)
    service = scope.session.public_session.service
    yes_book = service.book("yes-token")
    assert yes_book is not None
    yes_book.apply_snapshot([(.55, 10)], [(.57, 10)], 2)
    yes_book.snapshot_generation = scope.session.public_generation
    service.book_filter.evaluate = lambda *_args, **_kwargs: SimpleNamespace(
        ready=True, rejected=True, reasons=("SYNTHETIC_BOOK_FILTER_REJECTION",),
    )
    rejected = scope.session.book_filter_result()
    after = scope.session.initial_fill_window(now_monotonic_ns=802_000_000_000)

    assert rejected.ready and rejected.rejected
    assert after.reliable and after.started
    assert after.first_receipt_monotonic_ns == before.first_receipt_monotonic_ns
    assert after.deadline_monotonic_ns == before.deadline_monotonic_ns
    assert scope.session.user_session.gateway.calls == calls_before
    assert blocked_network == []


@pytest.mark.asyncio
async def test_complete_pair_closes_through_strict_gateway_and_certifies_capital(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.barrier_complete and result.barrier is not None
    assert result.capital is not None and result.capital.certified
    assert result.owned_order_ids == ("yes-order", "no-order")
    assert result.closed_order_ids == ("no-order", "yes-order")
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert result.capital.snapshot.reserved_cash == 0
    assert result.capital.snapshot.free_cash == Decimal("30.00")
    assert result.next_mode.value == "NORMAL"
    assert not result.replacement_blocked and not result.placement_unresolved
    assert result.execution_authorized is False
    assert scope.owner.current.replacement_blocked is False
    assert scope.processor.owned_record("yes-order") is not None
    assert scope.processor.owned_record("no-order") is not None
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_id", "quantity", "expected_delta", "expected_mode"),
    [
        (None, "0", "0", "NORMAL"),
        ("yes-token", "4.99", "4.99", "NORMAL"),
        ("yes-token", "5", "5", "REBALANCE"),
        ("no-token", "5", "-5", "REBALANCE"),
    ],
    ids=["zero-fill", "partial-below-five", "exact-five", "negative-exact-five"],
)
async def test_close_certifies_inventory_and_exact_five_share_classification(
    placement_scope, blocked_network, token_id, quantity, expected_delta, expected_mode,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    fill = None if token_id is None else (token_id, Decimal(quantity))
    trades = () if fill is None else (
        _rest_trade("yes-order" if token_id == "yes-token" else "no-order",
                    token_id, "0.39", quantity),
    )
    gateway, client = _close_gateway(scope, quotes, fill=fill, trades=trades)
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.barrier_complete and result.capital is not None
    assert result.capital.certified
    assert Decimal(str(result.barrier.inventory.delta)) == Decimal(expected_delta)
    assert result.next_mode.value == expected_mode
    assert not result.replacement_blocked
    assert len(client.cancel_calls) == 1
    assert blocked_network == []


@pytest.mark.asyncio
async def test_confirmed_fill_during_cancel_enters_close_inventory(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)

    def fill_during_cancel():
        client.rows["yes-order"]["size_matched"] = "2"
        client.trades = [_rest_trade("yes-order", "yes-token", "0.39", "2")]

    client.cancel_hook = fill_during_cancel
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.barrier_complete and result.capital is not None
    assert result.barrier.inventory.qy == 2
    assert result.barrier.inventory.qn == 0
    assert result.barrier.inventory.delta == 2
    assert scope.processor.applied_fill_keys == {("close-fill", "yes-order")}
    assert result.capital.snapshot.reserved_cash == 0
    assert blocked_network == []


@pytest.mark.asyncio
async def test_matched_only_fill_and_terminal_checksum_change_remain_blocked(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    matched_trade = _rest_trade("yes-order", "yes-token", "0.39", "5")
    matched_trade["status"] = "MATCHED"
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("5")), trades=(matched_trade,),
    )

    def change_terminal_cumulative(order_id, read_count):
        if order_id == "yes-order" and read_count == 4:
            # Reconciliation's own read saw five; the later terminal read says four.
            client.rows[order_id]["size_matched"] = "4"

    client.get_order_hook = change_terminal_cumulative
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert not result.barrier_complete
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert result.capital is None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_matched_only_fill_remains_pending_without_terminal_conflict(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    trade = _rest_trade("yes-order", "yes-token", ".39", "5")
    trade["status"] = "MATCHED"
    gateway, _client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("5")), trades=(trade,),
    )
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert not result.barrier_complete
    assert result.barrier.reason == "PENDING_CONFIRMATION"
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert result.capital is None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_contradictory_confirmed_fill_is_quarantined_and_close_stays_blocked(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    trade = _rest_trade("yes-order", "foreign-token", ".39", "5")
    gateway, _client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("5")), trades=(trade,),
    )
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert not result.barrier_complete
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert "yes-order" in scope.processor.quarantined_orders
    assert result.capital is None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_processor_change_during_final_open_read_invalidates_close_proof(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)

    def late_fill_on_final_snapshot(read_count):
        if read_count == 3:
            scope.processor.on_trade(
                TradeEvent(
                    "yes-token", Side.BUY, .39, 1, "late-close-fill",
                    TradeState.CONFIRMED, 1, "yes-order", scope.market.condition_id,
                ),
                scope.market.condition_id,
            )

    client.open_hook = late_fill_on_final_snapshot
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert not result.barrier_complete
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert result.capital is None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_foreign_market_order_is_never_a_close_cancellation_target(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    foreign = {
        "id": "manual-foreign", "asset_id": "unrelated-token", "side": "BUY",
        "price": ".4", "original_size": "7", "size_matched": "0",
        "status": "LIVE", "market": "another-condition",
    }
    gateway, client = _close_gateway(scope, quotes, extra_rows=(foreign,))
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.barrier_complete and not result.replacement_blocked
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert client.rows["manual-foreign"]["status"] == "LIVE"
    assert blocked_network == []


@pytest.mark.asyncio
async def test_lost_input_generation_still_closes_known_risk_but_cannot_certify_next_mode(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    scope.session.public_session.service.connection_generation += 1
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.barrier_complete
    assert result.reason == "UNRELIABLE_STATE"
    assert result.capital is not None and not result.capital.certified
    assert result.next_mode is None
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_partial_unknown_closes_known_subset_without_clearing_unresolved_block(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    outcome, _client = await _gateway_outcome(scope, [
        {"success": True, "orderID": "safe-yes"},
        {"success": True, "orderID": "unknown-no", "id": "alias-no"},
    ])
    assert isinstance(outcome, PlacementUnknown)
    attempt = scope.session.resolve_initial_normal_placement(attempt, outcome)
    gateway, client = _close_gateway(scope, (), extra_rows=({
        "id": "safe-yes", "asset_id": "yes-token", "side": "BUY",
        "price": ".39", "original_size": "5", "size_matched": "0",
        "status": "LIVE", "market": scope.market.condition_id,
    },))
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.barrier_complete
    assert result.closed_order_ids == ("safe-yes",)
    assert client.cancel_calls == [("safe-yes",)]
    assert result.placement_unresolved and result.replacement_blocked
    assert scope.owner.blocks_initial_admission
    assert result.capital is None and result.next_mode is None
    assert scope.processor.owned_record("unknown-no") is None
    assert scope.processor.owned_record("alias-no") is None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_observed_only_market_order_is_never_adopted_or_canceled(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    outcome, _client = await _gateway_outcome(scope, [
        {"success": True, "orderID": "safe-yes"},
        {"success": True, "orderID": "observed-no", "id": "observed-alias"},
    ])
    assert isinstance(outcome, PlacementUnknown)
    attempt = scope.session.resolve_initial_normal_placement(attempt, outcome)
    safe = {
        "id": "safe-yes", "asset_id": "yes-token", "side": "BUY",
        "price": ".39", "original_size": "5", "size_matched": "0",
        "status": "LIVE", "market": scope.market.condition_id,
    }
    observed = {
        "id": "observed-no", "asset_id": "no-token", "side": "BUY",
        "price": ".39", "original_size": "5", "size_matched": "0",
        "status": "LIVE", "market": scope.market.condition_id,
    }
    gateway, client = _close_gateway(scope, (), extra_rows=(safe, observed))
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert not result.barrier_complete and result.replacement_blocked
    assert client.cancel_calls == [("safe-yes",)]
    assert client.rows["observed-no"]["status"] == "LIVE"
    assert scope.processor.owned_record("observed-no") is None
    assert scope.processor.owned_record("observed-alias") is None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_zero_id_unknown_close_does_not_read_or_cancel(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    outcome, _client = await _gateway_outcome(
        scope, [{"success": True}, {"success": False}],
    )
    assert isinstance(outcome, PlacementUnknown)
    attempt = scope.session.resolve_initial_normal_placement(attempt, outcome)
    gateway, client = _close_gateway(scope, attempt.quotes)
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.reason == "PLACEMENT_OUTCOME_UNRESOLVED"
    assert result.barrier is None and result.closed_order_ids == ()
    assert client.open_count == 0 and client.cancel_calls == []
    assert result.replacement_blocked
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "expected_reason"),
    [
        ("cancel-exception", "CANCELLATION_UNCERTAIN"),
        ("cancel-noop", "OPEN_ORDERS_REMAIN"),
        ("open-read", "OPEN_ORDER_READ_FAILED"),
        ("malformed-cumulative", "OPEN_ORDER_READ_FAILED"),
        ("missing-matched", "OPEN_ORDER_READ_FAILED"),
        ("float-cumulative", "OPEN_ORDER_READ_FAILED"),
    ],
)
async def test_incomplete_close_evidence_never_releases_pair(
    placement_scope, blocked_network, fault, expected_reason,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    if fault == "cancel-exception":
        client.cancel_error = RuntimeError("private cancel sentinel")
    elif fault == "cancel-noop":
        client.cancel_mutates = False
    elif fault == "open-read":
        client.read_error = RuntimeError("private order-read sentinel")
    else:
        if fault == "malformed-cumulative":
            del client.rows["yes-order"]["original_size"]
        elif fault == "missing-matched":
            del client.rows["yes-order"]["size_matched"]
        else:
            client.rows["yes-order"]["original_size"] = 5.0
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert not result.barrier_complete
    assert result.reason == expected_reason
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert result.capital is None
    assert "private" not in repr(result)
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["local-live", "late-matched", "changed-price", "changed-condition"],
)
async def test_stale_close_proof_after_local_order_reappears_cannot_release_owner(
    placement_scope, blocked_network, mutation,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, _client = _close_gateway(scope, quotes)
    try:
        deadline = asyncio.get_running_loop().time() + 2
        barrier = await close_rdp_cycle(
            scope.market, scope.processor, _StrictCloseGateway(gateway),
            set(attempt.acknowledged_order_ids),
            set(attempt.acknowledged_order_ids), deadline,
        )
        assert barrier.complete
        capital = project_rdp_capital(
            scope.market, scope.processor, set(attempt.acknowledged_order_ids), [],
            market_state_reliable=True, order_state_reliable=True,
            close_evidence=barrier,
        )
        assert capital.certified
        if mutation == "local-live":
            scope.processor.on_order(normalize_order({
                "id": "yes-order", "asset_id": "yes-token", "side": "BUY",
                "price": ".39", "original_size": "5", "size_matched": "0",
                "status": "LIVE", "market": scope.market.condition_id,
            }), scope.market.condition_id)
        elif mutation == "late-matched":
            scope.processor.on_trade(TradeEvent(
                "yes-token", Side.BUY, .39, 1, "late-matched-fill",
                TradeState.MATCHED, 1, "yes-order", scope.market.condition_id,
            ), scope.market.condition_id)
        else:
            scope.store.register_owned_order(
                "yes-order",
                ("changed-condition" if mutation == "changed-condition"
                 else scope.market.condition_id),
                "yes-token", Side.BUY, 5,
                (.40 if mutation == "changed-price" else .39), "rdp_initial",
            )
        updated = scope.owner.record_close(attempt, barrier, capital)
    finally:
        gateway.close()

    assert updated.replacement_blocked
    assert scope.owner.blocks_initial_admission
    assert set(updated.closed_order_ids) == set(attempt.acknowledged_order_ids)
    assert scope.processor.owned_record("yes-order") is not None
    if mutation == "late-matched":
        fresh = project_rdp_capital(
            scope.market, scope.processor, set(attempt.acknowledged_order_ids), [],
            market_state_reliable=True, order_state_reliable=True,
            close_evidence=barrier,
        )
        assert not fresh.certified and fresh.reason == "PENDING_FILL"
    assert blocked_network == []


@pytest.mark.asyncio
async def test_wrong_attempt_and_deadline_close_retain_block_without_scope_expansion(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    try:
        with pytest.raises(RDPPlacementError, match="PLACEMENT_ATTEMPT_NOT_ACTIVE"):
            await scope.session.close_initial_normal_placement(
                replace(attempt, slug="foreign-market"),
                deadline=asyncio.get_running_loop().time() + 2,
            )
        expired = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() - 1,
        )
    finally:
        gateway.close()

    assert not expired.barrier_complete
    assert expired.reason == "OPERATIONAL_DEADLINE"
    assert expired.replacement_blocked and scope.owner.blocks_initial_admission
    assert client.cancel_calls == []
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_method", ["open_orders", "cancel", "get_order", "get_trades"])
@pytest.mark.parametrize("mode", ["caller-cancel", "deadline", "teardown"])
async def test_each_real_gateway_operation_drains_before_close_ownership_clears(
    placement_scope, blocked_network, sdk_method, mode,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway_real_executor(scope, quotes)
    sdk_started = threading.Event()
    sdk_release = threading.Event()

    def block_sdk(*_args):
        sdk_started.set()
        if not sdk_release.wait(timeout=5):
            raise RuntimeError("fake SDK release timeout")

    if sdk_method == "open_orders":
        client.open_hook = block_sdk
    elif sdk_method == "cancel":
        client.cancel_hook = block_sdk
    elif sdk_method == "get_order":
        client.get_order_hook = block_sdk
    else:
        client.get_trades_hook = block_sdk

    loop = asyncio.get_running_loop()
    deadline = loop.time() + (.2 if mode == "deadline" else 5)
    operation = asyncio.create_task(
        scope.session.close_initial_normal_placement(attempt, deadline=deadline),
    )
    shutdown = None

    def calls():
        return (client.open_count, len(client.cancel_calls),
                client.get_order_count, client.get_trades_count)

    expected_calls = {
        "open_orders": (1, 0, 0, 0),
        "cancel": (1, 1, 0, 0),
        "get_order": (2, 1, 1, 0),
        "get_trades": (2, 1, 1, 1),
    }[sdk_method]
    try:
        assert await asyncio.wait_for(asyncio.to_thread(sdk_started.wait, 2), timeout=3)
        assert calls() == expected_calls
        if mode == "caller-cancel":
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
            await asyncio.sleep(0)
            with pytest.raises(RDPPlacementError, match="PLACEMENT_CLOSE_IN_PROGRESS"):
                await scope.session.close_initial_normal_placement(
                    attempt, deadline=loop.time() + 5,
                )
            assert not operation.done()
            assert scope.session._placement_close_task is not None
            sdk_release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
        elif mode == "deadline":
            await asyncio.sleep(.25)
            assert not operation.done()
            assert scope.session._placement_close_task is not None
            with pytest.raises(RDPPlacementError, match="PLACEMENT_CLOSE_IN_PROGRESS"):
                await scope.session.close_initial_normal_placement(
                    attempt, deadline=loop.time() + 5,
                )
            sdk_release.set()
            result = await operation
            assert not result.barrier_complete
            assert result.reason == "OPERATIONAL_DEADLINE"
        else:
            shutdown = asyncio.create_task(scope.session.aclose())
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            assert not shutdown.done() and not operation.done()
            assert scope.session._placement_close_task is not None
            sdk_release.set()
            with pytest.raises(asyncio.CancelledError):
                await shutdown
            with pytest.raises(asyncio.CancelledError):
                await operation
    finally:
        sdk_release.set()
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown
        gateway.close()

    assert calls() == expected_calls
    assert scope.session._placement_close_task is None
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cancel", "open_orders", "get_order", "get_trades"])
@pytest.mark.parametrize("failing_handler", [False, True], ids=["normal-log", "failing-log"])
async def test_gateway_safe_diagnostics_hide_sdk_sentinel_and_error_chain(
    operation, failing_handler, capsys, blocked_network,
):
    gateway = ExecutionGateway(Config(), paper=False)
    client = FakeCloseSDKClient([{
        "id": "order-1", "asset_id": "yes-token", "side": "BUY",
        "price": ".39", "original_size": "5", "size_matched": "0",
        "status": "LIVE", "market": "condition-1",
    }])
    gateway._client = client
    gateway._derive_only = True
    sentinel = "SYNTHETIC_CREDENTIAL_SENTINEL"
    if operation == "cancel":
        client.cancel_error = RuntimeError(sentinel)
    elif operation == "open_orders":
        client.read_error = RuntimeError(sentinel)
    elif operation == "get_order":
        client.get_order_error = RuntimeError(sentinel)
    else:
        client.trades_error = RuntimeError(sentinel)

    class RaisingStream:
        def write(self, _message):
            raise OSError("synthetic log sink failure")

        def flush(self):
            return None

    old_structlog_config = structlog.get_config()
    root_logger = logging.getLogger()
    old_root_handlers, old_root_level = root_logger.handlers[:], root_logger.level
    configure_logging(console=False)
    logger = logging.getLogger("execution.gateway")
    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    records = []

    class CapturingHandler(logging.StreamHandler):
        def emit(self, record):
            records.append(record)
            super().emit(record)

    handler = CapturingHandler(RaisingStream() if failing_handler else io.StringIO())
    handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        if operation == "cancel":
            result = await gateway.cancel(["order-1"], safe_diagnostics=True)
            assert result is False
        else:
            with pytest.raises(GatewayReadError) as caught:
                if operation == "open_orders":
                    await gateway.open_orders(
                        strict_account=True, exact_decimal=True, safe_diagnostics=True,
                    )
                elif operation == "get_order":
                    await gateway.get_order("order-1", safe_diagnostics=True)
                else:
                    await gateway.get_trades(
                        market="condition-1", safe_diagnostics=True,
                    )
            assert caught.value.__cause__ is None
            assert caught.value.__context__ is None
            assert sentinel not in str(caught.value)
    finally:
        gateway.close()
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        root_logger.handlers[:] = old_root_handlers
        root_logger.setLevel(old_root_level)
        structlog.configure(**old_structlog_config)

    stderr = capsys.readouterr().err
    assert sentinel not in stderr
    expected_event = {
        "cancel": "cancel_failed", "open_orders": "open_orders_failed",
        "get_order": "get_order_failed", "get_trades": "get_trades_failed",
    }[operation]
    matching = [
        record.name == "execution.gateway"
        and (record.msg == expected_event
             or (isinstance(record.msg, dict)
                 and record.msg.get("event") == expected_event))
        for record in records
    ]
    assert any(matching), [(record.name, record.msg, record.__dict__) for record in records]
    for record in records:
        fields = record.msg if isinstance(record.msg, dict) else record.__dict__
        assert fields.get("error_type") == "RuntimeError"
        assert "err" not in fields
        assert sentinel not in repr(record.__dict__)
    assert blocked_network == []


@pytest.mark.asyncio
async def test_close_adapter_delegates_each_gateway_method_with_safe_diagnostics(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, _client = _close_gateway(scope, quotes)
    observed = []
    for name in ("open_orders", "cancel", "get_order", "get_trades"):
        original = getattr(gateway, name)

        async def delegate(*args, _name=name, _method=original, **kwargs):
            observed.append((_name, kwargs.get("safe_diagnostics")))
            return await _method(*args, **kwargs)

        setattr(gateway, name, delegate)
    try:
        result = await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 2,
        )
    finally:
        gateway.close()

    assert result.barrier_complete
    assert {name for name, safe in observed if safe is True} == {
        "open_orders", "cancel", "get_order", "get_trades",
    }
    assert all(safe is True for _name, safe in observed)
    assert blocked_network == []


@pytest.mark.asyncio
async def test_gateway_diagnostic_opt_in_defaults_off_and_legacy_reads_remain_usable(
    monkeypatch, blocked_network,
):
    import polymaker.execution.gateway as gateway_module

    gateway = ExecutionGateway(Config(), paper=False)
    client = FakeCloseSDKClient([{
        "id": "order-1", "asset_id": "yes-token", "side": "BUY",
        "price": ".39", "original_size": "5", "size_matched": "0",
        "status": "LIVE", "market": "condition-1",
    }])
    gateway._client = client
    monkeypatch.setattr(
        gateway_module, "_safe_gateway_diagnostic",
        lambda *_args, **_kwargs: pytest.fail("diagnostic opt-in was not requested"),
    )
    try:
        rows = await gateway.open_orders()
        assert len(rows) == 1 and isinstance(rows[0].price, float)
        assert await gateway.get_order("order-1") == client.rows["order-1"]
        assert await gateway.get_trades(market="condition-1") == []
        assert await gateway.cancel(["order-1"])
    finally:
        gateway.close()

    assert gateway_module.ExecutionGateway.cancel.__kwdefaults__["safe_diagnostics"] is False
    assert gateway_module.ExecutionGateway.open_orders.__kwdefaults__["safe_diagnostics"] is False
    assert gateway_module.ExecutionGateway.get_order.__kwdefaults__["safe_diagnostics"] is False
    assert gateway_module.ExecutionGateway.get_trades.__kwdefaults__["safe_diagnostics"] is False
    assert client.cancel_calls == [("order-1",)]
    assert blocked_network == []


async def _gateway_outcome(scope, response=None, *, post_error=None, quotes=None):
    gateway = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient(response, post_error=post_error)
    gateway._client = client

    async def inline_io(fn, *args):
        return fn(*args)

    gateway._io = inline_io
    try:
        try:
            outcome = await gateway.place(
                list(scope.quotes if quotes is None else quotes), scope.market,
                force_post_only=True,
                strict_placement=True,
                allow_two_order_rdp_pair=True,
                token_ticks={"yes-token": .01, "no-token": .01},
                submit_guard=lambda: True,
            )
        except PlacementUnknown as exc:
            outcome = exc
        return outcome, client
    finally:
        gateway.close()


def _accepted_rows(yes_id="yes-order", no_id="no-order"):
    return [
        {"success": True, "orderID": yes_id},
        {"success": True, "orderID": no_id},
    ]


def _arm(scope):
    return scope.session.arm_initial_normal_placement(scope.admission)


def _prepare_real_admission(scope):
    """Warm actual retained filter and account-read paths for admission tests."""
    service = RDPMarketDataService(scope.bootstrap)
    service.connection_generation = 3
    service.metrics.update(received=2, processed=2)
    now_mono = time.monotonic()
    base_mono = now_mono - 2.5
    for token, bid, ask in (("yes-token", .56, .60), ("no-token", .42, .48)):
        book = service.book(token)
        assert book is not None
        tick = .01
        book.set_tick_size(tick)
        book.apply_snapshot([(bid - tick, 10), (bid, 10)], [(ask, 10)], 1)
        book.snapshot_generation = 3
        assert service.book_filter.observe_book(
            token, book, base_mono, 3, condition_id=scope.market.condition_id,
        )
        assert service.book_filter.observe_book(
            token, book, base_mono + 2.2, 3, condition_id=scope.market.condition_id,
        )
        assert service.book_filter.observe_trade(
            TradePrint(token, scope.market.condition_id, Side.BUY, bid, 1, 2),
            now_mono - .1, 3,
        )
    scope.session.public_session.service = service
    account_gateway = FakeAccountReadGateway()
    scope.session.user_session.gateway = account_gateway
    scope.session.user_session.observation.account = SimpleNamespace(
        signer=account_gateway.address, funder=account_gateway.funder,
    )
    return service, account_gateway


class FakePrefillGateway:
    def __init__(self, rows, *, hook=None, error=None):
        self.rows = rows
        self.hook = hook
        self.error = error
        self.calls = []

    async def open_orders(self, *, strict_account, exact_decimal, safe_diagnostics):
        self.calls.append((strict_account, exact_decimal, safe_diagnostics))
        assert strict_account is True and exact_decimal is True and safe_diagnostics is True
        if self.hook is not None:
            result = self.hook()
            if asyncio.iscoroutine(result):
                await result
        if self.error is not None:
            raise self.error
        return self.rows() if callable(self.rows) else list(self.rows)


def _prefill_rows(attempt, *, remaining=None):
    rows = []
    for quote in attempt.quotes:
        size = (remaining or {}).get(quote.token_id, Decimal(str(quote.size)))
        rows.append(ExactOpenOrder(
            "yes-order" if quote.token_id == "yes-token" else "no-order",
            quote.token_id, quote.side, Decimal(str(quote.price)), size,
        ))
    return rows


def _retarget_prefill_market_to_current_epoch(scope):
    epoch = ((int(time.time()) // 300) + 1) * 300
    end_epoch = epoch + 300
    market = replace(
        scope.market,
        slug=f"btc-updown-5m-{epoch}",
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat().replace("+00:00", "Z"),
    )
    bootstrap = replace(scope.bootstrap, market=market, epoch=epoch, end_epoch=end_epoch)
    scope.market = market
    scope.bootstrap = bootstrap
    scope.session.bootstrap = bootstrap
    scope.session.placement_owner._bootstrap = bootstrap
    scope.admission = replace(scope.admission, slug=market.slug)
    return market


@pytest.mark.asyncio
async def test_initial_prefill_valid_pair_reads_gamma_and_exact_gateway_then_returns_rest(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    status_calls = []
    original_status = runtime.read_rdp_market_status

    async def capture_status(bootstrap, origin, *, now):
        status_calls.append((bootstrap, origin))
        return await original_status(bootstrap, origin, now=now)

    monkeypatch.setattr(runtime, "read_rdp_market_status", capture_status)
    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "REST" and result.reason == "ACKNOWLEDGED_PAIR_STILL_VALID"
    assert result.market_status is not None and result.market_status.age_s is not None
    assert (result.order_receipt_ts is not None and result.order_age_s is not None
            and 0 <= result.order_age_s < scope.session._account_max_age_s)
    assert result.book_filter is not None and result.book_filter.ready
    assert result.capital_projection is not None and result.capital_projection.certified
    assert result.current_candidate is not None
    assert result.execution_authorized is False
    assert status_calls == [(scope.bootstrap, scope.session._gamma_origin)]
    assert gateway.calls == [(True, True, True)]
    assert not hasattr(gateway, "cancel_calls")
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("token_id", ["yes-token", "no-token"])
async def test_initial_prefill_closes_when_either_current_quote_moves(
    placement_scope, blocked_network, token_id,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    book = scope.session.public_session.service.book(token_id)
    assert book is not None
    bid = .57 if token_id == "yes-token" else .43
    book.apply_snapshot([(bid, 10)], [(bid + .03, 10)], 3)
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE" and result.reason == "QUOTE_PRICE_CHANGED"
    assert result.current_candidate is not None
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_closes_on_price_gate_or_book_filter_rejection(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    service = scope.session.public_session.service
    yes_book = service.book("yes-token")
    assert yes_book is not None
    yes_book.apply_snapshot([(.80, 10)], [(.82, 10)], 3)
    no_book = service.book("no-token")
    assert no_book is not None
    no_book.apply_snapshot([(.30, 10)], [(.32, 10)], 3)
    price_gate = await scope.session.evaluate_initial_prefill(attempt)
    assert price_gate.action == "CLOSE" and price_gate.reason == "PRICE_GATE"

    service.book_filter.evaluate = lambda *_args, **_kwargs: SimpleNamespace(
        ready=True, rejected=True, reasons=("INACTIVE_YES",),
    )
    rejected = await scope.session.evaluate_initial_prefill(attempt)
    assert rejected.action == "CLOSE" and rejected.reason == "INACTIVE_YES"
    assert rejected.book_filter is not None and rejected.book_filter.rejected
    assert gateway.calls == [(True, True, True), (True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filter_reason",
    ["INACTIVE_YES", "INACTIVE_NO", "DEPTH_EXTREME", "FAST_MOVE_YES", "FAST_MOVE_NO"],
)
async def test_initial_prefill_closes_for_each_book_filter_rejection(
    placement_scope, blocked_network, filter_reason,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    scope.session.public_session.service.book_filter.evaluate = (
        lambda *_args, **_kwargs: SimpleNamespace(
            ready=True, rejected=True, reasons=(filter_reason,),
        )
    )

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "CLOSE" and result.reason == filter_reason
    assert result.book_filter is not None and result.book_filter.rejected
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_price_gate_equality_is_not_rejected(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    service = scope.session.public_session.service
    for token_id in ("yes-token", "no-token"):
        book = service.book(token_id)
        assert book is not None
        book.apply_snapshot([(.50, 10)], [(.52, 10)], 4)

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "CLOSE"
    assert result.reason == "QUOTE_PRICE_CHANGED"
    assert result.reason != "PRICE_GATE"
    assert result.current_candidate is not None
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("token_id", ["yes-token", "no-token"])
async def test_initial_prefill_dynamic_tick_change_on_either_token_closes(
    placement_scope, blocked_network, token_id,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    book = scope.session.public_session.service.book(token_id)
    assert book is not None
    book.set_tick_size(.02)
    scope.session.book_filter_result = lambda: SimpleNamespace(
        ready=True, rejected=False, reasons=(),
    )

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "CLOSE" and result.reason == "QUOTE_PRICE_CHANGED"
    assert result.current_candidate is not None
    changed = next(q for q in result.current_candidate.quotes if q.token_id == token_id)
    submitted = next(q for q in attempt.quotes if q.token_id == token_id)
    assert changed.price != submitted.price
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_tiny_exact_remaining_reduction_requires_close(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    tiny = Decimal("1e-25")
    reduced = {
        "yes-token": Decimal(str(attempt.quotes[0].size)) - tiny,
    }
    gateway = FakePrefillGateway(_prefill_rows(attempt, remaining=reduced))
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE"
    assert result.reason == "OPEN_ORDER_REMAINING_QUANTITY_CHANGED"
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row_change", ["missing", "foreign", "duplicate", "wrong-price", "wrong-side", "wrong-token"],
)
async def test_initial_prefill_rejects_unowned_or_contradictory_open_orders(
    placement_scope, blocked_network, row_change,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    rows = _prefill_rows(attempt)
    if row_change == "missing":
        rows.pop()
    elif row_change == "foreign":
        rows.append(ExactOpenOrder(
            "manual-order", "yes-token", Side.BUY, Decimal(".20"), Decimal("5"),
        ))
    elif row_change == "duplicate":
        rows.append(rows[0])
    elif row_change == "wrong-price":
        rows[0] = replace(rows[0], price=rows[0].price + Decimal(".01"))
    elif row_change == "wrong-side":
        rows[0] = replace(rows[0], side=Side.SELL)
    else:
        rows[0] = replace(rows[0], token_id="foreign-token")
    gateway = FakePrefillGateway(rows)
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE"
    assert result.reason in {
        "ACKNOWLEDGED_ORDER_MISSING", "FOREIGN_SELECTED_OPEN_ORDER",
        "DUPLICATE_OPEN_ORDER_ID", "OPEN_ORDER_IDENTITY_OR_PRICE_MISMATCH",
    }
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["wrong-row-type", "float-price", "nan-quantity"])
async def test_initial_prefill_malformed_open_order_evidence_closes(
    placement_scope, blocked_network, monkeypatch, malformed,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    rows = _prefill_rows(attempt)
    if malformed == "wrong-row-type":
        rows[0] = {"order_id": "yes-order"}
    elif malformed == "float-price":
        rows[0] = replace(rows[0], price=.39)
    else:
        rows[0] = replace(rows[0], remaining_size=Decimal("NaN"))
    gateway = FakePrefillGateway(rows)
    scope.session.user_session.gateway = gateway

    async def malformed_read(_self):
        return [
            (OpenOrder(row.order_id, row.token_id, row.side, row.price,
                       row.remaining_size, OrderState.LIVE)
             if isinstance(row, ExactOpenOrder) else row)
            for row in rows
        ]

    monkeypatch.setattr(runtime._StrictCloseGateway, "open_orders", malformed_read)

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "CLOSE"
    assert result.reason == "MALFORMED_OPEN_ORDER_EVIDENCE"
    # The strict wrapper is replaced here so malformed normalized evidence
    # reaches the pre-fill validator instead of failing in the lower adapter.
    assert gateway.calls == []
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_owned_order_id_collision_on_foreign_token_closes(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    rows = _prefill_rows(attempt)
    rows.append(replace(rows[0], token_id="foreign-token"))
    gateway = FakePrefillGateway(rows)
    scope.session.user_session.gateway = gateway

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "CLOSE" and result.reason == "DUPLICATE_OPEN_ORDER_ID"
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_confirmed_fill_during_reads_precedes_quote_movement(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(q for q in attempt.quotes if q.token_id == "yes-token")
    remaining = {"yes-token": Decimal(str(yes_quote.size)) - Decimal("1")}
    base_rows = _prefill_rows(attempt, remaining=remaining)

    def during_open_read():
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=time.monotonic_ns(),
            trade_id="prefill-race-fill", size=1, price=yes_quote.price,
        )
        book = scope.session.public_session.service.book("yes-token")
        assert book is not None
        book.apply_snapshot([(.57, 10)], [(.60, 10)], 4)

    gateway = FakePrefillGateway(base_rows, hook=during_open_read)
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "FILL_WINDOW"
    assert result.reason == "CONFIRMED_FILL_HANDOFF"
    assert result.fill_window is not None and result.fill_window.started
    assert result.inventory is not None and result.inventory.qy == 1
    assert result.current_candidate is None
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_read_failure_overrides_confirmation_during_gamma_read(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(q for q in attempt.quotes if q.token_id == "yes-token")
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway

    async def failed_status(bootstrap, _origin, *, now):
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=time.monotonic_ns(),
            trade_id="prefill-before-gamma-failure", size=1, price=yes_quote.price,
        )
        raise RDPBootstrapError("MARKET_STATUS_LOOKUP_FAILED", "sanitized")

    monkeypatch.setattr(runtime, "read_rdp_market_status", failed_status)
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE" and result.reason == "MARKET_STATUS_LOOKUP_FAILED"
    assert result.fill_window is None
    assert gateway.calls == []
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_order_read_failure_overrides_confirmation_during_read(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    quote = next(q for q in attempt.quotes if q.token_id == "yes-token")

    def fill_then_fail():
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=time.monotonic_ns(),
            trade_id="prefill-before-order-failure", size=1, price=quote.price,
        )

    gateway = FakePrefillGateway(
        _prefill_rows(attempt), hook=fill_then_fail, error=RuntimeError("private"),
    )
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE" and result.reason.startswith("ORDER_STATE_READ_FAILED:")
    assert "private" not in result.reason
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["delayed-read-error", "delayed-registry-change"])
async def test_initial_prefill_terminal_failure_recaptures_freshness_and_event(
    placement_scope, blocked_network, monkeypatch, failure,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    scope.session._market_status_max_age_s = 100
    scope.session._account_max_age_s = 100
    origin = time.time()
    clock = [origin]
    scope.session._now = lambda: clock[0]

    async def fresh_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, origin,
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fresh_status)

    def delayed_failure():
        clock[0] += 11
        if failure == "delayed-registry-change":
            scope.store.register_owned_order(
                "yes-order", scope.market.condition_id, "yes-token", Side.BUY,
                5, .40, "ITER-0022-test-mutation",
            )

    gateway = FakePrefillGateway(
        _prefill_rows(attempt), hook=delayed_failure,
        error=(RuntimeError("private") if failure == "delayed-read-error" else None),
    )
    scope.session.user_session.gateway = gateway

    with capture_logs() as events:
        result = await scope.session.evaluate_initial_prefill(attempt)

    if failure == "delayed-read-error":
        assert result.reason == "ORDER_STATE_READ_FAILED:RuntimeError"
        assert result.order_receipt_ts is None and result.order_age_s is None
    else:
        assert result.reason == "OWNERSHIP_SCOPE_CONTRADICTION"
        assert result.order_receipt_ts is not None and result.order_age_s is not None
        assert result.order_age_s >= 11
    assert result.action == "CLOSE"
    assert result.captured_at == clock[0]
    assert result.market_status is not None and result.market_status.age_s == 11
    evidence = [event for event in events
                if event.get("event") == "rdp_initial_prefill_decision"]
    assert evidence
    event_fields = evidence[-1]
    assert event_fields["captured_at"] == result.captured_at
    assert event_fields["market_status_age_s"] == result.market_status.age_s
    assert event_fields["order_receipt_ts"] == result.order_receipt_ts
    assert event_fields["order_age_s"] == result.order_age_s
    assert event_fields["reason"] == result.reason
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence_kind", "at_limit", "expected_action", "expected_reason"),
    [
        ("market-status", True, "CLOSE", "MARKET_STATUS_STALE"),
        ("market-status", False, "REST",
         "ACKNOWLEDGED_PAIR_STILL_VALID"),
        ("order-state", True, "CLOSE", "ORDER_STATE_STALE"),
        ("order-state", False, "REST",
         "ACKNOWLEDGED_PAIR_STILL_VALID"),
    ],
)
async def test_initial_prefill_terminal_freshness_boundary_matches_result_and_event(
    placement_scope, blocked_network, monkeypatch, evidence_kind, at_limit,
    expected_action, expected_reason,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    scope.session._market_status_max_age_s = 10 if evidence_kind == "market-status" else 100
    scope.session._account_max_age_s = 100 if evidence_kind == "market-status" else 10
    scope.session._market_data_max_age_s = 100
    origin = time.time()
    terminal_time = origin + 10.0
    if not at_limit:
        terminal_time = math.nextafter(terminal_time, origin)
    age = float(Decimal(str(terminal_time)) - Decimal(str(origin)))
    clock = [origin]
    scope.session._now = lambda: clock[0]

    async def fresh_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, origin,
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fresh_status)
    original_builder = runtime.build_normal_quotes

    def advance_clock_after_candidate(*args, **kwargs):
        candidate = original_builder(*args, **kwargs)
        # Earlier post-read checks see fresh evidence. The one final capture
        # happens only after candidate construction and must govern the action.
        clock[0] = terminal_time
        return candidate

    monkeypatch.setattr(runtime, "build_normal_quotes", advance_clock_after_candidate)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway

    with capture_logs() as events:
        result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == expected_action and result.reason == expected_reason
    assert result.captured_at == terminal_time
    assert result.market_status is not None
    assert result.market_status.age_s == age
    assert result.order_receipt_ts is not None and result.order_age_s == age
    event = next(
        row for row in reversed(events)
        if row.get("event") == "rdp_initial_prefill_decision"
    )
    assert event["action"] == result.action
    assert event["reason"] == result.reason
    assert event["captured_at"] == result.captured_at
    assert event["market_status_age_s"] == result.market_status.age_s
    assert event["order_age_s"] == result.order_age_s
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_fill_during_healthy_gamma_read_keeps_original_deadline(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(q for q in attempt.quotes if q.token_id == "yes-token")
    gateway = FakePrefillGateway(_prefill_rows(
        attempt, remaining={"yes-token": Decimal(str(yes_quote.size)) - Decimal("1")},
    ))
    scope.session.user_session.gateway = gateway
    receipt_ns = time.monotonic_ns()
    initial_book = scope.session.public_session.service.book("yes-token")
    assert initial_book is not None

    async def delayed_status(bootstrap, _origin, *, now):
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=receipt_ns,
            trade_id="prefill-gamma-delayed-fill", size=1, price=yes_quote.price,
        )
        initial_book.apply_snapshot([(.57, 10)], [(.60, 10)], 5)
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, now(),
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", delayed_status)

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "FILL_WINDOW" and result.reason == "CONFIRMED_FILL_HANDOFF"
    assert result.fill_window is not None and result.fill_window.started
    assert result.fill_window.first_receipt_monotonic_ns == receipt_ns
    assert result.fill_window.deadline_monotonic_ns == (
        receipt_ns + round(scope.session._fill_window_s * 1_000_000_000)
    )
    assert result.current_candidate is None
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["quarantined", "failed"])
async def test_initial_prefill_quarantined_or_failed_fill_evidence_closes(
    placement_scope, blocked_network, evidence,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    if evidence == "quarantined":
        scope.processor.quarantine_order("yes-order", "synthetic", notify=False)
    else:
        _send_window_trade(
            scope, "yes-order", status=TradeState.FAILED,
            trade_id="prefill-failed-fill", receipt_ns=time.monotonic_ns(),
        )
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "CLOSE"
    assert not (result.fill_window is not None and result.fill_window.started)
    assert result.reason in {
        "FILL_EVIDENCE_QUARANTINED", "FILL_EVIDENCE_UNRELIABLE",
        "CONFIRMED_RECEIPT_UNAVAILABLE",
    }
    assert gateway.calls == []
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_existing_confirmed_window_hands_off_without_rest_reads(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=time.monotonic_ns(),
        trade_id="prefill-before-call", size=1,
    )
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "FILL_WINDOW" and result.fill_window is not None
    assert result.fill_window.started
    assert gateway.calls == []
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["health", "generation", "backlog", "stale-book"])
async def test_initial_prefill_unreliable_public_state_never_returns_rest(
    placement_scope, blocked_network, failure,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    service = scope.session.public_session.service
    if failure == "health":
        scope.session.public_session.failure_category = lambda _generation: "FEED_DISCONNECTED"
    elif failure == "generation":
        service.connection_generation += 1
    elif failure == "backlog":
        service.metrics["received"] += 1
    else:
        book = service.book("yes-token")
        assert book is not None
        book.local_ts -= scope.session._market_data_max_age_s + 1
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE"
    assert result.reason in {
        "FEED_DISCONNECTED", "INPUT_GENERATION_CHANGED", "PUBLIC_INPUT_BACKLOG",
        "MARKET_DATA_STALE",
    }
    if failure in {"health", "generation", "backlog"}:
        assert gateway.calls == []
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["matched", "confirmed-only"])
async def test_initial_prefill_unadmitted_fill_evidence_cannot_return_rest_or_handoff(
    placement_scope, blocked_network, evidence,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    quote = next(q for q in attempt.quotes if q.token_id == "yes-token")
    if evidence == "matched":
        _send_window_trade(
            scope, "yes-order", status=TradeState.MATCHED,
            trade_id="prefill-matched-only", receipt_ns=time.monotonic_ns(),
            size=1, price=quote.price,
        )
    else:
        _send_window_trade(
            scope, "yes-order", status=TradeState.CONFIRMED,
            trade_id="prefill-confirmed-only", receipt_ns=time.monotonic_ns(),
            size=1, price=quote.price,
        )
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE"
    assert result.reason in {
        "PENDING_CAPTURE_UNRESOLVED", "FILL_EVIDENCE_UNRELIABLE",
        "CONFIRMED_RECEIPT_UNAVAILABLE",
    }
    assert not (result.fill_window is not None and result.fill_window.started)
    assert len(gateway.calls) <= 1
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_stale_gamma_status_closes_before_order_read(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway

    async def stale_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False,
            now() - scope.session._market_status_max_age_s,
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", stale_status)
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE" and result.reason == "MARKET_STATUS_STALE"
    assert result.market_status is not None
    assert gateway.calls == []
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_rechecks_status_and_order_age_at_terminal_capture(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    scope.session._market_status_max_age_s = 100
    scope.session._account_max_age_s = 5
    origin = time.time()
    clock = [origin]
    scope.session._now = lambda: clock[0]

    async def fresh_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, origin,
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fresh_status)

    class AdvancingClockGateway(FakePrefillGateway):
        async def open_orders(self, **kwargs):
            result = await super().open_orders(**kwargs)
            clock[0] += 6
            return result

    gateway = AdvancingClockGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE" and result.reason == "ORDER_STATE_STALE"
    assert result.order_age_s is not None and result.order_age_s >= 5
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_overlap_and_caller_cancellation_keep_read_owned(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_open_read():
        entered.set()
        await release.wait()

    gateway = FakePrefillGateway(_prefill_rows(attempt), hook=hold_open_read)
    scope.session.user_session.gateway = gateway
    caller = asyncio.create_task(scope.session.evaluate_initial_prefill(attempt))
    await entered.wait()
    active = scope.session._prefill_task
    assert active is not None and not active.done()
    overlap = await scope.session.evaluate_initial_prefill(attempt)
    assert overlap.action == "CLOSE" and overlap.reason == "PREFILL_EVALUATION_IN_PROGRESS"
    with pytest.raises(RDPPlacementError, match="PREFILL_EVALUATION_IN_PROGRESS"):
        await scope.session.close_initial_normal_placement(
            attempt, deadline=asyncio.get_running_loop().time() + 1,
        )
    caller.cancel()
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    assert not caller.done() and not active.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert active.done()
    assert scope.session._prefill_task is None
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_session_teardown_waits_for_owned_order_read(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_open_read():
        entered.set()
        await release.wait()

    gateway = FakePrefillGateway(_prefill_rows(attempt), hook=hold_open_read)
    scope.session.user_session.gateway = gateway
    caller = asyncio.create_task(scope.session.evaluate_initial_prefill(attempt))
    await entered.wait()
    active = scope.session._prefill_task
    assert active is not None and not active.done()
    teardown = asyncio.create_task(scope.session.aclose())
    await asyncio.sleep(0)
    assert not teardown.done() and not active.done()
    release.set()
    result = await caller
    await teardown
    assert result.action == "CLOSE" and result.reason == "SESSION_CLOSED"
    assert active.done() and scope.session._prefill_task is None
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_cancellation_drains_real_gamma_and_gateway_executor_reads(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.catalog.gamma as gamma_module
    import polymaker.rdp_live as live_module
    import polymaker.rdp_runtime as runtime
    from polymaker.catalog.gamma import GammaClient as RealGammaClient

    scope = placement_scope
    epoch = ((int(time.time()) // 300) + 1) * 300
    end_epoch = epoch + 300
    market = replace(
        scope.market,
        slug=f"btc-updown-5m-{epoch}",
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat().replace("+00:00", "Z"),
    )
    bootstrap = replace(scope.bootstrap, market=market, epoch=epoch, end_epoch=end_epoch)
    scope.market = market
    scope.bootstrap = bootstrap
    scope.session.bootstrap = bootstrap
    scope.session.placement_owner._bootstrap = bootstrap
    scope.admission = replace(scope.admission, slug=market.slug)
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    monkeypatch.setattr(runtime, "read_rdp_market_status", live_module.read_rdp_market_status)

    clients = []

    class TrackingGammaClient(RealGammaClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed_by_context = False
            clients.append(self)

        async def aclose(self):
            await super().aclose()
            self.closed_by_context = True

    monkeypatch.setattr(gamma_module, "GammaClient", TrackingGammaClient)
    monkeypatch.setattr(live_module, "GammaClient", TrackingGammaClient)
    raw = {
        "acceptingOrders": True, "closed": False, "negRisk": False,
        "conditionId": market.condition_id, "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01, "orderMinSize": 5,
    }
    started, release = threading.Event(), threading.Event()

    def hold_sdk_read(*_args):
        started.set()
        if not release.wait(timeout=5):
            raise RuntimeError("fake SDK release timeout")

    gateway, client = _close_gateway_real_executor(scope, quotes)
    client.open_hook = hold_sdk_read
    try:
        with respx.mock(assert_all_called=True) as router:
            gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
                return_value=httpx.Response(200, json=[raw]),
            )
            caller = asyncio.create_task(scope.session.evaluate_initial_prefill(attempt))
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            active = scope.session._prefill_task
            assert active is not None and not active.done()
            assert gamma.call_count == 1
            assert dict(gamma.calls[0].request.url.params) == {
                "slug": market.slug, "limit": "1",
            }
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done() and not active.done()
            assert clients and clients[0].closed_by_context
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert active.done()
            assert clients[0].closed_by_context
            assert client.open_count == 1
            assert not gateway._account_io
            assert client.cancel_calls == []
    finally:
        release.set()
        gateway.close()
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_first_and_repeated_cancellation_drain_active_gamma_http(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.catalog.gamma as gamma_module
    import polymaker.rdp_live as live_module
    import polymaker.rdp_runtime as runtime
    from polymaker.catalog.gamma import GammaClient as RealGammaClient

    scope = placement_scope
    market = _retarget_prefill_market_to_current_epoch(scope)
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    monkeypatch.setattr(runtime, "read_rdp_market_status", live_module.read_rdp_market_status)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    clients = []

    class TrackingGammaClient(RealGammaClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed_by_context = False
            clients.append(self)

        async def aclose(self):
            await super().aclose()
            self.closed_by_context = True

    monkeypatch.setattr(gamma_module, "GammaClient", TrackingGammaClient)
    monkeypatch.setattr(live_module, "GammaClient", TrackingGammaClient)
    raw = {
        "acceptingOrders": True, "closed": False, "negRisk": False,
        "conditionId": market.condition_id, "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01, "orderMinSize": 5,
    }
    http_entered, release_http = asyncio.Event(), asyncio.Event()

    async def hold_gamma_http(_request):
        http_entered.set()
        await release_http.wait()
        return httpx.Response(200, json=[raw])

    try:
        with respx.mock(assert_all_called=True) as router:
            gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
                side_effect=hold_gamma_http,
            )
            caller = asyncio.create_task(scope.session.evaluate_initial_prefill(attempt))
            await asyncio.wait_for(http_entered.wait(), timeout=2)
            active = scope.session._prefill_task
            assert active is not None and not active.done()
            assert clients and not clients[0].closed_by_context
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done() and not active.done()
            assert gateway.calls == []
            release_http.set()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert active.done() and scope.session._prefill_task is None
            assert gamma.call_count == 1
            assert clients[0].closed_by_context
    finally:
        release_http.set()

    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_teardown_drains_actual_gateway_executor_read(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.catalog.gamma as gamma_module
    import polymaker.rdp_live as live_module
    import polymaker.rdp_runtime as runtime
    from polymaker.catalog.gamma import GammaClient as RealGammaClient

    scope = placement_scope
    market = _retarget_prefill_market_to_current_epoch(scope)
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    monkeypatch.setattr(runtime, "read_rdp_market_status", live_module.read_rdp_market_status)
    clients = []

    class TrackingGammaClient(RealGammaClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed_by_context = False
            clients.append(self)

        async def aclose(self):
            await super().aclose()
            self.closed_by_context = True

    monkeypatch.setattr(gamma_module, "GammaClient", TrackingGammaClient)
    monkeypatch.setattr(live_module, "GammaClient", TrackingGammaClient)
    raw = {
        "acceptingOrders": True, "closed": False, "negRisk": False,
        "conditionId": market.condition_id, "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01, "orderMinSize": 5,
    }
    started, release = threading.Event(), threading.Event()

    def hold_sdk_read(*_args):
        started.set()
        if not release.wait(timeout=5):
            raise RuntimeError("fake SDK release timeout")

    gateway, client = _close_gateway_real_executor(scope, quotes)
    client.open_hook = hold_sdk_read
    try:
        with respx.mock(assert_all_called=True) as router:
            gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
                return_value=httpx.Response(200, json=[raw]),
            )
            caller = asyncio.create_task(scope.session.evaluate_initial_prefill(attempt))
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            active = scope.session._prefill_task
            assert active is not None and not active.done()
            assert gamma.call_count == 1
            teardown = asyncio.create_task(scope.session.aclose())
            await asyncio.sleep(0)
            assert not teardown.done() and not active.done()
            release.set()
            result = await caller
            await teardown
    finally:
        release.set()
        gateway.close()

    assert result.action == "CLOSE" and result.reason == "SESSION_CLOSED"
    assert active.done() and scope.session._prefill_task is None
    assert clients and clients[0].closed_by_context
    assert client.open_count == 1
    assert not gateway._account_io
    assert client.cancel_calls == []
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_market_expiry_and_untradable_status_close_fail_closed(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    gateway = FakePrefillGateway(_prefill_rows(attempt))
    scope.session.user_session.gateway = gateway
    scope.session._now = lambda: float(scope.bootstrap.end_epoch)
    expired = await scope.session.evaluate_initial_prefill(attempt)
    assert expired.action == "CLOSE" and expired.reason == "MARKET_EXPIRED"
    assert gateway.calls == []

    scope.session._now = time.time

    async def untradable_status(_bootstrap, _origin, *, now):
        raise RDPBootstrapError("MARKET_NOT_TRADABLE", "sanitized")

    monkeypatch.setattr(runtime, "read_rdp_market_status", untradable_status)
    untradable = await scope.session.evaluate_initial_prefill(attempt)
    assert untradable.action == "CLOSE" and untradable.reason == "MARKET_NOT_TRADABLE"
    assert gateway.calls == []
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_attempt_registry_mutation_during_read_closes(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)
    changed = replace(attempt, attempt_id=attempt.attempt_id + 1)

    def mutate_registry():
        scope.owner._current = changed

    gateway = FakePrefillGateway(_prefill_rows(attempt), hook=mutate_registry)
    scope.session.user_session.gateway = gateway
    result = await scope.session.evaluate_initial_prefill(attempt)
    assert result.action == "CLOSE" and result.reason == "PLACEMENT_ATTEMPT_CHANGED"
    assert scope.owner.current is changed
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_registered_economics_mutation_during_read_closes(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, _quotes = await _compose_accepted_initial_pair(scope)

    def mutate_economics():
        scope.store.register_owned_order(
            "yes-order", scope.market.condition_id, "yes-token", Side.BUY,
            5, .40, "ITER-0022-test-mutation",
        )

    gateway = FakePrefillGateway(_prefill_rows(attempt), hook=mutate_economics)
    scope.session.user_session.gateway = gateway

    result = await scope.session.evaluate_initial_prefill(attempt)

    assert result.action == "CLOSE" and result.reason == "OWNERSHIP_SCOPE_CONTRADICTION"
    assert scope.owner.current is attempt
    assert gateway.calls == [(True, True, True)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_prefill_excludes_active_fill_window_coordinator(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(time.monotonic_ns())
    scope.session._monotonic_ns = clock.read
    sleeper_entered, release_sleep = asyncio.Event(), asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id="prefill-coordinator-fill",
    )
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade(
            "yes-order", "yes-token", ".39", "1",
            trade_id="prefill-coordinator-fill",
        ),),
    )
    coordinator = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        decision = await scope.session.evaluate_initial_prefill(attempt)
        assert decision.action == "CLOSE"
        assert decision.reason == "PLACEMENT_COORDINATION_IN_PROGRESS"
        assert client.open_count == 0 and client.cancel_calls == []
        clock.now_ns = clock.read() + round(scope.session._fill_window_s * 1_000_000_000)
        release_sleep.set()
        completion = await coordinator
    finally:
        release_sleep.set()
        gateway.close()

    assert completion.close_result.barrier_complete
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_admission_arms_and_gateway_ack_replays_confirmed_fills_once(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    assert scope.processor._pending == {
        ("yes-token", Side.BUY), ("no-token", Side.BUY),
    }
    assert attempt.execution_authorized is False

    matched = TradeEvent("yes-token", Side.BUY, .39, 2, "trade-1",
                         TradeState.MATCHED, 10, "yes-order",
                         scope.market.condition_id)
    confirmed = replace(matched, status=TradeState.CONFIRMED, ts=11)
    scope.processor.on_trade(matched, scope.market.condition_id)
    scope.processor.on_trade(confirmed, scope.market.condition_id)
    # A foreign token event is captured by the existing processor but is not
    # attributed to this pair and must not become RDP inventory.
    scope.processor.on_trade(
        TradeEvent("foreign-token", Side.BUY, .2, 7, "foreign-trade",
                   TradeState.MATCHED, 12, "foreign-order", "other-condition"),
        "other-condition",
    )

    outcome, client = await _gateway_outcome(scope, _accepted_rows())
    assert len(client.created) == 2 and len(client.posts) == 1
    assert client.posts[0][1] is True
    assert all(item.orderType == "GTC" for item in client.posts[0][0])
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)

    assert result.outcome == "COMPLETE" and not result.unresolved
    assert result.acknowledged_order_ids == ("yes-order", "no-order")
    assert result.observed_order_ids == ()
    assert result.execution_authorized is False
    assert scope.processor._pending == set()
    assert scope.processor._pending_events == []
    assert scope.processor.applied_fill_keys == {("trade-1", "yes-order")}
    snapshot = snapshot_inventory(
        scope.market, scope.processor, {"yes-order", "no-order"},
    )
    assert (snapshot.qy, snapshot.qn, snapshot.cy, snapshot.cn) == (2, 0, .78, 0)
    assert scope.store.inflight("yes-token") == 0
    assert scope.store.owned_order("yes-order")["submitted_qty"] == 5
    assert scope.store.orders["yes-order"].size == 5
    assert blocked_network == []


@pytest.mark.asyncio
async def test_real_initial_admission_arms_same_session_and_resolves_strict_gateway_pair(
    placement_scope, blocked_network,
):
    scope = placement_scope
    service, _account_gateway = _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    assert admission.eligible and admission.reason is None
    assert admission.book_filter is not None and admission.book_filter.ready
    assert admission.capital_projection is not None and admission.capital_projection.certified
    assert admission.capital_admission is not None and admission.capital_admission.admitted
    assert admission.execution_authorized is False
    attempt = scope.session.arm_initial_normal_placement(admission)
    assert scope.session.processor is scope.processor
    assert scope.session.placement_owner.current is attempt
    assert scope.processor._pending == {
        ("yes-token", Side.BUY), ("no-token", Side.BUY),
    }

    outcome, client = await _gateway_outcome(
        scope, _accepted_rows(), quotes=admission.candidate.quotes,
    )
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.outcome == "COMPLETE" and not result.execution_authorized
    assert result.acknowledged_order_ids == ("yes-order", "no-order")
    assert scope.session.placement_owner._processor is scope.session.processor
    assert all(scope.processor.owned_record(order_id) is not None
               for order_id in result.acknowledged_order_ids)
    assert len(client.posts) == 1 and client.posts[0][1] is True
    assert service.connection_generation == admission.public_generation == 3


@pytest.mark.asyncio
async def test_initial_intent_revalidation_accepts_exact_session_admission_and_clean_arm(
    placement_scope, blocked_network,
):
    scope = placement_scope
    _service, _account_gateway = _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert result.factual_valid and result.reason is None
    assert result.captured_at is not None
    assert result.market_status is not None and result.market_status.age_s is not None
    assert result.account_read_age_s is not None and result.account_read_age_s >= 0
    assert {token for token, _age in result.book_ages} == {"yes-token", "no-token"}
    assert result.book_filter is not None and result.book_filter.ready
    assert result.candidate is not None and result.candidate.reason is None
    assert result.candidate.quotes == attempt.quotes == admission.candidate.quotes
    assert result.token_ticks == admission.token_ticks
    assert result.minimum_sizes == admission.minimum_sizes
    assert result.capital_projection is not None and result.capital_projection.certified
    assert result.capital_admission is not None and result.capital_admission.admitted
    assert result.capital_admission.protected_amount == Decimal("5")
    assert "HUMAN_LIVE_AUTHORIZATION" in result.outstanding_execution_gates
    assert result.execution_authorized is False
    assert scope.session.initial_intent_validation_results == (result,)
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate_while_signing", [False, True])
async def test_real_gateway_guard_revalidates_on_loop_before_and_after_signing(
    placement_scope, blocked_network, invalidate_while_signing,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    loop = asyncio.get_running_loop()
    caller_threads = []
    capture_threads = []
    original_revalidate = scope.session.revalidate_initial_normal_intent
    original_capture = scope.session._capture_initial_normal_intent

    def record_callback_thread(current_admission, current_attempt):
        caller_threads.append(threading.get_ident())
        return original_revalidate(current_admission, current_attempt)

    def record_capture_thread(current_admission, current_attempt):
        capture_threads.append(threading.get_ident())
        return original_capture(current_admission, current_attempt)

    scope.session.revalidate_initial_normal_intent = record_callback_thread
    scope.session._capture_initial_normal_intent = record_capture_thread
    changed = False

    def mutate_after_first_signature():
        nonlocal changed
        if not invalidate_while_signing or changed:
            return
        changed = True
        book = scope.session.public_session.service.book("yes-token")
        assert book is not None
        book.bids.clear()
        book.bids[.57] = 10
        book.asks.clear()
        book.asks[.60] = 10

    fake_client = FakeBatchClient(_accepted_rows(), on_create=lambda: (
        loop.call_soon_threadsafe(mutate_after_first_signature)
    ))
    gateway = ExecutionGateway(Config(), paper=False)
    gateway._client = fake_client
    try:
        placed = await gateway.place(
            list(admission.candidate.quotes), scope.market,
            submit_guard=scope.session.initial_normal_submit_guard(admission, attempt),
            force_post_only=True, strict_placement=True,
            allow_two_order_rdp_pair=True,
            token_ticks=dict(admission.token_ticks),
        )
    finally:
        await gateway.aclose()

    loop_thread = threading.get_ident()
    assert caller_threads == [loop_thread, fake_client.create_threads[0]]
    assert caller_threads[1] != loop_thread
    assert capture_threads == [loop_thread, loop_thread]
    assert len(fake_client.created) == 2
    assert [kwargs["options"].tick_size for _args, kwargs in fake_client.created] == [
        "0.01", "0.01",
    ]
    if invalidate_while_signing:
        assert changed and placed == [] and fake_client.posts == []
        assert [item.reason for item in scope.session.initial_intent_validation_results] == [
            None, "INTENT_ECONOMICS_OR_SIGNING_TICK_CHANGED",
        ]
    else:
        assert len(placed) == 2 and len(fake_client.posts) == 1
        assert fake_client.posts[0][1] is True
        assert all(item.factual_valid for item in scope.session.initial_intent_validation_results)
    assert all(item.execution_authorized is False
               for item in scope.session.initial_intent_validation_results)
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["copied-admission", "copied-attempt", "pending-buffer"])
async def test_initial_intent_revalidation_rejects_unprovenanced_or_changed_arm(
    placement_scope, blocked_network, mutation,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    if mutation == "copied-admission":
        candidate_admission = replace(admission)
        candidate_attempt = attempt
        expected = "ADMISSION_PROVENANCE_MISMATCH"
    elif mutation == "copied-attempt":
        changed_quote = replace(attempt.quotes[0], price=attempt.quotes[0].price + .01)
        candidate_admission = admission
        candidate_attempt = replace(attempt, quotes=(changed_quote, attempt.quotes[1]))
        expected = "ARMED_ATTEMPT_PROVENANCE_MISMATCH"
    else:
        scope.processor._pending_events.append((object(), None, None, None))
        candidate_admission = admission
        candidate_attempt = attempt
        expected = "PREACK_CAPTURE_NOT_CLEAN"

    result = scope.session.revalidate_initial_normal_intent(
        candidate_admission, candidate_attempt,
    )

    assert not result.factual_valid and result.reason == expected
    assert result.execution_authorized is False
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_intent_revalidation_rejects_foreign_session_admission(
    placement_scope, blocked_network,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    original_attempt = scope.session.arm_initial_normal_placement(admission)
    foreign_store = StateStore(":memory:")
    foreign_processor = UserEventProcessor(foreign_store, retain_confirmed_receipts=True)
    foreign_user = SimpleNamespace(
        observation=scope.session.user_session.observation,
        processor=foreign_processor, store=foreign_store,
        gateway=scope.session.user_session.gateway,
        failure_category=lambda _generation: None,
        aclose=_async_noop,
    )
    foreign_session = RDPInputSession(
        scope.bootstrap, public_session=scope.session.public_session,
        user_session=foreign_user, feed=scope.session.feed,
        backing=scope.session.backing, now=scope.session._now,
        sleep=scope.session._sleep, poll_interval_s=scope.session._poll_interval_s,
        started_at=scope.session.started_at,
        fill_window_s=scope.session._fill_window_s,
        monotonic_ns=scope.session._monotonic_ns,
    )
    foreign_attempt = foreign_session.arm_initial_normal_placement(admission)

    result = scope.session.revalidate_initial_normal_intent(
        admission, foreign_attempt,
    )

    assert not result.factual_valid and result.reason == "ARMED_ATTEMPT_PROVENANCE_MISMATCH"
    assert admission is scope.session._issued_initial_admission
    assert scope.session.placement_owner.current is original_attempt
    assert foreign_session.placement_owner.current is foreign_attempt
    await foreign_session.aclose()
    foreign_store.close()
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["yes-token", "no-token"])
async def test_initial_intent_revalidation_rejects_current_quote_change_for_either_token(
    placement_scope, blocked_network, token,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    book = scope.session.public_session.service.book(token)
    assert book is not None
    current = book.best_bid()
    ask = book.best_ask()
    assert current is not None and ask is not None
    book.bids.clear()
    book.bids[round(current.price + .01, 2)] = current.size
    book.asks.clear()
    book.asks[ask.price] = ask.size

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid
    assert result.reason in {
        "PRICE_GATE", "INTENT_ECONOMICS_OR_SIGNING_TICK_CHANGED",
    }
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["yes-token", "no-token"])
async def test_initial_intent_revalidation_rejects_tick_or_minimum_metadata_change(
    placement_scope, blocked_network, token,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    changed = tuple(
        replace(item, min_order_size=6)
        if item.token_id == token else item
        for item in scope.bootstrap.token_metadata
    )
    object.__setattr__(scope.bootstrap, "token_metadata", changed)

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid
    assert result.reason == "TOKEN_METADATA_CHANGED"
    assert result.minimum_sizes and dict(result.minimum_sizes)[token] == 6
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "buffered", ["matched", "confirmed", "malformed-id", "overflow", "quarantine", "foreign-store"],
)
async def test_initial_intent_revalidation_rejects_unexpected_local_economic_scope(
    placement_scope, blocked_network, buffered,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    if buffered in {"matched", "confirmed"}:
        event = TradeEvent(
            "yes-token", Side.BUY, .55, 1, f"buffered-{buffered}",
            TradeState.MATCHED if buffered == "matched" else TradeState.CONFIRMED,
            time.time(), "yes-order", scope.market.condition_id,
        )
        scope.processor.on_trade(
            event, scope.market.condition_id,
            received_at_us=1_800_000_000_000_000,
            received_monotonic_ns=time.monotonic_ns(), connection_generation=4,
        )
    elif buffered == "malformed-id":
        scope.processor._pending_malformed_order_ids.add("yes-order")
    elif buffered == "overflow":
        scope.processor._pending_limit = 0
        event = TradeEvent(
            "yes-token", Side.BUY, .55, 1, "overflow-intent", TradeState.MATCHED,
            time.time(), "yes-order", scope.market.condition_id,
        )
        scope.processor.on_trade(event, scope.market.condition_id)
    elif buffered == "quarantine":
        scope.processor.quarantined_orders.add("yes-order")
    else:
        scope.store.register_owned_order(
            "foreign-local", "other-condition", "foreign-token", Side.BUY, 1, .2, "other",
        )

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid and result.reason == "PREACK_CAPTURE_NOT_CLEAN"
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "public-generation", "user-generation", "public-health", "backlog", "expired", "closed",
])
async def test_initial_intent_revalidation_fails_closed_on_session_state_change(
    placement_scope, blocked_network, failure,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    if failure == "public-generation":
        scope.session.public_session.service.connection_generation += 1
        reason = "INPUT_GENERATION_CHANGED"
    elif failure == "user-generation":
        scope.session.user_session.observation.user_channel.connection_generation += 1
        reason = "INPUT_GENERATION_CHANGED"
    elif failure == "public-health":
        scope.session.public_session.failure_category = lambda _generation: "FEED_UNHEALTHY"
        reason = "FEED_UNHEALTHY"
    elif failure == "backlog":
        scope.session.public_session.service.metrics["received"] += 1
        reason = "PUBLIC_INPUT_BACKLOG"
    elif failure == "expired":
        now = scope.session._now()
        scope.session.bootstrap = replace(scope.session.bootstrap, end_epoch=now)
        scope.session.placement_owner._bootstrap = scope.session.bootstrap
        reason = "MARKET_EXPIRED"
    else:
        scope.session._closed = True
        reason = "SESSION_CLOSED"

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid and result.reason == reason
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence", "age", "expected"),
    [
        ("status", 9.999, None), ("status", 10.0, "MARKET_STATUS_STALE"),
        ("account", 9.999, None), ("account", 10.0, None),
        ("account", 10.001, "STALE_ACCOUNT_EVIDENCE"),
        ("yes-book", 4.999, None), ("yes-book", 5.0, "MARKET_DATA_STALE"),
        ("no-book", 4.999, None), ("no-book", 5.0, "MARKET_DATA_STALE"),
    ],
)
async def test_initial_intent_revalidation_uses_exact_current_freshness_boundaries(
    placement_scope, blocked_network, evidence, age, expected,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)

    captured_at = scope.session._now() + 1
    scope.session._now = lambda: captured_at
    scope.session._account_max_age_s = 10
    scope.session._market_data_max_age_s = 5
    if evidence == "status":
        status = admission.market_status
        assert status is not None
        object.__setattr__(status, "receipt_ts", captured_at - age)
    elif evidence == "account":
        account = scope.session._issued_initial_account
        assert account is not None
        for field in (
            "orders_receipt_ts", "positions_receipt_ts", "collateral_receipt_ts",
        ):
            object.__setattr__(account, field, captured_at - age)
        object.__setattr__(admission, "account_receipt_times", (captured_at - age,) * 3)
    else:
        token_id = "yes-token" if evidence == "yes-book" else "no-token"
        book = scope.session.public_session.service.book(token_id)
        assert book is not None
        book.local_ts = captured_at - age

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    if expected is None:
        assert result.factual_valid and result.reason is None
    else:
        assert not result.factual_valid and result.reason == expected
    assert result.captured_at == captured_at
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["future", "nan"])
async def test_initial_intent_revalidation_rejects_invalid_current_status_receipt(
    placement_scope, blocked_network, invalid,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    status = admission.market_status
    assert status is not None
    now = scope.session._now()
    object.__setattr__(status, "receipt_ts", now + 1 if invalid == "future" else float("nan"))

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid
    assert result.reason == (
        "MARKET_STATUS_RECEIPT_IN_FUTURE" if invalid == "future"
        else "INVALID_MARKET_STATUS_RECEIPT"
    )
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence", "invalid", "expected"),
    [
        ("account", "future", "INVALID_READ_TIMESTAMPS"),
        ("account", "nan", "INVALID_READ_TIMESTAMPS"),
        ("yes-book", "future", "MARKET_DATA_RECEIPT_IN_FUTURE"),
        ("yes-book", "nan", "INVALID_MARKET_DATA_RECEIPT"),
        ("no-book", "future", "MARKET_DATA_RECEIPT_IN_FUTURE"),
        ("no-book", "nan", "INVALID_MARKET_DATA_RECEIPT"),
    ],
)
async def test_initial_intent_revalidation_rejects_invalid_account_or_book_receipts(
    placement_scope, blocked_network, evidence, invalid, expected,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    captured_at = scope.session._now()
    invalid_receipt = captured_at + 1 if invalid == "future" else float("nan")
    if evidence == "account":
        account = scope.session._issued_initial_account
        assert account is not None
        for field in (
            "orders_receipt_ts", "positions_receipt_ts", "collateral_receipt_ts",
        ):
            object.__setattr__(account, field, invalid_receipt)
        object.__setattr__(
            admission, "account_receipt_times",
            None if invalid == "nan" else (invalid_receipt,) * 3,
        )
    else:
        token_id = evidence.removesuffix("-book") + "-token"
        book = scope.session.public_session.service.book(token_id)
        assert book is not None
        book.local_ts = invalid_receipt

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid and result.reason == expected
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backing", ["cash", "allowance"])
async def test_initial_intent_revalidation_rechecks_current_cash_and_allowance(
    placement_scope, blocked_network, backing,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    account = scope.session._issued_initial_account
    assert account is not None
    object.__setattr__(account, "balance_usd" if backing == "cash" else "allowance_usd", Decimal("29"))

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid
    assert result.reason == (
        "INSUFFICIENT_CASH_BACKING" if backing == "cash"
        else "INSUFFICIENT_ALLOWANCE_BACKING"
    )
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_state", ["rejected", "unready"])
async def test_initial_intent_revalidation_requires_ready_accepted_book_filter(
    placement_scope, blocked_network, filter_state,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    book_filter = admission.book_filter
    assert book_filter is not None
    changed_filter = replace(
        book_filter,
        ready=filter_state != "unready",
        rejected=filter_state == "rejected",
        reasons=("INACTIVE_YES",) if filter_state == "rejected" else ("BOOK_FILTER_UNREADY",),
    )
    scope.session.book_filter_result = lambda: changed_filter

    result = scope.session.revalidate_initial_normal_intent(admission, attempt)

    assert not result.factual_valid
    assert result.reason == (
        "BOOK_FILTER_REJECTED" if filter_state == "rejected" else "BOOK_FILTER_UNREADY"
    )
    assert result.book_filter is changed_filter
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_intent_bridge_times_out_without_evaluating_queued_mutable_state(
    placement_scope, blocked_network, monkeypatch,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    scope.session._account_max_age_s = .05
    loop = asyncio.get_running_loop()
    original_call_soon_threadsafe = loop.call_soon_threadsafe
    bridge_queued = threading.Event()
    queued_callbacks = []

    def observe_bridge_queue(callback, *args):
        if getattr(callback, "__name__", "") == "capture_on_owner_loop":
            queued_callbacks.append((callback, args))
            bridge_queued.set()
            return None
        return original_call_soon_threadsafe(callback, *args)

    monkeypatch.setattr(loop, "call_soon_threadsafe", observe_bridge_queue)
    captures = []

    def unexpected_capture(*_args):
        captures.append("capture")
        raise AssertionError("abandoned bridge must not capture mutable state")

    scope.session._capture_initial_normal_intent = unexpected_capture
    outcome = []
    validation_finished = threading.Event()
    validation_thread = threading.Thread(
        target=lambda: (
            outcome.append(scope.session.revalidate_initial_normal_intent(admission, attempt)),
            validation_finished.set(),
        ),
    )
    validation_thread.start()
    assert await asyncio.to_thread(bridge_queued.wait, 1)
    assert validation_finished.wait(.5)
    validation_thread.join()
    result = outcome[0]
    assert not result.factual_valid and result.reason == "VALIDATION_BRIDGE_TIMEOUT"
    assert captures == []
    queued_callbacks[0][0](*queued_callbacks[0][1])
    assert captures == []
    assert not scope.session._intent_bridge_requests
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_intent_bridge_teardown_abandons_queued_callback(
    placement_scope, blocked_network, monkeypatch,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    loop = asyncio.get_running_loop()
    original_call_soon_threadsafe = loop.call_soon_threadsafe
    queued_callbacks = []
    bridge_queued = threading.Event()

    def hold_bridge_callback(callback, *args):
        if getattr(callback, "__name__", "") == "capture_on_owner_loop":
            queued_callbacks.append((callback, args))
            bridge_queued.set()
            return None
        return original_call_soon_threadsafe(callback, *args)

    monkeypatch.setattr(loop, "call_soon_threadsafe", hold_bridge_callback)
    captures = []
    scope.session._capture_initial_normal_intent = lambda *_args: captures.append("capture")
    validation_task = asyncio.create_task(asyncio.to_thread(
        scope.session.revalidate_initial_normal_intent, admission, attempt,
    ))
    assert await asyncio.to_thread(bridge_queued.wait, 1)
    await scope.session.aclose()
    result = await validation_task
    assert not result.factual_valid and result.reason == "SESSION_CLOSED"
    assert queued_callbacks
    queued_callbacks[0][0](*queued_callbacks[0][1])
    assert captures == []
    assert not scope.session._intent_bridge_requests
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_intent_bridge_rejects_callback_delivered_after_wait_expiry(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    scope.session._account_max_age_s = .05
    loop = asyncio.get_running_loop()
    original_call_soon_threadsafe = loop.call_soon_threadsafe
    queued_callbacks = []
    bridge_queued = threading.Event()
    wait_expired = threading.Event()
    resume_worker = threading.Event()

    def hold_bridge_callback(callback, *args):
        if getattr(callback, "__name__", "") == "capture_on_owner_loop":
            queued_callbacks.append((callback, args))
            bridge_queued.set()
            return None
        return original_call_soon_threadsafe(callback, *args)

    base_request = runtime._IntentBridgeRequest

    class InterleavedWait(threading.Event):
        def wait(self, timeout=None):
            completed = super().wait(timeout)
            if timeout is not None and not completed:
                wait_expired.set()
                assert resume_worker.wait(1)
            return completed

    def request_with_interleaved_wait(admission_arg, attempt_arg, deadline):
        request = base_request(admission_arg, attempt_arg, deadline)
        request.completed = InterleavedWait()
        return request

    monkeypatch.setattr(loop, "call_soon_threadsafe", hold_bridge_callback)
    monkeypatch.setattr(runtime, "_IntentBridgeRequest", request_with_interleaved_wait)
    captures = []
    scope.session._capture_initial_normal_intent = lambda *_args: captures.append("capture")
    outcome = []
    worker_done = threading.Event()

    def run_validation():
        outcome.append(scope.session.revalidate_initial_normal_intent(admission, attempt))
        worker_done.set()

    worker = threading.Thread(target=run_validation)
    worker.start()
    assert await asyncio.to_thread(bridge_queued.wait, 1)
    assert await asyncio.to_thread(wait_expired.wait, 1)
    assert queued_callbacks
    queued_callbacks[0][0](*queued_callbacks[0][1])
    resume_worker.set()
    assert await asyncio.to_thread(worker_done.wait, 1)
    worker.join()

    result = outcome[0]
    assert not result.factual_valid and result.reason == "VALIDATION_BRIDGE_TIMEOUT"
    assert captures == []
    assert all(not item.factual_valid
               for item in scope.session.initial_intent_validation_results)
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_intent_bridge_does_not_publish_late_capture_result(
    placement_scope, blocked_network, monkeypatch,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    valid_capture = scope.session._capture_initial_normal_intent(admission, attempt)
    assert valid_capture.factual_valid
    scope.session._account_max_age_s = .05
    capture_started = threading.Event()
    release_capture = threading.Event()
    validation_finished = threading.Event()
    outcome = []

    def delayed_positive_capture(*_args):
        capture_started.set()
        assert release_capture.wait(1)
        return valid_capture

    scope.session._capture_initial_normal_intent = delayed_positive_capture

    def validate_from_worker():
        outcome.append(scope.session.revalidate_initial_normal_intent(admission, attempt))
        validation_finished.set()

    worker = threading.Thread(target=validate_from_worker)

    def release_only_after_timeout():
        assert capture_started.wait(1)
        assert validation_finished.wait(1)
        release_capture.set()

    controller = threading.Thread(target=release_only_after_timeout)
    controller.start()
    worker.start()
    assert await asyncio.to_thread(validation_finished.wait, 1)
    assert await asyncio.to_thread(capture_started.wait, 1)
    worker.join()
    controller.join()

    result = outcome[0]
    assert not result.factual_valid and result.reason == "VALIDATION_BRIDGE_TIMEOUT"
    assert scope.session.initial_intent_validation_results == (result,)
    assert blocked_network == []


@pytest.mark.asyncio
async def test_real_gateway_bridge_timeout_denies_final_guard_without_post(
    placement_scope, blocked_network, monkeypatch,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    scope.session._account_max_age_s = .05
    loop = asyncio.get_running_loop()
    original_call_soon_threadsafe = loop.call_soon_threadsafe
    queued_callbacks = []
    bridge_queued = threading.Event()

    def hold_bridge_callback(callback, *args):
        if getattr(callback, "__name__", "") == "capture_on_owner_loop":
            queued_callbacks.append((callback, args))
            bridge_queued.set()
            return None
        return original_call_soon_threadsafe(callback, *args)

    monkeypatch.setattr(loop, "call_soon_threadsafe", hold_bridge_callback)
    fake_client = FakeBatchClient(_accepted_rows())
    gateway = ExecutionGateway(Config(), paper=False)
    gateway._client = fake_client
    try:
        placed = await gateway.place(
            list(admission.candidate.quotes), scope.market,
            submit_guard=scope.session.initial_normal_submit_guard(admission, attempt),
            force_post_only=True, strict_placement=True,
            allow_two_order_rdp_pair=True,
            token_ticks=dict(admission.token_ticks),
        )
    finally:
        await gateway.aclose()

    assert bridge_queued.is_set() and queued_callbacks
    assert placed == [] and fake_client.posts == []
    assert len(fake_client.created) == 2
    assert [item.reason for item in scope.session.initial_intent_validation_results] == [
        None, "VALIDATION_BRIDGE_TIMEOUT",
    ]
    queued_callbacks[0][0](*queued_callbacks[0][1])
    assert [item.reason for item in scope.session.initial_intent_validation_results] == [
        None, "VALIDATION_BRIDGE_TIMEOUT",
    ]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_intent_bridge_rejects_closed_owner_loop(
    placement_scope, blocked_network,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    scope.session._intent_loop = closed_loop
    scope.session._intent_loop_thread_id = threading.get_ident() + 1

    result = await asyncio.to_thread(
        scope.session.revalidate_initial_normal_intent, admission, attempt,
    )

    assert not result.factual_valid and result.reason == "VALIDATION_LOOP_UNAVAILABLE"
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
async def test_real_gateway_first_guard_denial_prevents_signing_and_post(
    placement_scope, blocked_network,
):
    scope = placement_scope
    _prepare_real_admission(scope)
    admission = await scope.session.initial_normal_admission()
    attempt = scope.session.arm_initial_normal_placement(admission)
    book = scope.session.public_session.service.book("yes-token")
    assert book is not None
    book.bids.clear()
    book.bids[.80] = 10
    book.asks.clear()
    book.asks[.82] = 10
    fake_client = FakeBatchClient(_accepted_rows())
    gateway = ExecutionGateway(Config(), paper=False)
    gateway._client = fake_client
    try:
        placed = await gateway.place(
            list(admission.candidate.quotes), scope.market,
            submit_guard=scope.session.initial_normal_submit_guard(admission, attempt),
            force_post_only=True, strict_placement=True,
            allow_two_order_rdp_pair=True,
            token_ticks=dict(admission.token_ticks),
        )
    finally:
        await gateway.aclose()

    assert placed == [] and fake_client.created == [] and fake_client.posts == []
    assert [item.reason for item in scope.session.initial_intent_validation_results] == [
        "PRICE_GATE",
    ]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_admission_uses_fresh_exact_gamma_http_status(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    epoch = ((int(time.time()) // 300) + 1) * 300
    end_epoch = epoch + 300
    market = replace(
        scope.market,
        slug=f"btc-updown-5m-{epoch}",
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat().replace("+00:00", "Z"),
    )
    scope.market = market
    scope.bootstrap = replace(
        scope.bootstrap, market=market, epoch=epoch, end_epoch=end_epoch,
    )
    scope.session.bootstrap = scope.bootstrap
    scope.session.placement_owner.bootstrap = scope.bootstrap
    monkeypatch.setattr(runtime, "read_rdp_market_status", read_rdp_market_status)
    _service, _account_gateway = _prepare_real_admission(scope)
    raw = {
        "acceptingOrders": True,
        "closed": False,
        "negRisk": market.neg_risk,
        "conditionId": market.condition_id,
        "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01,
        "orderMinSize": 5,
    }
    with respx.mock(assert_all_called=True) as router:
        gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
            return_value=httpx.Response(200, json=[raw]),
        )
        admission = await scope.session.initial_normal_admission()
        repeated = await scope.session.initial_normal_admission()

    assert admission.eligible and admission.reason is None
    assert repeated.eligible and repeated.reason is None
    assert admission.market_status is not None
    status = admission.market_status
    assert status.slug == market.slug
    assert status.condition_id == market.condition_id
    assert status.token_ids == ("yes-token", "no-token")
    assert status.outcomes == ("Up", "Down")
    assert status.neg_risk is False
    assert status.end_epoch == end_epoch
    assert status.accepting_orders is True and status.closed is False
    assert 0 <= status.age_s < status.max_age_s
    assert "CURRENT_MARKET_TRADABILITY" not in admission.outstanding_execution_gates
    assert gamma.call_count == 2
    assert dict(gamma.calls[0].request.url.params) == {"slug": market.slug, "limit": "1"}
    assert blocked_network == []


@pytest.mark.asyncio
async def test_real_gamma_http_read_drains_and_closes_before_repeated_cancellation_returns(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_live as rdp_live
    import polymaker.rdp_runtime as runtime
    from polymaker.catalog.gamma import GammaClient as RealGammaClient

    scope = placement_scope
    epoch = ((int(time.time()) // 300) + 1) * 300
    end_epoch = epoch + 300
    market = replace(
        scope.market,
        slug=f"btc-updown-5m-{epoch}",
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat().replace("+00:00", "Z"),
    )
    scope.market = market
    scope.bootstrap = replace(scope.bootstrap, market=market, epoch=epoch, end_epoch=end_epoch)
    scope.session.bootstrap = scope.bootstrap
    scope.session.placement_owner.bootstrap = scope.bootstrap
    _prepare_real_admission(scope)
    monkeypatch.setattr(runtime, "read_rdp_market_status", read_rdp_market_status)

    created_clients = []

    class TrackingGammaClient(RealGammaClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed_by_context = False
            created_clients.append(self)

        async def aclose(self):
            await super().aclose()
            self.closed_by_context = True

    monkeypatch.setattr(rdp_live, "GammaClient", TrackingGammaClient)
    raw = {
        "acceptingOrders": True, "closed": False, "negRisk": False,
        "conditionId": market.condition_id, "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01, "orderMinSize": 5,
    }
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_response(_request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=[raw])

    with respx.mock(assert_all_called=True) as router:
        gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
            side_effect=delayed_response,
        )
        admission_task = asyncio.create_task(scope.session.initial_normal_admission())
        await entered.wait()
        status_task = scope.session._market_status_read_task
        assert status_task is not None and not status_task.done()
        admission_task.cancel()
        await asyncio.sleep(0)
        admission_task.cancel()
        await asyncio.sleep(0)
        assert not admission_task.done()
        assert not created_clients[0].closed_by_context
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await admission_task
    assert gamma.call_count == 1
    assert created_clients and created_clients[0].closed_by_context
    assert status_task.done() and scope.session._market_status_read_task is None
    assert scope.session._admission_task is None and not scope.session._admission_active
    assert not scope.session.user_session.gateway.calls
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age", "expected_reason"),
    [(1.0, None), (10.0, "MARKET_STATUS_STALE"),
     (11.0, "MARKET_STATUS_STALE"), (-1.0, "MARKET_STATUS_RECEIPT_IN_FUTURE"),
     (float("nan"), "INVALID_MARKET_STATUS_RECEIPT")],
)
async def test_initial_admission_status_age_is_strict_and_fail_closed(
    placement_scope, blocked_network, monkeypatch, age, expected_reason,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = [time.time()]
    scope.session._now = lambda: clock[0]

    async def age_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False,
            now() - age,
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", age_status)
    _prepare_real_admission(scope)
    result = await scope.session.initial_normal_admission()
    if expected_reason is None:
        assert result.eligible
        assert result.market_status is not None and result.market_status.age_s < 10
        assert "CURRENT_MARKET_TRADABILITY" not in result.outstanding_execution_gates
    else:
        assert not result.eligible and result.reason == expected_reason
        assert result.market_status is not None
        assert "CURRENT_MARKET_TRADABILITY" in result.outstanding_execution_gates
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("offset", "expected_reason"),
    [("0.0999", None), ("0.1", "MARKET_STATUS_STALE"),
     ("0.1001", "MARKET_STATUS_STALE")],
)
async def test_real_gamma_receipt_uses_exact_fractional_freshness_boundary(
    placement_scope, blocked_network, monkeypatch, offset, expected_reason,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    epoch = ((int(time.time()) // 300) + 1) * 300
    end_epoch = epoch + 300
    market = replace(
        scope.market,
        slug=f"btc-updown-5m-{epoch}",
        end_date_iso=datetime.fromtimestamp(end_epoch, UTC).isoformat().replace("+00:00", "Z"),
    )
    scope.market = market
    scope.bootstrap = replace(scope.bootstrap, market=market, epoch=epoch, end_epoch=end_epoch)
    scope.session.bootstrap = scope.bootstrap
    scope.session.placement_owner.bootstrap = scope.bootstrap
    scope.session._market_status_max_age_s = 0.1
    clock = [float(epoch + 2)]
    scope.session._now = lambda: clock[0]
    monkeypatch.setattr(runtime, "read_rdp_market_status", read_rdp_market_status)
    _service, gateway = _prepare_real_admission(scope)
    gateway.positions_hook = lambda: clock.__setitem__(
        0, float(epoch + 2) + float(offset),
    )
    raw = {
        "acceptingOrders": True, "closed": False, "negRisk": market.neg_risk,
        "conditionId": market.condition_id, "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01, "orderMinSize": 5,
    }
    with respx.mock(assert_all_called=True) as router:
        gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
            return_value=httpx.Response(200, json=[raw]),
        )
        result = await scope.session.initial_normal_admission()

    assert gamma.call_count == 1
    assert result.market_status is not None
    assert result.market_status.receipt_ts == float(epoch + 2)
    if expected_reason is None:
        assert result.eligible
        assert "CURRENT_MARKET_TRADABILITY" not in result.outstanding_execution_gates
    else:
        assert not result.eligible and result.reason == expected_reason
        assert "CURRENT_MARKET_TRADABILITY" in result.outstanding_execution_gates
    assert blocked_network == []


@pytest.mark.asyncio
async def test_fresh_market_status_does_not_clear_other_admission_gates(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = [time.time()]
    scope.session._now = lambda: clock[0]

    async def fresh_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, now(),
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fresh_status)
    _prepare_real_admission(scope)
    scope.session.user_session.gateway = None
    result = await scope.session.initial_normal_admission()
    assert not result.eligible and result.reason == "ACCOUNT_GATEWAY_UNAVAILABLE"
    assert result.market_status is not None
    assert "CURRENT_MARKET_TRADABILITY" not in result.outstanding_execution_gates
    assert result.outstanding_execution_gates == (
        "CURRENT_CYCLE_RECONCILIATION", "OPERATIONAL_TIME_SUFFICIENCY",
        "FINAL_SUBMIT_REVALIDATION", "HUMAN_LIVE_AUTHORIZATION",
    )
    assert blocked_network == []


@pytest.mark.asyncio
async def test_slow_account_read_cannot_admit_status_stale_at_joint_capture(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = [time.time()]
    scope.session._now = lambda: clock[0]

    async def fresh_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, now(),
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fresh_status)
    _service, gateway = _prepare_real_admission(scope)
    gateway.positions_hook = lambda: clock.__setitem__(0, clock[0] + 11)
    result = await scope.session.initial_normal_admission()
    assert not result.eligible and result.reason == "MARKET_STATUS_STALE"
    assert result.market_status is not None and result.market_status.age_s == 11
    assert not result.execution_authorized
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_reason", "failure_time", "expected_age"),
    [("categorized", "POSITIONS_UNAVAILABLE", 111.0, 11.0),
     ("unexpected", "ACCOUNT_READ_FAILED", 111.0, 11.0),
     ("unexpected", "ACCOUNT_READ_FAILED", 99.0, -1.0),
     ("unexpected", "ACCOUNT_READ_FAILED", float("nan"), None)],
)
async def test_terminal_account_read_failure_recaptures_market_status_freshness(
    placement_scope, blocked_network, monkeypatch, failure_kind, expected_reason,
    failure_time, expected_age,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = [100.0]
    scope.session._now = lambda: clock[0]
    scope.session._market_status_max_age_s = 10.0

    async def fresh_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, now(),
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fresh_status)
    _service, gateway = _prepare_real_admission(scope)

    def fail_positions():
        clock[0] = failure_time
        if failure_kind == "categorized":
            raise RDPAccountError("POSITIONS_UNAVAILABLE", "positions unavailable")
        raise RuntimeError("private-account-payload-sentinel")

    gateway.positions_hook = fail_positions
    result = await scope.session.initial_normal_admission()

    assert not result.eligible and result.reason == expected_reason
    assert result.market_status is not None
    assert result.market_status.age_s == expected_age
    assert result.market_status.max_age_s == 10.0
    assert "CURRENT_MARKET_TRADABILITY" in result.outstanding_execution_gates
    assert result.execution_authorized is False
    assert gateway.calls == ["open_orders", "positions"]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [("public_generation", "FEED_GENERATION_CHANGED"),
     ("user_generation", "USER_STREAM_GENERATION_CHANGED"),
     ("public_failure", "FEED_TEST_FAILURE"),
     ("expired", "MARKET_EXPIRED"),
     ("closed", "SESSION_CLOSED")],
)
async def test_status_await_rechecks_health_generation_expiry_and_closure(
    placement_scope, blocked_network, monkeypatch, change, reason,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = [time.time()]
    scope.session._now = lambda: clock[0]

    async def status_with_state_change(bootstrap, _origin, *, now):
        if change == "public_generation":
            scope.session.public_session.service.connection_generation += 1
        elif change == "user_generation":
            scope.session.user_session.observation.user_channel.connection_generation += 1
        elif change == "public_failure":
            scope.session.public_session.failure_category = lambda _generation: "FEED_TEST_FAILURE"
        elif change == "expired":
            clock[0] = bootstrap.end_epoch
        else:
            scope.session._closed = True
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, now(),
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", status_with_state_change)
    _prepare_real_admission(scope)
    result = await scope.session.initial_normal_admission()
    assert not result.eligible and result.reason == reason
    assert result.execution_authorized is False
    assert result.market_status is not None and result.market_status.age_s == 0
    if change == "expired":
        assert "CURRENT_MARKET_TRADABILITY" in result.outstanding_execution_gates
    else:
        assert "CURRENT_MARKET_TRADABILITY" not in result.outstanding_execution_gates
    assert scope.session.user_session.gateway.calls == []
    assert scope.session.placement_owner.current is None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_final_account_freshness_is_rechecked_after_status_read(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = [time.time()]
    scope.session._now = lambda: clock[0]
    scope.session._market_status_max_age_s = 30

    async def fresh_status(bootstrap, _origin, *, now):
        return RDPMarketStatusEvidence(
            bootstrap.market.slug, bootstrap.market.condition_id,
            tuple(token.token_id for token in bootstrap.market.tokens),
            tuple(token.outcome for token in bootstrap.market.tokens),
            bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, now(),
        )

    monkeypatch.setattr(runtime, "read_rdp_market_status", fresh_status)
    _service, gateway = _prepare_real_admission(scope)
    gateway.positions_hook = lambda: clock.__setitem__(0, clock[0] + 16)
    result = await scope.session.initial_normal_admission()
    assert not result.eligible
    assert result.reason == "STALE_ACCOUNT_EVIDENCE"
    assert result.market_status is not None and result.market_status.age_s == 16
    assert result.market_status.age_s < result.market_status.max_age_s
    assert result.backing is not None and not result.backing.eligible
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("teardown", [False, True])
async def test_market_status_read_is_owned_through_repeated_cancel_and_teardown(
    placement_scope, blocked_network, monkeypatch, teardown,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    entered = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()

    async def blocked_status(bootstrap, _origin, *, now):
        entered.set()
        try:
            await release.wait()
            return RDPMarketStatusEvidence(
                bootstrap.market.slug, bootstrap.market.condition_id,
                tuple(token.token_id for token in bootstrap.market.tokens),
                tuple(token.outcome for token in bootstrap.market.tokens),
                bootstrap.market.neg_risk, bootstrap.end_epoch, True, False, now(),
            )
        finally:
            cleaned.set()

    monkeypatch.setattr(runtime, "read_rdp_market_status", blocked_status)
    _prepare_real_admission(scope)
    admission_task = asyncio.create_task(scope.session.initial_normal_admission())
    await entered.wait()
    status_task = scope.session._market_status_read_task
    assert status_task is not None and not status_task.done()
    if teardown:
        close_task = asyncio.create_task(scope.session.aclose())
        await asyncio.sleep(0)
        assert not close_task.done()
    else:
        admission_task.cancel()
        await asyncio.sleep(0)
        admission_task.cancel()
        await asyncio.sleep(0)
        assert not admission_task.done()
    assert not cleaned.is_set()
    assert scope.session._admission_active
    release.set()
    if teardown:
        with pytest.raises(asyncio.CancelledError):
            await admission_task
        await close_task
    else:
        with pytest.raises(asyncio.CancelledError):
            await admission_task
    assert cleaned.is_set() and status_task.done()
    assert scope.session._market_status_read_task is None
    assert scope.session._admission_task is None
    assert not scope.session._admission_active
    assert not scope.session.user_session.gateway.calls
    assert blocked_network == []
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accepted_token", "accepted_id"),
    [("yes-token", "yes-only"), ("no-token", "no-only")],
)
async def test_partial_pair_registers_only_attributed_leg_and_blocks_replacement(
    placement_scope, blocked_network, accepted_token, accepted_id,
):
    scope = placement_scope
    attempt = _arm(scope)
    response = ([{"success": True, "orderID": accepted_id}, {"success": False}]
                if accepted_token == "yes-token"
                else [{"success": False}, {"success": True, "orderID": accepted_id}])
    outcome, _client = await _gateway_outcome(scope, response)
    assert isinstance(outcome, PlacementUnknown)
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.outcome == "PARTIAL" and result.unresolved
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert result.acknowledged_order_ids == (accepted_id,)
    assert scope.processor.owned_record(accepted_id) is not None
    assert len(scope.processor._owned) == 1
    assert scope.processor._pending_remaining == 1
    rejected = await scope.session.initial_normal_admission()
    assert not rejected.eligible and rejected.reason == "INITIAL_PLACEMENT_UNRESOLVED"
    with pytest.raises(RDPPlacementError, match="PLACEMENT_REPLACEMENT_BLOCKED"):
        _arm(scope)
    assert blocked_network == []


@pytest.mark.asyncio
async def test_accepted_plus_unknown_keeps_observed_id_unowned_and_blocks_empty_admission(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    response = [
        {"success": True, "orderID": "safe-yes"},
        {"success": True, "orderID": "uncertain-no", "id": "alias-no"},
    ]
    outcome, _client = await _gateway_outcome(scope, response)
    assert isinstance(outcome, PlacementUnknown)
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.outcome == "PARTIAL"
    assert result.reason == "UNRESOLVED_BATCH_RESULT"
    assert result.acknowledged_order_ids == ("safe-yes",)
    assert result.observed_order_ids == ("uncertain-no", "alias-no")
    assert scope.processor.owned_record("safe-yes") is not None
    assert scope.processor.owned_record("uncertain-no") is None
    assert scope.processor.owned_record("alias-no") is None
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "post_error", "expected_reason"),
    [
        ([{"success": False}, {"success": False}], None, None),
        ([{"success": False}], None, "BATCH_CARDINALITY_UNKNOWN"),
        ({"bad": "envelope"}, None, "BATCH_CARDINALITY_UNKNOWN"),
        (None, TimeoutError("private SDK detail"), "BATCH_POST_EXCEPTION"),
    ],
)
async def test_empty_and_unknown_gateway_outcomes_preserve_only_authoritative_evidence(
    placement_scope, blocked_network, response, post_error, expected_reason,
):
    scope = placement_scope
    attempt = _arm(scope)
    outcome, _client = await _gateway_outcome(scope, response, post_error=post_error)
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    if expected_reason is None:
        assert result.outcome == "EMPTY" and not result.unresolved
        assert not result.replacement_blocked and scope.processor._pending == set()
        assert result.acknowledged_order_ids == ()
    else:
        assert isinstance(outcome, PlacementUnknown)
        assert result.outcome == "UNKNOWN" and result.unresolved
        assert result.reason == expected_reason
        assert result.replacement_blocked and scope.owner.blocks_initial_admission
        assert not scope.processor._owned
        with pytest.raises(RDPPlacementError, match="PLACEMENT_REPLACEMENT_BLOCKED"):
            _arm(scope)
        if result.acknowledged_order_ids == ():
            rejected = await scope.session.initial_normal_admission()
            assert not rejected.eligible
            assert rejected.reason == "INITIAL_PLACEMENT_UNRESOLVED"
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("anomaly", ["malformed", "overflow", "unmatched"])
async def test_candidate_pending_anomalies_survive_processor_auto_clear(
    placement_scope, blocked_network, anomaly,
):
    scope = placement_scope
    attempt = _arm(scope)
    if anomaly == "malformed":
        scope.processor.on_malformed_trade({"yes-order"}, "bad maker event")
    elif anomaly == "overflow":
        scope.processor._pending_limit = 0
        scope.processor.on_trade(
            TradeEvent("yes-token", Side.BUY, .39, 1, "overflow-trade",
                       TradeState.MATCHED, 1, "yes-order", scope.market.condition_id),
            scope.market.condition_id,
        )
    else:
        scope.processor.on_trade(
            TradeEvent("yes-token", Side.BUY, .39, 1, "unmatched-trade",
                       TradeState.MATCHED, 1, "not-in-ack", scope.market.condition_id),
            scope.market.condition_id,
        )
    outcome, _client = await _gateway_outcome(scope, _accepted_rows())
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.unresolved and result.replacement_blocked
    assert result.pending_anomalies
    assert scope.owner.blocks_initial_admission
    if anomaly == "malformed":
        assert "PENDING_MALFORMED_MAKER_ID:yes-order" in result.pending_anomalies
        assert not scope.processor._pending_malformed_order_ids
        assert "yes-order" in scope.processor.quarantined_orders
    elif anomaly == "overflow":
        assert "PENDING_BUFFER_OVERFLOW" in result.pending_anomalies
        assert scope.processor.quarantined_orders == {"yes-order", "no-order"}
    else:
        assert "UNMATCHED_CANDIDATE_TRADE:unmatched-trade" in result.pending_anomalies
        assert ("unmatched-trade", "not-in-ack") not in scope.processor.applied_fill_keys
    assert blocked_network == []


@pytest.mark.asyncio
async def test_clean_empty_with_candidate_event_is_unknown_and_does_not_clear_capture(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    scope.processor.on_trade(
        TradeEvent("yes-token", Side.BUY, .39, 1, "candidate-before-empty",
                   TradeState.MATCHED, 1, "unreported-order", scope.market.condition_id),
        scope.market.condition_id,
    )
    outcome, _client = await _gateway_outcome(
        scope, [{"success": False}, {"success": False}],
    )
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.outcome == "UNKNOWN" and result.unresolved
    assert "UNMATCHED_CANDIDATE_TRADE:candidate-before-empty" in result.pending_anomalies
    assert scope.processor._pending
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_pending_overflow_and_malformed_evidence_are_not_lost_before_replay(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    scope.processor.on_malformed_trade({"yes-order"}, "malformed evidence")
    scope.processor._pending_limit = 0
    scope.processor.on_trade(
        TradeEvent("yes-token", Side.BUY, .39, 1, "over-limit",
                   TradeState.MATCHED, 1, "yes-order", scope.market.condition_id),
        scope.market.condition_id,
    )
    outcome, _client = await _gateway_outcome(scope, _accepted_rows())
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert "PENDING_BUFFER_OVERFLOW" in result.pending_anomalies
    assert "PENDING_MALFORMED_MAKER_ID:yes-order" in result.pending_anomalies
    assert result.unresolved and scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_later_confirmation_and_duplicate_replay_do_not_double_apply_or_restore_size(
    placement_scope, monkeypatch, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    matched = TradeEvent("yes-token", Side.BUY, .39, 2, "late-confirm",
                         TradeState.MATCHED, 10, "yes-order",
                         scope.market.condition_id)
    scope.processor.on_trade(matched, scope.market.condition_id)

    original_register = scope.processor.register_order

    def register_then_apply_live_order_change(order_id, *args, **kwargs):
        original_register(order_id, *args, **kwargs)
        if order_id == "yes-order":
            # Models authoritative local remaining state updated during replay.
            scope.store.upsert_order(OpenOrder(
                order_id, "yes-token", Side.BUY, .39, 3, OrderState.PARTIALLY_FILLED,
            ))

    monkeypatch.setattr(scope.processor, "register_order", register_then_apply_live_order_change)
    outcome, _client = await _gateway_outcome(scope, _accepted_rows())
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.outcome == "COMPLETE"
    assert scope.store.orders["yes-order"].size == 3
    snapshot = snapshot_inventory(scope.market, scope.processor, {"yes-order", "no-order"})
    assert snapshot.qy == 0 and snapshot.cy == 0  # MATCHED alone is not confirmed accounting.

    confirmed = replace(matched, status=TradeState.CONFIRMED, ts=11)
    scope.processor.on_trade(confirmed, scope.market.condition_id)
    scope.processor.on_trade(confirmed, scope.market.condition_id)
    snapshot = snapshot_inventory(scope.market, scope.processor, {"yes-order", "no-order"})
    assert snapshot.qy == 2 and snapshot.cy == .78
    assert scope.processor.applied_fill_keys == {("late-confirm", "yes-order")}
    assert scope.store.inflight("yes-token") == 0
    assert scope.store.orders["yes-order"].size == 3
    assert blocked_network == []


@pytest.mark.asyncio
async def test_duplicate_completion_and_existing_id_collision_do_not_overwrite_registry(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    outcome, _client = await _gateway_outcome(scope, _accepted_rows("collision", "no-safe"))
    scope.store.register_owned_order(
        "collision", "foreign-condition", "foreign-token", Side.SELL, 9, .7, "other",
    )
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.outcome == "PARTIAL" and result.unresolved
    assert result.acknowledged_order_ids == ("no-safe",)
    assert scope.processor.owned_record("collision")["condition_id"] == "foreign-condition"
    assert scope.processor.owned_record("no-safe") is not None
    with pytest.raises(RDPPlacementError, match="PLACEMENT_ATTEMPT_NOT_ACTIVE"):
        scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert blocked_network == []


@pytest.mark.asyncio
async def test_invalid_attribution_registers_only_the_other_safe_leg(placement_scope, blocked_network):
    scope = placement_scope
    attempt = _arm(scope)
    invalid = [
        OpenOrder("bad-yes", "yes-token", Side.SELL, .39, 5, OrderState.LIVE),
        OpenOrder("safe-no", "no-token", Side.BUY, .39, 5, OrderState.LIVE),
    ]
    result = scope.session.resolve_initial_normal_placement(attempt, invalid)
    assert result.outcome == "PARTIAL" and result.unresolved
    assert result.reason == "ACKNOWLEDGMENT_EVIDENCE_UNSAFE"
    assert result.acknowledged_order_ids == ("safe-no",)
    assert scope.processor.owned_record("bad-yes") is None
    assert scope.processor.owned_record("safe-no") is not None
    assert blocked_network == []


@pytest.mark.asyncio
async def test_gateway_duplicate_acknowledgment_id_is_never_registered(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    outcome, _client = await _gateway_outcome(
        scope, _accepted_rows("same-id", "same-id"),
    )
    assert isinstance(outcome, PlacementUnknown)
    result = scope.session.resolve_initial_normal_placement(attempt, outcome)
    assert result.outcome == "UNKNOWN" and result.unresolved
    assert result.acknowledged_order_ids == ()
    assert "same-id" in result.observed_order_ids
    assert scope.processor.owned_record("same-id") is None
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_three_duplicate_token_acknowledgments_are_permanently_excluded_but_safe_leg_kept(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt = _arm(scope)
    acknowledgments = [
        OpenOrder("yes-1", "yes-token", Side.BUY, .39, 5, OrderState.LIVE),
        OpenOrder("yes-2", "yes-token", Side.BUY, .39, 5, OrderState.LIVE),
        OpenOrder("yes-3", "yes-token", Side.BUY, .39, 5, OrderState.LIVE),
        OpenOrder("no-safe", "no-token", Side.BUY, .39, 5, OrderState.LIVE),
    ]
    result = scope.session.resolve_initial_normal_placement(attempt, acknowledgments)
    assert result.outcome == "PARTIAL" and result.unresolved
    assert result.replacement_blocked
    assert "DUPLICATE_ACKNOWLEDGMENT_TOKEN:yes-token" in result.pending_anomalies
    assert "TOO_MANY_ACKNOWLEDGMENTS" in result.pending_anomalies
    assert result.acknowledged_order_ids == ("no-safe",)
    assert scope.processor.owned_record("no-safe") is not None
    assert all(scope.processor.owned_record(order.order_id) is None
               for order in acknowledgments[:3])
    assert all(order.order_id not in scope.store.orders for order in acknowledgments[:3])
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("condition_id", "foreign-condition"),
        ("public_generation", 99),
        ("user_generation", 99),
    ],
    ids=["wrong-scope", "public-generation", "user-generation"],
)
async def test_wrong_scope_or_generation_rejected_before_capture_mutation(
    placement_scope, blocked_network, field, value,
):
    scope = placement_scope
    stale_admission = replace(scope.admission, **{field: value})
    pending_before = set(scope.processor._pending)
    events_before = tuple(scope.processor._pending_events)
    with pytest.raises(RDPPlacementError, match="ADMISSION_SCOPE_OR_GENERATION_MISMATCH"):
        scope.session.arm_initial_normal_placement(stale_admission)
    assert scope.owner.current is None
    assert scope.processor._pending == pending_before == set()
    assert tuple(scope.processor._pending_events) == events_before == ()
    assert not scope.processor._owned
    assert not scope.store.orders
    assert blocked_network == []


@pytest.mark.asyncio
async def test_generic_exception_is_sanitized_unknown_and_blocks_admission(
    placement_scope, blocked_network, caplog,
):
    scope = placement_scope
    attempt = _arm(scope)
    sentinel = "credential-sentinel-do-not-log"
    result = scope.session.resolve_initial_normal_placement(
        attempt, RuntimeError(f"private sdk detail {sentinel}"),
    )
    assert result.outcome == "UNKNOWN" and result.unresolved and result.replacement_blocked
    assert result.reason == "PLACEMENT_ERROR:RuntimeError"
    assert not result.acknowledged_order_ids and not result.observed_order_ids
    assert scope.processor._pending
    assert not scope.processor._owned and not scope.store.orders
    assert scope.owner.blocks_initial_admission
    rejected = await scope.session.initial_normal_admission()
    assert not rejected.eligible and rejected.reason == "INITIAL_PLACEMENT_UNRESOLVED"
    assert sentinel not in repr(result) and sentinel not in caplog.text
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_order",
    [
        OpenOrder("wrong-token", "foreign-token", Side.BUY, .39, 5, OrderState.LIVE),
        OpenOrder("wrong-price", "yes-token", Side.BUY, .40, 5, OrderState.LIVE),
        OpenOrder("wrong-size", "yes-token", Side.BUY, .39, 4, OrderState.LIVE),
    ],
    ids=["token", "price", "quantity"],
)
async def test_acknowledgment_economic_mismatch_never_establishes_ownership(
    placement_scope, blocked_network, invalid_order,
):
    scope = placement_scope
    attempt = _arm(scope)
    result = scope.session.resolve_initial_normal_placement(
        attempt,
        [invalid_order, OpenOrder(
            "safe-no", "no-token", Side.BUY, .39, 5, OrderState.LIVE,
        )],
    )
    assert result.outcome == "PARTIAL" and result.unresolved
    assert result.acknowledged_order_ids == ("safe-no",)
    assert scope.processor.owned_record(invalid_order.order_id) is None
    assert scope.processor.owned_record("safe-no") is not None
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_unresolved_zero_id_blocks_rearm_and_session_close_marks_armed_unknown(
    placement_scope, blocked_network,
):
    scope = placement_scope
    _arm(scope)
    with pytest.raises(RDPPlacementError, match="PLACEMENT_ATTEMPT_IN_PROGRESS"):
        _arm(scope)
    await scope.session.aclose()
    result = scope.owner.current
    assert result.outcome == "UNKNOWN" and result.unresolved
    assert result.reason == "PLACEMENT_INTERRUPTED"
    assert result.acknowledged_order_ids == ()
    assert result.replacement_blocked and scope.owner.blocks_initial_admission
    assert scope.processor._pending
    assert blocked_network == []


@pytest.mark.asyncio
async def test_coordinator_requires_started_window_and_valid_budget_without_gateway_calls(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    try:
        with pytest.raises(RDPPlacementError, match="FILL_WINDOW_NOT_STARTED"):
            await scope.session.complete_initial_fill_window(attempt, close_budget_s=1)
        for invalid in (0, -1, float("nan"), float("inf"), True):
            with pytest.raises(RDPPlacementError, match="INVALID_CLOSE_BUDGET"):
                await scope.session.complete_initial_fill_window(attempt, close_budget_s=invalid)
        assert client.open_count == 0 and client.cancel_calls == []
        assert scope.session.fill_window_completion_result is None
    finally:
        gateway.close()
    assert blocked_network == []


@pytest.mark.asyncio
async def test_coordinator_rejects_attempt_and_registry_mismatch_before_gateway_reads(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(90_000_000_000)
    scope.session._monotonic_ns = clock.read
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id="scope-check-fill",
    )
    scope.session.initial_fill_window()
    gateway, client = _close_gateway(scope, quotes)
    try:
        with pytest.raises(RDPPlacementError, match="FILL_WINDOW_ATTEMPT_SCOPE_INVALID"):
            await scope.session.complete_initial_fill_window(
                replace(attempt, slug="foreign-market"), close_budget_s=1,
            )
        scope.store.register_owned_order(
            "yes-order", scope.market.condition_id, "yes-token", Side.BUY,
            5, .40, "rdp_initial",
        )
        with pytest.raises(RDPPlacementError, match="FILL_WINDOW_REGISTRY_MISMATCH"):
            await scope.session.complete_initial_fill_window(attempt, close_budget_s=1)
        assert client.open_count == 0 and client.cancel_calls == []
        assert scope.session.fill_window_completion_result is None
    finally:
        gateway.close()
    assert blocked_network == []


@pytest.mark.asyncio
async def test_coordinator_waits_exact_original_deadline_even_after_full_natural_fills(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(100_000_000_000)
    scope.session._monotonic_ns = clock.read
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    no_quote = next(quote for quote in quotes if quote.token_id == "no-token")
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), size=5, price=yes_quote.price,
        trade_id="yes-full",
    )
    _send_matched_then_confirmed(
        scope, "no-order", receipt_ns=clock.read(), size=5, price=no_quote.price,
        trade_id="no-full",
    )
    started = scope.session.initial_fill_window()
    assert started.started and started.deadline_monotonic_ns == 103_000_000_000
    gateway, client = _close_gateway(
        scope, quotes,
        extra_rows=(
            {"id": "yes-order", "asset_id": "yes-token", "side": "BUY",
             "price": str(yes_quote.price), "original_size": "5", "size_matched": "5",
             "status": "MATCHED", "market": scope.market.condition_id},
            {"id": "no-order", "asset_id": "no-token", "side": "BUY",
             "price": str(no_quote.price), "original_size": "5", "size_matched": "5",
             "status": "MATCHED", "market": scope.market.condition_id},
        ),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "5",
                             trade_id="yes-full"),
                _rest_trade("no-order", "no-token", str(no_quote.price), "5",
                             trade_id="no-full")),
    )
    first_read_times = []
    client.open_hook = lambda _count: first_read_times.append(clock.read())
    try:
        result = await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
    finally:
        gateway.close()

    assert result.trigger == "WINDOW_EXPIRED"
    assert result.window_deadline_monotonic_ns == 103_000_000_000
    assert clock.read() == 103_000_000_000
    assert first_read_times and first_read_times[0] == 103_000_000_000
    assert client.cancel_calls == []
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None and result.close_result.capital.certified
    assert result.close_result.capital.snapshot.inventory.qy == 5
    assert result.close_result.capital.snapshot.inventory.qn == 5
    assert result.close_result.next_mode.value == "NORMAL"
    assert not result.close_result.replacement_blocked
    assert result.execution_authorized is False
    assert scope.session.fill_window_completion_result is result
    assert blocked_network == []


@pytest.mark.asyncio
async def test_configured_fill_window_closes_at_exact_coordinator_deadline(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(125_000_000_000)
    scope.session._fill_window_s = 1.25
    scope.session._monotonic_ns = clock.read
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
        trade_id="configured-window-fill",
    )
    decision = scope.session.initial_fill_window()
    assert decision.deadline_monotonic_ns == 126_250_000_000
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="configured-window-fill"),),
    )
    reads = []
    client.open_hook = lambda _count: reads.append(clock.read())
    try:
        result = await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
    finally:
        gateway.close()

    assert result.trigger == "WINDOW_EXPIRED"
    assert result.window_deadline_monotonic_ns == 126_250_000_000
    assert clock.read() == 126_250_000_000
    assert reads and reads[0] == 126_250_000_000
    assert result.close_result.barrier_complete
    assert result.close_result.next_mode.value == "NORMAL"
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["public", "user"])
async def test_same_generation_health_loss_blocks_continuation_with_consistent_close_evidence(
    placement_scope, blocked_network, source,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(350_000_000_000)
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
        trade_id=f"health-{source}-fill",
    )
    scope.session.initial_fill_window()
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id=f"health-{source}-fill"),),
    )
    completion = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        original_generation = (
            scope.session.public_session.service.connection_generation
            if source == "public"
            else scope.session.user_session.observation.user_channel.connection_generation
        )
        failure = f"{source.upper()}_HEALTH_LOST"
        if source == "public":
            scope.session.public_session.failure_category = lambda _generation: failure
        else:
            scope.session.user_session.failure_category = lambda _generation: failure
        scope.session._fill_window_wakeup.set()
        result = await completion
    finally:
        release_sleep.set()
        gateway.close()

    current_generation = (
        scope.session.public_session.service.connection_generation
        if source == "public"
        else scope.session.user_session.observation.user_channel.connection_generation
    )
    assert current_generation == original_generation
    assert result.trigger == f"WINDOW_UNRELIABLE:{failure}"
    assert result.close_result.barrier_complete
    assert result.close_result.barrier.inventory.qy == 1
    assert result.close_result.capital is not None
    assert not result.close_result.capital.certified
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked and scope.owner.blocks_initial_admission
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_id", "order_id", "expected_delta", "expected_cancel"),
    [
        ("yes-token", "yes-order", 5, ("no-order",)),
        ("no-token", "no-order", -5, ("yes-order",)),
    ],
    ids=["positive-five-rebalance", "negative-five-rebalance"],
)
async def test_coordinator_close_uses_confirmed_inventory_at_exact_rebalance_boundary(
    placement_scope, blocked_network, token_id, order_id, expected_delta, expected_cancel,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(150_000_000_000)
    scope.session._monotonic_ns = clock.read
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, order_id, receipt_ns=clock.read(), size=5,
        trade_id=f"boundary-{expected_delta}",
    )
    started = scope.session.initial_fill_window()
    assert started.started and started.deadline_monotonic_ns == 153_000_000_000
    clock.now_ns = started.deadline_monotonic_ns
    gateway, client = _close_gateway(
        scope, quotes, fill=(token_id, Decimal("5")),
        trades=(_rest_trade(order_id, token_id, ".39", "5",
                             trade_id=f"boundary-{expected_delta}"),),
    )
    try:
        result = await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
    finally:
        gateway.close()

    assert result.trigger == "WINDOW_EXPIRED"
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None and result.close_result.capital.certified
    assert result.close_result.capital.snapshot.inventory.delta == expected_delta
    assert result.close_result.next_mode.value == "REBALANCE"
    assert client.cancel_calls == [expected_cancel]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_coordinator_reconciles_confirmed_fill_arriving_during_cancel(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(250_000_000_000)
    scope.session._monotonic_ns = clock.read
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), size=2, trade_id="initial-window-fill",
    )
    window = scope.session.initial_fill_window()
    assert window.started
    clock.now_ns = window.deadline_monotonic_ns
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("2")),
        trades=(_rest_trade("yes-order", "yes-token", ".39", "2",
                             trade_id="initial-window-fill"),),
    )

    def add_fill_during_cancel():
        client.rows["yes-order"]["size_matched"] = "3"
        client.trades.append(_rest_trade(
            "yes-order", "yes-token", ".39", "1", trade_id="during-cancel-fill",
        ))

    client.cancel_hook = add_fill_during_cancel
    try:
        result = await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
    finally:
        gateway.close()

    assert result.trigger == "WINDOW_EXPIRED"
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None and result.close_result.capital.certified
    assert result.close_result.barrier.inventory.qy == 3
    assert result.close_result.barrier.inventory.qn == 0
    assert scope.processor.applied_fill_keys == {
        ("initial-window-fill", "yes-order"),
        ("during-cancel-fill", "yes-order"),
    }
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_later_fill_and_book_movement_do_not_retime_active_coordinator(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(175_000_000_000)
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id="first-fill",
    )
    initial = scope.session.initial_fill_window()
    gateway, client = _close_gateway(scope, quotes)
    reads = []
    client.open_hook = lambda _count: reads.append(clock.read())
    task = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        _send_matched_then_confirmed(
            scope, "no-order", receipt_ns=clock.read() + 1_000_000_000,
            trade_id="later-fill", size=1,
        )
        clock.now_ns += 1_000_000_000
        later = scope.session.initial_fill_window()
        assert later.started and later.deadline_monotonic_ns == initial.deadline_monotonic_ns
        service = scope.session.public_session.service
        yes_book = service.book("yes-token")
        assert yes_book is not None
        yes_book.apply_snapshot([(.55, 10)], [(.57, 10)], 2)
        yes_book.snapshot_generation = scope.session.public_generation
        service.book_filter.evaluate = lambda *_args, **_kwargs: SimpleNamespace(
            ready=True, rejected=True, reasons=("SYNTHETIC_BOOK_FILTER_REJECTION",),
        )
        assert scope.session.book_filter_result().rejected
        after_book = scope.session.initial_fill_window()
        assert after_book.deadline_monotonic_ns == initial.deadline_monotonic_ns
        assert client.open_count == 0 and client.cancel_calls == []
        clock.now_ns = initial.deadline_monotonic_ns
        release_sleep.set()
        result = await task
    finally:
        release_sleep.set()
        gateway.close()

    assert result.trigger == "WINDOW_EXPIRED"
    assert result.window_deadline_monotonic_ns == 178_000_000_000
    assert reads and reads[0] == 178_000_000_000
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_coordinator_and_explicit_close_cannot_overlap(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(200_000_000_000)
    scope.session._monotonic_ns = clock.read
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id="overlap-fill",
    )
    scope.session.initial_fill_window()
    gateway, client = _close_gateway(scope, quotes)
    try:
        owner_task = asyncio.create_task(
            scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
        )
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        with pytest.raises(RDPPlacementError, match="FILL_WINDOW_COORDINATOR_IN_PROGRESS"):
            await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
        with pytest.raises(RDPPlacementError, match="FILL_WINDOW_COORDINATOR_IN_PROGRESS"):
            await scope.session.close_initial_normal_placement(
                attempt, deadline=asyncio.get_running_loop().time() + 2,
            )
        clock.now_ns += 3_000_000_000
        release_sleep.set()
        result = await owner_task
    finally:
        gateway.close()
    assert result.trigger == "WINDOW_EXPIRED"
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_caller_cancellation_while_waiting_triggers_one_scoped_close(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(300_000_000_000)
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id="cancel-wait-fill",
    )
    scope.session.initial_fill_window()
    gateway, client = _close_gateway(scope, quotes)
    owner_task = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        owner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner_task
    finally:
        release_sleep.set()
        gateway.close()
    result = scope.session.fill_window_completion_result
    assert result is not None and result.trigger == "CALLER_CANCELLED"
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert result.close_result.replacement_blocked
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["public-generation", "user-generation", "market-end"])
async def test_coordinator_closes_once_on_waiting_input_failure_or_market_end(
    placement_scope, blocked_network, failure,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(350_000_000_000)
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id=f"failure-{failure}",
    )
    scope.session.initial_fill_window()
    gateway, client = _close_gateway(scope, quotes)
    task = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        assert client.open_count == 0 and client.cancel_calls == []
        if failure == "public-generation":
            scope.session.public_session.service.connection_generation += 1
        elif failure == "user-generation":
            scope.session.user_session.observation.user_channel.connection_generation += 1
        else:
            scope.session._now = lambda: float(scope.bootstrap.end_epoch)
        scope.session._fill_window_wakeup.set()
        result = await task
    finally:
        release_sleep.set()
        gateway.close()

    expected_trigger = "MARKET_END" if failure == "market-end" else (
        "WINDOW_UNRELIABLE:INPUT_GENERATION_CHANGED"
    )
    assert result.trigger == expected_trigger
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert result.close_result.replacement_blocked
    assert scope.session.fill_window_completion_result is result
    assert blocked_network == []


@pytest.mark.asyncio
async def test_session_teardown_wakes_and_drains_waiting_coordinator(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(375_000_000_000)
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id="teardown-wait-fill",
    )
    scope.session.initial_fill_window()
    gateway, client = _close_gateway(scope, quotes)
    completion = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        teardown = asyncio.create_task(scope.session.aclose())
        result = await completion
        await teardown
    finally:
        release_sleep.set()
        gateway.close()

    assert result.trigger == "SESSION_TEARDOWN"
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert scope.session._fill_window_task.done()
    assert scope.session.fill_window_completion_result is result
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timing-contradiction", "quarantine"])
async def test_unreliable_window_trigger_does_not_release_close_or_fabricate_next_mode(
    placement_scope, blocked_network, failure,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(600_000_000_000)
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id=f"unreliable-{failure}",
    )
    established = scope.session.initial_fill_window()
    assert established.started and established.reliable
    gateway, client = _close_gateway(scope, quotes)
    completion = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        if failure == "timing-contradiction":
            _send_matched_then_confirmed(
                scope, "yes-order", receipt_ns=clock.read() - 1_000_000_000,
                trade_id="earlier-after-start",
            )
            expected_reason = "WINDOW_UNRELIABLE:CONFIRMED_RECEIPT_PRECEDES_ESTABLISHED_WINDOW"
        else:
            scope.processor.quarantined_orders.add("yes-order")
            expected_reason = "WINDOW_UNRELIABLE:FILL_EVIDENCE_QUARANTINED"
        scope.session._fill_window_wakeup.set()
        result = await completion
    finally:
        release_sleep.set()
        gateway.close()

    assert result.trigger == expected_reason
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert result.close_result.replacement_blocked
    assert result.close_result.next_mode is None
    assert (result.close_result.capital is None
            or not result.close_result.capital.certified)
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_timing_contradiction_preserves_consistent_close_inventory_but_blocks_release(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(600_000_000_000)
    sleeper_entered = asyncio.Event()
    release_sleep = asyncio.Event()

    async def held_sleep(_delay):
        sleeper_entered.set()
        await release_sleep.wait()

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = held_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
        trade_id="initial-on-time-fill",
    )
    established = scope.session.initial_fill_window()
    assert established.started and established.deadline_monotonic_ns == 603_000_000_000
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("2")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="initial-on-time-fill"),
                _rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="contradictory-earlier-fill")),
    )
    completion = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=2),
    )
    try:
        await asyncio.wait_for(sleeper_entered.wait(), timeout=1)
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=clock.read() - 1_000_000_000,
            price=yes_quote.price, trade_id="contradictory-earlier-fill",
        )
        scope.session._fill_window_wakeup.set()
        result = await completion
    finally:
        release_sleep.set()
        gateway.close()

    assert result.trigger == (
        "WINDOW_UNRELIABLE:CONFIRMED_RECEIPT_PRECEDES_ESTABLISHED_WINDOW"
    )
    assert result.close_result.barrier_complete
    assert result.close_result.barrier.inventory.qy == 2
    assert result.close_result.capital is not None
    assert not result.close_result.capital.certified
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked and scope.owner.blocks_initial_admission
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_failed_owned_window_sleeper_triggers_one_sanitized_scoped_close(
    placement_scope, blocked_network, caplog,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(650_000_000_000)
    sleep_attempts = 0
    sentinel = "private-window-sleeper-sentinel"

    async def failed_sleep(_delay):
        nonlocal sleep_attempts
        sleep_attempts += 1
        raise RuntimeError(sentinel)

    scope.session._monotonic_ns = clock.read
    scope.session._sleep = failed_sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
        trade_id="sleep-failure-fill",
    )
    scope.session.initial_fill_window()
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="sleep-failure-fill"),),
    )
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        result = await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
    finally:
        loop.set_exception_handler(previous_handler)
        gateway.close()

    await asyncio.sleep(0)
    assert sleep_attempts == 1
    assert result.trigger == "WINDOW_WAIT_FAILED:RuntimeError"
    assert result.close_result.barrier_complete
    assert result.close_result.replacement_blocked
    assert result.close_result.next_mode is None
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert not unhandled
    assert sentinel not in caplog.text and sentinel not in repr(result)
    assert scope.session._fill_window_task.done()
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_method", ["open_orders", "cancel", "get_order", "get_trades"])
@pytest.mark.parametrize("mode", ["caller-cancel", "teardown", "deadline"])
async def test_coordinator_drains_each_real_gateway_operation_once(
    placement_scope, blocked_network, sdk_method, mode,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(400_000_000_000)
    scope.session._monotonic_ns = clock.read
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), trade_id=f"drain-{sdk_method}",
    )
    scope.session.initial_fill_window()
    clock.now_ns += 3_000_000_000
    gateway, client = _close_gateway_real_executor(scope, quotes)
    sdk_started = threading.Event()
    sdk_release = threading.Event()

    def block_sdk(*_args):
        sdk_started.set()
        if not sdk_release.wait(timeout=5):
            raise RuntimeError("fake SDK release timeout")

    if sdk_method == "open_orders":
        client.open_hook = block_sdk
    elif sdk_method == "cancel":
        client.cancel_hook = block_sdk
    elif sdk_method == "get_order":
        client.get_order_hook = block_sdk
    else:
        client.get_trades_hook = block_sdk

    close_budget = .05 if mode == "deadline" else 5
    operation = asyncio.create_task(
        scope.session.complete_initial_fill_window(attempt, close_budget_s=close_budget),
    )
    shutdown = None
    expected_calls = {
        "open_orders": (1, 0, 0, 0),
        "cancel": (1, 1, 0, 0),
        "get_order": (2, 1, 1, 0),
        "get_trades": (2, 1, 1, 1),
    }[sdk_method]

    def calls():
        return (client.open_count, len(client.cancel_calls),
                client.get_order_count, client.get_trades_count)

    try:
        assert await asyncio.wait_for(asyncio.to_thread(sdk_started.wait, 2), timeout=3)
        assert calls() == expected_calls
        if mode == "caller-cancel":
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
            assert scope.session._fill_window_task is not None
            sdk_release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            result = scope.session.fill_window_completion_result
            assert result is not None
        elif mode == "teardown":
            shutdown = asyncio.create_task(scope.session.aclose())
            await asyncio.sleep(0)
            assert not shutdown.done() and not operation.done()
            shutdown.cancel()
            await asyncio.sleep(0)
            shutdown.cancel()
            await asyncio.sleep(0)
            assert not shutdown.done() and not operation.done()
            sdk_release.set()
            with pytest.raises(asyncio.CancelledError):
                await shutdown
            result = await operation
            assert result.trigger == "WINDOW_EXPIRED"
        else:
            await asyncio.sleep(.07)
            assert not operation.done()
            sdk_release.set()
            result = await operation
            assert result.close_result.reason == "OPERATIONAL_DEADLINE"
    finally:
        sdk_release.set()
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown
        gateway.close()

    final_calls = expected_calls if mode == "deadline" else (3, 1, 4, 2)
    assert calls() == final_calls
    assert scope.session._fill_window_task.done()
    assert scope.session._placement_close_task is None
    assert scope.owner.blocks_initial_admission
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_rests_without_zero_fill_timeout_then_closes_and_caches(
    placement_scope, blocked_network, monkeypatch,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    yes_book = scope.session.public_session.service.book("yes-token")
    assert yes_book is not None
    sleeps = 0
    status_reads = 0
    import polymaker.rdp_runtime as runtime

    original_status = runtime.read_rdp_market_status

    async def count_status(*args, **kwargs):
        nonlocal status_reads
        status_reads += 1
        return await original_status(*args, **kwargs)

    async def later_price_move(_delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            yes_book.apply_snapshot([(.57, 10)], [(.60, 10)], 3)
        await asyncio.sleep(0)

    monkeypatch.setattr(runtime, "read_rdp_market_status", count_status)
    scope.session._sleep = later_price_move
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
        before = (status_reads, client.open_count, len(client.cancel_calls))
        cached = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    assert result.trigger == "QUOTE_PRICE_CHANGED"
    assert result.last_prefill_decision is not None
    assert result.last_prefill_decision.action == "CLOSE"
    assert status_reads == 2 and sleeps == 1
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None and result.close_result.capital.certified
    assert result.close_result.next_mode.value == "NORMAL"
    assert not result.close_result.replacement_blocked
    assert cached is result and (status_reads, client.open_count, len(client.cancel_calls)) == before
    assert len(client.cancel_calls) == 1
    assert scope.session.initial_cycle_completion_result is result
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalidation", ["price-gate", "filter-inactive-yes", "filter-inactive-no",
                     "filter-depth", "filter-fast-yes", "filter-fast-no", "quote-move"],
)
async def test_initial_cycle_ordinary_invalidation_uses_one_healthy_close(
    placement_scope, blocked_network, invalidation,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    if invalidation == "price-gate":
        yes = scope.session.public_session.service.book("yes-token")
        no = scope.session.public_session.service.book("no-token")
        assert yes is not None and no is not None
        yes.apply_snapshot([(.80, 10)], [(.82, 10)], 3)
        no.apply_snapshot([(.30, 10)], [(.32, 10)], 3)
    elif invalidation == "quote-move":
        yes = scope.session.public_session.service.book("yes-token")
        assert yes is not None
        yes.apply_snapshot([(.57, 10)], [(.60, 10)], 3)
    else:
        reason = {
            "filter-inactive-yes": "INACTIVE_YES",
            "filter-inactive-no": "INACTIVE_NO",
            "filter-depth": "DEPTH_EXTREME",
            "filter-fast-yes": "FAST_MOVE_YES",
            "filter-fast-no": "FAST_MOVE_NO",
        }[invalidation]
        base_filter = scope.session.book_filter_result()
        assert base_filter.ready and not base_filter.rejected
        yes_inactive = reason == "INACTIVE_YES"
        no_inactive = reason == "INACTIVE_NO"
        yes_fast = reason == "FAST_MOVE_YES"
        no_fast = reason == "FAST_MOVE_NO"
        sides = tuple(
            replace(
                side,
                inactive=(True if side.token_id == "yes-token" and yes_inactive
                          else True if side.token_id == "no-token" and no_inactive
                          else side.inactive),
                fast_move=(True if side.token_id == "yes-token" and yes_fast
                           else True if side.token_id == "no-token" and no_fast
                           else side.fast_move),
            )
            for side in base_filter.sides
        )
        book_filter = replace(
            base_filter,
            rejected=True,
            inactive=yes_inactive or no_inactive,
            depth_extreme=reason == "DEPTH_EXTREME",
            fast_move=yes_fast or no_fast,
            reasons=(reason,),
            sides=sides,
        )
        assert isinstance(book_filter, RDPBookFilterResult)
        scope.session.public_session.service.book_filter.evaluate = (
            lambda *_args, _result=book_filter, **_kwargs: _result
        )
    gateway, client = _close_gateway(scope, quotes)
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    assert result.last_prefill_decision is not None
    assert result.last_prefill_decision.action == "CLOSE"
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None and result.close_result.capital.certified
    assert not result.close_result.replacement_blocked
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("window_s", [3.0, 1.25])
async def test_initial_cycle_confirmed_before_activation_uses_original_window_and_delta_five(
    placement_scope, blocked_network, window_s,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(700_000_000_000)
    scope.session._monotonic_ns = clock.read
    scope.session._fill_window_s = window_s
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    receipt = clock.read()
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=receipt, size=5, price=yes_quote.price,
        trade_id="cycle-delta-five",
    )
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("5")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "5",
                             trade_id="cycle-delta-five"),),
    )
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    assert result.fill_window_completion is not None
    assert result.fill_window_completion.trigger == "WINDOW_EXPIRED"
    deadline = receipt + round(window_s * 1_000_000_000)
    assert result.fill_window_completion.window_deadline_monotonic_ns == deadline
    assert clock.read() == deadline
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None and result.close_result.capital.certified
    assert result.close_result.capital.snapshot.inventory.delta == 5
    assert result.close_result.next_mode.value == "REBALANCE"
    assert not result.close_result.replacement_blocked
    assert client.cancel_calls == [("no-order",)]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_confirmation_during_resting_keeps_receipt_deadline(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(710_000_000_000)
    scope.session._monotonic_ns = clock.read
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    receipt: list[int] = []
    sleeps = 0

    async def confirm_on_first_rest_wait(delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            receipt.append(clock.read())
            _send_matched_then_confirmed(
                scope, "yes-order", receipt_ns=receipt[0], price=yes_quote.price,
                trade_id="cycle-rest-fill",
            )
            client.rows["yes-order"]["size_matched"] = "1"
            scope.session._initial_cycle_wakeup.set()
        clock.now_ns += round(delay * 1_000_000_000)
        await asyncio.sleep(0)

    scope.session._sleep = confirm_on_first_rest_wait
    gateway, client = _close_gateway(
        scope, quotes,
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="cycle-rest-fill"),),
    )
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    assert sleeps >= 2
    assert result.fill_window_completion is not None
    assert result.fill_window_completion.window_deadline_monotonic_ns == receipt[0] + 3_000_000_000
    assert clock.read() == receipt[0] + 3_000_000_000
    assert result.close_result.barrier_complete
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_confirmation_during_delayed_gamma_read_hands_off(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = _DeterministicMonotonicClock(715_000_000_000)
    scope.session._monotonic_ns = clock.read
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="cycle-gamma-delay-fill"),),
    )
    original_status = runtime.read_rdp_market_status
    status_entered = asyncio.Event()
    release_status = asyncio.Event()

    async def delayed_status(*args, **kwargs):
        evidence = await original_status(*args, **kwargs)
        status_entered.set()
        await release_status.wait()
        return evidence

    monkeypatch.setattr(runtime, "read_rdp_market_status", delayed_status)
    operation = asyncio.create_task(scope.session.complete_initial_normal_cycle(
        attempt, close_budget_s=2,
    ))
    try:
        await asyncio.wait_for(status_entered.wait(), timeout=1)
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
            trade_id="cycle-gamma-delay-fill",
        )
        client.rows["yes-order"]["size_matched"] = "1"
        release_status.set()
        result = await operation
    finally:
        release_status.set()
        gateway.close()

    assert result.last_prefill_decision is not None
    assert result.last_prefill_decision.action == "FILL_WINDOW"
    assert result.fill_window_completion is not None
    assert result.fill_window_completion.window_deadline_monotonic_ns == 718_000_000_000
    assert result.close_result.barrier_complete
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_rest_result_racing_confirmation_uses_original_window(
    placement_scope, blocked_network, monkeypatch,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(716_000_000_000)
    scope.session._monotonic_ns = clock.read
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    gateway, client = _close_gateway(
        scope, quotes,
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="rest-return-fill-race"),),
    )
    original_evaluate = scope.session.evaluate_initial_prefill
    receipt: list[int] = []
    decisions = []

    async def confirm_after_rest_capture(current_attempt):
        decision = await original_evaluate(current_attempt)
        decisions.append(decision)
        assert decision.action == "REST"
        receipt.append(clock.read())
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=receipt[0], price=yes_quote.price,
            trade_id="rest-return-fill-race",
        )
        client.rows["yes-order"]["size_matched"] = "1"
        return decision

    monkeypatch.setattr(scope.session, "evaluate_initial_prefill", confirm_after_rest_capture)
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    expected_deadline = receipt[0] + 3_000_000_000
    assert decisions and decisions[0].action == "REST"
    assert result.fill_window_completion is not None
    assert result.fill_window_completion.window_deadline_monotonic_ns == expected_deadline
    assert clock.read() == expected_deadline
    assert result.close_result.barrier_complete
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unsupported", ["min-order-size", "illegal-price"])
async def test_initial_cycle_unsupported_candidate_rejection_closes_unsafe_with_fill(
    placement_scope, blocked_network, monkeypatch, unsupported,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    if unsupported == "min-order-size":
        object.__setattr__(
            scope.bootstrap,
            "token_metadata",
            tuple(replace(item, min_order_size=6) for item in scope.bootstrap.token_metadata),
        )
        expected_reason = "MIN_ORDER_SIZE"
    else:
        for token in ("yes-token", "no-token"):
            book = scope.session.public_session.service.book(token)
            assert book is not None
            # Change only the current book levels. The filter retains its
            # ready historical evidence, so the real quote builder reaches
            # its unsupported ILLEGAL_QUOTE_PRICE rejection.
            book.bids.clear()
            book.bids[.01] = 10
            book.asks.clear()
            book.asks[.02] = 10
        expected_reason = "ILLEGAL_QUOTE_PRICE"

    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    gateway, client = _close_gateway(
        scope, quotes,
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id=f"unsupported-{unsupported}-fill"),),
    )
    original_evaluate = scope.session.evaluate_initial_prefill
    decisions = []

    async def add_fill_after_candidate(current_attempt):
        decision = await original_evaluate(current_attempt)
        decisions.append(decision)
        assert decision.action == "CLOSE" and decision.reason == expected_reason
        _send_matched_then_confirmed(
            scope, "yes-order", receipt_ns=scope.session._monotonic_ns(),
            price=yes_quote.price, trade_id=f"unsupported-{unsupported}-fill",
        )
        client.rows["yes-order"]["size_matched"] = "1"
        return decision

    monkeypatch.setattr(scope.session, "evaluate_initial_prefill", add_fill_after_candidate)
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    close = result.close_result
    assert decisions and decisions[0].current_candidate is not None
    assert decisions[0].current_candidate.reason == expected_reason
    assert result.trigger == expected_reason
    assert close.barrier_complete
    assert close.barrier is not None and close.barrier.inventory.qy == 1
    assert close.capital is None or not close.capital.certified
    assert close.next_mode is None
    assert close.replacement_blocked and scope.owner.blocks_initial_admission
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_started_window_ignores_ordinary_book_movement(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = _DeterministicMonotonicClock(730_000_000_000)
    scope.session._monotonic_ns = clock.read
    scope.session._sleep = clock.sleep
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
        trade_id="cycle-book-after-fill",
    )
    yes = scope.session.public_session.service.book("yes-token")
    assert yes is not None
    yes.apply_snapshot([(.57, 10)], [(.60, 10)], 3)
    status_reads = 0
    original_status = runtime.read_rdp_market_status

    async def count_status(*args, **kwargs):
        nonlocal status_reads
        status_reads += 1
        return await original_status(*args, **kwargs)

    monkeypatch.setattr(runtime, "read_rdp_market_status", count_status)
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="cycle-book-after-fill"),),
    )
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    assert result.last_prefill_decision is not None
    assert result.last_prefill_decision.action == "FILL_WINDOW"
    assert status_reads == 0
    assert result.fill_window_completion is not None
    assert result.close_result.barrier_complete
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_confirmation_with_prefill_read_failure_closes_unsafe(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    clock = _DeterministicMonotonicClock(720_000_000_000)
    scope.session._monotonic_ns = clock.read
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="cycle-read-failure-fill"),),
    )
    original_open_orders = runtime._StrictCloseGateway.open_orders
    called = False

    async def fail_initial_read_once(adapter):
        nonlocal called
        if not called:
            called = True
            _send_matched_then_confirmed(
                scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
                trade_id="cycle-read-failure-fill",
            )
            raise RuntimeError("private read failure")
        return await original_open_orders(adapter)

    monkeypatch.setattr(runtime._StrictCloseGateway, "open_orders", fail_initial_read_once)
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    assert result.trigger == "ORDER_STATE_READ_FAILED:RuntimeError"
    assert result.fill_window_completion is None
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None
    assert not result.close_result.capital.certified
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked and scope.owner.blocks_initial_admission
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_rejects_competing_owners_and_delegates_while_resting(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    entered = asyncio.Event()
    release = asyncio.Event()
    yes = scope.session.public_session.service.book("yes-token")
    assert yes is not None

    async def held_wait(_delay):
        entered.set()
        await release.wait()

    scope.session._sleep = held_wait
    owner = asyncio.create_task(scope.session.complete_initial_normal_cycle(
        attempt, close_budget_s=2,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        with pytest.raises(RDPPlacementError, match="INITIAL_CYCLE_IN_PROGRESS"):
            await scope.session.complete_initial_normal_cycle(attempt, close_budget_s=2)
        competing_prefill = await scope.session.evaluate_initial_prefill(attempt)
        assert competing_prefill.action == "CLOSE"
        assert competing_prefill.reason == "INITIAL_CYCLE_COORDINATION_IN_PROGRESS"
        with pytest.raises(RDPPlacementError, match="INITIAL_CYCLE_COORDINATION_IN_PROGRESS"):
            await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
        with pytest.raises(RDPPlacementError, match="INITIAL_CYCLE_COORDINATION_IN_PROGRESS"):
            await scope.session.close_initial_normal_placement(
                attempt, deadline=asyncio.get_running_loop().time() + 2,
            )
        yes.apply_snapshot([(.57, 10)], [(.60, 10)], 3)
        scope.session._initial_cycle_wakeup.set()
        release.set()
        result = await owner
    finally:
        release.set()
        if not owner.done():
            owner.cancel()
            with suppress(asyncio.CancelledError):
                await owner
        gateway.close()

    assert result.close_result.barrier_complete
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_repeated_caller_cancellation_while_resting_drains_then_closes(
    placement_scope, blocked_network,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    entered = asyncio.Event()

    async def held_wait(_delay):
        entered.set()
        await asyncio.Event().wait()

    scope.session._sleep = held_wait
    operation = asyncio.create_task(scope.session.complete_initial_normal_cycle(
        attempt, close_budget_s=2,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        with pytest.raises(asyncio.CancelledError):
            await operation
        result = scope.session.initial_cycle_completion_result
    finally:
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        gateway.close()

    assert result is not None and result.trigger == "CALLER_CANCELLED"
    assert result.close_result.barrier_complete
    assert (result.close_result.capital is None
            or not result.close_result.capital.certified)
    assert result.close_result.replacement_blocked
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_teardown_during_fill_window_wait_is_unsafe_and_cached(
    placement_scope, blocked_network,
):
    scope = placement_scope
    clock = _DeterministicMonotonicClock(740_000_000_000)
    scope.session._monotonic_ns = clock.read
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes_quote = next(quote for quote in quotes if quote.token_id == "yes-token")
    _send_matched_then_confirmed(
        scope, "yes-order", receipt_ns=clock.read(), price=yes_quote.price,
        trade_id="cycle-window-teardown",
    )
    gateway, client = _close_gateway(
        scope, quotes, fill=("yes-token", Decimal("1")),
        trades=(_rest_trade("yes-order", "yes-token", str(yes_quote.price), "1",
                             trade_id="cycle-window-teardown"),),
    )
    entered = asyncio.Event()

    async def held_window_wait(_delay):
        entered.set()
        await asyncio.Event().wait()

    scope.session._sleep = held_window_wait
    operation = asyncio.create_task(scope.session.complete_initial_normal_cycle(
        attempt, close_budget_s=2,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        competing = await scope.session.evaluate_initial_prefill(attempt)
        assert competing.action == "CLOSE"
        with pytest.raises(RDPPlacementError, match="INITIAL_CYCLE_COORDINATION_IN_PROGRESS"):
            await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
        with pytest.raises(RDPPlacementError, match="INITIAL_CYCLE_COORDINATION_IN_PROGRESS"):
            await scope.session.close_initial_normal_placement(
                attempt, deadline=asyncio.get_running_loop().time() + 2,
            )
        await scope.session.aclose()
        result = await operation
    finally:
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        gateway.close()

    assert result.trigger == "SESSION_TEARDOWN"
    assert result.fill_window_completion is not None
    assert result.fill_window_completion.trigger == "SESSION_TEARDOWN"
    assert result.close_result.barrier_complete
    assert (result.close_result.capital is None
            or not result.close_result.capital.certified)
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["public-health", "user-health", "public-generation",
                                     "user-generation", "market-end"])
async def test_initial_cycle_rechecks_health_after_rest_wait_and_closes_unsafe(
    placement_scope, blocked_network, failure,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held_wait(_delay):
        entered.set()
        await release.wait()

    scope.session._sleep = held_wait
    operation = asyncio.create_task(scope.session.complete_initial_normal_cycle(
        attempt, close_budget_s=2,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        expected = {
            "public-health": "PUBLIC_WS_LOST",
            "user-health": "USER_WS_LOST",
            "public-generation": "INPUT_GENERATION_CHANGED",
            "user-generation": "INPUT_GENERATION_CHANGED",
            "market-end": "MARKET_EXPIRED",
        }[failure]
        if failure == "public-health":
            scope.session.public_session.failure_category = lambda _generation: "PUBLIC_WS_LOST"
        elif failure == "user-health":
            scope.session.user_session.failure_category = lambda _generation: "USER_WS_LOST"
        elif failure == "public-generation":
            scope.session.public_session.service.connection_generation += 1
        elif failure == "user-generation":
            scope.session.user_session.observation.user_channel.connection_generation += 1
        else:
            scope.session._now = lambda: scope.bootstrap.end_epoch
        release.set()
        scope.session._initial_cycle_wakeup.set()
        result = await operation
    finally:
        release.set()
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        gateway.close()

    assert result.trigger == expected
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None
    assert not result.close_result.capital.certified
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_uses_actual_gamma_http_and_authenticated_gateway_reads(
    placement_scope, blocked_network, monkeypatch,
):
    import polymaker.rdp_live as live
    import polymaker.rdp_runtime as runtime

    scope = placement_scope
    market = _retarget_prefill_market_to_current_epoch(scope)
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    monkeypatch.setattr(runtime, "read_rdp_market_status", live.read_rdp_market_status)
    yes = scope.session.public_session.service.book("yes-token")
    assert yes is not None
    yes.apply_snapshot([(.57, 10)], [(.60, 10)], 3)
    raw = {
        "acceptingOrders": True, "closed": False, "negRisk": market.neg_risk,
        "conditionId": market.condition_id, "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01, "orderMinSize": 5,
    }
    gateway, client = _close_gateway(scope, quotes)
    try:
        with respx.mock(assert_all_called=True) as router:
            gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
                return_value=httpx.Response(200, json=[raw]),
            )
            result = await scope.session.complete_initial_normal_cycle(
                attempt, close_budget_s=2,
            )
    finally:
        gateway.close()

    assert gamma.call_count == 1
    assert dict(gamma.calls[0].request.url.params) == {"slug": market.slug, "limit": "1"}
    assert client.open_count >= 2
    assert result.trigger == "QUOTE_PRICE_CHANGED"
    assert result.close_result.barrier_complete
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["caller-cancel", "teardown"])
async def test_initial_cycle_interruption_drains_actual_gamma_http_before_close(
    placement_scope, blocked_network, monkeypatch, interruption,
):
    import polymaker.catalog.gamma as gamma_module
    import polymaker.rdp_live as live_module
    import polymaker.rdp_runtime as runtime
    from polymaker.catalog.gamma import GammaClient as RealGammaClient

    scope = placement_scope
    market = _retarget_prefill_market_to_current_epoch(scope)
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    monkeypatch.setattr(runtime, "read_rdp_market_status", live_module.read_rdp_market_status)
    clients = []

    class TrackingGammaClient(RealGammaClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.closed_by_context = False
            clients.append(self)

        async def aclose(self):
            await super().aclose()
            self.closed_by_context = True

    monkeypatch.setattr(gamma_module, "GammaClient", TrackingGammaClient)
    monkeypatch.setattr(live_module, "GammaClient", TrackingGammaClient)
    raw = {
        "acceptingOrders": True, "closed": False, "negRisk": market.neg_risk,
        "conditionId": market.condition_id, "question": market.question,
        "slug": market.slug,
        "clobTokenIds": json.dumps([token.token_id for token in market.tokens]),
        "outcomes": json.dumps([token.outcome for token in market.tokens]),
        "endDate": market.end_date_iso,
        "orderPriceMinTickSize": .01, "orderMinSize": 5,
    }
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold_response(_request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json=[raw])

    gateway, client = _close_gateway_real_executor(scope, quotes)
    operation = None
    shutdown = None
    try:
        with respx.mock(assert_all_called=True) as router:
            gamma = router.get("https://gamma-api.polymarket.com/markets").mock(
                side_effect=hold_response,
            )
            operation = asyncio.create_task(scope.session.complete_initial_normal_cycle(
                attempt, close_budget_s=5,
            ))
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert clients and not clients[0].closed_by_context
            if interruption == "caller-cancel":
                operation.cancel()
                await asyncio.sleep(0)
                operation.cancel()
                await asyncio.sleep(0)
                assert not operation.done()
            else:
                shutdown = asyncio.create_task(scope.session.aclose())
                await asyncio.sleep(0)
                assert not shutdown.done() and not operation.done()
            release.set()
            if interruption == "caller-cancel":
                with pytest.raises(asyncio.CancelledError):
                    await operation
            else:
                await shutdown
                result = await operation
            assert gamma.call_count == 1
            assert clients[0].closed_by_context
    finally:
        release.set()
        if operation is not None and not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown
        gateway.close()

    if interruption == "caller-cancel":
        result = scope.session.initial_cycle_completion_result
    assert result is not None
    assert result.trigger == ("CALLER_CANCELLED" if interruption == "caller-cancel"
                              else "SESSION_TEARDOWN")
    assert result.close_result.barrier_complete
    assert (result.close_result.capital is None
            or not result.close_result.capital.certified)
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked
    assert client.open_count >= 2 and len(client.cancel_calls) == 1
    assert not gateway._account_io
    assert blocked_network == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_method", ["open_orders", "cancel", "get_order", "get_trades"])
@pytest.mark.parametrize("interruption", ["caller-cancel", "teardown"])
async def test_initial_cycle_interrupt_during_each_real_sdk_close_operation_blocks_certification(
    placement_scope, blocked_network, sdk_method, interruption,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    yes = scope.session.public_session.service.book("yes-token")
    assert yes is not None
    yes.apply_snapshot([(.57, 10)], [(.60, 10)], 3)
    gateway, client = _close_gateway_real_executor(scope, quotes)
    sdk_started = threading.Event()
    sdk_release = threading.Event()

    def block_sdk(*_args):
        sdk_started.set()
        if not sdk_release.wait(timeout=5):
            raise RuntimeError("fake SDK release timeout")

    if sdk_method == "open_orders":
        client.open_hook = lambda count: block_sdk() if count == 2 else None
    elif sdk_method == "cancel":
        client.cancel_hook = block_sdk
    elif sdk_method == "get_order":
        client.get_order_hook = block_sdk
    else:
        client.get_trades_hook = block_sdk

    operation = asyncio.create_task(scope.session.complete_initial_normal_cycle(
        attempt, close_budget_s=5,
    ))
    shutdown = None
    try:
        assert await asyncio.wait_for(asyncio.to_thread(sdk_started.wait, 2), timeout=3)
        competing = await scope.session.evaluate_initial_prefill(attempt)
        assert competing.action == "CLOSE"
        assert competing.reason == "INITIAL_CYCLE_COORDINATION_IN_PROGRESS"
        with pytest.raises(RDPPlacementError, match="INITIAL_CYCLE_COORDINATION_IN_PROGRESS"):
            await scope.session.complete_initial_fill_window(attempt, close_budget_s=2)
        with pytest.raises(RDPPlacementError, match="INITIAL_CYCLE_COORDINATION_IN_PROGRESS"):
            await scope.session.close_initial_normal_placement(
                attempt, deadline=asyncio.get_running_loop().time() + 2,
            )
        if interruption == "caller-cancel":
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
            await asyncio.sleep(0)
            assert not operation.done()
            sdk_release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            shutdown = asyncio.create_task(scope.session.aclose())
            await asyncio.sleep(0)
            assert not shutdown.done() and not operation.done()
            sdk_release.set()
            await shutdown
            result = await operation
            assert result.trigger == "SESSION_TEARDOWN"
    finally:
        sdk_release.set()
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        if shutdown is not None and not shutdown.done():
            shutdown.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown
        gateway.close()

    result = (scope.session.initial_cycle_completion_result if interruption == "caller-cancel"
              else result)
    assert result is not None
    assert result.close_result.barrier_complete
    assert (result.close_result.capital is None
            or not result.close_result.capital.certified)
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked
    assert scope.owner.blocks_initial_admission
    assert result.execution_authorized is False
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_failed_sleeper_closes_once_with_sanitized_cached_result(
    placement_scope, blocked_network, caplog,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)
    sentinel = "cycle-private-sleeper-sentinel"

    async def failed_sleep(_delay):
        raise RuntimeError(sentinel)

    scope.session._sleep = failed_sleep
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    unhandled = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
        cached = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)
        gateway.close()

    assert result.trigger == "CYCLE_WAIT_FAILED:RuntimeError"
    assert result.close_result.barrier_complete
    assert result.close_result.replacement_blocked and result.close_result.next_mode is None
    assert cached is result and len(client.cancel_calls) == 1
    assert not unhandled
    assert sentinel not in caplog.text and sentinel not in repr(result)
    assert blocked_network == []


@pytest.mark.asyncio
async def test_initial_cycle_does_not_trust_healthy_reason_string_without_evidence(
    placement_scope, blocked_network, monkeypatch,
):
    scope = placement_scope
    attempt, quotes = await _compose_accepted_initial_pair(scope)
    gateway, client = _close_gateway(scope, quotes)

    async def forged_reason(_attempt):
        return scope.session._prefill_decision("CLOSE", "PRICE_GATE", attempt)

    monkeypatch.setattr(scope.session, "_run_initial_prefill_evaluation", forged_reason)
    try:
        result = await scope.session.complete_initial_normal_cycle(
            attempt, close_budget_s=2,
        )
    finally:
        gateway.close()

    assert result.trigger == "PRICE_GATE"
    assert result.last_prefill_decision is not None
    assert result.last_prefill_decision.market_status is None
    assert result.close_result.barrier_complete
    assert result.close_result.capital is not None
    assert not result.close_result.capital.certified
    assert result.close_result.next_mode is None
    assert result.close_result.replacement_blocked and scope.owner.blocks_initial_admission
    assert client.cancel_calls == [("no-order", "yes-order")]
    assert blocked_network == []
