"""Preparation constraint outages: real SDK/controllers, offline venue only."""

import asyncio
from decimal import Decimal

import httpx
import pytest

from polymaker.rdp_sm import SessionStatus, _Issue
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_normal import NormalController
from polymaker.rdp_sm_recovery import PreparationReadRetry
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m1 import START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_obi_execution import env as env
from test_rdp_sm_recovery import Observer


async def setup_preparation(env, monkeypatch):
    # These preparation tests run at T20, not on the T60 confirmation boundary.
    env[0].time = START + 20
    setup = await setup_obi(env, monkeypatch)
    setup[1].cfg.rdp_sm.obi.network_pause_sec = .01
    feed = setup[2]
    now, wall = feed.monotonic(), feed.wall()
    with feed.lock:
        feed.probability_points.extendleft(reversed([
            (int(wall)-age, now-age, .51, feed.generation)
            for age in range(11, 0, -1)
        ]))
    return setup


def inject_constraint_fault(setup, monkeypatch, *, endpoint="/tick-size", fault="timeout",
                            persistent=False, on_failure=None):
    client = setup[6]
    native = client._transport.handle_request
    failures = []
    if endpoint == "/version":
        setup[1].gateway._client._ClobClient__cached_version = None

    def response(request):
        if request.url.path == endpoint and (persistent or not failures):
            failures.append(str(request.url))
            if on_failure:
                on_failure(len(failures))
            # Keep the real SDK wrapping and execution-network observation.
            if fault == "timeout":
                return throw_timeout(request)
            return httpx.Response(fault, request=request, json={})
        return native(request)

    monkeypatch.setattr(client, "_transport", httpx.MockTransport(response))
    return failures


def throw_timeout(request):
    raise httpx.ReadTimeout("offline preparation constraint timeout", request=request)


@pytest.mark.parametrize("endpoint", ["/tick-size", "/version"])
@pytest.mark.parametrize("fault", ["timeout", 429, 503])
async def test_constraint_retry_discards_candidate_and_posts_once_after_recovery(
    env, monkeypatch, endpoint, fault
):
    setup = await setup_preparation(env, monkeypatch)
    v, e, _, lifecycle, normal = setup[:5]
    try:
        # An obsolete candidate cannot survive an incomplete constraint refresh.
        from types import SimpleNamespace

        lifecycle.cached_slot = SimpleNamespace(slot_id="already-used")
        lifecycle.used_slots.add("already-used")
        failures = inject_constraint_fault(setup, monkeypatch, endpoint=endpoint, fault=fault)
        with pytest.raises(PreparationReadRetry) as caught:
            await normal.enter(Decimal(0), Decimal(0), "retry-window")
        assert caught.value.__cause__ is not None and len(failures) == 1
        assert lifecycle.cached_slot is None
        assert not e.attempts and not e.known and not v.post_requests
        await e.journal.flush()
        assert not any(r["kind"] == "sm_prepared" for r in journal_records(e.journal.path))
        # The next turn's original network admission still has to pass.
        assert await normal.enter(Decimal(0), Decimal(0), "retry-window") is None
        assert not v.post_requests
        await asyncio.sleep(.02)
        for _ in range(10):
            e.reads.network.observe("GET", .001)
        outcome = await normal.enter(Decimal(0), Decimal(0), "retry-window")
        assert outcome and outcome[0].close_barrier == "SAFE_TO_CONTINUE"
        assert len(v.post_requests) == 1 and e.safe_to_continue
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", [401, 403])
async def test_authentication_failure_is_not_preparation_retry(env, monkeypatch, fault):
    setup = await setup_preparation(env, monkeypatch)
    try:
        inject_constraint_fault(setup, monkeypatch, fault=fault)
        from py_clob_client_v2.exceptions import PolyApiException

        with pytest.raises(PolyApiException) as caught:
            await setup[4].enter(Decimal(0), Decimal(0), "auth")
        assert caught.value.status_code == fault and not setup[0].post_requests
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["identity", "version", "cancel"])
async def test_hard_constraints_and_cancellation_keep_original_exception(env, monkeypatch, fault):
    setup = await setup_preparation(env, monkeypatch)
    e, normal = setup[1], setup[4]
    client = e.gateway._client
    try:
        if fault == "version":
            monkeypatch.setattr(client, "_ClobClient__resolve_version", lambda: 1)
            expected = _Issue
        else:
            def fail(_token):
                if fault == "cancel":
                    raise asyncio.CancelledError()
                raise _Issue(SessionStatus.BLOCKED, "ACCOUNT_IDENTITY_MISMATCH")

            monkeypatch.setattr(client, "get_tick_size", fail)
            expected = asyncio.CancelledError if fault == "cancel" else _Issue
        with pytest.raises(expected):
            await normal.enter(Decimal(0), Decimal(0), "hard-failure")
        assert not setup[0].post_requests and not e.attempts
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("stage", ["wal", "activate"])
async def test_nonread_failures_are_never_converted_to_preparation_retry(env, monkeypatch, stage):
    setup = await setup_preparation(env, monkeypatch)
    try:
        async def fail(*_args, **_kwargs):
            raise httpx.ReadTimeout("offline non-read failure")

        target = setup[1].journal if stage == "wal" else setup[3]
        monkeypatch.setattr(target, "durable" if stage == "wal" else "activate", fail)
        with pytest.raises(httpx.ReadTimeout):
            await setup[4].enter(Decimal(0), Decimal(0), "non-read")
        assert not setup[0].post_requests
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("invalidate_proof", [False, True])
async def test_constraint_retry_retains_prior_order_proofs_and_does_not_bypass_barrier(
    env, monkeypatch, invalidate_proof
):
    setup = await setup_preparation(env, monkeypatch)
    v, e, _, _, normal = setup[:5]
    try:
        outcome = await normal.enter(Decimal(0), Decimal(0), "prior-window")
        assert outcome and outcome[0].close_barrier == "SAFE_TO_CONTINUE"
        setup[7]["direction"] = "NONE"
        await asyncio.sleep(.025)
        setup[7]["direction"] = "YES"
        await asyncio.sleep(.025)
        for _ in range(10):
            e.reads.network.observe("GET", .001)
        before = (set(e.known), dict(e.proofs), list(e.attempts), e.committed(), set(e.used_windows))
        failures = inject_constraint_fault(setup, monkeypatch)
        with pytest.raises(PreparationReadRetry):
            await normal.enter(Decimal(0), Decimal(0), "next-window")
        assert (set(e.known), dict(e.proofs), list(e.attempts), e.committed(), set(e.used_windows)) == before
        assert len(v.post_requests) == 1
        if invalidate_proof:
            e.invalidate("CLOSE_EVIDENCE_INVALIDATED")
            with pytest.raises(_Issue, match="PREVIOUS_WINDOW_UNRESOLVED"):
                await normal.enter(Decimal(0), Decimal(0), "next-window")
            assert len(failures) == 1 and len(v.post_requests) == 1
            assert not e.safe_to_continue
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
async def test_main_loop_pauses_rechecks_and_recovers_or_stops_at_cutoff(
    env, monkeypatch, tmp_path, cached, persistent
):
    setup = await setup_preparation(env, monkeypatch)
    v, e = setup[:2]
    companion = None
    if cached:
        companion, _ = await attach(setup, tmp_path)
    observer = Observer()
    e.display_events = []
    pauses, snapshots, entries = [], [], []
    native_snapshot, native_enter = e._snapshot, NormalController.enter

    async def sleep(seconds):
        pauses.append(seconds)
        await asyncio.sleep(min(seconds, .02))
        for _ in range(10):
            e.reads.network.observe("GET", .001)

    loop = SMTradingLoop(e.cfg, e.slug, e.session_id, sleep=sleep, observer=observer)
    loop.executor, loop.production = e, True
    loop.market_deadline = e.monotonic()+280  # T20; retain original NORMAL cutoff.
    monkeypatch.setattr(loop, "_merge", lambda *_: asyncio.sleep(0, result=False))

    async def snapshot(**kwargs):
        snapshots.append(e.monotonic())
        return await native_snapshot(**kwargs)

    async def enter(controller, *args):
        entries.append((len(snapshots), len(v.post_requests)))
        outcome = await native_enter(controller, *args)
        if outcome:
            loop.market_deadline = e.monotonic()
        return outcome

    def failure(count):
        if persistent and count == 3:
            loop.market_deadline = e.monotonic()+.02

    monkeypatch.setattr(e, "_snapshot", snapshot)
    monkeypatch.setattr(NormalController, "enter", enter)
    failures = inject_constraint_fault(setup, monkeypatch, persistent=persistent, on_failure=failure)
    result = RunResult(e.meta.condition_id, e.session_id)
    try:
        await asyncio.wait_for(loop._run_obi_session(result), 5)
        assert failures and pauses and all(0 < delay <= 2 for delay in pauses)
        assert result.stop_reason == "MARKET_CUTOFF" and result.execution_status == "COMPLETED"
        assert result.final_close_barrier == "SAFE_TO_CONTINUE"
        assert all(not count for _, count in entries)  # No order on any failed preparation.
        prepare = [x for x in observer.events if x["stage"] == "prepare"]
        assert prepare[0]["status"] == "PAUSED"
        assert all(b[0] > a[0] for a, b in zip(entries, entries[1:], strict=False))
        if persistent:
            assert len(failures) == 3 and not v.post_requests and not e.attempts
            assert not any(x["status"] == "RESUMED" for x in prepare)
        else:
            assert len(v.post_requests) == 1 and result.total_windows == 1, result.to_dict()
            assert prepare[-1]["status"] == "RESUMED"
        assert all(row["status"] != "LIVE" for row in v.rows.values())
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)
