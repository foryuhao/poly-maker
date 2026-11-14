"""The independent review must preserve both original M10 expiry bounds."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def review(monkeypatch):
    base = Path(__file__).resolve().parents[1]/'research_outputs/analysis/m910_density_v8_20261113'
    monkeypatch.syspath_prepend(str(base))
    spec = importlib.util.spec_from_file_location('m10_lifetime_review_test', base/'review_semantics.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_m10_lifetime


@pytest.mark.parametrize('confirmed', [101, 112, 118, 119.999])
def test_candidate_uses_the_earlier_of_rf_and_original_formation_expiry(review, confirmed):
    review({'confirmed': confirmed, 'deadline': min(confirmed+8, 120)},
           {'confirmed': confirmed, 'deadline': confirmed+8},
           {'confirmed': 100}, (1, 100, 120))


def test_actual_preserved_late_confirmation_case(review):
    import json
    base = Path(__file__).resolve().parents[1]/'research_outputs/analysis/m910_density_v8_20261113'
    cases = json.loads((base/'m10-deadline-semantic-review-incident.json').read_text())['affected_published_candidates']
    assert len(cases) == 2
    for case in cases:
        start = case['campaign_start']
        review(case['candidate'], case['rf'], case['root'],
               (start['seq'], start['event']['available'], start['event']['payload']['deadline']))


@pytest.mark.parametrize('fault', ['extend_past_formation', 'extend_past_rf', 'arbitrary_shortening',
                                  'retime_confirmation', 'renew_rf', 'renew_formation',
                                  'expired_formation', 'start_before_root'])
def test_unfounded_lifetime_changes_are_rejected(review, fault):
    ap = {'confirmed': 100}
    rf = {'confirmed': 118, 'deadline': 126}
    candidate = {'confirmed': 118, 'deadline': 120}
    start = (1, 100, 120)
    if fault == 'extend_past_formation':
        candidate['deadline'] = 126
    elif fault == 'extend_past_rf':
        rf.update(confirmed=101, deadline=109)
        candidate.update(confirmed=101, deadline=120)
    elif fault == 'arbitrary_shortening':
        candidate['deadline'] = 119
    elif fault == 'retime_confirmation':
        candidate['confirmed'] = 119
    elif fault == 'renew_rf':
        rf['deadline'] = 127
    elif fault == 'renew_formation':
        start = (1, 100, 121)
    elif fault == 'expired_formation':
        rf.update(confirmed=120, deadline=128)
        candidate.update(confirmed=120, deadline=120)
    else:
        start = (1, 99, 120)
    with pytest.raises(AssertionError):
        review(candidate, rf, ap, start)
