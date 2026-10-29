"""17:30 incident: lost NORMAL POST, HTTP-200 null details, risk retained."""

import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from polymaker import rdp_sm_normal_unknown as recovery
from polymaker.domain import Side
from polymaker.execution.gateway import CancelResult
from polymaker.rdp_sm_cached_reads import CachedExecutionReads
from polymaker.rdp_sm_execution import Placement, SMWindowExecutor, WindowQuote
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_normal import CANormalController
from polymaker.rdp_sm_sync import SyncSnapshot
from polymaker.rdp_sm_sync_reads import ReadEvidence
from test_rdp_sm_ba import trade
from test_rdp_sm_ca import buy
from test_rdp_sm_ca_entry import env as env
from test_rdp_sm_ca_matched_tp import fill, prices, tp_setup
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_network import ready as network_ready
from test_rdp_sm_network import recover as recover_network
from test_rdp_sm_repair import reflect_positions


async def incident(env, monkeypatch, tmp_path, *, post_delay=0, initial_cash=None):
    setup = await tp_setup(env, monkeypatch)
    v, e, feed, lifecycle, normal, _, client = setup[:7]
    e.cfg.rdp_sm.ca_matched_tp.profit_per_share = D(".05")  # Recorded 17:30 configuration.
    if initial_cash is not None:
        v.capital["balance"] = str(int(D(initial_cash) * 1_000_000))
        e.starting_collateral = D(initial_cash)
    unknown_id = []

    def cache_hook(request):
        if (
            unknown_id
            and unknown_id[0] not in v.rows
            and request.url.path == "/data/order/" + unknown_id[0]
        ):
            return httpx.Response(200, content=b"null", request=request)

    companion, calls = await attach(setup, tmp_path, hook=cache_hook)
    cache = e.shared_reads
    cache.timeout_s = 0.3
    try:
        prices(feed, ".64")
        feed.apply([trade(feed, "2"), buy(feed, "2")])
        first, _ = await normal.enter(D(0), D(0), "incident-first-no")
        assert first.close_barrier == "SAFE_TO_CONTINUE", first.block_reason
        assert first.requested_quotes[0].price == 0.35
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        native = client._transport.handle_request

        def failed_post(request):
            if request.method == "POST":
                # Real SDK/guard/WAL executed, but no venue acceptance/ACK.
                v.post_requests.append(json.loads(request.content))
                unknown_id.append(e.attempts[-1].expected_order_id)
                if post_delay:
                    time.sleep(post_delay)  # SDK thread: cancellation remains independent.
                raise httpx.ReadTimeout("17:30 incident lost POST", request=request)
            return native(request)

        monkeypatch.setattr(client, "_transport", httpx.MockTransport(failed_post))
        prices(feed, ".68")
        feed.apply([trade(feed), buy(feed)])
        second, _ = await normal.enter(D(0), D(0), "incident-unknown-yes")
        assert second.close_barrier == "BLOCKED"
        a = e.attempts[-1]
        assert a.outcome == "UNKNOWN" and a.quote.price == 0.68 and a.quote.size == 5
        assert e.committed() == D("3.4")
        monkeypatch.setattr(client, "_transport", httpx.MockTransport(native))
        return setup, companion, calls, a
    except BaseException:
        await finish(setup, companion)
        raise


async def ready(e, timeout=4):
    deadline = time.monotonic() + timeout
    while not e.admission_ready or not e.network_health().allowed:
        assert time.monotonic() < deadline, e.reason
        e.normal_unknown.observe()
        await asyncio.sleep(0.02)
    assert e.normal_unknown.observe()


async def test_1730_lost_post_recovers_only_after_ten_seconds_and_retains_risk(
    env, monkeypatch, tmp_path
):
    setup, companion, calls, a = await incident(env, monkeypatch, tmp_path, post_delay=0.35)
    v, e = setup[:2]
    try:
        assert recovery.WAIT_SEC == 10
        assert not e.admission_ready and not e.safe_to_continue
        deadline = time.monotonic() + 4
        while not e.normal_unknown.suppress_cancel(a.order_id):
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        assert e.normal_unknown.suppress_cancel(a.order_id)
        boundary = e.normal_unknown.cancel_drained_after_post[a.order_id]
        await asyncio.sleep(max(0, boundary + 10 - time.monotonic()) + 0.01)
        await ready(e)
        assert a.outcome == "UNKNOWN" and e.committed() == D("3.4")
        assert e.economic_inventory() == (D(0), D(0))
        assert a.order_id in e.known and a.order_id not in e.proofs and a.order_id not in e.terminal
        assert not e.safe_to_continue
        assert not e.delta_candidate_allowed(D(0), "YES", D(5))
        assert e.delta_candidate_allowed(D(0), "NO", D(5))
        assert len(v.post_requests) == 2  # Never replay the failed signal.
        account = (await e._snapshot(include_books=False))[3]
        assert account["execution_reserved_usd"] == 3.4
        assert not await e.verify_close(cancel=False)  # Final closure is still strict.
        await e.journal.flush()
        logs = journal_records(e.journal.path)
        event = next(
            r["data"]["payload"] for r in logs if r["kind"] == "sm_normal_unknown_cache_recovery"
        )
        assert event["event"] == "READY" and event["reservation_released"] is False
        assert event["reserved_usdc"] == "3.40" or D(event["reserved_usdc"]) == D("3.4")
    finally:
        await finish(setup, companion)


async def test_1730_new_opposite_ca_can_post_same_direction_still_cannot(
    env, monkeypatch, tmp_path
):
    setup, companion, _, a = await incident(env, monkeypatch, tmp_path)
    v, e, feed, lifecycle, normal = setup[:5]
    monkeypatch.setattr(recovery, "WAIT_SEC", 0.03)
    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        prices(feed, ".69")
        feed.apply([trade(feed), buy(feed)])
        assert await normal.enter(D(0), D(0), "same-side-blocked") is None
        assert len(v.post_requests) == 2
        prices(feed, ".60")
        feed.apply([trade(feed, "2"), buy(feed, "2")])
        third = await normal.enter(D(0), D(0), "new-opposite-ca")
        assert third is not None, lifecycle.guard_reason
        assert len(v.post_requests) == 3
        assert e.attempts[-1].quote.outcome == "NO" and e.attempts[-1].quote.size == 5
        assert a.outcome == "UNKNOWN" and e.committed() >= D("3.4")
        assert not e.safe_to_continue
    finally:
        await finish(setup, companion)


def replay_fixture():
    return json.loads((Path(__file__).parent / "fixtures/normal_unknown_1730.json").read_text())


def recorded_snapshot(row):
    def reads(group):
        values = []
        for original in row[group].values():
            data = dict(original)
            data["data_json"] = (
                json.dumps(data.pop("data")) if data["data"] is not None else data.pop("data")
            )
            data["partial_json"] = json.dumps(data.pop("partial"))
            values.append(ReadEvidence(**data))
        return tuple(values)

    return SyncSnapshot(
        version=row["version"],
        status=row["status"],
        session_id=row["session_id"],
        market_json=json.dumps(row["market"]),
        published_monotonic=row["published_monotonic"],
        published_wall=row["published_wall"],
        attempts=reads("attempts"),
        last_good=reads("last_good"),
        execution_cache=True,
    )


def replay_state():
    fixture = replay_fixture()
    frame = fixture["frames"][-1]
    market = frame["market"]
    now = [frame["published_monotonic"]]
    snapshot = [recorded_snapshot(frame)]
    oid = fixture["unknown_order_id"]
    quote = WindowQuote(
        market["condition_id"], market["tokens"][0], "YES", Side.BUY, 0.68, 5, "recorded-1730-w2"
    )
    a = Placement(
        "recorded-1730",
        quote,
        outcome="UNKNOWN",
        order_id=oid,
        expected_order_id=oid,
        post_started=True,
    )
    processor = SimpleNamespace(
        quarantined_orders=set(),
        fill_records={},
        economic_fills_for_orders=lambda ids: [],
        has_failed_fill=lambda oid: False,
        has_unresolved_fill_key=lambda oid: False,
    )
    e = SimpleNamespace(
        attempts=[a],
        known={oid},
        proofs={},
        terminal={},
        closed=False,
        fatal=None,
        journal=SimpleNamespace(ready=True),
        inventory_mismatch=None,
        merge_pending=False,
        gateway=SimpleNamespace(
            _client=SimpleNamespace(
                pagination_failure=None, observed_open_rows={}, observed_trade_legs={}
            )
        ),
        observed_order_events=set(),
        processor=processor,
        monotonic=lambda: now[0],
        starting_collateral=D(1000),
        merged_pairs=D(0),
        economic_fills=lambda: [],
        economic_inventory=lambda: (D(0), D(0)),
        position_excluded=set(),
        position_reconciled={},
        meta=SimpleNamespace(
            condition_id=market["condition_id"],
            yes=SimpleNamespace(token_id=market["tokens"][0]),
            no=SimpleNamespace(token_id=market["tokens"][1]),
        ),
        cfg=SimpleNamespace(rdp_sm=SimpleNamespace(repair=SimpleNamespace(delta_limit=D(5)))),
    )
    e._official_fills = SMWindowExecutor._official_fills.__get__(e)
    e._local_fills = SMWindowExecutor._local_fills.__get__(e)
    e.shared_reads = SimpleNamespace(
        inflight={},
        timeout_s=5,
        last_write=fixture["cancel_ack"],
        post_returns={oid: fixture["post_returned"]},
        order_writes={oid: fixture["cancel_ack"]},
        companion=SimpleNamespace(failure=None),
        service=SimpleNamespace(latest=lambda: snapshot[0]),
        context_matches=lambda s: (
            s.session_id == fixture["source_session_id"]
            and s.market_json is not None
            and json.loads(s.market_json) == market
        ),
    )
    m = recovery.NormalUnknownRecovery(e)
    m.track([a], {"applicable_spenders": ["fixture"]})
    m.cancel_drained_after_post[oid] = fixture["cancel_ack"]
    m.transport_failures.add(oid)  # Recorded POST PolyApiException / TRANSPORT_ERROR.
    e.normal_unknown = m
    return fixture, e, m, a, now, snapshot


def test_recorded_1730_frames_keep_unknown_reserved_and_grant_after_ten_seconds():
    fixture, e, m, a, now, snapshot = replay_state()
    admitted = []
    for row in fixture["frames"]:
        now[0] = row["published_monotonic"]
        snapshot[0] = recorded_snapshot(row)
        facts = m.facts()
        elapsed = now[0] - fixture["cancel_ack"]
        if elapsed < 10:
            assert facts is None
        if facts:
            admitted.append(elapsed)
            assert facts["risk_retained"] and not facts["reservation_released"]
            assert D(facts["reserved_usdc"]) == D("3.4")
        assert a.outcome == "UNKNOWN" and a.maximum == D("3.4")
        assert not e.proofs and not e.terminal and e.known == {a.order_id}
    assert admitted and 10 <= admitted[0] <= 14
    assert not m.candidate_allowed(D(0), "YES", D(5))
    assert m.candidate_allowed(D(0), "NO", D(5))


def update_read(snapshot, name, **changes):
    def changed(values):
        return tuple(replace(r, **changes) if r.name == name else r for r in values)

    return replace(
        snapshot, attempts=changed(snapshot.attempts), last_good=changed(snapshot.last_good)
    )


@pytest.mark.parametrize("source", recovery.SOURCES)
@pytest.mark.parametrize("fault", ["stale", "failed", "before_write"])
def test_cache_recovery_requires_every_core_source_fresh_complete_after_writes(source, fault):
    _, e, m, a, now, snap = replay_state()
    assert m.facts()
    changes = (
        {"started_monotonic": now[0] - 6}
        if fault == "stale"
        else {"complete": False, "data_json": None}
        if fault == "failed"
        else {"started_monotonic": e.shared_reads.last_write - 0.001}
    )
    snap[0] = update_read(snap[0], source, **changes)
    assert not m.facts()
    assert a.outcome == "UNKNOWN" and a.maximum == D("3.4")


@pytest.mark.parametrize(
    "fault",
    [
        "session",
        "context",
        "stopped",
        "inflight",
        "balance",
        "positions",
        "positive_detail",
        "bad_detail",
        "late_order",
        "late_trade",
        "identity",
        "other_unknown",
        "fatal",
        "merge",
    ],
)
def test_changed_account_or_order_evidence_revokes_cached_permission(fault):
    _, e, m, a, _, snap = replay_state()
    assert m.facts()
    if fault == "session":
        snap[0] = replace(snap[0], session_id="wrong")
    elif fault == "context":
        snap[0] = replace(snap[0], market_json="{}")
    elif fault == "stopped":
        snap[0] = replace(snap[0], status="STOPPED")
    elif fault == "inflight":
        e.shared_reads.inflight[object()] = ("DELETE", (a.order_id,))
    elif fault == "balance":
        snap[0] = update_read(
            snap[0],
            "capital",
            data_json=json.dumps({"balance": "999000000", "allowances": {"fixture": "1000000000"}}),
        )
    elif fault == "positions":
        snap[0] = update_read(
            snap[0],
            "positions",
            data_json=json.dumps([{"token_id": a.quote.token_id, "size": "5"}]),
        )
    elif fault == "positive_detail":
        snap[0] = update_read(snap[0], "order:" + a.order_id, complete=True)
    elif fault == "bad_detail":
        snap[0] = update_read(snap[0], "order:" + a.order_id, error_code="SYNC_ORDER_INVALID")
    elif fault == "late_order":
        e.observed_order_events.add(a.order_id)
    elif fault == "late_trade":
        e.gateway._client.observed_trade_legs[("late", a.order_id)] = object()
    elif fault == "identity":
        a.quote = replace(a.quote, price=0.67)
    elif fault == "other_unknown":
        b = replace(a, attempt_id="another", order_id="other", expected_order_id="other")
        e.attempts.append(b)
    elif fault == "fatal":
        e.fatal = "SIGNED_ORDER_ID_MISMATCH"
    else:
        e.merge_pending = True
    assert not m.facts()
    assert a.outcome == "UNKNOWN"


def test_only_exact_current_submit_can_share_cache_permission():
    _, e, m, a, _, _ = replay_state()
    q = replace(a.quote, outcome="NO", token_id=e.meta.no.token_id, window_id="new")
    pending = Placement("new", q, expected_order_id="new-id")
    e.attempts.append(pending)
    e.shared_reads.inflight[object()] = ("POST", ("new-id",))
    assert not m.facts()
    assert m.facts(pending_batch=[pending])
    assert m.candidate_allowed(D(0), "NO", D(5), exclude=[pending])
    pending.post_started = True
    assert not m.facts(pending_batch=[pending])


async def test_late_real_terminal_detail_restores_strict_close(env, monkeypatch, tmp_path):
    setup, companion, _, a = await incident(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    monkeypatch.setattr(recovery, "WAIT_SEC", 0.03)
    try:
        await ready(e)
        v.rows[a.order_id] = {
            "id": a.order_id,
            "asset_id": a.quote.token_id,
            "market": e.meta.condition_id,
            "side": "BUY",
            "price": ".68",
            "original_size": "5",
            "size_matched": "0",
            "status": "CANCELED",
            "associate_trades": [],
        }
        deadline = time.monotonic() + 10
        while not await e.verify_close(cancel=False):
            assert time.monotonic() < deadline, e.reason
            await asyncio.sleep(0.03)
        assert a.outcome == "VERIFIED" and e.safe_to_continue and e.committed() == 0
        assert not e.normal_unknown.unknowns()
    finally:
        await finish(setup, companion)


async def test_latest_cache_failure_before_native_post_revokes_permission(
    env, monkeypatch, tmp_path
):
    setup, companion, _, a = await incident(env, monkeypatch, tmp_path)
    v, e, feed, lifecycle, normal = setup[:5]
    monkeypatch.setattr(recovery, "WAIT_SEC", 0.03)
    native = e.gateway.send_prepared

    async def changed(*args, **kwargs):
        bad = update_read(companion.service.latest(), "capital", complete=False, data_json=None)
        monkeypatch.setattr(companion.service, "latest", lambda: bad)
        return await native(*args, **kwargs)

    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        prices(feed, ".60")
        feed.apply([trade(feed, "2"), buy(feed, "2")])
        monkeypatch.setattr(e.gateway, "send_prepared", changed)
        await normal.enter(D(0), D(0), "cache-changes-before-post")
        assert len(v.post_requests) == 2
        assert e.attempts[-1].outcome == "ABORTED"
        assert lifecycle.guard_reason == "SYNC_CACHE_EVIDENCE_CHANGED"
        assert a.outcome == "UNKNOWN" and e.committed() == D("3.4")
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize(
    "case", ["before_post", "no_sdk", "unknown_cancel", "wrong_reason", "valid"]
)
def test_only_cancellation_started_after_post_drains_can_start_timeout(case):
    fixture, e, m, a, _, _ = replay_state()
    m.cancel_drained_after_post.clear()
    post = fixture["post_returned"]
    result = CancelResult((), ((a.order_id, "not found"),), (), post + 0.001, post + 0.002)
    if case == "before_post":
        result = replace(result, sdk_started_monotonic=post - 0.001)
    elif case == "no_sdk":
        result = replace(result, sdk_started_monotonic=None)
    elif case == "unknown_cancel":
        result = replace(result, unknown=(a.order_id,))
    elif case == "wrong_reason":
        result = replace(result, not_canceled=((a.order_id, "permission denied"),))
    m.cancel_completed(result)
    assert bool(m.cancel_drained_after_post) == (
        case in {"valid", "unknown_cancel", "wrong_reason"}
    )


def test_unknown_transport_owner_is_released_only_after_original_network_probe_recovers():
    network, clock = network_ready()
    assert network.begin_batch("unknown")
    network.observe("POST", 5.02, success=False, outcome="TRANSPORT_ERROR", batch_id="unknown")
    pause = network.pause_until
    network.release_drained_unknown_batch("unknown")
    assert network.snapshot().active_batch == "unknown"
    recover_network(network, clock, pause + 1)
    network.release_drained_unknown_batch("other")
    assert network.snapshot().active_batch == "unknown"
    network.release_drained_unknown_batch("unknown")
    assert network.snapshot().allowed and network.snapshot().state == "PROBE"
    assert network.write_evidence["POST"]["status"] == "TRANSPORT_ERROR"
    events = network.drain_events()
    assert any(payload.get("action") == "DRAINED_UNKNOWN_RISK_RETAINED" for _, payload, _ in events)


@pytest.mark.parametrize(
    "filled,delta,side,quantity,allowed",
    [
        (0, 0, "YES", 5, False),
        (0, 0, "NO", 5, True),
        (0, 0, "NO", 6, False),
        (0, -5, "YES", 5, True),
        (0, -5, "NO", 5, False),
        (2, 2, "NO", 5, True),
        (2, 2, "YES", 5, False),
        (5, 5, "NO", 5, True),
    ],
)
def test_pending_unknown_and_partial_fills_stay_in_worst_case_delta(
    filled, delta, side, quantity, allowed
):
    _, e, m, a, _, _ = replay_state()
    if filled:
        e.economic_fills = lambda: [SimpleNamespace(maker_order_id=a.order_id, size=filled)]
    assert m.candidate_allowed(D(delta), side, D(quantity)) is allowed
    assert a.outcome == "UNKNOWN" and a.maximum == D("3.4")


def test_cached_empty_page_cannot_erase_previously_seen_fill_on_another_owned_order():
    _, e, m, _, _, _ = replay_state()
    e.known.add("previous-owned")
    e.proofs["previous-owned"] = object()
    e.terminal["previous-owned"] = object()
    assert m.facts()
    e.gateway._client.observed_trade_legs[("late-leg", "previous-owned")] = ("MATCHED", object())
    assert not m.facts()


async def test_reconciled_unknown_still_yields_while_network_probe_recovers():
    clock = [0.0]
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        await asyncio.sleep(0)

    loop = SMTradingLoop(
        SimpleNamespace(),
        "btc-updown-5m-1000000000",
        "session",
        sleep=sleep,
        monotonic=lambda: clock[0],
    )
    loop.market_deadline = 100
    loop.executor = SimpleNamespace(
        monotonic=lambda: clock[0],
        fatal=None,
        journal=SimpleNamespace(failure=None),
        closed=False,
        inventory_mismatch=None,
        merge_pending=False,
        attempts=[object()],
        safe_to_continue=False,
        admission_ready=True,
        repair_abandonment=None,
    )
    await loop._wait_network_retry(SimpleNamespace(done=lambda: False))
    assert sleeps and clock[0] == pytest.approx(0.5)


async def test_matched_tp_after_recovery_keeps_original_quote_and_unknown_budget(
    env, monkeypatch, tmp_path
):
    setup, companion, _, a = await incident(env, monkeypatch, tmp_path)
    v, e, feed, lifecycle, normal = setup[:5]
    monkeypatch.setattr(recovery, "WAIT_SEC", 0.03)
    send = e.gateway.send_prepared
    starting_cash = D(v.capital["balance"])
    spent = D(0)

    async def fill_new(signed, **kwargs):
        nonlocal spent
        result = await send(signed, **kwargs)
        oid = signed.order_ids[0]
        quote = e.attempts[-1].quote
        fill(v, oid, "new-fill-" + oid, 5)
        v.rows[oid]["status"] = "MATCHED"
        spent += D(str(quote.price)) * 5
        v.capital["balance"] = str(starting_cash - spent * 1_000_000)
        reflect_positions(v)
        return result

    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        prices(feed, ".60")
        feed.apply([trade(feed, "2"), buy(feed, "2")])
        monkeypatch.setattr(e.gateway, "send_prepared", fill_new)
        result, _ = await normal.enter(D(0), D(0), "new-opening-and-tp")
        assert len(v.post_requests) == 4
        assert e.attempts[-2].quote.outcome == "NO" and e.attempts[-2].quote.price == 0.39
        assert e.attempts[-1].quote.outcome == "YES" and e.attempts[-1].quote.price == 0.56
        assert e.economic_inventory() == (D(5), D(5))
        assert a.outcome == "UNKNOWN" and e.committed() == D("8.15")
        assert result.close_barrier == "BLOCKED" and not e.safe_to_continue
    finally:
        await finish(setup, companion)


async def test_unknown_cash_reservation_can_still_block_new_opposite_order(
    env, monkeypatch, tmp_path
):
    setup, companion, _, a = await incident(env, monkeypatch, tmp_path, initial_cash="6")
    v, e, feed, lifecycle, normal = setup[:5]
    monkeypatch.setattr(recovery, "WAIT_SEC", 0.03)
    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert lifecycle.account["execution_reserved_usd"] == 3.4
        assert lifecycle.account["basic_spendable_budget_usd"] == pytest.approx(2.6)
        prices(feed, ".39")  # NO 0.60 x5 needs $3, beyond the remaining $2.60.
        feed.apply([trade(feed, "2"), buy(feed, "2")])
        assert await normal.enter(D(0), D(0), "cash-still-reserved") is None
        assert len(v.post_requests) == 2 and e.committed() == D("3.4")
        assert a.outcome == "UNKNOWN"
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize("status", [401, 403, None])
def test_hard_post_errors_do_not_gain_timeout_admission(status):
    _, e, m, a, _, _ = replay_state()
    m.transport_failures.clear()
    request = httpx.Request("POST", "https://clob.polymarket.com/orders")
    error = (
        httpx.HTTPStatusError(
            "authentication failed",
            request=request,
            response=httpx.Response(status, request=request),
        )
        if status
        else RuntimeError("invalid signed identity")
    )
    m.post_failed([a], error)
    assert not m.transport_failures and not m.facts()
    assert a.unknown_recovery_eligible is False


def test_duplicate_delete_completion_cannot_restart_the_original_timeout():
    fixture, e, m, a, _, _ = replay_state()
    boundary = m.cancel_drained_after_post[a.order_id]
    later = boundary + 3
    e.shared_reads.order_writes[a.order_id] = later
    m.cancel_completed(CancelResult((), (), (a.order_id,), fixture["post_returned"] + 0.1, later))
    assert m.cancel_drained_after_post[a.order_id] == boundary


def test_second_normal_unknown_keeps_admission_blocked():
    _, e, m, a, _, _ = replay_state()
    b = replace(a, attempt_id="second", order_id="second-id", expected_order_id="second-id")
    e.attempts.append(b)
    m.track([b], {"applicable_spenders": ["fixture"]})
    m.transport_failures.add("second-id")
    assert len(m.unknowns()) == 2 and not m.facts()


def test_active_window_waives_only_its_previously_admitted_unknown_detail():
    _, e, m, a, _, snap = replay_state()
    m.active_ids = m.ids()
    assert m.active_ids == {a.order_id}
    e.known.add("new-current-order")
    e.shared_reads.e = e
    e.shared_reads.proof_only_rejections = lambda: set()
    assert not m.facts()  # The new active order has no close proof yet.
    admission = CachedExecutionReads._detail_requirements(
        e.shared_reads, snap[0], allow_unknown=True
    )
    strict = CachedExecutionReads._detail_requirements(e.shared_reads, snap[0], allow_unknown=False)
    assert set(admission) == {"new-current-order"}
    assert set(strict) == {a.order_id, "new-current-order"}
    assert a.outcome == "UNKNOWN" and not e.proofs and not e.terminal


async def test_real_session_loop_resumes_new_signal_without_waiving_merge_or_final_close(
    env, monkeypatch, tmp_path
):
    setup, companion, _, a = await incident(env, monkeypatch, tmp_path)
    v, e, feed = setup[:3]
    monkeypatch.setattr(recovery, "WAIT_SEC", 0.03)
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor, loop.production = e, True
    loop.market_deadline = e.monotonic() + 240
    enter = CANormalController.enter
    calls = []

    async def new_signal(controller, *args):
        calls.append(args)
        prices(feed, ".60")
        feed.apply([trade(feed, "2"), buy(feed, "2")])
        result = await enter(controller, *args)
        if result:
            loop.market_deadline = e.monotonic()
        return result

    monkeypatch.setattr(CANormalController, "enter", new_signal)
    try:
        await ready(e)
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 8)
        assert calls and len(v.post_requests) == 3
        assert result.normal_windows == 1 and result.execution_status == "INCOMPLETE"
        assert result.final_close_barrier == "BLOCKED" and not e.safe_to_continue
        assert a.outcome == "UNKNOWN" and e.committed() == D("3.4")
        await e.journal.flush()
        assert not any(r["kind"] == "sm_merge_intent" for r in journal_records(e.journal.path))
    finally:
        await finish(setup, companion)
