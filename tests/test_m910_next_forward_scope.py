"""The next live test cannot silently select retired six-strategy entry points."""
import sys
from pathlib import Path

import pytest
import m511_sim_forward
import m511_sim_start


@pytest.mark.parametrize('scope', ['m5-m10','additive'])
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
