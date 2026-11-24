"""Phase gates: development never substitutes for independent real-time proof."""

import hashlib
import json
from pathlib import Path


def strategy_schema(sop, suffix):
    strategy = sop.get("strategy", "M12")
    if strategy not in {"M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M21", "M23"} or sop.get("groups", [strategy]) != [strategy]:
        raise ValueError("SINGLE_IMPLEMENTED_SOP_STRATEGY_REQUIRED")
    return strategy.lower() + "-" + suffix


def frequency_reporting_contract(stage, denominator):
    counts = {
        "DEVELOPMENT_CAPTURE": 24,
        "INDEPENDENT_VALIDATION": 24,
        "CONTINUOUS_24H_TEST": 288,
        "USER_DIRECT_24H_TEST": 288,
    }
    roles = {
        "DEVELOPMENT_CAPTURE": "DEVELOPMENT_SELECTION_ONLY_NOT_INDEPENDENT_ADMISSION",
        "INDEPENDENT_VALIDATION": "ADMISSION_ONLY_AFTER_COMPLETE_INDEPENDENT_COHORT",
        "CONTINUOUS_24H_TEST": "DESCRIPTIVE_RESULT_ONLY_NOT_RESTART_OR_RETUNING_TRIGGER",
        "USER_DIRECT_24H_TEST": "DESCRIPTIVE_RESULT_USER_AUTHORIZED_WITHOUT_INDEPENDENT_24_VALIDATION",
    }
    if stage not in counts or type(denominator) is not int or denominator != counts[stage]:
        raise ValueError("REPORTING_TARGET_REQUIRES_MATCHING_PHASE_DENOMINATOR")
    return {
        "coverage_target": "0.40",
        "minimum_distinct_primary_signal_markets": (2 * denominator + 4) // 5,
        "denominator": denominator,
        "role": roles[stage],
    }


def load_bound_document(reference):
    path = Path(reference["path"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("PHASE_EVIDENCE_CHANGED:" + str(path))
    return json.loads(raw)


def validate_cohort(cohort, settings):
    stage = cohort.get("stage")
    count = {
        "DEVELOPMENT_CAPTURE": 24,
        "INDEPENDENT_VALIDATION": 24,
        "CONTINUOUS_24H_TEST": 288,
        "USER_DIRECT_24H_TEST": 288,
    }.get(stage)
    epochs = cohort.get("market_epochs")
    if (
        cohort.get("strategy") not in {"M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M21", "M23"}
        or cohort.get("strategy") != settings.get("sop", {}).get("strategy", "M12")
        or count is None
        or not isinstance(epochs, list)
        or len(epochs) != count
        or settings["max_markets"] != count
        or any(type(e) is not int or e % 300 for e in epochs)
        or epochs != list(range(epochs[0], epochs[0] + count * 300, 300))
        or cohort.get("markets") != [f"btc-updown-5m-{e}" for e in epochs]
        or cohort.get("window_start_epoch") != epochs[0]
        or cohort.get("window_end_epoch") != epochs[-1] + 300
    ):
        raise ValueError("INVALID_PREDECLARED_SOP_COHORT")
    return stage


def validate_selection(selection, sop):
    if (
        selection.get("schema") != strategy_schema(sop, "frequency-selection-v1")
        or selection.get("sample_role") != "DEVELOPMENT_ONLY"
        or selection.get("selection_uses_pnl") is not False
        or selection.get("selected_sop_definition") != sop
    ):
        raise ValueError("MATCHING_FROZEN_FREQUENCY_ONLY_SELECTION_REQUIRED")
    evidence = load_bound_document(selection["frequency_report"])
    if (
        evidence.get("status") != "PASS"
        or evidence.get("selection_uses_pnl") is not False
        or evidence.get("sop_definition") != sop
        or evidence["revision"]["parameter_sha256"] != sop["parameter_sha256"]
        or evidence["revision"]["revision_round"] > 3
        or evidence.get("frequency", {}).get("denominator") != 24
        or evidence["frequency"].get("primary_signal_markets", 0) < 10
        or evidence["frequency"].get("missing_markets") != []
    ):
        raise ValueError("FROZEN_DEVELOPMENT_FREQUENCY_EVIDENCE_REQUIRED")
    epochs = selection.get("development_market_epochs")
    if (
        not isinstance(epochs, list)
        or len(epochs) != 24
        or len(set(epochs)) != 24
        or epochs != list(range(epochs[0], epochs[0] + 24 * 300, 300))
        or evidence.get("market_epochs") != epochs
    ):
        raise ValueError("FIXED_DEVELOPMENT_COHORT_REQUIRED")
    return epochs


def validate_independent_report(report, sop, development_epochs):
    if (
        report.get("schema") != strategy_schema(sop, "independent-validation-report-v1")
        or report.get("sample_role") != "INDEPENDENT_VALIDATION"
        or report.get("mode") != "REALTIME_PUBLIC_DATA_SIMULATION"
        or report.get("phase_complete") is not True
        or report.get("run_status") != "PASS"
        or report.get("sop_definition") != sop
        or type(report.get("batch_number")) is not int
        or not 1 <= report["batch_number"] <= 2
    ):
        raise ValueError("COMPLETED_INDEPENDENT_REALTIME_VALIDATION_REQUIRED")
    epochs = report.get("market_epochs")
    rows = report.get("markets")
    if (
        not isinstance(epochs, list)
        or len(epochs) != 24
        or epochs != list(range(epochs[0], epochs[0] + 24 * 300, 300))
        or set(epochs) & set(development_epochs)
        or epochs[0] <= development_epochs[-1]
        or not isinstance(rows, list)
        or len(rows) != 24
        or [r.get("market") for r in rows] != [f"btc-updown-5m-{e}" for e in epochs]
    ):
        raise ValueError("UNSEEN_FIXED_24_VALIDATION_MARKETS_REQUIRED")
    if sop.get("strategy") == "M17" and any(
            not isinstance(r.get("admission_signal_ids"), list) for r in rows):
        raise ValueError("M17_INDEPENDENT_REFERENCE_READINESS_EVIDENCE_REQUIRED")
    proven = [
        r["market"]
        for r in rows
        if r.get("primary_eligible") is True
        and r.get("audit_validity") == "VALID"
        and r.get("admission_signal_ids", r.get("signal_ids"))
    ]
    if len(proven) < 10:
        raise ValueError("INDEPENDENT_PRIMARY_SIGNAL_COVERAGE_BELOW_10_OF_24")
    return {
        "primary_signal_markets": len(proven),
        "denominator": 24,
        "primary_coverage": len(proven) / 24,
        "validation_epochs": epochs,
    }


def user_direct_admission(document, cohort, cfg):
    """Explicit M21-only user decision, never an independent validation PASS.

    Bind the current definition and the previous frequency-only replay. Changes
    to other strategy dispatch do not erase the unchanged M21 source identity.
    The normal independent gate remains mandatory on its original stage.
    """
    sop = cfg["sop"]
    if (document is None or document.get("schema") != "m21-user-direct-24h-admission-v1"
            or document.get("authorization_quote") != "清理刚才的日志，空出磁盘空间，然后M21跑24小时"
            or document.get("independent_realtime_validation_completed") is not False
            or document.get("selection_uses_pnl") is not False
            or document.get("selected_sop_definition") != sop
            or sop.get("strategy") != "M21" or sop.get("groups") != ["M21"]
            or sop.get("variant_id") != "M21_ENDPOINT_COFLOW"
            or cfg.get("sop_parameter_revision", {}).get("revision_round") != 0
            or document.get("runtime_sha256") != cohort.get("runtime_sha256")):
        raise ValueError("EXPLICIT_MATCHING_M21_USER_DIRECT_ADMISSION_REQUIRED")
    evidence = load_bound_document(document["frequency_report"])
    replay = load_bound_document(document["replay_plan"])
    review = load_bound_document(document["frequency_review"])
    previous = replay["selected_sop"]
    semantic_keys = ("strategy", "groups", "variant_id", "version", "parameters",
                     "parameter_sha256", "selected_sop_sha256", "selected_document_parameters_sha256",
                     "input_contract", "signal_contract", "execution_contract", "source_order",
                     "adapter_input_version", "runtime_retirement_version")
    source_names = ("m21.py", "feed.py", "m19_path.py", "m12.py", "metrics.py")
    if (any(sop.get(k) != previous.get(k) for k in semantic_keys)
            or any(not sop["implementation"].get(k)
                   or sop["implementation"][k] != previous["implementation"].get(k)
                   for k in source_names)
            or evidence.get("status") != "PASS" or evidence.get("denominator") != 24
            or evidence.get("pnl_used_for_selection") is not False
            or evidence.get("independent_or_24h_admission_granted") is not False
            or evidence.get("revision", {}).get("parameter_sha256") != sop["parameter_sha256"]
            or review.get("status") != "PASS" or review.get("actual_child_exit") != 0
            or review.get("frequency_sha256") != document["frequency_report"]["sha256"]
            or review.get("runtime_sha256")[:16] != Path(evidence["owner"]["runtime"]).name):
        raise ValueError("UNCHANGED_M21_DEVELOPMENT_REPLAY_AND_REVIEW_REQUIRED")
    rows = evidence.get("markets", [])
    epochs = [int(r["market"].removeprefix("btc-updown-5m-")) for r in rows]
    proven = {r["market"] for r in rows if r.get("primary_eligible") is True and r.get("signal_ids")}
    if (len(epochs) != 24 or epochs != list(range(epochs[0], epochs[0] + 7200, 300))
            or len(proven) < 10 or len(proven) != evidence.get("primary_distinct_signal_markets")
            or review.get("distinct_signal_markets") != len(proven)
            or cohort["market_epochs"][0] <= epochs[-1]):
        raise ValueError("M21_FIXED_DEVELOPMENT_FREQUENCY_AND_NEW_FORWARD_COHORT_REQUIRED")
    return {"stage": "USER_DIRECT_24H_TEST", "admission_granted_for_24h": True,
            "admission_basis": "EXPLICIT_USER_AUTHORIZATION_AFTER_DEVELOPMENT_REPLAY",
            "independent_realtime_validation_completed": False,
            "development_primary_signal_markets": len(proven), "development_denominator": 24,
            "note": "Direct24h authorized by user; development13/24 is not independent real-time validation."}


def phase_admission(cohort, cfg, *, selection=None, independent_report=None,
                    user_direct_document=None):
    stage = validate_cohort(cohort, cfg)
    if stage == "USER_DIRECT_24H_TEST":
        if selection is not None or independent_report is not None:
            raise ValueError("USER_DIRECT_PHASE_MUST_NOT_IMPERSONATE_INDEPENDENT_VALIDATION")
        return user_direct_admission(user_direct_document, cohort, cfg)
    if user_direct_document is not None:
        raise ValueError("USER_DIRECT_AUTHORIZATION_ONLY_APPLIES_TO_ITS_EXPLICIT_PHASE")
    if stage == "DEVELOPMENT_CAPTURE":
        if cfg.get("sop_parameter_revision", {}).get("revision_round", 0) != 0:
            raise ValueError("DEVELOPMENT_CAPTURE_REQUIRES_BASELINE_NOT_LIVE_PARAMETER_SEARCH")
        if selection is not None or independent_report is not None:
            raise ValueError("DEVELOPMENT_CAPTURE_HAS_NO_VALIDATION_ADMISSION")
        return {"stage": stage, "admission_granted_for_24h": False}
    if selection is None:
        raise ValueError("FROZEN_SELECTION_REQUIRED")
    development = validate_selection(selection, cfg["sop"])
    if (
        set(cohort["market_epochs"]) & set(development)
        or cohort["market_epochs"][0] <= development[-1]
    ):
        raise ValueError("NEW_FORWARD_COHORT_MUST_NOT_REUSE_DEVELOPMENT_MARKETS")
    if stage == "INDEPENDENT_VALIDATION":
        if type(cohort.get("batch_number")) is not int or not 1 <= cohort["batch_number"] <= 2:
            raise ValueError("AT_MOST_TWO_INDEPENDENT_VALIDATION_BATCHES")
        return {"stage": stage, "admission_granted_for_24h": False}
    if independent_report is None:
        raise ValueError("INDEPENDENT_VALIDATION_REPORT_REQUIRED_FOR_24H")
    admitted = validate_independent_report(independent_report, cfg["sop"], development)
    if (
        set(cohort["market_epochs"]) & set(admitted["validation_epochs"])
        or cohort["market_epochs"][0] <= admitted["validation_epochs"][-1]
    ):
        raise ValueError("24H_COHORT_MUST_BE_NEW_AND_AFTER_VALIDATION")
    return {"stage": stage, "admission_granted_for_24h": True, **admitted}
