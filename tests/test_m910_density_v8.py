"""Target-local changes preserve causal evidence and shared primitives."""
from types import SimpleNamespace
import gc
import weakref

import pytest
from m510_frequency_grid import attach_profiles
from maker_signals.model import D
from maker_signals.strategies import M5_M10_LOCAL_AP, M910_DENSITY, Strategies, resolve_frequency_parameters

from test_m511_feed import Tape, play_m10_book_path
from test_m511_strategies import H
from test_m510_phase3 import play_private_m9


def manager(h, **parameters):
    h.s = Strategies(h.c, signal_variant=M910_DENSITY, parent_filters=False,
                     enabled_strategies=['M9', 'M10'], frequency_parameters={'M9': {
                         'private_ex': True, 'ex_total_fraction': '.40',
                         'ex_bin_fraction': '.25', **parameters}})
    return h.s


def volume(h, at, quantity):
    h.c.flows['up'].trades.append(SimpleNamespace(received=at, available=at,
            input_seq=h.c.seq+1, subindex=0, side='SELL', quantity=D(str(quantity))))


def seed(h):
    manager(h)
    ap = h.ap('SELL', volume=10)
    r = h.rnd(.1, recovery='.6')
    h.s.on_round(r)
    volume(h, .2, 1.5)
    volume(h, .45, 1.5)
    return ap


def test_m9_local_ex_can_confirm_weaker_but_still_decaying_pressure_and_fresh_retest():
    h = H()
    ap = seed(h)
    h.at(.75, tick=True)
    p = h.s.active('M9', 'up')
    assert p is not None and not h.signals('M9')
    ex = p.data['seed_ex']
    assert ex.kind == 'EX_SELL_M9_PRIVATE'
    assert ex.refs == (ap.id,)
    assert sum(ex.values['subwindow_volumes']) == 3
    assert ex.values['total_fraction'] == '.40' or D(ex.values['total_fraction']) == D('.40')
    h.at(.8)
    h.s.retest_qualified(p, SimpleNamespace(minimum=D('.5')))
    r = h.rnd(.95, start=.8, hit=1, loss=1, recovery='.4')
    h.s.retest_round(p, r)
    assert len(h.signals('M9')) == 1


def test_private_ex_does_not_consume_the_public_ex_fact():
    h = H()
    manager(h)
    ap = h.ap('SELL')
    h.s.on_round(h.rnd(.1))
    h.fact('EX_SELL', refs=[ap.id], ttl=.75, dispatch=True,
           values={'volume': D(10)})
    assert h.s.active('M9', 'up') is None


@pytest.mark.parametrize('failure', ['gap', 'coverage', 'reverse'])
def test_private_ex_rejects_missing_evidence_or_reverse_attack(failure):
    h = H()
    seed(h)
    if failure == 'gap':
        h.c.unavailable('up', 'GAP')
    elif failure == 'coverage':
        h.c.flows['up'].covered = lambda *_: False
    else:
        h.at(.3)
        h.ap('BUY')
    h.at(.75, tick=True)
    assert h.s.active('M9', 'up') is None
    assert not any(f.kind == 'EX_SELL_M9_PRIVATE' for f in h.c.registry.facts.values())


def test_resumed_sell_pressure_invalidates_private_ex_and_no_expiry_renewal():
    h = H()
    seed(h)
    h.at(.75, tick=True)
    p = h.s.active('M9', 'up')
    ex = p.data['seed_ex']
    deadline = ex.deadline
    volume(h, .8, 2)
    h.at(.8, tick=True)
    assert h.c.registry.endings[ex.id]['reason'] == 'EX_PRESSURE_RESUMED'
    assert p.closed and ex.deadline == deadline
    h.at(1.6, tick=True)
    assert len([f for f in h.c.registry.facts.values() if f.kind == 'EX_SELL_M9_PRIVATE']) == 1


@pytest.mark.parametrize('resume', ['.65','.75'])
def test_private_retest_resumption_is_frozen_and_still_strictly_weaker(resume):
    h = H()
    manager(h, ex_resume_fraction=resume, seed_ex_ttl_seconds=3)
    h.ap('SELL',volume=10)
    h.s.on_round(h.rnd(.1,recovery='.6'))
    volume(h,.2,1.5)
    volume(h,.45,1.5)
    h.at(.75,tick=True)
    p = h.s.active('M9','up')
    assert p is not None
    source = p.data['seed_ex_source']
    deadline = p.data['seed_ex'].deadline
    assert D(source.values['pressure_resume_fraction'])==D(resume)
    assert D(p.data['seed_ex'].values['pressure_resume_fraction'])==D(resume)
    volume(h,.8,3)
    h.at(.8,tick=True)
    # Formation was 3/10, now6/10; the explicitly relaxed retest remains
    # below65/75percent. Neither the seed identity nor its deadline renews.
    assert not p.closed and p.data['seed_ex'].deadline==deadline
    volume(h,.81,D(resume)*10-D(6))
    h.at(.81,tick=True)
    assert p.closed
    assert h.c.registry.endings[source.id]['reason']=='EX_PRESSURE_RESUMED'
    assert p.data['seed_ex'].deadline==deadline


def test_extended_local_seed_keeps_its_resume_threshold_after_public_soft_ttl():
    h = H()
    manager(h, ex_resume_fraction='.65',seed_ex_ttl_seconds=3)
    h.ap('SELL',volume=10)
    h.s.on_round(h.rnd(.1,recovery='.6'))
    h.at(.75,tick=True)
    p = h.s.active('M9','up')
    h.at(1.6,tick=True)
    assert not p.closed
    volume(h,1.7,6)
    h.at(1.7,tick=True)
    assert not p.closed
    volume(h,1.71,.5)
    h.at(1.71,tick=True)
    assert p.closed
    assert h.c.registry.endings[p.data['seed_ex'].id]['reason']=='EX_PRESSURE_RESUMED'


@pytest.mark.parametrize('values', [
    {'ex_resume_fraction':'.49','private_ex':True},
    {'ex_resume_fraction':'.76','private_ex':True},
    {'ex_resume_fraction':'1','private_ex':True},
    {'ex_resume_fraction':'NaN','private_ex':True},
    {'ex_resume_fraction':'.65','private_ex':False},
])
def test_resume_threshold_cannot_remove_weakness_or_change_shared_ex(values):
    with pytest.raises(ValueError):
        resolve_frequency_parameters(M910_DENSITY,{'M9':values})


def test_relaxed_retest_still_needs_strong_initial_exhaustion_and_source_quality():
    h = H()
    manager(h,ex_resume_fraction='.75')
    h.ap('SELL',volume=10)
    h.s.on_round(h.rnd(.1))
    for at in (.2,.45,.7):
        volume(h,at,1.5)
    h.at(.75,tick=True)
    assert h.s.active('M9','up') is None  # 4.5/10 exceeds the40percent formation limit.
    assert not any(f.kind=='EX_SELL_M9_PRIVATE' for f in h.c.registry.facts.values())


@pytest.mark.parametrize('values', [
    {'ex_total_fraction': '.50'}, {'ex_total_fraction': 'NaN'},
    {'ex_total_fraction': '.40', 'ex_bin_fraction': '.45'}, {'private_ex': 1},
])
def test_private_ex_requires_at_least_half_pressure_decay_and_valid_scope(values):
    with pytest.raises(ValueError):
        resolve_frequency_parameters(M910_DENSITY, {'M9': values})


def test_longer_return_window_is_explicit_and_never_changes_old_or_shared_defaults():
    with pytest.raises(ValueError):
        resolve_frequency_parameters(M5_M10_LOCAL_AP, {'M10': {'round_seconds': 1}})
    p = resolve_frequency_parameters(M910_DENSITY, {'M10': {'round_seconds': 1,
                                                          'stable_recovery': '.20'}})
    assert p['M10']['round_seconds'] == 1
    tape = Tape()
    s = Strategies(tape.feed, signal_variant=M910_DENSITY,
                   enabled_strategies=['M10'], frequency_parameters={'M10': p['M10']})
    assert tape.feed.refills['up'].observer.window_seconds == .5
    assert tape.feed.refills['up'].observer.stable_recovery == D('.50')


def test_shadow_local_ex_and_direct_manager_have_the_same_causal_signal():
    a, b = H(), H()
    manager(a)
    primary = Strategies(b.c, signal_variant=M910_DENSITY, parent_filters=False,
                         enabled_strategies=['M9', 'M10'])
    shadows = attach_profiles(b.c, primary, {'base': {}, 'local': {'M9': {
        'private_ex': True, 'ex_total_fraction': '.40', 'ex_bin_fraction': '.25'}}},
        'base', profile_targets={'local': ['M9']})
    b.s = b.c.strategies
    for h in (a, b):
        ap = h.ap('SELL', volume=10)
        h.s.on_round(h.rnd(.1))
        volume(h, .2, 1.5)
        volume(h, .45, 1.5)
        h.at(.75, tick=True)
    shadow = shadows['local']
    assert shadow.active('M9', 'up') is not None
    assert a.s.active('M9', 'up') is not None
    assert primary.active('M9', 'up') is None
    assert a.c.good == b.c.good
    assert any(p['event']=='CAMPAIGN_STARTED' and
               p['payload'].get('calibration_profile')=='local'
               for kind,p in b.events if kind=='RESEARCH_EVIDENCE')
    for h, m in ((a, a.s), (b, shadow)):
        p = m.active('M9', 'up')
        h.at(.8)
        m.retest_qualified(p, SimpleNamespace(minimum=D('.5')))
        m.retest_round(p, h.rnd(.95, start=.8, hit=1, loss=1, recovery='.4'))
        h.s.publish()
    def physical(c, m):
        return sorted((c.registry.facts[i].confirmed, c.registry.facts[i].deadline,
                       c.registry.facts[i].anchor) for i in m.published)
    assert physical(a.c, a.s) == physical(b.c, shadow)


@pytest.mark.parametrize('resume', ['.50','.65','.75'])
def test_shadow_and_direct_match_when_new_retest_volume_exceeds_old_resume_threshold(resume):
    a,b = H(),H()
    manager(a,ex_resume_fraction=resume,seed_ex_ttl_seconds=3)
    primary = Strategies(b.c,signal_variant=M910_DENSITY,parent_filters=False,
                         enabled_strategies=['M9','M10'])
    shadows = attach_profiles(b.c,primary,{'base':{},'local':{'M9':{
        'private_ex':True,'ex_total_fraction':'.40','ex_bin_fraction':'.25',
        'ex_resume_fraction':resume,'seed_ex_ttl_seconds':3}}},'base',
        profile_targets={'local':['M9']})
    b.s = b.c.strategies
    for h,m in ((a,a.s),(b,shadows['local'])):
        h.ap('SELL',volume=10)
        h.s.on_round(h.rnd(.1))
        volume(h,.2,1.5)
        volume(h,.45,1.5)
        h.at(.75,tick=True)
        p = m.active('M9','up')
        assert p is not None
        volume(h,.8,3)  # Rolling6/10 crosses the old50percent threshold.
        h.at(.8,tick=True)
        if resume=='.50':
            assert p.closed
        else:
            assert not p.closed
            m.retest_qualified(p,SimpleNamespace(minimum=D('.5')))
            m.retest_round(p,h.rnd(.95,start=.8,hit=1,loss=1,recovery='.4'))
        h.s.publish()
    def physical(c,m):
        return sorted((c.registry.facts[i].token,c.registry.facts[i].confirmed,
                       c.registry.facts[i].deadline,c.registry.facts[i].anchor)
                      for i in m.published)
    assert physical(a.c,a.s)==physical(b.c,shadows['local'])
    assert len(a.signals('M9'))==(0 if resume=='.50' else 1)


@pytest.mark.parametrize('resume', ['.50','.65','.75'])
def test_new_local_parameters_preserve_shared_public_ap_rf_and_quality(resume):
    a, b = Tape(), Tape()
    Strategies(a.feed, signal_variant=M5_M10_LOCAL_AP, parent_filters=False,
               enabled_strategies=['M5', 'M6', 'M9', 'M10'])
    Strategies(b.feed, signal_variant=M910_DENSITY, parent_filters=False,
               enabled_strategies=['M5', 'M6', 'M9', 'M10'], frequency_parameters={'M9': {
                   'private_ex': True, 'ex_total_fraction': '.40', 'ex_bin_fraction': '.25',
                   'ex_resume_fraction':resume}})
    for t in (a, b):
        play_m10_book_path(t)
    def public(t):
        return sorted((f.kind, f.token, f.start, f.end, f.confirmed, f.deadline, f.anchor)
                      for f in t.feed.registry.facts.values()
                      if f.kind in {'AP_BUY', 'AP_SELL', 'EX_SELL', 'EX_BUY', 'RF_BID'})
    assert public(a) == public(b)
    assert a.feed.good == b.feed.good and a.feed.reasons == b.feed.reasons


@pytest.mark.parametrize('failure', [None, 'no_fresh_sell', 'no_recovery'])
def test_raw_private_ex_requires_a_real_fresh_stable_retest(failure):
    class Altered(Tape):
        def trade(self, at, *args, **kwargs):
            if failure == 'no_fresh_sell' and at == 1.52:
                return
            super().trade(at, *args, **kwargs)

        def books(self, at, **kwargs):
            if failure == 'no_recovery' and at >= 1.53:
                kwargs['bid'] = 90
            super().books(at, **kwargs)

    t = Altered()
    s = Strategies(t.feed, signal_variant=M910_DENSITY, parent_filters=False,
        enabled_strategies=['M9'], frequency_parameters={'M9': {
            'private_seed': True, 'seed_round_seconds': .25, 'seed_hold_seconds': .025,
            'seed_stable_recovery': '.40', 'private_ex': True,
            'ex_total_fraction': '.40', 'ex_bin_fraction': '.25'}})
    play_private_m9(t)
    signals = [p['payload'] for k, p in t.rows if k == 'SIGNAL' and p['strategy'] == 'M9']
    assert len(signals) == (1 if failure is None else 0)
    if signals:
        p = s.active('M9', 'up')
        assert p.data['seed_ex'].kind == 'EX_SELL_M9_PRIVATE'
        assert p.rounds[0].start > p.data['seed_ex'].confirmed
        assert p.data['first_qualified'] < p.data['seed_deadline']


@pytest.mark.parametrize('failed_first', [False, True])
def test_raw_one_second_partial_refill_still_requires_two_returns_without_skipping(failed_first):
    t = Tape()
    s = Strategies(t.feed, signal_variant=M910_DENSITY, parent_filters=False,
        enabled_strategies=['M10'], frequency_parameters={'M10': {
            'ap_window_seconds': .75, 'round_seconds': 1,
            'hold_seconds': .01, 'stable_recovery': '.20'}})
    trades = {1: ('BUY', 10), 27: ('BUY', 10), 53: ('BUY', 10),
              80: ('SELL', 10), 200: ('SELL', 8), 320: ('SELL', 6)}
    changes = {82: 90, 120: 90 if failed_first else 92, 190: 100,
               202: 92, 240: '93.6', 310: 100, 322: 94, 360: '95.2'}
    bid = 100
    for i in range(426):
        at = i/100
        if i in changes:
            bid = changes[i]
        if i % 5 == 0 or i in changes:
            t.books(at, bid=bid)
        if i in trades:
            t.trade(at, *trades[i])
        t.tick(at)
    signals = [p['payload'] for k, p in t.rows if k == 'SIGNAL' and p['strategy'] == 'M10']
    assert len(signals) == (0 if failed_first else 1)
    if signals:
        p = s.active('M10', 'up')
        assert p is not None
        assert len(p.rounds) == 2
        assert all(r.values['window_seconds'] == 1 for r in p.rounds)
        assert p.rounds[1].values['hit'] < p.rounds[0].values['hit']
        assert p.rounds[1].values['u'] < p.rounds[0].values['u']
        assert all(r.values['recovery'] == D('.20') for r in p.rounds)


def test_retirement_releases_strategy_local_ex_callbacks_without_global_collection():
    t = Tape()
    s = Strategies(t.feed, signal_variant=M910_DENSITY, parent_filters=False,
                   enabled_strategies=['M9','M10'], frequency_parameters={'M9': {
                       'private_ex':True,'ex_total_fraction':'.40','ex_bin_fraction':'.25'}})
    references = [weakref.ref(x) for x in s.private_ex.values()]
    manager_ref = weakref.ref(s)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        t.feed.release_runtime()
        del s
        assert all(r() is None for r in references)
        assert manager_ref() is None
    finally:
        if was_enabled:
            gc.enable()
