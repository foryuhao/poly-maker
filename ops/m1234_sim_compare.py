"""Compare complete simulation state and ordered audit rows without normalizing facts."""

import argparse
import hashlib
import json
from itertools import zip_longest
from pathlib import Path

from polymaker.m1234_sim.events import digest, encode, write_json
from polymaker.m1234_sim.report import read_state
from polymaker.m1234_sim.store import read_audit
from polymaker.m1234_sim.validity import database_validity

DIAGNOSTIC_EVENTS = {
    "AP_NOT_CONFIRMED",
    "AP_CONFIRMED",
    "M2_AP_BUY_REFERENCE",
    "RF_CONFIRMED",
    "EX_ENDED",
    "ROUND_CLOSED",
    "EX_NOT_CONFIRMED",
    "ROUND_NOT_QUALIFIED",
}


def diagnostic(row):
    """Only observed primitive diagnostics, never candidates or broker facts."""
    if row["kind"] != "SIGNAL":
        return False
    payload = row["payload"]
    event = payload.get("event")
    reason = payload.get("payload", {}).get("reason")
    return (
        event in DIAGNOSTIC_EVENTS
        or event == "SIGNAL_INVALIDATED"
        and reason in {"M1_BOOK_UNAVAILABLE", "M1_INPUT_BACKLOG"}
        or event == "RF_INVALIDATED"
        and reason == "M1_ANCHOR_CHANGED"
    )


def diagnostic_groups(rows):
    """Canonicalize contiguous diagnostics within one input, preserving anchors."""
    pending = []

    def group():
        return {
            "seq_start": pending[0]["seq"],
            "seq_end": pending[-1]["seq"],
            "diagnostics": sorted(
                ({k: v for k, v in row.items() if k != "seq"} for row in pending),
                key=encode,
            ),
        }

    for row in rows:
        if pending and (not diagnostic(row) or row["input_seq"] != pending[-1]["input_seq"]):
            yield group()
            pending = []
        if diagnostic(row):
            pending.append(row)
        else:
            yield row
    if pending:
        yield group()


def audit_hash(path):
    value = hashlib.sha256(b"[")
    count = 0
    for row in read_audit(path):
        if count:
            value.update(b",")
        value.update(encode(row).encode())
        count += 1
    value.update(b"]")
    return value.hexdigest(), count


def compare(online, offline, run_id, *, classify_diagnostics=False):
    first_difference = None
    differences = 0
    for number, (left, right) in enumerate(
        zip_longest(read_audit(online), read_audit(offline)), start=1
    ):
        if left != right:
            differences += 1
            if first_difference is None:
                first_difference = {"row": number, "online": left, "offline": right}
    online_hash, online_count = audit_hash(online)
    offline_hash, offline_count = audit_hash(offline)
    first_state = read_state(online)
    second_state = read_state(offline)
    online_state = digest(first_state)
    offline_state = digest(second_state)
    validity = {"online": database_validity(online, first_state),
                "offline": database_validity(offline, second_state)}
    bindings = {name: value.get("evidence", {}).get("binding") for name, value in validity.items()}
    common_definition = all(bindings.values()) and (
        {key: value for key, value in bindings["online"].items() if key != "database_id"}
        == {key: value for key, value in bindings["offline"].items() if key != "database_id"}
    )
    evidence_verified = common_definition and all(
        not value.get("evidence", {}).get("errors") for value in validity.values())
    failure_facts_equal = validity["online"]["failures"] == validity["offline"]["failures"]
    failure_watermarks_equal = (validity["online"].get("evidence", {}).get("failure_input_seq")
                               == validity["offline"].get("evidence", {}).get("failure_input_seq"))
    classified_differences = None
    semantic_differences = None
    if classify_diagnostics:
        classified_differences = sum(
            left != right
            for left, right in zip_longest(
                diagnostic_groups(read_audit(online)), diagnostic_groups(read_audit(offline))
            )
        )
        semantic_differences = sum(
            left != right
            for left, right in zip_longest(
                (row for row in read_audit(online) if not diagnostic(row)),
                (row for row in read_audit(offline) if not diagnostic(row)),
            )
        )
    status = "FAIL"
    if online_state == offline_state:
        if differences == 0:
            status = "PASS"
        elif classify_diagnostics and classified_differences == semantic_differences == 0:
            status = "PASS_WITH_DIAGNOSTIC_ORDER_DIFFERENCES"
    equivalent = (status.startswith("PASS") and evidence_verified and failure_facts_equal
                  and failure_watermarks_equal)
    if not equivalent or any(value["status"] != "VALID" for value in validity.values()):
        status = "FAIL"
    return {
        "status": status,
        "equivalence": "MATCH" if equivalent else "DIFF",
        "simulation_validity": validity,
        "binding_comparison": {"bindings": bindings, "common_definition": bool(common_definition),
                               "individually_verified": bool(evidence_verified),
                               "failure_facts_equal": failure_facts_equal,
                               "failure_watermarks_equal": failure_watermarks_equal,
                               "instance_scope": "Independent executions may have distinct physical database IDs. All binding fields are exposed and validated."},
        "strict_comparison": "PASS" if differences == 0 else "DIFF",
        "run_id": run_id,
        "kind": "COMPLETE_STATE_AND_ORDERED_AUDIT_COMPARISON",
        "online": str(Path(online).resolve()),
        "offline": str(Path(offline).resolve()),
        "state_hashes": {"online": online_state, "offline": offline_state},
        "state_equal": online_state == offline_state,
        "audit_hashes": {"online": online_hash, "offline": offline_hash},
        "audit_rows": {"online": online_count, "offline": offline_count},
        "different_audit_rows": differences,
        "first_difference": first_difference,
        "normalization": "Raw comparison ignores nothing. Optional classification sorts only allowlisted contiguous primitive SIGNAL diagnostics within one input; all payload fields, input/sequence boundaries, candidates, actions, IDs, amounts and times retained.",
        "diagnostic_classification_requested": classify_diagnostics,
        "remaining_differences_after_diagnostic_classification": classified_differences,
        "candidate_action_economic_ordered_differences": semantic_differences,
        "diagnostic_event_allowlist": sorted(DIAGNOSTIC_EVENTS)
        + ["SIGNAL_INVALIDATED:BOOK_UNAVAILABLE/INPUT_BACKLOG", "RF_INVALIDATED:ANCHOR_CHANGED"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", required=True)
    parser.add_argument("--offline", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--classify-signal-diagnostics", action="store_true")
    args = parser.parse_args()
    result = compare(
        args.online, args.offline, args.run_id,
        classify_diagnostics=args.classify_signal_diagnostics,
    )
    write_json(args.output, result)
    print(json.dumps({"status": result["status"], "different_audit_rows": result["different_audit_rows"], "output": args.output}))
    if not result["status"].startswith("PASS"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
