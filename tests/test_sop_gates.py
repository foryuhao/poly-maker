import hashlib
import json
from copy import deepcopy

import pytest
from m1223_gates import phase_admission


def setup(tmp_path):
    sop = {"parameter_sha256": "synthetic-test-only"}
    development = list(range(1800000000, 1800000000 + 24 * 300, 300))
    epochs = list(range(development[-1] + 300, development[-1] + 300 + 24 * 300, 300))
    evidence = tmp_path / "frequency.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "PASS",
                "selection_uses_pnl": False,
                "sop_definition": sop,
                "market_epochs": development,
                "revision": {"parameter_sha256": sop["parameter_sha256"], "revision_round": 1},
                "frequency": {
                    "denominator": 24,
                    "primary_signal_markets": 10,
                    "missing_markets": [],
                },
            }
        )
    )
    selection = {
        "schema": "m12-frequency-selection-v1",
        "sample_role": "DEVELOPMENT_ONLY",
        "selection_uses_pnl": False,
        "selected_sop_definition": sop,
        "development_market_epochs": development,
        "frequency_report": {
            "path": str(evidence),
            "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        },
    }
    report = {
        "schema": "m12-independent-validation-report-v1",
        "sample_role": "INDEPENDENT_VALIDATION",
        "mode": "REALTIME_PUBLIC_DATA_SIMULATION",
        "phase_complete": True,
        "run_status": "PASS",
        "sop_definition": sop,
        "batch_number": 1,
        "market_epochs": epochs,
        "markets": [
            {
                "market": f"btc-updown-5m-{e}",
                "primary_eligible": True,
                "audit_validity": "VALID",
                "signal_ids": [f"signal{i}"] if i < 10 else [],
            }
            for i, e in enumerate(epochs)
        ],
    }
    return sop, development, selection, report


def cohort(epochs, stage):
    return {
        "strategy": "M12",
        "stage": stage,
        "market_epochs": epochs,
        "markets": [f"btc-updown-5m-{e}" for e in epochs],
        "window_start_epoch": epochs[0],
        "window_end_epoch": epochs[-1] + 300,
        "batch_number": 1,
    }


def test_development_and_independent_phases_cannot_grant_24h_admission(tmp_path):
    sop, dev, selection, report = setup(tmp_path)
    cfg = {"max_markets": 24, "sop": sop}
    assert not phase_admission(cohort(dev, "DEVELOPMENT_CAPTURE"), cfg)["admission_granted_for_24h"]
    gate = phase_admission(
        cohort(report["market_epochs"], "INDEPENDENT_VALIDATION"), cfg, selection=selection
    )
    assert not gate["admission_granted_for_24h"]


def day(report):
    first = report["market_epochs"][-1] + 300
    return cohort(list(range(first, first + 288 * 300, 300)), "CONTINUOUS_24H_TEST")


def test_only_independent_10_distinct_proven_markets_admit_a_new_full_day(tmp_path):
    sop, _dev, selection, report = setup(tmp_path)
    gate = phase_admission(
        day(report),
        {"max_markets": 288, "sop": sop},
        selection=selection,
        independent_report=report,
    )
    assert gate["admission_granted_for_24h"] and gate["primary_signal_markets"] == 10
    assert gate["denominator"] == 24


@pytest.mark.parametrize(
    "corruption",
    [
        "offline",
        "incomplete",
        "same_markets",
        "one_market_many_signals",
        "contaminated",
        "unknown",
        "third_batch",
    ],
)
def test_no_shortcuts_from_tuned_data_many_signals_or_unproved_markets(tmp_path, corruption):
    sop, dev, selection, report = setup(tmp_path)
    planned_day = day(report)
    if corruption == "offline":
        report["mode"] = "OFFLINE_DEVELOPMENT_REPLAY"
    elif corruption == "incomplete":
        report["phase_complete"] = False
    elif corruption == "same_markets":
        report["market_epochs"] = dev
    elif corruption == "one_market_many_signals":
        for r in report["markets"]:
            r["signal_ids"] = []
        report["markets"][0]["signal_ids"] = [str(i) for i in range(100)]
    elif corruption == "contaminated":
        report["markets"][0]["primary_eligible"] = False
    elif corruption == "unknown":
        report["markets"][0]["audit_validity"] = "UNAVAILABLE"
    else:
        report["batch_number"] = 3
    with pytest.raises(ValueError):
        phase_admission(
            planned_day,
            {"max_markets": 288, "sop": sop},
            selection=selection,
            independent_report=report,
        )


def test_changed_evidence_or_model_identity_is_rejected(tmp_path):
    sop, _dev, selection, report = setup(tmp_path)
    cfg = {"max_markets": 288, "sop": sop}
    changed = deepcopy(selection)
    changed["selected_sop_definition"] = {"parameter_sha256": "other"}
    with pytest.raises(ValueError):
        phase_admission(day(report), cfg, selection=changed, independent_report=report)
    with open(selection["frequency_report"]["path"], "a") as f:
        f.write(" ")
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED"):
        phase_admission(day(report), cfg, selection=selection, independent_report=report)


def test_independent_batch_counter_cannot_exceed_two(tmp_path):
    sop, _dev, selection, report = setup(tmp_path)
    c = cohort(report["market_epochs"], "INDEPENDENT_VALIDATION")
    c["batch_number"] = 3
    with pytest.raises(ValueError, match="AT_MOST_TWO"):
        phase_admission(c, {"max_markets": 24, "sop": sop}, selection=selection)


def test_parameter_candidates_cannot_launch_extra_live_development_search(tmp_path):
    sop, dev, _selection, _report = setup(tmp_path)
    with pytest.raises(ValueError, match="NOT_LIVE_PARAMETER_SEARCH"):
        phase_admission(
            cohort(dev, "DEVELOPMENT_CAPTURE"),
            {"max_markets": 24, "sop": sop, "sop_parameter_revision": {"revision_round": 1}},
        )


@pytest.mark.parametrize("change", ["implementation", "market_list"])
def test_frequency_count_cannot_be_rebound_to_another_implementation_or_dataset(tmp_path, change):
    sop, _dev, selection, report = setup(tmp_path)
    path = selection["frequency_report"]["path"]
    with open(path) as stream:
        evidence = json.load(stream)
    if change == "implementation":
        evidence["sop_definition"]["implementation_version"] = "OTHER_UNTESTED_CODE"
    else:
        evidence["market_epochs"] = report["market_epochs"]
    with open(path, "w") as stream:
        json.dump(evidence, stream)
    with open(path, "rb") as stream:
        selection["frequency_report"]["sha256"] = hashlib.sha256(stream.read()).hexdigest()
    with pytest.raises(ValueError):
        phase_admission(
            cohort(report["market_epochs"], "INDEPENDENT_VALIDATION"),
            {"max_markets": 24, "sop": sop},
            selection=selection,
        )
