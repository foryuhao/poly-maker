"""Bound run terminals and stopped-run evidence, independent of report rendering."""

import json
import sqlite3
from pathlib import Path
from uuid import UUID

from polymaker.m1234_sim import INPUT_SCHEMA, MODEL
from polymaker.m1234_sim.events import digest
from polymaker.m1234_sim.store import read_transaction
from polymaker.m1234_sim.validity import database_validity

RUN_SCHEMA = 1


def run_identity(settings, run_id):
    if str(UUID(run_id)) != run_id:
        raise ValueError("SIM_RUN_IDENTITY_INVALID")
    return {"run_id": run_id, "settings_hash": digest(settings),
            "implementation_hash": digest(settings["implementation"]),
            "model": settings.get("model", MODEL),
            "input_schema": settings.get("input_schema", INPUT_SCHEMA)}


def failure_path(directory, identity):
    return Path(directory) / (identity["run_id"] + ".run-failure.json")


def member_state(database):
    with read_transaction(database) as db:
        row = db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()
        if row is None:
            raise ValueError("SIM_SNAPSHOT_UNAVAILABLE")
        state = json.loads(row[0])
        validity = database_validity(database, state, connection=db)
        owner = db.execute("SELECT payload FROM metadata WHERE name='run_owner'").fetchone()
    return {"market": state["market"]["slug"], "input_seq": state["input_seq"],
            "binding": validity.get("evidence", {}).get("binding"), "validity": validity,
            "run_owner": json.loads(owner[0]) if owner else None}


def validate_outcome(record):
    if record.get("status") not in {"PASS", "FAIL"}:
        raise ValueError("invalid terminal status")
    cause, cleanup = record.get("primary_error"), record.get("cleanup_errors", [])
    if record["status"] == "PASS" and (cause or cleanup or record.get("invalid_markets")):
        raise ValueError("success terminal contradicts known failure")
    if record["status"] == "FAIL" and not (cause or cleanup
            or record.get("reason") in {"MANUAL_STOP", "MAX_DURATION"}):
        raise ValueError("failure terminal has no failure cause or stop evidence")
    if cause is not None and (not isinstance(cause, dict)
            or not isinstance(cause.get("error_type"), str)
            or not isinstance(cause.get("error"), str)):
        raise ValueError("malformed primary failure")
    if not isinstance(cleanup, list) or any(not isinstance(row, dict)
            or not all(isinstance(row.get(key), str) for key in ("step", "error_type", "error"))
            for row in cleanup):
        raise ValueError("malformed cleanup failures")


def inspect_run(directory):
    """Absence of a verified terminal never establishes acceptance or liveness."""
    directory = Path(directory)
    errors, records, members, invalid, unassociated_invalid = [], [], [], [], []
    identity, plan = None, None
    try:
        plan = json.loads((directory / "plan.json").read_text())
        identity = run_identity(plan["settings"], plan["run_identity"]["run_id"])
        if plan.get("run_schema") != RUN_SCHEMA or plan["run_identity"] != identity:
            raise ValueError("run identity contradicts plan settings")
        if plan["settings_hash"] != identity["settings_hash"]:
            raise ValueError("plan settings hash mismatch")
        if identity["model"] != MODEL or identity["input_schema"] != INPUT_SCHEMA:
            raise ValueError("run model or input version is not supported")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append("RUN_PLAN_UNVERIFIED:" + str(exc))
        identity = None
    paths = [directory / "completion.json"]
    if identity is not None:
        paths.append(failure_path(directory, identity))
    expected = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            record = json.loads(path.read_text())
            if identity is None or record.get("run_schema") != RUN_SCHEMA:
                raise ValueError("missing supported run association")
            if record.get("run_identity") != identity:
                raise ValueError("terminal belongs to a different run")
            validate_outcome(record)
            if path != paths[0] and record["status"] != "FAIL":
                raise ValueError("failure channel contains nonfailure terminal")
            declared = record["members"]
            if not isinstance(declared, list) or len({m["market"] for m in declared}) != len(declared):
                raise ValueError("invalid terminal membership")
            declared_members = {}
            for member in declared:
                if type(member["input_seq"]) is not int or member["input_seq"] < 0:
                    raise ValueError("invalid member watermark")
                database = (directory / member["database"]).resolve()
                if not database.is_relative_to(directory.resolve().parent):
                    raise ValueError("member database is outside the run export")
                previous = expected.get(member["market"])
                if previous and previous["binding"] != member["binding"]:
                    raise ValueError("terminal member identity contradicts earlier terminal")
                declared_members[member["market"]] = {**member, "database": str(database)}
            expected.update(declared_members)
            records.append({"file": path.name, "value": record})
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append(path.name + ":" + str(exc))
    latest = directory / "latest.json"
    if identity is not None and latest.exists():
        try:
            cause = json.loads(latest.read_text()).get("run_failure")
            if cause is not None:
                if cause.get("run_identity") != identity or cause.get("run_schema") != RUN_SCHEMA:
                    raise ValueError("report failure has no matching run association")
                validate_outcome(cause)
                if cause["status"] != "FAIL":
                    raise ValueError("report failure contains no failure")
                records.append({"file": latest.name, "value": {key: cause.get(key) for key in
                    ("status", "run_identity", "run_schema", "reason", "primary_error", "cleanup_errors")}})
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append("REPORT_FAILURE_UNVERIFIED:" + str(exc))
    databases = {str((p / ("simulation.sqlite3" if (p / "simulation.sqlite3").exists()
                          else "snapshot.sqlite3")).resolve())
                 for p in directory.glob("btc-updown-5m-*") if p.is_dir()
                 and ((p / "simulation.sqlite3").exists() or (p / "snapshot.sqlite3").exists())}
    databases.update(member["database"] for member in expected.values())
    for database in sorted(databases):
        try:
            member = member_state(database)
            binding = member["binding"]
            if identity is None and member["validity"]["status"] == "INVALID":
                unassociated_invalid.append(member)
                errors.append("simulation invalid in bound database; run association unverified: " + database)
                continue
            if identity is not None and (not binding
                    or binding["implementation_hash"] != identity["implementation_hash"]
                    or binding["model"] != identity["model"]
                    or binding["input_schema"] != identity["input_schema"]):
                raise ValueError("member implementation does not match run plan")
            declared = expected.get(member["market"])
            if identity is None or member["run_owner"] != identity and (member["run_owner"] is not None
                    or declared is None):
                raise ValueError("member has no verified association with this run")
            if declared is not None:
                if declared["binding"] != binding:
                    raise ValueError("member database instance differs from terminal")
                if declared["input_seq"] < member["input_seq"]:
                    raise ValueError("terminal precedes committed member watermark")
                watermark = member["validity"].get("evidence", {}).get("failure_input_seq")
                if watermark is not None and watermark > declared["input_seq"]:
                    raise ValueError("terminal precedes known member failure")
                member["terminal_input_seq"] = declared["input_seq"]
                member["ended"] = declared["ended"]
            if member["validity"]["status"] == "INVALID":
                invalid.append(member["market"])
            elif member["validity"]["status"] != "VALID":
                errors.append("member validity unverified: " + member["market"])
            members.append({**member, "database": database})
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            errors.append("MEMBER_UNVERIFIED:" + str(database) + ":" + str(exc))
    failed = [row for row in records if row["value"]["status"] == "FAIL"]
    if invalid or failed or unassociated_invalid:
        status = "FAIL"
    elif errors or not records:
        status = "INCOMPLETE"
    else:
        status = "PASS"
        if len(expected) != len(members) or any(not m.get("ended") for m in members):
            status = "INCOMPLETE"
            errors.append("terminal membership is incomplete")
    return {"status": status, "run_identity": identity, "errors": errors,
            "invalid_markets": invalid, "members": members, "records": records,
            "unassociated_invalid_databases": unassociated_invalid,
            "terminal": failed[0]["value"] if failed else records[0]["value"] if records else None,
            "liveness": "NOT_ESTABLISHED", "acceptance_status": "PENDING_FULL_CHECKS"}
