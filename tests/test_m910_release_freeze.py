"""Exercise release assembly in a temporary tree, never select real parameters."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest


BASE = Path(__file__).resolve().parents[1]/'research_outputs/analysis/m910_density_v8_20261113'


@pytest.fixture
def freeze_case(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BASE))
    spec = importlib.util.spec_from_file_location('release_freeze_test', BASE/'freeze_chosen_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'HERE', tmp_path)
    for name in ('frozen-performance-v1', 'frozen-final-entry-v1'):
        shutil.copytree(BASE/name, tmp_path/name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('performance-v1-source-manifest.json', 'final-entry-source-manifest.json',
                 'final-entry-import-and-test-binding.json', 'baseline-v7-parameters.json',
                 'baseline288-economics-v1.json', 'cohort288.json'):
        shutil.copyfile(BASE/name, tmp_path/name)
    preparation = json.loads((BASE/'final-release-preparation.json').read_text())
    preparation['base_frozen_directory'] = str(tmp_path/'frozen-performance-v1')
    preparation['entry_frozen_directory'] = str(tmp_path/'frozen-final-entry-v1')
    (tmp_path/'final-release-preparation.json').write_text(json.dumps(preparation))
    baseline = json.loads((tmp_path/'baseline-v7-parameters.json').read_text())
    profiles = json.loads((BASE/'stage2-profiles.json').read_text())
    names = {'M9': 'm9_seed0.5_refill.20_ex.49', 'M10': 'm10_round0.5_refill.20_ap0.5'}
    winners = {}
    for strategy, name in names.items():
        parameters = deepcopy(baseline[strategy])
        parameters.update(profiles[name][strategy])
        winners[strategy] = {'stage': 'stage2', 'profile': name, 'signals': 39,
                             'development_goal_reached': False, 'parameters': parameters}
    # Synthetic gate output tests assembly only; these are not observed counts,
    # a parameter choice, or a substitute for the actual development100 gate.
    prepared = {'recorded_at': 'SYNTHETIC_TEST_ONLY', 'winners': winners,
                'expected_development_signatures': {'fixture-market': []},
                'combined_policy_sha256': 'synthetic-policy'}
    monkeypatch.setattr(module, 'prepare', lambda: deepcopy(prepared))
    return module, tmp_path, prepared


def test_actual_pinned_release_resolves_defaults_and_preserves_failed_target(freeze_case):
    module, directory, prepared = freeze_case
    result = module.freeze('v8-fixture')
    assert result['status'] == 'RELEASE_FROZEN_REPLAY_NOT_LAUNCHED'
    choice = json.loads(Path(result['selection_file']).read_text())
    root = Path(result['release_root'])
    assert choice['development_targets_reached'] == {'M9': False, 'M10': False}
    assert choice['development_winners'] == prepared['winners']
    assert choice['resolved_frequency_parameters']['M9']['ex_resume_fraction'] == '.50'
    assert choice['resolved_frequency_parameters']['M10']['stable_recovery'] == '.20'
    assert choice['strategy_scope'] == ['M5', 'M6', 'M9', 'M10']
    assert choice['goal_complete'] is choice['remaining188_replayed_at_freeze'] is False
    assert choice['long_forward_authorized_by_this_file'] is False
    manifest = Path(choice['release_manifest_file'])
    assert module.sha(manifest) == choice['release_manifest_sha256']
    assert module.inventory(root) == json.loads(manifest.read_text())
    assert module.sha(root/'extension/m510_sim_replay.py') == module.sha(
        directory/'frozen-final-entry-v1/extension/m510_sim_replay.py')
    command = json.loads(Path(result['replay_command_file']).read_text())
    assert command['status'] == 'PREPARED_NOT_LAUNCHED'
    assert command['selection_sha256'] == module.sha(Path(result['selection_file']))
    assert command['argv'][3] == module.PYTHON
    index = command['argv'].index('--strategies')
    assert command['argv'][index+1:index+5] == ['M5', 'M6', 'M9', 'M10']
    assert not Path(command['run_directory']).exists()
    # A second invocation cannot overwrite an earlier frozen choice or output.
    before = module.inventory(root)
    with pytest.raises(module.SelectionNotReady, match='REFUSE_REUSING_PRIOR_RELEASE_OR_REPLAY'):
        module.freeze('v8-fixture')
    assert module.inventory(root) == before


def test_incomplete_development_gate_cannot_create_release(freeze_case, monkeypatch):
    module, directory, _ = freeze_case
    def not_ready():
        raise module.SelectionNotReady('stage4:CONTINUATION_NOT_FINISHED')
    monkeypatch.setattr(module, 'prepare', not_ready)
    with pytest.raises(module.SelectionNotReady, match='CONTINUATION_NOT_FINISHED'):
        module.freeze('v8-fixture')
    assert not (directory/'candidate-selection.json').exists()
    assert not list(directory.glob('chosen-*'))
    assert not (directory/'frozen-chosen-v8-fixture').exists()
