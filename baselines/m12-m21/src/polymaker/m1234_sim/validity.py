"""Simulation validity combines committed facts with instance-bound failure evidence."""

import json
import sqlite3
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from polymaker.m1234_sim.events import digest

CONTRACT = "arrival-history-v2"
SNAPSHOT_SCHEMA = 4
EVIDENCE_SCHEMA = 1


def new_validity():
    return {"contract": CONTRACT, "status": "VALID", "failures": []}


def simulation_validity(state, recorded_failures=()):
    declared = state.get("validity", {})
    failures = {failure["id"]: failure for failure in declared.get("failures", [])}
    for broker in state.get("brokers", {}).values():
        for failure in broker.get("constraint_conflicts", []):
            failures.setdefault(failure["id"], failure)
    for failure in recorded_failures:
        failures.setdefault(failure["id"], failure)
    if failures or declared.get("status") == "INVALID":
        status = "INVALID"
    elif (state.get("snapshot_schema") == SNAPSHOT_SCHEMA
          and declared.get("contract") == CONTRACT
          and declared.get("status") == "VALID"):
        status = "VALID"
    else:
        status = "UNVERIFIED_LEGACY"
    return {"contract": CONTRACT, "status": status,
            "failures": deepcopy(list(failures.values()))}


def make_binding(definition, database_id):
    return {"database_id": database_id, "definition_hash": digest(definition),
            "implementation_hash": digest(definition["settings"]["implementation"]),
            "market": definition["market"]["slug"],
            "condition_id": definition["market"]["condition_id"],
            "model": definition["model"], "input_schema": definition["input_schema"]}


def tracker_path(path, binding):
    return Path(path).parent / (binding["database_id"] + ".validity.json")


def evidence_record(binding, input_seq, validity):
    return {"evidence_schema": EVIDENCE_SCHEMA, "binding": deepcopy(binding),
            "input_seq": input_seq, "validity": deepcopy(validity)}


def read_evidence(path, binding):
    """A required tracker and optional result records are checked against the database."""
    tracker = tracker_path(path, binding)
    paths = [tracker, Path(path).parent / (binding["database_id"] + ".failure.json")]
    paths += [Path(path).parent / name for name in ("failure.json", "replay.json", "recovery.json")]
    failures, records, foreign, errors = [], [], [], []
    invalid = False
    for source in paths:
        if not source.exists():
            if source == tracker:
                errors.append("MISSING_INSTANCE_TRACKER")
            continue
        try:
            value = json.loads(source.read_text())
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            identity = value.get("binding")
            if not isinstance(identity, dict) or set(identity) != set(binding):
                raise ValueError("missing or incomplete binding")
            UUID(identity["database_id"])
            if any(not isinstance(identity[key], str) or not identity[key]
                   for key in binding if key != "input_schema"):
                raise ValueError("invalid binding field types")
            if type(identity["input_schema"]) is not int:
                raise ValueError("invalid binding input schema")
            if identity["database_id"] != binding["database_id"]:
                if source == tracker:
                    raise ValueError("required tracker belongs to another instance")
                foreign.append({"file": source.name, "binding": identity})
                continue
            if identity != binding:
                raise ValueError("same-instance binding contradicts database")
            if value.get("evidence_schema") != EVIDENCE_SCHEMA:
                raise ValueError("unsupported evidence schema")
            seq = value.get("input_seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
                raise ValueError("invalid input watermark")
            validity = value.get("validity", {})
            status = validity.get("status")
            if source == tracker and status not in {"TRACKING", "INVALID"}:
                raise ValueError("invalid tracker state")
            rows = validity.get("failures", [])
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or type(row.get("input_seq")) is not int or not 0 <= row["input_seq"] <= seq
                or not isinstance(row.get("reason"), str)
                for row in rows
            ):
                raise ValueError("invalid failure facts or watermarks")
            evidence_errors = value.get("evidence_errors", [])
            if not isinstance(evidence_errors, list):
                raise ValueError("invalid exported evidence diagnostics")
            errors.extend(str(error) for error in evidence_errors)
            if value.get("status") == "FAIL" and status != "INVALID":
                raise ValueError("failed result has no invalidity fact")
            if status == "INVALID" and not rows:
                raise ValueError("invalid record has no failure facts")
            invalid |= status == "INVALID"
            failures.extend(rows)
            records.append({"file": source.name, "binding": identity,
                            "input_seq": seq, "status": status})
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append(source.name + ":" + str(exc))
    return failures, records, foreign, errors, invalid


def database_validity(path, state, recorded_failures=None, *, connection=None):
    """No earlier committed watermark can erase a matching persistent failure."""
    with (sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
          if connection is None else nullcontext(connection)) as db:
        if recorded_failures is None:
            recorded_failures = [json.loads(row[0])["conflict"] for row in db.execute(
                "SELECT payload FROM audit WHERE kind='SIMULATION_INVALID' ORDER BY seq")]
        base = simulation_validity(state, recorded_failures)
        row = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='metadata'").fetchone():
            row = db.execute("SELECT payload FROM metadata WHERE name='identity'").fetchone()
        if row is None:
            if base["status"] == "VALID":
                base["status"] = "UNVERIFIED_EVIDENCE"
                base["evidence"] = {"errors": ["MISSING_DATABASE_IDENTITY"]}
            return base
        errors = []
        try:
            binding = json.loads(row[0])
            definition_row = db.execute(
                "SELECT payload FROM audit WHERE kind='DEFINITION' ORDER BY seq LIMIT 1").fetchone()
            if definition_row is None:
                raise ValueError("missing committed definition")
            definition = json.loads(definition_row[0])["definition"]
            if binding != make_binding(definition, binding["database_id"]):
                raise ValueError("database identity contradicts committed definition")
            if (binding["definition_hash"] != state.get("definition_hash")
                    or binding["market"] != state.get("market", {}).get("slug")):
                raise ValueError("snapshot contradicts database identity")
            failures, records, foreign, errors, invalid = read_evidence(path, binding)
        except (ValueError, TypeError, KeyError) as exc:
            binding, failures, records, foreign, invalid = None, [], [], [], False
            errors.append("INVALID_DATABASE_IDENTITY:" + str(exc))
        audit_seq, audit_input = db.execute(
            "SELECT COALESCE(MAX(seq),0),COALESCE(MAX(input_seq),0) FROM audit").fetchone()
        snapshot_input = state.get("input_seq")
        if type(snapshot_input) is not int or snapshot_input < audit_input:
            errors.append("COMMITTED_AUDIT_AHEAD_OF_OR_MISSING_SNAPSHOT_WATERMARK")
    combined = {row["id"]: row for row in base["failures"]}
    combined.update({row["id"]: row for row in failures})
    if invalid or base["status"] == "INVALID" or combined:
        base["status"] = "INVALID"
    elif errors and base["status"] != "UNVERIFIED_LEGACY":
        base["status"] = "UNVERIFIED_EVIDENCE"
    base["failures"] = list(combined.values())
    failure_watermarks = [row["input_seq"] for row in records if row["status"] == "INVALID"]
    failure_watermarks += [row.get("input_seq", 0) for row in base["failures"]]
    base["evidence"] = {"binding": binding, "snapshot_input_seq": state.get("input_seq"),
                        "committed_audit_seq": audit_seq, "committed_audit_input_seq": audit_input,
                        "failure_input_seq": max(failure_watermarks, default=None),
                        "records": records,
                        "foreign_records": foreign, "errors": errors}
    return base
