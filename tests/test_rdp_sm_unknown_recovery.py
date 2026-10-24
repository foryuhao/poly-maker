"""Real SDK POST faults + periodic account proofs; never real network/orders."""
import asyncio
import json
import time
import uuid
from dataclasses import replace
from decimal import Decimal
from functools import partial

import httpx
import pytest

from polymaker.domain import Side
from polymaker.rdp_sm import _Issue
from polymaker.rdp_sm_execution import Placement, WindowQuote
from polymaker.rdp_sm_sl import SLExecution
from polymaker.rdp_sm_sl_ledger import exposure
from polymaker.rdp_sm_tp import TPExecution
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_entry import boundary
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_path_repair import env as env
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import reflect_positions
from test_repair_sl_execution import force_sl, until

D = Decimal


async def fault_setup(env, monkeypatch, tmp_path, *, rounds=3, late=False, tp=False,
                      opening_size=5):
    if opening_size != 5:
        from test_rdp_sm_repair import open_yes

        monkeypatch.setattr("test_rdp_sm_path_repair.open_yes", partial(open_yes, size=opening_size))
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle, _, wire = setup[:6]
    e.cfg.rdp_sm.repair.unknown_recovery.confirmation_rounds = rounds
    e.cfg.rdp_sm.obi.network_pause_sec = .01
    e.cfg.rdp_sm.obi.network_max_pause_sec = .02
    if not tp:
        force_sl(controller)
    state = {"unknown": None, "posts": [], "before_recovery": None, "late": late,
             "cutoff": False}
    native_elapsed = controller.elapsed
    monkeypatch.setattr(controller, "elapsed", lambda *_: 240. if state["cutoff"] else native_elapsed())

    def http(request):
        if request.method == "POST":
            a = e.gateway._client._operation_value("attempt")
            state["posts"].append(a)
            if state["unknown"] is None:
                state["unknown"] = a
                v.post_requests.append(json.loads(request.content))
                if late:
                    # The venue accepts, but acknowledgement is lost.
                    v.post_requests.pop()
                    wire(request)
                raise httpx.ReadTimeout("offline lost POST result", request=request)
            state["before_recovery"] = exposure(e)
            response = wire(request)
            if state.get("fill_on_second"):
                for placed in state["posts"]:
                    v.fill(placed.expected_order_id, "late-"+placed.attempt_id, 5)
                reflect_positions(v)
            state["cutoff"] = True
            return response
        return wire(request)

    client = boundary(monkeypatch, v, http)

    def detail(request):
        unknown = state["unknown"]
        if unknown and request.url.path == "/data/order/"+unknown.expected_order_id:
            return httpx.Response(200, content=b"null", headers={"Content-Type": "application/json"})
        return None

    companion, calls = await attach(setup, tmp_path, hook=detail)
    if tp:
        snapshot = lifecycle.snapshot(controller, *e.economic_inventory())
        decision, point = controller.observe(snapshot)
        controller.plan = controller._make_plan(snapshot, decision, point)
        assert controller.plan["mode"] == "TP"
    actor = (TPExecution if tp else SLExecution)(controller, "unknown-repair")
    return setup, controller, actor, companion, client, state, calls


async def test_persistent_null_recovers_only_repair_after_three_complete_rounds(
    env, monkeypatch, tmp_path
):
    setup, _, actor, companion, client, state, _ = await fault_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    try:
        await asyncio.wait_for(actor.run(), 5)
        assert len(state["posts"]) == 2, (state, actor.recovery.count, actor.last_wait)
        unknown = state["unknown"]
        assert unknown.outcome == "UNKNOWN" and unknown.unknown_recovery_eligible
        before = state["before_recovery"]
        assert before.no_pending == 5 and before.interval == (D(0), D(5))
        assert before.reserved == state["posts"][-1].maximum
        assert not e.safe_to_continue and not e.reads.network.snapshot().allowed
        assert unknown.order_id in e.known and unknown.order_id not in e.proofs
        with pytest.raises(_Issue, match="PREVIOUS_WINDOW_UNRESOLVED"):
            await setup[4].enter(*e.economic_inventory(), "no-normal")
        assert len(state["posts"]) == 2
        await e.journal.flush()
        ready = [r["data"]["payload"] for r in journal_records(e.journal.path)
                 if r["kind"] == "sm_unknown_repair_recovery" and r["data"]["payload"]["event"] == "READY"]
        assert ready and ready[0]["rounds"] == 3 and ready[0]["normal_allowed"] is False
        assert ready[0]["reservation_released"] and not ready[0]["risk_retained"]
        assert unknown.outcome == "UNKNOWN"
        assert all(row["status"] != "LIVE" for row in v.rows.values())
    finally:
        state["cutoff"] = True
        await finish(setup, companion)
        client.close()


@pytest.fixture
async def gate(env, monkeypatch, tmp_path):
    setup, controller, actor, companion, client, state, _ = await fault_setup(env, monkeypatch, tmp_path)
    e, cache = setup[1], setup[1].shared_reads
    goal, reason = actor.goal()
    assert goal, reason
    side, price, qty, _ = goal
    a = Placement(uuid.uuid4().hex, WindowQuote(e.meta.condition_id,
        e.meta.yes.token_id if side == "YES" else e.meta.no.token_id,
        side, Side.BUY, float(price), float(qty), "unknown-repair:sl:1"))
    e.attempts.append(a)
    actor.orders.append(a)
    actor.created.append(a)
    actor.posts[a.attempt_id] = asyncio.create_task(actor.submit(goal, a))
    await actor.posts[a.attempt_id]
    assert a.outcome == "UNKNOWN"
    actor.cancels[a.attempt_id] = asyncio.create_task(actor.cancel(a))
    await actor.cancels[a.attempt_id]
    await cache.observe_sl(actor)
    await until(lambda: any(r.name == "order:"+a.order_id and r.error_code == "SYNC_ORDER_NOT_FOUND"
                           for r in cache.service.latest().attempts))
    await cache.observe_sl(actor)
    base = cache.service.latest()
    baseline, latest = [base], [base]
    monkeypatch.setattr(cache.service, "latest", lambda: latest[0])
    sequence = [0]

    async def publish(*, mutate=None, reuse=None):
        await asyncio.sleep(.004)
        base = baseline[0]
        sequence[0] += 1
        now = time.monotonic()
        updates = {r.name: replace(r, read_id=f"{r.name}-{sequence[0]}",
                                  started_monotonic=now-.001, completed_monotonic=now-.0005)
                   for r in base.attempts if r.name in {"time", "open_orders", "trades", "capital", "positions"}}
        if reuse:
            updates[reuse] = next(r for r in latest[0].attempts if r.name == reuse)
        snap = replace(base, version=base.version+sequence[0],
            attempts=tuple(updates.get(r.name, r) for r in base.attempts),
            last_good=tuple(updates.get(r.name, r) for r in base.last_good))
        if mutate:
            snap = mutate(snap)
        latest[0] = snap
        actor.recovery.observe()
        return snap

    try:
        yield setup, controller, actor, companion, state, a, publish, latest, baseline
    finally:
        state["cutoff"] = True
        await finish(setup, companion)
        client.close()


async def ready(gate):
    for _ in range(3):
        await gate[6]()
    assert gate[2].recovery.granted
    for _ in range(10):
        gate[0][1].reads.network.observe("GET", .001)


async def test_only_distinct_complete_nonoverlapping_rounds_count(gate):
    actor, publish = gate[2], gate[6]
    await publish()
    for _ in range(30):
        actor.recovery.observe()
    assert actor.recovery.count == 1 and not actor.recovery.allows()
    await publish(reuse="positions")
    assert actor.recovery.count == 1
    await publish()
    assert actor.recovery.count == 2 and not actor.recovery.allows()
    assert exposure(gate[0][1]).no_pending == 5
    await publish()
    assert actor.recovery.allows() and actor.recovery.count == 3
    assert exposure(gate[0][1]).no_pending == exposure(gate[0][1]).reserved == 0


def alter_source(snap, name, *, failed=False, data=None, stale=False):
    old = next(r for r in snap.attempts if r.name == name)
    change = (replace(old, complete=False, data_json=None, error_type="ReadTimeout", read_id="failed")
              if failed else replace(old, data_json=json.dumps(data)) if data is not None
              else replace(old, started_monotonic=old.started_monotonic-20))
    return replace(snap, attempts=tuple(change if r.name == name else r for r in snap.attempts),
                   last_good=snap.last_good if failed else tuple(
                       change if r.name == name else r for r in snap.last_good))


@pytest.mark.parametrize("name", ["time", "open_orders", "trades", "positions", "capital"])
async def test_any_failed_collection_revokes_and_requires_three_new_rounds(gate, name):
    await ready(gate)
    actor, publish = gate[2], gate[6]
    await publish(mutate=lambda s: alter_source(s, name, failed=True))
    assert not actor.recovery.active and not actor.health().allowed
    assert exposure(gate[0][1]).no_pending == 5
    await publish()
    await publish()
    assert not actor.recovery.allows()
    await publish()
    assert actor.recovery.allows()
    assert exposure(gate[0][1]).no_pending == 0


@pytest.mark.parametrize("kind", ["stale", "position", "capital", "external_order"])
async def test_inconsistent_or_stale_evidence_never_grants_recovery(gate, kind):
    e, actor, publish = gate[0][1], gate[2], gate[6]

    def corrupt(snap):
        if kind == "stale":
            return alter_source(snap, "positions", stale=True)
        if kind == "position":
            return alter_source(snap, "positions", data=[{"token_id": e.meta.no.token_id,
                                                        "size": 5., "avg_price": .5}])
        if kind == "capital":
            raw = next(r.data for r in snap.attempts if r.name == "capital")
            raw["balance"] = str(int(raw["balance"])-1_000_000)
            return alter_source(snap, "capital", data=raw)
        return alter_source(snap, "open_orders", data=[{"order_id": "external", "side": "BUY",
            "token_id": e.meta.yes.token_id, "price": .5, "original_size": 5}])

    for _ in range(4):
        await publish(mutate=corrupt)
    assert not actor.recovery.allows() and not actor.health().allowed


async def test_unseen_account_read_failure_also_resets_the_cohort(gate):
    actor, companion, publish = gate[2], gate[3], gate[6]
    await ready(gate)
    companion.service.account_failure_revision += 1
    assert not actor.recovery.allows()
    await publish()
    assert actor.recovery.count == 1 and not actor.recovery.active
    await publish()
    await publish()
    assert actor.recovery.allows()


async def test_new_network_fault_requires_fresh_rounds_and_probe(gate):
    e, actor, publish = gate[0][1], gate[2], gate[6]
    await ready(gate)
    e.reads.network.observe("GET", .001, False, outcome="TRANSPORT_ERROR")
    assert not actor.health().allowed and not actor.recovery.allows()
    await publish()
    await publish()
    assert not actor.recovery.allows()
    await publish()
    await asyncio.sleep(.025)
    for _ in range(10):
        e.reads.network.observe("GET", .001)
    assert actor.recovery.allows() and actor.health().allowed
    assert not e.reads.network.snapshot().allowed
    assert not e.reads.network.continuation("another-repair").allowed
    assert not e.reads.network.repair_probe("another-repair").allowed


@pytest.mark.parametrize("hazard", ["post_pending", "cancel_before_post", "cancel_pending"])
async def test_post_and_cancel_races_cannot_count_old_evidence(gate, hazard):
    actor, a, publish = gate[2], gate[5], gate[6]
    if hazard == "post_pending":
        actor.posts[a.attempt_id] = asyncio.get_running_loop().create_future()
    elif hazard == "cancel_before_post":
        actor.cancel_started[a.attempt_id] = actor.recovery.post_boundary(a)-1
    else:
        actor.cancels[a.attempt_id] = asyncio.get_running_loop().create_future()
    for _ in range(4):
        await publish()
    assert not actor.recovery.active and not actor.recovery.allows()


async def test_late_open_order_revokes_grant_and_resumes_cancellation(gate):
    e, actor, a, publish = gate[0][1], gate[2], gate[5], gate[6]
    await ready(gate)
    assert actor.recovery.suppress_cancel(a)
    row = {"order_id": a.order_id, "market_id": e.meta.condition_id,
           "token_id": a.quote.token_id, "side": "BUY", "price": a.quote.price,
           "original_size": a.quote.size, "size_matched": 0, "status": "LIVE"}
    await publish(mutate=lambda s: alter_source(s, "open_orders", data=[row]))
    assert not actor.recovery.active and not actor.recovery.allows()
    assert not actor.recovery.suppress_cancel(a)
    old = actor.cancels[a.attempt_id]
    actor.cancel_old()
    assert actor.cancels[a.attempt_id] is not old
    await actor.cancels[a.attempt_id]
    assert a.outcome == "UNKNOWN" and exposure(e).no_pending == 5


@pytest.mark.parametrize("hazard", ["cutoff", "fatal", "inventory", "merge", "cooldown", "abandon"])
async def test_safety_and_market_cutoff_override_an_existing_grant(gate, hazard):
    e, actor, state = gate[0][1], gate[2], gate[4]
    await ready(gate)
    if hazard == "cutoff":
        state["cutoff"] = True
    elif hazard == "fatal":
        e.fatal = "TEST_FATAL"
    elif hazard == "inventory":
        e.inventory_mismatch = {"test": True}
    elif hazard == "merge":
        e.merge_pending = True
    elif hazard == "cooldown":
        e.cooldown.deadline = e.monotonic()+20
    else:
        e.repair_abandonment = {"test": True}
    assert not actor.recovery.allows() and not actor.health().allowed


async def test_released_unknown_still_checks_candidate_delta_and_capital_limits(gate):
    e, actor, a = gate[0][1], gate[2], gate[5]
    await ready(gate)
    goal, _ = actor.goal()
    assert actor.admission_reason(goal) is None
    side, price, _, snap = goal
    assert actor.admission_reason((side, price, D(11), snap)) == "WAIT_DELTA_CAPACITY"
    assert exposure(e).reserved == exposure(e).no_pending == 0
    assert a.outcome == "UNKNOWN" and a.order_id not in e.proofs
    actor.capital = exposure(e).spent+a.maximum-D('.01')
    assert actor.admission_reason(goal) == "WAIT_CAPITAL_CAPACITY"


async def test_claiming_a_submit_grant_keeps_only_the_new_order_reserved(gate):
    e, actor = gate[0][1], gate[2]
    await ready(gate)
    goal, _ = actor.goal()
    side, price, qty, _ = goal
    candidate = Placement(uuid.uuid4().hex, WindowQuote(e.meta.condition_id,
        e.meta.no.token_id, side, Side.BUY, float(price), float(qty), "replacement"))
    e.attempts.append(candidate)
    actor.orders.append(candidate)
    actor.recovery.reserve(candidate)
    assert not actor.recovery.granted and actor.recovery.allows(candidate)
    assert exposure(e).reserved == candidate.maximum
    assert exposure(e).no_pending == qty


@pytest.mark.parametrize("tp", [False, True])
async def test_partial_472_opening_can_submit_minimum_five_after_unknown_release(
    env, monkeypatch, tmp_path, tp
):
    setup, controller, actor, companion, client, state, _ = await fault_setup(
        env, monkeypatch, tmp_path, tp=tp, opening_size=4.72)
    e = setup[1]
    task = asyncio.create_task(actor.run())
    try:
        if tp:
            await until(lambda: state["unknown"] is not None
                        and state["unknown"].outcome == "UNKNOWN")
            force_sl(controller)
        await asyncio.wait_for(task, 5)
        before = state["before_recovery"]
        assert len(state["posts"]) == 2
        assert before.actual == D('4.72')
        assert before.no_pending == 5 and before.interval == (D('-.28'), D('4.72'))
        assert before.reserved == state["posts"][-1].maximum
        assert state["unknown"].outcome == "UNKNOWN"
        assert state["unknown"].order_id in e.known
        assert state["unknown"].order_id not in e.proofs
        assert not e.safe_to_continue
    finally:
        state["cutoff"] = True
        if not task.done():
            await asyncio.wait_for(task, 5)
        await finish(setup, companion)
        client.close()


async def late_fill(gate, quantity, status="CONFIRMED"):
    setup, _, actor, companion, _, a, _, latest, baseline = gate
    v, e = setup[:2]
    v.rows[a.order_id] = {"id": a.order_id, "asset_id": a.quote.token_id,
        "market": e.meta.condition_id, "side": "BUY", "price": str(a.quote.price),
        "original_size": str(a.quote.size), "size_matched": "0", "status": "CANCELED",
        "associate_trades": []}
    v.fill(a.order_id, "late-fill", quantity, status)
    reflect_positions(v)

    def arrived():
        snap = companion.service.snapshot_at(time.monotonic())
        return snap and any(t["trade_id"] == "late-fill"
            for r in snap.last_good if r.name == "trades" for t in r.data)

    await until(arrived)
    snap = companion.service.snapshot_at(time.monotonic())
    baseline[0] = latest[0] = snap
    # The worker guard sees changed official facts before the async ingest.
    assert not actor.recovery.allows() and not actor.health().allowed
    assert exposure(e) == exposure(e, retain_unknown_reservations=True)
    e.shared_reads._ingest(snap)
    actor.recovery.observe()
    return snap


@pytest.mark.parametrize("quantity", [1, 5])
async def test_late_fill_revokes_permission_and_keeps_full_remaining_envelope(gate, quantity):
    e, actor, a, publish = gate[0][1], gate[2], gate[5], gate[6]
    await ready(gate)
    await late_fill(gate, quantity)
    assert not actor.recovery.active
    assert e.economic_inventory() == (D(5), D(quantity))
    s = exposure(e)
    assert s.no_pending == 5-quantity
    assert s.spent+s.reserved == D("1.5")+a.maximum
    assert a.outcome == "UNKNOWN"
    # New fill evidence requires a fresh cancel attempt before fresh cohorts.
    actor.cancels[a.attempt_id] = asyncio.create_task(actor.cancel(a))
    await actor.cancels[a.attempt_id]
    # Wait for the separate position/capital lane to cover the newly seen fill.
    def account_updated():
        reads = {r.name: r for r in gate[3].service.snapshot_at(time.monotonic()).last_good}
        return (len(reads["positions"].data) == 2
                and D(str(reads["capital"].data["balance"]))/1_000_000
                == e.starting_collateral-exposure(e).spent)

    await until(account_updated)
    gate[8][0] = gate[3].service.snapshot_at(time.monotonic())
    for _ in range(3):
        await publish()
    assert actor.recovery.granted
    if quantity == 5:
        assert actor.goal()[0] is None
    else:
        goal, _ = actor.goal()
        for _ in range(10):
            e.reads.network.observe("GET", .001)
        assert actor.admission_reason(goal) is None
        assert exposure(e).interval == (D(4), D(4))


async def test_pending_late_fill_cannot_be_counted_as_zero(gate):
    await ready(gate)
    await late_fill(gate, 1, "MATCHED")
    for _ in range(4):
        await gate[6]()
    assert not gate[2].recovery.granted and not gate[2].recovery.allows()
    assert gate[5].outcome == "UNKNOWN" and exposure(gate[0][1]).no_pending == 5


async def test_late_fill_before_final_sdk_guard_aborts_new_submission(gate):
    setup, _, actor = gate[:3]
    e = setup[1]
    await ready(gate)
    goal, _ = actor.goal()
    side, price, qty, _ = goal
    candidate = Placement(uuid.uuid4().hex, WindowQuote(e.meta.condition_id, e.meta.no.token_id,
        side, Side.BUY, float(price), float(qty), "recover-candidate"))
    actor.recovery.reserve(candidate)
    e.attempts.append(candidate)
    actor.orders.append(candidate)
    await late_fill(gate, 5)
    actor.posts[candidate.attempt_id] = asyncio.create_task(actor.submit(goal, candidate))
    await actor.posts[candidate.attempt_id]
    assert candidate.outcome == "ABORTED" and not candidate.post_started
    assert len(gate[4]["posts"]) == 1 and gate[5].outcome == "UNKNOWN"


@pytest.mark.parametrize("opening_size", [5, 4.72])
async def test_old_and_new_late_fills_keep_actual_inventory_without_reopening_normal(
    env, monkeypatch, tmp_path, opening_size
):
    setup, _, actor, companion, client, state, _ = await fault_setup(
        env, monkeypatch, tmp_path, late=True, opening_size=opening_size)
    v, e = setup[:2]
    state["fill_on_second"] = True
    try:
        await asyncio.wait_for(actor.run(), 5)
        assert len(state["posts"]) == 2
        await until(lambda: any(r.name == "trades" and len(r.data) == 3
                               for r in companion.service.latest().last_good))
        await e.shared_reads.observe_sl(actor)
        assert e.economic_inventory() == (D(str(opening_size)), D(10))
        assert exposure(e).actual == D(str(opening_size))-10
        assert state["unknown"].outcome == "UNKNOWN"
        assert not e.safe_to_continue and not e.reads.network.snapshot().allowed
        with pytest.raises(_Issue):
            await setup[4].enter(*e.economic_inventory(), "blocked-normal")
        assert len(state["posts"]) == 2
    finally:
        state["cutoff"] = True
        await finish(setup, companion)
        client.close()


async def test_unknown_tp_handoff_recovers_sl_with_released_old_budget(
    env, monkeypatch, tmp_path
):
    setup, controller, actor, companion, client, state, _ = await fault_setup(
        env, monkeypatch, tmp_path, tp=True)
    e = setup[1]
    task = asyncio.create_task(actor.run())
    try:
        await until(lambda: state["unknown"] is not None
                    and state["unknown"].outcome == "UNKNOWN")
        assert state["unknown"].quote.price == .46
        force_sl(controller)
        await asyncio.wait_for(task, 5)
        assert actor.handoff is not None and len(state["posts"]) == 2
        assert actor.handoff.inherited == [state["unknown"]]
        assert state["unknown"].outcome == "UNKNOWN"
        assert state["before_recovery"].interval == (D(0), D(5))
        assert not e.safe_to_continue and not e.reads.network.snapshot().allowed
    finally:
        state["cutoff"] = True
        if not task.done():
            await asyncio.wait_for(task, 5)
        await finish(setup, companion)
        client.close()


async def test_disabled_recovery_never_grants_permission(gate):
    gate[0][1].cfg.rdp_sm.repair.unknown_recovery.enabled = False
    for _ in range(4):
        await gate[6]()
    assert not gate[2].recovery.waiting() and not gate[2].recovery.allows()
    assert not gate[2].health().allowed


async def test_confirmation_rounds_are_configurable(gate):
    gate[0][1].cfg.rdp_sm.repair.unknown_recovery.confirmation_rounds = 2
    await gate[6]()
    assert not gate[2].recovery.allows()
    await gate[6]()
    assert gate[2].recovery.allows()


def test_confirmation_rounds_cannot_be_less_than_two():
    from pydantic import ValidationError

    from polymaker.config import RDPUnknownRecoveryConfig

    with pytest.raises(ValidationError):
        RDPUnknownRecoveryConfig(confirmation_rounds=1)
