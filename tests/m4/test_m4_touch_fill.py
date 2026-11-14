"""Offline observation economics, cutoffs and crash durability; no network."""
# ruff: noqa: ASYNC240 -- Synthetic pytest ledgers only.
import sqlite3
from dataclasses import asdict
from decimal import Decimal as D

import pytest
from m4_helpers import close, op, queue_hit, setup

from polymaker.m4_baseline import settle
from polymaker.m4_fill_model import (
    EVIDENCE_POLICY,
    LEGACY_MODEL,
    LEGACY_POLICY,
    POLICY,
    TouchFillModel,
    finish_observation,
)
from polymaker.m4_state import M4Ledger


def frame(feed, ask, *, stamp=None):
    return {'event_type':'book', 'market':feed.meta.condition_id, 'asset_id':'1',
            'timestamp':str((feed.wall() if stamp is None else stamp)*1000),
            'bids':[{'price':str(D(ask)-D('.01')), 'size':'100'}],
            'asks':[{'price':ask, 'size':'.1'}]}


async def posted(tmp_path, price='.70', policy=LEGACY_POLICY):
    m,e,f,c = await setup(tmp_path, price=price, touch_model=True, fill_policy=policy)
    await m.submit(next(iter(m.pending.values())))
    assert m.ledger.rows()[0]['state'] == 'ACK'
    post = m.ledger.facts('virtual_post')[0]
    assert not m.ledger.facts('virtual_fill')
    assert len(e.reads.fill_model.pending) == 1
    return m,e,f,c,post


@pytest.mark.parametrize('delay,ask,status', [(1,'.69',None), (3,'.68','FILLED'),
    (10,'.67','FILLED'), (10.001,'.68','NOT_TOUCHED')])
async def test_post_not_fill_until_valid_touch_and_inclusive_deadline(tmp_path, delay, ask, status):
    m,e,f,c,p = await posted(tmp_path)
    before = m.ledger.metric()
    try:
        c.now = p['post_monotonic']+delay
        f.apply(frame(f,ask))
        e.reads.fill_model.poll()
        observations = m.ledger.facts('virtual_observation')
        assert (observations[0]['status'] if observations else None) == status
        receipts = m.ledger.facts('virtual_fill')
        assert len(receipts) == (status == 'FILLED')
        if receipts:
            r = receipts[0]
            assert r['model'] == LEGACY_MODEL and r['quote'] == p['quote']
            assert r['quote']['price'] == .70 and r['quote']['size'] == 7.1
            assert r['touch']['after_post_sec'] == pytest.approx(delay)
            # The agreed model fills all shares even with only .1 ask shares.
            assert r['touch']['best_ask_size'] == '0.1'
            c.now += .01
            f.apply(frame(f,ask))
            assert len(m.ledger.facts('virtual_fill')) == 1
        assert m.ledger.metric() == before
    finally:
        await close(m,e)


async def test_no_touch_keeps_risk_direction_and_cannot_fill_after_ten_seconds(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        c.now = p['deadline_monotonic']+.001
        e.reads.fill_model.poll()
        f.apply(frame(f,'.65'))
        assert not m.ledger.facts('virtual_fill')
        assert m.ledger.facts('virtual_observation')[0]['status'] == 'NOT_TOUCHED'
        assert m.ledger.committed() == D('4.97')
        m.ledger.observe(op(999), minimum=5, elapsed=30)
        assert not m.ledger.reserve('df999', spendable=100)
        assert m.ledger.row('df999')['reason'] == 'M4_DIRECTION_OPPORTUNITY_LIMIT'
    finally:
        await close(m,e)


@pytest.mark.parametrize('gap', ['disconnect','stale_depth','malformed_frame','processing_backlog'])
async def test_no_valid_touch_with_gap_is_unfilled_and_keeps_diagnostics(tmp_path, gap):
    m,e,f,c,p = await posted(tmp_path)
    try:
        c.now += 1
        if gap == 'disconnect':
            f.invalidate('SYNTHETIC_DISCONNECT')
            f.connect()
        elif gap == 'stale_depth':
            f.apply(frame(f,'.68',stamp=f.wall()-1))
        elif gap == 'malformed_frame':
            with pytest.raises(ValueError):
                f.apply([frame(f,'.68'), {'event_type':'book'}])
        else:
            f.apply(frame(f,'.68'),received_mono=c.now-.3,received_wall=f.wall())
        assert not m.ledger.facts('virtual_fill')
        c.now = p['deadline_monotonic']+.001
        e.reads.fill_model.poll()
        observation = m.ledger.facts('virtual_observation')[0]
        assert observation['status'] == 'NOT_TOUCHED' and observation['gaps']
        assert observation['reason'] == 'NO_VALID_TOUCH_EVIDENCE'
        assert m.ledger.committed() == D('4.97')
    finally:
        await close(m,e)


async def test_valid_touch_after_a_gap_is_positive_evidence(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        f.invalidate('SYNTHETIC_DISCONNECT')
        f.connect()
        c.now += 2
        f.apply(frame(f,'.68'))
        result = m.ledger.facts('virtual_observation')[0]
        assert result['status'] == 'FILLED' and result['gaps']
    finally:
        await close(m,e)


async def test_gap_no_fill_keeps_session_available_for_later_signals(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        f.invalidate('SYNTHETIC_DISCONNECT')
        f.connect()
        c.now = p['deadline_monotonic'] + .001
        e.reads.fill_model.poll()
        assert m.ledger.facts('virtual_observation')[0]['status'] == 'NOT_TOUCHED'
        assert m.ledger.rows()[0]['state'] == 'ACK'
        assert m.ledger.committed() == D('4.97')

        recovered = frame(f, '.71')
        recovered['bids'][0]['size'] = '100'
        recovered['asks'][0]['size'] = '100'
        f.apply(recovered)
        start = c.now + .02
        queue_hit(f,c,start)
        queue_hit(f,c,start+.60,side='BID')
        queue_hit(f,c,start+1.20,recovery=D('.2'),qty=D(25))
        f.m1.evaluate(f.snapshot())
        later = f.m1.selected
        assert later is not None and later.episode_id != p['opportunity_id']
        assert f.m1.failure is None and e.fatal is None and not e.closed

        await m.refresh_account()
        m.collect()
        await m.submit(later)
        # The recovered signal is evaluated with the original direction quota.
        assert m.ledger.row(later.episode_id)['reason'] == 'M4_DIRECTION_OPPORTUNITY_LIMIT'
        assert sum(row['posts'] for row in m.ledger.rows()) == 1
        assert m.ledger.committed() == D('4.97')
    finally:
        await close(m,e)


@pytest.mark.parametrize('receive_touch', [False, True])
async def test_failed_feed_validator_without_valid_touch_records_no_fill(tmp_path, receive_touch):
    m,e,f,c,p = await posted(tmp_path)
    try:
        f.m1.failure = 'M4_EVENT_RECORDING_FAILED'
        c.now += 1
        if receive_touch:
            f.apply(frame(f,'.68'))
        assert not m.ledger.facts('virtual_fill')
        c.now = p['deadline_monotonic']+.001
        e.reads.fill_model.poll()
        assert m.ledger.facts('virtual_observation')[0]['status'] == 'NOT_TOUCHED'
        assert m.ledger.committed() == D('4.97')
    finally:
        await close(m,e)


@pytest.mark.parametrize('late_source', [False,True])
async def test_prepost_quotes_cannot_confirm_a_fill(tmp_path, late_source):
    m,e,f,c,p = await posted(tmp_path)
    try:
        c.now += .1
        received = c.now if late_source else p['post_monotonic']
        stamp = p['wall']-.01 if late_source else p['wall']
        f.apply(frame(f,'.68',stamp=stamp),received_mono=received,
                received_wall=f.wall())
        assert not m.ledger.facts('virtual_fill')
    finally:
        await close(m,e)


async def test_transient_touch_inside_one_validated_frame_is_retained(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        c.now += 2
        f.apply([frame(f,'.68'),frame(f,'.74')])
        assert f.books['1'].best_ask == D('.74')
        assert m.ledger.facts('virtual_fill')[0]['touch']['best_ask'] == '0.68'
    finally:
        await close(m,e)


async def test_deadline_waits_for_already_received_input_to_drain(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        c.now = p['deadline_monotonic']+.01
        f.m1_pending_received.append(p['deadline_monotonic'])
        e.reads.fill_model.poll()
        assert not m.ledger.facts('virtual_observation')
        f.m1_pending_received.popleft()
        f.apply(frame(f,'.68',stamp=p['deadline_wall']),
                received_mono=p['deadline_monotonic'],received_wall=p['deadline_wall'])
        assert m.ledger.facts('virtual_observation')[0]['status'] == 'FILLED'
    finally:
        await close(m,e)


async def test_tick_is_fixed_at_post_and_threshold_may_be_below_order_floor(tmp_path):
    m,e,f,c,p = await posted(tmp_path,price='.20')
    try:
        assert p['target_price'] == '0.18'
        c.now += 1
        f.apply({'event_type':'tick_size_change','market':f.meta.condition_id,'asset_id':'1',
                 'timestamp':str(f.wall()*1000),'old_tick_size':'.01','new_tick_size':'.001'})
        f.verify_ticks({'1':D('.001')})
        f.apply(frame(f,'.19'))
        assert not m.ledger.facts('virtual_fill')  # Frozen .18, not new .198.
        c.now += .1
        f.apply(frame(f,'.18'))
        assert m.ledger.facts('virtual_fill')[0]['quote']['size'] == 25
    finally:
        await close(m,e)


async def test_restart_does_not_reset_window_or_duplicate_durable_fill(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        before = m.ledger.metric()
        # Replacement observer represents a process whose earlier coverage is lost.
        identity = {k:v for k,v in m.ledger.get('identity').items()
                    if k not in {'limit','order_budget','prepost_policy'}}
        reopened = M4Ledger(m.ledger.path,identity)
        observer = TouchFillModel(f,reopened)
        e.reads.fill_model = f.fill_model = observer
        assert not observer.pending
        assert m.ledger.facts('virtual_observation')[0]['status'] == 'NOT_TOUCHED'
        assert m.ledger.facts('virtual_observation')[0]['reason'] == 'PROCESS_RESTART_OR_INCOMPLETE_WINDOW'
        c.now += 1
        f.apply(frame(f,'.68'))
        assert not m.ledger.facts('virtual_fill')
        e.reads.fill_model = f.fill_model = TouchFillModel(f,m.ledger)
        assert len(m.ledger.facts('virtual_observation')) == 1
        assert m.ledger.metric() == before
        reopened.close()
    finally:
        await close(m,e)


async def test_uncertain_retry_retains_two_observers_and_two_reservations(tmp_path):
    m,e,f,c = await setup(tmp_path,touch_model=True)
    native = e.gateway._client._native_order_post
    calls = []

    def uncertain_once(*args,**kwargs):
        calls.append(1)
        return {'unexpected':'synthetic timeout'} if len(calls) == 1 else native(*args,**kwargs)

    e.gateway._client._native_order_post = uncertain_once
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await m.submit(ep)
        await m.submit(ep)
        assert len(calls) == 2
        assert len(m.ledger.facts('virtual_post')) == 2
        assert m.ledger.committed() == D('9.94')
        c.now += 1
        f.apply(frame(f,'.68'))
        assert len(m.ledger.facts('virtual_fill')) == 2
        assert m.ledger.committed() == D('9.94')
        assert m.ledger.rows()[0]['uncertain_retries'] == 1
    finally:
        await close(m,e)


async def test_fill_and_observation_commit_atomically_and_survive_restart(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        m.ledger.db.executescript("""CREATE TRIGGER fail_fill BEFORE INSERT ON facts
            WHEN NEW.kind='virtual_fill' BEGIN SELECT RAISE(ABORT,'synthetic crash'); END;""")
        c.now += 1
        with pytest.raises(sqlite3.IntegrityError):
            f.apply(frame(f,'.68'))
        assert not m.ledger.facts('virtual_fill') and not m.ledger.facts('virtual_observation')
        m.ledger.db.execute('DROP TRIGGER fail_fill')
        c.now += .01
        f.apply(frame(f,'.68'))
        e.reads.fill_model = f.fill_model = TouchFillModel(f,m.ledger)
        c.now += .01
        f.apply(frame(f,'.68'))
        assert len(m.ledger.facts('virtual_fill')) == 1
        assert m.ledger.facts('virtual_observation')[0]['status'] == 'FILLED'
    finally:
        await close(m,e)


async def test_pending_observation_delays_t240_cleanup_but_not_entry_cutoff(tmp_path):
    m,e,f,c,p = await posted(tmp_path)
    try:
        e.official_time += 220  # Model a POST whose observation straddles T240.
        await m.advance()
        await m.drain()
        assert not m.entry_window_open() and not m.feed_closed
        c.now = p['post_monotonic']+9
        f.apply(frame(f,'.68'))
        await m.advance()
        await m.drain()
        assert m.feed_closed and m.memory_recycled
        assert len(m.ledger.facts('virtual_fill')) == 1
        assert m.ledger.metric()['native_posts'] == 1
    finally:
        await close(m,e)


@pytest.mark.parametrize('status', ['FILLED','NOT_TOUCHED','UNKNOWN'])
@pytest.mark.parametrize('winner', ['YES','NO'])
async def test_settlement_uses_all_posts_and_legacy_unknown_has_no_fill(tmp_path,status,winner):
    m,e,f,c,p = await posted(tmp_path)
    try:
        m.ledger.set('market',asdict(f.meta))
        if status == 'FILLED':
            c.now += 2
            f.apply(frame(f,'.68'))
        elif status == 'UNKNOWN':
            # Durable historical observations retain their original status.
            finish_observation(m.ledger,p,'UNKNOWN',wall=f.wall(),
                               reason='OBSERVATION_GAP',gaps=('DISCONNECTED',))
            e.reads.fill_model = f.fill_model = TouchFillModel(f,m.ledger)
        else:
            c.now = p['deadline_monotonic']+.001
            e.reads.fill_model.poll()
        result = settle(m.ledger.path,m.ledger.get('identity'),{'slug':f.meta.slug,
            'condition_id':f.meta.condition_id,'token_ids':{'YES':'1','NO':'2'},'winner':winner})
        instant = D('2.13') if winner == 'YES' else D('-4.97')
        assert result['model'] == LEGACY_MODEL
        assert D(result['modeled_pnl_usdc']) == (instant if status == 'FILLED' else 0)
        comparison = result['instant_fill_comparison']
        assert D(comparison['modeled_pnl_usdc']) == instant
        assert result['observation_counts']['confirmed_fills' if status == 'FILLED' else 'not_touched'] == 1
        assert result['observation_counts']['unknown'] == 0
        assert result['observation_counts']['legacy_unknown'] == (status == 'UNKNOWN')
        assert result['fill_evidence_policy'] == EVIDENCE_POLICY
        assert result['pnl_completeness'] == 'COMPLETE'
        assert comparison['comparable_posts'] == 1
        assert D(comparison['touch_minus_instant_all_posts_usdc']) == (0 if status == 'FILLED' else -instant)
        if status == 'UNKNOWN':
            assert m.ledger.facts('virtual_observation')[0]['status'] == 'UNKNOWN'
            assert e.reads.fill_model.summary()['not_touched'] == 1
            assert e.reads.fill_model.summary()['unknown'] == 0
        assert m.ledger.committed() == D('4.97')
    finally:
        await close(m,e)


def test_live_identity_rejects_baseline_model(tmp_path):
    from m4_helpers import ledger
    b = ledger(tmp_path,'LIVE')
    try:
        with pytest.raises(ValueError,match='REQUIRES_VIRTUAL'):
            b.initialize_session('synthetic',{}, {},baseline_fill_model=POLICY)
        assert b.get('baseline_fill_model') is None
    finally:
        b.close()


@pytest.mark.parametrize('at', ['later', 'cutoff', 'after_cutoff'])
async def test_one_tick_touch_until_t240_and_simulated_cancel(tmp_path, at):
    m,e,f,c,p = await posted(tmp_path,policy=POLICY)
    try:
        assert p['target_price'] == '0.69'
        assert p['deadline_monotonic']-p['post_monotonic'] > 10
        c.now = (p['post_monotonic']+60 if at == 'later' else
                 p['deadline_monotonic']+(0 if at == 'cutoff' else .001))
        f.apply(frame(f,'.69'))
        e.reads.fill_model.poll()
        if at == 'after_cutoff':
            result=m.ledger.facts('virtual_observation')[0]
            assert result['status']=='NOT_TOUCHED'
            assert result['reason']=='T240_NO_VALID_TOUCH_EVIDENCE'
            assert not m.ledger.facts('virtual_fill')
            canceled=m.ledger.facts('virtual_cancel')
            assert len(canceled)==1 and canceled[0]['real_cancel_sent'] is False
            assert canceled[0]['effective_wall']==p['deadline_wall']
            f.apply(frame(f,'.68'))
            assert not m.ledger.facts('virtual_fill')
        else:
            assert len(m.ledger.facts('virtual_fill'))==1
            assert m.ledger.facts('virtual_fill')[0]['quote']==p['quote']
            assert not m.ledger.facts('virtual_cancel')
        assert m.ledger.rows()[0]['state']=='ACK'
        assert m.ledger.committed()==D('4.97')
        assert sum(row['posts'] for row in m.ledger.rows())==1
    finally:
        await close(m,e)


async def test_new_cutoff_cancel_is_durable_and_restart_retains_risk(tmp_path):
    m,e,f,c,p = await posted(tmp_path,policy=POLICY)
    try:
        f.invalidate('SYNTHETIC_GAP')
        c.now=p['deadline_monotonic']+.01
        e.reads.fill_model.poll()
        original=m.ledger.facts('virtual_cancel')
        assert len(original)==1
        e.reads.fill_model=f.fill_model=TouchFillModel(f,m.ledger)
        e.reads.fill_model.poll()
        assert m.ledger.facts('virtual_cancel')==original
        assert m.ledger.committed()==D('4.97')
        assert m.ledger.rows()[0]['state']=='ACK'
    finally:
        await close(m,e)
