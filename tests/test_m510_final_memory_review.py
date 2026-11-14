"""Reject stitched resource evidence and audit differences hidden by equal snapshots."""

import gzip
import hashlib
import json
import sqlite3

import m510_final_memory_review as review


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value)+"\n")


def selected():
    return {"source_root": "frozen", "signal_variant": "v6",
            "frequency_parameters": {"M7": {}}, "parameters_sha256": "fixed"}


def member(root, run, market, owner):
    directory = root / run / market
    directory.mkdir(parents=True)
    state = {"input_seq": 1, "coverage": {"complete_window": True}, "validity": {"status": "VALID"},
             "resolution": {"winner": "Up"}, "brokers": {"research/trade-through": {}}}
    definition = {"settings": {"m5_m11": {"signal_variant": "v6", "frequency_parameters": {"M7": {}}}}}
    with sqlite3.connect(directory / "simulation.sqlite3") as db:
        db.execute("CREATE TABLE snapshots(name TEXT,payload TEXT)")
        db.execute("INSERT INTO snapshots VALUES ('state',?)", (json.dumps(state),))
        db.execute("CREATE TABLE audit(seq INTEGER,input_seq INTEGER,kind TEXT,payload TEXT)")
        db.execute("INSERT INTO audit VALUES (1,1,'DEFINITION',?)", (json.dumps({"definition": definition}),))
        db.execute("CREATE TABLE metadata(name TEXT,payload TEXT)")
        db.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("run_owner", json.dumps(owner)), ("diagnostic_archive", json.dumps({
                "file": "diagnostic.gz", "closed": True, "rows": 0, "input_seq": 1}))])
    with gzip.open(directory / "diagnostic.gz", "wb"):
        pass
    put(directory / "replay-state.json", state)


def test_resource_rejects_stitched_run_identities_and_never_passes_partial288(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "configuration", lambda *_: (selected(), {}, True))
    cohort = [{"market": f"m{i}", "input_seq": 1, "winner": "Up"} for i in range(288)]
    put(tmp_path / "cohort288-final.json", {"markets": cohort})
    put(tmp_path / "cohort288-final-freeze.json", {
        "sha256": hashlib.sha256((tmp_path / "cohort288-final.json").read_bytes()).hexdigest()})
    proof = {"expected_source_root": "frozen", "status": "MATCH", "loaded_modules": {"same": "build"}}
    for run in ("selected", "resource"):
        put(tmp_path / run / "import-proof.json", proof)
    rows = []
    for index in range(2):
        market = f"m{index}"
        member(tmp_path, "resource", market, "one" if index == 0 else "two")
        rows.append({"market": market, "index": index+1, "input_seq": 1, "retained_engines": index+1,
                     "retained_research_graphs": 0, "retirement_gc_seconds": 0,
                     "retirement_collected_objects": 0, "rss_mib": 80, "peak_rss_mib": 90})
    put(tmp_path / "resource/progress.json", {"markets": rows})
    result = review.resource_review(tmp_path, "resource", "selected")
    assert result["status"] == "FAIL"
    assert "MULTIPLE_OR_MISSING_COMMITTED_RUN_IDENTITIES" in result["errors"]
    with sqlite3.connect(tmp_path / "resource/m1/simulation.sqlite3") as db:
        db.execute("UPDATE metadata SET payload=? WHERE name='run_owner'", (json.dumps("one"),))
    result = review.resource_review(tmp_path, "resource", "selected")
    assert result["status"] == "PARTIAL" and result["checked_markets"] == 2
    assert not result["continuous_single_process"]


def test_invariance_detects_changed_fill_audit_even_with_identical_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "configuration", lambda _root, run: (
        selected(), {"settings": {"identical": True}, "run_identity": run}, True))
    cohort = [{"market": f"m{i}", "input_seq": 1, "winner": "Up"} for i in range(25)]
    put(tmp_path / "cohort25.json", {"markets": cohort})
    for run in ("optimized", "control"):
        put(tmp_path / run / "completion.json", {"memory_release": run == "optimized"})
        for item in cohort:
            member(tmp_path, run, item["market"], run)
    assert review.invariance_review(tmp_path, "optimized", "control")["status"] == "PASS"
    with sqlite3.connect(tmp_path / "optimized/m0/simulation.sqlite3") as db:
        db.execute("INSERT INTO audit VALUES (2,1,'MODELLED_FILL',?)", (json.dumps({"quantity": "1"}),))
    result = review.invariance_review(tmp_path, "optimized", "control")
    assert result["status"] == "FAIL"
    assert result["checks"][0]["snapshots_identical"]
    assert not result["checks"][0]["audit"]["ordered_non_end_records_identical"]
