import json
from pathlib import Path
from types import SimpleNamespace

import m111_hourly_watch as watch
import pytest


def metric(strategy="M1", submitted=3):
    scenario = "trades-only" if strategy == "M1" else "main/trades-only"
    profiles = {
        f"{strategy}/{strategy}-virtual/{scenario}": {
            "submitted_orders": submitted,
            "filled_orders": 1,
            "unresolved_orders": 1,
        }
    }
    return {
        "status": "OK",
        "counted_markets": 1,
        "observed_at": 100,
        "modes": {
            "main": {"strategies": {strategy: {"signals": 2}}, "by_profile_scenario": profiles}
        },
    }


def ledger():
    return {
        "status": "OK",
        "counted_markets": 1,
        "observed_at": 105,
        "strategies": {"M2": {"signals": 4, "assumed_filled_orders": 2}},
        "execution_comparison": {
            "M2": {
                "main/trades-only": {
                    "submitted_orders": 2,
                    "filled_orders": 1,
                    "unresolved_orders": 1,
                },
                "main/cancel-half": {
                    "submitted_orders": 2,
                    "filled_orders": 2,
                    "unresolved_orders": 0,
                },
            }
        },
    }


def test_uses_same_watermark_and_does_not_mix_main_and_retained_baseline():
    row = watch.make_row("M2", metric("M2", 9), ledger())
    assert row["signals"] == 4
    assert row["execution"]["trades-only"]["submitted_orders"] == 2
    assert row["execution"]["cancel-half"]["filled_orders"] == 2
    assert row["assumed_filled_orders"] == 2
    assert row["observed_at"] == 105


def test_m1_profiles_are_explicit_and_signals_not_multiplied():
    m = metric()
    m["modes"]["main"]["by_profile_scenario"]["M1/M1-live-mirror/trades-only"] = {
        "submitted_orders": 2,
        "filled_orders": 2,
        "unresolved_orders": 0,
    }
    row = watch.make_row("M1", m, {})
    assert row["signals"] == 2 and row["assumed_filled_orders"] == 5
    assert row["execution"]["trades-only"]["filled_orders"] == 3
    assert "NOT_SEPARATE_LEDGER" in row["assumed_basis"]
    split = watch.make_row("M1", m, {}, "M1-live-mirror")
    assert split["execution"]["trades-only"]["submitted_orders"] == 2


def test_missing_metrics_are_not_zero():
    row = watch.make_row("M1", {"status": "UNAVAILABLE"}, {})
    assert row["signals"] is None and row["assumed_filled_orders"] is None
    assert row["execution"]["trades-only"]["filled_orders"] is None


def test_inspection_during_source_switch_leaves_research_metrics_and_lag_missing(
    tmp_path, monkeypatch
):
    base = tmp_path / "research_outputs"
    source, follower = base / "main", base / "old-follower"
    (tmp_path / "src/polymaker").mkdir(parents=True)
    plan = {"mode": "SIMULATION_ONLY", "real_order_transport": "ABSENT"}
    watch.atomic_json(source / "plan.json", plan)
    watch.atomic_json(follower / "plan.json", plan)
    watch.atomic_json(
        follower / "status.json",
        {
            "observed_at": 1,
            "active": {
                "btc-updown-5m-0": {
                    "source_wall": 1,
                    "source_bytes_read": 0,
                    "source_bytes_available": 100,
                }
            },
        },
    )
    watch.atomic_json(
        base / "m1234_sim/monitor/control.json",
        {
            "run_directory": str(source),
            "unit": "main",
            "metrics_script": "metrics.py",
            "signal_validation": {"directory": str(base / "main-ledger"), "unit": "ledger"},
        },
    )
    watch.atomic_json(
        base / "m511_sim/current.json",
        {
            "directory": str(follower),
            "source_run": str(base / "old-main"),
            "source_root": str(base / "old-code"),
            "unit": "follower",
            "assumed_fill_unit": "follow-ledger",
        },
    )
    queried = []

    def execution_counts(run, *_):
        queried.append(run)
        return metric()

    monkeypatch.setattr(watch, "execution_counts", execution_counts)
    monkeypatch.setattr(watch, "assumed_report", lambda *_: {})
    monkeypatch.setattr(watch, "service", lambda _: {"ActiveState": "active"})
    report = watch.inspect(tmp_path / "monitor", project=tmp_path)
    assert queried == [source]
    assert "RESEARCH_FOLLOWS_DIFFERENT_SOURCE_RUN" in report["problems"]
    assert report["following_lag_seconds"] is None
    research_rows = [row for row in report["rows"] if row["strategy"] in watch.RESEARCH]
    assert all(
        row["signals"] is None and row["data_status"] == "UNAVAILABLE" for row in research_rows
    )


def test_all_excluded_research_samples_are_not_zero():
    m = {
        "status": "PARTIAL",
        "counted_markets": 2,
        "modes": {"research": {"strategies": {"M5": {"signals": 0}}, "by_profile_scenario": {}}},
    }
    row = watch.make_row(
        "M5",
        m,
        {"status": "PARTIAL", "counted_markets": 0, "excluded_research_samples": [{"market": "x"}]},
    )
    assert row["signals"] is None and row["execution"]["trades-only"]["filled_orders"] is None
    assert row["data_status"] == "NO_ELIGIBLE_SAMPLES"


@pytest.mark.parametrize(
    "change", [{"observed_at": 1}, {"source_identity": "wrong"}, {"source_scenario": "trades-only"}]
)
def test_stale_or_cross_run_ledger_is_rejected(tmp_path, change):
    source, output = tmp_path / "source", tmp_path / "ledger"
    source.mkdir()
    output.mkdir()
    (source / "plan.json").write_text(json.dumps({"run_identity": "run"}))
    data = {
        "source_run": str(source),
        "source_identity": "run",
        "source_scenario": "main/trades-only",
        "evidence": "ASSUMED_FILL",
        "observed_at": 299,
    }
    data.update(change)
    (output / "latest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        watch.assumed_report(output, source, "main/trades-only", 300)


@pytest.mark.parametrize(
    ("scenario", "terminal"),
    [
        ("main/trades-only", {"status": "PASS", "reason": "PLANNED_COMPLETE"}),
        ("research/trades-only", {"status": "SOURCE_COMPLETED", "unfinished_markets": []}),
    ],
)
def test_planned_final_ledger_does_not_expire(tmp_path, scenario, terminal):
    source, output = tmp_path / "source", tmp_path / "ledger"
    watch.atomic_json(source / "plan.json", {"run_identity": "run"})
    watch.atomic_json(
        output / "latest.json",
        {
            "source_run": str(source),
            "source_identity": "run",
            "source_scenario": scenario,
            "source_terminal": {key: terminal.get(key) for key in ("status", "reason")},
            "evidence": "ASSUMED_FILL",
            "observed_at": 1,
        },
    )
    watch.atomic_json(source / "completion.json", {**terminal, "run_identity": "run"})
    assert watch.assumed_report(output, source, scenario, 300)["final_snapshot"] is True
    with pytest.raises(ValueError, match="ASSUMED_FILL_REPORT_STALE"):
        watch.assumed_report(output, source, scenario, 0)
    watch.atomic_json(source / "completion.json", {**terminal, "run_identity": "another-run"})
    with pytest.raises(ValueError, match="ASSUMED_FILL_REPORT_STALE"):
        watch.assumed_report(output, source, scenario, 300)


def test_final_ledger_requires_matching_execution_watermarks():
    source = {"market": "market", "database_id": "database", "audit_seq": 12, "input_seq": 30}
    final = {"sources": [source]}
    watch.verify_final_watermarks(final, {"status": "OK", "sources": [dict(source)]})
    with pytest.raises(ValueError, match="FINAL_ASSUMED_FILL_WATERMARK_MISMATCH"):
        watch.verify_final_watermarks(final, {"status": "OK", "sources": [dict(source, audit_seq=13)]})


def test_queue_existing_thread_once_per_hour_even_if_resume_warns(tmp_path, monkeypatch):
    (tmp_path / "prompt.txt").write_text("Authorized inspection")
    monkeypatch.setattr(watch, "render", lambda report: "report")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert argv[1:4] == ["queue", "--thread", "existing-thread"]
        assert argv[-1] == "--resume"
        return SimpleNamespace(returncode=0, stdout="", stderr="warning: resume not confirmed")

    control = {"thread": "existing-thread"}
    report = {"bucket": 42}
    assert watch.wake(control, report, tmp_path, runner)["status"] == "QUEUED_RESUME_UNCONFIRMED"
    assert watch.wake(control, report, tmp_path, runner)["status"] == "ALREADY_QUEUED"
    assert len(calls) == 1


def test_failed_queue_is_retryable_without_advancing_dedup(tmp_path, monkeypatch):
    (tmp_path / "prompt.txt").write_text("Authorized inspection")
    monkeypatch.setattr(watch, "render", lambda report: "report")

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    assert (
        watch.wake({"thread": "existing"}, {"bucket": 1}, tmp_path, runner)["status"]
        == "WAKE_FAILED"
    )
    assert not (tmp_path / "agent-state.json").exists()


def test_timer_is_halfway_between_live_five_minute_slots():
    timer = (Path(watch.__file__).parent / "polymaker-m111-hourly-watch.timer").read_text()
    assert "OnCalendar=*-*-* *:02:50 Asia/Shanghai" in timer
    assert "Persistent=false" in timer
    assert (watch.OFFSET - 20) % 300 == 150


def test_m11_is_not_reported_as_an_independent_direction():
    assert "M11" not in watch.RESEARCH
    assert {"M4+M11", "M9+M11", "M10+M11"} <= set(watch.RESEARCH)


def test_planned_completion_or_manual_stop_is_not_service_failure():
    assert watch.expected_stop("M1_M4", {"reason": "PLANNED_COMPLETE"}, None)
    assert watch.expected_stop("M234_ASSUMED", {"reason": "MANUAL_STOP"}, None)
    assert watch.expected_stop("M5_M11", None, {"status": "STOPPED_BY_OPERATOR"})
    assert watch.expected_stop("M511_ASSUMED", None, {"status": "SOURCE_COMPLETED"})
    assert not watch.expected_stop("M5_M11", {"reason": "PLANNED_COMPLETE"}, None)
    assert not watch.expected_stop("M1_M4", {"reason": "FAIL"}, None)


def test_ended_market_caught_up_waiting_for_tail_is_not_processing_backlog():
    market = "btc-updown-5m-1000"
    follow = {
        "active": {
            market: {
                "processed_input_seq": 100,
                "source_wall": 1300,
                "source_bytes_read": 500,
                "source_bytes_available": 500,
            }
        }
    }
    source = {"sources": [{"market": market, "input_seq": 100}]}
    progress, lag = watch.following_progress(follow, source, 1800)
    assert lag == 0
    assert progress[market]["stage"] == "AT_COMMITTED_WATERMARK_WAITING_SOURCE_TAIL"
    assert progress[market]["last_event_age_seconds"] == 500


def test_buffered_input_still_counts_as_backlog_after_all_bytes_read():
    market = "btc-updown-5m-1000"
    follow = {
        "active": {
            market: {
                "processed_input_seq": 100,
                "source_wall": 1300,
                "source_bytes_read": 500,
                "source_bytes_available": 500,
            }
        }
    }
    source = {"sources": [{"market": market, "input_seq": 200}]}
    progress, lag = watch.following_progress(follow, source, 1800)
    assert lag == 500
    assert progress[market]["stage"] == "PROCESSING_RECORDED_INPUT"


def test_unflushed_source_tail_is_not_follower_processing_lag(tmp_path):
    import gzip

    market = "btc-updown-5m-1000"
    directory = tmp_path / market
    directory.mkdir()
    tape = directory / "input.jsonl.gz"
    with gzip.open(tape, "wt") as writer:
        for seq in range(1, 129):
            writer.write(json.dumps({"seq": seq}) + "\n")
        writer.flush()
        # SQLite may already expose this next event while the recorder buffers it.
        writer.write(json.dumps({"seq": 129}) + "\n")
        assert watch.published_input_seq(tape) == 128
        follow = {
            "active": {
                market: {
                    "processed_input_seq": 128,
                    "source_wall": 1300,
                    "source_bytes_read": tape.stat().st_size,
                    "source_bytes_available": tape.stat().st_size,
                }
            }
        }
        source = {"sources": [{"market": market, "input_seq": 129}]}
        progress, lag = watch.following_progress(follow, source, 1800, tmp_path)
        assert lag == 0
        assert progress[market]["stage"] == "WAITING_RECORDER_FLUSH_OR_SOURCE_TAIL"
