"""Original M16 contract cannot be silently retuned or selected by economics."""

import pytest
from sop_signals.m16 import M16, Parameters
from sop_signals.m15 import Parameters as M15Parameters
from sop_signals.parameters import load_revision, strategy_from_parameters


def document(**parameters):
    return {"strategy": "M16", "revision_round": 0, "parameters": parameters,
            "hypothesis": "ORIGINAL_SOP_BASELINE", "pnl_used_for_selection": False}


def test_baseline_parameters_have_separate_strategy_identity():
    p, r = load_revision(document())
    assert p == Parameters() and strategy_from_parameters(p) == "M16"
    assert r["revision_round"] == 0 and r["parameters"]["ttl"] == 3
    assert r["numeric_candidates_implemented"]
    with pytest.raises(ValueError, match="M16_PARAMETERS_REQUIRED"):
        M16(lambda *_: None, M15Parameters())


@pytest.mark.parametrize("name,value", [("round_seconds", .25), ("stable_seconds", .05),
                                        ("pair_gap", 3), ("second_speed_ratio_max", ".75"),
                                        ("reference_latency_min", .05), ("ttl", 4)])
def test_original_baseline_values_are_not_relabelled(name, value):
    with pytest.raises(ValueError, match="BASELINE_CANNOT_BE_RELABELLED"):
        load_revision(document(**{name:value}))


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "oops"])
def test_nonfinite_or_invalid_input_does_not_become_a_numeric_zero(value):
    with pytest.raises(ValueError, match="INVALID_PARAMETER_NUMBER"):
        load_revision(document(absolute_hit_min=value))


def test_pnl_selection_unreviewed_candidate_or_excess_rounds_cannot_be_admitted():
    d = document()
    d["pnl_used_for_selection"] = True
    with pytest.raises(ValueError, match="FREQUENCY_ONLY"):
        load_revision(d)
    d.update(pnl_used_for_selection=False, revision_round=4)
    with pytest.raises(ValueError, match="AT_MOST_THREE"):
        load_revision(d)
    d.update(revision_round=1)
    with pytest.raises(ValueError, match="EXPLICIT_NUMERIC_CHANGE_REVIEW_REQUIRED"):
        load_revision(d)


def candidate(**parameters):
    d = document(**parameters)
    d.update(revision_round=1, hypothesis="Synthetic parameter parser case; not a selected live candidate",
             numeric_candidate_review={
                 "profile": "M16_EXPLICIT_TIMING_COMPARABILITY_NUMERICAL_V1",
                 "parameter_change_notes": {k: "Explicit synthetic numerical change for boundary validation" for k in parameters}})
    return d


def test_explicit_numerical_candidate_has_distinct_identity_and_preserves_strict_contract():
    baseline, base = load_revision(document())
    p, record = load_revision(candidate(reference_latency_min=.025, pair_gap=2.5,
                                       second_speed_ratio_max=".75", normalized_loss_min=".60"))
    assert p != baseline and record["parameter_sha256"] != base["parameter_sha256"]
    assert (p.round_seconds, p.stable_seconds, str(p.stable_recovery), p.ttl) == (.5, .1, "0.50", 3)
    assert p.absolute_hit_min == p.normalized_hit_min == 1
    assert p.post_recovery_ratio_min == baseline.post_recovery_ratio_min
    assert p.post_speed_ratio_max == baseline.post_speed_ratio_max
    assert not record["original_sop_numerical_result"]
    assert not record["original_100ms_reference_and_50ms_minimum_improvement_guarantee"]
    assert record["explicit_numeric_overrides"]["reference_latency_min"]["original"] == .1
    assert record["explicit_numeric_overrides"]["reference_latency_min"]["selected"] == .025


@pytest.mark.parametrize("name,value", [("round_seconds", .25), ("stable_seconds", .05),
    ("stable_recovery", ".40"), ("ttl", 4), ("absolute_hit_min", ".9"),
    ("normalized_hit_min", ".9"), ("post_recovery_ratio_min", ".5"), ("post_speed_ratio_max", "2")])
def test_candidates_cannot_relax_stability_strength_direction_or_maintenance(name, value):
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED"):
        load_revision(candidate(**{name: value}))


@pytest.mark.parametrize("name,value", [("pair_gap", .5), ("pair_gap", 3),
    ("reference_latency_min", 0), ("reference_latency_min", .2),
    ("second_speed_ratio_max", 0), ("second_speed_ratio_max", ".81"),
    ("absolute_hit_max", ".9"), ("absolute_hit_max", "4.1"),
    ("normalized_hit_max", ".9"), ("normalized_hit_max", "4.1"),
    ("normalized_loss_min", ".49"), ("normalized_loss_min", "1.1"),
    ("normalized_loss_max", ".9"), ("normalized_loss_max", "2.1")])
def test_candidates_remain_in_declared_comparability_and_improvement_search_range(name, value):
    with pytest.raises(ValueError, match="SEMANTICS_OR_BOUNDED_SEARCH_RANGE"):
        load_revision(candidate(**{name: value}))


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("numeric_candidate_review"),
    lambda d: d["numeric_candidate_review"].update(profile="unreviewed"),
    lambda d: d["numeric_candidate_review"]["parameter_change_notes"].clear(),
    lambda d: d["numeric_candidate_review"]["parameter_change_notes"].update(reference_latency_min=""),
    lambda d: d["numeric_candidate_review"]["parameter_change_notes"].update(pair_gap="not changed"),
])
def test_each_changed_number_requires_exact_explicit_review_without_extra_fields(mutate):
    d = candidate(reference_latency_min=.025); mutate(d)
    with pytest.raises(ValueError, match="EXPLICIT_NUMERIC_CHANGE_REVIEW_REQUIRED"):
        load_revision(d)


def test_baseline_cannot_be_relabelled_by_adding_a_candidate_review():
    d = candidate(reference_latency_min=.025); d.update(revision_round=0, parameters={})
    with pytest.raises(ValueError, match="BASELINE_CANNOT_USE_CANDIDATE_REVIEW"):
        load_revision(d)


@pytest.mark.parametrize("name", ["epsilon", "crossing_bracket_max", "source_max_age", "pre_hit_required"])
def test_clock_and_source_quality_are_not_numeric_tuning_parameters(name):
    with pytest.raises(ValueError, match="UNKNOWN_OR_UNAUTHORIZED_M16_PARAMETER"):
        load_revision(candidate(**{name: .01}))


def configured_tape(parameters):
    from test_sop_m16 import Tape

    tape = Tape()
    tape.s = M16(lambda k, p: tape.events.append((k, p)), parameters, receiver_quantum=".001")
    return tape


def test_loaded_candidate_changes_reference_number_but_keeps_two_real_stable_rounds_and_new_floor():
    baseline, _ = load_revision(document())
    original = configured_tape(baseline)
    assert original.reference(low_at=1.08, high_at=1.10) is None
    parameters, record = load_revision(candidate(reference_latency_min=.025))
    tuned = configured_tape(parameters)
    reference = tuned.reference(low_at=1.08, high_at=1.10)
    assert str(reference.reference["tau_low"]) == "0.058"
    assert not tuned.s.signals
    tuned.second(low_at=1.62, high_at=1.63)
    signal = next(iter(tuned.s.signals.values()))
    assert signal["root_id"] == reference.id
    assert signal["version"].endswith(record["parameter_sha256"][:12])
    assert signal["prior_reference"]["guard"] == 88 and signal["bid"]["guard"] == 83
    assert signal["deadline"] == 5.101
    tuned.depth(2.11, "82")
    assert not tuned.s.signals  # The original new F2 guard is still binding.


def test_loaded_candidate_does_not_reselect_after_first_weaker_second_round():
    parameters, _ = load_revision(candidate(reference_latency_min=.025))
    tuned = configured_tape(parameters)
    root = tuned.reference(low_at=1.08, high_at=1.10).id
    tuned.second(hits="9", low_at=1.62, high_at=1.63)
    assert not tuned.s.signals and not tuned.s.campaigns
    tuned.second(start=2.3)
    assert not any(p.get("root_id") == root for kind, p in tuned.events if kind == "SIGNAL_CONFIRMED")
