"""Keep audit comparison exceptions narrower than economic assertions."""

import gzip
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("audit_equivalence", Path(__file__).parents[1] / "ops/m510_audit_equivalence.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def row(kind, payload):
    return (1, kind, json.dumps(payload))


def test_fact_end_siblings_can_permute_but_confirmations_and_economics_cannot():
    x = row("RESEARCH_EVIDENCE", {"event": "FACT_ENDED", "payload": {"id": "a", "at": 1, "reason": "GAP"}})
    y = row("RESEARCH_EVIDENCE", {"event": "FACT_ENDED", "payload": {"id": "b", "at": 1, "reason": "GAP"}})
    assert MOD.records([x, y]) == MOD.records([y, x])
    a, b = row("MODELLED_FILL", {"quantity": "1"}), row("SETTLE", {"payout": "1"})
    assert MOD.records([a, b]) != MOD.records([b, a])
    assert MOD.records([a]) != MOD.records([row("MODELLED_FILL", {"quantity": "2"})])


def test_only_verified_legacy_round_annotations_are_normalized():
    bare = {"values": {"hit": "2", "recovery": ".5"}}
    annotated = {"values": {**bare["values"], "window_seconds": .5,
                             "hold_seconds": .1, "stable_recovery": "0.50"}}
    assert MOD.normalize(annotated) == bare
    annotated["values"]["hold_seconds"] = .01
    with pytest.raises(ValueError, match="LEGACY_ROUND_PARAMETER"):
        MOD.normalize(annotated)


def test_only_candidate_run_identity_is_ignored():
    a = row("SIGNAL", {"event": "CANDIDATE", "payload": {"run_id": "old", "id": "a"}})
    b = row("SIGNAL", {"event": "CANDIDATE", "payload": {"run_id": "new", "id": "a"}})
    assert MOD.records([a]) == MOD.records([b])
    c = row("SIGNAL", {"event": "CANDIDATE", "payload": {"run_id": "new", "id": "b"}})
    assert MOD.records([a]) != MOD.records([c])


def test_same_configuration_comparison_keeps_definition_and_nonlegacy_parameters():
    a = row("RESEARCH_EVIDENCE", {"window_seconds": .15, "hold_seconds": .01,
                                 "stable_recovery": ".40"})
    b = row("RESEARCH_EVIDENCE", {"window_seconds": .10, "hold_seconds": .01,
                                 "stable_recovery": ".40"})
    assert MOD.records([a], legacy_annotations=False) == MOD.records([a], legacy_annotations=False)
    assert MOD.records([a], legacy_annotations=False) != MOD.records([b], legacy_annotations=False)
    first, second = row("DEFINITION", {"tick": ".01"}), row("DEFINITION", {"tick": ".02"})
    assert MOD.records([first], legacy_annotations=False) != MOD.records([second], legacy_annotations=False)


def test_archive_checks_payload_and_catches_truncated_stream(tmp_path):
    database = tmp_path / "simulation.sqlite3"
    meta = {"file": "unavailable-diagnostics.jsonl.gz", "closed": False, "rows": 2, "input_seq": 12}
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE metadata(name TEXT, payload TEXT)")
        db.execute("INSERT INTO metadata VALUES (?,?)", ("diagnostic_archive", json.dumps(meta)))
    path = tmp_path / meta["file"]
    with gzip.open(path, "wb") as stream:
        stream.write(b'{"a":1}\n{"a":2}\n')
    result = MOD.archive(database)
    assert result["readable"] and result["rows_match_metadata"] and result["payload_rows"] == 2
    assert result["closed_metadata"] is False  # Retain original stale closure fact.
    path.write_bytes(path.read_bytes()[:-8])
    assert MOD.archive(database)["readable"] is False
