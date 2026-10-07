"""Tests for the rate budgeter and the paper-mode gateway."""

from __future__ import annotations

import asyncio
import time

import pytest

from polymaker.config import Config
from polymaker.domain import Quote, Side
from polymaker.execution.gateway import (
    BatchPlacementIncomplete,
    ExecutionGateway,
    GatewayReadError,
    PlacementUnknown,
    _tick_str,
)
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


def rdp_pair_quotes(meta, *, yes_size=5, no_size=5):
    # Reverse token order to prove result attribution follows request position.
    return [Quote(meta.no.token_id, Side.BUY, .48, no_size),
            Quote(meta.yes.token_id, Side.BUY, .49, yes_size)]


def placement_row(success, canonical_id=None, **extra):
    row = {"success": success}
    if canonical_id is not None:
        row["orderID"] = canonical_id
    row.update(extra)
    return row


class FakeBatchClient:
    def __init__(self, response=None, *, post_error=None, on_create=None):
        self.response = response
        self.post_error = post_error
        self.on_create = on_create
        self.created = []
        self.posts = []
        self.cancels = []

    def create_order(self, args, **kwargs):
        self.created.append((args, kwargs))
        if self.on_create is not None:
            self.on_create()
        return {"signed": len(self.created)}

    def post_orders(self, args, *, post_only):
        self.posts.append((args, post_only))
        if self.post_error is not None:
            raise self.post_error
        return self.response

    def cancel_orders(self, order_ids):
        self.cancels.append(tuple(order_ids))


async def _inline_io(fn, *args):
    return fn(*args)


async def place_pair(gw, meta, quotes=None):
    return await gw.place(quotes if quotes is not None else rdp_pair_quotes(meta), meta,
        force_post_only=True, strict_placement=True, allow_two_order_rdp_pair=True)


@pytest.mark.asyncio
async def test_opt_in_pair_accepts_two_positional_ids_and_forces_post_only_gtc(meta):
    cfg = Config()
    cfg.execution.post_only = False
    gw = ExecutionGateway(cfg, paper=False)
    client = FakeBatchClient([placement_row(True, "no-order", order_id="no-order"),
                              placement_row(True, "yes-order")])
    gw._client, gw._io = client, _inline_io
    quotes = rdp_pair_quotes(meta)
    placed = await place_pair(gw, meta, quotes)
    assert [(o.order_id, o.token_id, o.price, o.size) for o in placed] == [
        ("no-order", meta.no.token_id, .48, 5),
        ("yes-order", meta.yes.token_id, .49, 5),
    ]
    assert len(client.created) == 2 and len(client.posts) == 1
    args, post_only = client.posts[0]
    assert post_only is True
    assert len(args) == 2 and all(item.orderType == "GTC" for item in args)


@pytest.mark.asyncio
async def test_opt_in_pair_two_explicit_rejections_returns_empty(meta):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient([placement_row(False), placement_row(False)])
    gw._client, gw._io = client, _inline_io
    assert await place_pair(gw, meta) == []
    assert len(client.posts) == 1 and not client.cancels


@pytest.mark.asyncio
@pytest.mark.parametrize(("accepted_index", "rejected_index"), [(0, 1), (1, 0)])
async def test_opt_in_pair_partial_acceptance_raises_with_exact_ack_evidence(
    meta, accepted_index, rejected_index,
):
    rows = [placement_row(False), placement_row(False)]
    rows[accepted_index] = placement_row(True, "accepted-order")
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient(rows)
    gw._client, gw._io = client, _inline_io
    quotes = rdp_pair_quotes(meta)
    with pytest.raises(BatchPlacementIncomplete) as caught:
        await place_pair(gw, meta, quotes)
    error = caught.value
    assert error.reason == "PARTIAL_ACCEPTANCE"
    assert error.observed_order_ids == ("accepted-order",)
    assert len(error.acknowledged_orders) == 1
    order = error.acknowledged_orders[0]
    assert (order.token_id, order.price, order.size) == (
        quotes[accepted_index].token_id, quotes[accepted_index].price,
        quotes[accepted_index].size)
    assert rejected_index != accepted_index and len(client.posts) == 1
    assert client.cancels == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("accepted_index", "unknown_index"), [(0, 1), (1, 0)])
async def test_opt_in_pair_accepted_plus_unknown_preserves_independent_ack(
    meta, accepted_index, unknown_index,
):
    rows = [None, None]
    rows[accepted_index] = placement_row(True, "accepted")
    rows[unknown_index] = placement_row(None, "unknown")
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient(rows)
    gw._client, gw._io = client, _inline_io
    quotes = rdp_pair_quotes(meta)
    with pytest.raises(PlacementUnknown) as caught:
        await place_pair(gw, meta, quotes)
    error = caught.value
    assert error.reason == "UNRESOLVED_BATCH_RESULT"
    assert error.observed_order_ids == tuple(
        rows[index]["orderID"] for index in range(2))
    assert len(error.acknowledged_orders) == 1
    assert error.acknowledged_orders[0].token_id == quotes[accepted_index].token_id
    assert error.acknowledged_orders[0].order_id == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    [placement_row(True, "a"), placement_row(True, "a")],
    [placement_row(True, "a", order_id="different"), placement_row(False)],
    [placement_row(True, "   "), placement_row(False)],
    [placement_row(False, "rejected-but-id"), placement_row(False)],
    [placement_row(None, "observed"), placement_row(False)],
    [placement_row(1, "observed"), placement_row(False)],
    [placement_row(True), placement_row(False)],
    [placement_row(True, order_id="alias-only"), placement_row(False)],
    [placement_row(True, "a"), "malformed-row"],
    {"success": True, "orderID": "top-level"},
    [placement_row(True, "short")],
    [placement_row(True, "one"), placement_row(True, "two"), placement_row(False)],
    "malformed-envelope",
])
async def test_opt_in_pair_malformed_or_ambiguous_response_fails_closed(meta, response):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient(response)
    gw._client, gw._io = client, _inline_io
    with pytest.raises(PlacementUnknown) as caught:
        await place_pair(gw, meta)
    assert len(client.posts) == 1 and client.cancels == []
    if isinstance(response, list) and len(response) != 2:
        assert caught.value.acknowledged_orders == ()
    assert caught.value.observed_order_ids == tuple(dict.fromkeys(
        order_id for row in (response if isinstance(response, list) else [response])
        if isinstance(row, dict)
        for key in ("orderID", "order_id", "orderId", "id")
        if isinstance((order_id := row.get(key)), str) and order_id.strip()
    ))


@pytest.mark.asyncio
async def test_opt_in_ambiguous_duplicate_receipt_does_not_attribute_conflicting_id(meta):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient([
        placement_row(True, "same"),
        placement_row(True, "same", order_id="other"),
    ])
    gw._client, gw._io = client, _inline_io
    with pytest.raises(PlacementUnknown) as caught:
        await place_pair(gw, meta)
    assert caught.value.acknowledged_orders == ()
    assert caught.value.observed_order_ids == ("same", "other")


@pytest.mark.asyncio
async def test_opt_in_duplicate_accepted_ids_are_never_attributed(meta):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient([placement_row(True, "same"), placement_row(True, "same")])
    gw._client, gw._io = client, _inline_io
    with pytest.raises(PlacementUnknown) as caught:
        await place_pair(gw, meta)
    assert caught.value.reason == "DUPLICATE_ACCEPTED_ID"
    assert caught.value.acknowledged_orders == ()
    assert caught.value.observed_order_ids == ("same",)


@pytest.mark.asyncio
async def test_opt_in_pair_wrong_cardinality_keeps_observed_ids_without_attribution(meta):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient([placement_row(True, "one")])
    gw._client, gw._io = client, _inline_io
    with pytest.raises(PlacementUnknown) as caught:
        await place_pair(gw, meta)
    assert caught.value.reason == "BATCH_CARDINALITY_UNKNOWN"
    assert caught.value.acknowledged_orders == ()
    assert caught.value.observed_order_ids == ("one",)


@pytest.mark.asyncio
async def test_opt_in_post_exception_is_unknown_once_without_retry_or_cancel(meta):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient(post_error=TimeoutError("timed out"))
    gw._client, gw._io = client, _inline_io
    with pytest.raises(PlacementUnknown) as caught:
        await place_pair(gw, meta)
    assert caught.value.reason == "BATCH_POST_EXCEPTION"
    assert len(client.posts) == 1 and client.cancels == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("quotes", "force", "strict"), [
    ([], True, True),
    ([Quote("yes-token", Side.BUY, .49, 5)], True, True),
    ([Quote("yes-token", Side.BUY, .49, 5), Quote("yes-token", Side.BUY, .48, 5)], True, True),
    ([Quote("foreign", Side.BUY, .49, 5), Quote("no-token", Side.BUY, .48, 5)], True, True),
    ([Quote("yes-token", Side.SELL, .49, 5), Quote("no-token", Side.BUY, .48, 5)], True, True),
    (None, False, True),
    (None, True, False),
    ([Quote("yes-token", Side.BUY, .49, 5), Quote("no-token", Side.BUY, .48, 5)], False, True),
    ([Quote("yes-token", Side.BUY, .49, 5), Quote("no-token", Side.BUY, .48, 5)], True, False),
])
async def test_opt_in_pair_invalid_shape_or_flags_fail_before_signing(meta, quotes, force, strict):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient([placement_row(True, "a"), placement_row(True, "b")])
    gw._client, gw._io = client, _inline_io
    with pytest.raises(ValueError):
        await gw.place(quotes, meta, force_post_only=force,
            strict_placement=strict, allow_two_order_rdp_pair=True)
    assert client.created == [] and client.posts == []


@pytest.mark.asyncio
async def test_opt_in_pair_minimum_size_denial_skips_signing_and_post(meta):
    gw = ExecutionGateway(Config(), paper=False)
    client = FakeBatchClient([placement_row(True, "a"), placement_row(True, "b")])
    gw._client, gw._io = client, _inline_io
    result = await place_pair(gw, meta, rdp_pair_quotes(meta, yes_size=4.9))
    assert result == [] and client.created == [] and client.posts == []


@pytest.mark.asyncio
async def test_opt_in_pair_pre_and_final_submit_guards_block_without_post(meta):
    gw = ExecutionGateway(Config(), paper=False)
    state = {"checks": 0, "posts": 0, "recovery_pending": False}

    def signing_enters_recovery():
        state["recovery_pending"] = True

    client = FakeBatchClient([placement_row(True, "a"), placement_row(True, "b")],
        on_create=signing_enters_recovery)
    original_post = client.post_orders

    def count_post(args, *, post_only):
        state["posts"] += 1
        return original_post(args, post_only=post_only)

    client.post_orders = count_post
    gw._client, gw._io = client, _inline_io
    assert await gw.place(rdp_pair_quotes(meta), meta, submit_guard=lambda: False,
        force_post_only=True, strict_placement=True,
        allow_two_order_rdp_pair=True) == []
    assert client.created == [] and state["posts"] == 0

    # First guard check admits intent; final check after both signatures rejects.
    def guarded_sign():
        state["checks"] += 1
        return not state["recovery_pending"]

    assert await gw.place(rdp_pair_quotes(meta), meta, submit_guard=guarded_sign,
        force_post_only=True, strict_placement=True,
        allow_two_order_rdp_pair=True) == []
    assert state["checks"] == 2 and len(client.created) == 2 and state["posts"] == 0


@pytest.mark.asyncio
async def test_opt_in_pair_paper_mode_uses_no_client_and_keeps_default_behavior(meta):
    gw = ExecutionGateway(Config(), paper=True)
    placed = await place_pair(gw, meta)
    assert len(placed) == 2
    assert [order.token_id for order in placed] == [meta.no.token_id, meta.yes.token_id]
    assert all(order.order_id.startswith("paper-") for order in placed)


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
