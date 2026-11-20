import pytest
from m1223_gates import frequency_reporting_contract
from m1223_start import phase_configuration


def test_development_and_validation_use_24_markets_and_day_keeps_full_budget():
    dev = phase_configuration(
        "DEVELOPMENT_CAPTURE", 0, has_selection=False, has_independent_report=False
    )
    independent = phase_configuration(
        "INDEPENDENT_VALIDATION", 0, has_selection=True, has_independent_report=False
    )
    day = phase_configuration(
        "CONTINUOUS_24H_TEST", 1, has_selection=True, has_independent_report=True
    )
    assert dev["markets"] == independent["markets"] == 24
    assert independent["batch_number"] == 1
    assert day["markets"] == 288 and day["config"] == "m1223-24h.toml"


def test_day_reporting_target_is_116_of_288_and_does_not_redefine_admission():
    independent = frequency_reporting_contract("INDEPENDENT_VALIDATION", 24)
    day = frequency_reporting_contract("CONTINUOUS_24H_TEST", 288)
    assert independent["minimum_distinct_primary_signal_markets"] == 10
    assert independent["role"] == "ADMISSION_ONLY_AFTER_COMPLETE_INDEPENDENT_COHORT"
    assert day["minimum_distinct_primary_signal_markets"] == 116
    assert day["denominator"] == 288 and day["coverage_target"] == "0.40"
    assert day["role"] == "DESCRIPTIVE_RESULT_ONLY_NOT_RESTART_OR_RETUNING_TRIGGER"


@pytest.mark.parametrize("phase,count", [
    ("CONTINUOUS_24H_TEST", 24), ("INDEPENDENT_VALIDATION", 288), ("UNKNOWN", 24),
])
def test_reporting_cannot_reuse_another_phase_denominator(phase, count):
    with pytest.raises(ValueError, match="PHASE_DENOMINATOR"):
        frequency_reporting_contract(phase, count)


@pytest.mark.parametrize(
    "phase,used,selected,admitted",
    [
        ("DEVELOPMENT_CAPTURE", 0, True, False),
        ("INDEPENDENT_VALIDATION", 0, False, False),
        ("INDEPENDENT_VALIDATION", 2, True, False),
        ("CONTINUOUS_24H_TEST", 1, True, False),
        ("CONTINUOUS_24H_TEST", 1, False, True),
    ],
)
def test_launcher_does_not_silently_skip_required_phases(phase, used, selected, admitted):
    with pytest.raises(ValueError):
        phase_configuration(phase, used, has_selection=selected, has_independent_report=admitted)
