"""Operational counts preserve signal, scenario, partial-fill and run identity boundaries."""

import json
import sqlite3

from ops.m1234_sim_metrics import collect


def market_fixture(tmp_path):
    run = tmp_path / 'run'
    market = run / 'market'
    market.mkdir(parents=True)
    owner = {'run_id': 'current'}
    (run / 'plan.json').write_text(json.dumps({'run_identity': owner}))
    path = market / 'simulation.sqlite3'
    binding = {'database_id': 'database-1', 'market': 'market'}
    tracker = market / 'database-1.validity.json'
    tracker.write_text(json.dumps({'binding': binding, 'evidence_schema': 1,
                                   'validity': {'status': 'TRACKING'}}))
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE audit(seq INTEGER PRIMARY KEY, input_seq INT, kind TEXT, payload TEXT);'
                         'CREATE TABLE metadata(name TEXT PRIMARY KEY, payload TEXT);')
        db.executemany('INSERT INTO metadata VALUES (?,?)',
                       [('identity', json.dumps(binding)), ('run_owner', json.dumps(owner))])
    return run, path, tracker, tmp_path / 'cache.json'


def append(path, rows):
    with sqlite3.connect(path) as db:
        db.executemany('INSERT INTO audit(input_seq,kind,payload) VALUES (1,?,?)',
                       [(kind, json.dumps(payload)) for kind, payload in rows])


def candidate(strategy, key):
    detail = ({'opportunity': {'episode_id': key}} if strategy == 'M1' else
              {'candidate_id': key} if strategy == 'M2' else {'id': key})
    return 'SIGNAL', {'strategy': strategy, 'payload': detail,
                      'event': {'M1': 'OPPORTUNITY_IDENTIFIED', 'M2': 'M2_CANDIDATE'}.get(strategy, 'CANDIDATE')}


def submit(scenario='trades-only', profile='M1-virtual'):
    return 'SUBMIT', {'order': 'order-1', 'scenario': scenario,
                      'intent': {'strategy': 'M1', 'profile': profile}}


def fill(scenario='trades-only'):
    return 'MODELLED_FILL', {'order': 'order-1', 'scenario': scenario, 'quantity': '0.25'}


def test_candidates_are_unique_and_partial_fills_are_one_order_per_scenario(tmp_path):
    run, path, _, cache = market_fixture(tmp_path)
    append(path, [candidate(s, 'same-id') for s in ('M1', 'M2', 'M3', 'M4')] +
           [candidate('M1', 'same-id'), submit(), fill(), fill(), submit('cancel-half'), fill('cancel-half')])
    report = collect(run, cache)
    assert report['status'] == 'OK'
    assert [row['signals'] for row in report['strategies'].values()] == [1, 1, 1, 1]
    assert report['strategies']['M1'] == {'signals': 1, 'submitted_orders': 2, 'filled_orders': 2,
                                         'fill_events': 3, 'filled_shares': '0.75', 'unresolved_orders': 0}
    assert report['by_profile_scenario']['M1/M1-virtual/trades-only']['filled_orders'] == 1


def test_incremental_watermark_and_uncommitted_tail(tmp_path):
    run, path, _, cache = market_fixture(tmp_path)
    append(path, [candidate('M2', 'first')])
    first = collect(run, cache)
    with sqlite3.connect(path) as writer:
        writer.execute('INSERT INTO audit(input_seq,kind,payload) VALUES(2,?,?)',
                       ('SIGNAL', json.dumps(candidate('M2', 'second')[1])))
        assert collect(run, cache)['strategies'] == first['strategies']
    second = collect(run, cache)
    assert second['strategies']['M2']['signals'] == 2
    assert second['sources'][0]['audit_seq'] == 2
    assert collect(run, cache)['strategies'] == second['strategies']


def test_invalid_or_missing_tracker_is_explicitly_excluded(tmp_path):
    run, path, tracker, cache = market_fixture(tmp_path)
    append(path, [candidate('M4', 'one')])
    assert collect(run, cache)['strategies']['M4']['signals'] == 1
    value = json.loads(tracker.read_text())
    value['validity']['status'] = 'INVALID'
    tracker.write_text(json.dumps(value))
    report = collect(run, cache)
    assert report['status'] == 'PARTIAL' and report['excluded_invalid_markets'] == ['market']
    assert report['strategies']['M4']['signals'] == 0
    tracker.unlink()
    report = collect(run, cache)
    assert report['status'] == 'PARTIAL' and report['errors'][0]['market'] == 'market'


def test_foreign_run_and_unjoined_fill_are_errors_not_zero(tmp_path):
    run, path, _, cache = market_fixture(tmp_path)
    append(path, [fill()])
    assert collect(run, cache)['status'] == 'PARTIAL'
    assert json.loads(cache.read_text())['markets'] == {}
    with sqlite3.connect(path) as db:
        db.execute('UPDATE metadata SET payload=? WHERE name=?',
                   (json.dumps({'run_id': 'different'}), 'run_owner'))
    assert 'METRICS_RUN_IDENTITY_MISMATCH' in collect(run, cache)['errors'][0]['error']


def test_cache_does_not_mix_runs_or_prepost_databases(tmp_path):
    run, path, _, cache = market_fixture(tmp_path)
    append(path, [candidate('M1', 'one')])
    prepost = path.parent / 'prepost'
    prepost.mkdir()
    (prepost / 'simulation.sqlite3').write_text('not a simulation database')
    assert collect(run, cache)['market_databases'] == 1
    next_run = tmp_path / 'next-run'
    next_run.mkdir()
    (next_run / 'plan.json').write_text(json.dumps({'run_identity': {'run_id': 'next'}}))
    assert collect(next_run, cache)['strategies']['M1']['signals'] == 0
    assert json.loads(cache.read_text())['markets'] == {}


def test_uncertainty_is_counted_once_and_migrates_old_cache(tmp_path):
    run, path, _, cache = market_fixture(tmp_path)
    append(path, [submit()])
    assert collect(run, cache)['strategies']['M1']['unresolved_orders'] == 0
    uncertain = {'order': 'order-1', 'scenario': 'trades-only'}
    append(path, [('ORDER_UNCERTAIN', uncertain), ('UNKNOWN_TAIL', uncertain)])
    report = collect(run, cache)
    assert report['strategies']['M1']['unresolved_orders'] == 1
    assert report['strategies']['M1']['filled_orders'] == 0
    # Earlier caches advanced over uncertainty without retaining it. Rebuild them.
    old = json.loads(cache.read_text())
    old['schema'] = 1
    for market in old['markets'].values():
        for order in market['orders'].values():
            del order['unresolved']
    cache.write_text(json.dumps(old))
    assert collect(run, cache)['strategies']['M1']['unresolved_orders'] == 1
    append(path, [fill()])
    report = collect(run, cache)
    assert report['strategies']['M1']['filled_orders'] == 1
    assert report['strategies']['M1']['unresolved_orders'] == 1


def test_through_cache_without_submitted_quantity_rebuilds_before_new_fill(tmp_path):
    run, path, _, cache = market_fixture(tmp_path)
    row = submit('research/trade-through')
    row[1]['intent']['quantity'] = '0.50'
    uncertain = {'order': 'order-1', 'scenario': 'research/trade-through'}
    append(path, [row, ('ORDER_UNCERTAIN', uncertain), fill('research/trade-through')])
    first = collect(run, cache)
    assert first['strategies']['M1']['unresolved_orders'] == 1
    old = json.loads(cache.read_text())
    for state in old['markets'].values():
        for order in state['orders'].values():
            del order['submitted_quantity']
    cache.write_text(json.dumps(old))
    append(path, [fill('research/trade-through')])
    result = collect(run, cache)
    assert result['status'] == 'OK'
    assert result['strategies']['M1']['filled_orders'] == 1
    assert result['strategies']['M1']['filled_shares'] == '0.50'
    assert result['strategies']['M1']['fill_events'] == 2
    assert result['strategies']['M1']['unresolved_orders'] == 0
