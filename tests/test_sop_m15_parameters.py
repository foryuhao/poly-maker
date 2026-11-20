"""M15 baseline identity and bounded numerical revisions never alter guards."""

import pytest
from sop_signals.m15 import Parameters
from sop_signals.parameters import load_revision, strategy_from_parameters


def document(**parameters):
    return {"strategy": "M15", "revision_round": 0, "parameters": parameters,
            "hypothesis": "ORIGINAL_BASELINE", "pnl_used_for_selection": False}


def test_original_parameter_identity_does_not_include_m13_strength_ratios():
    p, r = load_revision(document())
    assert p == Parameters() and strategy_from_parameters(p) == "M15"
    assert r["parameter_sha256"] == "0eff3dca0c983287eafa00c01753ebb57e6d52d5ccaab4c8c3ac0b0698e0f01a"
    assert "ratio_min" not in r["parameters"] and r["parameters"]["ttl"] == 8


@pytest.mark.parametrize("name,value", [
    ("round_seconds", .25), ("stable_seconds", .05), ("stable_recovery", ".4"),
    ("ttl", 9), ("lw_native_ttl", 1), ("lw_root_hard_ttl", 4),
    ("post_retest_ttl", 4), ("formation_after_lw_max", 2),
    ("lw_prehistory_seconds", .1), ("lw_hold_seconds", .05),
    ("lw_keep_residual_min", ".05"), ("lw_keep_residual_loss_min", ".1"),
])
def test_revisions_cannot_reset_clocks_or_relax_quality_or_maintenance(name, value):
    d = document(**{name: value})
    d.update(revision_round=1, hypothesis="A_NUMERICAL_HYPOTHESIS")
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE"):
        load_revision(d)


@pytest.mark.parametrize("name,value", [("ask_recovery", ".5"), ("ask_loss", 0),
                                        ("lw_residual_min", ".05"), ("bid_recovery", ".4")])
def test_allowed_numerical_fields_still_preserve_weak_ask_and_strict_support(name, value):
    d = document(**{name: value})
    d.update(revision_round=1, hypothesis="BOUNDARY_REJECTION")
    with pytest.raises(ValueError, match="SEMANTICS_OR_FEASIBILITY"):
        load_revision(d)


@pytest.mark.parametrize("value", [True, None, "NaN", "Infinity", "not_a_number"])
def test_invalid_numbers_are_rejected(value):
    with pytest.raises(ValueError, match="INVALID_PARAMETER_NUMBER"):
        load_revision(document(ask_loss=value))


def test_baseline_cannot_be_silently_relabelled_tuned_or_profit_selected():
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE"):
        load_revision(document(ask_loss=".02"))
    d = document()
    d["revision_round"] = 4
    with pytest.raises(ValueError, match="AT_MOST_THREE"):
        load_revision(d)
    d.update(revision_round=1, hypothesis="")
    with pytest.raises(ValueError, match="HYPOTHESIS"):
        load_revision(d)
    d.update(hypothesis="frequency", pnl_used_for_selection=True)
    with pytest.raises(ValueError, match="FREQUENCY_ONLY"):
        load_revision(d)


def hold_profile_document(**parameters):
    d = document(lw_hold_seconds=.250, **parameters)
    d.update(revision_round=3, hypothesis="LONGER_CONTINUOUS_FORMATION_NOT_FACT_RENEWAL",
             formation_hold_profile="M15_LW_HOLD250MS_ORIGINAL_CLOCKS")
    return d


def test_explicit_longer_hold_profile_keeps_all_other_original_clocks_and_guards():
    p, r = load_revision(hold_profile_document())
    baseline = Parameters()
    assert p.lw_hold_seconds == .250 and baseline.lw_hold_seconds == .100
    assert {k for k in p.__dataclass_fields__ if getattr(p, k) != getattr(baseline, k)} == {"lw_hold_seconds"}
    assert r["formation_hold_profile"] == "M15_LW_HOLD250MS_ORIGINAL_CLOCKS"
    assert r["fixed_causal_rules"]["lw_hold_seconds"] == .250
    assert p.stable_seconds == .100 and p.lw_formation_seconds == .750
    assert p.lw_native_ttl == .750 and p.formation_after_lw_max == 1.250 and p.ttl == 8


@pytest.mark.parametrize("hold", [.050, .100, .200, .300])
def test_explicit_hold_profile_cannot_silently_use_another_duration(hold):
    d = hold_profile_document()
    d["parameters"]["lw_hold_seconds"] = hold
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE.*lw_hold_seconds"):
        load_revision(d)


@pytest.mark.parametrize("profile", [None, False, True, "UNKNOWN", ""])
def test_unknown_or_non_string_hold_profile_is_rejected(profile):
    d = hold_profile_document()
    d["formation_hold_profile"] = profile
    with pytest.raises(ValueError, match="FORMATION_HOLD_PROFILE_NOT_AUTHORIZED"):
        load_revision(d)


def test_hold_change_requires_profile_and_cannot_be_original_revision_zero():
    d = hold_profile_document()
    d.pop("formation_hold_profile")
    with pytest.raises(ValueError, match="FIXED_SOP_CAUSAL_RULE.*lw_hold_seconds"):
        load_revision(d)
    d = hold_profile_document()
    d["revision_round"] = 0
    with pytest.raises(ValueError, match="ORIGINAL_BASELINE_CANNOT_USE_HOLD_PROFILE"):
        load_revision(d)
