"""Production SM/Gateway/Tracker/FillTruth/Journal integration, fake HTTP/time only."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from polymaker.config import Config, Secrets
from polymaker.domain import Side, TradeState
from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_sm_execution import RiskJournal, SMWindowExecutor, WindowQuote
from polymaker.state.tracker import TradeEvent
from polymaker.userstream.parse import normalize_order
from test_rdp_sm_m1 import ADDRESS, CID, DUMMY_KEY, SLUG, START, Venue


class ExecutionVenue(Venue):
    def __init__(self):
        super().__init__()
        self.mono = 0.0
        self.rows = {}
        self.trades = []
        self.posts = []
        self.post_requests = []
        self.cancels = []
        self.replies = []
        self.after_post = None
        self.on_cancel = None
        self.on_sleep = None
        self.on_order_read = None
        self.cancel_terminal = True
        self.post_delay = 0
        self.executor = None
        self.open_rows_override = None

    async def sleep(self, seconds):
        self.mono += seconds
        self.time += seconds
        if self.on_sleep:
            await self.on_sleep(self)
        await asyncio.sleep(0)

    def fill(self, oid, tid="trade-1", qty=2, status="CONFIRMED"):
        row = self.rows[oid]
        trade = {
            "id": tid,
            "status": status,
            "market": CID,
            "asset_id": "3",
            "side": "SELL",
            "timestamp": str(self.time),
            "maker_orders": [
                {
                    "order_id": oid,
                    "asset_id": row["asset_id"],
                    "side": "BUY",
                    "price": row["price"],
                    "matched_amount": str(qty),
                }
            ],
        }
        self.trades.append(trade)
        row["size_matched"] = str(float(row["size_matched"]) + qty)
        row["associate_trades"].append(tid)
        return trade

    def response(self, request):
        path = request.url.path
        if request.method == "POST":
            assert request.url.host == "clob.polymarket.com" and path == "/orders"
            body = json.loads(request.content)
            assert 1 <= len(body) <= 2
            self.post_requests.append(deepcopy(body))
            attempts = self.executor.gateway._client.batch_attempts
            assert len(attempts) == len(body)
            assert self.executor.committed() <= Decimal("20")
            lines = journal_records(self.executor.journal.path)
            replies = []
            for row, attempt in zip(body, attempts, strict=True):
                assert row["postOnly"] is True and row["orderType"] == "GTC"
                signed = row["order"]
                assert signed["side"] == "BUY"
                assert signed["signature"].startswith("0x") and signed["signer"].lower() == ADDRESS.lower()
                # Count orders separately from actual HTTP requests.
                self.posts.append([row])
                assert any(x["kind"] == "sm_placement_intent"
                           and x["data"]["payload"]["attempt_id"] == attempt.attempt_id for x in lines)
                reply = (self.replies.pop(0) if self.replies
                         else {"success": True, "orderID": f"o-{len(self.posts)}"})
                replies.append(reply)
                item = reply[0] if isinstance(reply, list) and reply else reply
                if not (isinstance(item, dict) and item.get("success") is False):
                    oid = item.get("orderID", "hidden") if isinstance(item, dict) else "hidden"
                    q = attempt.quote
                    self.rows[oid] = {
                        "id": oid, "asset_id": q.token_id, "market": CID, "side": "BUY",
                        "price": str(q.price), "original_size": str(q.size), "size_matched": "0",
                        "status": "LIVE", "associate_trades": [],
                    }
            self.mono += self.post_delay
            self.time += self.post_delay
            if self.after_post:
                self.after_post(self)
            for reply in replies:
                if isinstance(reply, Exception):
                    raise reply
            return httpx.Response(200, json=deepcopy(replies[0] if len(body) == 1 else replies), request=request)
        if request.method == "DELETE":
            assert request.url.host == "clob.polymarket.com" and path == "/orders"
            ids = json.loads(request.content)
            self.cancels.append(ids)
            if self.on_cancel:
                self.on_cancel(self)
            if self.cancel_terminal:
                for oid in ids:
                    self.rows[oid]["status"] = "CANCELED"
            return httpx.Response(200, json={"canceled": ids, "not_canceled": {}}, request=request)
        if path.startswith("/data/order/"):
            oid = path.split("/")[-1]
            if self.on_order_read:
                self.on_order_read(self, oid)
            self.calls.append((request.url.host, path, dict(request.url.params)))
            return httpx.Response(200, json=deepcopy(self.rows[oid]), request=request)
        if path == "/data/trades":
            self.calls.append((request.url.host, path, dict(request.url.params)))
            return httpx.Response(
                200, json={"data": deepcopy(self.trades), "next_cursor": "LTE="}, request=request
            )
        if path == "/data/orders":
            self.orders = (
                self.open_rows_override
                if self.open_rows_override is not None
                else [r for r in self.rows.values() if r["status"] == "LIVE"]
            )
        if path == "/book":
            self.books[request.url.params["token_id"]]["timestamp"] = str(self.time * 1000)
        return super().response(request)


@pytest.fixture
def env(monkeypatch, tmp_path):
    attempts = []

    def deny(*_args, **_kwargs):
        attempts.append(True)
        raise AssertionError("M2 offline: network or forbidden component call")

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, deny)
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, deny)
    for name in (
        "connect",
        "cancel_all",
        "cancel_asset",
        "cancel_market_asset",
        "market_order",
        "token_balance",
        "token_balances",
        "_token_balance_opt",
    ):
        monkeypatch.setattr(ExecutionGateway, name, deny)
    monkeypatch.chdir(tmp_path)
    venue = ExecutionVenue()
    transport = httpx.MockTransport(venue.response)
    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=transport, **kw))
    client = httpx.Client(transport=transport)
    monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
    cfg = Config(secrets=Secrets.model_construct(pk=DUMMY_KEY, browser_address=ADDRESS))
    cfg.wallet.signature_type = 0
    cfg.paths.journal_dir = str(tmp_path / "journal")
    yield venue, cfg
    if venue.executor:
        venue.executor.close()
    client.close()
    assert not attempts


async def open_executor(env):
    venue, cfg = env
    executor = await SMWindowExecutor.open(
        cfg, SLUG, "session-1", monotonic=lambda: venue.mono, sleep=venue.sleep
    )
    venue.executor = executor
    return venue, executor


def quotes(window="w1", size=5):
    return [
        WindowQuote(CID, "1", "YES", Side.BUY, 0.49, size, window),
        WindowQuote(CID, "2", "NO", Side.BUY, 0.48, size, window),
    ]


def journal_records(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


async def test_e01_e08_both_ack_no_fill_verified_close_and_next_window(env):
    v, executor = await open_executor(env)
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert len(v.posts) == 2 and result.known_order_ids == ["o-1", "o-2"]
    assert all(executor.processor.owned_record(oid) for oid in result.known_order_ids)
    assert all(v.rows[oid]["status"] == "CANCELED" for oid in result.known_order_ids)
    assert result.yes_inventory == result.no_inventory == result.capital_committed == 0
    assert result.fill_deadline_monotonic == result.t0_monotonic + 3
    assert json.loads(json.dumps(result.to_dict()))["close_barrier"] == "SAFE_TO_CONTINUE"
    next_result = await executor.execute_window("w2", quotes("w2"))
    assert next_result.close_barrier == "SAFE_TO_CONTINUE" and len(v.posts) == 4
    assert any(c[1] == "/data/trades" for c in v.calls)


async def test_e02_first_ack_second_explicit_postonly_reject_closes_fills(env):
    v, executor = await open_executor(env)
    v.replies = [{"success": True, "orderID": "o-1"}, {"success": False, "errorMsg": "post only"}]

    def cancel(venue):
        venue.fill("o-1", qty=2)

    v.on_cancel = cancel
    result = await executor.execute_window("w1", quotes())
    assert [p["outcome"] for p in result.placement_results] == ["ACK", "REJECTED"]
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert result.yes_inventory == 2 and result.no_inventory == 0
    assert result.capital_committed == pytest.approx(0.98)
    assert len(v.posts) == 2 and v.mono == 0  # immediate close, no fill-window wait


@pytest.mark.parametrize(
    "first,reply",
    [
        (True, httpx.ReadTimeout("offline timeout")),
        (False, {"success": True}),
        (False, httpx.ReadTimeout("timeout")),
    ],
)
async def test_e03_h03_unknown_retains_commitment_no_retry_or_next_window(env, first, reply):
    v, executor = await open_executor(env)
    v.replies = [reply] if first else [{"success": True, "orderID": "o-1"}, reply]
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED"
    assert result.placement_results[-1]["outcome"] == "UNKNOWN"
    assert result.capital_committed >= (2.45 if first else 2.4)
    count = len(v.posts)
    again = await executor.execute_window("w2", quotes("w2"))
    assert again.close_barrier == "BLOCKED" and len(v.posts) == count
    assert executor.committed() >= Decimal("2.45" if first else "2.4")
    if not first and not isinstance(reply, Exception):
        assert v.rows["o-1"]["status"] == "CANCELED"
    if isinstance(reply, Exception):
        assert result.known_order_ids == [] and result.capital_committed == 4.85
    records = journal_records(executor.journal.path)
    assert any(r["kind"] == "sm_placement_unknown" for r in records)
    assert any(r["kind"] == "sm_unknown_official_lookup" for r in records)


async def test_e04_partial_fill_cancel_race_and_duplicate_confirmations(env):
    v, executor = await open_executor(env)

    async def fill_during_window(venue):
        if not venue.trades:
            venue.fill("o-1", "early", 1)

    v.on_sleep = fill_during_window
    v.on_cancel = lambda venue: venue.fill("o-1", "during-cancel", 2)
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert result.yes_inventory == 3 and len(result.confirmed_fills) == 2
    assert result.capital_committed == pytest.approx(1.47)
    for _ in range(2):
        executor.processor.on_trade(
            TradeEvent(
                "1", Side.BUY, 0.49, 2, "during-cancel", TradeState.CONFIRMED, v.time, "o-1", CID
            ),
            CID,
        )
    assert sum(f.size for f in executor.confirmed()) == 3
    assert await executor.verify_close()
    assert executor.committed() == Decimal("1.47")


async def test_e04_cancel_ack_does_not_prove_terminal_or_release_capital(env):
    v, executor = await open_executor(env)
    v.cancel_terminal = False
    result = await executor.execute_window("w1", quotes())
    assert v.cancels and result.close_barrier == "BLOCKED"
    assert result.capital_committed == 4.85
    assert not executor.safe_to_continue
    await executor.execute_window("w2", quotes("w2"))
    assert len(v.posts) == 2


@pytest.mark.parametrize("status", ["MATCHED", "MINED"])
async def test_e05_pending_confirmation_cannot_be_inventory_or_complete(env, status):
    v, executor = await open_executor(env)
    v.on_cancel = lambda venue: venue.fill("o-1", qty=2, status=status)
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.yes_inventory == 0
    assert result.capital_committed == 2.45
    assert executor.processor.tracked_trade_qty("o-1") == 2
    assert executor.processor.fill_records[("trade-1", "o-1")].status.value == status
    count = len(v.posts)
    await executor.execute_window("w2", quotes("w2"))
    assert len(v.posts) == count


async def test_e06_h02_late_confirmed_invalidates_complete_and_requires_official_set(env):
    v, executor = await open_executor(env)
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE"
    executor.processor.on_trade(
        TradeEvent("1", Side.BUY, 0.49, 2, "late", TradeState.MATCHED, v.time, "o-1", CID), CID
    )
    executor.processor.on_trade(
        TradeEvent("1", Side.BUY, 0.49, 2, "late", TradeState.CONFIRMED, v.time, "o-1", CID), CID
    )
    assert not executor.safe_to_continue and executor.committed() == Decimal("2.45")
    assert sum(f.size for f in executor.confirmed()) == 2
    count = len(v.posts)
    blocked = await executor.execute_window("w2", quotes("w2"))
    assert blocked.close_barrier == "BLOCKED" and len(v.posts) == count
    assert not await executor.verify_close()  # old REST zero fill cannot prove new trade
    v.fill("o-1", "late", 2)
    assert await executor.verify_close()
    assert sum(f.size for f in executor.confirmed()) == 2
    assert executor.committed() == Decimal(".98")
    # Account inventory is now SM's own, not a fresh clean-zero requirement.
    v.positions = [{"token_id": "1", "current_size": 2, "avg_price": 0.49}]
    next_result = await executor.execute_window("w3", quotes("w3"))
    assert next_result.close_barrier == "SAFE_TO_CONTINUE", next_result.block_reason
    assert len(v.posts) == 4 and next_result.yes_inventory == 2


async def test_e07_h01_terminal_then_live_blocks_even_old_official_terminal(env):
    v, executor = await open_executor(env)
    assert (await executor.execute_window("w1", quotes())).close_barrier == "SAFE_TO_CONTINUE"
    live = normalize_order({**v.rows["o-1"], "status": "LIVE"})
    executor.processor.on_order(live, CID)
    assert not executor.safe_to_continue and executor.committed() == Decimal("2.45")
    assert not await executor.verify_close()  # no timestamp explanation: latch contradiction
    result = await executor.execute_window("w2", quotes("w2"))
    assert result.close_barrier == "BLOCKED" and len(v.posts) == 2
    assert result.block_reason == "TERMINAL_LIVE_CONTRADICTION"
    assert any(c[1] == "/data/order/o-1" for c in v.calls)


async def test_e09_twenty_dollar_cap_checked_before_any_submit(env):
    v, executor = await open_executor(env)
    result = await executor.execute_window("w1", quotes(size=40))
    assert (
        result.close_barrier == "BLOCKED" and result.block_reason == "TEST_CAPITAL_LIMIT_EXCEEDED"
    )
    assert not v.posts and executor.committed() == 0


async def test_e09_confirmed_cost_counts_in_later_window_cap(env):
    v, executor = await open_executor(env)
    v.on_cancel = lambda venue: venue.fill("o-1", qty=20)
    first = await executor.execute_window("w1", quotes(size=20))
    assert first.close_barrier == "SAFE_TO_CONTINUE" and first.capital_committed == 9.8
    v.on_cancel = None
    v.positions = [{"token_id": "1", "current_size": 20, "avg_price": 0.49}]
    second = await executor.execute_window("w2", quotes("w2", size=20))
    assert second.close_barrier == "BLOCKED" and len(v.posts) == 2
    assert executor.committed() >= Decimal("9.8")


@pytest.mark.parametrize("elapsed", [14, 180, 222, 239, 240, 260, 300])
async def test_e10_cutoff_has_no_new_posts(env, elapsed):
    v, executor = await open_executor(env)
    v.time = START + elapsed
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and not v.posts
    assert result.block_reason == "MARKET_SUBMISSION_CUTOFF"


async def test_batch_post_delay_does_not_reset_deadline_or_issue_another_request(env):
    v, executor = await open_executor(env)
    v.post_delay = 4
    result = await executor.execute_window("w1", quotes())
    assert len(v.posts) == 2 and len(v.post_requests) == 1 and result.close_barrier == "SAFE_TO_CONTINUE"
    assert result.t0_monotonic == 0 and result.fill_deadline_monotonic == 3
    assert v.cancels == [["o-1", "o-2"]]


@pytest.mark.parametrize(
    "change",
    [
        dict(price=0.495),
        dict(price=1),
        dict(price=0.51),
        dict(size=0),
        dict(size=4),
        dict(size=5.001),
        dict(token_id="01"),
        dict(side=Side.SELL),
        dict(market_id="unapproved"),
    ],
)
async def test_invalid_quote_never_submitted(env, change):
    v, executor = await open_executor(env)
    result = await executor.execute_window("w1", [replace(quotes()[0], **change)])
    assert result.close_barrier == "BLOCKED" and not v.posts


async def test_failed_journal_write_prevents_placement(env, monkeypatch):
    v, executor = await open_executor(env)
    original = os.fsync
    failing = True

    def fail(fd):
        nonlocal failing
        if failing:
            failing = False
            raise OSError("offline disk full")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fail)
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and not v.posts
    assert result.capital_committed >= 2.45 and result.block_reason == "JOURNAL_WRITE_FAILED"


async def test_account_identity_mismatch_prevents_submission(env):
    v, executor = await open_executor(env)
    executor.gateway._client.builder.funder = "0x" + "11" * 20
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and not v.posts
    assert result.block_reason == "ACCOUNT_IDENTITY_MISMATCH"


async def test_e10_abnormal_exit_durable_risk_blocks_restart(env):
    v, executor = await open_executor(env)

    async def abnormal(venue):
        raise asyncio.CancelledError()

    v.on_sleep = abnormal
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.block_reason == "ABNORMAL_EXIT"
    assert v.cancels and len(v.posts) == 2
    journal_path = executor.journal.path
    executor.close()
    v.executor = None
    assert any(record["kind"] == "sm_placement_intent" for record in journal_records(journal_path))
    with pytest.raises(RuntimeError, match="HISTORICAL_SM_RISK"):
        await SMWindowExecutor.open(env[1], SLUG, "new-session")
    assert len(v.posts) == 2


async def test_other_market_orders_reserve_official_funds(env):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    v.rows["external"] = {
        "id": "external",
        "asset_id": "3",
        "market": "other",
        "side": "BUY",
        "price": ".9",
        "original_size": "5",
        "size_matched": "0",
        "status": "LIVE",
        "associate_trades": [],
    }
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and not v.posts
    assert result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"


async def test_cancel_race_ws_fill_is_processed_then_officially_confirmed(env):
    v, executor = await open_executor(env)
    loop = asyncio.get_running_loop()

    def during_cancel(venue):
        venue.fill("o-1", qty=2)
        delivered = threading.Event()

        def ws_event():
            executor.processor.on_trade(
                TradeEvent(
                    "1", Side.BUY, 0.49, 2, "trade-1", TradeState.MATCHED, v.time, "o-1", CID
                ),
                CID,
            )
            delivered.set()

        loop.call_soon_threadsafe(ws_event)
        assert delivered.wait(2), "test WS delivery stalled"

    v.on_cancel = during_cancel
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert result.yes_inventory == 2 and len(result.confirmed_fills) == 1
    assert result.capital_committed == 0.98


async def test_confirmed_status_only_waits_for_official_economics(env):
    v, executor = await open_executor(env)
    await executor.execute_window("w1", quotes())
    executor.processor.on_trade(
        TradeEvent("1", Side.BUY, 0.49, 2, "late", TradeState.CONFIRMED, v.time, "o-1", CID), CID
    )
    assert not executor.confirmed() and not executor.safe_to_continue
    assert not await executor.verify_close()
    await executor.execute_window("w2", quotes("w2"))
    assert len(v.posts) == 2
    v.fill("o-1", "late", 2)
    assert await executor.verify_close()
    assert sum(f.size for f in executor.confirmed()) == 2


async def test_official_live_after_terminal_also_blocks_next_post(env):
    v, executor = await open_executor(env)
    await executor.execute_window("w1", quotes())
    v.rows["o-1"]["status"] = "LIVE"
    result = await executor.execute_window("w2", quotes("w2"))
    assert result.close_barrier == "BLOCKED" and len(v.posts) == 2
    assert executor.fatal == "TERMINAL_LIVE_CONTRADICTION"


async def test_new_trade_during_multi_order_close_cannot_reuse_first_order_proof(env):
    v, executor = await open_executor(env)
    armed = False
    v.on_cancel = lambda venue: None

    def cancel(venue):
        nonlocal armed
        armed = True

    def order_read(venue, oid):
        nonlocal armed
        if armed and oid == "o-2":
            armed = False
            venue.fill("o-1", "cross-order-late", 2)

    v.on_cancel, v.on_order_read = cancel, order_read
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and len(v.posts) == 2
    assert not executor.safe_to_continue
    assert await executor.verify_close()
    assert sum(f.size for f in executor.confirmed()) == 2


async def test_new_trade_during_next_pre_submit_snapshot_stops_new_window(env):
    v, executor = await open_executor(env)
    await executor.execute_window("w1", quotes())
    original = v.response
    # External HTTP scheduling, not an internal executor/FillTruth replacement.
    transport = executor.gateway._client
    assert transport.known_ids == {"o-1", "o-2"}
    loop = asyncio.get_running_loop()
    v.on_order_read = None
    # Trigger just after previous proofs are re-read, on the next Gamma read.
    triggered = False

    def response(request):
        nonlocal triggered
        if request.url.host == "gamma-api.polymarket.com" and not triggered:
            triggered = True

            def ws_event():
                executor.processor.on_trade(
                    TradeEvent(
                        "1", Side.BUY, 0.49, 2, "new-state", TradeState.MATCHED, v.time, "o-1", CID
                    ),
                    CID,
                )

            loop.call_soon_threadsafe(ws_event)
        return original(request)

    # Existing MockTransport invokes the fixture's bound handler, so replace its
    # handler boundary without changing any tested production component.
    executor_transport = httpx.MockTransport(response)
    # Gamma uses new AsyncClient instances via the fixture factory.
    v.on_sleep = None
    # Install only the external HTTP handler, retaining real clients/signing.
    with pytest.MonkeyPatch.context() as patch:
        # The fixture's factory captured its original transport; use an actual
        # AsyncClient class to avoid nesting factories.
        from httpx._client import AsyncClient

        patch.setattr(
            httpx, "AsyncClient", lambda **kw: AsyncClient(transport=executor_transport, **kw)
        )
        result = await executor.execute_window("w2", quotes("w2"))
    assert result.close_barrier == "BLOCKED" and len(v.posts) == 2


async def test_journal_lock_prevents_second_writer_and_history_prevents_restart(env):
    v, executor = await open_executor(env)
    with pytest.raises(BlockingIOError):
        RiskJournal(env[1].paths.journal_dir, ADDRESS)
    path = executor.journal.path
    executor.close()
    v.executor = None
    with pytest.raises(RuntimeError, match="HISTORICAL_SM_RISK"):
        RiskJournal(env[1].paths.journal_dir, ADDRESS)
    assert journal_records(path) and not v.posts


async def test_inflight_post_caller_cancellation_waits_for_ack_then_closes(env):
    v, executor = await open_executor(env)
    posted, release = threading.Event(), threading.Event()

    def pause_post(venue):
        posted.set()
        assert release.wait(2), "offline POST release timed out"

    v.after_post = pause_post
    task = asyncio.create_task(executor.execute_window("w1", quotes()))
    assert await asyncio.to_thread(posted.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # repeated cancellation must not orphan the SDK worker
    release.set()
    result = await task
    assert result.close_barrier == "BLOCKED" and result.block_reason == "ABNORMAL_EXIT"
    assert result.known_order_ids == ["o-1", "o-2"] and v.cancels == [["o-1", "o-2"]]
    assert len(v.post_requests) == 1 and len(v.posts) == 2
    assert all(v.rows[oid]["status"] == "CANCELED" for oid in result.known_order_ids)


async def test_delayed_batch_ack_crosses_cutoff_only_cleanup_is_allowed(env):
    v, executor = await open_executor(env)
    v.time, v.post_delay = START + 179, 65
    result = await executor.execute_window("w1", quotes())
    assert len(v.posts) == 2 and len(v.post_requests) == 1 and v.time > START + 240
    assert v.cancels == [["o-1", "o-2"]] and result.close_barrier == "SAFE_TO_CONTINUE"


async def test_duplicate_placement_identity_keeps_ownership_and_unknown_risk(env):
    v, executor = await open_executor(env)
    # Keep the original order at the fake venue; second reply is contradictory.
    v.replies = [{"success": True, "orderID": "o-1"}, {"success": False, "orderID": "o-1"}]
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED"
    assert result.placement_results[-1]["outcome"] == "UNKNOWN"
    assert executor.processor.owned_record("o-1")["token_id"] == "1"
    assert result.capital_committed >= 2.4
    await executor.execute_window("w2", quotes("w2"))
    assert len(v.posts) == 2


async def test_valid_one_quote_and_official_changed_tick(env):
    v, executor = await open_executor(env)
    v.ticks["1"] = ".001"
    v.books["1"]["tick_size"] = ".001"
    result = await executor.execute_window("w1", [replace(quotes()[0], price=0.495)])
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert len(v.posts) == 1 and result.known_order_ids == ["o-1"]


async def test_unproven_proxy_identity_stays_blocked_before_execution(env):
    v, cfg = env
    cfg.wallet.signature_type = 2
    with pytest.raises(ValueError, match="M2_REQUIRES_PROVEN_EOA"):
        await SMWindowExecutor.open(cfg, SLUG, "session-1")
    assert not v.posts and not v.cancels


async def test_two_owned_maker_legs_share_trade_id_without_deduplication_loss(env):
    v, executor = await open_executor(env)

    def fill_both(venue):
        first = venue.fill("o-1", "shared-trade", 2)
        second = venue.fill("o-2", "shared-trade", 2)
        first["maker_orders"].extend(second["maker_orders"])
        venue.trades = [first]

    v.on_cancel = fill_both
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert result.yes_inventory == result.no_inventory == 2 and result.delta == 0
    assert len(result.confirmed_fills) == 2 and result.capital_committed == 1.94
    assert await executor.verify_close()
    assert len(executor.confirmed()) == 2


async def test_exact_twenty_dollar_single_order_is_allowed(env):
    v, executor = await open_executor(env)
    result = await executor.execute_window("w1", [replace(quotes()[0], price=0.5, size=40)])
    assert result.close_barrier == "SAFE_TO_CONTINUE" and len(v.posts) == 1
    assert executor.attempts[0].maximum == Decimal("20")


async def test_journal_failure_after_ack_stops_new_requests_but_closes_entire_batch(env, monkeypatch):
    v, executor = await open_executor(env)
    original = os.fsync
    failed = False

    def fsync(fd):
        nonlocal failed
        if not failed and journal_records(executor.journal.path)[-1]["kind"] == "sm_placement_ack":
            failed = True
            raise OSError("offline disk error after ACK")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    v.on_cancel = lambda venue: venue.fill("o-1", qty=2)
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.block_reason == "JOURNAL_WRITE_FAILED"
    assert len(v.posts) == 2 and len(v.post_requests) == 1 and result.yes_inventory == 2
    assert v.rows["o-1"]["status"] == "CANCELED"
    assert v.rows["o-2"]["status"] == "CANCELED"
    assert executor.processor.owned_record("o-1") is not None


async def test_unknown_lookup_failure_still_recovers_known_confirmed_fill(env):
    v, executor = await open_executor(env)
    v.replies = [{"success": True, "orderID": "o-1"}, {"success": True}]

    def after_post(venue):
        if len(venue.posts) == 2:
            venue.fail_path = "/data/orders"

    v.after_post = after_post
    v.on_cancel = lambda venue: venue.fill("o-1", qty=2)
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.yes_inventory == 2
    assert result.capital_committed == 3.38 and len(v.posts) == 2
    assert v.rows["o-1"]["status"] == "CANCELED"
    assert any(
        r["kind"] == "sm_unknown_official_lookup" and "read_error" in r["data"]["payload"]
        for r in journal_records(executor.journal.path)
    )


async def test_official_open_orders_contradiction_stops_cached_terminal_reuse(env):
    v, executor = await open_executor(env)
    await executor.execute_window("w1", quotes())
    v.open_rows_override = [{**v.rows["o-1"], "status": "LIVE"}]
    result = await executor.execute_window("w2", quotes("w2"))
    assert result.close_barrier == "BLOCKED" and len(v.posts) == 2
    assert executor.fatal == "TERMINAL_LIVE_CONTRADICTION"
