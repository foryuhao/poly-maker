"""Forward-only, independent M4 frequency research on shared round evidence."""
from decimal import Decimal as D

import pytest

from polymaker.m2_config import M2Config
from polymaker.m2_store import M2Store
from polymaker.m2_report import analyze
from polymaker.m2_study import source_fingerprint
from test_m234_signal import shared, queue_hit
from test_m234_study import event
from test_m2_study import META
from test_rdp_sm_m1_strategy import advance


def candidate_fixture():
    feed, clock, _ = shared(frequency=True)
    queue_hit(feed, clock, .02)
    queue_hit(feed, clock, .60, side='BID')
    queue_hit(feed, clock, 1.20, recovery=D('.2'))
    return feed, clock


def test_frequency_definition_replaces_baseline_and_uses_one_bid_round():
    feed, clock = candidate_fixture()
    candidates = feed.hub.candidates()
    new = next(c for c in candidates if c['strategy'] == 'M4')
    assert len([c for c in candidates if c['strategy'] == 'M4']) == 1
    assert feed.hub.m4.features_only
    assert new['definition'] == 'm4-frequency-v2'
    assert new['price'] == D('.50')
    assert new['deadline'] == pytest.approx(6.70)
    assert feed.hub.active(new)
    assert feed.hub.counts['M4:RF_BID_CONFIRMED'] == 0
    assert feed.hub.counts['M4:BID_SUPPORT_CONFIRMED'] == 1
    assert not feed.hub.failures
    advance(feed, clock, new['deadline']+.025)
    assert not feed.hub.m4_v2.candidates


def test_normal_ask_recovery_is_not_defense_decay():
    feed, clock, _ = shared(frequency=True)
    queue_hit(feed, clock, .02)
    queue_hit(feed, clock, .60, side='BID')
    queue_hit(feed, clock, 1.20, recovery=D('.9'))
    assert not feed.hub.m4_v2.candidates
    assert feed.hub.counts['M4:DF_ASK_CONFIRMED'] == 0


def test_m3_candidate_is_identical_with_frequency_observer_enabled():
    from test_rdp_sm_ba import depth, trade
    result = []
    for enabled in (False, True):
        feed, clock, _ = shared(frequency=enabled)
        advance(feed, clock, .02)
        feed.apply(trade(feed, size='5'))
        advance(feed, clock, .05)
        feed.apply(trade(feed, side='BUY', price='.52', size='10'))
        advance(feed, clock, .10)
        feed.apply(trade(feed, size='5'))
        feed.apply(depth(feed, 75))
        feed.apply(depth(feed, 80))
        feed.hub.tick()
        result.append(feed.hub.m3.candidates['1'])
    assert result[0] == result[1]


def test_queue_disappearance_invalidates_frequency_evidence_too():
    feed, _ = candidate_fixture()
    assert feed.hub.m4_v2.candidates
    feed.hub.m4.invalidate('INPUT_GAP', '1')
    assert not feed.hub.m4_v2.candidates
    assert not feed.hub.m4_v2.states


def test_ask_recovery_cancels_pending_frequency_candidate():
    feed, clock = candidate_fixture()
    queue_hit(feed, clock, 1.80, recovery=D(1))
    assert not feed.hub.m4_v2.candidates


def test_frequency_observer_error_leaves_other_observers_running():
    feed, clock, _ = shared(frequency=True)
    feed.hub.m4_v2.ingest = lambda *args: 1/0
    queue_hit(feed, clock, .02)
    assert feed.hub.failures == {'M4_V2': 'ZeroDivisionError'}
    assert feed.m1.failure is None
    assert feed.hub.m3.failure is None
    assert feed.hub.counts['M4:ROUND_CLOSED'] == 1


def test_shared_observation_ledger_limits_and_official_settlement(tmp_path):
    cfg = M2Config(include_m3_m4=True, m4_frequency_study=True)
    feed, _ = candidate_fixture()
    feed.hub.observe_new(lambda now: {'elapsed_lower': 20., 'elapsed_upper': 21.,
                                    'clock_fresh': True, 'planned_shutdown': False})
    store = M2Store(tmp_path, cfg, 'frequency-test')
    try:
        store.start('run', META)
        events = list(feed.hub.events['M4'])
        store.ingest_extra('run', META['slug'], events, minimum=5)
        store.ingest_extra('run', META['slug'], events, minimum=5)
        first = store.db.execute('SELECT * FROM strategy_samples').fetchall()
        assert len(first) == 1 and first[0]['strategy'] == 'M4'
        assert D(first[0]['cost']) <= 5
        store.ingest_extra('run', META['slug'], [event('M4', 'second-root')], minimum=5)
        second = store.db.execute('SELECT * FROM strategy_samples ORDER BY id').fetchall()
        assert second[1]['reason'] == 'DIRECTION_ENTRY_LIMIT_REACHED'
        assert second[1]['status'] == 'SKIPPED'
        store.finish('run', META['slug'], full_window=True, complete=True, counts={})
        store.settle(META['slug'], {'slug': META['slug'], 'winner': 'YES'})
        report = store.report({})
        assert set(report['strategies']) == {'M2', 'M3', 'M4'}
        assert report['strategies']['M4']['settled_fills'] == 1
        assert analyze(tmp_path)['strategies']['M4']['overall']['settled_assumed_fills'] == 1
    finally:
        store.close()


def test_frequency_source_and_flag_are_versioned():
    strict = M2Config(include_m3_m4=True)
    frequency = M2Config(include_m3_m4=True, m4_frequency_study=True)
    old_version, old_sources = source_fingerprint(strict)
    new_version, new_sources = source_fingerprint(frequency)
    assert old_version != new_version
    assert 'm4_frequency_signal.py' not in old_sources
    assert 'm4_frequency_signal.py' in new_sources
    assert strict.signal == frequency.signal
    with pytest.raises(ValueError, match='M4_FREQUENCY_REQUIRES'):
        M2Config(m4_frequency_study=True)
