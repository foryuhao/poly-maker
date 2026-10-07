"""Offline M3 safety and one-order lifecycle tests; all venue calls are fake."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from polymaker.btc5m import slug_for_epoch
from polymaker.cli import app
from polymaker.config import Config
from polymaker.domain import OpenOrder, OrderState, Side, TokenMeta
from polymaker.execution.gateway import (
    ExecutionGateway,
    GatewayReadError,
    OrderNotFound,
    PlacementUnknown,
    normalize_signing_price,
)
from polymaker.live_authorization import LiveExecutionIdentity
from polymaker.mi_cash_reservation import MICashReservation, ReservationState
from polymaker.mi_micro_live import (
    STRATEGY,
    MicroLiveError,
    MicroMode,
    candidate_quote,
    order_evidence,
    prove_terminal_from_account_truth,
    reconcile,
    selected_epoch,
    settle_order,
)
from polymaker.strategy.maker_inventory import (
    Action,
    MakerInventoryBook,
    MakerInventorySnapshot,
    evaluate,
)

EPOCH = 1_800_000_000
IDENTITY = LiveExecutionIdentity("0xsigner", "0xfunder", 2)


def snapshot(yes=("0.64", "0.66"), no=("0.34", "0.36"), *, elapsed=1):
    return MakerInventorySnapshot(
        MakerInventoryBook(*yes), MakerInventoryBook(*no), Decimal(0), Decimal(0),
        Decimal(25), Decimal(elapsed), True, Decimal("0.01"), Decimal(5), Decimal("0.01"),
    )


def market(meta):
    return replace(meta, condition_id="0xcond", slug=slug_for_epoch(EPOCH),
                   tokens=(TokenMeta("yes-token", "Up"), TokenMeta("no-token", "Down")))


def reservation(identity=IDENTITY, condition="0xcond", state=ReservationState.ACTIVE):
    return MICashReservation("reserve-1", identity, condition, 25_000_000, state)


def quote(meta, s=None, r=None, *, working=0, filled=0, now=EPOCH + 1):
    s = s or snapshot()
    return candidate_quote(evaluate(s, config=STRATEGY), s, market(meta), r or reservation(),
                           IDENTITY, now=now, epoch=EPOCH, working_orders=working,
                           filled_clips=filled)


def test_startup_skip_and_fresh_market():
    assert selected_epoch(EPOCH + 180) == EPOCH + 300  # 120 seconds remain
    assert selected_epoch(EPOCH + 1) == EPOCH  # 299 seconds remain
    assert selected_epoch(EPOCH + 120) == EPOCH  # inclusive 180-second threshold


def test_target_clamp_and_exact_clip(meta):
    positive = evaluate(snapshot(("0.89", "0.91"), ("0.09", "0.11")), config=STRATEGY)
    negative = evaluate(snapshot(("0.09", "0.11"), ("0.89", "0.91")), config=STRATEGY)
    assert positive.n_target == 5 and positive.qty == 5
    assert negative.n_target == -5 and negative.qty == 5
    assert quote(meta).size == 5


@pytest.mark.parametrize("bad", [
    reservation(state=ReservationState.RELEASED),
    reservation(identity=LiveExecutionIdentity("B", "B", 2)),
    reservation(condition="other-market"),
])
def test_missing_or_wrong_reservation_denies(meta, bad):
    with pytest.raises(MicroLiveError, match="RESERVATION"):
        quote(meta, r=bad)


def test_one_order_one_fill_and_cutoff(meta):
    for kwargs in ({"working": 1}, {"filled": 1}):
        with pytest.raises(MicroLiveError, match="ONE_ORDER_ONE_FILL"):
            quote(meta, **kwargs)
    with pytest.raises(MicroLiveError, match="CUTOFF"):
        quote(meta, now=EPOCH + 285)


def test_no_candidate_and_invalid_maker_price(meta):
    s = snapshot(("0.49", "0.51"), ("0.49", "0.51"))
    assert evaluate(s, config=STRATEGY).action is Action.WAIT
    with pytest.raises(MicroLiveError, match="NO_VALID"):
        quote(meta, s=s)
    invalid = snapshot(("0.64", "0.63"))
    with pytest.raises(MicroLiveError, match="NO_VALID"):
        quote(meta, s=invalid)


@pytest.mark.parametrize("bid,ask,expected", [
    ("0.645", "0.660", 0.64),  # SDK rounding stays strictly below the ask.
    ("0.655", "0.660", None),  # Review reproduction: SDK rounds to the ask.
    ("0.655", "0.659", None),  # SDK rounding crosses the ask.
])
def test_signing_price_is_the_maker_price(meta, bid, ask, expected):
    s = replace(snapshot((bid, ask), ("0.345", "0.355")), tick_size=Decimal("0.001"))
    if expected is None:
        with pytest.raises(MicroLiveError, match="MAKER_OR_TOKEN_INVALID"):
            quote(meta, s=s)
    else:
        q = quote(meta, s=s)
        assert q.price == expected
        assert normalize_signing_price(q.price, meta.tick_size) == q.price
        assert q.price < float(ask)


def test_book_tick_can_be_coarser_than_signing_tick(meta):
    finer_meta = replace(market(meta), tick_size=0.001)
    s = snapshot()
    decision = evaluate(s, config=STRATEGY)
    q = candidate_quote(decision, s, finer_meta, reservation(), IDENTITY,
                        now=EPOCH + 1, epoch=EPOCH)
    assert q.price == 0.64
    assert normalize_signing_price(q.price, finer_meta.tick_size) == q.price


@pytest.mark.parametrize("bad_tick", [None, float("nan"), 0.0, 0.003])
def test_missing_or_unsupported_signing_tick_denies(meta, bad_tick):
    s = snapshot()
    decision = evaluate(s, config=STRATEGY)
    with pytest.raises(MicroLiveError, match="SIGNING_PRICE_OR_TICK_INVALID"):
        candidate_quote(decision, s, replace(market(meta), tick_size=bad_tick),
                        reservation(), IDENTITY, now=EPOCH + 1, epoch=EPOCH)


def test_candidate_action_must_match_outcome(meta):
    s = snapshot()
    decision = evaluate(s, config=STRATEGY)
    with pytest.raises(MicroLiveError, match="ACTION_OUTCOME_MISMATCH"):
        candidate_quote(replace(decision, action=Action.PLACE_BUY_NO), s, market(meta),
                        reservation(), IDENTITY, now=EPOCH + 1, epoch=EPOCH)


def test_cli_requires_explicit_arm_and_sends_nothing(monkeypatch):
    called = Mock()
    monkeypatch.setattr("polymaker.mi_micro_live.run_micro_live", called)
    result = CliRunner().invoke(app, ["mi-micro-live"])
    assert result.exit_code != 0
    called.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("response,expected", [
    ({"success": True, "orderID": "0xofficial"}, "0xofficial"),
    ([{"success": True, "orderID": "0xofficial"}], "0xofficial"),
    ({"success": True}, None),
    ({"success": True, "orderID": ""}, None),
])
async def test_official_gateway_one_post_and_id(meta, response, expected):
    client = SimpleNamespace(create_order=Mock(return_value={}), post_orders=Mock(return_value=response))
    gw = ExecutionGateway(Config(), paper=False)
    async def inline(fn):
        return fn()
    gw._client, gw._io = client, inline
    q = quote(meta)
    try:
        if expected is None:
            with pytest.raises(PlacementUnknown):
                await gw.place([q], market(meta), force_post_only=True, strict_placement=True)
        else:
            placed = await gw.place([q], market(meta), force_post_only=True, strict_placement=True)
            assert [o.order_id for o in placed] == [expected]
        client.post_orders.assert_called_once()
        assert client.post_orders.call_args.kwargs["post_only"] is True
    finally:
        gw.close()


@pytest.mark.asyncio
async def test_safe_normalized_price_is_exactly_what_gateway_signs(meta):
    s = replace(snapshot(("0.645", "0.660"), ("0.345", "0.355")),
                tick_size=Decimal("0.001"))
    q = quote(meta, s=s)
    signed = []

    def sign(args, options):
        signed.append(normalize_signing_price(args.price, float(options.tick_size)))
        return {"signed_price": signed[-1]}

    client = SimpleNamespace(create_order=Mock(side_effect=sign),
                             post_orders=Mock(return_value=[{"success": True, "orderID": "0xofficial"}]))
    gw = ExecutionGateway(Config(), paper=False)
    async def inline(fn):
        return fn()
    gw._client, gw._io = client, inline
    try:
        placed = await gw.place([q], market(meta), force_post_only=True, strict_placement=True)
        assert q.price == 0.64 == signed[0] == placed[0].price
        client.post_orders.assert_called_once()
        assert client.post_orders.call_args.kwargs["post_only"] is True
    finally:
        gw.close()


class FakeGateway:
    def __init__(self, rows, *, cancel_ok=True, position=5, trades=None, open_orders=None,
                 get_order_exc=None):
        self._paper = False
        self.rows = list(rows)
        self.cancel_ok = cancel_ok
        self.position = position
        self.trades = list(trades) if trades is not None else []
        self.get_order_exc = get_order_exc
        self.cancel_calls = 0
        self.orders = list(open_orders) if open_orders is not None else []

    async def get_order(self, _order_id):
        if self.get_order_exc is not None:
            raise self.get_order_exc
        return self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]

    async def get_trades(self, *, market=None, asset_id=None):
        return list(self.trades)

    async def cancel(self, _ids):
        self.cancel_calls += 1
        return self.cancel_ok

    async def open_orders(self):
        return self.orders

    async def balance_allowance(self):
        return {"balance": "25000000"}

    async def positions(self):
        return {"yes-token": (self.position, 0)}


def order():
    return OpenOrder("0xofficial", "yes-token", Side.BUY, 0.64, 5, OrderState.LIVE)


def row(matched, status="LIVE"):
    return {"id": "0xofficial", "asset_id": "yes-token", "side": "BUY",
            "price": "0.64", "original_size": "5", "size_matched": str(matched),
            "status": status}


@pytest.mark.asyncio
async def test_full_fill_reconciles_without_cancel(meta):
    gw = FakeGateway([row(5)], position=5)
    status, matched, summary = await settle_order(gw, order(), market(meta), deadline=10,
                                                   now=lambda: 0)
    assert status == "FILLED" and matched == 5
    assert summary["yes_shares"] == 5 and gw.cancel_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("filled,status,position", [(0, "CANCELLED", 0), (2, "PARTIAL", 2)])
async def test_unfilled_or_partial_cancels_and_reconciles(meta, filled, status, position):
    gw = FakeGateway([row(filled), row(filled, "CANCELED")], position=position)
    async def no_sleep(_):
        return None
    result, matched, _summary = await settle_order(gw, order(), market(meta), deadline=0,
                                                   now=lambda: 0, sleep=no_sleep)
    assert result == status and matched == filled and gw.cancel_calls == 1


@pytest.mark.asyncio
async def test_partial_fill_cancels_before_observation_deadline(meta):
    gw = FakeGateway([row(2), row(2, "CANCELED")], position=2)
    async def no_sleep(_):
        pytest.fail("partial fill should cancel immediately")
    result, matched, _ = await settle_order(
        gw, order(), market(meta), deadline=100, now=lambda: 0, sleep=no_sleep,
    )
    assert result == "PARTIAL" and matched == 2 and gw.cancel_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_ok", [False, True])
async def test_cancel_failure_or_unconfirmed_stays_uncertain(meta, cancel_ok):
    gw = FakeGateway([row(0)], cancel_ok=cancel_ok, position=0)
    async def no_sleep(_):
        return None
    with pytest.raises(MicroLiveError, match="MANUAL ATTENTION"):
        await settle_order(gw, order(), market(meta), deadline=0, now=lambda: 0,
                           sleep=no_sleep)
    assert gw.cancel_calls == 1


@pytest.mark.asyncio
async def test_reconcile_refuses_open_commitment_or_missing_fill(meta):
    gw = FakeGateway([row(5)], position=0)
    with pytest.raises(MicroLiveError, match="POSITION_FILL_MISMATCH"):
        await reconcile(gw, order(), Decimal(5), market(meta))
    gw.orders = [order()]
    with pytest.raises(MicroLiveError, match="OPEN_COMMITMENT"):
        await reconcile(gw, order(), Decimal(0), market(meta))


def test_order_id_and_identity_evidence(meta):
    assert order_evidence({**row(5), "orderID": "0xofficial"}, order())[0] == 5
    with pytest.raises(MicroLiveError, match="ORDER_STATE_UNKNOWN"):
        order_evidence({**row(0), "asset_id": "wrong"}, order())
    with pytest.raises(MicroLiveError, match="ORDER_STATE_UNKNOWN"):
        order_evidence({**row(0), "orderID": "different"}, order())


@pytest.mark.asyncio
@pytest.mark.parametrize("armed,expected", [(True, "FILLED"), (False, "NOT_ARMED")])
async def test_one_market_lifecycle_releases_only_after_reconcile(monkeypatch, meta, armed, expected):
    import asyncio
    from unittest.mock import AsyncMock

    from polymaker import mi_cash_reservation as cash_module
    from polymaker import mi_micro_live as micro
    from polymaker.mi_cash_reservation import ConfirmedSpendableCollateral
    from polymaker.mi_readonly_account import AccountSnapshot

    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())
    monkeypatch.setattr(micro, "validate_market", lambda *_: None)
    monkeypatch.setattr(micro, "current_identity", lambda *_: IDENTITY)
    monkeypatch.setattr(micro, "authorize", lambda *_args, **_kwargs:
                        SimpleNamespace(identity=IDENTITY))
    monkeypatch.setattr(micro, "read_confirmed_spendable_collateral",
                        AsyncMock(return_value=ConfirmedSpendableCollateral(IDENTITY, 25_000_000)))
    monkeypatch.setattr(micro, "build_snapshot", lambda *_: snapshot())
    monkeypatch.setattr(micro.time, "time", lambda: EPOCH + 1)

    class FakeMarketData:
        def __init__(self, **_kwargs):
            pass
        def set_markets(self, _markets):
            pass
        async def run(self):
            await asyncio.Event().wait()
        def stop(self):
            pass
        def books_fresh(self, _meta):
            return True
        def book(self, _token):
            return SimpleNamespace(best_bid=lambda: SimpleNamespace(price=0.64),
                                   best_ask=lambda: SimpleNamespace(price=0.66))

    monkeypatch.setattr(micro, "MarketDataService", FakeMarketData)
    gw = FakeGateway([row(5)], position=5)
    gw.place = AsyncMock(return_value=[order()])
    account = SimpleNamespace(snapshot=AsyncMock(return_value=AccountSnapshot({}, (), Decimal(25))))
    messages = []
    result = await micro.run_market(SimpleNamespace(proxy=None), gw, account, market(meta), EPOCH,
                                    mode=MicroMode.MICRO_LIVE, arm=lambda: armed,
                                    emit=messages.append)
    assert result == expected
    assert gw.place.await_count == int(armed)
    assert any("CANDIDATE" in message and "maker_price=" in message for message in messages)
    assert any("RESERVATION RELEASED" in message for message in messages)
    assert all(record.state is ReservationState.RELEASED
               for record in cash_module._PROCESS_AUTHORITY.records.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("bid,ask,signing_tick,expected_place", [
    (0.655, 0.660, 0.01, False),
    (0.655, 0.659, 0.01, False),
    (0.645, 0.660, 0.01, True),
    (0.645, 0.660, None, False),
])
async def test_tick_drift_checks_signed_price_before_place(
    monkeypatch, meta, bid, ask, signing_tick, expected_place,
):
    import asyncio
    from unittest.mock import AsyncMock

    from polymaker import mi_cash_reservation as cash_module
    from polymaker import mi_micro_live as micro
    from polymaker.mi_cash_reservation import ConfirmedSpendableCollateral
    from polymaker.mi_readonly_account import AccountSnapshot

    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())
    monkeypatch.setattr(micro, "validate_market", lambda *_: None)
    monkeypatch.setattr(micro, "current_identity", lambda *_: IDENTITY)
    monkeypatch.setattr(micro, "authorize", lambda *_args, **_kwargs:
                        SimpleNamespace(identity=IDENTITY))
    monkeypatch.setattr(micro, "read_confirmed_spendable_collateral",
                        AsyncMock(return_value=ConfirmedSpendableCollateral(IDENTITY, 25_000_000)))
    monkeypatch.setattr(micro, "build_snapshot", lambda *_: replace(
        snapshot((str(bid), str(ask)), ("0.345", "0.355")), tick_size=Decimal("0.001")))
    monkeypatch.setattr(micro.time, "time", lambda: EPOCH + 1)

    class FakeMarketData:
        def __init__(self, **_kwargs):
            pass
        def set_markets(self, _markets):
            pass
        async def run(self):
            await asyncio.Event().wait()
        def stop(self):
            pass
        def books_fresh(self, _meta):
            return True
        def book(self, _token):
            return SimpleNamespace(best_bid=lambda: SimpleNamespace(price=bid),
                                   best_ask=lambda: SimpleNamespace(price=ask))

    monkeypatch.setattr(micro, "MarketDataService", FakeMarketData)
    gw = FakeGateway([row(5)], position=5)
    post_orders = Mock(return_value=[order()])
    async def fake_place(_quotes, _meta, **_kwargs):
        return post_orders()
    gw.place = AsyncMock(side_effect=fake_place)
    account = SimpleNamespace(snapshot=AsyncMock(return_value=AccountSnapshot({}, (), Decimal(25))))
    selected_market = replace(market(meta), tick_size=signing_tick)
    run = micro.run_market(SimpleNamespace(proxy=None), gw, account, selected_market, EPOCH,
                           mode=MicroMode.MICRO_LIVE, arm=lambda: True, emit=lambda _: None)
    if expected_place:
        assert await run == "FILLED"
        assert gw.place.call_args.args[0][0].price == 0.64
        assert gw.place.await_count == 1
        post_orders.assert_called_once()
    else:
        with pytest.raises(MicroLiveError, match="MAKER_OR_TOKEN_INVALID|SIGNING_PRICE_OR_TICK_INVALID"):
            await run
        gw.place.assert_not_awaited()
        post_orders.assert_not_called()


@pytest.mark.asyncio
async def test_no_candidate_exits_without_placement(monkeypatch, meta):
    import asyncio
    from unittest.mock import AsyncMock

    from polymaker import mi_cash_reservation as cash_module
    from polymaker import mi_micro_live as micro
    from polymaker.mi_cash_reservation import ConfirmedSpendableCollateral
    from polymaker.mi_readonly_account import AccountSnapshot

    clock = SimpleNamespace(value=EPOCH + 1)
    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())
    monkeypatch.setattr(micro, "validate_market", lambda *_: None)
    monkeypatch.setattr(micro, "current_identity", lambda *_: IDENTITY)
    monkeypatch.setattr(micro, "read_confirmed_spendable_collateral",
                        AsyncMock(return_value=ConfirmedSpendableCollateral(IDENTITY, 25_000_000)))
    monkeypatch.setattr(micro, "build_snapshot", lambda *_: snapshot(("0.49", "0.51"),
                                                                       ("0.49", "0.51")))
    monkeypatch.setattr(micro.time, "time", lambda: clock.value)

    class FakeMarketData:
        def __init__(self, **_kwargs):
            pass
        def set_markets(self, _markets):
            pass
        async def run(self):
            await asyncio.Event().wait()
        def stop(self):
            pass
        def books_fresh(self, _meta):
            return True
        connected = True
        metrics = {"queue_full": 0}

    monkeypatch.setattr(micro, "MarketDataService", FakeMarketData)

    async def account_snapshot(_meta):
        clock.value = EPOCH + 285
        return AccountSnapshot({}, (), Decimal(25))

    gw = FakeGateway([row(0)], position=0)
    gw.place = AsyncMock()
    account = SimpleNamespace(snapshot=account_snapshot)
    result = await micro.run_market(SimpleNamespace(proxy=None), gw, account, market(meta), EPOCH,
                                    mode=MicroMode.MICRO_LIVE, arm=lambda: True,
                                    emit=lambda _: None)
    assert result == "NO_VALID_MI_CANDIDATE"
    gw.place.assert_not_awaited()
    assert all(record.state is ReservationState.RELEASED
               for record in cash_module._PROCESS_AUTHORITY.records.values())


def test_process_reservation_revalidation_detects_release(monkeypatch):
    from polymaker import mi_cash_reservation as cash_module
    from polymaker.mi_cash_reservation import ConfirmedSpendableCollateral, MICashReservationBook

    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())
    book = MICashReservationBook()
    record = book.reserve(ConfirmedSpendableCollateral(IDENTITY, 25_000_000),
                          "0xcond", current_identity=IDENTITY)
    assert book.is_active(record)
    book.release(record.reservation_id, current_identity=IDENTITY, condition_id="0xcond")
    assert not book.is_active(record)


@pytest.mark.asyncio
async def test_runner_waits_for_next_market_without_trading_old_one(monkeypatch, meta):
    from unittest.mock import AsyncMock

    from polymaker import mi_micro_live as micro

    clock = SimpleNamespace(value=EPOCH + 180)
    monkeypatch.setattr(micro.time, "time", lambda: clock.value)
    config = SimpleNamespace()
    monkeypatch.setattr(micro.Config, "load", lambda *_: config)
    monkeypatch.setattr(micro, "validate_official_hosts", lambda *_: None)

    class RunnerGateway:
        def __init__(self, *_args, **_kwargs):
            self.place = AsyncMock()
        async def connect(self):
            pass
        def close(self):
            pass

    class RunnerAccount:
        def __init__(self, *_args):
            pass
        async def connect(self):
            pass

    monkeypatch.setattr(micro, "ExecutionGateway", RunnerGateway)
    monkeypatch.setattr(micro, "OfficialReadOnlyAccount", RunnerAccount)

    async def advance(seconds):
        clock.value += seconds

    monkeypatch.setattr(micro.asyncio, "sleep", advance)
    resolve = AsyncMock(return_value=market(meta))
    run = AsyncMock(return_value="NO_VALID_MI_CANDIDATE")
    monkeypatch.setattr(micro, "resolve_btc5m_market", resolve)
    monkeypatch.setattr(micro, "run_market", run)
    messages = []
    assert await micro.run_micro_live("fake", arm=lambda: False, emit=messages.append) == "NO_VALID_MI_CANDIDATE"
    assert resolve.call_args.args[1] == slug_for_epoch(EPOCH + 300)
    assert run.call_args.args[4] == EPOCH + 300
    assert any("SKIP" in message for message in messages)


@pytest.mark.asyncio
async def test_unknown_placement_retains_reservation(monkeypatch, meta):
    import asyncio
    from unittest.mock import AsyncMock

    from polymaker import mi_cash_reservation as cash_module
    from polymaker import mi_micro_live as micro
    from polymaker.mi_cash_reservation import ConfirmedSpendableCollateral
    from polymaker.mi_readonly_account import AccountSnapshot

    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())
    monkeypatch.setattr(micro, "validate_market", lambda *_: None)
    monkeypatch.setattr(micro, "current_identity", lambda *_: IDENTITY)
    monkeypatch.setattr(micro, "authorize", lambda *_args, **_kwargs:
                        SimpleNamespace(identity=IDENTITY))
    monkeypatch.setattr(micro, "read_confirmed_spendable_collateral",
                        AsyncMock(return_value=ConfirmedSpendableCollateral(IDENTITY, 25_000_000)))
    monkeypatch.setattr(micro, "build_snapshot", lambda *_: snapshot())
    monkeypatch.setattr(micro.time, "time", lambda: EPOCH + 1)

    class FakeMarketData:
        def __init__(self, **_kwargs):
            pass
        def set_markets(self, _markets):
            pass
        async def run(self):
            await asyncio.Event().wait()
        def stop(self):
            pass
        def books_fresh(self, _meta):
            return True
        def book(self, _token):
            return SimpleNamespace(best_bid=lambda: SimpleNamespace(price=0.64),
                                   best_ask=lambda: SimpleNamespace(price=0.66))

    monkeypatch.setattr(micro, "MarketDataService", FakeMarketData)
    gw = FakeGateway([row(0)], position=0)
    gw.place = AsyncMock(side_effect=PlacementUnknown("unknown POST"))
    account = SimpleNamespace(snapshot=AsyncMock(return_value=AccountSnapshot({}, (), Decimal(25))))
    messages = []
    with pytest.raises(PlacementUnknown):
        await micro.run_market(SimpleNamespace(proxy=None), gw, account, market(meta), EPOCH,
                               mode=MicroMode.MICRO_LIVE, arm=lambda: True,
                               emit=messages.append)
    assert gw.place.await_count == 1
    assert any("MANUAL ATTENTION REQUIRED" in message for message in messages)
    assert all(record.state is ReservationState.ACTIVE
               for record in cash_module._PROCESS_AUTHORITY.records.values())


@pytest.mark.asyncio
async def test_wrong_runtime_mode_denies_before_readiness(meta):
    from polymaker import mi_micro_live as micro

    gw = FakeGateway([row(0)])
    gw._paper = True
    with pytest.raises(MicroLiveError, match="EXPLICIT_MICRO_LIVE_MODE_REQUIRED"):
        await micro.run_market(SimpleNamespace(), gw, SimpleNamespace(), market(meta), EPOCH,
                               mode=MicroMode.MICRO_LIVE, arm=lambda: True)


def test_absent_reservation_denies(meta):
    s = snapshot()
    with pytest.raises(MicroLiveError, match="RESERVATION"):
        candidate_quote(evaluate(s, config=STRATEGY), s, market(meta), None,
                        IDENTITY, now=EPOCH + 1, epoch=EPOCH)


# ── M3 readiness hotfix: MI must reuse FavoriteFirst feed/connectivity
#    readiness for the current market before it may evaluate/place. ──────────


class ProbedMarketData:
    """MarketDataService stand-in whose FL freshness is driven per probe.

    ``ready(meta)`` is consulted on every ``books_fresh`` probe (the exact
    FavoriteFirst market-feed readiness primitive). Each probe advances the
    fake clock and re-arms the run loop's dirty event so the tests never depend
    on real wall-clock time.
    """

    def __init__(self, ready, *, clock, on_dirty=None, queue_full=0, connected=True,
                 bid=0.64, ask=0.66, **_kwargs):
        self._ready = ready
        self._clock = clock
        self._on_dirty = on_dirty or (lambda *_args: None)
        self.connected = connected
        self.queue_full = queue_full
        self._book = SimpleNamespace(best_bid=lambda: SimpleNamespace(price=bid),
                                     best_ask=lambda: SimpleNamespace(price=ask))

    @property
    def metrics(self):
        return {"queue_full": self.queue_full}

    def set_markets(self, _markets):
        pass

    async def run(self):
        import asyncio
        await asyncio.Event().wait()

    def stop(self):
        pass

    def books_fresh(self, meta):
        self._clock.value += 5
        self._on_dirty(meta.condition_id, meta.yes.token_id)
        return bool(self._ready(meta))

    def book(self, _token):
        return self._book


class GuardedGateway:
    """Gateway stand-in that runs MI's real submit guard before its fake POST."""

    _paper = False

    def __init__(self):
        self.post_orders = Mock(return_value=[{"success": True, "orderID": "0xofficial"}])
        self.place_calls = 0

    async def place(self, quotes, meta, *, submit_guard=None, force_post_only=False,
                    strict_placement=False):
        self.place_calls += 1
        if submit_guard is not None and not submit_guard():
            return []  # zero POST, exactly like ExecutionGateway's abort path
        self.post_orders()
        return [order()]

    async def get_order(self, _order_id):
        return row(5)

    async def get_trades(self, *, market=None, asset_id=None):
        return []

    async def cancel(self, _ids):
        return True

    async def open_orders(self):
        return []

    async def balance_allowance(self):
        return {"balance": "25000000"}

    async def positions(self):
        return {"yes-token": (5, 0)}


def _install_readiness_harness(monkeypatch, ready, clock, *, queue_full=0, connected=True):
    from unittest.mock import AsyncMock

    from polymaker import mi_cash_reservation as cash_module
    from polymaker import mi_micro_live as micro
    from polymaker.mi_cash_reservation import ConfirmedSpendableCollateral
    from polymaker.mi_readonly_account import AccountSnapshot

    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())
    monkeypatch.setattr(micro, "validate_market", lambda *_: None)
    monkeypatch.setattr(micro, "current_identity", lambda *_: IDENTITY)
    monkeypatch.setattr(micro, "authorize", lambda *_a, **_k: SimpleNamespace(identity=IDENTITY))
    monkeypatch.setattr(micro, "read_confirmed_spendable_collateral",
                        AsyncMock(return_value=ConfirmedSpendableCollateral(IDENTITY, 25_000_000)))
    monkeypatch.setattr(micro, "build_snapshot", lambda *_: snapshot())
    monkeypatch.setattr(micro.time, "time", lambda: clock.value)
    monkeypatch.setattr(micro, "MarketDataService",
                        lambda **kwargs: ProbedMarketData(ready, clock=clock,
                                                          queue_full=queue_full,
                                                          connected=connected, **kwargs))
    account = SimpleNamespace(snapshot=AsyncMock(return_value=AccountSnapshot({}, (), Decimal(25))))
    return micro, account


@pytest.mark.asyncio
async def test_subscribed_but_not_ready_never_places(monkeypatch, meta):
    """A book may exist and a candidate be computable, but FL readiness is
    False for the current connection -> zero gateway.place / zero POST."""
    from types import SimpleNamespace as NS

    clock = NS(value=EPOCH + 1)
    micro, account = _install_readiness_harness(
        monkeypatch, lambda _m: False, clock)
    gw = GuardedGateway()
    messages = []
    result = await micro.run_market(NS(proxy=None), gw, account, market(meta), EPOCH,
                                    mode=MicroMode.MICRO_LIVE, arm=lambda: True,
                                    emit=messages.append)
    assert result == "FEED_READINESS_TIMEOUT"
    assert gw.place_calls == 0
    gw.post_orders.assert_not_called()
    assert not any("CANDIDATE" in message for message in messages)
    assert any("waiting_for_feed_readiness" in message for message in messages)


def test_readiness_requires_connected_fresh_and_clear_queue(meta):
    """Reuses the exact FavoriteFirst conditions: connected + books_fresh +
    queue_full == 0. Any one missing is not readiness."""
    from types import SimpleNamespace as NS

    from polymaker.mi_micro_live import market_feed_ready

    def book_md(*, connected=True, queue_full=0, fresh=True):
        return NS(connected=connected, metrics={"queue_full": queue_full},
                  books_fresh=lambda _m: fresh)

    assert market_feed_ready(book_md(), meta) is True
    assert market_feed_ready(book_md(connected=False), meta) is False
    assert market_feed_ready(book_md(queue_full=1), meta) is False
    assert market_feed_ready(book_md(fresh=False), meta) is False


@pytest.mark.asyncio
async def test_readiness_then_single_post(monkeypatch, meta):
    """Readiness false then true on the same market -> exactly one fake POST."""
    from types import SimpleNamespace as NS

    clock = NS(value=EPOCH + 1)
    state = {"probes": 0}

    def ready(_meta):
        state["probes"] += 1
        return state["probes"] >= 2

    micro, account = _install_readiness_harness(monkeypatch, ready, clock)
    gw = GuardedGateway()
    messages = []
    result = await micro.run_market(NS(proxy=None), gw, account, market(meta), EPOCH,
                                    mode=MicroMode.MICRO_LIVE, arm=lambda: True,
                                    emit=messages.append)
    assert result == "FILLED"
    assert gw.place_calls == 1
    gw.post_orders.assert_called_once()
    assert any("feed_ready" in message for message in messages)
    assert any("CANDIDATE" in message for message in messages)


def test_new_market_cannot_inherit_readiness(monkeypatch, meta):
    """A fresh condition_id must re-establish its own FL readiness."""
    clock = SimpleNamespace(value=EPOCH + 1)
    ready_markets = {"0xconda"}
    md = ProbedMarketData(lambda m: m.condition_id.lower() in ready_markets, clock=clock)
    meta_a = replace(market(meta), condition_id="0xcondA")
    meta_b = replace(market(meta), condition_id="0xcondB")
    from polymaker.mi_micro_live import market_feed_ready

    assert market_feed_ready(md, meta_a) is True
    assert market_feed_ready(md, meta_b) is False  # cannot inherit A's readiness
    ready_markets.add("0xcondb")
    assert market_feed_ready(md, meta_b) is True


@pytest.mark.asyncio
async def test_new_market_not_ready_gets_zero_post(monkeypatch, meta):
    from types import SimpleNamespace as NS

    clock = NS(value=EPOCH + 1)
    micro, account = _install_readiness_harness(monkeypatch, lambda _m: False, clock)
    gw = GuardedGateway()
    meta_b = replace(market(meta), condition_id="0xcondB")
    result = await micro.run_market(NS(proxy=None), gw, account, meta_b, EPOCH,
                                    mode=MicroMode.MICRO_LIVE, arm=lambda: True,
                                    emit=lambda _m: None)
    assert result == "FEED_READINESS_TIMEOUT"
    assert gw.place_calls == 0
    gw.post_orders.assert_not_called()


@pytest.mark.asyncio
async def test_final_stale_guard_still_blocks_post(monkeypatch, meta):
    """Readiness passes, candidate is built, connectivity goes stale at the
    final guard -> zero POST and the real root cause is preserved."""
    from types import SimpleNamespace as NS

    clock = NS(value=EPOCH + 1)
    state = {"probes": 0}

    def ready(_meta):
        state["probes"] += 1
        return state["probes"] <= 2  # gate + pre-place pass; the guard probe fails

    micro, account = _install_readiness_harness(monkeypatch, ready, clock)
    gw = GuardedGateway()
    messages = []
    result = await micro.run_market(NS(proxy=None), gw, account, market(meta), EPOCH,
                                    mode=MicroMode.MICRO_LIVE, arm=lambda: True,
                                    emit=messages.append)
    assert gw.place_calls >= 1        # the final guard was actually consulted
    gw.post_orders.assert_not_called()  # zero POST
    assert result == "STALE_CONNECTIVITY"
    assert any("CANDIDATE" in message for message in messages)


def test_mi_arm_uses_no_blocking_input(monkeypatch):
    """--arm is the only human intent: no stdin prompt / typed confirmation."""
    import builtins
    from unittest.mock import Mock

    async def fake_run(*_args, **_kwargs):
        return "DONE"

    called = Mock(side_effect=fake_run)
    monkeypatch.setattr("polymaker.mi_micro_live.run_micro_live", called)
    monkeypatch.setattr(builtins, "input", Mock(side_effect=AssertionError("stdin read")))
    result = CliRunner().invoke(app, ["mi-micro-live", "--arm"], input="")
    assert result.exit_code == 0, result.output
    called.assert_called_once()


# ── M3 order-state hotfix: the authenticated order read is one evidence source,
#    not a permanent authoritative lookup. An absent primary order row must be
#    reconciled from official account truth; malformed/ambiguous state still
#    fails closed and retains the reservation. ──────────────────────────────


async def _no_sleep(_seconds):
    return None


@pytest.mark.asyncio
async def test_missing_primary_order_read_reconciles_to_terminal_no_risk(meta):
    """Case E: order absent + no open order + no fill -> terminal, releasable."""
    gw = FakeGateway([], get_order_exc=OrderNotFound("gone"), position=0)
    result, matched, summary = await settle_order(
        gw, order(), market(meta), deadline=0, now=lambda: 0, sleep=_no_sleep,
    )
    assert result == "NOT_OPEN_UNFILLED" and matched == 0
    assert summary["open_orders"] == 0 and summary["yes_shares"] == 0
    assert gw.cancel_calls == 1  # cancellation was still attempted


@pytest.mark.asyncio
async def test_missing_primary_order_read_with_fill_is_not_called_no_fill(meta):
    """Case G: order absent but a fill exists -> FILLED/PARTIAL, never 'no fill'."""
    gw = FakeGateway([], get_order_exc=OrderNotFound("gone"), position=5,
                     trades=[{"id": "trade-1"}])
    result, matched, _ = await settle_order(
        gw, order(), market(meta), deadline=0, now=lambda: 0, sleep=_no_sleep,
    )
    assert result == "FILLED" and matched == 5


@pytest.mark.asyncio
async def test_missing_primary_order_read_with_partial_fill(meta):
    gw = FakeGateway([], get_order_exc=OrderNotFound("gone"), position=2,
                     trades=[{"id": "trade-1"}])
    result, matched, _ = await settle_order(
        gw, order(), market(meta), deadline=0, now=lambda: 0, sleep=_no_sleep,
    )
    assert result == "PARTIAL" and matched == 2


@pytest.mark.asyncio
async def test_malformed_primary_order_read_fails_closed(meta):
    """A malformed/differently shaped row is NOT release evidence."""
    gw = FakeGateway([], get_order_exc=GatewayReadError("order read returned malformed data"),
                     position=0)
    with pytest.raises(MicroLiveError, match="MANUAL ATTENTION"):
        await settle_order(gw, order(), market(meta), deadline=0, now=lambda: 0,
                           sleep=_no_sleep)
    assert gw.cancel_calls == 1


@pytest.mark.asyncio
async def test_reconcile_ambiguous_reads_fail_closed(meta):
    """Case F: any failed/conflicting official reconciliation read stays UNKNOWN."""
    # open-order read unavailable
    gw_open = FakeGateway([], position=0)
    async def broken_open_orders():
        raise GatewayReadError("open orders snapshot failed")
    gw_open.open_orders = broken_open_orders
    with pytest.raises(GatewayReadError):
        await prove_terminal_from_account_truth(gw_open, order())
    # open-order read returned a live commitment
    with pytest.raises(MicroLiveError, match="OPEN_COMMITMENT_UNRESOLVED"):
        await prove_terminal_from_account_truth(
            FakeGateway([], open_orders=[order()], position=0), order())
    # position read unavailable
    gw = FakeGateway([], position=0)
    async def broken_positions():
        raise GatewayReadError("positions snapshot failed")
    gw.positions = broken_positions
    with pytest.raises(GatewayReadError):
        await prove_terminal_from_account_truth(gw, order())
    # trade read unavailable
    gw2 = FakeGateway([], position=0)
    async def broken_trades(*, market=None, asset_id=None):
        raise GatewayReadError("trade history read failed")
    gw2.get_trades = broken_trades
    with pytest.raises(GatewayReadError):
        await prove_terminal_from_account_truth(gw2, order())
    # contradictory: flat position but a fill record
    with pytest.raises(MicroLiveError, match="FILL_EVIDENCE_UNRECONCILED"):
        await prove_terminal_from_account_truth(
            FakeGateway([], position=0, trades=[{"id": "trade-1"}]), order())
    # unexpected over-sized position is not silently accepted
    with pytest.raises(MicroLiveError, match="FILL_EVIDENCE_UNRECONCILED"):
        await prove_terminal_from_account_truth(
            FakeGateway([], position=6, trades=[{"id": "trade-1"}]), order())


@pytest.mark.asyncio
async def test_real_order_missing_shape_releases_reservation(monkeypatch, meta):
    """Real-order regression fixture: official id returned, then the primary
    order read is absent while official account truth shows no open order and
    no fill -> the single live clip ends safely with no manual attention."""
    import asyncio
    from unittest.mock import AsyncMock

    from polymaker import mi_cash_reservation as cash_module
    from polymaker import mi_micro_live as micro
    from polymaker.mi_cash_reservation import ConfirmedSpendableCollateral
    from polymaker.mi_readonly_account import AccountSnapshot

    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())
    monkeypatch.setattr(micro, "validate_market", lambda *_: None)
    monkeypatch.setattr(micro, "current_identity", lambda *_: IDENTITY)
    monkeypatch.setattr(micro, "authorize", lambda *_args, **_kwargs:
                        SimpleNamespace(identity=IDENTITY))
    monkeypatch.setattr(micro, "read_confirmed_spendable_collateral",
                        AsyncMock(return_value=ConfirmedSpendableCollateral(IDENTITY, 25_000_000)))
    monkeypatch.setattr(micro, "build_snapshot", lambda *_: snapshot())
    monkeypatch.setattr(micro.time, "time", lambda: EPOCH + 1)

    class FakeMarketData:
        def __init__(self, **_kwargs):
            pass
        def set_markets(self, _markets):
            pass
        async def run(self):
            await asyncio.Event().wait()
        def stop(self):
            pass
        def books_fresh(self, _meta):
            return True
        connected = True
        metrics = {"queue_full": 0}
        def book(self, _token):
            return SimpleNamespace(best_bid=lambda: SimpleNamespace(price=0.64),
                                   best_ask=lambda: SimpleNamespace(price=0.66))

    monkeypatch.setattr(micro, "MarketDataService", FakeMarketData)
    # Official order id was returned, but the subsequent primary read is absent
    # (real shape not yet observed offline) and the account snapshot proves no
    # open commitment and no fill.
    gw = FakeGateway([], get_order_exc=OrderNotFound("order read returned no order"),
                     position=0)
    gw.place = AsyncMock(return_value=[order()])
    account = SimpleNamespace(snapshot=AsyncMock(return_value=AccountSnapshot({}, (), Decimal(25))))
    messages = []
    result = await micro.run_market(SimpleNamespace(proxy=None), gw, account, market(meta), EPOCH,
                                    mode=MicroMode.MICRO_LIVE, arm=lambda: True,
                                    emit=messages.append)
    assert result == "NOT_OPEN_UNFILLED"
    assert gw.place.await_count == 1
    assert any("RESERVATION RELEASED" in message for message in messages)
    assert not any("MANUAL ATTENTION REQUIRED" in message for message in messages)
    assert all(record.state is ReservationState.RELEASED
               for record in cash_module._PROCESS_AUTHORITY.records.values())
