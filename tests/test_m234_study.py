"""The existing public M2 collector carries M3/M4 without any account client."""

from decimal import Decimal as D

import pytest

from polymaker.m2_config import M2Config
from polymaker.m2_report import analyze
from polymaker.m2_store import M2Store
from polymaker.m2_study import M2Study, write_report
from polymaker.m234_signal import M234Feed
from test_m2_study import META, ingest, row
from test_m234_signal import shared
from test_rdp_sm_ba import depth, trade
from test_rdp_sm_m1_strategy import advance


def event(strategy='M3', root='root1', **changes):
    return {'strategy': strategy, 'event': 'OBSERVATION', 'wall': 100.,
            'payload': {'id': 'candidate-'+root, 'root': root, 'outcome': 'YES',
                        'price': '.50', 'tick': '.01', 'book_best_ask': '.52',
                        'visible_bid_quantity': '80', 'observation_valid': True,
                        'clock_fresh': True, 'elapsed_lower': 20., 'elapsed_upper': 21., **changes}}


def test_reuse_ledger_limits_are_independent_and_settle_together(tmp_path):
    store = M2Store(tmp_path, M2Config(include_m3_m4=True), 'version1')
    try:
        store.start('run1', META)
        ingest(store, [row()])
        rows = [event(s, root) for s in ('M3', 'M4') for root in ('root1', 'root1', 'root2')]
        store.ingest_extra('run1', META['slug'], rows, minimum=5)
        samples = store.db.execute('SELECT * FROM strategy_samples').fetchall()
        assert len(samples) == 4
        assert sum(r['status'] == 'ASSUMED_FILL' for r in samples) == 2
        assert all(D(r['cost']) <= 5 for r in samples)
        assert sum(r['reason'] == 'DIRECTION_ENTRY_LIMIT_REACHED' for r in samples) == 2
        store.finish('run1', META['slug'], full_window=True, complete=True, counts={})
        store.settle(META['slug'], {'slug': META['slug'], 'winner': 'YES'})
        report = store.report({})
        assert set(report['strategies']) == {'M2', 'M3', 'M4'}
        for summary in report['strategies'].values():
            assert summary['assumed_fills'] == summary['settled_fills'] == 1
            assert summary['settled_pnl'] == '5.0000'
        write_report(tmp_path, report)
        analysis = analyze(tmp_path)
        assert all(s['overall']['settled_assumed_fills'] == 1 for s in analysis['strategies'].values())
    finally:
        store.close()


@pytest.mark.parametrize('changes', [
    {'clock_fresh': False}, {'observation_valid': False}, {'elapsed_lower': 14.9},
    {'elapsed_upper': 240}, {'planned_shutdown': True}, {'visible_bid_quantity': '0'},
    {'price': '.505'}, {'book_best_ask': '.50'},
])
def test_extra_observations_fail_closed(tmp_path, changes):
    store = M2Store(tmp_path, M2Config(include_m3_m4=True), 'v')
    try:
        store.start('run1', META)
        store.ingest_extra('run1', META['slug'], [event(**changes)], minimum=5)
        assert not store.db.execute('SELECT * FROM strategy_samples').fetchall()
        assert store.db.execute('SELECT count(*) FROM strategy_observations').fetchone()[0] == 1
    finally:
        store.close()


def test_real_signal_events_survive_serialization_and_use_existing_store(tmp_path):
    feed, clock, _ = shared()
    advance(feed, clock, .02)
    feed.apply(trade(feed, size='5'))
    advance(feed, clock, .05)
    feed.apply(trade(feed, side='BUY', price='.52', size='10'))
    advance(feed, clock, .10)
    feed.apply(trade(feed, size='5'))
    feed.apply(depth(feed, 75))
    feed.apply(depth(feed, 80))
    feed.hub.observe_new(lambda now: {'elapsed_lower': 20., 'elapsed_upper': 21., 'clock_fresh': True})
    store = M2Store(tmp_path, M2Config(include_m3_m4=True), 'v')
    try:
        store.start('run1', META)
        store.ingest_extra('run1', META['slug'], list(feed.hub.events['M3']), minimum=5)
        assert store.db.execute('SELECT count(*) FROM strategy_samples').fetchone()[0] == 1
    finally:
        store.close()


def test_flag_uses_original_study_with_shared_public_feed(tmp_path):
    study = M2Study(M2Config(include_m3_m4=True), tmp_path, api=object())
    try:
        assert study.feed_factory is M234Feed
        assert study.runtime['real_posts'] == 0
    finally:
        study.store.close()
