from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from polymaker.domain import OpenOrder, OrderState, Side
from polymaker.execution.gateway import PlacementUnknown
from polymaker.filltest import (
    MAX_NOTIONAL, _tick_price, attempt_wait_seconds,
    candidate_price, choose_candidate, eligible_elapsed, run_filltest,
    safe_remaining_qty,
)
from polymaker.marketdata.orderbook import BookView
from polymaker.state.store import StateStore
from polymaker.state.tracker import UserEventProcessor


def test_time_window_and_order_lifetime():
    assert not eligible_elapsed(29.999)
    assert eligible_elapsed(30) and eligible_elapsed(209.999)
    assert not eligible_elapsed(210)
    assert attempt_wait_seconds(269, 0) == 1
    assert attempt_wait_seconds(105, 0) == 15


def test_candidate_and_five_dollar_cap(meta):
    assert candidate_price(.21, .01) == .20
    assert candidate_price(.20, .01) == .19
    assert choose_candidate(meta, {"yes-token": {"best_ask": .18},
                                   "no-token": {"best_ask": .95}}) is not None
    too_large = replace(meta, min_order_size=100)
    assert choose_candidate(too_large, {"yes-token": {"best_ask": .20},
                                        "no-token": {"best_ask": .20}}) is None
    assert MAX_NOTIONAL == 5


def test_candidate_cap_uses_notional_and_lowest_exposure(meta):
    candidate = choose_candidate(meta, {"yes-token": {"best_ask": .50},
                                        "no-token": {"best_ask": .40}})
    assert candidate == ("no-token", "NO", .39, 5)
    oversized = replace(meta, min_order_size=20)
    assert choose_candidate(oversized, {"yes-token": {"best_ask": .30},
                                        "no-token": {"best_ask": .30}}) is None
    assert _tick_price(.11, .01, above=True) == .11
    assert _tick_price(.105, .01, above=True) == .11


@pytest.mark.parametrize("sold,position,expected", [
    (3, 5, 2),  # positions API lags
    (3, 2, 2),  # positions API caught up
    (1, 2, 2),  # position is the conservative bound
])
def test_safe_remaining_uses_lower_inventory_bound(sold, position, expected):
    assert safe_remaining_qty(5, sold, position, 0) == expected


def test_safe_remaining_rejects_sell_fills_above_opening_inventory():
    with pytest.raises(ValueError, match="exceed opening"):
        safe_remaining_qty(5, 5.01, 5, 0)


class Clock:
    def __init__(self, value): self.value = value
    def now(self): return self.value
    async def sleep(self, seconds): self.value += seconds


class FakeMarket:
    def __init__(self, *, on_connection, **kwargs):
        self.connected = False; self.on_connection = on_connection
        self.connected_since = 0; self.connection_generation = 0
        self.states = {token: SimpleNamespace(snapshot_local_ts=0, snapshot_generation=0)
                       for token in ("yes-token", "no-token")}
        self.delay_snapshot = kwargs.get("delay_snapshot", False)
        self.never_snapshot = kwargs.get("never_snapshot", False)
    def set_markets(self, _): pass
    async def run(self):
        await asyncio.sleep(0)
        self.connected = True; self.connected_since = 1; self.connection_generation = 1
        self.on_connection(True)
        if self.delay_snapshot:
            for _ in range(3):
                await asyncio.sleep(0)
        if not self.never_snapshot:
            for state in self.states.values():
                state.snapshot_local_ts = 2
                state.snapshot_generation = 1
    def view(self, _): return BookView(.1, 10, .17, 10, .09, .18, 10, 10)
    def book(self, token): return self.states.get(token)
    def last_local_ts(self, _): return 1
    def stop(self): pass


class FakeGateway:
    def __init__(self, *_):
        self.creds, self.address = object(), "wallet"
        self.placements = 0; self.cancels = 0; self.open_reads = 0
        self.live = []; self.events = []; self.position_error = False
        self.scoped_cancels = []; self.unknown_open_error = False
        self.processor = None; self.accepted = {}
    async def connect(self): pass
    async def get_book(self, token):
        return {"best_bid": .10, "best_ask": .18 if token == "yes-token" else .95}
    async def place(self, quotes, meta, *, submit_guard, force_post_only, strict_placement=False):
        assert len(quotes) == 1 and force_post_only
        if not submit_guard(): return []
        self.placements += 1
        order = OpenOrder("ours", quotes[0].token_id, quotes[0].side, quotes[0].price,
                          quotes[0].size, OrderState.LIVE)
        self.live = [order]
        self.accepted[order.order_id] = order
        return [order]
    async def cancel(self, ids): self.cancels += 1; self.live = []; return True
    async def cancel_market_asset(self, market, asset):
        self.scoped_cancels.append((market, asset)); self.live = []; return True
    async def open_orders(self):
        self.open_reads += 1
        if self.unknown_open_error and self.open_reads > 1: raise RuntimeError("authoritative read failed")
        return self.live
    async def positions(self):
        if self.position_error: raise RuntimeError("read failure")
        return {}
    async def get_order(self, order_id):
        order = self.accepted.get(order_id)
        owned = self.processor.owned_record(order_id) if self.processor else None
        token = order.token_id if order else str(owned["token_id"])
        side = order.side.value if order else str(owned["side"])
        price = order.price if order else float(owned["submitted_price"])
        size = order.size if order else float(owned["submitted_qty"])
        qty = self.processor.tracked_trade_qty(order_id) if self.processor else 0.0
        return {"id": order_id, "asset_id": token, "side": side,
            "price": str(price), "original_size": str(size),
            "size_matched": str(qty)}
    async def get_trades(self, *, market=None, asset_id=None):
        if not self.processor: return []
        rows = []
        for order_id in self.accepted:
            for ev in self.processor.economic_fills_for_order(order_id):
                rows.append({"id": ev.trade_id, "status": "MATCHED", "market": "0xcond",
                    "maker_orders": [{"order_id": order_id, "asset_id": ev.token_id,
                        "side": ev.our_side.value, "matched_amount": str(ev.size),
                        "price": str(ev.price)}]})
        return rows
    def close(self): pass


class FakeStream:
    def __init__(self, *_args, on_connection, **_kwargs):
        processor = _args[2]
        self.connected = False; self.on_connection = on_connection
        self.callback = lambda pair: processor.on_trade(pair[1], "0xcond")
        self.order_callback = lambda event: processor.on_order(event, "0xcond")
    def set_markets(self, _): pass
    async def run(self):
        await asyncio.sleep(0)
        self.connected = True; self.on_connection(True)
    def stop(self): pass


def config():
    return SimpleNamespace(secrets=SimpleNamespace(has_wallet=True), proxy=None)


async def run_fake(meta, clock, gateway, *, before_submit=None, startup_timeout_s=0.2,
                   stream_never_ready=False, delay_snapshot=False,
                   never_snapshot=False, snapshot_timeout_s=0.2):
    async def resolver(_cfg, _slug): return meta
    def stream_factory(*args, **kwargs):
        gateway.processor = args[2]
        stream = FakeStream(*args, **kwargs)
        if stream_never_ready:
            async def never_ready(): await asyncio.Event().wait()
            stream.run = never_ready
        if before_submit:
            before_submit(stream)
        return stream
    async def sleep(seconds):
        await clock.sleep(seconds)
        await asyncio.sleep(0)
    return await run_filltest(config(), now=clock.now, sleep=sleep,
        resolver=resolver, gateway_factory=lambda _: gateway,
        market_factory=lambda **kwargs: FakeMarket(**kwargs, delay_snapshot=delay_snapshot,
                                                    never_snapshot=never_snapshot),
        stream_factory=stream_factory, startup_timeout_s=startup_timeout_s,
        snapshot_timeout_s=snapshot_timeout_s)


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [29.9, 270, 300])
async def test_outside_window_never_places(meta, elapsed):
    clock = Clock(600 + elapsed); gw = FakeGateway()
    assert not await run_fake(meta, clock, gw)
    assert gw.placements == 0


@pytest.mark.asyncio
async def test_no_candidate_never_places(meta):
    clock = Clock(640); gw = FakeGateway()
    no_candidate = replace(meta, min_order_size=100)
    assert not await run_fake(no_candidate, clock, gw)
    assert gw.placements == 0


@pytest.mark.asyncio
async def test_no_fill_cancel_clean_and_exactly_one_order(meta):
    clock = Clock(640); gw = FakeGateway()
    assert not await run_fake(meta, clock, gw)
    assert (gw.placements, gw.cancels) == (1, 1)
    assert gw.open_reads >= 2


@pytest.mark.asyncio
async def test_venue_authoritative_zero_is_independent_and_reconciled_after_cleanup(meta, capsys):
    clock = Clock(640)
    class VenueZero(FakeGateway):
        def __init__(self):
            super().__init__(); self.sequence = []
        async def cancel(self, ids):
            self.sequence.append("cancel")
            return await super().cancel(ids)
        async def open_orders(self):
            self.sequence.append("open_orders")
            return await super().open_orders()
        async def get_order(self, order_id):
            self.sequence.append("get_order")
            assert self.processor.tracked_trade_qty(order_id) == 0
            return {"id": order_id, "asset_id": "yes-token", "side": "BUY",
                "price": ".17", "original_size": "5", "size_matched": "0"}
        async def get_trades(self, **_kwargs):
            self.sequence.append("get_trades")
            return []
    gw = VenueZero()
    assert not await run_fake(meta, clock, gw)
    output = capsys.readouterr().out
    assert "NO FILL — TEST INCONCLUSIVE" in output
    cancel_index = gw.sequence.index("cancel")
    open_index = gw.sequence.index("open_orders", cancel_index)
    assert cancel_index < open_index < gw.sequence.index("get_order")
    assert gw.sequence.count("get_order") == 1


@pytest.mark.asyncio
async def test_venue_boolean_size_matched_aborts_without_clean_no_fill(meta, capsys):
    clock = Clock(640)
    class BooleanVenue(FakeGateway):
        async def get_order(self, order_id):
            return {"id": order_id, "asset_id": "yes-token", "side": "BUY",
                "price": ".17", "original_size": "5", "size_matched": False}
        async def get_trades(self, **_kwargs): return []
    gw = BooleanVenue()
    assert not await run_fake(meta, clock, gw)
    output = capsys.readouterr().out
    assert "NO FILL — TEST INCONCLUSIVE" not in output
    assert "filltest_aborted" in output


@pytest.mark.asyncio
async def test_final_truth_runs_fresh_read_after_cleanup_despite_prior_inflight_reconcile(
        meta, capsys):
    from polymaker.state.tracker import OrderEvent
    clock = Clock(640)
    class RacingGateway(FakeGateway):
        def __init__(self):
            super().__init__(); self.sequence = []; self.order_reads = 0
        async def cancel(self, ids):
            self.live = []
            self.processor.on_order(OrderEvent("ours", "yes-token", Side.BUY, .17, 0,
                original_size=5, size_matched=5, status="UPDATE"), "0xcond")
            await asyncio.sleep(0)
            return True
        async def open_orders(self):
            await asyncio.sleep(0)
            self.sequence.append("cleanup_verified" if self.placements else "pretrade_open_orders")
            return []
        async def get_order(self, order_id):
            self.order_reads += 1
            self.sequence.append("get_order")
            return {"id": order_id, "asset_id": "yes-token", "side": "BUY",
                "price": ".17", "original_size": "5", "size_matched": "0"}
        async def get_trades(self, **_kwargs): return []
    gw = RacingGateway()
    assert not await run_fake(meta, clock, gw)
    capsys.readouterr()
    assert gw.order_reads == 2  # in-flight operational read plus fresh final read
    cleanup_index = gw.sequence.index("cleanup_verified")
    assert gw.sequence.index("get_order", cleanup_index + 1) > cleanup_index


@pytest.mark.asyncio
async def test_final_rest_truth_recovers_fill_when_both_ws_events_are_missed(meta, capsys):
    clock = Clock(640)
    gw = FakeGateway()
    gw.remote_position = 0.0
    original_place = gw.place
    async def place(quotes, *args, **kwargs):
        result = await original_place(quotes, *args, **kwargs)
        if result:
            result[0].order_id = "BUY123" if quotes[0].side is Side.BUY else "SELL123"
            gw.remote_position = 5.0 if quotes[0].side is Side.BUY else gw.remote_position
        return result
    gw.place = place
    async def positions():
        return {"yes-token": (gw.remote_position, .17)} if gw.remote_position else {}
    gw.positions = positions
    async def get_order(order_id):
        return {"id": order_id, "asset_id": "yes-token", "side": "BUY" if order_id == "BUY123" else "SELL",
            "price": ".17", "original_size": "5", "size_matched": "5" if order_id == "BUY123" else "0"}
    async def get_trades(**_kwargs):
        return [{"id": "TRADE1", "market": "0xcond", "status": "MATCHED",
            "maker_orders": [{"order_id": "BUY123", "asset_id": "yes-token", "side": "BUY",
                "matched_amount": "5", "price": ".17"}]}]
    gw.get_order, gw.get_trades = get_order, get_trades
    assert not await run_fake(meta, clock, gw)
    output = capsys.readouterr().out
    assert "filltest_open_fill" in output and "matched_amount=5.0" in output
    assert "NO FILL — TEST INCONCLUSIVE" not in output
    assert gw.placements == 2  # recovered opening truth allowed the close phase to start


@pytest.mark.asyncio
async def test_malformed_final_rest_truth_never_reports_clean_no_fill(meta, capsys):
    clock = Clock(640)
    gw = FakeGateway()
    async def malformed(_order_id):
        return {"id": "ours", "asset_id": "yes-token", "side": "BUY", "price": ".17",
            "original_size": "5"}  # missing authoritative cumulative state
    gw.get_order = malformed
    gw.get_trades = lambda **_kwargs: asyncio.sleep(0, result=[])
    assert not await run_fake(meta, clock, gw)
    output = capsys.readouterr().out
    assert "NO FILL — TEST INCONCLUSIVE" not in output
    assert "filltest_aborted" in output
    assert gw.placements == 1


@pytest.mark.asyncio
async def test_correlated_partial_open_is_incomplete_and_never_rebuys(meta):
    clock = Clock(640); gw = FakeGateway()
    def deliver(stream):
        from polymaker.state.tracker import TradeEvent
        from polymaker.domain import Side, TradeState
        sent = False
        async def sleep(seconds):
            nonlocal sent
            clock.value += seconds
            if gw.placements and not sent:
                sent = True
                stream.callback(("ours", TradeEvent("yes-token", Side.BUY, .17, 2,
                    "trade", TradeState.MATCHED, clock.value, "ours")))
        clock.sleep = sleep
    assert not await run_fake(meta, clock, gw, before_submit=deliver)
    assert gw.placements == 1 and gw.cancels == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first_close_fill,reported_after_first,expected", [
    (3.0, 5.0, True),  # lagging position must still cap retry at 2
    (3.0, 2.0, True),
    (1.0, 2.0, False),  # attributed fills remain incomplete when position is flat
])
async def test_maker_round_trip_uses_conservative_close_retry(
        meta, first_close_fill, reported_after_first, expected):
    meta = replace(meta, min_order_size=2.0)
    clock = Clock(640)
    gw = FakeGateway()
    gw.positions = lambda: asyncio.sleep(0, result=getattr(gw, "position_snapshot", {}))
    original_place = gw.place
    fills = []
    seq = 0

    async def place(quotes, *args, **kwargs):
        nonlocal seq
        result = await original_place(quotes, *args, **kwargs)
        if result:
            seq += 1
            result[0].order_id = f"ours-{seq}"
            fills.append((result[0].order_id, quotes[0].side, quotes[0].size, quotes[0].price))
        return result
    gw.place = place
    sent = set()

    def deliver(stream):
        from polymaker.state.tracker import TradeEvent
        from polymaker.domain import TradeState
        async def advancing_sleep(seconds):
            clock.value += seconds
            for order_id, side, qty, price in fills:
                if order_id in sent:
                    continue
                sent.add(order_id)
                sell_orders = [f for f in fills if f[1] is Side.SELL]
                event_qty = (min(first_close_fill, qty)
                             if side is Side.SELL and order_id == sell_orders[0][0] else qty)
                stream.callback((order_id, TradeEvent("yes-token", side, price, event_qty,
                    f"trade-{order_id}", TradeState.MATCHED, clock.value, order_id)))
                sell_order_count = len([f for f in fills if f[1] is Side.SELL])
                remaining = (reported_after_first if sell_order_count == 1 else 0.0) \
                    if side is Side.SELL else qty
                gw.position_snapshot = {"yes-token": (remaining, price)}
        clock.sleep = advancing_sleep

    assert await run_fake(meta, clock, gw, before_submit=deliver) is expected
    sells = [f for f in fills if f[1] is Side.SELL]
    assert [x[1] for x in fills] == [Side.BUY, Side.SELL, Side.SELL]
    assert [x[2] for x in fills] == [5.0, 5.0, 2.0]
    assert all(x[3] > 0 for x in fills)
    assert [f[2] for f in sells] == [5.0, 2.0]


@pytest.mark.asyncio
async def test_zero_position_without_attributed_sell_is_not_pass(meta, capsys):
    clock = Clock(640)
    gw = FakeGateway()
    gw.positions = lambda: asyncio.sleep(0, result=getattr(gw, "position_snapshot", {}))
    original_place = gw.place
    orders = []

    async def place(quotes, *args, **kwargs):
        result = await original_place(quotes, *args, **kwargs)
        if result:
            result[0].order_id = f"order-{len(orders)+1}"
            orders.append((result[0].order_id, quotes[0].side, quotes[0].size, quotes[0].price))
            if quotes[0].side is Side.SELL:
                gw.position_snapshot = {"yes-token": (0.0, 0.0)}
        return result
    gw.place = place

    def deliver(stream):
        from polymaker.state.tracker import TradeEvent
        from polymaker.domain import TradeState
        async def advancing_sleep(seconds):
            clock.value += seconds
            for order_id, side, qty, price in orders:
                if side is Side.BUY and not getattr(stream, "buy_sent", False):
                    stream.buy_sent = True
                    stream.callback((order_id, TradeEvent("yes-token", Side.BUY, price, qty,
                        "buy-only", TradeState.MATCHED, clock.value, order_id)))
                    gw.position_snapshot = {"yes-token": (qty, price)}
        clock.sleep = advancing_sleep

    assert not await run_fake(meta, clock, gw, before_submit=deliver)
    output = capsys.readouterr().out
    assert "ROUND TRIP VERIFICATION INCOMPLETE" in output
    assert "MAKER ROUND TRIP PASS" not in output
    assert [order[1] for order in orders] == [Side.BUY, Side.SELL]


@pytest.mark.asyncio
@pytest.mark.parametrize("positions,expected,terminal_action", [
    ([0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 0.0], True, None),
    ([0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0], False, None),
    ([0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 0.0], False, "failed"),
    ([0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 0.0], False, "quarantine"),
    ([0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 0.0], False, "unresolved"),
])
async def test_final_position_convergence_grace(meta, capsys, positions, expected,
                                                terminal_action):
    clock = Clock(640)
    gw = FakeGateway()
    gw.position_reads = 0
    gw.position_sequence = positions
    async def read_positions():
        index = min(gw.position_reads, len(gw.position_sequence)-1)
        gw.position_reads += 1
        value = gw.position_sequence[index]
        if value == 0 and gw.position_reads > 1 and terminal_action:
            if terminal_action == "failed":
                from polymaker.state.tracker import TradeEvent
                from polymaker.domain import TradeState
                gw.processor.on_trade(TradeEvent("yes-token", Side.SELL, .11, 5,
                    "trade-conv-2", TradeState.FAILED, clock.value, "conv-2"), "0xcond")
            elif terminal_action == "quarantine":
                gw.processor.quarantine_order("conv-2", "review race", notify=False)
            else:
                gw.processor.quarantined_fill_keys.add(("trade-conv-2", "conv-2"))
        return {"yes-token": (value, .17)} if value else {}
    gw.positions = read_positions
    original_place = gw.place
    orders = []
    async def place(quotes, *args, **kwargs):
        result = await original_place(quotes, *args, **kwargs)
        if result:
            result[0].order_id = f"conv-{len(orders)+1}"
            orders.append((result[0].order_id, quotes[0].side, quotes[0].size, quotes[0].price))
        return result
    gw.place = place
    def deliver(stream):
        from polymaker.state.tracker import TradeEvent
        from polymaker.domain import TradeState
        async def advancing_sleep(seconds):
            clock.value += seconds
            for order_id, side, qty, price in orders:
                marker = f"sent_{order_id}"
                if getattr(stream, marker, False):
                    continue
                setattr(stream, marker, True)
                stream.callback((order_id, TradeEvent("yes-token", side, price, qty,
                    f"trade-{order_id}", TradeState.MATCHED, clock.value, order_id)))
        clock.sleep = advancing_sleep
    assert await run_fake(meta, clock, gw, before_submit=deliver) is expected
    output = capsys.readouterr().out
    if expected:
        assert "filltest_position_lag" in output
        assert "filltest_position_converged" in output
        assert "MAKER ROUND TRIP PASS" in output
    else:
        if terminal_action is None:
            assert "filltest_position_convergence_timeout" in output
            assert "position_convergence_timeout" in output
        assert "MAKER ROUND TRIP PASS" not in output


def _trade_message(order_id, amount=2, trade_id="trade-1", side="SELL"):
    import json
    return json.dumps({"event_type": "trade", "status": "MATCHED", "id": trade_id,
        "asset_id": "yes-token", "maker_orders": [{"order_id": order_id,
        "asset_id": "yes-token", "side": side, "matched_amount": str(amount),
        "price": "0.11", "maker_address": "wallet"}]})


@pytest.mark.asyncio
async def test_immediate_full_open_fill_verifies_position_before_closing(meta, capsys):
    clock = Clock(640)
    timeline = []
    delivered = []
    submissions = []
    stream_ref = {}

    class RecordingGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.position_reads = 0
            self.max_live = 0
            self.close_posted = False

        async def place(self, quotes, meta, *, submit_guard, force_post_only,
                        strict_placement=False):
            quote = quotes[0]
            assert submit_guard() and force_post_only and strict_placement
            self.placements += 1
            submissions.append((quote.side, quote.token_id, quote.size, force_post_only))
            if quote.side is Side.BUY:
                oid = "BUY123"
                # The exchange fill reaches User WS before this POST returns.
                stream_ref["stream"]._handle(_trade_message(
                    oid, amount=5, trade_id="TRADE1", side="BUY"))
            else:
                timeline.append("sell_post")
                assert "position_confirmed" in timeline
                self.close_posted = True
                oid = "SELL123"
                stream_ref["stream"]._handle(_trade_message(
                    oid, amount=quote.size, trade_id="SELLTRADE1", side="SELL"))
            self.live = [OpenOrder(oid, quote.token_id, quote.side, quote.price,
                                   quote.size, OrderState.LIVE)]
            self.max_live = max(self.max_live, len(self.live))
            return self.live.copy()

        async def cancel(self, ids):
            timeline.append(("cancel", ids[0]))
            self.cancels += 1
            self.live = []
            return True

        async def open_orders(self):
            self.open_reads += 1
            timeline.append(("open_orders", tuple(o.order_id for o in self.live)))
            return self.live.copy()

        async def positions(self):
            self.position_reads += 1
            timeline.append("positions_read")
            if self.position_reads == 1:  # clean baseline before the BUY
                return {}
            if any(o.order_id == "BUY123" for o in self.live):
                pytest.fail("positions checked before opening order cleanup")
            if self.position_reads == 2:  # authoritative opening confirmation
                timeline.append("position_confirmed")
                return {"yes-token": (5.0, .17)}
            return {"yes-token": (0.0, 0.0) if self.close_posted else (5.0, .17)}

    gw = RecordingGateway()
    raw_buy = _trade_message("BUY123", amount=5, trade_id="TRADE1", side="BUY")
    replayed = False

    from polymaker.userstream.client import UserStream

    class TestCaptureStream(UserStream):
        async def run(self):
            await asyncio.sleep(0)
            self.connected = True
            self._on_connection(True)

    async def resolver(_cfg, _slug):
        return meta

    def stream_factory(*args, **kwargs):
        gw.processor = args[2]
        stream = TestCaptureStream(*args, **kwargs)
        stream_ref["stream"] = stream
        return stream

    async def sleep(seconds):
        nonlocal replayed
        clock.value += seconds
        if not replayed:
            replayed = True
            # Same trade arrives again after opening_order_id registration.
            stream_ref["stream"]._handle(raw_buy)
        await asyncio.sleep(0)

    result = await run_filltest(
        config(), now=clock.now, sleep=sleep, resolver=resolver,
        gateway_factory=lambda _: gw,
        market_factory=lambda **kwargs: FakeMarket(**kwargs),
        stream_factory=stream_factory,
    )

    assert result is True
    assert gw.placements == 2  # exactly one BUY, then one SELL
    assert sum(side is Side.BUY for side, *_ in submissions) == 1
    assert submissions[0][0] is Side.BUY and submissions[0][2] == 5
    assert submissions[1][0] is Side.SELL
    assert submissions[1][1] == submissions[0][1] == "yes-token"
    assert 0 < submissions[1][2] <= 5
    assert submissions[1][3] is True
    assert gw.max_live == 1
    assert gw.position_reads >= 3
    cleanup_index = timeline.index(("cancel", "BUY123"))
    verify_index = timeline.index(("open_orders", ()), cleanup_index)
    assert cleanup_index < verify_index < timeline.index("position_confirmed") \
        < timeline.index("sell_post")
    assert timeline.count("position_confirmed") == 1
    output = capsys.readouterr().out
    assert output.count("filltest_open_fill") == 1
    assert "matched_amount=5.0" in output
    assert "filltest_close_fill" in output


def test_shared_pending_placement_buffers_until_exact_order_registration():
    from polymaker.domain import TradeState
    from polymaker.state.tracker import TradeEvent

    store = StateStore(":memory:")
    try:
        captured = []
        processor = UserEventProcessor(store, on_maker_fill=captured.append)
        processor.arm_pending("yes-token", Side.BUY)
        foreign = TradeEvent("yes-token", Side.BUY, .11, 2, "t", TradeState.MATCHED, 1, "foreign")
        ours = TradeEvent("yes-token", Side.BUY, .11, 2, "t", TradeState.MATCHED, 1, "our")
        processor.on_trade(foreign, "condition")
        processor.on_trade(ours, "condition")
        assert not captured
        processor.register_order("our", "condition", "yes-token", Side.BUY, 5, .11, "filltest")
        assert captured == [ours]
        processor.on_trade(ours, "condition")
        assert captured == [ours]
        assert store.position("yes-token").size == 2
    finally:
        store.close()


def test_same_trade_id_distinct_maker_orders_apply_independently():
    from polymaker.domain import TradeState
    from polymaker.state.tracker import TradeEvent

    store = StateStore(":memory:")
    try:
        processor = UserEventProcessor(store)
        for oid in ("a", "b"):
            processor.register_order(oid, "condition", "yes-token", Side.BUY, 5, .11)
            ev = TradeEvent("yes-token", Side.BUY, .11, 2, "same-trade", TradeState.MATCHED, 1, oid)
            processor.on_trade(ev, "condition")
        assert store.position("yes-token").size == 4
    finally:
        store.close()


@pytest.mark.asyncio
async def test_fillgap_uses_shared_rest_reconciliation_before_filltest_progresses(meta, monkeypatch):
    from polymaker import filltest as filltest_module
    from polymaker.domain import Side
    from polymaker.state.tracker import OrderEvent

    clock = Clock(640)
    stream_ref = {}
    processors = []
    original_processor = filltest_module.UserEventProcessor
    class CapturingProcessor(original_processor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            processors.append(self)
    monkeypatch.setattr(filltest_module, "UserEventProcessor", CapturingProcessor)

    class RecoveringGateway(FakeGateway):
        async def positions(self):
            return getattr(self, "position_snapshot", {})
        async def get_order(self, order_id):
            return {"id": order_id, "asset_id": "yes-token", "side": "BUY",
                "price": ".17", "original_size": "5", "size_matched": "5"}
        async def get_trades(self, **_kwargs):
            stream_ref["stream"].connected = False
            return [{"id": "recovered", "status": "MATCHED", "maker_orders": [{
                "order_id": "ours", "asset_id": "yes-token", "side": "BUY",
                "matched_amount": "5", "price": ".17"}]}]

    gw = RecoveringGateway()
    sent_gap = False
    def send_gap(stream):
        nonlocal sent_gap
        stream_ref["stream"] = stream
        async def advancing_sleep(seconds):
            nonlocal sent_gap
            clock.value += seconds
            if gw.placements and not sent_gap:
                sent_gap = True
                gw.position_snapshot = {"yes-token": (5.0, .17)}
                stream.order_callback(OrderEvent("ours", "yes-token", Side.BUY, .17, 0,
                    original_size=5, size_matched=5, status="UPDATE"))
            await asyncio.sleep(0)
        clock.sleep = advancing_sleep

    assert not await run_fake(meta, clock, gw, before_submit=send_gap)
    assert sent_gap
    assert processors[0].tracked_trade_qty("ours") == 5
    assert processors[0].economic_fills_for_order("ours")[0].trade_id == "recovered"
    assert "ours" not in processors[0].quarantined_orders


@pytest.mark.asyncio
async def test_fillgap_with_unresolved_rest_reconciliation_aborts_filltest(meta):
    from polymaker.domain import Side
    from polymaker.state.tracker import OrderEvent
    clock = Clock(640)
    gw = FakeGateway()
    sent = False
    def send_gap(stream):
        nonlocal sent
        async def advancing_sleep(seconds):
            nonlocal sent
            clock.value += seconds
            if gw.placements and not sent:
                sent = True
                stream.order_callback(OrderEvent("ours", "yes-token", Side.BUY, .17, 0,
                    original_size=5, size_matched=5, status="UPDATE"))
            await asyncio.sleep(0)
        clock.sleep = advancing_sleep
    assert not await run_fake(meta, clock, gw, before_submit=send_gap)
    assert sent
    assert gw.placements == 1
    assert gw.cancels == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("active_state", [
    "running", "done", "no_task", "queued", "stalled_get_order", "stalled_get_trades",
])
async def test_active_lag_deadline_returns_to_cancel_then_final_reconciliation(
        meta, active_state, monkeypatch):
    import polymaker.filltest as filltest_module
    from polymaker.domain import TradeState
    from polymaker.state.tracker import OrderEvent, TradeEvent

    clock = Clock(640)
    timeline = []
    queued_gate = asyncio.Event()
    if active_state == "queued":
        original_reconcile = filltest_module.reconcile_owned_order

        async def delayed_queued_reconcile(order_id, gateway, processor, **kwargs):
            deadline = kwargs.get("deadline")
            if deadline is not None:
                await queued_gate.wait()
                await asyncio.sleep(max(0.0, deadline-asyncio.get_running_loop().time()) + .01)
            return await original_reconcile(order_id, gateway, processor, **kwargs)

        monkeypatch.setattr(filltest_module, "reconcile_owned_order", delayed_queued_reconcile)

    class DeadlineGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.order_reads = {}
            self.position_size = 0
            self.active_state = active_state

        async def place(self, quotes, meta, *, submit_guard, force_post_only,
                        strict_placement=False):
            quote = quotes[0]
            if not submit_guard(): return []
            oid = "buy" if quote.side is Side.BUY else "sell"
            self.placements += 1
            order = OpenOrder(oid, quote.token_id, quote.side, quote.price,
                              quote.size, OrderState.LIVE)
            self.live = [order]
            self.accepted[oid] = order
            ev = TradeEvent(quote.token_id, quote.side, quote.price, quote.size,
                f"trade-{oid}", TradeState.MATCHED, clock.now(), oid)
            self.processor.on_trade(ev, "0xcond")
            if quote.side is Side.BUY:
                self.position_size = quote.size
            else:
                self.position_size = 0
                # Start ordinary cutoff cases near T+270; queued scheduling
                # deliberately queues recovery before cutoff and starts its
                # reconciliation body after the monotonic deadline.
                clock.value = 870 - (.01 if self.active_state == "queued" else .3)
                original_sleep = clock.sleep
                if self.active_state == "done":
                    async def delayed_scheduler(seconds):
                        clock.value += seconds
                        clock.sleep = original_sleep
                        await asyncio.sleep(.35)  # recovery finishes before runner wakes
                    clock.sleep = delayed_scheduler
                elif self.active_state == "no_task":
                    async def cutoff_before_schedule(_seconds):
                        clock.value = 870
                        clock.sleep = original_sleep
                        await asyncio.sleep(0)  # order event arrives at cutoff
                    clock.sleep = cutoff_before_schedule
                elif self.active_state == "queued":
                    async def cutoff_after_queue(_seconds):
                        await asyncio.sleep(0)  # order callback queues recovery
                        clock.value = 870
                        queued_gate.set()
                        clock.sleep = original_sleep
                    clock.sleep = cutoff_after_queue
                asyncio.get_running_loop().call_soon(lambda: self.processor.on_order(
                    OrderEvent(oid, quote.token_id, quote.side, quote.price, 0,
                        original_size=quote.size, size_matched=0, status="UPDATE"),
                    "0xcond"))
            return [order]

        async def get_order(self, order_id):
            self.order_reads[order_id] = self.order_reads.get(order_id, 0) + 1
            self.last_order_read = order_id
            timeline.append(("get_order", order_id, self.order_reads[order_id]))
            if (order_id == "sell" and self.order_reads[order_id] == 1
                    and self.active_state == "stalled_get_order"):
                try:
                    await asyncio.Event().wait()
                finally:
                    timeline.append(("rest_cancelled", "get_order"))
            order = self.accepted[order_id]
            tracked = self.processor.tracked_trade_qty(order_id)
            matched = (0 if order_id == "sell" and self.active_state != "no_task"
                       and self.order_reads[order_id] <= 2 else tracked)
            return {"id": order_id, "asset_id": order.token_id,
                "side": order.side.value, "price": str(order.price),
                "original_size": str(order.size), "size_matched": str(matched)}

        async def get_trades(self, *, market=None, asset_id=None):
            timeline.append(("get_trades", getattr(self, "last_order_read", None)))
            if (self.active_state == "stalled_get_trades"
                    and self.order_reads.get("sell") == 1):
                try:
                    await asyncio.Event().wait()
                finally:
                    timeline.append(("rest_cancelled", "get_trades"))
            rows = []
            for oid in self.accepted:
                for ev in self.processor.economic_fills_for_order(oid):
                    rows.append({"id": ev.trade_id, "status": "MATCHED", "market": "0xcond",
                        "maker_orders": [{"order_id": oid, "asset_id": ev.token_id,
                            "side": ev.our_side.value, "matched_amount": str(ev.size),
                            "price": str(ev.price)}]})
            return rows

        async def cancel(self, ids):
            timeline.append(("cancel", ids[0]))
            return await super().cancel(ids)

        async def open_orders(self):
            timeline.append(("open_orders",))
            return await super().open_orders()

        async def positions(self):
            return {"yes-token": (self.position_size, .17)} if self.position_size else {}

    gw = DeadlineGateway()
    assert await run_fake(meta, clock, gw)
    cancel_sell = timeline.index(("cancel", "sell"))
    open_after_cancel = timeline.index(("open_orders",), cancel_sell)
    sell_reads = [i for i, event in enumerate(timeline)
                  if event[:2] == ("get_order", "sell")]
    if active_state == "no_task":
        assert cancel_sell < open_after_cancel < sell_reads[0]
        assert len(sell_reads) == 1  # first truth read is final, after cleanup
    elif active_state == "queued":
        assert cancel_sell < open_after_cancel < sell_reads[0] < sell_reads[1] < sell_reads[2]
        assert not any(event[:2] in {("get_order", "sell"), ("get_trades", "sell")}
                       for event in timeline[:cancel_sell])
        assert len(sell_reads) == 3  # final grace lag, retry lag, convergence
    elif active_state in {"stalled_get_order", "stalled_get_trades"}:
        cancelled_read = ("get_order" if active_state == "stalled_get_order"
                          else "get_trades")
        cancel_read_index = timeline.index(("rest_cancelled", cancelled_read))
        assert timeline.index(("get_order", "sell", 1)) < cancel_read_index < cancel_sell
        assert len(sell_reads) == 3  # cancelled active attempt, then final lag and convergence
        assert sell_reads[0] < cancel_read_index < cancel_sell < open_after_cancel
        assert sell_reads[1] > open_after_cancel
    else:
        assert len(sell_reads) == 3  # active lag, final lag, final convergence
        assert sell_reads[0] < cancel_sell < open_after_cancel < sell_reads[1] < sell_reads[2]
        # Exactly one active fill-truth read happened before cancellation.
        assert len([i for i in sell_reads if i < cancel_sell]) == 1
    assert gw.order_reads["sell"] == len(sell_reads)
    assert gw.processor.tracked_trade_qty("sell") == 5
    assert "sell" not in gw.processor.quarantined_orders
