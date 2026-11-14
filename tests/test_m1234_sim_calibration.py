"""R4: artificial hand-computed order-level comparisons with nonempty denominators."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from decimal import Decimal as D

import pytest

from polymaker.m1234_sim.calibration import assess, load_records


def actual_partials():
    base = {"market": "m", "token": "1", "order_id": "o", "physical_submit_id": "p",
            "submitted_at": 100, "fill_price": ".10", "evidence": "ACTUAL_FILL", "fixture_only": True}
    return [{**base, "fill_id": "f1", "fill_at": 101, "fill_quantity": "4"},
            {**base, "fill_id": "f2", "fill_at": 102, "fill_quantity": "6"}]


def model_partials():
    return [{**row, "evidence": "MODELLED_FILL"} for row in actual_partials()]


def one_comparison(result):
    assert result["matched_physical_orders"] == 1
    assert len(result["differences"]) == 1, "Empty comparisons cannot prove zero error"
    return result["differences"][0]


def test_identical_four_plus_six_is_one_physical_order_with_nonempty_zero_difference():
    result = assess(actual_partials(), model_partials(), split_at=150)
    row = one_comparison(result)
    assert row["actual"]["observed_quantity"] == row["modelled"]["observed_quantity"] == "10"
    assert row["quantity_delta"] == "0" and row["wait_delta_seconds"] == 0
    assert row["actual"]["first_fill_wait_seconds"] == row["modelled"]["first_fill_wait_seconds"] == 1
    assert result["matched_actual_fill_records"] == result["matched_modelled_fill_records"] == 2
    assert result["actual_input"]["unique_physical_orders"] == 1
    assert result["zero_observed_quantity_error"] and result["zero_comparable_wait_error"]
    assert result["calibration_status"] == "UNCALIBRATED"
    assert row["actual"]["full_fill_wait_seconds"] is None


def test_all_valid_metrics_are_invariant_to_input_order_on_both_sides():
    normal = assess(actual_partials(), model_partials(), split_at=150)
    reversed_inputs = assess(list(reversed(actual_partials())), list(reversed(model_partials())), split_at=150)
    one_comparison(normal)
    one_comparison(reversed_inputs)
    assert normal == reversed_inputs


def test_original_reference_control_input_is_nonempty_and_explicitly_not_model_evidence():
    a = actual_partials()
    result = assess(a, deepcopy(a), split_at=150)
    row = one_comparison(result)
    assert row["quantity_delta"] == "0" and row["wait_delta_seconds"] == 0
    assert result["modelled_reference_control"] and result["calibration_status"] == "UNCALIBRATED"


def test_different_fill_splitting_does_not_create_quantity_error():
    model = [{**model_partials()[0], "fill_quantity": "10"}]
    row = one_comparison(assess(actual_partials(), model))
    assert row["quantity_delta"] == "0" and row["wait_delta_seconds"] == 0
    assert row["actual"]["fill_records"] == 2 and row["modelled"]["fill_records"] == 1


def test_two_physical_retries_with_reused_local_fill_ids_remain_two_orders():
    actual = [*actual_partials(), *[{**r, "physical_submit_id": "retry-2"} for r in actual_partials()]]
    model = [{**r, "evidence": "MODELLED_FILL"} for r in actual]
    result = assess(actual, list(reversed(model)))
    assert result["matched_physical_orders"] == len(result["differences"]) == 2
    assert result["matched_actual_fill_records"] == result["matched_modelled_fill_records"] == 4
    assert all(r["quantity_delta"] == "0" for r in result["differences"])


@pytest.mark.parametrize("field", ["market", "token", "order_id", "physical_submit_id"])
def test_full_physical_key_is_required_for_a_match(field):
    model = [{**r, field: "other"} for r in model_partials()]
    result = assess(actual_partials(), model)
    assert result["matched_physical_orders"] == 0 and not result["differences"]
    assert len(result["unmatched_actual_physical_orders"]) == len(result["unmatched_modelled_physical_orders"]) == 1
    assert result["unmatched_actual_fill_records"] == result["unmatched_modelled_fill_records"] == 2
    assert not result["zero_observed_quantity_error"]


def test_exact_duplicate_is_visible_and_does_not_double_count_shares():
    actual = [*actual_partials(), deepcopy(actual_partials()[0])]
    result = assess(actual, model_partials())
    row = one_comparison(result)
    assert row["actual"]["observed_quantity"] == "10" and row["quantity_delta"] == "0"
    assert result["records"] == 3 and result["usable_actual_fills"] == 2
    assert len(result["actual_input"]["duplicates"]) == 1


@pytest.mark.parametrize("side", ["actual", "modelled"])
def test_conflicting_fill_id_excludes_the_order_instead_of_keeping_first_record(side):
    actual, model = actual_partials(), model_partials()
    selected = actual if side == "actual" else model
    selected.append({**selected[0], "fill_quantity": "5"})
    result = assess(actual, model)
    assert result["matched_physical_orders"] == 0 and not result["zero_observed_quantity_error"]
    assert "CONFLICTING_FILL_ID" in result[side + "_input"]["invalid_physical_orders"][0]["reasons"]


@pytest.mark.parametrize("side", ["actual", "modelled"])
@pytest.mark.parametrize("change", [
    {"fill_quantity": "0"}, {"fill_quantity": "NaN"}, {"fill_price": "1"},
    {"fill_at": 99}, {"submitted_at": "Infinity"}, {"timestamps_reliable": False},
    {"evidence": "ACK"}, {"fill_id": []},
])
def test_invalid_input_on_either_side_is_reported_without_false_zero_error(side, change):
    actual, model = actual_partials(), model_partials()
    selected = actual if side == "actual" else model
    selected[0].update(change)
    result = assess(actual, model)
    assert result["matched_physical_orders"] == 0
    assert result[side + "_input"]["rejected"]
    assert not result["zero_observed_quantity_error"] and not result["zero_comparable_wait_error"]


def test_related_partial_identity_does_not_silently_drop_a_second_fill():
    actual = actual_partials()
    del actual[1]["physical_submit_id"]
    result = assess(actual, model_partials())
    assert result["missing_fields"]["physical_submit_id"] == 1
    assert result["matched_physical_orders"] == 0
    assert "RELATED_INCOMPLETE_FILL_RECORD" in result["actual_input"]["invalid_physical_orders"][0]["reasons"]


def test_conflicting_submission_times_are_not_merged_into_one_wait():
    actual = actual_partials()
    actual[1]["submitted_at"] = 100.5
    result = assess(actual, model_partials())
    assert result["matched_physical_orders"] == 0
    assert "CONFLICTING_SUBMISSION_TIMES" in result["actual_input"]["invalid_physical_orders"][0]["reasons"]


def test_partition_follows_submission_for_all_fills_and_completion_is_not_inferred_from_last():
    result = assess(actual_partials(), model_partials(), split_at=101.5)
    row = one_comparison(result)
    assert row["partition"] == "CALIBRATION" and row["crosses_split_during_fills"]
    assert row["actual"]["last_observed_fill_wait_seconds"] == 2
    assert row["actual"]["full_fill_wait_seconds"] is None
    sized_actual = [{**r, "order_quantity": "10"} for r in actual_partials()]
    sized_model = [{**r, "order_quantity": "10"} for r in model_partials()]
    sized = one_comparison(assess(sized_actual, sized_model))
    assert sized["actual"]["full_fill_wait_seconds"] == 2
    assert sized["actual"]["completion_status"] == "FULL_QUANTITY_OBSERVED"
    partial = one_comparison(assess(sized_actual[:1], sized_model[:1]))
    assert partial["actual"]["full_fill_wait_seconds"] is None


def test_postfill_metric_is_quantity_weighted_and_requires_same_horizon_and_basis():
    actual, model = actual_partials(), model_partials()
    for records in (actual, model):
        for row, value in zip(records, [".01", ".02"], strict=True):
            row.update({"postfill_change": value, "postfill_horizon_seconds": 1,
                        "postfill_time_basis": "EXCHANGE_FILL_TIME"})
    model[0]["fill_quantity"], model[1]["fill_quantity"] = "2", "8"
    row = one_comparison(assess(actual, model))
    assert D(row["postfill_result_delta"]) == D(".002")
    assert row["postfill_status"] == "AVAILABLE_QUANTITY_WEIGHTED"
    model[0]["postfill_horizon_seconds"] = 5
    assert one_comparison(assess(actual, model))["postfill_result_delta"] is None


def test_incompatible_clock_bases_do_not_create_a_wait_difference():
    actual = [{**r, "time_basis": "EXCHANGE_EXECUTION"} for r in actual_partials()]
    model = [{**r, "time_basis": "LOCAL_AVAILABILITY"} for r in model_partials()]
    row = one_comparison(assess(actual, model))
    assert row["quantity_delta"] == "0" and row["wait_delta_seconds"] is None
    assert row["wait_comparison_status"] == "INCOMPATIBLE_TIME_BASES"


def test_counterfactual_model_contexts_are_never_combined_as_one_order():
    model = [*[{**r, "scenario": "a"} for r in model_partials()],
             *[{**r, "scenario": "b"} for r in model_partials()]]
    result = assess(actual_partials(), model)
    assert result["matched_physical_orders"] == 0
    assert "AMBIGUOUS_PROFILE_OR_SCENARIO_SELECT_ONE_CONTEXT" in result["modelled_input"]["invalid_physical_orders"][0]["reasons"]


@pytest.mark.parametrize("model", [None, []])
def test_no_comparison_is_never_claimed_as_zero_error(model):
    result = assess(actual_partials(), model)
    assert not result["differences"] and result["matched_physical_orders"] == 0
    assert not result["zero_observed_quantity_error"] and not result["zero_comparable_wait_error"]


def test_cli_same_partial_records_produces_one_nonempty_order_comparison(tmp_path):
    actual, model = tmp_path / "actual.jsonl", tmp_path / "model.jsonl"
    for path, data in [(actual, actual_partials()), (model, model_partials())]:
        path.write_text(''.join(json.dumps(row) + '\n' for row in data))
    output = tmp_path / "comparison.json"
    process = subprocess.run([sys.executable, '-m', 'polymaker.m1234_sim', 'calibrate',
                              '--actual', str(actual), '--modelled', str(model), '--split-at', '150',
                              '--output', str(output)], capture_output=True, text=True, env=os.environ.copy())
    assert process.returncode == 0, process.stdout + process.stderr
    row = one_comparison(json.loads(output.read_text()))
    assert row["quantity_delta"] == "0" and row["wait_delta_seconds"] == 0
    assert len(load_records(actual)) == 2
