"""M0 composition evidence, not an RDP runtime or a replacement Gateway.

Only the external SDK is faked. Gateway uses its actual executor, rate limiter,
parsers, strict reads and cancellation code. SQLite/Tracker/FillTruth are real.
The small Probe holds candidate *orchestrator* safety state in this test only.
Run via tests/rdp_sm_m0_offline.py to isolate existing regression suites too.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from copy import deepcopy

import pytest

from polymaker.config import Config, Secrets
from polymaker.domain import Quote, Side, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.execution.gateway import ExecutionGateway, GatewayReadError, PlacementUnknown
from polymaker.filltest import _unknown_cleanup
from polymaker.state.store import StateStore
from polymaker.state.tracker import UserEventProcessor
from polymaker.userstream.parse import normalize_order, normalize_trade

NETWORK_ATTEMPTS = []


def deny_network(*_args, **_kwargs):
    NETWORK_ATTEMPTS.append(True)
    raise AssertionError("M0 forbids network/DNS access")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Also protect direct pytest invocation, without inspecting credential values.
    monkeypatch.setattr(
        Secrets,
        "settings_customise_sources",
        classmethod(
            lambda cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings: (
                init_settings,
            )
        ),
    )
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, deny_network)
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, deny_network)
    before = len(NETWORK_ATTEMPTS)
    yield
    assert len(NETWORK_ATTEMPTS) == before, "network attempt was swallowed by tested code"


class FakeSDK:
    """Script venue replies and race ordering at the real Gateway SDK boundary."""

    def __init__(self):
        self.responses = [
            {"success": True, "orderID": "yes-1"},
            [{"success": True, "orderID": "no-1"}],
        ]
        self.rows = {}
        self.order_responses = {}
        self.trades = []
        self.posts = []
        self.cancels = []
        self.scoped_cancels = []
        self.trade_filters = []
        self.cancel_reply = {"canceled": [], "not_canceled": {}}
        self.cancel_started = threading.Event()
        self.cancel_release = threading.Event()
        self.block_cancel = False
        self.before_post = None

    def create_order(self, args, *, options):
        assert args.side == "BUY"
        assert options.tick_size == "0.01"
        if self.before_post:
            self.before_post()
        return args  # fake signing only

    def post_orders(self, args, *, post_only):
        assert len(args) == 1 and post_only is True
        assert str(args[0].orderType) == "GTC"
        self.posts.append(args[0].order)
        response = self.responses[len(self.posts) - 1]
        item = response[0] if isinstance(response, list) else response
        # UNKNOWN can really have reached the venue: retain a hidden order.
        oid = item.get("orderID") if isinstance(item, dict) else None
        rejected = isinstance(item, dict) and item.get("success") is False
        if not rejected:
            q = args[0].order
            self.rows[oid or "hidden-no"] = {
                "id": oid or "hidden-no",
                "asset_id": q.token_id,
                "side": q.side,
                "price": str(q.price),
                "original_size": str(q.size),
                "size_matched": "0",
                "status": "LIVE",
                "associate_trades": [],
            }
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    def cancel_orders(self, order_ids):
        self.cancels.append(tuple(order_ids))
        self.cancel_started.set()
        if self.block_cancel and not self.cancel_release.wait(5):
            raise RuntimeError("test cancel race synchronization timed out")
        if isinstance(self.cancel_reply, Exception):
            raise self.cancel_reply
        # Deliberately do NOT mutate the venue state on a cancellation ACK.
        return deepcopy(self.cancel_reply)

    def cancel_market_orders(self, params):
        self.scoped_cancels.append((params.market, params.asset_id))
        for row in self.rows.values():
            if row["asset_id"] == params.asset_id:
                row["status"] = "CANCELED"
        return {"canceled": [], "not_canceled": {}}

    def get_order(self, oid):
        if self.order_responses.get(oid):
            return deepcopy(self.order_responses[oid].pop(0))
        row = self.rows[oid]
        if isinstance(row, Exception):
            raise row
        return deepcopy(row)

    def get_trades(self, params):
        self.trade_filters.append((params.market, params.asset_id))
        if isinstance(self.trades, Exception):
            raise self.trades
        return deepcopy(self.trades)

    def get_open_orders(self):
        return [deepcopy(row) for row in self.rows.values() if row["status"] == "LIVE"]


class Probe:
    """Minimal test-only Thin SM composition; no strategy/window scheduler."""

    def __init__(self, meta):
        self.meta = meta
        # model_construct avoids Secrets.__init__ entirely (which reads env itself).
        self.gw = ExecutionGateway(Config(secrets=Secrets.model_construct()), paper=False)
        self.sdk = FakeSDK()
        self.gw._client = self.sdk
        self.store = StateStore(":memory:")
        self.proc = UserEventProcessor(self.store)
        self.attempts = []
        self.known = set()
        self.registry = {}  # main-loop snapshot; worker submit_guard cannot read SQLite
        self.terminal = set()
        self.outcomes = {}
        self.fail_closed = False

    async def submit(self, token):
        if self.fail_closed:
            raise RuntimeError("fail-closed; submission prohibited")
        self.attempts.append("PENDING")  # record before awaiting the non-atomic POST
        self.proc.arm_pending(token, Side.BUY)
        try:
            orders = await self.gw.place(
                [Quote(token, Side.BUY, 0.2, 10)],
                self.meta,
                force_post_only=True,
                strict_placement=True,
            )
        except PlacementUnknown:
            self.attempts[-1] = "UNKNOWN"
            self.fail_closed = True
            # Preserve pending capture on UNKNOWN until explicit session teardown.
            raise
        if not orders:
            # Not called REJECTED: [] also means local abort/signing failure.
            self.attempts[-1] = "NO_ACK"
            self.proc.discard_pending()
            return []
        order = orders[0]
        if self.proc.owned_record(order.order_id) is not None:
            self.attempts[-1] = "IDENTITY_CONFLICT"
            self.fail_closed = True
            raise RuntimeError("duplicate identity; preserve original ownership")
        self.proc.register_order(
            order.order_id,
            self.meta.condition_id,
            order.token_id,
            order.side,
            order.size,
            order.price,
            "m0_probe",
        )
        self.store.upsert_order(order)
        self.known.add(order.order_id)
        self.registry[order.order_id] = self.proc.owned_record(order.order_id)
        self.attempts[-1] = "ACK"
        return orders

    async def verify(self, oid):
        # Clear prior evidence BEFORE a fresh read so read failure cannot reuse it.
        self.terminal.discard(oid)
        self.outcomes.pop(oid, None)
        outcome = await reconcile_owned_order(oid, self.gw, self.proc, retry_delays=())
        self.outcomes[oid] = outcome
        # Terminal evidence must be newer than the order read inside FillTruth.
        row = await self.gw.get_order(oid)
        event = normalize_order(row)
        assert event is not None
        # Never infer terminality from local disappearance or cancellation ACK.
        if event.status in {"CANCELED", "CANCELLED"} or (
            event.status == "MATCHED" and event.remaining_size == 0
        ):
            self.terminal.add(oid)
        self.proc.on_order(event, self.meta.condition_id)
        self.registry[oid] = self.proc.owned_record(oid)
        return outcome

    def pending(self):
        return [
            ev
            for key, ev in self.proc.fill_records.items()
            if key[1] in self.known
            and (
                ev.status is not TradeState.CONFIRMED
                or key not in self.proc.applied_fill_keys
                or key in self.proc.quarantined_fill_keys
            )
        ]

    def confirmed_qty(self, token):
        return sum(
            ev.size
            for ev in self.proc.economic_fills_for_orders(self.known)
            if ev.token_id == token and ev.status is TradeState.CONFIRMED
        )

    def safe_to_continue(self):
        return (
            bool(self.attempts)
            and not self.fail_closed
            and all(a in {"ACK", "NO_ACK"} for a in self.attempts)
            and self.attempts.count("ACK") == len(self.known)
            and self.known == self.terminal
            and all(
                self.registry.get(oid) is not None
                and self.outcomes.get(oid) is ReconciliationOutcome.COMPLETE
                and not self.registry[oid]["unresolved"]
                and oid not in self.proc.quarantined_orders
                for oid in self.known
            )
            and not self.pending()
        )

    async def try_next_window(self):
        # Exercise the real Gateway's guard, including its final pre-POST check.
        return await self.gw.place(
            [Quote(self.meta.yes.token_id, Side.BUY, 0.2, 10)],
            self.meta,
            submit_guard=self.safe_to_continue,
            force_post_only=True,
            strict_placement=True,
        )

    def close(self):
        self.sdk.cancel_release.set()
        self.gw.close()
        self.store.close()


@pytest.fixture
def probe(meta):
    p = Probe(meta)
    yield p
    p.close()


def trade(p, oid, tid, size, status="CONFIRMED"):
    row = p.sdk.rows[oid]
    return {
        "id": tid,
        "status": status,
        "market": p.meta.condition_id,
        "maker_orders": [
            {
                "order_id": oid,
                "asset_id": row["asset_id"],
                "side": "BUY",
                "price": row["price"],
                "matched_amount": str(size),
            }
        ],
    }


def ws(p, raw):
    for ev in normalize_trade(raw, "", lambda _token: None):
        p.proc.on_trade(ev, p.meta.condition_id)


async def assert_blocked(p):
    count = len(p.sdk.posts)
    assert not p.safe_to_continue()
    assert await p.try_next_window() == []
    assert len(p.sdk.posts) == count


async def pair(p):
    await p.submit(p.meta.yes.token_id)
    await p.submit(p.meta.no.token_id)
    assert p.known == {"yes-1", "no-1"}
    assert len(p.store.owned_orders()) == 2
    assert set(p.store.orders) == p.known


async def test_s1_both_acknowledged_and_confirmed_closure(probe):
    p = probe
    await pair(p)
    await assert_blocked(p)
    for oid in sorted(p.known):
        assert await p.gw.cancel([oid])
        assert oid in p.store.orders
        await assert_blocked(p)
        p.sdk.rows[oid].update(status="CANCELED", size_matched="2", associate_trades=[oid])
        p.sdk.trades.append(trade(p, oid, oid, 2))
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert p.safe_to_continue() and not p.store.orders
    assert await p.gw.open_orders() == []
    assert p.confirmed_qty(p.meta.yes.token_id) == 2
    assert p.confirmed_qty(p.meta.no.token_id) == 2
    assert all(market == p.meta.condition_id for market, _ in p.sdk.trade_filters)


async def test_s2_first_ack_second_explicit_rejection(probe):
    p = probe
    p.sdk.responses[1] = {"success": False, "errorMsg": "post only would cross"}
    await p.submit(p.meta.yes.token_id)
    assert await p.submit(p.meta.no.token_id) == []
    assert p.attempts == ["ACK", "NO_ACK"] and not p.fail_closed
    assert p.known == {"yes-1"}
    await assert_blocked(p)
    assert await p.gw.cancel(["yes-1"])
    await assert_blocked(p)
    # First order filled despite the second order's rejection.
    p.sdk.rows["yes-1"].update(status="CANCELED", size_matched="3", associate_trades=["late"])
    p.sdk.trades = [trade(p, "yes-1", "late", 3)]
    assert await p.verify("yes-1") is ReconciliationOutcome.COMPLETE
    assert p.confirmed_qty(p.meta.yes.token_id) == 3
    assert p.safe_to_continue() and len(p.sdk.posts) == 2


@pytest.mark.parametrize("response", [TimeoutError("lost POST response"), {"success": True}, None])
async def test_s3_unknown_submission_stays_fail_closed_after_scoped_cancel(probe, response):
    p = probe
    p.sdk.responses[1] = response
    await p.submit(p.meta.yes.token_id)
    with pytest.raises(PlacementUnknown):
        await p.submit(p.meta.no.token_id)
    assert p.attempts == ["ACK", "UNKNOWN"] and p.fail_closed
    assert "hidden-no" in p.sdk.rows and "hidden-no" not in p.known
    assert (
        await _unknown_cleanup(
            p.gw, p.meta.condition_id, p.meta.no.token_id, RuntimeError("unknown submission")
        )
        is False
    )
    assert p.sdk.scoped_cancels == [(p.meta.condition_id, p.meta.no.token_id)]
    assert await p.gw.cancel(["yes-1"])
    p.sdk.rows["yes-1"]["status"] = "CANCELED"
    assert await p.verify("yes-1") is ReconciliationOutcome.COMPLETE
    assert await p.gw.open_orders() == []
    assert await p.gw.get_trades(market=p.meta.condition_id) == []
    assert p.proc._pending == {(p.meta.no.token_id, Side.BUY)}
    # Empty official snapshots cannot prove attribution or reconcile unknown fills.
    await assert_blocked(p)
    with pytest.raises(RuntimeError, match="fail-closed"):
        await p.submit(p.meta.no.token_id)
    assert len(p.sdk.posts) == 2


async def test_s4_partial_fill_cancel_race_reconciles_and_deduplicates(probe):
    p = probe
    await pair(p)
    first = trade(p, "yes-1", "t1", 2, "MATCHED")
    ws(p, first)
    p.sdk.rows["yes-1"].update(size_matched="2", associate_trades=["t1"])
    p.proc.on_order(normalize_order(p.sdk.rows["yes-1"]), p.meta.condition_id)
    assert p.store.orders["yes-1"].size == 8
    assert p.store.orders["yes-1"].notional == pytest.approx(1.6)
    assert p.confirmed_qty(p.meta.yes.token_id) == 0
    p.sdk.block_cancel = True
    task = asyncio.create_task(p.gw.cancel(["yes-1", "no-1"]))
    try:
        assert await asyncio.to_thread(p.sdk.cancel_started.wait, 5)
        # Main-loop WS event runs while the real executor's cancellation is waiting.
        ws(p, trade(p, "yes-1", "t2", 3, "MATCHED"))
        p.sdk.rows["yes-1"].update(size_matched="5", associate_trades=["t1", "t2"])
        p.proc.on_order(normalize_order(p.sdk.rows["yes-1"]), p.meta.condition_id)
        assert p.store.orders["yes-1"].size == 5
        assert p.store.orders["yes-1"].notional == 1
    finally:
        p.sdk.cancel_release.set()
        assert await task
    await assert_blocked(p)
    p.sdk.rows["yes-1"]["status"] = p.sdk.rows["no-1"]["status"] = "CANCELED"
    # A third fill was missed by WS; official history must recover it.
    p.sdk.rows["yes-1"].update(size_matched="6", associate_trades=["t1", "t2", "t3"])
    p.sdk.trades = [
        trade(p, "yes-1", "t1", 2),
        trade(p, "yes-1", "t2", 3),
        trade(p, "yes-1", "t3", 1),
    ]
    for oid in sorted(p.known):
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    ws(p, first)  # older MATCHED replay cannot downgrade CONFIRMED
    ws(p, p.sdk.trades[1])
    assert len(p.proc.applied_fill_keys) == 3
    assert p.store.position(p.meta.yes.token_id).size == 6
    assert p.confirmed_qty(p.meta.yes.token_id) == 6
    assert p.proc.tracked_trade_qty("yes-1") == 6
    assert not p.store.orders and not p.pending() and p.safe_to_continue()


@pytest.mark.parametrize("status", ["MATCHED", "MINED", "RETRYING"])
async def test_s5_terminal_order_with_unconfirmed_trade_blocks_continuation(probe, status):
    p = probe
    await pair(p)
    assert await p.gw.cancel(sorted(p.known))
    p.sdk.rows["yes-1"].update(status="CANCELED", size_matched="2", associate_trades=["pending"])
    p.sdk.rows["no-1"]["status"] = "CANCELED"
    # Seed MATCHED first, matching the lifecycle for MINED and RETRYING.
    ws(p, trade(p, "yes-1", "pending", 2, "MATCHED"))
    p.sdk.trades = [trade(p, "yes-1", "pending", 2, status)]
    for oid in sorted(p.known):
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert p.terminal == p.known  # ORDER_TERMINAL
    assert len(p.pending()) == 1  # not TRADE_CONFIRMED
    assert p.store.position(p.meta.yes.token_id).size == 2  # FL optimistic inventory
    assert p.confirmed_qty(p.meta.yes.token_id) == 0  # SM must use this projection
    await assert_blocked(p)  # raw COMPLETE is not SM RECONCILIATION_COMPLETE
    p.sdk.trades[0]["status"] = "CONFIRMED"
    assert await p.verify("yes-1") is ReconciliationOutcome.COMPLETE
    assert p.confirmed_qty(p.meta.yes.token_id) == 2
    assert not p.pending() and p.safe_to_continue()


async def test_s6_no_fill_cancel_and_verified_final_states(probe):
    p = probe
    await pair(p)
    assert await p.gw.cancel(sorted(p.known))
    # Even successful reads/checksums for LIVE orders do not open the barrier.
    for oid in sorted(p.known):
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    await assert_blocked(p)
    for oid in sorted(p.known):
        p.sdk.rows[oid]["status"] = "CANCELED"
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert p.safe_to_continue() and not p.store.orders
    assert await p.gw.open_orders() == []
    assert all(
        p.confirmed_qty(t) == 0 and p.store.position(t).size == 0
        for t in (p.meta.yes.token_id, p.meta.no.token_id)
    )


async def test_duplicate_ack_identity_preserves_original_registry(probe):
    p = probe
    p.sdk.responses[1] = {"success": True, "orderID": "yes-1"}
    await p.submit(p.meta.yes.token_id)
    with pytest.raises(RuntimeError, match="duplicate identity"):
        await p.submit(p.meta.no.token_id)
    assert len(p.store.owned_orders()) == 1
    assert p.proc.owned_record("yes-1")["token_id"] == p.meta.yes.token_id
    await assert_blocked(p)


@pytest.mark.parametrize(
    "reply", [{"not_canceled": {"yes-1": "still live"}}, TimeoutError("cancel reply lost")]
)
async def test_cancel_reply_never_proves_terminal_state(probe, reply):
    p = probe
    await pair(p)
    p.sdk.cancel_reply = reply
    assert await p.gw.cancel(sorted(p.known)) is (not isinstance(reply, Exception))
    for oid in sorted(p.known):
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert not p.terminal and p.store.orders
    await assert_blocked(p)


@pytest.mark.parametrize("failure", ["order", "trades", "gap", "failed", "status"])
async def test_read_failure_or_unresolved_evidence_cannot_release_barrier(probe, failure):
    p = probe
    await pair(p)
    for oid in sorted(p.known):
        p.sdk.rows[oid]["status"] = "CANCELED"
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert p.safe_to_continue()
    if failure == "order":
        p.sdk.rows["yes-1"] = TimeoutError("order read failed")
        with pytest.raises(GatewayReadError):
            await p.verify("yes-1")
    else:
        if failure == "trades":
            p.sdk.trades = TimeoutError("trade read failed")
        elif failure == "gap":
            p.sdk.rows["yes-1"].update(size_matched="1", associate_trades=["missing"])
        elif failure == "failed":
            p.sdk.rows["yes-1"].update(size_matched="1", associate_trades=["failed"])
            p.sdk.trades = [trade(p, "yes-1", "failed", 1, "FAILED")]
        else:
            p.sdk.rows["yes-1"]["status"] = "UNRECOGNIZED"
        result = await p.verify("yes-1")
        assert result is (
            ReconciliationOutcome.COMPLETE
            if failure == "status"
            else ReconciliationOutcome.UNRESOLVED
        )
    await assert_blocked(p)


async def test_guard_rechecks_barrier_after_sdk_signing(probe):
    p = probe
    await pair(p)
    for oid in sorted(p.known):
        p.sdk.rows[oid]["status"] = "CANCELED"
        await p.verify(oid)
    assert p.safe_to_continue()
    p.sdk.before_post = lambda: setattr(p, "fail_closed", True)
    assert await p.try_next_window() == []
    assert len(p.sdk.posts) == 2 and p.fail_closed


async def test_guard_allows_next_post_after_verified_closure(probe):
    p = probe
    await pair(p)
    assert await p.gw.cancel(sorted(p.known))
    for oid in sorted(p.known):
        p.sdk.rows[oid]["status"] = "CANCELED"
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert p.safe_to_continue()
    p.sdk.responses.append({"success": True, "orderID": "yes-2"})
    # The same guard must succeed both on the loop and in the real SDK thread.
    orders = await p.try_next_window()
    assert [o.order_id for o in orders] == ["yes-2"] and len(p.sdk.posts) == 3
    order = orders[0]
    p.proc.register_order(
        order.order_id,
        p.meta.condition_id,
        order.token_id,
        order.side,
        order.size,
        order.price,
        "m0_probe",
    )
    p.store.upsert_order(order)
    p.known.add(order.order_id)
    p.registry[order.order_id] = p.proc.owned_record(order.order_id)
    p.attempts.append("ACK")
    await assert_blocked(p)
    assert await p.gw.cancel([order.order_id])
    p.sdk.rows[order.order_id]["status"] = "CANCELED"
    assert await p.verify(order.order_id) is ReconciliationOutcome.COMPLETE
    assert p.safe_to_continue() and not p.store.orders


@pytest.mark.parametrize("status", ["MINED", "RETRYING", "CONFIRMED"])
async def test_owned_status_before_matched_cannot_disappear_at_close(probe, status):
    p = probe
    await pair(p)
    ws(p, trade(p, "yes-1", "out-of-order", 2, status))
    assert ("out-of-order", "yes-1") in p.proc.fill_records
    assert ("out-of-order", "yes-1") not in p.proc.applied_fill_keys
    # Status can arrive ahead of cumulative order reads and trade history.
    for oid in sorted(p.known):
        p.sdk.rows[oid]["status"] = "CANCELED"
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert len(p.pending()) == 1 and p.confirmed_qty(p.meta.yes.token_id) == 0
    await assert_blocked(p)
    p.sdk.rows["yes-1"].update(size_matched="2", associate_trades=["out-of-order"])
    p.sdk.trades = [trade(p, "yes-1", "out-of-order", 2, "CONFIRMED")]
    assert await p.verify("yes-1") is ReconciliationOutcome.COMPLETE
    assert p.confirmed_qty(p.meta.yes.token_id) == 2
    assert p.store.position(p.meta.yes.token_id).size == 2
    assert not p.pending() and p.safe_to_continue()


async def test_latest_official_order_read_controls_terminal_barrier(probe):
    p = probe
    await pair(p)
    for oid in sorted(p.known):
        p.sdk.rows[oid]["status"] = "CANCELED"
    canceled = deepcopy(p.sdk.rows["yes-1"])
    # FillTruth sees canceled; the later official read contradicts terminality.
    p.sdk.order_responses["yes-1"] = [canceled, {**canceled, "status": "LIVE"}]
    for oid in sorted(p.known):
        assert await p.verify(oid) is ReconciliationOutcome.COMPLETE
    assert "yes-1" not in p.terminal and "yes-1" in p.store.orders
    await assert_blocked(p)
    assert await p.verify("yes-1") is ReconciliationOutcome.COMPLETE
    assert p.safe_to_continue() and not p.store.orders
