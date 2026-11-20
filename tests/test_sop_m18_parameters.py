"""Original M18 numerical identity cannot silently become a tuned candidate."""
import pytest

from sop_signals.m18 import M18, Parameters
from sop_signals.m17 import Parameters as M17Parameters
from sop_signals.parameters import load_revision, strategy_from_parameters


def document(**values):
    return {"strategy": "M18", "revision_round": 0, "parameters": values,
            "pnl_used_for_selection": False}


def test_original_parameters_have_distinct_strategy_and_no_speed_contract():
    p, r = load_revision(document())
    assert p == Parameters() and strategy_from_parameters(p) == "M18"
    assert r["revision_round"] == 0 and r["original_sop_numerical_result"]
    assert r["parameters"]["hit_ratio_min"] == "2"
    with pytest.raises(ValueError, match="M18_PARAMETERS_REQUIRED"):
        M18(lambda *_: None, M17Parameters())


@pytest.mark.parametrize("name,value", [
    ("round_seconds", .25), ("stable_seconds", .05), ("stable_recovery", ".40"),
    ("hit_ratio_min", "1"), ("reference_hit_shares_min", "1"),
    ("absolute_loss_ratio_max", "2"), ("pair_gap", 3), ("ttl", 5),
])
def test_baseline_cannot_be_silently_relabelled(name, value):
    with pytest.raises(ValueError, match="ORIGINAL_SOP_BASELINE_PARAMETER_REQUIRED"):
        load_revision(document(**{name:value}))


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "oops"])
def test_invalid_numbers_are_not_zero_or_default(value):
    with pytest.raises(ValueError, match="ORIGINAL_SOP_BASELINE_PARAMETER_REQUIRED"):
        load_revision(document(hit_ratio_min=value))


def test_pnl_and_undeclared_numeric_selection_are_rejected():
    d = document()
    d["pnl_used_for_selection"] = True
    with pytest.raises(ValueError, match="FREQUENCY_ONLY"):
        load_revision(d)
    d.update(pnl_used_for_selection=False, revision_round=1)
    with pytest.raises(ValueError, match="PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED"):
        load_revision(d)


def candidate(**values):
    return {"strategy": "M18", "revision_round": 1, "parameters": values,
            "pnl_used_for_selection": False, "hypothesis": "Material reference floor only",
            "numeric_candidate_review": {
                "profile": "M18_MATERIAL_REFERENCE_AND_ESCALATION_NUMERICAL_V1",
                "parameter_change_notes": {name: "Explicit numerical tolerance; proof sequence retained"
                                           for name in values}}}


def test_explicit_materiality_candidate_has_a_distinct_identity_and_fixed_core():
    p, record = load_revision(candidate(reference_hit_shares_min="2.5",
                                        reference_hit_fraction_min=".05",
                                        reference_loss_fraction_min=".025"))
    _, original = load_revision(document())
    assert record["parameter_sha256"] != original["parameter_sha256"]
    assert record["original_sop_numerical_result"] is False
    assert (p.round_seconds, p.stable_seconds, str(p.stable_recovery)) == (.5, .1, "0.50")
    assert p.hit_ratio_min == 2 and p.absolute_loss_ratio_max == p.normalized_loss_ratio_max == 1
    assert p.ttl == 3 and p.pair_gap == 2


@pytest.mark.parametrize("name,value", [
    ("round_seconds", .25), ("stable_seconds", .05), ("stable_recovery", ".40"),
    ("absolute_loss_ratio_max", "1.01"), ("normalized_loss_ratio_max", "1.01"), ("ttl", 4),
])
def test_numeric_candidate_cannot_weaken_stability_loss_or_lifetime_rules(name, value):
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE"):
        load_revision(candidate(reference_hit_shares_min="2.5", **{name:value}))


@pytest.mark.parametrize("name,value", [
    ("reference_hit_shares_min", "0"), ("reference_hit_fraction_min", "0"),
    ("reference_loss_fraction_min", "0"), ("hit_ratio_min", "1"),
    ("hit_ratio_max", "100"), ("initial_depth_ratio_min", ".1"),
    ("initial_depth_ratio_max", "10"), ("pair_gap", 100),
])
def test_candidate_cannot_remove_materiality_escalation_or_comparability(name, value):
    with pytest.raises(ValueError, match="BOUNDED_NUMERIC_SEARCH_RANGE"):
        load_revision(candidate(**{name:value}))


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "oops"])
def test_candidate_invalid_numbers_are_not_missing_or_success(value):
    with pytest.raises(ValueError, match="INVALID_PARAMETER_NUMBER"):
        load_revision(candidate(reference_hit_shares_min=value))


@pytest.mark.parametrize("revision", [True, -1, 4, 1.0])
def test_candidate_revision_budget_is_enforced(revision):
    d = candidate(reference_hit_shares_min="2.5")
    d["revision_round"] = revision
    with pytest.raises(ValueError, match="AT_MOST_THREE"):
        load_revision(d)


@pytest.mark.parametrize("change", ["missing", "stale", "empty", "no_change"])
def test_each_actual_numeric_change_needs_an_explicit_semantic_review(change):
    d = candidate(reference_hit_shares_min="2.5")
    if change == "missing":
        d.pop("numeric_candidate_review")
    elif change == "stale":
        d["numeric_candidate_review"]["parameter_change_notes"]["pair_gap"] = "Not actually changed"
    elif change == "empty":
        d["numeric_candidate_review"]["parameter_change_notes"]["reference_hit_shares_min"] = ""
    else:
        d = candidate()
    with pytest.raises(ValueError, match="EXPLICIT_NUMERIC_SEMANTIC_REVIEW"):
        load_revision(d)


def short_document():
    d = candidate(reference_hit_shares_min="1", reference_hit_fraction_min=".01",
                  reference_loss_fraction_min=".005", pair_gap=4., round_seconds=.250)
    d.update(revision_round=3, hypothesis="Explicit quarter-second two-round candidate",
             explicit_short_round_candidate="M18_250MS_KEEP_100MS_HALF_RECOVERY_R3")
    d["numeric_candidate_review"]["profile"] = "M18_EXPLICIT_250MS_KEEP_STRICT_RECOVERY_NUMERICAL_V1"
    d["numeric_candidate_review"]["parameter_change_notes"]["round_seconds"] = (
        "Explicit250ms formation/followup rounds;100ms hold and all proof guards remain")
    return d


def test_short_candidate_is_explicit_r3_and_reported_with_its_actual_time_scale():
    from m1223_sim import definition
    p, revision = load_revision(short_document())
    sop = definition(p)
    assert p.round_seconds == .25 and p.stable_seconds == .1
    assert revision["original_sop_numerical_result"] is False
    assert revision["explicit_primitive_numeric_override"] == sop["explicit_primitive_numeric_override"]
    assert sop["terminal_quantity_input_contract"]["window_ms"] == 250
    assert "explicit-bid250-stable100" in sop["version"]
    assert revision["fixed_causal_rules"]["stable_recovery"] == "0.50"
    assert revision["fixed_causal_rules"]["absolute_loss_ratio_max"] == "1"
    assert revision["fixed_causal_rules"]["normalized_loss_ratio_max"] == "1"


@pytest.mark.parametrize("change", ["baseline", "r1", "r2", "missing_marker", "wrong_marker", "wrong_profile", "missing_note"])
def test_short_window_cannot_silently_replace_a_baseline_or_other_candidate(change):
    d = short_document()
    if change in ("baseline", "r1", "r2"):
        d["revision_round"] = {"baseline":0,"r1":1,"r2":2}[change]
    elif change == "missing_marker":
        d.pop("explicit_short_round_candidate")
    elif change == "wrong_marker":
        d["explicit_short_round_candidate"] = "not-declared"
    elif change == "wrong_profile":
        d["numeric_candidate_review"]["profile"] = "M18_MATERIAL_REFERENCE_AND_ESCALATION_NUMERICAL_V1"
    else:
        d["numeric_candidate_review"]["parameter_change_notes"].pop("round_seconds")
    with pytest.raises(ValueError):
        load_revision(d)


@pytest.mark.parametrize("field,value", [("stable_seconds", .05), ("stable_recovery", ".40"),
                                        ("absolute_loss_ratio_max", "1.01"), ("ttl", 5)])
def test_explicit_short_profile_does_not_unlock_other_fixed_guards(field, value):
    d = short_document()
    d["parameters"][field] = value
    d["numeric_candidate_review"]["parameter_change_notes"][field] = "Unauthorized"
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE"):
        load_revision(d)


def test_short_declaration_cannot_be_retained_on_an_actual_500ms_profile():
    d = short_document()
    d["parameters"]["round_seconds"] = .5
    with pytest.raises(ValueError, match="SHORT_ROUND_DECLARATION_DIFFERS"):
        load_revision(d)
