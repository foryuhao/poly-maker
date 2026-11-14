"""Guards for combining independently calibrated strategy profiles."""

import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("final_selection", Path(__file__).parents[1] / "ops/m510_final_selection.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value)+"\n")


@pytest.fixture
def experiment(tmp_path):
    cohort = {"markets": [{"market": f"market-{i}"} for i in range(25)]}
    put(tmp_path, "cohort25.json", cohort)
    sha = hashlib.sha256((tmp_path / "cohort25.json").read_bytes()).hexdigest()
    put(tmp_path, "phase3-frequency-rule.json", {"cohort_sha256": sha, "priority": ["control_v4", "fast"]})
    put(tmp_path, "phase4-failed-root-rule.json", {"cohort_sha256": sha,
        "signal_variant": "v6", "primary": "control_v6", "priority": ["control_v6", "restart_only"]})
    put(tmp_path, "phase4-freeze.json", {"source_root": str(tmp_path / "frozen"), "manifest_sha256": "frozen-sha"})
    params = {s: {"profile": "base"} for s in MOD.TARGETS}

    def signal(strategy, i):
        return {"strategy": strategy, "token": "yes", "confirmed": i, "deadline": i+8, "anchor_price": ".5"}

    rows3, rows4 = [], []
    for i, member in enumerate(cohort["markets"]):
        a = {"market": member["market"], "profiles": {}}
        for name, n9, n10 in (("control_v4", 10, 8), ("fast", 13, 10)):
            p = deepcopy(params)
            if name == "fast":
                p["M9"]["profile"] = p["M10"]["profile"] = "fast"
            a["profiles"][name] = {"parameters": p, "signals": [
                signal(s, i) for s, n in (("M9", n9), ("M10", n10)) if i < n]}
        b = {"market": member["market"], "profiles": {
            "control_v6": {"parameters": deepcopy(params), "signals": [signal(s, i) for s in ("M5", "M6", "M8")]},
            "restart_only": {"parameters": deepcopy(params), "signals": [signal("M7", i)] if i < 13 else []}}}
        b["profiles"]["restart_only"]["parameters"]["M7"]["restart_after_failed_attack"] = True
        rows3.append(a)
        rows4.append(b)
    put(tmp_path, "frequency-grid-phase3-v2/progress.json", {"markets": rows3})
    put(tmp_path, "frequency-grid-phase4/progress.json", {"markets": rows4})
    put(tmp_path, "phase3-grid-review.json", {"status": "COMPLETE", "errors": [],
        "counts": {"control_v4": {"M7": 0, "M9": 10, "M10": 8}, "fast": {"M7": 0, "M9": 13, "M10": 10}}})
    put(tmp_path, "phase4-grid-review.json", {"status": "COMPLETE", "errors": [],
        "M7_signals_by_profile": {"control_v6": 0, "restart_only": 13}, "selection": {"profile": "restart_only"}})
    put(tmp_path, "control25-config-v5-review.json", {"status": "PASS", "errors": []})
    return tmp_path


def test_combines_selected_strategies_and_untouched_primary_without_reading_pnl(experiment):
    result = MOD.select(experiment)
    assert result["profiles"] == {"M7": "restart_only", "M9": "control_v4", "M10": "fast"}
    assert result["expected_counts"] == {"M7": 13, "M9": 10, "M10": 10}
    assert result["frequency_target_met"] and result["selection_uses_pnl"] is False
    assert len(result["expected_signatures"]["market-0"]) == 6
    assert MOD.select(experiment) == result


def test_incomplete_control_never_emits_selection(experiment):
    put(experiment, "control25-config-v5-review.json", {"status": "PARTIAL"})
    with pytest.raises(ValueError, match="COMPLETE_INDEPENDENT"):
        MOD.select(experiment)
    assert not (experiment / "combined-selection-v6.json").exists()


def test_changed_parameters_or_existing_evidence_are_rejected(experiment):
    MOD.select(experiment)
    path = experiment / "combined-parameters-v6.json"
    path.write_text("{}\n")
    with pytest.raises(ValueError, match="EXISTING_PARAMETERS_CHANGED"):
        MOD.select(experiment)
    assert path.read_text() == "{}\n"


def test_count_rule_does_not_claim_subtarget_pass():
    assert MOD.choose(["base", "a", "b"], {"base": 7, "a": 9, "b": 9}) == "a"
    assert MOD.choose(["base", "a", "b"], {"base": 10, "a": 20, "b": 30}) == "base"


def test_preview_does_not_emit_a_final_configuration(experiment):
    record = MOD.select(experiment, emit=False)
    assert record["frequency_target_met"]
    assert not (experiment / "combined-selection-v6.json").exists()
    assert not (experiment / "combined-parameters-v6.json").exists()


def test_passing_numeric_m7_precedes_additional_state_machine(experiment):
    review = MOD.read(experiment / "phase3-grid-review.json")
    review["counts"]["fast"]["M7"] = 10
    put(experiment, "phase3-grid-review.json", review)
    progress = MOD.read(experiment / "frequency-grid-phase3-v2/progress.json")
    for i, row in enumerate(progress["markets"][:10]):
        row["profiles"]["fast"]["signals"].append({"strategy": "M7", "token": "yes",
            "confirmed": i, "deadline": i+8, "anchor_price": ".5"})
    put(experiment, "frequency-grid-phase3-v2/progress.json", progress)
    selected = MOD.select(experiment)
    assert selected["profiles"]["M7"] == "fast"
    assert selected["source_grids"]["M7"] == "phase3"
    assert selected["frequency_parameters"]["M7"]["restart_after_failed_attack"] is False
    assert selected["expected_counts"]["M7"] == 10


def phase5(experiment, *, complete=True):
    sha = hashlib.sha256((experiment / "cohort25.json").read_bytes()).hexdigest()
    priority = {s: [s.lower()] for s in MOD.TARGETS}
    put(experiment, "phase5-window-rule.json", {
        "cohort_sha256": sha, "primary": "control_v6", "priority_by_strategy": priority})
    put(experiment, "phase5-freeze.json", {
        "source_root": str(experiment / "frozen5"), "manifest_sha256": "frozen5-sha"})
    rows = MOD.read(experiment / "frequency-grid-phase4/progress.json")["markets"]
    for index, row in enumerate(rows):
        for strategy in MOD.TARGETS:
            parameters = deepcopy(row["profiles"]["control_v6"]["parameters"])
            parameters[strategy]["profile"] = "phase5"
            row["profiles"][strategy.lower()] = {"parameters": parameters, "signals": [
                {"strategy": strategy, "token": "yes", "confirmed": index,
                 "deadline": index+8, "anchor_price": ".5"}] if index < 11 else []}
    put(experiment, "frequency-grid-phase5/progress.json", {"markets": rows})
    put(experiment, "phase5-grid-review.json", {
        "status": "COMPLETE" if complete else "PARTIAL", "errors": [],
        "counts": {s: {s.lower(): 11} for s in MOD.TARGETS}})


def test_incomplete_declared_phase5_prevents_early_selection(experiment):
    phase5(experiment, complete=False)
    with pytest.raises(ValueError, match="COMPLETE_INDEPENDENT_PHASE5"):
        MOD.select(experiment)
    assert not (experiment / "combined-selection-v6.json").exists()


def test_phase5_numeric_m7_precedes_restart_and_preserves_earlier_passing_profiles(experiment):
    phase5(experiment)
    selected = MOD.select(experiment)
    assert selected["profiles"] == {"M7": "m7", "M9": "control_v4", "M10": "fast"}
    assert selected["source_grids"] == {"M7": "phase5", "M9": "phase3", "M10": "phase3"}
    assert selected["frequency_parameters"]["M7"]["restart_after_failed_attack"] is False
    assert selected["source_root"].endswith("frozen5")
    assert selected["expected_counts"] == {"M7": 11, "M9": 10, "M10": 10}


def test_unscaled_short_pause_cannot_win_selection_even_if_it_has_ten_signals(experiment):
    review = MOD.read(experiment / "phase3-grid-review.json")
    review["counts"]["fast"]["M7"] = 10
    put(experiment, "phase3-grid-review.json", review)
    progress = MOD.read(experiment / "frequency-grid-phase3-v2/progress.json")
    for index, row in enumerate(progress["markets"]):
        row["profiles"]["fast"]["parameters"]["M7"].update(
            pause_seconds=.25, pause_total_fraction=".50")
        if index < 10:
            row["profiles"]["fast"]["signals"].append({"strategy": "M7", "token": "yes",
                "confirmed": index, "deadline": index+8, "anchor_price": ".5"})
    put(experiment, "frequency-grid-phase3-v2/progress.json", progress)
    selected = MOD.select(experiment)
    assert selected["profiles"]["M7"] == "restart_only"
    assert selected["semantic_exclusions"][0]["profile"] == "fast"
    assert selected["semantic_exclusions"][0]["reason"] == "PAUSE_CAN_ADMIT_NONDECREASING_AVERAGE_BUY_RATE"


def test_short_pause_scaling_preserves_exhaustion_and_flat_buy_rate_is_rejected():
    assert MOD.m7_pause_is_exhaustion({"pause_seconds": .5, "pause_total_fraction": ".50"})
    assert MOD.m7_pause_is_exhaustion({"pause_seconds": .25, "pause_total_fraction": ".25"})
    assert not MOD.m7_pause_is_exhaustion({"pause_seconds": .375, "pause_total_fraction": ".50"})
    assert not MOD.m7_pause_is_exhaustion({"pause_seconds": .25, "pause_total_fraction": ".50"})
    assert MOD.m7_pause_is_exhaustion({"ap_window_seconds": .25, "pause_seconds": .25,
                                      "pause_total_fraction": ".50"})
    assert not MOD.m7_pause_is_exhaustion({"ap_window_seconds": .5, "pause_seconds": .25,
                                          "pause_total_fraction": ".50"})


def phase6(experiment, *, complete=True, lower_public=False):
    phase5(experiment)
    if lower_public:
        for phase, name, counts_key, limits in (
                (3, "frequency-grid-phase3-v2", "counts", {"fast": {"M10": 9}}),
                (4, "frequency-grid-phase4", "M7_signals_by_profile", {"restart_only": {"M7": 9}}),
                (5, "frequency-grid-phase5", "counts", {"m7": {"M7": 6}, "m10": {"M10": 7}})):
            value = MOD.read(experiment / f"phase{phase}-grid-review.json")
            rows = MOD.read(experiment / name / "progress.json")["markets"]
            for profile, targets in limits.items():
                for strategy, maximum in targets.items():
                    if phase == 3:
                        value[counts_key][profile][strategy] = maximum
                    elif phase == 4:
                        value[counts_key][profile] = maximum
                    else:
                        value[counts_key][strategy][profile] = maximum
                    for index, row in enumerate(rows):
                        if index >= maximum:
                            row["profiles"][profile]["signals"] = [
                                s for s in row["profiles"][profile]["signals"] if s["strategy"] != strategy]
            put(experiment, f"phase{phase}-grid-review.json", value)
            put(experiment, name+"/progress.json", {"markets": rows})
    sha = hashlib.sha256((experiment / "cohort25.json").read_bytes()).hexdigest()
    put(experiment, "phase6-local-ap-rule.json", {"cohort_sha256": sha,
        "primary": "control_v7", "signal_variant": "v7",
        "priority_by_strategy": {"M7": ["local_m7"], "M10": ["local_m10"]}})
    put(experiment, "phase6-freeze.json", {
        "source_root": str(experiment / "frozen6"), "manifest_sha256": "frozen6-sha"})
    original = MOD.read(experiment / "frequency-grid-phase5/progress.json")["markets"]
    rows = []
    for index, row in enumerate(original):
        base = deepcopy(row["profiles"]["control_v6"])
        profiles = {"control_v7": base}
        for strategy, maximum in (("M7", 14), ("M10", 12)):
            parameters = deepcopy(base["parameters"])
            parameters[strategy]["ap_window_seconds"] = .5
            profiles["local_"+strategy.lower()] = {"parameters": parameters, "signals": [
                {"strategy": strategy, "token": "yes", "confirmed": index,
                 "deadline": index+8, "anchor_price": ".5"}] if index < maximum else []}
        rows.append({"market": row["market"], "profiles": profiles})
    put(experiment, "frequency-grid-phase6/progress.json", {"markets": rows})
    put(experiment, "phase6-grid-review.json", {"status": "COMPLETE" if complete else "PARTIAL",
        "errors": [], "counts": {"M7": {"local_m7": 14}, "M10": {"local_m10": 12}}})


def test_incomplete_local_grid_blocks_final_selection_but_public_preview_emits_nothing(experiment):
    phase6(experiment, complete=False)
    with pytest.raises(ValueError, match="COMPLETE_INDEPENDENT_PHASE6"):
        MOD.select(experiment)
    public = MOD.select(experiment, emit=False, include_local=False)
    assert not public["source_root"].endswith("frozen6")
    assert not (experiment / "combined-selection-v6.json").exists()


def test_public_ap_profile_is_preferred_when_it_already_passes(experiment):
    phase6(experiment)
    selected = MOD.select(experiment)
    assert selected["source_grids"]["M7"] == "phase5"
    assert selected["frequency_parameters"]["M7"]["ap_window_seconds"] == .75
    assert selected["signal_variant"] == "v7"


def test_local_ap_is_selected_only_after_complete_count_evidence(experiment):
    phase6(experiment, lower_public=True)
    selected = MOD.select(experiment)
    assert selected["source_grids"] == {"M7": "phase6", "M9": "phase3", "M10": "phase6"}
    assert selected["frequency_parameters"]["M7"]["ap_window_seconds"] == .5
    assert selected["frequency_parameters"]["M10"]["ap_window_seconds"] == .5
    assert selected["expected_counts"] == {"M7": 14, "M9": 10, "M10": 12}
    assert selected["frequency_target_met"]
