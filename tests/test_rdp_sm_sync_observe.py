"""Standalone sampling artifacts, failure visibility and observer semantics."""

import json
import threading

import httpx
import pytest

from polymaker.rdp_sm_sync import SyncSettings
from polymaker.rdp_sm_sync_observe import ObserverStats, main, run_observation
from test_rdp_sm_sync import make_service, trade, wait_rounds
from test_rdp_sm_sync import sync_env as sync_env


def test_confirmed_is_observed_once_for_all_account_legs_without_trading(sync_env):
    venue, _, _, _ = sync_env
    venue.trades = [trade(status="MATCHED")]
    service, _ = make_service(sync_env)
    service.start()
    try:
        first = wait_rounds(service)
        assert any(e["kind"] == "sync_trade_observed" and e["status"] == "MATCHED" for e in first)
        venue.trades = [trade(status="CONFIRMED")]
        second = wait_rounds(service, count=2)
        confirmed = [e for e in second if e["kind"] == "sync_trade_observed"
                     and e["status"] == "CONFIRMED"]
        assert len(confirmed) == 1
        assert confirmed[0]["observed_monotonic"] <= confirmed[0]["applied_monotonic"]
        assert not service.latest().to_dict()["trading_authorized"]
        assert service.latest().pending_ids  # Still LIVE: CONFIRMED is not a close proof.
    finally:
        assert service.stop()


def test_standalone_measurement_reports_requests_age_and_resource_usage(sync_env, tmp_path):
    service, _ = make_service(sync_env)
    report = run_observation(service, .22, tmp_path / "observer")
    assert report["status"] == "COMPLETED"
    assert report["io_drained"] and report["dropped_events"] == 0
    assert report["cpu_seconds"] >= 0 and report["process_peak_rss_kib"] > 0
    assert report["metrics"]["round_counts"]["orders"]["complete"] > 0
    assert report["metrics"]["actual_start_interval_ms"]["orders"]["n"] > 0
    assert report["metrics"]["source_age_at_publication_ms"]["trades"]["n"] > 0
    from pathlib import Path
    directory = Path(report["directory"])
    assert {p.name for p in directory.iterdir()} == {"events.jsonl", "summary.json", "manifest.json"}
    all_text = "".join(p.read_text() for p in directory.iterdir())
    for secret in ("dummy-key", "c2FmZS10ZXN0", "dummy-pass", "must-not-be-exported"):
        assert secret not in all_text
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    assert all(e["method"] == "GET" for e in events if e["kind"] == "sync_http")
    assert all(e["trading_authorized"] is False for e in events if e["kind"] == "sync_snapshot")


def test_startup_failure_is_visible_and_drained(sync_env, tmp_path):
    venue, _, _, _ = sync_env
    venue.hook = lambda r: httpx.Response(401, json={"secret": "hidden"}) if (
        r.url.path == "/auth/derive-api-key") else None
    service, readers = make_service(sync_env)
    report = run_observation(service, .2, tmp_path / "failed")
    assert report["status"] == "FAILED"
    assert report["fatal_type"] == "HTTPStatusError"
    assert readers[0].http.is_closed
    assert report["metrics"]["http_total"] == 1
    assert "hidden" not in json.dumps(report)


def test_early_external_stop_marks_incomplete_measurement(sync_env, tmp_path):
    service, _ = make_service(sync_env)
    stop = threading.Event()
    stop.set()
    report = run_observation(service, 3600, tmp_path / "interrupted", stop=stop)
    assert report["status"] in {"INTERRUPTED", "FAILED"}
    assert report["actual_duration_s"] < 3
    assert report["io_drained"]


def test_stats_preserve_old_source_age_on_failure():
    stats = ObserverStats()
    def event(now, complete):
        return {"kind": "sync_snapshot", "published_monotonic": now,
                "attempt_updates": {"trades": {"complete": complete, "started_monotonic": now - .1,
                                                "error_type": None if complete else "ReadTimeout"}}}
    stats.observe(event(1, True))
    stats.observe(event(5, False))
    report = stats.to_dict()
    assert report["source_age_at_publication_ms"]["trades"]["max"] > 4000
    assert report["source_failures"] == [{"source": "trades", "error_type": "ReadTimeout", "count": 1}]


@pytest.mark.parametrize("value", ["nan", "-1", "0", "inf"])
def test_invalid_duration_does_not_open_account(value, capsys):
    assert main(["--duration-seconds", value]) == 2
    assert json.loads(capsys.readouterr().out)["error_type"] == "ValueError"


def test_invalid_settings():
    for args in ((float("nan"), 2), (0, 2), (1, -1)):
        with pytest.raises(ValueError):
            SyncSettings(*args)
