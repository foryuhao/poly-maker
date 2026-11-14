"""A passing older calibration must not certify a later report configuration."""

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "m510_report", Path(__file__).parents[1] / "ops/m510_next_report.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def put(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def evidence(tmp_path):
    run = "selected25-v7"
    source, parameter_sha = "/frozen-final-v7", "final-parameter-sha"
    put(tmp_path, "combined-selection-v6.json", {
        "source_root": source, "parameters_sha256": parameter_sha,
        "frequency_parameters": {"M7": {"ap_window_seconds": .5}},
        "expected_counts": {s: 10 for s in ("M7", "M9", "M10")}})
    put(tmp_path, "final-parameter-selection.json", {
        "expected_confirmed_counts": {"M7": 5, "M9": 7, "M10": 7},
        "resolved_parameters": {"M7": {"ap_window_seconds": .75}}})
    put(tmp_path, run+"/plan.json", {"frequency_parameters_input_sha256": parameter_sha})
    put(tmp_path, run+"/import-proof.json", {"expected_source_root": source})
    put(tmp_path, run+"-signature-review.json", {
        "status": "PASS_MATCHED_CALIBRATION", "checked_markets": 25})
    put(tmp_path, "memory288-release-phase3-review.json", {
        "status": "PASS", "retirement_mode": "GC_FREE_DURABLE_RETIREMENT"})
    put(tmp_path, "memory288-selected-v7-review.json", {
        "status": "PASS", "configuration_from_run": run, "checked_markets": 288,
        "execution": "trade-through", "source_root": source,
        "frequency_parameters_input_sha256": parameter_sha})
    put(tmp_path, "native288-selected-v7/result.json", {
        "status": "PASS", "source_root": source,
        "frequency_parameters_input_sha256": parameter_sha})
    put(tmp_path, "invariance-"+run+".json", {
        "status": "PASS", "optimized_run": run, "checked_markets": 25,
        "implementation_and_frequency_identical": True})
    put(tmp_path, "ledger-validation-"+run+".json", {"status": "PASS"})
    put(tmp_path, "cohort-quality.json", {
        "cohort25_status_counts": {"HAS_AMBIGUOUS_INTERVALS": 25}})
    put(tmp_path, "data-scope-decision.json", {"interval_qualified_acceptance": True})
    put(tmp_path, "cohort25.json", {"markets": [
        {"market": f"btc-updown-5m-{1794305700+300*i}"} for i in range(25)]})
    metrics = {key: 0 for key in (
        "submitted", "assumed_filled", "modelled_filled", "unresolved",
        "assumed_trading_market_count", "modelled_trading_market_count", "assumed_pnl",
        "modelled_pnl", "modelled_pnl_lower", "modelled_pnl_upper", "rule_unfillable_orders",
        "unresolved_rule_unfillable_orders", "assumed_without_best_1", "assumed_without_best_3",
        "assumed_low_price_pnl", "modelled_without_best_1", "modelled_without_best_3",
        "modelled_low_price_pnl")}
    metrics["signals"] = 10
    through = {s+"/research/trade-through": dict(metrics) for s in MOD.GROUPS}
    legacy = {s+"/research/"+q: dict(metrics) for s in MOD.GROUPS
              for q in ("trades-only", "cancel-half")}
    put(tmp_path, "comparison-final.json", {
        "status": "COMPLETE", "runs": {
            "oldsignals-through25": {"results": through},
            run: {"results": through}, "baseline25": {"results": legacy}},
        "incremental_signal_cohorts": {"definition": "fixed physical cohorts", "results": {
            s: {"orders": {}, "added_candidates": 0, "lost_candidates": 0,
                "common_candidates": 10} for s in ("M7", "M9", "M10")}},
        "submission_cohort_equivalence_oldqueue_to_through": {"strategies": {}},
        "unchanged_M5_M6_M8_candidate_semantics": True,
        "unchanged_M5_M6_M8_execution": {"strategies": {}}})
    return tmp_path, run, source, parameter_sha


def test_report_uses_matching_combined_parameters_and_can_pass(evidence):
    root, run, _, _ = evidence
    result = MOD.render(root, "comparison-final.json", run)
    report = Path(result["file"]).read_text()
    assert result["memory_verified"]
    assert result["selection_matches_run_source_and_parameters"]
    assert result["source_scope_accepted"]
    assert not result["whole_window_reconstruction_verified"]
    assert "'ap_window_seconds': 0.5" in report
    assert "独立校准计数：{'M7': 10, 'M9': 10, 'M10': 10}" in report
    assert "均未达到每策略10个目标" not in report
    assert result["file"].endswith("/report.md")


@pytest.mark.parametrize("mismatch", ["source", "parameters", "missing_hashes"])
def test_old_or_missing_configuration_cannot_certify_new_run(evidence, mismatch):
    root, run, source, sha = evidence
    path = root / "combined-selection-v6.json"
    selection = json.loads(path.read_text())
    if mismatch == "source":
        selection["source_root"] = "/different-frozen-source"
    elif mismatch == "parameters":
        selection["parameters_sha256"] = "old-parameter-sha"
    else:
        selection.pop("parameters_sha256")
        put(root, run+"/plan.json", {})
        for name in ("memory288-selected-v7-review.json", "native288-selected-v7/result.json"):
            value = json.loads((root / name).read_text())
            value.pop("frequency_parameters_input_sha256")
            put(root, name, value)
    put(root, "combined-selection-v6.json", selection)
    result = MOD.render(root, "comparison-final.json", run)
    assert not result["selection_matches_run_source_and_parameters"]
    assert not result["memory_verified"]
    assert not result["final_configuration_resource_verified"]
    assert result["file"].endswith("/report-draft.md")
    report = Path(result["file"]).read_text()
    assert "尚无匹配本次源码与参数的选型记录" in report
    assert "'ap_window_seconds': 0.75" not in report


def test_partial_signature_review_cannot_complete_report(evidence):
    root, run, _, _ = evidence
    put(root, run+"-signature-review.json", {
        "status": "PASS_MATCHED_CALIBRATION", "checked_markets": 1})
    result = MOD.render(root, "comparison-final.json", run)
    assert result["file"].endswith("/report-draft.md")
