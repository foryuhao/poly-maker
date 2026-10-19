"""Actual discovery/M1/M3/M2 composition; inject only official HTTP and WS."""

import asyncio
import json
import time

import httpx
import pytest

from polymaker.rdp_sm_entry import discover_current_market
from polymaker.rdp_sm_execution import SMWindowExecutor
from polymaker.rdp_sm_loop import SMTradingLoop
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import CID, SLUG, START
from test_rdp_sm_obi_execution import Wire
from test_rdp_sm_obi_execution import env as env


@pytest.mark.parametrize("disconnect_preflight,cancel_before_order", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("prior_unresolved", [False, True])
@pytest.mark.parametrize("cached", [False, True])
async def test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown(env, monkeypatch, disconnect_preflight, cancel_before_order, prior_unresolved, cached):
    v, cfg = env
    cfg.rdp_sm.normal_entry_mode = "obi_v1"
    cfg.rdp_sm.obi.network_min_samples = cfg.rdp_sm.obi.network_recovery_samples = 1
    cfg.rdp_sm.obi.signal_horizon_sec = 0.12
    cfg.rdp_sm.obi.max_signal_age_ms = 50
    if prior_unresolved:
        from polymaker.rdp_sm_fence import AccountLease

        lease = AccountLease(cfg, v.funder)
        lease.activate(lease.path.parent / "missing_prior_market.json", "prior-session")
        lease.close()
    anchor = time.monotonic()
    monkeypatch.setattr(time, "time", lambda: v.time + time.monotonic() - anchor)
    sockets, wire_ref, reads = [], [], []
    feeds, injected = [], []

    def capture_feed(*args, **kwargs):
        feed = SessionFeed(*args, **kwargs)
        feeds.append(feed)
        return feed

    monkeypatch.setattr("polymaker.rdp_sm_marketdata.SessionFeed", capture_feed)
    real_open = SMWindowExecutor.open

    async def observe_open(cls, *args, **kwargs):
        e = await real_open(*args, **kwargs)
        v.executor = e
        wire_ref.append(Wire(v, e))
        if cancel_before_order:
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
        return e

    monkeypatch.setattr(SMWindowExecutor, "open", classmethod(observe_open))

    class Socket:
        def __init__(self):
            self.counter = 0
            self.closed = False
            self.pings = 0
            sockets.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            self.closed = True

        async def send(self, payload):
            if payload == "PING":
                self.pings += 1
                return
            assert json.loads(payload) == {"assets_ids": ["1", "2"], "type": "market"}

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(0.005)
            self.counter += 1
            n = self.counter % 2
            return json.dumps(
                [
                    {
                        "event_type": "book",
                        "market": CID,
                        "asset_id": t,
                        "timestamp": str((v.time + time.monotonic() - anchor) * 1000),
                        "bids": [
                            {
                                "price": ".5" if t == "1" else ".49",
                                "size": str(80 + n if t == "1" else 20 + n),
                            }
                        ],
                        "asks": [
                            {
                                "price": ".52" if t == "1" else ".51",
                                "size": str(20 + n if t == "1" else 80 + n),
                            }
                        ],
                    }
                    for t in ("1", "2")
                ]
            )

    monkeypatch.setattr(
        "polymaker.rdp_sm_marketdata.websockets.connect", lambda *_args, **_kw: Socket()
    )

    def http(request):
        if request.url.path == "/book":
            raise AssertionError("REST book in obi_v1 entry")
        if request.url.path == "/time":
            if disconnect_preflight and feeds and feeds[-1].snapshot().valid and not injected:
                injected.append(True)
                feeds[-1].invalidate("WS_DISCONNECTED")
            return httpx.Response(200, json=v.time + time.monotonic() - anchor, request=request)
        reads.append((request.method, request.url.path))
        if request.method in {"POST", "DELETE"}:
            response = wire_ref[0](request)
            if request.method == "DELETE":
                v.time = START + 300
            return response
        return None

    client = boundary(monkeypatch, v, http)
    loop = SMTradingLoop(cfg, SLUG, "obi-entry", monotonic=time.monotonic)
    companion = None
    try:
        meta, _, _, books = await discover_current_market(cfg)
        assert books == {} and meta.condition_id == CID
        # Production entry pins this deadline before the first M1 attempt,
        # including attempts that fail before a complete market snapshot exists.
        loop.market_deadline = anchor + START + 300 - v.time
        if cached:
            from polymaker.rdp_sm_entry import AccountRiskPolicy
            from polymaker.rdp_sm_sync_companion import SyncCompanion
            from polymaker.rdp_sm_sync_reads import ObserverReader

            cfg.rdp_sm.sync_observe.enabled = cfg.rdp_sm.sync_observe.use_for_execution = True
            cfg.rdp_sm.sync_observe.order_interval_s = .03
            cfg.rdp_sm.sync_observe.account_interval_s = .05
            companion = SyncCompanion(cfg, "obi-entry", reader_factory=lambda emit, stop:
                ObserverReader(cfg, emit=emit, stop=stop,
                               transport=httpx.MockTransport(v.response)))
            companion.bind_market(meta, START)
            companion.start()
            policy = AccountRiskPolicy(use_starting_balance=True, sync_observer=companion)
            result = await asyncio.wait_for(loop._run(offline=True, account_policy=policy), 10)
        else:
            result = await asyncio.wait_for(loop.run(offline=True), 10)
        assert result.execution_status == ("BLOCKED" if cancel_before_order else "COMPLETED"), (
            result.stop_reason,
            result.to_dict(),
            wire_ref,
            reads[-15:],
            loop.executor.reads.feed.snapshot() if loop.executor else None,
        )
        assert result.total_windows == (0 if cancel_before_order else 1)
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
        assert len(v.post_requests) == (0 if cancel_before_order else 1)
        if cancel_before_order:
            assert result.stop_reason == "USER_INTERRUPTED_BEFORE_ORDERS"
            assert json.loads(loop.executor.reads.lease.path.read_text())["status"] == "CLOSED"
        assert any(socket.pings for socket in sockets)
        assert all(socket.closed for socket in sockets)
        assert loop.executor.reads.feed.task.done()
        assert loop.normal_controller is not loop.rebalance_controller
        from polymaker.rdp_sm_path_repair import PathRepairController

        assert isinstance(loop.rebalance_controller, PathRepairController) == (cfg.rdp_sm.repair.policy == "hd_tp_sl_v1")
        assert bool(injected) is disconnect_preflight
    finally:
        if companion is not None:
            await asyncio.to_thread(companion.close)
        client.close()
