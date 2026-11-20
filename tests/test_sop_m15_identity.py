"""M15 owns one source/contract/account; old strategy evidence is not admission."""

from copy import deepcopy

import pytest
from m1223_gates import phase_admission, strategy_schema, validate_selection
from m1223_sim import definition
from sop_signals.feed import Feed
from sop_signals.m13 import Parameters as M13Parameters
from sop_signals.m15 import M15, Parameters
from test_sop_gates import cohort, setup


def test_m15_own_frozen_definition_and_original_numeric_identity():
    d = definition(Parameters())
    assert d["strategy"] == "M15" and d["groups"] == ["M15"] and d["real_orders"] == 0
    assert d["version"].startswith("m15-fixed-anchor-entry-v1/")
    assert "M15.md" in d["sop_references"] and "M13.md" not in d["sop_references"]
    assert d["parameters"]["round_seconds"] == .5 and d["parameters"]["ttl"] == 8
    assert strategy_schema(d, "frequency-selection-v1") == "m15-frequency-selection-v1"


def test_previous_strategy_samples_or_selection_cannot_pass_m15_admission(tmp_path):
    _, dev, selection, _ = setup(tmp_path)
    d = definition(Parameters())
    with pytest.raises(ValueError, match="MATCHING_FROZEN"):
        validate_selection(selection, d)
    c = deepcopy(cohort(dev, "DEVELOPMENT_CAPTURE"))
    with pytest.raises(ValueError, match="INVALID_PREDECLARED"):
        phase_admission(c, {"max_markets": 24, "sop": d})
    c["strategy"] = "M15"
    assert not phase_admission(c, {"max_markets": 24, "sop": d})["admission_granted_for_24h"]
    c["strategy"] = "M16"
    with pytest.raises(ValueError, match="INVALID_PREDECLARED"):
        phase_admission(c, {"max_markets": 24, "sop": d})


def test_source_consumer_rejects_wrong_strategy_parameter_type():
    f = Feed("condition", ["yes", "no"], {"yes": ".01", "no": ".01"}, lambda *_: None,
             strategy="M15", parameters=Parameters())
    assert isinstance(f.core, M15)
    with pytest.raises(ValueError, match="M15_PARAMETERS_REQUIRED"):
        Feed("condition", ["yes", "no"], {"yes": ".01", "no": ".01"}, lambda *_: None,
             strategy="M15", parameters=M13Parameters())
