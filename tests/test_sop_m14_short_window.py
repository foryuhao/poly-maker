"""Explicit Ask clocks keep original Bid clock and a feasible stable-Ask counterproof."""

from decimal import Decimal as D

import pytest
from m1223_sim import definition
from sop_signals.m14 import M14, Parameters
from sop_signals.parameters import load_revision

from test_sop_m14 import Tape
from test_sop_m14_parameters import document


@pytest.mark.parametrize('seconds,profile', [(.25, 'M14_ASK250MS_BID500MS_STABLE100MS'),
                                           (.15, 'M14_ASK150MS_BID500MS_STABLE100MS')])
def test_explicit_ask_profile_freezes_original_bid_and_both_stability_requirements(seconds, profile):
    doc = document()
    doc.update(revision_round=2, observation_window_profile=profile,
               hypothesis='SYNTHETIC_CLOCK_CONTRACT_NOT_FREQUENCY_SELECTION',
               parameters={'round_seconds': seconds})
    p, revision = load_revision(doc)
    assert p.round_seconds == seconds and p.stable_seconds == .1 and p.stable_recovery == D('.50')
    assert revision['numerical_observation_windows']['ASK_seconds'] == seconds
    assert revision['numerical_observation_windows']['BID_seconds'] == .5
    assert revision['fixed_causal_rules']['bid_round_seconds'] == .5
    assert 'round_seconds' not in revision['fixed_causal_rules']
    result = definition(p)
    assert result['observation_windows'] == revision['numerical_observation_windows']
    assert 'ask-window-bid500-stable100-v2' in result['version']
    assert result['input_contract'] == definition(Parameters())['input_contract']
    assert result['execution_sha256'] == definition(Parameters())['execution_sha256']


@pytest.mark.parametrize('marker', [None, True, [], 'UNDECLARED', 'M14_ASK100MS_BID500MS_STABLE100MS'])
def test_unknown_or_no_stable_hold_margin_profile_is_not_accepted(marker):
    doc = document()
    doc.update(revision_round=2, observation_window_profile=marker, parameters={'round_seconds': .25})
    with pytest.raises(ValueError, match='PROFILE_NOT_AUTHORIZED'):
        load_revision(doc)


@pytest.mark.parametrize('seconds', [.1, .05, 0])
def test_direct_core_cannot_make_stable_ask_counterevidence_unobservable(seconds):
    with pytest.raises(ValueError, match='POSITIVE_STABLE_RECOVERY_MARGIN'):
        M14(lambda *_: None, Parameters(round_seconds=seconds))


@pytest.mark.parametrize('change', ['baseline', 'window', 'stable_hold', 'stable_recovery', 'ttl'])
def test_explicit_ask_profile_does_not_unlock_original_baseline_or_other_fixed_rules(change):
    doc = document()
    doc.update(revision_round=2, observation_window_profile='M14_ASK250MS_BID500MS_STABLE100MS',
               parameters={'round_seconds': .25})
    if change == 'baseline':
        doc['revision_round'] = 0
    elif change == 'window':
        doc['parameters']['round_seconds'] = .5
    elif change == 'stable_hold':
        doc['parameters']['stable_seconds'] = .05
    elif change == 'stable_recovery':
        doc['parameters']['stable_recovery'] = '.40'
    else:
        doc['parameters']['ttl'] = 8
    with pytest.raises(ValueError):
        load_revision(doc)


def formed_tape(seconds):
    t = Tape(Parameters(round_seconds=seconds))
    t.hit(1, 'BUY')
    t.hit(1, 'SELL')
    t.depth(1.01, 'ASK', 80)
    t.depth(1.01, 'BID', 80)
    t.depth(1.04, 'ASK', 82)
    t.depth(1.05, 'BID', 95)
    assert t.s.rounds['yes', 'BID', D('.40')].end == pytest.approx(1.5)
    assert t.s.rounds['yes', 'ASK', D('.42')].end == pytest.approx(1 + seconds)
    t.close(1 + seconds + .001)
    assert ('yes', 'BID', D('.40')) in t.s.rounds
    root = t.s.roots['yes', D('.42')]
    start = t.s.now + .01
    t.hit(start, 'BUY')
    t.depth(start + .01, 'ASK', 70)
    t.depth(start + .03, 'ASK', D('71.2'))
    for close_at in sorted([1.501, start + seconds + .001]):
        t.close(close_at)
    return t, root


@pytest.mark.parametrize('seconds', [.25, .15])
def test_two_new_short_asks_pair_with_original500ms_bid_and_original_min_deadline(seconds):
    t, root = formed_tape(seconds)
    assert len(t.s.signals) == 1 and root.second_round_id
    signal = next(iter(t.s.signals.values()))
    assert signal['root_id'] == root.first.id
    assert signal['bid']['window_end'] == pytest.approx(1.5)
    assert signal['ask1']['window_end'] == pytest.approx(1 + seconds)
    assert signal['ask2']['window_end'] - signal['ask1']['key'][0] > seconds
    assert signal['deadline'] == pytest.approx(min(signal['ask2']['key'][0] + 3, signal['bid']['key'][0] + 3))
    assert signal['anchor_price'] == D('.40')


@pytest.mark.parametrize('seconds', [.25, .15])
def test_short_ask_stable_recovery_still_invalidates_existing_signal_after_full100ms(seconds):
    t, _ = formed_tape(seconds)
    start = t.s.now + .01
    t.hit(start, 'BUY')
    t.depth(start + .005, 'ASK', 40)
    t.depth(start + .02, 'ASK', 60)  # Below old ceiling76; above own half-recovery threshold.
    assert t.s.signals
    t.close(start + seconds + .001)
    assert not t.s.signals and not t.s.roots
    assert 'SUBSEQUENT_STABLE_ASK' in t.reasons()
    closed = [p for kind, p in t.events if kind == 'ROUND_CLOSED' and p['side'] == 'ASK'][-1]
    assert closed['stable'] and closed['end'] - closed['stable_since'] >= .1


@pytest.mark.parametrize('seconds', [.25, .15])
def test_short_second_attempt_stays_consumed_after_first_ceiling_loss(seconds):
    t = Tape(Parameters(round_seconds=seconds))
    t.hit(1, 'BUY')
    t.depth(1.01, 'ASK', 80)
    t.depth(1.04, 'ASK', 82)
    t.close(1 + seconds + .001)
    start = t.s.now + .01
    t.hit(start, 'BUY')
    identity = next(iter(t.s.roots.values())).second_round_id
    t.depth(start + .01, 'ASK', 70)
    t.depth(start + .02, 'ASK', 90)
    t.depth(start + .03, 'ASK', D('71.2'))
    assert not t.s.roots and identity in t.s.second_rounds
    t.close(start + seconds + .001)
    assert not t.s.roots and not t.s.signals and not t.s.second_rounds
