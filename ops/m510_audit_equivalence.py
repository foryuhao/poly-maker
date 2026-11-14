"""Explain version annotations while checking legacy-rule replay audit content."""

import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from decimal import Decimal

ROUND_ANNOTATIONS = {"window_seconds": Decimal(".5"),
                     "hold_seconds": Decimal(".1"), "stable_recovery": Decimal(".50")}


def normalize(value):
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: normalize(v) for k, v in value.items()}
    if set(ROUND_ANNOTATIONS) <= result.keys():
        # Only the unchanged legacy round settings are admissible annotations.
        # Arbitrary numeric or economic differences are never discarded.
        if any(Decimal(str(result[k])) != v for k, v in ROUND_ANNOTATIONS.items()):
            raise ValueError("LEGACY_ROUND_PARAMETER_ANNOTATION_CHANGED")
        for key in ROUND_ANNOTATIONS:
            del result[key]
    return result


def records(rows, *, legacy_annotations=True):
    ordered, ended, ignored = [], Counter(), Counter()
    for input_seq, kind, raw in rows:
        payload = json.loads(raw)
        if kind == "DEFINITION" and legacy_annotations:
            # This row must identify the new implementation; it is deliberately
            # separate from unchanged economic/state assertions in memory_review.
            ignored["version_definition_rows"] += 1
            continue
        if (kind == "SIGNAL" and payload.get("event") == "CANDIDATE"
                and "run_id" in payload.get("payload", {})):
            del payload["payload"]["run_id"]
            ignored["candidate_run_identity_fields"] += 1
        value = normalize(payload) if legacy_annotations else payload
        record = (input_seq, kind, json.dumps(value, sort_keys=True, separators=(",", ":")))
        if kind == "RESEARCH_EVIDENCE" and payload.get("event") == "FACT_ENDED":
            # The source invalidates a set of facts at one input. Its set order
            # is process-dependent; all identities, times and reasons must match.
            ended[record] += 1
        else:
            ordered.append(record)
    return ordered, ended, ignored


def compare(old_path, new_path, *, legacy_annotations=True):
    values = []
    for path in (old_path, new_path):
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            values.append(db.execute("SELECT input_seq,kind,payload FROM audit ORDER BY seq").fetchall())
    a, ae, ai = records(values[0], legacy_annotations=legacy_annotations)
    b, be, bi = records(values[1], legacy_annotations=legacy_annotations)
    archives = [archive(path) for path in (old_path, new_path)]
    archive_equivalent = (all(v["readable"] and v["rows_match_metadata"] for v in archives)
                          and archives[0].get("payload_sha256") == archives[1].get("payload_sha256")
                          and archives[0]["input_seq"] == archives[1]["input_seq"]
                          and archives[1]["closed_metadata"]
                          and (legacy_annotations or archives[0]["closed_metadata"]))
    return {"equivalent_after_explicit_annotation_normalization": a == b and ae == be and archive_equivalent,
            "raw_rows_identical": values[0] == values[1],
            "source_audit_rows": len(values[0]), "replay_audit_rows": len(values[1]),
            "ordered_non_end_records_identical": a == b,
            "same_input_fact_ends_multisets_identical": ae == be,
            "unavailable_diagnostic_payloads_identical": bool(archive_equivalent),
            "source_archive": archives[0], "replay_archive": archives[1],
            "source_identity_rows": dict(ai), "replay_identity_rows": dict(bi),
            "scope": ("Definition/version identity separate; candidate run identity ignored; legacy0.5s/0.1s/50% round annotations checked then removed; FACT_ENDED order alone ignored at same input with exact identities/times/reasons. All other audit records retain order/content."
                      if legacy_annotations else
                      "Definition and all round parameters retained exactly; only candidate run identity and same-input FACT_ENDED sibling ordering normalized. Both diagnostic archives must be durably closed and have identical decompressed payloads.")}


def archive(database):
    with closing(sqlite3.connect(database.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        row = db.execute("SELECT payload FROM metadata WHERE name='diagnostic_archive'").fetchone()
    if row is None:
        return {"readable": False, "rows_match_metadata": False, "reason": "ARCHIVE_METADATA_MISSING"}
    meta = json.loads(row[0])
    path = database.parent / meta["file"]
    result = {"closed_metadata": meta["closed"], "input_seq": meta["input_seq"],
              "metadata_rows": meta["rows"]}
    digest, rows, last = hashlib.sha256(), 0, b""
    try:
        with gzip.open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(256*1024), b""):
                digest.update(chunk)
                rows += chunk.count(b"\n")
                last = chunk[-1:]
    except (OSError, EOFError) as exc:
        return {**result, "readable": False, "rows_match_metadata": False,
                "reason": type(exc).__name__+":"+str(exc)}
    rows += int(bool(last) and last != b"\n")
    return {**result, "readable": True, "payload_sha256": digest.hexdigest(),
            "payload_rows": rows, "rows_match_metadata": rows == meta["rows"]}
