"""Current M4 detector only; no M1/M2/M3 strategy or research ledger."""
import json
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
from m4_helpers import advance, fixture_feed, form, queue_hit

from polymaker.m4_frequency_signal import M4FrequencySignals


def test_bid_support_and_weak_ask_form_one_candidate():
    feed,clock=fixture_feed()
    form(feed,clock)
    assert len(feed.m1.frequency.candidates)==1
    assert feed.m1.counts['BID_SUPPORT_CONFIRMED']==1
    assert feed.m1.counts['RF_BID_CONFIRMED']==0
    advance(feed,clock,6.725)
    assert not feed.m1.frequency.candidates


def test_normal_ask_recovery_does_not_form_candidate():
    feed,clock=fixture_feed()
    queue_hit(feed,clock,.02)
    queue_hit(feed,clock,.60,side='BID')
    queue_hit(feed,clock,1.20,recovery=D('.9'),qty=D(25))
    assert not feed.m1.frequency.candidates
    assert feed.m1.counts['DF_ASK_CONFIRMED']==0


def test_queue_disappearance_invalidates_evidence():
    feed,clock=fixture_feed()
    form(feed,clock)
    assert feed.m1.frequency.candidates
    feed.m1.invalidate('INPUT_GAP','1')
    assert not feed.m1.frequency.candidates and not feed.m1.frequency.states


def test_recovered_ask_cancels_candidate():
    feed,clock=fixture_feed()
    form(feed,clock)
    queue_hit(feed,clock,1.80,recovery=D(1))
    assert not feed.m1.frequency.candidates


def test_observer_failure_blocks_m4():
    feed,clock=fixture_feed()
    form(feed,clock)
    def fail():
        raise ValueError('synthetic observer failure')
    feed.m1.frequency.tick=fail
    feed.m1.evaluate(feed.snapshot())
    assert feed.m1.failure=='M4_OBSERVER_FAILED:ValueError'
    assert feed.m1.selected is None
    assert not feed.m1.frequency.candidates


@pytest.mark.parametrize('loss,expected', [('1.18',False),('4.99',False),('5',True),('5.01',True),
                                        ('19.99',True),('20',True)])
def test_weak_ask_requires_five_percent_loss_inclusive(loss,expected):
    feed,clock=fixture_feed()
    # The reference and Bid support each lose only 4%; the new gate belongs
    # exclusively to the subsequent weak Ask round, whose initial size is 100.
    queue_hit(feed,clock,.02,qty=D(4))
    queue_hit(feed,clock,.60,side='BID',qty=D(4))
    queue_hit(feed,clock,1.20,recovery=D(0),qty=D(loss))
    assert feed.m1.counts['BID_SUPPORT_CONFIRMED']==1
    assert bool(feed.m1.frequency.candidates)==expected
    assert feed.m1.counts['DF_ASK_CONFIRMED']==int(expected)
    rejected=[e for e in feed.m1.events if e['event']=='WEAK_ROUND_REJECTED']
    if expected:
        assert not rejected
    else:
        assert len(rejected)==1
        assert rejected[0]['reason']=='ASK_LOSS_FRACTION_BELOW_MINIMUM'
        assert rejected[0]['loss_fraction']==D(loss)/100
        assert rejected[0]['minimum_loss_fraction']==D('.05')


def test_small_weak_round_consumes_reference_without_searching_ahead():
    feed,clock=fixture_feed()
    queue_hit(feed,clock,.02)
    queue_hit(feed,clock,.60,side='BID')
    queue_hit(feed,clock,1.20,recovery=D(0),qty=D(4))
    assert feed.m1.frequency.state('1','ASK')['reference'] is None
    queue_hit(feed,clock,1.80,recovery=D(0),qty=D(25))
    assert not feed.m1.frequency.candidates
    assert feed.m1.counts['DF_ASK_CONFIRMED']==0


@pytest.mark.parametrize('case', json.loads(
    (Path(__file__).parent/'fixtures/m4_weak_loss_cases.json').read_text()),
    ids=lambda case: case['label'])
def test_reviewed_loss_rounds_under_new_weak_ask_gate(case):
    model=M4FrequencySignals(SimpleNamespace(),lambda *_:None)
    for fact in sorted(case['rounds'],key=lambda fact:fact['confirmed']):
        model.ingest('ROUND_CLOSED',fact)
    weak=case['rounds'][-1]
    assert bool(model.state(weak['token'],'ASK')['df'])==case['expected_df']
