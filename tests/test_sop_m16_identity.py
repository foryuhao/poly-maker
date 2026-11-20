"""M16 capability requirements and strategy evidence isolation."""

from copy import deepcopy
import pytest

from m1223_gates import phase_admission, strategy_schema, validate_selection
from m1223_sim import definition
from sop_signals.feed import Feed
from sop_signals.m16 import Parameters
from sop_signals.m15 import Parameters as M15Parameters
from test_sop_gates import setup, cohort


def test_own_definition_records_timing_input_and_original_numeric_contract():
    d = definition(Parameters())
    assert d["strategy"] == "M16" and d["groups"] == ["M16"] and d["real_orders"] == 0
    assert d["timing_input_contract"]["receiver_clock_evidence_required"]
    assert d["timing_input_contract"]["crossing_bracket_max_ms"] == 50
    assert "TRADE_TIMING_CAPABILITIES.md" in d["sop_references"]
    assert d["parameters"]["pair_gap"] == 2 and d["parameters"]["reference_latency_min"] == .1
    assert strategy_schema(d, "frequency-selection-v1") == "m16-frequency-selection-v1"


def test_old_strategy_selection_is_not_new_strategy_admission(tmp_path):
    _, dev, selection, _ = setup(tmp_path)
    d = definition(Parameters())
    with pytest.raises(ValueError, match="MATCHING_FROZEN"):
        validate_selection(selection, d)
    c = deepcopy(cohort(dev,"DEVELOPMENT_CAPTURE"))
    with pytest.raises(ValueError, match="INVALID_PREDECLARED"):
        phase_admission(c, {"max_markets":24,"sop":d})
    c["strategy"] = "M16"
    assert not phase_admission(c, {"max_markets":24,"sop":d})["admission_granted_for_24h"]
    with pytest.raises(ValueError, match="M16_PARAMETERS_REQUIRED"):
        Feed("condition", ("yes","no"), {"yes":".01","no":".01"}, lambda *_:None,
             parameters=M15Parameters(), strategy="M16")
