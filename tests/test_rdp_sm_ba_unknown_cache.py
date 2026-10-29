"""BA UNKNOWN uses NORMAL's bounded cache recovery, never terminal inference."""

import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal as D

import httpx
import pytest

from polymaker import rdp_sm_normal_unknown as recovery
from polymaker.execution.gateway import CancelResult
from polymaker.rdp_sm_loop import RunResult, SMTradingLoop
from polymaker.rdp_sm_opposite_ba import OppositeBAController
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_normal_unknown_cache import ready, replay_state, update_read
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_repair_ba_tolerance import tolerant_setup


async def incident(env, monkeypatch, tmp_path, *, status=500, initial_cash=None):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, feed, lifecycle, _, _, client = setup[:7]
    if initial_cash is not None:
        e.starting_collateral = D(initial_cash)
        venue.capital['balance'] = str(int((D(initial_cash) - D('1.5')) * 1_000_000))
    missing = []

    def hook(request):
        if any(request.url.path == '/data/order/' + oid and oid not in venue.rows for oid in missing):
            return httpx.Response(200, content=b'null', request=request)

    companion, calls = await attach(setup, tmp_path, hook=hook)
    e.shared_reads.timeout_s = .3
    native = client._transport.handle_request

    def failed_post(request):
        if request.method == 'POST':
            venue.post_requests.append(json.loads(request.content))
            missing.append(e.attempts[-1].expected_order_id)
            if status:
                return httpx.Response(status, json={'error': 'server error'}, request=request)
            raise httpx.ReadTimeout('lost BA POST', request=request)
        return native(request)

    try:
        monkeypatch.setattr(client, '_transport', httpx.MockTransport(failed_post))
        absorption(feed, '2')
        first_episode = next(iter(feed.opposite_ba.opportunities.values()))
        result, _ = await controller.repair(*e.economic_inventory(), 'ba-unknown')
        assert result.close_barrier == 'BLOCKED'
        attempt = e.attempts[-1]
        assert attempt.outcome == 'UNKNOWN' and attempt.quote.outcome == 'NO'
        assert attempt.order_id in e.normal_unknown.repair_ids
        assert attempt.order_id in e.normal_unknown.transport_failures
        assert attempt.order_id in e.normal_unknown.cancel_drained_after_post
        assert first_episode.consumed
        monkeypatch.setattr(client, '_transport', httpx.MockTransport(native))
        return setup, controller, companion, calls, attempt, first_episode, failed_post
    except BaseException:
        await finish(setup, companion)
        raise


@pytest.mark.parametrize('status', [500, None])
async def test_ba_unknown_recovers_on_new_signal_without_releasing_risk(env, monkeypatch, tmp_path, status):
    setup, c, companion, _, old, episode, _ = await incident(env, monkeypatch, tmp_path, status=status)
    v, e, feed, lifecycle = setup[:4]
    try:
        assert recovery.WAIT_SEC == 10
        assert not e.admission_ready and not e.safe_to_continue
        assert e.committed() == old.maximum + D('1.5')
        monkeypatch.setattr(recovery, 'WAIT_SEC', .03)
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert D(str(lifecycle.account['execution_reserved_usd'])) == old.maximum
        assert await c.repair(*e.economic_inventory(), 'no-same-signal-repost') is None
        assert len(v.post_requests) == 2
        absorption(feed, '2')
        result, _ = await c.repair(*e.economic_inventory(), 'fresh-ba')
        assert result is not None, (c.lifecycle.wait_reason, c.lifecycle.guard_reason)
        assert len(v.post_requests) == 3
        assert e.attempts[-1].quote.outcome == 'NO' and e.attempts[-1].quote.size == 5
        assert result.close_barrier == 'BLOCKED' and not e.safe_to_continue
        assert old.outcome == 'UNKNOWN' and e.committed() == old.maximum + D('1.5')
        assert e.economic_inventory() == (D(5), D(0)) and e.repair_episode is not None
        assert episode.consumed
        assert not await e.verify_close(cancel=False)
        await e.journal.flush()
        logs = journal_records(e.journal.path)
        event = next(r['data']['payload'] for r in logs if r['kind'] == 'sm_ba_unknown_cache_recovery')
        assert event['event'] == 'READY' and event['risk_retained']
        assert not event['reservation_released']
    finally:
        await finish(setup, companion)


async def test_second_ba_unknown_blocks_third_buy(env, monkeypatch, tmp_path):
    setup, c, companion, _, old, _, failed_post = await incident(env, monkeypatch, tmp_path)
    v, e, feed, lifecycle, _, _, client = setup[:7]
    monkeypatch.setattr(recovery, 'WAIT_SEC', .03)
    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        monkeypatch.setattr(client, '_transport', httpx.MockTransport(failed_post))
        absorption(feed, '2')
        result, _ = await c.repair(*e.economic_inventory(), 'second-unknown')
        assert result is not None and result.close_barrier == 'BLOCKED'
        assert len(e.normal_unknown.unknowns()) == 2
        assert not e.normal_unknown.facts() and not e.admission_ready
        absorption(feed, '2')
        assert await c.repair(*e.economic_inventory(), 'third-blocked') is None
        assert len(v.post_requests) == 3 and old.outcome == 'UNKNOWN'
        assert lifecycle.wait_reason[0] == 'BA_REPAIR_UNKNOWN_CACHE_NOT_READY'
    finally:
        await finish(setup, companion)


async def test_repair_recovery_keeps_unknown_cash_reserved_for_new_ba(env, monkeypatch, tmp_path):
    setup, c, companion, _, old, _, _ = await incident(
        env, monkeypatch, tmp_path, initial_cash='6'
    )
    v, e, feed, lifecycle = setup[:4]
    monkeypatch.setattr(recovery, 'WAIT_SEC', .03)
    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert D(str(lifecycle.account['execution_reserved_usd'])) == old.maximum
        assert lifecycle.account['basic_spendable_budget_usd'] < float(old.maximum)
        absorption(feed, '2')
        assert await c.repair(*e.economic_inventory(), 'insufficient-unreserved-cash') is None
        assert len(v.post_requests) == 2 and old.outcome == 'UNKNOWN'
        assert e.committed() == old.maximum + D('1.5')
    finally:
        await finish(setup, companion)


def repair_replay():
    fixture, e, m, a, now, snapshot = replay_state()
    m.normal_ids.clear()
    m.track([a], {'applicable_spenders': ['fixture']}, repair=True)
    return fixture, e, m, a, now, snapshot


@pytest.mark.parametrize('source', recovery.SOURCES)
@pytest.mark.parametrize('fault', ['stale', 'failed', 'before_write'])
def test_repair_recovery_requires_complete_fresh_post_boundary_sources(source, fault):
    _, e, m, a, now, snap = repair_replay()
    assert m.facts()
    changes = ({'started_monotonic': now[0] - 6} if fault == 'stale'
               else {'complete': False, 'data_json': None} if fault == 'failed'
               else {'started_monotonic': e.shared_reads.last_write - .001})
    snap[0] = update_read(snap[0], source, **changes)
    assert not m.facts() and a.outcome == 'UNKNOWN'


@pytest.mark.parametrize('second_scope', ['NORMAL', 'BA_REPAIR'])
def test_unknown_limit_is_shared_across_normal_and_repair(second_scope):
    _, e, m, a, _, _ = repair_replay()
    assert m.facts()
    b = replace(a, attempt_id='second', order_id='second-id', expected_order_id='second-id')
    e.attempts.append(b)
    m.track([b], {}, repair=second_scope == 'BA_REPAIR')
    assert len(m.unknowns()) == 2 and not m.facts()


@pytest.mark.parametrize('held,allowed', [('5', True), ('4.72', False), ('3', False)])
def test_accepted_reverse_five_share_risk_never_exceeds_delta_limit(held, allowed):
    _, _, m, _, _, _ = repair_replay()
    assert m.candidate_allowed(-D(held), 'YES', D(5)) is allowed


def test_post_drain_boundary_and_original_ten_second_wait_apply_to_repair():
    fixture, e, m, a, now, snap = repair_replay()
    m.cancel_drained_after_post.clear()
    post = fixture['post_returned']
    m.cancel_completed(CancelResult((), (), (a.order_id,), post - .001, post + .001))
    assert not m.cancel_drained_after_post and not m.facts()
    m.cancel_completed(CancelResult((), (), (a.order_id,), post + .001, post + .002))
    boundary = m.cancel_drained_after_post[a.order_id]
    for source in (*recovery.SOURCES, 'order:' + a.order_id):
        snap[0] = update_read(snap[0], source, started_monotonic=boundary + 9.9)
    now[0] = boundary + 9.999
    assert not m.facts()
    now[0] = boundary + 10.001
    assert m.facts()
    m.cancel_completed(CancelResult((), (), (a.order_id,), post + 2, post + 3))
    assert m.cancel_drained_after_post[a.order_id] == boundary


@pytest.mark.parametrize('evidence', ['order', 'trade', 'detail', 'balance', 'positions'])
def test_late_evidence_revokes_repair_admission_without_risk_release(evidence):
    _, e, m, a, _, snap = repair_replay()
    assert m.facts()
    if evidence == 'order':
        e.observed_order_events.add(a.order_id)
    elif evidence == 'trade':
        e.gateway._client.observed_trade_legs[('late', a.order_id)] = object()
    elif evidence == 'detail':
        snap[0] = update_read(snap[0], 'order:' + a.order_id, complete=True)
    elif evidence == 'balance':
        snap[0] = update_read(snap[0], 'capital', data_json=json.dumps({'balance': '999000000', 'allowances': {'fixture': '1000000000'}}))
    else:
        snap[0] = update_read(snap[0], 'positions', data_json=json.dumps([{'token_id': a.quote.token_id, 'size': '5'}]))
    assert not m.facts() and a.outcome == 'UNKNOWN' and a.maximum == D('3.4')


async def test_real_loop_resumes_repair_and_keeps_merge_and_final_close_strict(env, monkeypatch, tmp_path):
    setup, _, companion, _, old, _, _ = await incident(env, monkeypatch, tmp_path)
    v, e, feed = setup[:3]
    monkeypatch.setattr(recovery, 'WAIT_SEC', .03)
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id)
    loop.executor, loop.production = e, True
    loop.market_deadline = e.monotonic() + 240
    repair = OppositeBAController.repair
    calls = []

    async def fresh_signal(controller, *args):
        calls.append(args)
        absorption(feed, '2')
        result = await repair(controller, *args)
        if result:
            loop.market_deadline = e.monotonic()
        return result

    monkeypatch.setattr(OppositeBAController, 'repair', fresh_signal)
    try:
        await ready(e)
        result = RunResult(e.meta.condition_id, e.session_id)
        await asyncio.wait_for(loop._run_obi_session(result), 8)
        assert calls and len(v.post_requests) == 3
        assert result.execution_status == 'INCOMPLETE' and result.final_close_barrier == 'BLOCKED'
        assert not e.safe_to_continue and old.outcome == 'UNKNOWN'
        assert e.committed() == old.maximum + D('1.5')
        await e.journal.flush()
        assert not any(r['kind'] == 'sm_merge_intent' for r in journal_records(e.journal.path))
    finally:
        await finish(setup, companion)


def late_row(e, attempt):
    return {
        'id': attempt.order_id, 'asset_id': attempt.quote.token_id,
        'market': e.meta.condition_id, 'side': 'BUY', 'price': str(attempt.quote.price),
        'original_size': str(attempt.quote.size), 'size_matched': '0',
        'status': 'CANCELED', 'associate_trades': [],
    }


@pytest.mark.parametrize('old_fills', [False, True])
async def test_new_ba_pairs_then_late_old_terminal_reconciles_actual_reverse_inventory(
    env, monkeypatch, tmp_path, old_fills
):
    setup, c, companion, _, old, _, _ = await incident(env, monkeypatch, tmp_path)
    v, e, feed, lifecycle, normal = setup[:5]
    monkeypatch.setattr(recovery, 'WAIT_SEC', .03)

    def fill_new(venue, oid):
        if oid != old.order_id and venue.rows[oid]['asset_id'] == '2' and not venue.rows[oid]['associate_trades']:
            venue.fill(oid, 'new-ba-filled', 5)
            reflect_positions(venue)

    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        v.on_order_read = fill_new
        absorption(feed, '2')
        result, _ = await c.repair(*e.economic_inventory(), 'new-ba-fills')
        assert result is not None and result.close_barrier == 'BLOCKED'
        assert e.economic_inventory() == (D(5), D(5))
        assert old.outcome == 'UNKNOWN' and not e.safe_to_continue
        assert e.repair_episode is not None and e.trading_state == 'REPAIR'
        assert await normal.enter(*e.economic_inventory(), 'no-new-normal-round') is None
        assert len(v.post_requests) == 3
        v.on_order_read = None
        v.rows[old.order_id] = late_row(e, old)
        if old_fills:
            v.fill(old.order_id, 'old-ba-late-filled', 5)
            reflect_positions(v)
        deadline = time.monotonic() + 5
        while not await e.verify_close(cancel=False):
            assert time.monotonic() < deadline, e.reason
            await asyncio.sleep(.03)
        assert old.outcome == 'VERIFIED' and e.safe_to_continue
        assert not e.normal_unknown.unknowns()
        e.update_repair_episode()
        c.reconcile_episode()
        assert e.economic_inventory() == (D(5), D(10 if old_fills else 5))
        if old_fills:
            assert e.repair_episode is not None and e.trading_state == 'REPAIR'
            assert e.economic_inventory()[0] - e.economic_inventory()[1] == -D(5)
            assert await c.repair(*e.economic_inventory(), 'no-old-signal-on-reverse') is None
        else:
            assert e.repair_episode is None and e.trading_state == 'NORMAL'
        assert len(v.post_requests) == 3
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize('fault', ['cache', 'late_order'])
async def test_prepost_revalidation_revokes_repair_recovery(env, monkeypatch, tmp_path, fault):
    setup, c, companion, _, old, _, _ = await incident(env, monkeypatch, tmp_path)
    v, e, feed, lifecycle = setup[:4]
    monkeypatch.setattr(recovery, 'WAIT_SEC', .03)
    native = e.gateway.send_prepared

    async def changed(*args, **kwargs):
        if fault == 'cache':
            bad = update_read(companion.service.latest(), 'capital', complete=False, data_json=None)
            monkeypatch.setattr(companion.service, 'latest', lambda: bad)
        else:
            e.observed_order_events.add(old.order_id)
        return await native(*args, **kwargs)

    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        monkeypatch.setattr(e.gateway, 'send_prepared', changed)
        absorption(feed, '2')
        await c.repair(*e.economic_inventory(), 'evidence-before-post')
        assert len(v.post_requests) == 2
        assert e.attempts[-1].outcome == 'ABORTED'
        assert old.outcome == 'UNKNOWN' and not e.admission_ready
        assert e.committed() == old.maximum + D('1.5')
    finally:
        await finish(setup, companion)


async def test_positive_old_evidence_after_post_cancels_new_order(env, monkeypatch, tmp_path):
    setup, c, companion, _, old, _, _ = await incident(env, monkeypatch, tmp_path)
    v, e, feed, lifecycle, _, _, client = setup[:7]
    monkeypatch.setattr(recovery, 'WAIT_SEC', .03)
    try:
        await ready(e)
        lifecycle.account = (await e._snapshot(include_books=False))[3]

        native = client._transport.handle_request

        def late_evidence(request):
            response = native(request)
            if request.method == 'POST':
                e.observed_order_events.add(old.order_id)
            return response

        monkeypatch.setattr(client, '_transport', httpx.MockTransport(late_evidence))
        absorption(feed, '2')
        result, _ = await c.repair(*e.economic_inventory(), 'old-evidence-after-post')
        assert result is not None and len(v.post_requests) == 3
        new = e.attempts[-1]
        assert any(new.order_id in ids for ids in v.cancels)
        assert not e.normal_unknown.still_absent({old.order_id})
        assert old.outcome == 'UNKNOWN' and not e.admission_ready
        await e.journal.flush()
        logs = journal_records(e.journal.path)
        assert any(r['kind'] in {'sm_obi_close_requested', 'sm_ba_close_requested'}
                   and r['data']['payload']['reason'] == 'SESSION_ECONOMICS_CHANGED' for r in logs)
    finally:
        await finish(setup, companion)
