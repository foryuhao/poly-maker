"""Final reports must stay bound to the declared real-public-data experiment."""

import json
import sqlite3

import pytest
from m1223_phase_report import collect, read_definition, sha256, source_binding


def phase(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    epochs = list(range(1800000000, 1800000000 + 24 * 300, 300))
    cohort = {
        "strategy": "M12",
        "stage": "INDEPENDENT_VALIDATION",
        "market_epochs": epochs,
        "markets": [f"btc-updown-5m-{e}" for e in epochs],
        "window_start_epoch": epochs[0],
        "window_end_epoch": epochs[-1] + 300,
    }
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(cohort))
    plan = {
        "mode": "SIMULATION_ONLY",
        "real_order_transport": "ABSENT",
        "settings": {
            "max_markets": 24,
            "sop": {"groups": ["M12"]},
            "sop_cohort": cohort,
            "sop_cohort_sha256": sha256(path),
        },
    }
    (run / "plan.json").write_text(json.dumps(plan))
    return run, path, plan


def test_exact_predeclared_public_capture_is_bound(tmp_path):
    run, path, plan = phase(tmp_path)
    assert source_binding(run, path) == (plan, plan["settings"]["sop_cohort"])


def test_bound_metadata_cannot_relabel_validation_as_a_288_market_target(tmp_path):
    run, path, plan = phase(tmp_path)
    cohort = json.loads(path.read_text())
    cohort["frequency_reporting_contract"] = {
        "coverage_target": "0.40", "denominator": 288,
        "minimum_distinct_primary_signal_markets": 10,
        "role": "ADMISSION_ONLY_AFTER_COMPLETE_INDEPENDENT_COHORT",
    }
    path.write_text(json.dumps(cohort))
    plan["settings"].update(sop_cohort=cohort, sop_cohort_sha256=sha256(path))
    (run / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="REPORTING_TARGET_DIFFERS"):
        source_binding(run, path)


@pytest.mark.parametrize("change", ["offline", "real_orders", "other_strategy", "new_cohort"])
def test_other_modes_strategies_or_replaced_markets_cannot_supply_live_proof(tmp_path, change):
    run, path, plan = phase(tmp_path)
    if change == "offline":
        plan["mode"] = "OFFLINE_DEVELOPMENT_REPLAY"
    elif change == "real_orders":
        plan["real_order_transport"] = "PRESENT"
    elif change == "other_strategy":
        plan["settings"]["sop"]["groups"] = ["M13"]
    else:
        path.write_text(path.read_text() + " ")
    (run / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        source_binding(run, path)


def test_artificial_audit_cannot_pass_as_real_public_evidence(tmp_path):
    database = tmp_path / "simulation.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE audit (kind TEXT, payload TEXT)")
        db.execute(
            "INSERT INTO audit VALUES (?,?)",
            ("DEFINITION", json.dumps({"definition": {"artificial_fixture": True}})),
        )
    with pytest.raises(ValueError, match="SYNTHETIC_FIXTURE"):
        read_definition(database)


def test_missing_init_cannot_be_reported_as_zero_signals(tmp_path):
    database = tmp_path / "simulation.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE audit (kind TEXT, payload TEXT)")
    with pytest.raises(ValueError, match="DEFINITION_UNAVAILABLE"):
        read_definition(database)


def test_all_isolated_markets_keep_raw_counts_but_primary_is_no_eligible_sample(tmp_path, monkeypatch):
    run, path, plan = phase(tmp_path)
    plan["run_identity"] = {"run_id": "SYNTHETIC_REPORT_TEST"}
    plan["settings"]["max_rss_mib"] = 512
    plan["settings"]["sop_execution"] = {"mode": "SYNTHETIC_REPORT_TEST"}
    (run / "plan.json").write_text(json.dumps(plan))
    (run / "completion.json").write_text(json.dumps({"reason": "PLANNED_COMPLETE"}))
    for market in plan["settings"]["sop_cohort"]["markets"]:
        directory = run / market
        directory.mkdir()
        (directory / "simulation.sqlite3").touch()
    monkeypatch.setattr("polymaker.m1234_sim.runstate.inspect_run", lambda *_: {"status": "PASS", "errors": []})
    monkeypatch.setattr("m1223_phase_report.read_definition", lambda *_: {"settings": plan["settings"]})

    def result(database, _project):
        return {"market": database.parent.name, "status": "SEALED", "audit_validity": "VALID",
                "complete": True, "unknown": False, "primary_eligible": False,
                "signal_ids": [database.parent.name + ":SYNTHETIC"], "signal_count": 1}, []

    monkeypatch.setattr("m1223_phase_report.market_result", result)
    report = collect(run, path, project=tmp_path)
    assert report["known_raw_signal_count"] == 24 and report["all_recorded"] is not None
    assert report["primary_metrics_status"] == "NO_ELIGIBLE_SAMPLES"
    assert report["known_primary_signal_count"] is None and report["primary"] is None
