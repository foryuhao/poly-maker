"""A historic certificate is distinct from the latest timer, with strict resets."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def prove(monkeypatch):
    base = Path(__file__).resolve().parents[1]/'research_outputs/analysis/m910_density_v8_20261113'
    spec = importlib.util.spec_from_file_location('hold_history_test', base/'hold_history_evidence.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prove_hold


def sample(at, quantity, held, start, *, low='90', watermark=None, seq=1):
    from decimal import Decimal as D
    return {'input_seq': seq, 'received': at, 'now': at, 'watermark': at if watermark is None else watermark,
            'initial': '100', 'low': low, 'current': quantity, 'hit': '2',
            'qualified': True, 'recovery': str(min(D(1), (D(quantity)-D(low))/(D(100)-D(low)))),
            'held_after': held, 'hold_start_after': start}


def target():
    return {'start': 0, 'end': .75, 'values': {'initial': '100', 'stable_recovery': '.30',
            'hold_seconds': .01, 'held': True, 'stable': True, 'recovery': '.4',
            'hold_start': .741}}


def test_earlier_full_proof_survives_a_timer_reset_above_cancellation_floor(prove):
    samples = [sample(.1, '95', False, .1), sample(.112, '95', True, .1),
               sample(.2, '92', True, None), sample(.741, '94', True, .741),
               sample(.75, '94', True, .741)]
    proof = prove(target(), samples)
    assert proof['acquisitions'][0]['seconds'] >= .01
    assert proof['latest_timer_seconds'] < .01
    assert not proof['cancellations']


@pytest.mark.parametrize('failure', ['no_previous_full_hold', 'deep_dip', 'new_trough',
                                    'insufficient_watermark', 'fabricated_quantity', 'missing_close'])
def test_latched_flag_without_a_valid_unrevoked_history_is_rejected(prove, failure):
    samples = [sample(.1, '95', False, .1), sample(.112, '95', True, .1),
               sample(.2, '92', True, None), sample(.741, '94', True, .741),
               sample(.75, '94', True, .741)]
    if failure == 'no_previous_full_hold':
        samples = samples[2:]
    elif failure == 'deep_dip':
        samples[2] = sample(.2, '91', True, None)
    elif failure == 'new_trough':
        samples[2] = sample(.2, '89', True, None, low='89')
    elif failure == 'insufficient_watermark':
        samples[1]['watermark'] = .105
    elif failure == 'fabricated_quantity':
        samples[1]['current'] = '90'
    else:
        samples.pop()
    with pytest.raises(AssertionError):
        prove(target(), samples)
