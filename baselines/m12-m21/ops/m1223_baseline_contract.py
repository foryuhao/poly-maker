"""Public-only sequential baseline identities, atomic state and admission."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

SCHEMA = "sop-sequential-baseline-v1"
ORDER = ["M23", "M12"]
VARIANTS = {"M23": "M23_FIRST_SELL_WHILE_VALID", "M12": "M12_VALIDITY_OVERLAP"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_plan(plan):
    expected = {
        "schema": SCHEMA, "mode": "SIMULATION_ONLY_PUBLIC_DATA",
        "strategy_order": ORDER, "variants": VARIANTS,
        "markets_per_strategy": 288, "seconds_per_strategy": 86400,
        "quantity_step": "0.1", "order_budget_usdc": "5", "session_budget_usdc": "10",
        "maximum_posts_per_direction": 1, "price_filter": None, "real_orders": 0,
        "switch_after": "PLANNED_COHORT_OBSERVED_AND_CLEAN_SHUTDOWN",
        "frequency_gate": "NOT_APPLIED_USER_AUTHORIZED_BASELINE_NOT_FREQUENCY_SELECTION",
        "restart": "STOP_VISIBLE_NEVER_RESTART_OR_RESELECT_COHORT",
    }
    if plan != expected:
        raise ValueError("BASELINE_PLAN_DIFFERS_FROM_CONFIRMED_CONTRACT")
    return plan


def parameter_document(strategy):
    from sop_signals import m12_overlap, m23
    from sop_signals.m12 import parameter_identity
    from sop_signals.parameters import load_revision

    module = {"M23": m23, "M12": m12_overlap}[strategy]
    document = {
        "strategy": strategy, "variant_id": VARIANTS[strategy], "revision_round": 0,
        "sop_sha256": module.SOP_SHA256,
        "document_parameters_sha256": module.DOCUMENT_PARAMETERS_SHA256,
        "pnl_used_for_selection": False,
        "parameters": parameter_identity(module.Parameters())[0],
    }
    load_revision(document)
    return document


def verify_runtime(runtime):
    runtime = Path(runtime).resolve()
    manifest = json.loads((runtime / "frozen-manifest.json").read_text())
    for rel, expected in manifest.items():
        path = (runtime / rel).resolve()
        if not path.is_relative_to(runtime) or not path.is_file() or sha256(path) != expected:
            raise ValueError("BASELINE_FROZEN_SOURCE_CHANGED:" + rel)
    actual = {str(p.relative_to(runtime)) for folder in ("src", "extension")
              for p in (runtime / folder).rglob("*.py")}
    expected = {p for p in manifest if p.endswith(".py") and p.split("/", 1)[0] in {"src", "extension"}}
    if actual != expected:
        raise ValueError("BASELINE_UNMANIFESTED_SOURCE")
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def cohort(strategy, first, plan, runtime_hash):
    validate_plan(plan)
    if strategy not in ORDER or type(first) is not int or first % 300:
        raise ValueError("BASELINE_COHORT_IDENTITY")
    return {
        "schema": SCHEMA, "strategy": strategy, "variant_id": VARIANTS[strategy],
        "stage": "USER_AUTHORIZED_24H_BASELINE", "frequency_selection_used": False,
        "market_epochs": list(range(first, first + 86400, 300)),
        "window_start_epoch": first, "window_end_epoch": first + 86400,
        "denominator": 288, "runtime_sha256": runtime_hash,
        "selection": "NEXT_288_CONSECUTIVE_MARKETS_PREDECLARED_BEFORE_COLLECTION",
    }


def completed(result, expected_cohort):
    expected = [f"btc-updown-5m-{v}" for v in expected_cohort["market_epochs"]]
    return (result.get("status") == "PASS" and result.get("reason") == "PLANNED_COMPLETE"
            and result.get("markets") == len(expected)
            and [m.get("market") for m in result.get("members", [])] == expected
            and all(m.get("ended") and m.get("database_closed") for m in result["members"]))


def validate_cohort(value, strategy, plan, runtime_hash):
    if value != cohort(strategy, value["window_start_epoch"], plan, runtime_hash):
        raise ValueError("BASELINE_COHORT_CHANGED_OR_WRONG_STRATEGY")
    return value
