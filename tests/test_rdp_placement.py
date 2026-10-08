from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

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
from polymaker.execution.gateway import ExecutionGateway, PlacementUnknown
from polymaker.execution.rdp_placement import RDPPlacementError
from polymaker.marketdata.parse import TradePrint
from polymaker.rdp_live import RDPBootstrap, TokenBookMetadata
from polymaker.rdp_marketdata import RDPMarketDataService
from polymaker.rdp_runtime import RDPInitialNormalAdmission, RDPInputSession
from polymaker.risk.rdp_capital import RDPInitialBackingResult
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor
from polymaker.strategy.rdp_accounting import snapshot_inventory
from polymaker.strategy.rdp_quotes import RDPQuoteDecision


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
def placement_scope(tmp_path):
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
    processor = UserEventProcessor(store)
    def make_session():
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
            now=time.time, sleep=asyncio.sleep, poll_interval_s=.1,
            started_at=time.time(),
        )

    session = make_session()
    owner = session.placement_owner
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
        self.address = "fake-signer"
        self.funder = "fake-funder"
        self.creds = SimpleNamespace(
            api_key="fake-key", api_secret="fake-secret", api_passphrase="fake-passphrase",
        )
        self.spender = "0x" + "a" * 40

    async def open_orders(self, *, strict_account, exact_decimal):
        assert strict_account is True and exact_decimal is True
        return []

    async def positions(self, *, strict_account):
        assert strict_account is True
        return {"yes-token": ("0", "0"), "no-token": ("0", "0")}

    async def required_collateral_spenders(self, _market):
        return (self.spender,)

    async def balance_allowance(self):
        return {"balance": "30000000", "allowances": {self.spender: "30000000"}}


class FakeBatchClient:
    def __init__(self, response=None, *, post_error=None):
        self.response = response
        self.post_error = post_error
        self.created = []
        self.posts = []

    def create_order(self, args, **kwargs):
        self.created.append((args, kwargs))
        return {"fake_signature": len(self.created)}

    def post_orders(self, args, *, post_only):
        self.posts.append((args, post_only))
        if self.post_error is not None:
            raise self.post_error
        return self.response


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
