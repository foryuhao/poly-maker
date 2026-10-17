"""Main-owned lifecycle and immutable, timestamp-correct snapshot receipts."""

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from polymaker.rdp_sm_sync import SyncObserver, SyncSettings
from polymaker.rdp_sm_sync_companion import SyncCompanion, close_companion, slim_reference
from polymaker.rdp_sm_sync_reads import ObservedMarket, ObserverReader
from test_rdp_sm_m1 import CID, SLUG, START
from test_rdp_sm_sync import sync_env as sync_env
from test_rdp_sm_sync import wait_rounds


def reader_factory(venue, cfg):
    return lambda emit, stop: ObserverReader(cfg, emit=emit, stop=stop,
                                             transport=httpx.MockTransport(venue.response))


def metadata():
    return SimpleNamespace(slug=SLUG, condition_id=CID,
                           yes=SimpleNamespace(token_id="1"), no=SimpleNamespace(token_id="2"))


def test_pinned_service_waits_for_context_never_discovers_or_switches(sync_env):
    venue, cfg, _, _ = sync_env
    service = SyncObserver(reader_factory(venue, cfg), SyncSettings(.03, .05),
                           follow_main=True, session_id="main-session")
    service.start()
    try:
        deadline = time.monotonic() + 3
        while service.latest().version == 0 and time.monotonic() < deadline:
            service.wait_after(0, .02)
        assert service.latest().status == "AWAITING_MAIN_MARKET"
        assert not any(path in {"/data/orders", "/data/trades", "/markets", "/v2/positions"}
                       for path, _ in venue.calls)
        target = ObservedMarket(SLUG, CID, START, ("1", "2"))
        service.bind_market(target)
        wait_rounds(service)
        earlier = service.latest()
        venue.time = START + 305
        wait_rounds(service, count=3)
        latest = service.latest()
        assert json.loads(latest.market_json)["start"] == START
        assert latest.session_id == "main-session"
        assert not any(path == "/markets" for path, _ in venue.calls)
        assert service.snapshot_at(earlier.published_monotonic) is earlier
        assert service.snapshot_at(earlier.published_monotonic - 1000) is None
        with pytest.raises(ValueError, match="ALREADY_BOUND"):
            service.bind_market(ObservedMarket("other", "other", START + 300, ("3", "4")))
    finally:
        assert service.stop()


def test_attached_snapshots_and_asof_references_are_persisted_and_drained(sync_env, tmp_path):
    venue, cfg, _, _ = sync_env
    cfg.rdp_sm.sync_observe.output_dir = str(tmp_path / "attached")
    cfg.rdp_sm.sync_observe.order_interval_s = .03
    cfg.rdp_sm.sync_observe.account_interval_s = .05
    companion = SyncCompanion(cfg, "session-a", reader_factory=reader_factory(venue, cfg))
    companion.start()
    try:
        companion.bind_market(metadata(), START)
        wait_rounds(companion.service, count=2)
        previous = companion.service.latest()
        companion.capture("sm_obi_final_decision", {
            "intent": {"window_id": "session-a:w1", "phase": "NORMAL",
                       "post_started": previous.published_monotonic - 1000,
                       "post_returned": previous.published_monotonic},
            "api_secret": "must-not-copy-this", "raw_response": {"private_key": "hidden"}},
            monotonic=previous.published_monotonic)
        wait_rounds(companion.service)
    finally:
        assert companion.close()
    directory = companion.directory
    snapshots = [json.loads(line) for line in (directory / "snapshots.jsonl").read_text().splitlines()]
    references = [json.loads(line) for line in (directory / "strategy_refs.jsonl").read_text().splitlines()]
    assert snapshots[-1]["status"] == "STOPPED"
    assert all(row["session_id"] == "session-a" and not row["trading_authorized"] for row in snapshots)
    assert all(row["market"] is None or row["market"]["condition_id"] == CID for row in snapshots)
    ref = references[0]
    assert ref["fields"]["window_id"] == "session-a:w1"
    assert ref["snapshot_links"]["post_started"]["snapshot_version"] is None
    assert ref["snapshot_links"]["post_returned"]["snapshot_version"] == previous.version
    assert ref["snapshot_links"]["recorded"]["snapshot_version"] == previous.version
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["io_drained"] and summary["status"] == "RECORDED"
    assert summary["main_market_snapshot_count"] > 0
    assert not companion._thread.is_alive() and not companion.service._thread.is_alive()
    text = "".join(p.read_text() for p in directory.iterdir())
    assert "must-not-copy-this" not in text and "hidden" not in text and "dummy-pass" not in text


def test_reference_whitelist_and_boolean_post_started():
    from polymaker.domain import TradeState

    row = slim_reference("sm_placement_result", {
        "expected_order_id": "0xorder", "post_started": True, "status": TradeState.CONFIRMED,
        "raw_response": {"api_secret": "private"}}, 5)
    assert row["fields"]["expected_order_id"] == "0xorder"
    assert "post_started" not in row["milestones"]
    assert "private" not in json.dumps(row)
    assert row["fields"]["status"] == "CONFIRMED"


def test_recorder_disk_failure_does_not_start_or_leak_reader(sync_env, tmp_path):
    venue, cfg, _, _ = sync_env
    root = tmp_path / "file"
    root.write_text("not a directory")
    cfg.rdp_sm.sync_observe.output_dir = str(root)
    companion = SyncCompanion(cfg, "failure", reader_factory=reader_factory(venue, cfg))
    before = len(venue.calls)
    companion.start()
    companion._thread.join(3)
    assert not companion.close()
    assert companion.failure and len(venue.calls) == before
    assert companion.service._thread is None


def test_separate_invocations_have_no_shared_snapshot_history(sync_env, tmp_path):
    venue, cfg, _, _ = sync_env
    cfg.rdp_sm.sync_observe.output_dir = str(tmp_path)
    first = SyncCompanion(cfg, "first", reader_factory=reader_factory(venue, cfg))
    first.start()
    first.bind_market(metadata(), START)
    try:
        wait_rounds(first.service)
        stamp = first.service.latest().published_monotonic
    finally:
        first.close()
    second = SyncCompanion(cfg, "second", reader_factory=reader_factory(venue, cfg))
    assert second.service.snapshot_at(stamp) is None
    assert second.directory != first.directory
    assert second.service.latest().attempts == ()


async def test_cancellation_still_drains_companion_cleanup():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    class Companion:
        def close(self):
            calls.append(True)
    # Delay only the test's to_thread handoff, not production I/O or lifecycle.
    from unittest.mock import patch
    async def delayed(fn):
        entered.set()
        await release.wait()
        return fn()
    with patch("polymaker.rdp_sm_sync_companion.asyncio.to_thread", delayed):
        task = asyncio.create_task(close_companion(Companion()))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == [True]
