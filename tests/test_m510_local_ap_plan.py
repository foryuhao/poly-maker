"""Keep the extra calibration bounded, target-scoped and independent of PnL."""

from copy import deepcopy

from m510_local_ap_plan import profiles_from
from maker_signals.strategies import (
    M5_M10_LOCAL_AP,
    frequency_defaults,
    resolve_frequency_parameters,
)


def test_only_missing_targets_get_independent_predeclared_profiles():
    control = frequency_defaults(M5_M10_LOCAL_AP)
    parameters = deepcopy(control)
    parameters["M7"].update(round_seconds=.1, hold_seconds=.01)
    baseline = {"expected_counts": {"M7": 6, "M9": 10, "M10": 7},
                "frequency_parameters": parameters}
    profiles, targets, priority = profiles_from(baseline, control)
    assert len(profiles) == 19 and set(priority) == {"M7", "M10"}
    assert profiles["control_v7"] == {} and "control_v7" not in targets
    for name, values in profiles.items():
        if name == "control_v7":
            continue
        assert set(values) == set(targets[name]) and not set(values) & {"M5", "M6", "M8", "M9"}
        resolved = resolve_frequency_parameters(M5_M10_LOCAL_AP, values)
        for strategy in targets[name]:
            assert resolved[strategy]["ap_window_seconds"] in (.5, .375, .25)
    assert priority["M10"][:2] == ["m10_ap500_selected_d99", "m10_ap500_selected_d999"]
    assert control == frequency_defaults(M5_M10_LOCAL_AP)


def test_no_additional_experiment_if_public_profiles_already_pass():
    control = frequency_defaults(M5_M10_LOCAL_AP)
    profiles, targets, priority = profiles_from({
        "expected_counts": {"M7": 10, "M9": 12, "M10": 10}, "frequency_parameters": control}, control)
    assert profiles == {"control_v7": {}} and targets == priority == {}


def test_identical_m7_control_and_selection_are_not_duplicated():
    control = frequency_defaults(M5_M10_LOCAL_AP)
    profiles, targets, priority = profiles_from({
        "expected_counts": {"M7": 6, "M9": 12, "M10": 10}, "frequency_parameters": control}, control)
    assert len(profiles) == 4 and len(targets) == 3 and set(priority) == {"M7"}
