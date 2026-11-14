"""Read-only local lifecycle import. Missing actual fills remain UNCALIBRATED."""

import json
import math
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

REQUIRED = {
    "market",
    "token",
    "order_id",
    "physical_submit_id",
    "submitted_at",
    "fill_id",
    "fill_at",
    "fill_quantity",
    "fill_price",
    "evidence",
}


def load_records(path):
    path = Path(path).resolve()
    if path.suffix in {".sqlite3", ".sqlite", ".db"}:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "actual_fills" in tables:
                return [dict(r) for r in db.execute("SELECT * FROM actual_fills")]
            if "modelled_fills" in tables:
                return [dict(r) for r in db.execute("SELECT * FROM modelled_fills")]
            # Existing M1 prepost ledger is NOT fill tracking. Import opportunity
            # identities without inventing fill times or interpreting ACK as fill.
            if "opportunities" in tables:
                identity_row = db.execute(
                    "SELECT value FROM metadata WHERE key='identity'"
                ).fetchone() if "metadata" in tables else None
                identity = json.loads(identity_row[0]) if identity_row else {}
                market = identity.get("market_id") or identity.get("market")
                records = [
                    {
                        "market": market,
                        "order_id": r["order_id"],
                        "token": r["token"],
                        "physical_submit_id": r["order_id"] if r["started"] else None,
                        "post_started": bool(r["started"]),
                        "status": r["state"],
                        "evidence": "ACTUAL_ORDER_LIFECYCLE_INCOMPLETE",
                    }
                    for r in db.execute("SELECT * FROM opportunities")
                ]
                if "uncertain_posts" in tables:
                    for r in db.execute("SELECT * FROM uncertain_posts"):
                        quote = json.loads(r["quote"])
                        records.append(
                            {
                                "market": market,
                                "order_id": r["order_id"],
                                "physical_submit_id": r["order_id"],
                                "token": quote["token_id"],
                                "post_started": True,
                                "status": "UNKNOWN_ARCHIVED_PHYSICAL_POST",
                                "evidence": "ACTUAL_ORDER_LIFECYCLE_INCOMPLETE",
                            }
                        )
                return records
            return []
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


PHYSICAL_KEY = ("market", "token", "order_id", "physical_submit_id")


def number(value, *, positive=False, timestamp=False):
    result = Decimal(str(value))
    if not result.is_finite() or positive and result <= 0:
        raise ValueError("INVALID_NUMBER")
    if timestamp and (result < 0 or not math.isfinite(float(result))):
        raise ValueError("INVALID_LIFECYCLE_TIME")
    return result


def text_number(value):
    return format(value.normalize(), "f") if value else "0"


def key_dict(key):
    return dict(zip(PHYSICAL_KEY, key, strict=True))


def aggregate(fills):
    """Quantities describe observed records; completion requires order-size evidence."""
    fills = sorted(fills, key=lambda f: (f["at"], f["fill_id"]))
    starts = {f["submitted"] for f in fills}
    contexts = {f["context"] for f in fills}
    bases = {f["basis"] for f in fills}
    evidence_types = {f["evidence"] for f in fills}
    sizes = {f["order_quantity"] for f in fills if f["order_quantity"] is not None}
    if len(starts) != 1:
        raise ValueError("CONFLICTING_SUBMISSION_TIMES")
    if len(contexts) != 1:
        raise ValueError("AMBIGUOUS_PROFILE_OR_SCENARIO_SELECT_ONE_CONTEXT")
    if len(bases) != 1:
        raise ValueError("MIXED_LIFECYCLE_TIME_BASES")
    if len(evidence_types) != 1:
        raise ValueError("MIXED_FILL_EVIDENCE_CATEGORIES")
    if len(sizes) > 1:
        raise ValueError("CONFLICTING_ORDER_QUANTITIES")
    quantity = sum((f["q"] for f in fills), Decimal(0))
    cost = sum((f["q"] * f["p"] for f in fills), Decimal(0))
    size = next(iter(sizes), None)
    if size is not None and quantity > size:
        raise ValueError("OVERFILL_VS_ORDER_QUANTITY")
    submitted = next(iter(starts))
    first_wait, last_wait = fills[0]["at"] - submitted, fills[-1]["at"] - submitted
    horizons = {f["horizon"] for f in fills}
    post_bases = {f["post_basis"] for f in fills}
    post_comparable = (all(f["post"] is not None for f in fills)
                       and len(horizons) == len(post_bases) == 1
                       and None not in horizons and None not in post_bases)
    post = (sum((f["q"] * f["post"] for f in fills), Decimal(0)) / quantity
            if post_comparable else None)
    return {"quantity": quantity, "cost": cost, "submitted": submitted,
            "first_at": fills[0]["at"], "last_at": fills[-1]["at"],
            "first_wait": first_wait, "last_wait": last_wait,
            "full_wait": last_wait if size is not None and quantity == size else None,
            "completion": "FULL_QUANTITY_OBSERVED" if size is not None and quantity == size
            else "PARTIAL_QUANTITY_OBSERVED" if size is not None else "UNKNOWN_ORDER_QUANTITY",
            "order_quantity": size, "fill_count": len(fills),
            "fill_ids": sorted(f["fill_id"] for f in fills), "basis": next(iter(bases)),
            "post": post, "horizon": next(iter(horizons)) if len(horizons) == 1 else None,
            "post_basis": next(iter(post_bases)) if len(post_bases) == 1 else None,
            "context": next(iter(contexts))}


def validate(records, role):
    missing, labels = Counter(), Counter()
    by_key, invalid = defaultdict(dict), defaultdict(set)
    rejected, duplicates, partial_identities = [], [], []
    raw_keys = set()
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            rejected.append({"row": index, "reasons": ["ROW_NOT_OBJECT"]})
            missing.update(REQUIRED)
            continue
        absent = sorted(k for k in REQUIRED if row.get(k) in (None, ""))
        missing.update(absent)
        reasons = ["MISSING:" + k for k in absent]
        labels[str(row.get("evidence"))] += 1
        allowed = {"ACTUAL_FILL"} if role == "actual" else {"MODELLED_FILL", "ACTUAL_FILL"}
        if row.get("evidence") not in allowed:
            reasons.append("NOT_ACTUAL_FILL" if role == "actual" else "NOT_MODELLED_OR_REFERENCE_FILL")
        if (not isinstance(row.get("fill_id"), (str, int)) or isinstance(row.get("fill_id"), bool)
                or not str(row["fill_id"]).strip()):
            reasons.append("INVALID_FILL_ID")
        complete_key = all(isinstance(row.get(k), (str, int)) and not isinstance(row[k], bool)
                           and str(row[k]).strip() for k in PHYSICAL_KEY)
        key = tuple(str(row[k]) for k in PHYSICAL_KEY) if complete_key else None
        if key is not None:
            raw_keys.add(key)
        elif not any(k in absent for k in PHYSICAL_KEY):
            reasons.append("INVALID_PHYSICAL_IDENTITY")
        normalized = None
        if not reasons:
            try:
                q, p = number(row["fill_quantity"], positive=True), number(row["fill_price"], positive=True)
                if p >= 1:
                    raise ValueError("INVALID_FILL_PRICE")
                submitted, at = number(row["submitted_at"], timestamp=True), number(row["fill_at"], timestamp=True)
                if at < submitted or row.get("timestamps_reliable") is False:
                    raise ValueError("INVALID_LIFECYCLE_TIME")
                size = number(row["order_quantity"], positive=True) if row.get("order_quantity") is not None else None
                post = number(row["postfill_change"]) if row.get("postfill_change") is not None else None
                horizon = number(row["postfill_horizon_seconds"], positive=True, timestamp=True) if row.get("postfill_horizon_seconds") is not None else None
                basis = row.get("time_basis") or "UNSPECIFIED_SECONDS"
                if not isinstance(basis, str):
                    raise ValueError("INVALID_TIME_BASIS")
                normalized = {"q": q, "p": p, "submitted": submitted, "at": at,
                              "fill_id": str(row["fill_id"]), "order_quantity": size,
                              "post": post, "horizon": horizon,
                              "post_basis": row.get("postfill_time_basis"),
                              "basis": basis,
                              "context": (row.get("profile"), row.get("scenario")),
                              "evidence": row["evidence"]}
            except (ValueError, ArithmeticError, TypeError) as exc:
                reasons.append(str(exc) if isinstance(exc, ValueError) else "INVALID_FILL_ECONOMICS")
        if reasons:
            rejected.append({"row": index, "order_id": row.get("order_id"), "reasons": reasons})
            if key is not None:
                invalid[key].update(reasons)
            else:
                partial_identities.append(row)
            continue
        fid = normalized["fill_id"]
        previous = by_key[key].get(fid)
        if previous is not None:
            if previous == normalized:
                duplicates.append({"key": key_dict(key), "fill_id": fid, "row": index,
                                   "status": "EXACT_DUPLICATE_DEDUPLICATED"})
            else:
                reason = ("AMBIGUOUS_PROFILE_OR_SCENARIO_SELECT_ONE_CONTEXT"
                          if previous["context"] != normalized["context"] else "CONFLICTING_FILL_ID")
                invalid[key].add(reason)
                rejected.append({"row": index, "order_id": row["order_id"],
                                 "reasons": [reason]})
        else:
            by_key[key][fid] = normalized
    # A malformed row with a partial identity cannot silently disappear from a
    # physical order that otherwise appears complete.
    for row in partial_identities:
        known = {i: str(row[k]) for i, k in enumerate(PHYSICAL_KEY)
                 if isinstance(row.get(k), (str, int)) and str(row[k]).strip()}
        if known:
            for key in by_key:
                if all(key[i] == value for i, value in known.items()):
                    invalid[key].add("RELATED_INCOMPLETE_FILL_RECORD")
    groups = {}
    for key, records_by_id in sorted(by_key.items()):
        if key in invalid:
            continue
        try:
            groups[key] = aggregate(list(records_by_id.values()))
        except (ValueError, ArithmeticError, TypeError) as exc:
            invalid[key].add(str(exc) if isinstance(exc, ValueError) else "INVALID_ORDER_TOTALS")
    coverage = {"records": len(records), "unique_physical_orders": len(raw_keys),
                "usable_physical_orders": len(groups),
                "usable_fill_records": sum(g["fill_count"] for g in groups.values()),
                "missing_fields": dict(sorted(missing.items())), "rejected": rejected,
                "duplicates": duplicates,
                "invalid_physical_orders": [{"key": key_dict(k), "reasons": sorted(v)}
                                             for k, v in sorted(invalid.items())],
                "evidence_labels": dict(sorted(labels.items()))}
    return groups, coverage


def public_order(order):
    return {"observed_quantity": text_number(order["quantity"]),
            "observed_cost": text_number(order["cost"]), "fill_records": order["fill_count"],
            "fill_ids": order["fill_ids"], "submitted_at": float(order["submitted"]),
            "first_fill_wait_seconds": float(order["first_wait"]),
            "last_observed_fill_wait_seconds": float(order["last_wait"]),
            "full_fill_wait_seconds": float(order["full_wait"]) if order["full_wait"] is not None else None,
            "completion_status": order["completion"],
            "order_quantity": text_number(order["order_quantity"]) if order["order_quantity"] is not None else None,
            "time_basis": order["basis"], "profile": order["context"][0],
            "scenario": order["context"][1]}


def assess(records, modelled=None, split_at=None):
    split = number(split_at, timestamp=True) if split_at is not None else None
    actual_orders, actual = validate(records, "actual")
    model_orders, model = validate(modelled or [], "modelled")
    differences = []
    for key in sorted(actual_orders.keys() & model_orders.keys()):
        left, right = actual_orders[key], model_orders[key]
        basis_matches = left["basis"] == right["basis"]
        post_matches = (left["post"] is not None and right["post"] is not None
                        and left["horizon"] == right["horizon"]
                        and left["post_basis"] == right["post_basis"])
        differences.append({
            **key_dict(key), "actual": public_order(left), "modelled": public_order(right),
            "quantity_delta": text_number(right["quantity"] - left["quantity"]),
            "observed_cost_delta": text_number(right["cost"] - left["cost"]),
            "wait_delta_seconds": float(right["first_wait"] - left["first_wait"]) if basis_matches else None,
            "wait_comparison_status": "ASSUMED_COMMON_SECONDS_AXIS" if basis_matches and left["basis"] == "UNSPECIFIED_SECONDS"
            else "SAME_DECLARED_TIME_BASIS" if basis_matches else "INCOMPATIBLE_TIME_BASES",
            "partition": "UNSPLIT" if split is None else "CALIBRATION" if left["submitted"] < split else "HELD_OUT",
            "partition_basis": "ACTUAL_PHYSICAL_SUBMISSION; all fills remain with that order",
            "crosses_split_during_fills": bool(split is not None and left["submitted"] < split <= left["last_at"]),
            "postfill_result_delta": text_number(right["post"] - left["post"]) if post_matches else None,
            "postfill_status": "AVAILABLE_QUANTITY_WEIGHTED" if post_matches else "MISSING_OR_INCOMPATIBLE_PRICE_HISTORY",
            "postfill_weighting": "fill_quantity; only complete same-horizon same-time-basis observations",
            "postfill_horizon_seconds": float(left["horizon"]) if post_matches else None,
        })
    unmatched_actual = sorted(actual_orders.keys() - model_orders.keys())
    unmatched_model = sorted(model_orders.keys() - actual_orders.keys())
    compared_waits = [row["wait_delta_seconds"] for row in differences if row["wait_delta_seconds"] is not None]
    clean = not any(side["rejected"] or side["invalid_physical_orders"] for side in (actual, model))
    clean = clean and not unmatched_actual and not unmatched_model
    return {
        "calibration_status": "UNCALIBRATED", "records": len(records),
        "usable_actual_fills": actual["usable_fill_records"],
        "usable_modelled_fills": model["usable_fill_records"],
        "matched_physical_orders": len(differences), "matched_actual_fill_records": sum(actual_orders[k]["fill_count"] for k in actual_orders.keys() & model_orders.keys()),
        "matched_modelled_fill_records": sum(model_orders[k]["fill_count"] for k in actual_orders.keys() & model_orders.keys()),
        "comparison_status": "MODELLED_INPUT_MISSING" if modelled is None else "NO_COMPARABLE_PHYSICAL_ORDERS" if not differences else "COMPARED_OBSERVED_PHYSICAL_ORDERS",
        "zero_observed_quantity_error": bool(differences) and clean and all(Decimal(r["quantity_delta"]) == 0 for r in differences),
        "zero_comparable_wait_error": bool(compared_waits) and clean and all(value == 0 for value in compared_waits),
        "comparable_wait_physical_orders": len(compared_waits),
        "unmatched_actual_physical_orders": [key_dict(k) for k in unmatched_actual],
        "unmatched_modelled_physical_orders": [key_dict(k) for k in unmatched_model],
        "unmatched_actual_fill_records": sum(actual_orders[k]["fill_count"] for k in unmatched_actual),
        "unmatched_modelled_fill_records": sum(model_orders[k]["fill_count"] for k in unmatched_model),
        "missing_fields": actual["missing_fields"], "rejected": actual["rejected"],
        "modelled_missing_fields": model["missing_fields"], "modelled_rejected": model["rejected"],
        "actual_input": actual, "modelled_input": model, "differences": differences,
        "modelled_reference_control": bool(model["evidence_labels"].get("ACTUAL_FILL")),
        "reference_control_note": "ACTUAL_FILL supplied on the modelled side remains explicitly a reference-control input, not independent modelled or real calibration evidence.",
        "quantity_scope": "Observed cumulative records, not a proven complete order unless order_quantity evidence establishes full quantity.",
        "predeclared_split_at": split_at,
        "reason": "Interface only; model parameters have not been fitted and validated on a later independent interval.",
        "does_not_open_wallet_or_subscribe_account": True,
    }
