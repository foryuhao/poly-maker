"""M14 must use its own contract, samples, account and frequency evidence."""

from copy import deepcopy

import pytest
from m1223_gates import phase_admission, strategy_schema, validate_selection
from m1223_sim import definition
from sop_signals.feed import Feed
from sop_signals.m13 import Parameters as M13Parameters
from sop_signals.m14 import M14, Parameters

from test_sop_gates import cohort, setup


def test_m14_definition_has_original_parameters_external_sop_and_single_paper_account():
    d = definition(Parameters())
    assert d['strategy'] == 'M14' and d['groups'] == ['M14'] and d['real_orders'] == 0
    assert d['version'].startswith('m14-fixed-anchor-entry-v1/')
    assert d['parameters']['round_seconds'] == .5
    assert 'M14.md' in d['sop_references'] and 'M13.md' not in d['sop_references']
    assert d['parameter_sha256'] == 'bbfbc47f4c06fa9b5daf9bafbbd7b4d12ec298bc613af59a8002abe8be88a32d'
    assert strategy_schema(d, 'frequency-selection-v1') == 'm14-frequency-selection-v1'


def test_previous_strategy_evidence_cannot_be_relabelled_m14(tmp_path):
    _, dev, selection, _ = setup(tmp_path)
    d = definition(Parameters())
    with pytest.raises(ValueError, match='MATCHING_FROZEN'):
        validate_selection(selection, d)
    c = deepcopy(cohort(dev, 'DEVELOPMENT_CAPTURE'))
    with pytest.raises(ValueError, match='INVALID_PREDECLARED'):
        phase_admission(c, {'max_markets': 24, 'sop': d})
    c['strategy'] = 'M14'
    assert not phase_admission(c, {'max_markets': 24, 'sop': d})['admission_granted_for_24h']
    c['strategy'] = 'M15'
    with pytest.raises(ValueError, match='INVALID_PREDECLARED'):
        phase_admission(c, {'max_markets': 24, 'sop': d})


def test_source_consumer_installs_only_m14_and_rejects_other_strategy_parameters():
    feed = Feed('condition', ['yes', 'no'], {'yes': '.01', 'no': '.01'}, lambda *_: None,
                strategy='M14', parameters=Parameters())
    assert isinstance(feed.core, M14)
    with pytest.raises(ValueError, match='M14_PARAMETERS_REQUIRED'):
        Feed('condition', ['yes', 'no'], {'yes': '.01', 'no': '.01'}, lambda *_: None,
             strategy='M14', parameters=M13Parameters())
