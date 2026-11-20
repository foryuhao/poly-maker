"""Sequential strategy support must not let M12 evidence admit M13."""

from copy import deepcopy

import pytest
from m1223_gates import phase_admission, validate_selection
from m1223_sim import definition
from sop_signals.m13 import Parameters

from test_sop_gates import cohort, setup


def test_m13_definition_is_a_single_new_account_and_external_sop():
    d = definition(Parameters())
    assert d["strategy"] == "M13" and d["groups"] == ["M13"]
    assert d["version"].startswith("m13-fixed-anchor-entry-v1/")
    assert "M13.md" in d["sop_references"] and "M12.md" not in d["sop_references"]
    assert d["real_orders"] == 0


def test_old_strategy_selection_and_cohort_cannot_be_relabelled_m13(tmp_path):
    _, dev, selection, _ = setup(tmp_path)
    d = definition(Parameters())
    with pytest.raises(ValueError, match="MATCHING_FROZEN"):
        validate_selection(selection, d)
    with pytest.raises(ValueError, match="INVALID_PREDECLARED"):
        phase_admission(cohort(dev, "DEVELOPMENT_CAPTURE"), {"max_markets": 24, "sop": d})
    c = deepcopy(cohort(dev, "DEVELOPMENT_CAPTURE"))
    c["strategy"] = "M13"
    assert not phase_admission(c, {"max_markets": 24, "sop": d})["admission_granted_for_24h"]
    c["strategy"] = "M14"
    with pytest.raises(ValueError, match="INVALID_PREDECLARED"):
        phase_admission(c, {"max_markets": 24, "sop": d})
