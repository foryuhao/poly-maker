"""The next live test cannot silently select retired six-strategy entry points."""
import sys
import json
from pathlib import Path

import pytest
import m511_sim_forward
import m511_sim_start


PROJECT = Path(__file__).resolve().parents[1]


def test_default_check_uses_accepted_two_strategy_parameters_without_starting(tmp_path, monkeypatch, capsys):
    from polymaker.m1234_sim import runner
    from maker_signals.strategies import M910_DENSITY

    def forbidden(*args, **kwargs):
        raise AssertionError('A check must not instantiate a live run or collect data')

    monkeypatch.setattr(runner.LiveRun, '__init__', forbidden)
    monkeypatch.setattr(runner, 'Engine', runner.Engine)
    monkeypatch.setattr(runner, 'OUTPUT', runner.OUTPUT)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(runner, 'preflight', lambda *args: {'status': 'PASS'})
    output = tmp_path/'must-not-exist'
    monkeypatch.setattr(sys, 'argv', ['forward', '--source-root', str(PROJECT),
        '--mode', 'check', '--config', str(PROJECT/'config/m1234-sim-24h.toml'),
        '--contract', str(PROJECT/'config/m1234-sim-contract.json'), '--output', str(output)])
    m511_sim_forward.main()
    result = json.loads(capsys.readouterr().out)
    actual = result['m5_m11']
    policy = json.loads((PROJECT/'config/m9-m10-next-test.json').read_text())
    selected = json.loads((PROJECT/policy['parameter_provenance']['selection_file']).read_text())
    assert actual['groups'] == result['profiles'] == ['M9', 'M10']
    assert set(actual['strategy_versions']) == {'M9', 'M10'}
    assert actual['signal_variant'] == M910_DENSITY
    for strategy in ('M9', 'M10'):
        assert actual['frequency_parameters'][strategy] == selected['resolved_frequency_parameters'][strategy]
    assert result['scenarios'] == ['research/trade-through']
    assert actual['execution_override']['budget_usdc'] == '5'
    assert runner.Engine.keywords['enabled_strategies'] == ('M9', 'M10')
    assert runner.Engine.keywords['frequency_parameters'] == policy['frequency_parameters']
    assert not output.exists()


@pytest.mark.parametrize('change', ['variant', 'parameters', 'missing_policy'])
def test_two_strategy_entry_cannot_silently_use_another_selection(tmp_path, change):
    if change == 'missing_policy':
        with pytest.raises(FileNotFoundError):
            m511_sim_forward.m910_configuration(tmp_path)
        return
    if change == 'variant':
        with pytest.raises(ValueError, match='ACCEPTED_SIGNAL_VARIANT'):
            m511_sim_forward.m910_configuration(PROJECT, 'm5-m10-frequency-v4')
        return
    parameters = tmp_path/'wrong.json'
    parameters.write_text('{"M10":{"stable_recovery":".50"}}')
    with pytest.raises(ValueError, match='ACCEPTED_FREQUENCY_PARAMETERS'):
        m511_sim_forward.m910_configuration(PROJECT, parameter_file=parameters)


@pytest.mark.parametrize('scope', ['m5-m10','additive','m5-m6-m9-m10'])
def test_legacy_forward_scope_rejected_before_source_import_or_output(tmp_path, monkeypatch, scope):
    output = tmp_path/'must-not-exist'
    monkeypatch.setattr(sys,'argv',['forward','--source-root',str(tmp_path/'not-a-source'),
        '--scope',scope,'--mode','run','--config','unused','--contract','unused',
        '--output',str(output)])
    with pytest.raises(ValueError,match='NEXT_TEST_SCOPE'):
        m511_sim_forward.main()
    assert not output.exists()


def test_old_additive_starter_cannot_create_a_service_under_current_retirement_policy(tmp_path, monkeypatch):
    project = tmp_path/'project'
    (project/'config').mkdir(parents=True)
    (project/'config/m5-m6-m9-m10-next-test.json').write_text(
        '{"retired_strategies":["M7","M8"]}')
    monkeypatch.setattr(m511_sim_start,'PROJECT',project)
    def forbidden(*args, **kwargs):
        raise AssertionError('No subprocess or service may be invoked')
    monkeypatch.setattr(m511_sim_start.subprocess,'run',forbidden)
    with pytest.raises(ValueError,match='ADDITIVE_FOLLOWER_RETIRED'):
        m511_sim_start.start(tmp_path/'missing-validation')
    assert not (project/'research_outputs').exists()
