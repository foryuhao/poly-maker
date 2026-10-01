"""Tests for the rate budgeter and the paper-mode gateway."""

from __future__ import annotations

import asyncio
import time

import pytest

from polymaker.config import Config
from polymaker.domain import Quote, Side
from polymaker.execution.gateway import ExecutionGateway, GatewayReadError, PlacementUnknown, _tick_str
from polymaker.execution.ratelimit import TokenBucket


def test_tick_str_formats():
    assert _tick_str(0.01) == "0.01"
    assert _tick_str(0.001) == "0.001"
    assert _tick_str(0.0025) == "0.0025"
    assert _tick_str(0.1) == "0.1"


async def test_token_bucket_limits_rate():
    bucket = TokenBucket(rate_per_s=100.0, burst=5.0)
    start = time.monotonic()
    # burst of 5 is instant; the next 5 must wait ~ (5/100)s = 50ms
    for _ in range(10):
        await bucket.acquire(1)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04  # had to wait for refill


async def test_token_bucket_pressure_rises_when_drained():
    bucket = TokenBucket(rate_per_s=10.0, burst=10.0)
    assert bucket.pressure == pytest.approx(0.0, abs=0.01)
    for _ in range(10):
        await bucket.acquire(1)
    assert bucket.pressure > 0.8


async def test_paper_gateway_places_and_cancels_without_wallet(meta):
    cfg = Config()  # defaults, no secrets
    gw = ExecutionGateway(cfg, paper=True)
    quotes = [
        Quote(meta.yes.token_id, Side.BUY, 0.49, 100),
        Quote(meta.no.token_id, Side.BUY, 0.48, 100),
    ]
    placed = await gw.place(quotes, meta)
    assert len(placed) == 2
    assert all(o.order_id.startswith("paper-") for o in placed)
    # cancel is a no-op in paper mode but must not raise
    await gw.cancel([o.order_id for o in placed])
    assert await gw.open_orders() == []


async def test_paper_gateway_heartbeat_and_cancel_all_noop():
    gw = ExecutionGateway(Config(), paper=True)
    assert await gw.heartbeat() is True  # paper: healthy no-op
    assert gw.heartbeat_failures == 0
    await gw.cancel_all()  # no client, must not raise


def test_gateway_requires_wallet_for_live_connect():
    from polymaker.config import Secrets

    # explicitly-empty secrets (don't read a real .env that may exist on disk)
    cfg = Config(secrets=Secrets(_env_file=None))
    gw = ExecutionGateway(cfg, paper=False)
    with pytest.raises(RuntimeError, match="no wallet"):
        asyncio.run(gw.connect())


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [[{"success": True, "orderID": "order-1"}]])
async def test_gateway_confirms_valid_placement_response(meta, response):
    gw = ExecutionGateway(Config(), paper=False)

    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs): return response

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    placed = await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                            force_post_only=True, strict_placement=True)
    assert [o.order_id for o in placed] == ["order-1"]


@pytest.mark.asyncio
async def test_gateway_confirms_exact_live_orderid_response(meta):
    gw = ExecutionGateway(Config(), paper=False)
    response = [{
        "errorMsg": "",
        "makingAmount": "...",
        "orderID": "0xabc123",
        "status": "...",
        "success": True,
        "takingAmount": "...",
    }]

    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs): return response

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    placed = await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                            force_post_only=True, strict_placement=True)
    assert len(placed) == 1
    assert placed[0].order_id == "0xabc123"


@pytest.mark.asyncio
@pytest.mark.parametrize("success,order_id", [
    (False, "0xabc123"),
    (True, None),
    (True, ""),
    (True, "   "),
])
async def test_gateway_handles_exact_live_orderid_response_variants(meta, success, order_id):
    gw = ExecutionGateway(Config(), paper=False)
    response = [{
        "errorMsg": "",
        "makingAmount": "...",
        "orderID": order_id,
        "status": "...",
        "success": success,
        "takingAmount": "...",
    }]

    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs): return response

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    if success is False:
        assert await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                              force_post_only=True, strict_placement=True) == []
    else:
        with pytest.raises(PlacementUnknown):
            await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                            force_post_only=True, strict_placement=True)


@pytest.mark.asyncio
async def test_gateway_accepts_mapping_placement_response(meta):
    gw = ExecutionGateway(Config(), paper=False)
    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs): return {"orderID": "0xABC", "success": True}
    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    placed = await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                            force_post_only=True, strict_placement=True)
    assert [o.order_id for o in placed] == ["0xABC"]


@pytest.mark.asyncio
async def test_gateway_accepts_singleton_live_placement_envelope(meta):
    gw = ExecutionGateway(Config(), paper=False)
    live_result = {"errorMsg": "", "makingAmount": "0", "orderID": "0xABC",
                   "status": "live", "success": True, "takingAmount": "0"}
    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs): return [live_result]
    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    placed = await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                            force_post_only=True, strict_placement=True)
    assert [o.order_id for o in placed] == ["0xABC"]


@pytest.mark.asyncio
async def test_gateway_conflicting_placement_id_aliases_unknown(meta):
    gw = ExecutionGateway(Config(), paper=False)
    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs):
            return {"orderID": "A", "orderId": "B", "success": True}
    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    with pytest.raises(PlacementUnknown):
        await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                       force_post_only=True, strict_placement=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    None,
    [],
    [
        {"errorMsg": "", "makingAmount": "...", "orderID": "0xabc123",
         "status": "...", "success": True, "takingAmount": "..."},
        {"errorMsg": "", "makingAmount": "...", "orderID": "0xdef456",
         "status": "...", "success": True, "takingAmount": "..."},
    ],
    [{"success": True}],
    ["malformed item"],
    "malformed",
])
async def test_gateway_malformed_post_response_is_unknown(meta, response):
    gw = ExecutionGateway(Config(), paper=False)

    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs): return response

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    with pytest.raises(PlacementUnknown):
        await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                        force_post_only=True, strict_placement=True)


@pytest.mark.asyncio
async def test_gateway_post_exception_is_unknown(meta):
    gw = ExecutionGateway(Config(), paper=False)

    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs): raise TimeoutError("response timed out")

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    with pytest.raises(PlacementUnknown):
        await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                        force_post_only=True, strict_placement=True)


@pytest.mark.asyncio
async def test_gateway_explicit_rejection_is_confirmed_no_order(meta):
    gw = ExecutionGateway(Config(), paper=False)

    class Client:
        def create_order(self, *_args, **_kwargs): return {}
        def post_orders(self, *_args, **_kwargs):
            return [{"success": False, "errorMsg": "post only would cross"}]

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    assert await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
                          force_post_only=True, strict_placement=True) == []


@pytest.mark.asyncio
async def test_gateway_final_submit_guard_blocks_post_when_fill_recovery_starts(meta):
    gw = ExecutionGateway(Config(), paper=False)
    state = {"fill_recovery_pending": set(), "posts": 0, "checks": 0}
    class Client:
        def create_order(self, *_args, **_kwargs):
            state["fill_recovery_pending"].add(meta.condition_id)
            return {"signed": True}
        def post_orders(self, *_args, **_kwargs):
            state["posts"] += 1
            return [{"success": True, "orderID": "ours"}]
    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    def guard():
        state["checks"] += 1
        return meta.condition_id not in state["fill_recovery_pending"]
    assert await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
        submit_guard=guard, force_post_only=True, strict_placement=True) == []
    assert state["checks"] == 2
    assert state["posts"] == 0


@pytest.mark.asyncio
async def test_fill_recovery_completion_invalidates_old_intent_but_allows_new_intent(meta):
    from types import SimpleNamespace
    from polymaker.engine import Engine

    eng = Engine.__new__(Engine)
    eng._running = True
    eng._market_connectivity_generation = 0
    eng._market_recovery_pending = False
    eng._fill_truth_generation = {meta.condition_id: 10}
    eng._fill_recovery_pending = set()
    eng.md = SimpleNamespace(connected=True)
    eng.user_proc = SimpleNamespace(has_quarantine_for_condition=lambda _cid: False)
    gw = ExecutionGateway(Config(), paper=False)
    state = {"posts": 0, "mutate": True}

    class Client:
        def create_order(self, *_args, **_kwargs):
            if state["mutate"]:
                # FillGap begins and successfully resolves while signing.
                eng._fill_recovery_pending.add(meta.condition_id)
                eng._invalidate_fill_truth(meta.condition_id)
                eng._fill_recovery_pending.discard(meta.condition_id)
                state["mutate"] = False
            return {"signed": True}
        def post_orders(self, *_args, **_kwargs):
            state["posts"] += 1
            return [{"success": True, "orderID": f"ours-{state['posts']}"}]

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    stale_guard = lambda: eng._can_submit_market(0, meta.condition_id, 10)
    assert await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
        submit_guard=stale_guard, force_post_only=True, strict_placement=True) == []
    assert state["posts"] == 0
    assert eng._fill_truth_generation[meta.condition_id] == 11
    assert meta.condition_id not in eng._fill_recovery_pending

    fresh_guard = lambda: eng._can_submit_market(0, meta.condition_id, 11)
    placed = await gw.place([Quote(meta.yes.token_id, Side.BUY, .18, 5)], meta,
        submit_guard=fresh_guard, force_post_only=True, strict_placement=True)
    assert len(placed) == 1
    assert state["posts"] == 1


@pytest.mark.asyncio
async def test_authoritative_get_order_rejects_missing_cumulative_quantity():
    gw = ExecutionGateway(Config(), paper=False)
    class Client:
        def get_order(self, _oid):
            return {"id": "ours", "asset_id": "token", "side": "BUY", "price": ".2",
                    "original_size": "5"}
    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    with pytest.raises(GatewayReadError):
        await gw.get_order("ours")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["original_size", "size_matched", "price"])
@pytest.mark.parametrize("value", [False, True])
async def test_open_order_numeric_evidence_rejects_boolean(field, value):
    gw = ExecutionGateway(Config(), paper=False)
    class Client:
        def get_open_orders(self):
            row = {"id": "ours", "asset_id": "token", "side": "BUY", "price": ".2",
                "original_size": "5", "size_matched": "0"}
            row[field] = value
            return [row]
    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    with pytest.raises(GatewayReadError):
        await gw.open_orders()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {}, {"data": []}, ["bad row"]])
async def test_authoritative_get_trades_rejects_malformed_top_level_shape(response):
    gw = ExecutionGateway(Config(), paper=False)
    class Client:
        def get_trades(self, _params): return response
    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    with pytest.raises(GatewayReadError):
        await gw.get_trades(market="condition")


@pytest.mark.asyncio
async def test_gateway_market_asset_cancel_uses_both_scope_fields():
    gw = ExecutionGateway(Config(), paper=False)
    captured = []

    class Client:
        def cancel_market_orders(self, payload): captured.append(payload)

    async def inline(fn): return fn()
    gw._client, gw._io = Client(), inline
    assert await gw.cancel_market_asset("condition-1", "token-1")
    assert captured[0].market == "condition-1"
    assert captured[0].asset_id == "token-1"
