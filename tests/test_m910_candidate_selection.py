"""Frequency decisions must reject partial evidence and never select on PnL."""
from collections import Counter
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def selection_case(monkeypatch):
    base = Path(__file__).resolve().parents[1]/'research_outputs/analysis/m910_density_v8_20261113'
    monkeypatch.syspath_prepend(str(base))
    spec = importlib.util.spec_from_file_location('candidate_selection_test', base/'prepare_candidate_selection.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rule = {'development_markets': 2, 'development_goal_each': 3, 'full288_goal_each': 116,
            'priority': {'M9': [{'stage': 'stage2', 'profile': 'm9'},
                               {'stage': 'stage4', 'profile': 'm9'}],
                         'M10': [{'stage': 'stage2', 'profile': 'm10'}]}}
    def signal(strategy, ident, at=1):
        return {'strategy': strategy, 'id': ident, 'token': 'token',
                'confirmed': at, 'deadline': at+1, 'anchor_price': '.5'}
    rows = {}
    for stage in ('stage2', 'stage4'):
        rows[stage] = []
        for i in range(2):
            profiles = {'baseline_v8': {'signals': [signal('M5', 'base'+str(i))]},
                        'm9': {'signals': [signal('M9', stage+'m9'+str(i))],
                               'parameters': {'M9': {'window': .25}}}}
            if stage == 'stage2':
                profiles['m10'] = {'signals': [signal('M10', 'm10')] if i == 0 else [],
                                   'parameters': {'M10': {'window': .5}}}
            for p in profiles.values():
                p['counts'] = dict(Counter(s['strategy'] for s in p['signals']))
            rows[stage].append({'market': 'market'+str(i), 'complete_window': True,
                                'profiles': profiles})
    targets = {'stage2': {'m9': ['M9'], 'm10': ['M10']}, 'stage4': {'m9': ['M9']}}
    def reviews():
        count_review, semantics = {}, {}
        for stage, members in rows.items():
            counts = {name: Counter() for name in members[0]['profiles']}
            proofs = []
            for row in members:
                for name, p in row['profiles'].items():
                    counts[name].update(p['counts'])
                    proofs.extend({'market': row['market'], 'profile': name,
                                   'strategy': s['strategy'], 'candidate_id': s['id']}
                                  for s in p['signals'] if s['strategy'] in ('M9', 'M10'))
            count_review[stage] = {'status': 'DEVELOPMENT100_REVIEWED', 'counted_markets': 2,
                                   'all_controls_match': True, 'source_control_mismatches': [],
                                   'counts': counts}
            semantics[stage] = {'counted_markets': 2, 'pnl_read_for_selection': False,
                                'proofs': proofs}
        return count_review, semantics
    return module, rows, rule, targets, reviews


def test_equal_frequency_uses_frozen_priority_and_preserves_failed_target(selection_case):
    module, rows, rule, targets, reviews = selection_case
    review, semantics = reviews()
    # Financial-looking values cannot influence the pure signal decision.
    rows['stage4'][0]['profiles']['m9']['assumed_pnl'] = '999999999'
    result = module.select_reviewed(rows, rule, targets, review, semantics)
    assert result['winners']['M9']['stage'] == 'stage2'
    assert result['winners']['M9']['signals'] == 2
    assert result['winners']['M9']['development_goal_reached'] is False
    assert len(result['expected_development_signatures']['market0']) == 3
    assert result['pnl_consulted'] is result['goal_complete'] is result['release_frozen'] is False


def test_higher_frequency_wins_even_when_later_priority(selection_case):
    module, rows, rule, targets, reviews = selection_case
    extra = deepcopy(rows['stage4'][0]['profiles']['m9']['signals'][0])
    extra.update(id='additional', confirmed=3, deadline=4)
    rows['stage4'][0]['profiles']['m9']['signals'].append(extra)
    rows['stage4'][0]['profiles']['m9']['counts']['M9'] += 1
    result = module.select_reviewed(rows, rule, targets, *reviews())
    assert result['winners']['M9']['stage'] == 'stage4'
    assert result['winners']['M9']['development_goal_reached'] is True
    assert result['full288_completed'] is False


@pytest.mark.parametrize('fault', ['partial', 'control', 'missing_proof', 'duplicate_proof',
                                  'physical_duplicate', 'parameters', 'checked_count', 'retired_scope'])
def test_partial_inconsistent_or_unreviewed_candidates_cannot_be_selected(selection_case, fault):
    module, rows, rule, targets, reviews = selection_case
    review, semantics = reviews()
    if fault == 'partial':
        rows['stage4'].pop()
    elif fault == 'control':
        review['stage2']['all_controls_match'] = False
    elif fault == 'missing_proof':
        semantics['stage4']['proofs'].pop()
    elif fault == 'duplicate_proof':
        semantics['stage4']['proofs'].append(deepcopy(semantics['stage4']['proofs'][0]))
    elif fault == 'physical_duplicate':
        p = rows['stage2'][0]['profiles']['m9']
        extra = dict(p['signals'][0], id='different_id_same_physics')
        p['signals'].append(extra)
        p['counts']['M9'] += 1
        review, semantics = reviews()
    elif fault == 'parameters':
        rows['stage2'][1]['profiles']['m9']['parameters']['M9']['window'] = .5
    elif fault == 'checked_count':
        review['stage2']['counts']['m9']['M9'] += 1
    else:
        targets['stage4']['m9'] = ['M8']
    with pytest.raises(module.SelectionNotReady):
        module.select_reviewed(rows, rule, targets, review, semantics)
