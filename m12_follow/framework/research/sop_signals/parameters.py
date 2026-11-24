"""Explicit SOP revisions; fixed causal and quality rules are not tuning knobs."""

import math
from dataclasses import fields
from decimal import Decimal

from .m12 import Parameters, parameter_identity
from .m13 import Parameters as M13Parameters
from .m14 import Parameters as M14Parameters
from .m15 import Parameters as M15Parameters
from .m16 import Parameters as M16Parameters
from .m17 import Parameters as M17Parameters
from .m18 import Parameters as M18Parameters, SHORT_ROUND_MARKER, SHORT_ROUND_PROFILE
from .m19 import Parameters as M19Parameters
from .m23 import Parameters as M23Parameters
from .m21 import Parameters as M21Parameters
from .m24 import Parameters as M24Parameters, VARIANT as M24_VARIANT
from .m7 import Parameters as M7Parameters
from .m9 import Parameters as M9Parameters

IMPLEMENTED = {"M7", "M9", "M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M21", "M23"}


def strategy_from_parameters(parameters):
    if isinstance(parameters, M24Parameters):
        return "M24"
    if isinstance(parameters, M9Parameters):
        return "M9"
    if isinstance(parameters, M7Parameters):
        return "M7"
    if isinstance(parameters, M21Parameters):
        return "M21"
    if isinstance(parameters, M23Parameters):
        return "M23"
    if isinstance(parameters, M19Parameters):
        return "M19"
    if isinstance(parameters, M18Parameters):
        return "M18"
    if isinstance(parameters, M17Parameters):
        return "M17"
    if isinstance(parameters, M16Parameters):
        return "M16"
    if isinstance(parameters, M15Parameters):
        return "M15"
    if isinstance(parameters, M14Parameters):
        return "M14"
    if isinstance(parameters, M13Parameters):
        return "M13"
    if isinstance(parameters, Parameters):
        return "M12"
    raise ValueError("UNIMPLEMENTED_SOP_PARAMETERS")

TUNABLE = {"ask_recovery", "ask_loss", "ratio_min", "ratio_max", "pair_gap", "bid_recovery"}
FIXED = {"round_seconds", "stable_seconds", "stable_recovery", "ttl"}


def load_revision(document):
    if document.get("variant_id"):
        return load_explicit_variant(document)
    if document.get("strategy") == "M19":
        return load_m19_revision(document)
    if document.get("strategy") == "M18":
        return load_m18_revision(document)
    if document.get("strategy") == "M17":
        return load_m17_revision(document)
    if document.get("strategy") == "M16":
        return load_m16_revision(document)
    if document.get("strategy") == "M15":
        return load_m15_revision(document)
    if document.get("strategy") == "M14":
        return load_m14_revision(document)
    if document.get("strategy") == "M13":
        return load_m13_revision(document)
    if document.get("strategy") != "M12" or document.get("pnl_used_for_selection") is not False:
        raise ValueError("M12_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M12_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision and not str(document.get("hypothesis", "")).strip():
        raise ValueError("PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED")
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values) - TUNABLE - FIXED:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M12_PARAMETER")
    baseline = Parameters()
    resolved = {}
    for field in fields(baseline):
        base = getattr(baseline, field.name)
        raw = values.get(field.name, base)
        if isinstance(raw, bool) or raw is None:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name)
        try:
            value = Decimal(str(raw)) if isinstance(base, Decimal) else float(raw)
            finite = value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)
            if not finite:
                raise ValueError("NONFINITE_PARAMETER")
        except (TypeError, ArithmeticError, ValueError) as exc:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name) from exc
        if (field.name in FIXED or revision == 0) and value != base:
            raise ValueError("FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED:" + field.name)
        resolved[field.name] = value
    p = Parameters(**resolved)
    if not (
        p.stable_recovery <= p.bid_recovery <= 1
        and 0 <= p.ask_recovery < Decimal(".50")
        and 0 < p.ask_loss <= 1
        and 0 < p.ratio_min <= p.ratio_max
        and 0 < p.pair_gap < p.ttl
    ):
        raise ValueError("M12_PARAMETER_SEMANTICS_OR_FEASIBILITY")
    values, fingerprint = parameter_identity(p)
    return p, {
        "strategy": "M12",
        "revision_round": revision,
        "hypothesis": document.get("hypothesis", "BASELINE_SOP"),
        "parameters": values,
        "parameter_sha256": fingerprint,
        "pnl_used_for_selection": False,
        "fixed_causal_rules": {name: values[name] for name in sorted(FIXED)},
    }


def load_explicit_variant(document):
    """New document contracts must never silently fall back to old M defaults."""
    variant = document.get("variant_id")
    if variant == "M12_VALIDITY_OVERLAP":
        from . import m12_overlap as contract
        strategy = "M12"
    elif variant == "M23_FIRST_SELL_WHILE_VALID":
        from . import m23 as contract
        strategy = "M23"
    elif variant == "M21_ENDPOINT_COFLOW":
        from . import m21 as contract
        strategy = "M21"
    elif variant == M24_VARIANT:
        from . import m24 as contract
        strategy = "M24"
    elif variant == "M7_PAUSE_RELEASE":
        from . import m7 as contract
        strategy = "M7"
    elif variant == "M9_EX_VALIDITY_OVERLAP":
        from . import m9 as contract
        strategy = "M9"
    else:
        raise ValueError("UNIMPLEMENTED_EXPLICIT_SOP_VARIANT")
    if (document.get("strategy") != strategy
            or document.get("sop_sha256") != contract.SOP_SHA256
            or document.get("document_parameters_sha256") != contract.DOCUMENT_PARAMETERS_SHA256
            or document.get("pnl_used_for_selection") is not False
            or type(document.get("revision_round")) is not int
            or document["revision_round"] != 0):
        raise ValueError("FROZEN_EXPLICIT_VARIANT_BASELINE_REQUIRED")
    p = contract.Parameters()
    values, fingerprint = parameter_identity(p)
    if document.get("parameters") != values:
        raise ValueError("EXPLICIT_VARIANT_PARAMETERS_DIFFER_FROM_SELECTED_SOP")
    return p, {"strategy": strategy, "variant_id": variant, "revision_round": 0,
               "hypothesis": "USER_SELECTED_UPDATED_SOP_BASELINE",
               "parameters": values, "parameter_sha256": fingerprint,
               "sop_sha256": contract.SOP_SHA256,
               "document_parameters_sha256": contract.DOCUMENT_PARAMETERS_SHA256,
               "pnl_used_for_selection": False}


def load_m19_revision(document):
    """Original SOP first; no undeclared numeric candidate or silent fallback."""
    if document.get("pnl_used_for_selection") is not False:
        raise ValueError("M19_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M19_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision:
        raise ValueError("M19_NUMERIC_CANDIDATE_NOT_IMPLEMENTED_NO_SILENT_DEFAULT")
    baseline = M19Parameters()
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values)-{field.name for field in fields(baseline)}:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M19_PARAMETER")
    for field in fields(baseline):
        raw = values.get(field.name, getattr(baseline,field.name))
        try:
            if raw is None or isinstance(raw,bool):
                raise ValueError("INVALID_NUMBER")
            number = Decimal(str(raw))
            if not number.is_finite() or number != Decimal(str(getattr(baseline,field.name))):
                raise ValueError("BASELINE_VALUE_CHANGED")
        except (TypeError,ArithmeticError,ValueError) as exc:
            raise ValueError("M19_ORIGINAL_SOP_BASELINE_PARAMETER_REQUIRED:"+field.name) from exc
    resolved,fingerprint = parameter_identity(baseline)
    return baseline, {"strategy":"M19","revision_round":0,"hypothesis":"ORIGINAL_SOP_BASELINE",
        "parameters":resolved,"parameter_sha256":fingerprint,"pnl_used_for_selection":False,
        "original_sop_numerical_result":True,"fixed_causal_rules":resolved,
        "frequency_basis":"CONFIRMED_VALID_AFTER_COMPLETE_PARENT_BOUNDARY"}


def load_m18_revision(document):
    """Keep the original identity; explicit candidates retain causal guards."""
    if document.get("pnl_used_for_selection") is not False:
        raise ValueError("M18_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M18_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision:
        return _load_m18_numeric_candidate(document, revision)
    values = document.get("parameters")
    baseline = M18Parameters()
    names = {f.name for f in fields(baseline)}
    if not isinstance(values, dict) or set(values) - names:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M18_PARAMETER")
    for f in fields(baseline):
        raw = values.get(f.name, getattr(baseline, f.name))
        try:
            if raw is None or isinstance(raw, bool):
                raise ValueError("INVALID_NUMBER")
            value = Decimal(str(raw))
            if not value.is_finite() or value != Decimal(str(getattr(baseline, f.name))):
                raise ValueError("BASELINE_VALUE_CHANGED")
        except (TypeError, ArithmeticError, ValueError) as exc:
            raise ValueError("M18_ORIGINAL_SOP_BASELINE_PARAMETER_REQUIRED:" + f.name) from exc
    resolved, fingerprint = parameter_identity(baseline)
    return baseline, {"strategy": "M18", "revision_round": 0,
                      "hypothesis": "ORIGINAL_SOP_BASELINE", "parameters": resolved,
                      "parameter_sha256": fingerprint, "pnl_used_for_selection": False,
                      "original_sop_numerical_result": True,
                      "fixed_causal_rules": resolved,
                      "frequency_basis": "CONFIRMED_VALID_AFTER_COMPLETE_PARENT_BOUNDARY"}


def _load_m18_numeric_candidate(document, revision):
    """Explicit numerical variants; proof, stability and loss guards stay fixed."""
    if not isinstance(document.get("hypothesis"), str) or not document["hypothesis"].strip():
        raise ValueError("PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED")
    baseline = M18Parameters()
    names = {f.name for f in fields(baseline)}
    tunable = {"reference_hit_shares_min", "reference_hit_fraction_min",
               "reference_loss_fraction_min", "initial_depth_ratio_min",
               "initial_depth_ratio_max", "hit_ratio_min", "hit_ratio_max", "pair_gap"}
    fixed = names - tunable
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values) - names:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M18_PARAMETER")
    resolved = {}
    short_round = False
    for field in fields(baseline):
        base, raw = getattr(baseline, field.name), values.get(field.name, getattr(baseline, field.name))
        try:
            if raw is None or isinstance(raw, bool):
                raise ValueError("INVALID_NUMBER")
            number = Decimal(str(raw))
            if not number.is_finite():
                raise ValueError("NONFINITE_NUMBER")
            value = number if isinstance(base, Decimal) else float(number)
        except (TypeError, ArithmeticError, ValueError) as exc:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name) from exc
        explicit_short = (field.name == "round_seconds" and value == .250 and revision == 3
                          and document.get("explicit_short_round_candidate") == SHORT_ROUND_MARKER)
        if field.name in fixed and value != base and not explicit_short:
            raise ValueError("FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED:" + field.name)
        short_round = short_round or explicit_short
        resolved[field.name] = value
    parameters = M18Parameters(**resolved)
    if document.get("explicit_short_round_candidate") is not None and not short_round:
        raise ValueError("M18_EXPLICIT_SHORT_ROUND_DECLARATION_DIFFERS")
    if short_round:
        tunable = tunable | {"round_seconds"}
        fixed = fixed - {"round_seconds"}
    if not (Decimal(".5") <= parameters.reference_hit_shares_min <= 5
            and Decimal(".01") <= parameters.reference_hit_fraction_min <= Decimal(".10")
            and Decimal(".005") <= parameters.reference_loss_fraction_min <= Decimal(".05")
            and Decimal(".67") <= parameters.initial_depth_ratio_min <= Decimal(".80")
            and Decimal("1.25") <= parameters.initial_depth_ratio_max <= Decimal("1.50")
            and Decimal("1.25") <= parameters.hit_ratio_min <= 2
            and 4 <= parameters.hit_ratio_max <= 8
            and 2 <= parameters.pair_gap <= 4):
        raise ValueError("M18_BOUNDED_NUMERIC_SEARCH_RANGE_REQUIRED")
    changed = {name for name in tunable if getattr(parameters, name) != getattr(baseline, name)}
    review = document.get("numeric_candidate_review")
    if (not changed or not isinstance(review, dict)
            or set(review) != {"profile", "parameter_change_notes"}
            or review.get("profile") != (SHORT_ROUND_PROFILE if short_round else
                                         "M18_MATERIAL_REFERENCE_AND_ESCALATION_NUMERICAL_V1")
            or not isinstance(review.get("parameter_change_notes"), dict)
            or set(review["parameter_change_notes"]) != changed
            or any(not isinstance(note, str) or not note.strip()
                   for note in review["parameter_change_notes"].values())):
        raise ValueError("M18_EXPLICIT_NUMERIC_SEMANTIC_REVIEW_REQUIRED")
    values, fingerprint = parameter_identity(parameters)
    record = {"strategy": "M18", "revision_round": revision,
              "hypothesis": document["hypothesis"], "parameters": values,
              "parameter_sha256": fingerprint, "pnl_used_for_selection": False,
              "original_sop_numerical_result": False,
              "fixed_causal_rules": {name: values[name] for name in sorted(fixed)},
              "numeric_candidate_review": review,
              "frequency_basis": "CONFIRMED_VALID_AFTER_COMPLETE_PARENT_BOUNDARY"}
    if short_round:
        record["explicit_primitive_numeric_override"] = {
            "version": SHORT_ROUND_MARKER, "original_window_ms": 500, "window_ms": 250,
            "stable_continuous_hold_ms": 100, "stable_recovery_min": "0.50",
            "applies_to": "FORMATION_AND_FOLLOWUP_ROUNDS", "source_quality_contract_changed": False}
    return parameters, record


def load_m17_revision(document):
    """Bounded numeric declarations; no data selection or frequency admission."""
    if document.get("pnl_used_for_selection") is not False:
        raise ValueError("M17_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M17_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision and (not isinstance(document.get("hypothesis"), str)
                     or not document["hypothesis"].strip()):
        raise ValueError("PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED")
    baseline = M17Parameters()
    all_fields = {f.name for f in fields(baseline)}
    tunable = {"minimum_sell_shares", "minimum_sell_to_initial_depth", "max_single_sell_fraction",
               "minimum_visible_to_initial_depth", "terminal_visible_to_initial_depth",
               "wait_floor_to_initial_depth"}
    fixed = all_fields - tunable
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values) - all_fields:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M17_PARAMETER")
    resolved = {}
    for f in fields(baseline):
        base, raw = getattr(baseline, f.name), values.get(f.name, getattr(baseline, f.name))
        if raw is None or isinstance(raw, bool):
            raise ValueError("INVALID_PARAMETER_NUMBER:" + f.name)
        try:
            n = Decimal(str(raw))
            if not n.is_finite() or (type(base) is int and n != n.to_integral_value()):
                raise ValueError("NONFINITE_OR_FRACTIONAL_PARAMETER")
            value = n if isinstance(base, Decimal) else int(n) if type(base) is int else float(n)
        except (TypeError, ArithmeticError, ValueError) as exc:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + f.name) from exc
        if (revision == 0 or f.name in fixed) and value != base:
            raise ValueError("FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED:" + f.name)
        resolved[f.name] = value
    p = M17Parameters(**resolved)
    if not (1 <= p.minimum_sell_shares <= 5
            and Decimal(".10") <= p.minimum_sell_to_initial_depth <= Decimal(".50")
            and Decimal(".80") <= p.max_single_sell_fraction <= Decimal(".90")
            and Decimal(".60") <= p.minimum_visible_to_initial_depth <= Decimal(".70")
            and Decimal(".80") <= p.terminal_visible_to_initial_depth <= Decimal(".90")
            and p.wait_floor_to_initial_depth == p.minimum_visible_to_initial_depth):
        raise ValueError("M17_NUMERIC_SEARCH_RANGE_OR_SAME_ABSOLUTE_FLOOR_REQUIRED")
    changed = {k for k in tunable if getattr(p, k) != getattr(baseline, k)}
    review = document.get("numeric_candidate_review")
    if revision and (not changed or not isinstance(review, dict)
            or set(review) != {"profile", "parameter_change_notes"}
            or review.get("profile") != "M17_RETAINED_DEPTH_NUMERICAL_V1"
            or not isinstance(review.get("parameter_change_notes"), dict)
            or set(review["parameter_change_notes"]) != changed
            or any(not isinstance(v, str) or not v.strip() for v in review["parameter_change_notes"].values())):
        raise ValueError("M17_EXPLICIT_NUMERIC_SEMANTIC_REVIEW_REQUIRED")
    values, fingerprint = parameter_identity(p)
    return p, {"strategy": "M17", "revision_round": revision,
               "hypothesis": document.get("hypothesis", "BASELINE_SOP"), "parameters": values,
               "parameter_sha256": fingerprint, "pnl_used_for_selection": False,
               "fixed_causal_rules": {k: values[k] for k in sorted(fixed)},
               "numeric_candidate_review": review if revision else None,
               "original_sop_numerical_result": not changed,
               "frequency_basis": "EVER_REFERENCE_READY_BEFORE_ORIGINAL_DEADLINE"}


def load_m13_revision(document):
    if document.get("pnl_used_for_selection") is not False:
        raise ValueError("M13_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M13_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision and not str(document.get("hypothesis", "")).strip():
        raise ValueError("PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED")
    baseline = M13Parameters()
    tunable = {"ratio_min", "ratio_max", "bid_recovery", "lw_loss_min", "lw_residual_min", "lw_residual_loss_min"}
    all_fields = {f.name for f in fields(baseline)}
    fixed = all_fields - tunable
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values) - all_fields:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M13_PARAMETER")
    resolved = {}
    for field in fields(baseline):
        base = getattr(baseline, field.name)
        raw = values.get(field.name, base)
        if isinstance(raw, bool) or raw is None:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name)
        value = Decimal(str(raw)) if isinstance(base, Decimal) else float(raw)
        if not (value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)):
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name)
        explicit_short_round = (
            field.name == "round_seconds" and value == .250 and revision == 3
            and document.get("explicit_short_round_candidate")
            == "M13_250MS_KEEP_100MS_HALF_RECOVERY_R3"
        )
        if (field.name in fixed or revision == 0) and value != base and not explicit_short_round:
            raise ValueError("FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED:" + field.name)
        resolved[field.name] = value
    p = M13Parameters(**resolved)
    if not (Decimal(".50") <= p.bid_recovery <= 1 and 0 < p.ratio_min <= p.ratio_max
            and p.lw_keep_residual_min <= p.lw_residual_min <= p.lw_loss_min <= 1
            and p.lw_keep_residual_loss_min <= p.lw_residual_loss_min <= 1):
        raise ValueError("M13_PARAMETER_SEMANTICS_OR_FEASIBILITY")
    values, fingerprint = parameter_identity(p)
    record = {"strategy": "M13", "revision_round": revision,
              "hypothesis": document.get("hypothesis", "BASELINE_SOP"),
              "parameters": values, "parameter_sha256": fingerprint,
              "pnl_used_for_selection": False,
              "fixed_causal_rules": {name: values[name] for name in sorted(fixed)}}
    if p.round_seconds != baseline.round_seconds:
        record["explicit_primitive_numeric_override"] = {
            "original_round_seconds": baseline.round_seconds,
            "selected_round_seconds": p.round_seconds,
            "declaration": document["explicit_short_round_candidate"],
            "continuous_stability_seconds": p.stable_seconds,
            "recovery_min": str(p.stable_recovery),
            "original_sop_numerical_result": False,
        }
    return p, record


def load_m16_revision(document):
    """Explicit numerical candidates retain the original causal/quality rules.

    Loading a declaration neither selects it nor grants a dataset/realtime gate.
    The live baseline stays in its immutable runtime; revisions use new bundles.
    """
    if document.get("pnl_used_for_selection") is not False:
        raise ValueError("M16_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M16_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision:
        if not isinstance(document.get("hypothesis"), str) or not document["hypothesis"].strip():
            raise ValueError("PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED")
    baseline = M16Parameters()
    all_fields = {f.name for f in fields(baseline)}
    tunable = {"pair_gap", "reference_latency_min", "second_speed_ratio_max",
               "absolute_hit_max", "normalized_hit_max", "normalized_loss_min", "normalized_loss_max"}
    fixed = all_fields - tunable
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values) - all_fields:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M16_PARAMETER")
    resolved = {}
    for field in fields(baseline):
        base, raw = getattr(baseline, field.name), values.get(field.name, getattr(baseline, field.name))
        if isinstance(raw, bool) or raw is None:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name)
        try:
            value = Decimal(str(raw)) if isinstance(base, Decimal) else float(raw)
            if not (value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)):
                raise ValueError("NONFINITE_PARAMETER")
        except (TypeError, ArithmeticError, ValueError) as exc:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name) from exc
        if revision == 0 and value != base:
            raise ValueError("FIXED_SOP_BASELINE_CANNOT_BE_RELABELLED:" + field.name)
        if field.name in fixed and value != base:
            raise ValueError("FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED:" + field.name)
        resolved[field.name] = value
    p = M16Parameters(**resolved)
    if not (
        p.round_seconds < p.pair_gap < p.ttl
        and 0 < p.reference_latency_min <= baseline.reference_latency_min
        and 0 < p.second_speed_ratio_max <= Decimal(".80")
        and 1 <= p.absolute_hit_max <= 4 and 1 <= p.normalized_hit_max <= 4
        and Decimal(".50") <= p.normalized_loss_min <= 1 <= p.normalized_loss_max <= 2
    ):
        raise ValueError("M16_PARAMETER_SEMANTICS_OR_BOUNDED_SEARCH_RANGE")
    changed = {name for name in tunable if getattr(p, name) != getattr(baseline, name)}
    review = document.get("numeric_candidate_review")
    if revision:
        if (not isinstance(review, dict)
                or set(review) != {"profile", "parameter_change_notes"}
                or review.get("profile") != "M16_EXPLICIT_TIMING_COMPARABILITY_NUMERICAL_V1"
                or not isinstance(review.get("parameter_change_notes"), dict)
                or set(review["parameter_change_notes"]) != changed
                or not changed
                or any(not isinstance(note, str) or not note.strip()
                       for note in review["parameter_change_notes"].values())):
            raise ValueError("M16_EXPLICIT_NUMERIC_CHANGE_REVIEW_REQUIRED")
    elif review is not None:
        raise ValueError("M16_ORIGINAL_BASELINE_CANNOT_USE_CANDIDATE_REVIEW")
    values, fingerprint = parameter_identity(p)
    record = {"strategy": "M16", "revision_round": revision,
              "hypothesis": document.get("hypothesis", "ORIGINAL_SOP_BASELINE"),
              "parameters": values, "parameter_sha256": fingerprint,
              "pnl_used_for_selection": False,
              "numeric_candidates_implemented": True,
              "fixed_causal_rules": values if revision == 0 else {name: values[name] for name in sorted(fixed)}}
    if revision:
        original, _ = parameter_identity(baseline)
        record["explicit_numeric_overrides"] = {
            name: {"original": original[name], "selected": values[name],
                   "explanation": review["parameter_change_notes"][name]} for name in sorted(changed)}
        record["numeric_candidate_review"] = review
        record["original_sop_numerical_result"] = False
        record["original_100ms_reference_and_50ms_minimum_improvement_guarantee"] = (
            p.reference_latency_min >= .1 and p.second_speed_ratio_max <= Decimal(".50"))
        record["search_scope_not_universal_parameter_impossibility_proof"] = True
    return p, record


def load_m15_revision(document):
    if document.get("pnl_used_for_selection") is not False:
        raise ValueError("M15_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M15_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision and not str(document.get("hypothesis", "")).strip():
        raise ValueError("PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED")
    hold_profiles = {"M15_LW_HOLD250MS_ORIGINAL_CLOCKS": .250}
    hold_profile = document.get("formation_hold_profile")
    if "formation_hold_profile" in document and (
        not isinstance(hold_profile, str) or hold_profile not in hold_profiles
    ):
        raise ValueError("M15_FORMATION_HOLD_PROFILE_NOT_AUTHORIZED")
    if hold_profile and revision == 0:
        raise ValueError("M15_ORIGINAL_BASELINE_CANNOT_USE_HOLD_PROFILE")
    baseline = M15Parameters()
    tunable = {"ask_recovery", "ask_loss", "bid_recovery", "lw_loss_min",
               "lw_residual_min", "lw_residual_loss_min"}
    all_fields = {f.name for f in fields(baseline)}
    fixed = all_fields - tunable
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values) - all_fields:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M15_PARAMETER")
    resolved = {}
    for field in fields(baseline):
        base = getattr(baseline, field.name)
        raw = values.get(field.name, base)
        if isinstance(raw, bool) or raw is None:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name)
        try:
            value = Decimal(str(raw)) if isinstance(base, Decimal) else float(raw)
            finite = value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)
            if not finite:
                raise ValueError("NONFINITE_PARAMETER")
        except (TypeError, ArithmeticError, ValueError) as exc:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name) from exc
        expected = (
            hold_profiles[hold_profile]
            if field.name == "lw_hold_seconds" and hold_profile
            else base
        )
        if (field.name in fixed or revision == 0) and value != expected:
            raise ValueError("FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED:" + field.name)
        resolved[field.name] = value
    p = M15Parameters(**resolved)
    if not (p.stable_recovery <= p.bid_recovery <= 1 and 0 <= p.ask_recovery < Decimal(".50")
            and 0 < p.ask_loss <= 1
            and p.lw_keep_residual_min <= p.lw_residual_min <= p.lw_loss_min <= 1
            and p.lw_keep_residual_loss_min <= p.lw_residual_loss_min <= 1):
        raise ValueError("M15_PARAMETER_SEMANTICS_OR_FEASIBILITY")
    values, fingerprint = parameter_identity(p)
    resolved_revision = {"strategy": "M15", "revision_round": revision,
                         "hypothesis": document.get("hypothesis", "BASELINE_SOP"),
                         "parameters": values, "parameter_sha256": fingerprint,
                         "pnl_used_for_selection": False,
                         "fixed_causal_rules": {name: values[name] for name in sorted(fixed)}}
    if hold_profile:
        resolved_revision["formation_hold_profile"] = hold_profile
    return p, resolved_revision


def load_m14_revision(document):
    if document.get("pnl_used_for_selection") is not False:
        raise ValueError("M14_FREQUENCY_ONLY_REVISION_REQUIRED")
    revision = document.get("revision_round")
    if type(revision) is not int or not 0 <= revision <= 3:
        raise ValueError("M14_AT_MOST_THREE_PARAMETER_REVISIONS")
    if revision and not str(document.get("hypothesis", "")).strip():
        raise ValueError("PREDECLARED_PARAMETER_HYPOTHESIS_REQUIRED")
    baseline = M14Parameters()
    profiles = {"M14_ASK250MS_BID500MS_STABLE100MS": .25,
                "M14_ASK150MS_BID500MS_STABLE100MS": .15}
    profile = document.get("observation_window_profile")
    if "observation_window_profile" in document and (not isinstance(profile, str) or profile not in profiles):
        raise ValueError("M14_OBSERVATION_WINDOW_PROFILE_NOT_AUTHORIZED")
    all_fields = {f.name for f in fields(baseline)}
    fixed = {"round_seconds", "stable_seconds", "stable_recovery", "ttl"}
    values = document.get("parameters")
    if not isinstance(values, dict) or set(values) - all_fields:
        raise ValueError("UNKNOWN_OR_UNAUTHORIZED_M14_PARAMETER")
    resolved = {}
    for field in fields(baseline):
        base = getattr(baseline, field.name)
        raw = values.get(field.name, base)
        if isinstance(raw, bool) or raw is None:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name)
        try:
            value = Decimal(str(raw)) if isinstance(base, Decimal) else float(raw)
            finite = value.is_finite() if isinstance(value, Decimal) else math.isfinite(value)
            if not finite:
                raise ValueError("NONFINITE_PARAMETER")
        except (TypeError, ArithmeticError, ValueError) as exc:
            raise ValueError("INVALID_PARAMETER_NUMBER:" + field.name) from exc
        explicit_ask_window = (field.name == "round_seconds" and revision > 0
                               and profile is not None and value == profiles[profile])
        if (field.name in fixed or revision == 0) and value != base and not explicit_ask_window:
            raise ValueError("FIXED_SOP_CAUSAL_RULE_CANNOT_BE_TUNED:" + field.name)
        resolved[field.name] = value
    p = M14Parameters(**resolved)
    if profile is not None and (revision == 0 or p.round_seconds != profiles[profile]):
        raise ValueError("M14_OBSERVATION_WINDOW_PROFILE_DIFFERS_FROM_DECLARED_VALUES")
    if not (p.stable_recovery <= p.bid_recovery <= 1 and 0 <= p.ask_recovery < Decimal(".50")
            and 0 < p.ask_loss <= 1 and 0 <= p.second_recovery_increase <= 1
            and 0 < p.ratio_min <= p.ratio_max and p.ask_gap > p.round_seconds
            and p.round_seconds > p.stable_seconds
            and 0 < p.pair_gap < p.ttl):
        raise ValueError("M14_PARAMETER_SEMANTICS_OR_FEASIBILITY")
    values, fingerprint = parameter_identity(p)
    record = {"strategy": "M14", "revision_round": revision,
              "hypothesis": document.get("hypothesis", "BASELINE_SOP"),
              "parameters": values, "parameter_sha256": fingerprint,
              "pnl_used_for_selection": False,
              "fixed_causal_rules": {name: values[name] for name in sorted(fixed)}}
    if profile is not None:
        from .m14 import observation_windows

        record["observation_window_profile"] = profile
        record["numerical_observation_windows"] = observation_windows(p)
        record["fixed_causal_rules"].pop("round_seconds")
        record["fixed_causal_rules"]["bid_round_seconds"] = .5
    return p, record
