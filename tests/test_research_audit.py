"""Offline audit checks against real M4A writer/lifecycle artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.research.audit import _coverage_passes, audit_collection, audit_session, write_audit
from polymaker.research.capture import CausalCapture
from polymaker.research.lifecycle import EpisodeLifecycle
from polymaker.research.observer import ResearchConfig, ResearchObserver
from polymaker.research.schema import DatasetManifest
from polymaker.research.writer import ResearchWriter


def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, name: str = "session",
            end_us: int = 104_000_000, stop_us: int | None = None,
            duplicate_fill: bool = False, mutations: bool = False,
            terminal_cancel: bool = False, terminal_decision: bool = True,
            terminal_state: str = "COMPLETE") -> Path:
    current = [100_000_000, 100_000_000_000]
    monkeypatch.setattr("polymaker.research.capture.clock", lambda: tuple(current))
    observer = ResearchObserver(ResearchConfig(enabled=True))
    assert observer.queue is not None
    capture = CausalCapture(observer, name)
    lifecycle = EpisodeLifecycle(capture, dataset_id="m4a", market_slug="slug",
        condition_id="condition", yes_token_id="yes", no_token_id="no",
        market_start_us=0, market_end_us=end_us, strategy_version="test",
        strategy_config_id="public", venue_min_order_qty=1)
    config_hash = hashlib.sha256(b"{}").hexdigest()
    manifest = DatasetManifest(schema_version=1, dataset_id="m4a", session_id=name,
        code_commit="0" * 40, strategy_config_id="public", effective_strategy_config={},
        config_sha256=config_hash, feature_version=None, label_version=None,
        policy_version=None, execution_version=None,
        payload_versions={"ResearchEvent": 1, "Episode": 1, "StateSnapshot": 1},
        condition_id="condition", market_slug="slug", yes_token_id="yes",
        no_token_id="no", split_boundaries_us={}, started_at_us=100_000_000,
        ended_at_us=None, file_sha256={}, event_watermark=0, gap_count=0,
        close_status="OPEN")
    writer = ResearchWriter(tmp_path, "m4a", name, observer.queue, manifest=manifest)
    writer.start()

    def emit(kind: str, payload: dict, at: int, generation: int | None = None) -> None:
        current[0], current[1] = at, at * 1000
        capture.emit(kind, "condition", payload, connection_generation=generation)

    emit("CONNECTION", {"stream": "market", "state": "UP"}, 100_000_000, 1)
    emit("CONNECTION", {"stream": "user", "state": "UP"}, 100_000_001, 1)
    emit("BOOK", {"token_id": "yes", "bids": ((.60, 9),), "asks": ((.62, 8),),
                  "tick_size": .01, "snapshot_generation": 1}, 100_000_002, 1)
    emit("BOOK", {"token_id": "no", "bids": ((.38, 9),), "asks": ((.40, 8),),
                  "tick_size": .01, "snapshot_generation": 1}, 100_000_003, 1)
    emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "token_id": "yes",
                         "price": .60, "qty": 3, "remaining_qty": 3,
                         "transition": "REGISTERED"}, 100_000_004)
    fill = {"role": "FIRST", "trade_id": "trade", "maker_order_id": "first",
            "token_id": "yes", "side": "BUY", "qty": 2, "price": ".6",
            "accepted": True, "quarantined": False, "status": "MATCHED",
            "fill_truth_ready": True}
    emit("FILL_REVISION", fill, 101_000_000)
    if duplicate_fill:
        emit("FILL_REVISION", fill, 101_000_000)
    emit("SAFETY", {"transition": "FIRST_FINALIZED", "first_finalized": True}, 101_000_001)
    emit("SAFETY", {"transition": "PAIR_TERMINAL", "pair_terminal": True}, 101_000_002)
    if terminal_decision:
        emit("BASELINE_DECISION", {"strategy_state": terminal_state, "pure_quote": None},
             101_000_003)
    if terminal_cancel:
        emit("ORDER_STATE", {"order_id": "first", "role": "FIRST", "token_id": "yes",
                             "price": .6, "qty": 3, "remaining_size": 1,
                             "status": "LIVE", "cancel": True}, 101_000_004)
    if mutations:
        emit("DELTA", {"token_id": "yes", "side": "BUY", "price": .61, "size": 5}, 101_000_010)
        emit("TICK", {"token_id": "yes", "tick_size": .02}, 101_000_011)
        emit("TICK", {"token_id": "no", "tick_size": .02}, 101_000_012)
        emit("REST_BOOK", {"token_id": "no", "bids": ((.37, 3),),
                           "asks": ((.41, 4),), "tick_size": .02,
                           "snapshot_generation": 2}, 101_000_013, 1)
    for at in range(102_000_000, (stop_us or end_us) + 1, 1_000_000):
        current[0], current[1] = at, at * 1000
        lifecycle.tick(at, at * 1000)
    asyncio.run(lifecycle.close())
    assert writer.close()
    return writer.directory


def rows(path: Path, name: str) -> list[dict]:
    return [json.loads(line) for line in (path / name).read_text().splitlines()]


def replace_rows(path: Path, name: str, data: list[dict]) -> None:
    artifact = path / name
    artifact.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in data))
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["file_sha256"][name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    (path / "manifest.json").write_text(json.dumps(manifest) + "\n")


def gates(result: dict) -> set[str]:
    return {issue["gate"] for issue in result["issues"]}


def test_valid_writer_session_and_read_only_outputs(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    before = {name: (path / name).read_bytes() for name in
              ("manifest.json", "quality.json", "events.jsonl", "episodes.jsonl", "states.jsonl")}
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]
    assert result["counts"]["events"] > 0
    assert result["cadence"][result["episodes"][0]]["coverage"] == 1.0
    collection = audit_collection([path])
    assert collection["integrity"] == "PASS"
    assert collection["collection"] == "UNESTABLISHED"
    assert collection["timing"]["status"] == "UNESTABLISHED"
    write_audit(collection, tmp_path / "reports")
    assert (tmp_path / "reports/audit.json").is_file()
    assert (tmp_path / "reports/audit_report.md").is_file()
    assert before == {name: (path / name).read_bytes() for name in before}
    with pytest.raises(ValueError):
        write_audit(collection, path)


def test_full_observation_window_after_baseline_complete(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch, end_us=400_000_000)
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]
    cadence = result["cadence"][result["episodes"][0]]
    assert cadence["expected_connected"] == 299
    assert cadence["observed_valid"] == 299
    assert cadence["longest_unexplained_gap_s"] == 0


def test_duplicate_fill_revision_counts_economics_once(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch, duplicate_fill=True)
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]
    assert all(row["first_qty"] == "2" for row in rows(path, "states.jsonl"))


def test_independent_yes_delta_no_rest_and_historical_tick(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch, mutations=True)
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]
    states = rows(path, "states.jsonl")
    assert states[0]["tick_size"] == "0.01"
    periodic = next(row for row in states if row["sample_kind"] == "PERIODIC")
    assert periodic["tick_size"] == "0.02"
    assert periodic["yes_book"]["best_bid"] == "0.61"
    assert periodic["no_book"]["best_bid"] == "0.37"
    states[0]["tick_size"] = "0.02"
    replace_rows(path, "states.jsonl", states)
    assert "tick_reconstruction" in gates(audit_session(path))


def test_cancel_true_live_status_is_terminal(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch, terminal_cancel=True)
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]
    assert all(not state["working_orders"] for state in rows(path, "states.jsonl")
               if state["available_at_us"] >= 101_000_004)


def test_early_censor_excludes_later_scheduled_slots(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch, end_us=104_000_000, stop_us=102_000_000)
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]
    assert result["episodes"] == []
    cadence = next(iter(result["cadence"].values()))
    assert cadence["excluded_censored"] == 2


@pytest.mark.parametrize("change", [
    lambda row: row.update(sample_kind="ORDER"),
    lambda row: row.update(sample_kind="PERIODIC", scheduled_at_us=row["available_at_us"]),
    lambda row: row.update(available_at_us=row["available_at_us"] - 1),
])
def test_terminal_decision_requires_immediate_material_snapshot(tmp_path, monkeypatch, change):
    path = session(tmp_path, monkeypatch)
    assert audit_session(path)["integrity"] == "PASS"
    events = rows(path, "events.jsonl")
    terminal_seq = next(event["event_seq"] for event in events
                        if event["kind"] == "BASELINE_DECISION" and
                        event["payload"].get("strategy_state") == "COMPLETE")
    states = rows(path, "states.jsonl")
    target = next(row for row in states if row["knowledge_seq"] == terminal_seq and
                  row["sample_kind"] == "SAFETY")
    change(target)
    replace_rows(path, "states.jsonl", states)
    result = audit_session(path)
    assert result["integrity"] == "FAIL"
    assert "terminal_decision_material_snapshot" in gates(result)


def test_censored_without_terminal_decision_needs_no_terminal_snapshot(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch, stop_us=102_000_000, terminal_decision=False)
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]
    assert "terminal_decision_material_snapshot" not in gates(result)


@pytest.mark.parametrize("terminal_state", ["COMPLETE", "HALTED", "UNPAIRABLE_RESIDUAL"])
def test_terminal_variants_reconstruct_from_event_prefix(tmp_path, monkeypatch, terminal_state):
    path = session(tmp_path, monkeypatch, terminal_state=terminal_state)
    result = audit_session(path)
    assert result["integrity"] == "PASS", result["issues"]


@pytest.mark.parametrize(("terminal_state", "stale_state"), [
    ("COMPLETE", "UNKNOWN"), ("COMPLETE", "RUNNER"),
    ("COMPLETE", "HALTED"), ("HALTED", "COMPLETE"),
    ("UNPAIRABLE_RESIDUAL", "PAIR_STOP"),
])
def test_terminal_material_state_must_match_event_prefix(
        tmp_path, monkeypatch, terminal_state, stale_state):
    path = session(tmp_path, monkeypatch, terminal_state=terminal_state)
    events = rows(path, "events.jsonl")
    decision = next(row for row in events if row["kind"] == "BASELINE_DECISION" and
                    row["payload"].get("strategy_state") == terminal_state)
    states = rows(path, "states.jsonl")
    material = next(row for row in states if row["sample_kind"] == "SAFETY" and
                    row["knowledge_seq"] == decision["event_seq"])
    assert material["available_at_us"] == decision["available_at_us"]
    material["baseline_state"] = stale_state
    replace_rows(path, "states.jsonl", states)
    result = audit_session(path)
    assert result["integrity"] == "FAIL"
    assert "terminal_decision_material_snapshot" in gates(result)


@pytest.mark.parametrize(("field", "wrong"), [
    ("baseline_pair_trigger", "TIME_FORCED"),
    ("baseline_peak_quoted_pnl_usd", "1"),
    ("baseline_quote", {"token_id": "no", "side": "BUY", "price": "0.4", "qty": "2"}),
])
def test_terminal_material_decision_fields_match_prefix(tmp_path, monkeypatch, field, wrong):
    path = session(tmp_path, monkeypatch)
    decision = next(row for row in rows(path, "events.jsonl")
                    if row["kind"] == "BASELINE_DECISION")
    states = rows(path, "states.jsonl")
    material = next(row for row in states if row["sample_kind"] == "SAFETY" and
                    row["knowledge_seq"] == decision["event_seq"])
    material[field] = wrong
    replace_rows(path, "states.jsonl", states)
    assert "terminal_decision_material_snapshot" in gates(audit_session(path))


def test_later_terminal_decision_does_not_backfill_earlier_snapshot(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    states = rows(path, "states.jsonl")
    first_fill = next(row for row in states if row["sample_kind"] == "FILL")
    assert first_fill["baseline_state"] == "UNKNOWN"
    first_fill["baseline_state"] = "COMPLETE"
    replace_rows(path, "states.jsonl", states)
    result = audit_session(path)
    assert result["integrity"] == "FAIL"
    assert "baseline_reconstruction" in gates(result)


@pytest.mark.parametrize(("valid", "expected", "passes"), [
    (9899, 10000, False), (9900, 10000, True), (9901, 10000, True),
])
def test_exact_99_percent_boundary(valid, expected, passes):
    assert _coverage_passes(valid, expected) is passes


@pytest.mark.parametrize(("missing_slots", "expected_integrity"), [
    ((102_000_000,), "PASS"),
    ((102_000_000, 104_000_000), "FAIL"),
])
def test_99_percent_boundary_through_writer_artifacts(
        tmp_path, monkeypatch, missing_slots, expected_integrity):
    path = session(tmp_path, monkeypatch, end_us=201_000_000)
    states = [row for row in rows(path, "states.jsonl")
              if row.get("scheduled_at_us") not in missing_slots]
    for number, row in enumerate(states, 1):
        row["snapshot_seq"] = number
        row["snapshot_id"] = row["record_id"] = (
            f"{row['episode_id']}:{row['session_id']}:{number}")
    replace_rows(path, "states.jsonl", states)
    removed = len(missing_slots)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["accepted_count"] -= removed
    manifest["persisted_count"] -= removed
    (path / "manifest.json").write_text(json.dumps(manifest) + "\n")
    quality = json.loads((path / "quality.json").read_text())
    quality["accepted"] -= removed
    quality["persisted"] -= removed
    (path / "quality.json").write_text(json.dumps(quality) + "\n")
    result = audit_session(path)
    cadence = next(iter(result["cadence"].values()))
    assert cadence["expected_connected"] == 100
    assert cadence["observed_valid"] == 100 - removed
    assert result["integrity"] == expected_integrity, result["issues"]
    assert ("cadence" in gates(result)) == (expected_integrity == "FAIL")


@pytest.mark.parametrize(("artifact", "change", "gate"), [
    ("events.jsonl", lambda rows: rows.pop(1), "event_seq"),
    ("events.jsonl", lambda rows: rows.__setitem__(5, {**rows[5], "available_at_us": 1}), "causal_clock"),
    ("states.jsonl", lambda rows: rows[0].update(knowledge_seq=999), "knowledge_seq"),
    ("states.jsonl", lambda rows: rows[0].update(first_qty="9"), "inventory"),
    ("states.jsonl", lambda rows: rows[0].update(tick_size="0.02"), "tick_reconstruction"),
    ("states.jsonl", lambda rows: rows[0]["yes_book"].update(best_bid="0.99"), "book_reconstruction"),
    ("states.jsonl", lambda rows: rows[0].update(working_orders=[]), "working_orders"),
    ("states.jsonl", lambda rows: rows[0]["features"].update(first_qty=9.0), "features"),
    ("episodes.jsonl", lambda rows: rows[0].update(revision=2), "episode_lifecycle"),
])
def test_corrupt_record_fails_relevant_gate(tmp_path, monkeypatch, artifact, change, gate):
    path = session(tmp_path, monkeypatch)
    data = rows(path, artifact)
    change(data)
    replace_rows(path, artifact, data)
    result = audit_session(path)
    assert result["integrity"] == "FAIL"
    assert gate in gates(result), result["issues"]


def test_missing_artifact_bad_json_secret_and_false_complete(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    (path / "events.jsonl").write_text("{bad\n")
    assert "schema" in gates(audit_session(path))
    path = session(tmp_path, monkeypatch, name="second")
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["effective_strategy_config"]["api_secret"] = "hidden"
    (path / "manifest.json").write_text(json.dumps(manifest))
    result = audit_session(path)
    assert "secrets" in gates(result)
    assert "hidden" not in json.dumps(result)
    path = session(tmp_path, monkeypatch, name="third")
    quality = json.loads((path / "quality.json").read_text())
    quality["status"] = "DEGRADED"
    (path / "quality.json").write_text(json.dumps(quality))
    assert "quality" in gates(audit_session(path))
    path = session(tmp_path, monkeypatch, name="fourth")
    (path / "episodes.jsonl").unlink()
    assert "artifacts" in gates(audit_session(path))
    path = session(tmp_path, monkeypatch, name="fifth")
    (path / "events.jsonl").write_text((path / "events.jsonl").read_text() + "\n")
    assert "artifacts" in gates(audit_session(path))


def test_cadence_gap_and_coverage(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch, end_us=111_000_000)
    data = rows(path, "states.jsonl")
    data[:] = [row for row in data if row["sample_kind"] != "PERIODIC" or
               row["scheduled_at_us"] not in (104_000_000, 105_000_000, 106_000_000)]
    replace_rows(path, "states.jsonl", data)
    result = audit_session(path)
    assert "snapshot_gap" in gates(result)
    assert "cadence" in gates(result)


def test_known_gap_is_not_silence(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    data = rows(path, "events.jsonl")
    data[0].update(kind="GAP", payload={"lost_start_seq": 1, "lost_end_seq": 1,
                                       "count": 1, "reason": "QUEUE_CAPACITY"})
    replace_rows(path, "events.jsonl", data)
    result = audit_session(path)
    assert "eligibility" in gates(result)
    assert result["integrity"] == "FAIL"


def test_proven_source_reference_hash_and_bounds(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    source = tmp_path / "closed-journal.jsonl"
    source.write_bytes(b"0123456789")
    events = rows(path, "events.jsonl")
    events[0]["source_ref"] = {"path": str(source),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "offset_bytes": 2, "length_bytes": 4}
    replace_rows(path, "events.jsonl", events)
    assert audit_session(path)["integrity"] == "PASS"
    events[0]["source_ref"]["length_bytes"] = 99
    replace_rows(path, "events.jsonl", events)
    assert "source_ref" in gates(audit_session(path))


def test_real_provenance_and_timing_gates(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    clean = audit_session(path)
    fake = audit_collection([path], real_sessions={"session": {
        "capture_mode": "PAPER", "manifest_sha256": clean["manifest_sha256"]}})
    assert fake["eligible_episode_count"] == 0
    assert fake["excluded_episode_reasons"]["unverified_real_provenance"] == 1
    attested = audit_collection([path], real_sessions={"session": {
        "capture_mode": "LIVE", "manifest_sha256": clean["manifest_sha256"]}},
        timing_evidence={"controlled_on_off": True,
                         "peak_traffic": True,
                         "code_commits": ["0" * 40],
                         "config_sha256": [clean["config_sha256"]],
                         "callback_ms": [0.1, 0.2, 0.3],
                         "baseline_loop_lag_ms": [0, 0, 0],
                         "on_loop_lag_ms": [1, 2, 3],
                         "off_decisions_and_intents": [{"decision": "hold"}],
                         "on_decisions_and_intents": [{"decision": "hold"}],
                         "fault_decisions_and_intents": [{"decision": "hold"}]})
    assert attested["eligible_episode_count"] == 0
    assert attested["candidate_episode_count"] == 1
    assert attested["live_attestation"]["status"] == "UNESTABLISHED"
    assert attested["collection"] == "UNESTABLISHED"
    assert attested["timing"]["status"] == "UNESTABLISHED"
    assert attested["isolation"]["status"] == "UNESTABLISHED"
    assert attested["caller_assertions"]["accepted_for_gates"] is False
    assert attested["overall"] == "INCOMPLETE"
    slow = audit_collection([path], timing_evidence={"controlled_on_off": True,
        "peak_traffic": True,
        "code_commits": ["0" * 40], "config_sha256": [clean["config_sha256"]],
        "callback_ms": [1.0], "baseline_loop_lag_ms": [0],
        "on_loop_lag_ms": [5.1]})
    assert slow["timing"]["status"] == "UNESTABLISHED"
    assert slow["overall"] == "INCOMPLETE"


def test_episode_censor_and_duplicate_economic_fill(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    revisions = rows(path, "episodes.jsonl")
    revisions[-1]["capture_status"] = "CENSORED"
    revisions[-1]["quality_reasons"] = ["PROCESS_SHUTDOWN"]
    replace_rows(path, "episodes.jsonl", revisions)
    result = audit_session(path)
    assert not result["episodes"]
    path = session(tmp_path, monkeypatch, name="duplicate")
    events = rows(path, "events.jsonl")
    fill = next(row for row in events if row["kind"] == "FILL_REVISION")
    fill["payload"]["qty"] = 4
    replace_rows(path, "events.jsonl", events)
    assert "inventory" in gates(audit_session(path))


def test_collection_thresholds_and_duplicate_ids(monkeypatch, tmp_path):
    def fake(path: Path) -> dict:
        number = int(path.name)
        return {"path": str(path), "integrity": "PASS", "issues": [],
                "dataset_id": "m4a", "session_id": path.name,
                "code_commit": "0" * 40, "config_sha256": "config",
                "manifest_sha256": path.name, "episodes": [f"episode-{number}"],
                "excluded_episodes": 0, "market_days": [f"2026-10-{1 + number // 7:02d}"]}

    monkeypatch.setattr("polymaker.research.audit.audit_session", fake)
    paths = [tmp_path / str(number) for number in range(20)]
    attestations = {str(number): {"capture_mode": "LIVE", "manifest_sha256": str(number)}
                    for number in range(20)}
    timing = {"controlled_on_off": True, "peak_traffic": True,
              "code_commits": ["0" * 40], "config_sha256": ["config"],
              "callback_ms": [.1] * 20, "baseline_loop_lag_ms": [0] * 20,
              "on_loop_lag_ms": [1] * 20,
              "off_decisions_and_intents": [{"decision": "hold"}],
              "on_decisions_and_intents": [{"decision": "hold"}],
              "fault_decisions_and_intents": [{"decision": "hold"}]}
    result = audit_collection(paths, real_sessions=attestations, timing_evidence=timing)
    assert result["eligible_episode_count"] == 0
    assert result["candidate_episode_count"] == 20
    assert result["candidate_market_day_count"] == 3
    assert result["candidate_collection_threshold_met"] is True
    assert result["collection"] == "UNESTABLISHED"
    assert result["timing"]["status"] == "UNESTABLISHED"
    assert result["isolation"]["status"] == "UNESTABLISHED"
    assert result["overall"] == "INCOMPLETE"
    fewer = audit_collection(paths[:19], real_sessions=attestations, timing_evidence=timing)
    assert fewer["candidate_collection_threshold_met"] is False
    def same_day(path: Path) -> dict:
        value = fake(path)
        value["market_days"] = ["2026-10-01"]
        return value

    monkeypatch.setattr("polymaker.research.audit.audit_session", same_day)
    one_day = audit_collection(paths, real_sessions=attestations, timing_evidence=timing)
    assert one_day["candidate_episode_count"] == 20
    assert one_day["candidate_collection_threshold_met"] is False


def test_cli_audits_dataset_and_distinguishes_collection_exit(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    (path.parent / "unrelated.txt").write_text("not a session")
    (path.parent / "unrelated").mkdir()
    (path.parent / "unrelated" / "notes.txt").write_text("not a session")
    output = tmp_path / "derived"
    response = CliRunner().invoke(app, ["research-audit", str(path.parent),
                                       "--output-dir", str(output)])
    assert response.exit_code == 2, response.output
    report = output / tmp_path.name / "m4a" / "audit.json"
    assert json.loads(report.read_text())["integrity"] == "PASS"
    assert json.loads(report.read_text())["candidate_session_count"] == 1
    assert (path / "manifest.json").is_file()


def test_cli_caller_claim_files_cannot_establish_readiness(tmp_path, monkeypatch):
    path = session(tmp_path, monkeypatch)
    clean = audit_session(path)
    real = tmp_path / "claimed-live.json"
    real.write_text(json.dumps({path.name: {"capture_mode": "LIVE",
        "manifest_sha256": clean["manifest_sha256"]}}))
    timing = tmp_path / "claimed-timing.json"
    timing.write_text(json.dumps({"controlled_on_off": True, "peak_traffic": True,
        "callback_ms": [.1], "baseline_loop_lag_ms": [0], "on_loop_lag_ms": [1],
        "off_decisions_and_intents": ["hold"],
        "on_decisions_and_intents": ["hold"],
        "fault_decisions_and_intents": ["hold"]}))
    output = tmp_path / "derived"
    response = CliRunner().invoke(app, ["research-audit", str(path.parent),
        "--output-dir", str(output), "--real-sessions-file", str(real),
        "--timing-file", str(timing)])
    assert response.exit_code == 2, response.output
    result = json.loads((output / tmp_path.name / "m4a" / "audit.json").read_text())
    assert result["collection"] == "UNESTABLISHED"
    assert result["timing"]["status"] == "UNESTABLISHED"
    assert result["isolation"]["status"] == "UNESTABLISHED"
    assert result["eligible_episode_count"] == 0
    assert result["overall"] == "INCOMPLETE"


@pytest.mark.parametrize("manifest_change", [
    "missing", "empty", "malformed", "unsupported", "identity",
])
def test_cli_dataset_discovery_fails_closed_on_bad_session_manifest(
        tmp_path, monkeypatch, manifest_change):
    valid = session(tmp_path, monkeypatch)
    bad = valid.parent / "broken-session"
    bad.mkdir()
    (bad / "quality.json").write_text("{}")
    if manifest_change == "empty":
        (bad / "manifest.json").write_text("")
    elif manifest_change == "malformed":
        (bad / "manifest.json").write_text("{broken")
    elif manifest_change in ("unsupported", "identity"):
        manifest = json.loads((valid / "manifest.json").read_text())
        manifest["session_id"] = bad.name if manifest_change == "unsupported" else "wrong-id"
        if manifest_change == "unsupported":
            manifest["schema_version"] = 2
        (bad / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "derived"
    response = CliRunner().invoke(app, ["research-audit", str(valid.parent),
                                        "--output-dir", str(output)])
    assert response.exit_code == 1, response.output
    report = json.loads((output / tmp_path.name / "m4a" / "audit.json").read_text())
    assert report["integrity"] == "FAIL"
    assert report["candidate_session_count"] == 2
    assert report["integrity_clean_session_count"] == 1
    assert report["collection_ineligible_session_count"] == 2
    assert any(row["path"].endswith("broken-session") and row["integrity"] == "FAIL"
               for row in report["sessions"])
    assert "broken-session" in (output / tmp_path.name / "m4a" / "audit_report.md").read_text()
    if manifest_change in ("missing", "empty", "malformed"):
        assert report["pre_manifest_failure_count"] == 1
