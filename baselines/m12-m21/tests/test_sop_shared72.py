"""Offline proof of same-tape execution, sealing, handover and disk stops."""

import asyncio
import fcntl
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from baseline_v2 import SingleStrategyRun, main as baseline_v2_main, resume_session
from m122124_hourly_watch import collect
from m1223_pair_phase import PairRun, archive_resume, prepare_resume
from m1223_settlement_closeout import canonical_identity
from m1223_shared72 import (
    BASELINE_V2,
    MARKETS,
    MARKET_SECONDS,
    PREWARM_SECONDS,
    PUBLIC_ONLY_SAMPLE,
    SCHEMA,
    MarketCapture,
    MarketEngines,
    RecordedMarketEngines,
    SharedCaptureFeed,
    SharedRun,
    StrategyReplayTimeout,
    configuration,
    replay,
)
from m1223_shared_storage import (
    FREE_RESERVE,
    HANDOFF_FILE,
    M12_ONLY,
    PAIR,
    PHASE_DIRECTORY,
    STOP_HEADROOM,
    TAPE_RETENTION_SECONDS,
    Recorder,
    prune_expired_tapes,
    read_tape,
    storage_status,
)

from m1234_fixtures import fixture_events
from polymaker.m1234_sim.engine import market_meta
from polymaker.m1234_sim.events import Clock
from polymaker.m1234_sim.runner import CaptureQueue
from polymaker.m1234_sim.store import read_audit


@pytest.mark.asyncio
@pytest.mark.parametrize("group_class,participants,protocol", [
    (group, labels, protocol)
    for group in (MarketEngines, RecordedMarketEngines)
    for labels, protocol in ((("M12", "M21", "M24"), None), (PAIR, "v1"), (PAIR, "v2"))
] + [(MarketEngines, (strategy,), protocol)
     for strategy in ("M12", "M21", "M24") for protocol in ("v1", "v2")])
async def test_three_strategies_same_tape_anchor_entries_and_official_settlement(
    tmp_path, monkeypatch, group_class, participants, protocol,
):
    def forbidden(*args):
        pytest.fail("shared baseline accessed a local account journal")

    monkeypatch.setattr("m511_sample_quality.own_market_gate", forbidden)
    settings, parameters = configuration(participants)
    assert settings["min_free_disk_mib"] == 5 * 1024
    assert settings["max_markets"] == MARKETS == 2016
    assert settings["max_output_mib"] is None
    assert settings["tape_retention_seconds"] == 86400
    initial = fixture_events()[0]
    definition = {**initial["definition"], "settings": settings}
    if protocol:
        definition["market_protocol"] = protocol
    wall = initial["available_wall"]
    identity = definition["market"]
    strategy = participants[0]
    single = len(participants) == 1
    prewarm = 25 if single else 0
    if single:
        definition["official_clock"] = [wall - prewarm, 0.0, 0.0]
    run_directory = tmp_path / PHASE_DIRECTORY if participants == M12_ONLY else tmp_path
    run_directory.mkdir(exist_ok=True)
    directory = run_directory / identity["slug"]
    capture = None
    writer = None
    if single:
        assert settings["baseline_architecture"] == BASELINE_V2

        async def forbid_worker(*args, **kwargs):
            pytest.fail("single-strategy markets must seal without a replay worker")

        monkeypatch.setattr("m1223_shared72.asyncio.create_subprocess_exec", forbid_worker)
        monkeypatch.setattr("m1223_shared72.Recorder", forbidden)
        session = SharedRun(run_directory, tmp_path, settings, parameters, api=object())
        capture = MarketCapture(session, market_meta(identity), definition["ticks"])
        group = capture.group
        available = Clock()
        monkeypatch.setattr("m1223_shared72.time", available)
    else:
        group = group_class(directory, settings, parameters, {"mode": "OFFLINE_FIXTURE"})
        writer = Recorder(directory / "input.jsonl.zst", 8192)

    async def record(t, kind, **payload):
        if capture is not None:
            available.set(t + prewarm, wall + t)
            if kind == "FRAME":
                payload["received_mono"] += prewarm
            await capture.record(group.engines.get(strategy), kind, **payload)
        else:
            event = writer.append(kind, Clock(t, wall + t), **payload)
            await group.ingest(event)

    async def frame(t, *rows):
        raw = json.dumps(
            [
                {
                    **row,
                    "market": identity["condition_id"],
                    "timestamp": str(round((wall + t) * 1000)),
                }
                for row in rows
            ]
        )
        await record(t, "FRAME", raw=raw, received_mono=t, received_wall=wall + t)

    async def books(t):
        await frame(
            t,
            *(
                {
                    "event_type": "book",
                    "asset_id": token,
                    "bids": [{"price": ".50", "size": "100"}, {"price": ".49", "size": "100"}],
                    "asks": [{"price": ".52", "size": "100"}],
                }
                for token in ("1", "2")
            ),
        )

    async def bbo(t):
        await frame(
            t,
            *(
                {
                    "event_type": "best_bid_ask",
                    "asset_id": token,
                    "best_bid": ".50",
                    "best_ask": ".52",
                }
                for token in ("1", "2")
            ),
        )

    async def delta(t, bid=None, ask=None):
        await frame(
            t,
            {
                "event_type": "price_change",
                "price_changes": [
                    {"asset_id": "1", "side": side, "price": price, "size": q}
                    for side, price, q in (("BUY", ".50", bid), ("SELL", ".52", ask))
                    if q is not None
                ],
            },
        )

    async def trade(t, side="SELL", price=".50", qty="10"):
        await frame(
            t,
            {
                "event_type": "last_trade_price",
                "asset_id": "1",
                "side": side,
                "price": price,
                "size": qty,
                "trade_id": str(t),
            },
        )

    await record(-prewarm, "INIT", definition=definition)
    await record(-prewarm, "CONNECT")
    if single:
        await books(-prewarm)
    await books(0)
    await trade(0.01, qty="5")
    await delta(0.10, bid="90")
    for t in (0.15, 0.30, 0.45):
        await bbo(t)
    await delta(0.49, bid="96", ask="80")
    await bbo(0.505)
    await bbo(0.512)
    await record(0.513, "POLL")
    await trade(0.65, price=".49", qty="3")
    await delta(0.70, bid="93")
    await record(0.92, "POLL")

    await books(7)
    await trade(7.01)
    await delta(7.02, bid="99")
    await bbo(7.17)
    await delta(7.31, bid="99.8")
    await bbo(7.46)
    await bbo(7.505)
    await record(7.512, "TIMER")
    await bbo(7.70)
    await trade(7.81, side="BUY", price=".52")
    await delta(7.82, ask="80")
    await bbo(7.96)
    await delta(8.11, ask="84")
    await bbo(8.17)
    await bbo(8.305)
    await record(8.312, "POLL")
    await trade(8.45, price=".49", qty="2")

    await books(13.985)
    await trade(14, qty="20")
    await trade(14.115, side="BUY", price=".52", qty="20")
    for t in (14.17, 14.35, 14.48):
        await delta(t, bid="98", ask="94")
    await record(14.50, "POLL")
    await bbo(14.60)
    await trade(14.65, price=".49", qty="4")
    await trade(14.67, side="BUY", price=".52")
    await trade(14.70, price=".49", qty="2")
    await record(14.93, "POLL")
    if participants == M12_ONLY:
        while (capture.input_seq + 1) % 40:
            await record(14.94, "FRAME", raw="PONG", received_mono=14.94, received_wall=wall + 14.94)
        await record(14.94, "TIMER")
        (intent,) = [
            row["payload"]["intent"]
            for row in read_audit(directory / "M12/simulation.sqlite3")
            if row["kind"] == "INTENT" and row["payload"]["scenario"] == "sop/anchor"
        ]
        assert intent["strategy"] == intent["profile"] == "M12"
        assert intent["order_type"] == "ASSUMED_FIXED_ANCHOR"
        assert Decimal(intent["price"]) == Decimal(".50")
    await record(300, "END", reason="T300_OBSERVED", research_sample_eligibility=PUBLIC_ONLY_SAMPLE)
    if group_class is MarketEngines or capture is not None:
        for market, orders in group.report().values():
            assert market["pending_settlement_orders"] == 1
            assert orders[0]["assumed_gross_pnl"] is None
    resolution = {"slug": identity["slug"], "conditionId": identity["condition_id"],
                  "clobTokenIds": ["1", "2"], "outcomePrices": ["1", "0"],
                  "closed": True, "resolutionStatus": "resolved"}
    if protocol == "v1":
        resolution.update(version="v1", outcomes='["Up","Down"]', umaResolutionStatus="resolved")
    elif protocol == "v2":
        resolution.update(version="v2", outcomes='["Down","Up"]', positionIds=["2", "1"],
                          clobTokenIds='["71","72"]', outcomePrices='["0","1"]')
        await record(300.5, "SETTLEMENT", raw={**resolution, "version": "v1"})
        assert group.primary.resolution is None
    await record(301, "SETTLEMENT", raw=resolution)
    if capture is not None:
        await capture.close()
        final_seq = capture.input_seq
    else:
        writer.close()
        await group.close()
        if isinstance(group, RecordedMarketEngines):
            await group.materialize(writer.path)
        final_seq = list(read_tape(writer.path))[-1]["seq"]
    frozen = canonical_identity(directory / strategy / "simulation.sqlite3")
    assert frozen.get("protocol_version") == protocol and frozen["token_ids"] == {"YES": "1", "NO": "2"}
    original = group.report()
    for name, (market, orders) in original.items():
        assert market["input_seq"] == final_seq
        assert market["status"] == "SEALED" and market["official_winner"] == "YES"
        (order,) = orders
        assert order["token"] == "1"
        assert Decimal(order["price"]) == Decimal(".50")
        assert Decimal(order["quantity"]) == 10 and Decimal(order["assumed_gross_pnl"]) == 5
        assert Decimal(order["assumed_filled_quantity"]) == 10
        assert "known_simulated_gross_pnl" not in order
        evidence = list(read_audit(directory / name / "simulation.sqlite3"))
        if protocol == "v2":
            rejected, resolved = [row["payload"] for row in evidence if row["kind"] == "OFFICIAL_RESULT"]
            assert rejected["read_error"] == "PUBLIC_MARKET_VERSION_MISMATCH"
            assert rejected["result"] is None
            assert resolved["raw"] == resolution and resolved["result"]["position_ids"] == ["2", "1"]
        (admission,) = [row for row in evidence if row["kind"] == "ANCHOR_ENTRY"]
        (fill,) = [row for row in evidence if row["kind"] == "ASSUMED_FILL"]
        assert fill["input_seq"] == admission["input_seq"]
        if name == "M24":
            assert fill["payload"]["intent"]["available"] == 14.50 + prewarm
            assert any(
                row["kind"] == "SOP_EVIDENCE" and row["payload"]["event"] == "SIGNAL_SUSPENDED"
                for row in evidence
            )
        assert not any(
            row["kind"].startswith(("THROUGH_", "CANCEL_"))
            or row["kind"] in {"SUBMIT", "MODELLED_FILL", "RECEIPT_READY"}
            for row in evidence
        )
    if writer is not None:
        result = await replay(writer.path, tmp_path / "replayed", settings, parameters)
        for name, (market, orders) in result.items():
            assert market["input_seq"] == original[name][0]["input_seq"]
            assert orders == original[name][1]
        capture = object.__new__(MarketCapture)
        capture.group, capture.directory, capture.recorder = group, directory, writer
        capture.meta = SimpleNamespace(slug=identity["slug"])

    sealed = capture.seal()
    assert len(list(directory.rglob("input.jsonl.zst"))) == (writer is not None)
    if writer is None:
        assert sealed["baseline_architecture"] == BASELINE_V2
        assert sealed["input"] is sealed["input_sha256"] is None
    assert not list(directory.rglob("snapshot.sqlite3"))
    assert set(sealed["ledgers"]) == set(parameters)
    for ledger in sealed["ledgers"].values():
        assert (directory / ledger["file"]).is_file()
        assert not (directory / ledger["file"].removesuffix(".zst")).exists()
    if writer is None:
        report = directory / "market-report.json"
        expected = json.loads(report.read_text())
        report.unlink()  # A crash between compaction and publishing the report.
        epoch = int(identity["slug"].rsplit("-", 1)[1])
        if participants == M12_ONLY:
            recovery = PairRun(tmp_path, settings, parameters, end=0, resume={
                "cutoff": epoch, "first": epoch + MARKET_SECONDS,
                "baseline_v2_start_epoch": epoch, "predecessor": None, "previous_completion": None,
            })
            (recovery.output / "runtime.jsonl").touch()
        else:
            predecessor = {"market_epochs": [epoch], "settings": settings}
            recovery = SingleStrategyRun(run_directory, tmp_path, settings, parameters,
                                         api=SimpleNamespace(close=lambda: asyncio.sleep(0)),
                                         predecessor=predecessor)
            (run_directory / "plan.json").write_text(json.dumps({**predecessor, **recovery.plan_fields()}))
        await recovery.api.close()
        await recovery.materialize_closed_markets()
        assert not recovery.errors
        assert json.loads(report.read_text()) == expected
        if participants != M12_ONLY:
            metrics = await asyncio.to_thread(
                collect, run_directory, {}, {}, datetime.fromtimestamp(epoch + 300, timezone.utc)
            )
            assert list(metrics["rows"]) == [strategy]
            assert Decimal(metrics["rows"][strategy]["pnl"]) == 5
    if single:
        assert sealed["active_strategies"] == [strategy]
        assert {path.name for path in directory.iterdir() if path.is_dir()} == {strategy}
        return
    if participants == PAIR:
        assert sealed["active_strategies"] == list(participants)
        assert not (directory / "M24").exists()
        assert (directory / "M21").exists() == (participants == PAIR)
        return
    if group_class is RecordedMarketEngines:
        pair_settings, pair_parameters = configuration(PAIR)
        pair = PairRun(tmp_path, pair_settings, pair_parameters, end=0)
        await pair.api.close()
        pair.output.mkdir()
        (tmp_path / "plan.json").write_text(json.dumps({
            "continuation_start_epoch": 0, "continuation_settings": settings,
        }))
        await pair.recover_one(identity["slug"], sealed["input_seq"])
        recovered = pair.output / "recovered" / identity["slug"] / "market-report.json"
        rereport = json.loads(recovered.read_text())
        assert rereport["active_strategies"] == list(PAIR)
        assert rereport["input_sha256"] == sealed["input_sha256"]
        assert rereport["ledgers"]["M12"] == sealed["ledgers"]["M12"]
        for name in PAIR:
            assert rereport["strategies"][name] == list(sealed["strategies"][name])


@pytest.mark.asyncio
async def test_saturated_receiver_keeps_every_frame_in_order():
    class Socket:
        async def __aiter__(self):
            for seq in range(1500):
                yield str(seq)

    receiver_blocked = asyncio.Event()

    class Receiver(SharedCaptureFeed):
        async def enqueue(self, kind, **payload):
            if self.target.full():
                receiver_blocked.set()
            await super().enqueue(kind, **payload)

    queue = CaptureQueue(4)
    feed = Receiver(None, queue)
    task = asyncio.create_task(feed.receive(Socket()))
    await asyncio.wait_for(receiver_blocked.wait(), timeout=1)
    assert queue.full()
    assert len(feed.m1_pending_received) == 1
    received = []
    for _ in range(1500):
        kind, payload = await asyncio.wait_for(queue.get(), timeout=1)
        assert kind == "FRAME"
        received.append((int(payload["raw"]), payload["received_mono"]))
        if len(received) % 4 == 0:
            await asyncio.sleep(0)
    await task
    assert [n for n, _ in received] == list(range(1500))
    assert received == sorted(received, key=lambda row: row[1])
    assert not feed.m1_pending_received and feed.pending_event is None


@pytest.mark.asyncio
async def test_disconnect_records_already_received_frame_before_gap():
    queue = CaptureQueue(1)
    feed = SharedCaptureFeed(None, queue)
    await feed.enqueue("FRAME", raw="1", received_mono=1)
    blocked = asyncio.create_task(feed.enqueue("FRAME", raw="2", received_mono=2))
    await asyncio.sleep(0)
    blocked.cancel()
    await asyncio.gather(blocked, return_exceptions=True)
    disconnected = asyncio.create_task(feed.disconnected("WS_DISCONNECTED"))
    events = [await asyncio.wait_for(queue.get(), timeout=1) for _ in range(3)]
    await disconnected
    assert [kind for kind, _ in events] == ["FRAME", "FRAME", "GAP"]
    assert [payload["raw"] for _, payload in events[:2]] == ["1", "2"]


@pytest.mark.asyncio
async def test_stopped_capture_drains_frame_waiting_for_capacity(tmp_path, monkeypatch):
    settings, parameters = configuration()
    definition = fixture_events()[0]["definition"]
    meta = market_meta(definition["market"])

    class SaturatingFeed(SharedCaptureFeed):
        async def run(self):
            await self.enqueue("CONNECT", transport_generation=1)
            await self.enqueue("FRAME", raw="PING", received_mono=time.monotonic(),
                               received_wall=time.time())
            stop_after_enqueue = asyncio.create_task(stop_when_blocked(self))
            try:
                await self.enqueue("FRAME", raw="PONG", received_mono=time.monotonic(),
                                   received_wall=time.time())
            finally:
                await stop_after_enqueue

    async def stop_when_blocked(feed):
        assert feed.backpressure_count == 1
        session.request_stop("MANUAL_STOP")

    async def let_receiver_fill(engine):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    session = SharedRun(tmp_path, tmp_path, settings, parameters, api=object(),
                        feed_factory=SaturatingFeed)
    epoch = int(meta.slug.rsplit("-", 1)[-1])
    session.public_clock = [epoch - 2, 0, time.monotonic()]
    capture = MarketCapture(session, meta, definition["ticks"])
    monkeypatch.setattr(capture, "refresh_ticks", let_receiver_fill)
    start_feed = capture.start_feed

    def start_small_queue():
        start_feed()
        capture.queue._maxsize = 2

    capture.start_feed = start_small_queue
    try:
        await asyncio.wait_for(capture.capture(), timeout=10)
    finally:
        await capture.close()

    remainder = list(read_tape(capture.recorder.path))
    assert [e["raw"] for e in remainder if e["kind"] == "FRAME"] == ["PING", "PONG"]
    assert remainder[-1]["reason"] == "MANUAL_STOP"
    await capture.group.materialize(capture.recorder.path)
    report = capture.seal()
    assert report["input_seq"] == len(remainder)
    assert report["complete_window"] is False
    assert all(not row["complete"] for row, _ in report["strategies"].values())


@pytest.mark.asyncio
@pytest.mark.parametrize("stalls", [False, True])
async def test_replay_timeout_uses_progress_not_total_wall_time(tmp_path, monkeypatch, stalls):
    class ReplayProcess:
        def __init__(self):
            self.stdout = self
            self.returncode = None
            self.input_seq = 0
            self.killed = False

        async def readline(self):
            if stalls and self.input_seq:
                await asyncio.Event().wait()
            await asyncio.sleep(0.025)
            self.input_seq += 1
            row = {"input_seq": self.input_seq}
            if self.input_seq == 4:
                row["states"] = {"M21": {"seq": 4}, "M24": {"seq": 4}}
            return (json.dumps(row) + "\n").encode()

        async def wait(self):
            self.returncode = -9 if self.killed else 0
            return self.returncode

        def kill(self):
            self.killed = True

    process = ReplayProcess()
    async def start(*args, **kwargs):
        return process

    monkeypatch.setattr("m1223_shared72.REPLAY_IDLE_SECONDS", 0.08)
    monkeypatch.setattr("asyncio.create_subprocess_exec", start)
    group = RecordedMarketEngines(tmp_path, {}, {name: object() for name in ("M12", "M21", "M24")}, {})
    if stalls:
        with pytest.raises(StrategyReplayTimeout, match="STALLED_AT:1"):
            await group.materialize(Path("tape"))
        assert process.killed
    else:
        await group.materialize(Path("tape"))
        assert group.worker_states == {"M21": {"seq": 4}, "M24": {"seq": 4}}
        assert not process.killed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocked_stage", "timed_out"), [("settlement", False), ("replay", False), ("replay", True)]
)
async def test_next_market_survives_slow_or_timed_out_replay(
    tmp_path, monkeypatch, blocked_stage, timed_out,
):
    session = SharedRun(
        tmp_path,
        tmp_path,
        {"max_markets": 2, "max_pending_markets": 1 if blocked_stage == "replay" else 12},
        {},
        api=SimpleNamespace(
            clock=lambda: asyncio.sleep(0, result=[0, 0, 0]), close=lambda: asyncio.sleep(0)
        ),
    )
    old_waiting = asyncio.Event()
    new_subscribed = asyncio.Event()
    start_times = []

    async def preflight(slug, epoch):
        return SimpleNamespace(slug=slug, epoch=epoch), {}

    async def wait_until(epoch):
        if epoch == 2 * MARKET_SECONDS - 25:
            await old_waiting.wait()
        start_times.append(epoch)
        return True

    class Capture:
        def __init__(self, _session, meta, _ticks):
            self.meta, self.directory = meta, tmp_path / meta.slug
            self.recorder = SimpleNamespace(seq=1, path=self.directory / "input")
            self.group = SimpleNamespace(materialize=self.materialize)

        async def capture(self):
            if self.meta.epoch == 2 * MARKET_SECONDS:
                new_subscribed.set()
            return self.meta

        async def settle(self, meta):
            if meta.epoch == MARKET_SECONDS and blocked_stage == "settlement":
                old_waiting.set()
                await new_subscribed.wait()

        async def close(self):
            pass

        async def materialize(self, path):
            if self.meta.epoch == MARKET_SECONDS and blocked_stage == "replay":
                old_waiting.set()
                await new_subscribed.wait()
                if timed_out:
                    raise StrategyReplayTimeout("SHARED_STRATEGY_REPLAY_STALLED_AT:1")

        def seal(self):
            return {"market": self.meta.slug, "complete_window": True, "official_winner": "YES", "strategies": {}}

    monkeypatch.setattr("m1223_shared72.MarketCapture", Capture)
    monkeypatch.setattr(session, "server_now", lambda: 0)
    monkeypatch.setattr(session, "prepare", preflight)
    monkeypatch.setattr(session, "wait_until", wait_until)
    monkeypatch.setattr(session, "watch_capacity", lambda: asyncio.sleep(100))
    await asyncio.wait_for(session.run(), timeout=3)
    assert [n * MARKET_SECONDS - PREWARM_SECONDS for n in (1, 2)] == [
        t for t in start_times if t % 300 == 295
    ]
    terminal = json.loads((tmp_path / "completion.json").read_text())
    assert terminal["common_complete_settled_markets"] == (1 if timed_out else 2)
    assert terminal["status"] == ("PARTIAL" if timed_out else "PASS")
    assert not session.stop.is_set()
    assert bool(terminal["errors"]) is timed_out


@pytest.mark.asyncio
async def test_m12_v2_seals_without_replay_and_isolates_seal_failure(tmp_path, monkeypatch):
    settings, parameters = configuration(M12_ONLY)
    session = SharedRun(tmp_path, tmp_path, settings, parameters, api=object())

    async def prepare(slug, epoch):
        return SimpleNamespace(slug=slug, epoch=epoch), {}

    class Capture:
        input_seq = 12

        def __init__(self, _session, meta, _ticks):
            self.meta = meta

        async def capture(self):
            return self.meta

        async def settle(self, _engine):
            pass

        async def close(self):
            pass

        def seal(self):
            if self.meta.epoch == MARKET_SECONDS:
                raise OSError("injected compaction failure")
            return {"market": self.meta.slug}

    monkeypatch.setattr("m1223_shared72.MarketCapture", Capture)
    monkeypatch.setattr(session, "prepare", prepare)
    monkeypatch.setattr(session, "wait_until", lambda _: asyncio.sleep(0, result=True))
    first, second = (f"btc-updown-5m-{index * MARKET_SECONDS}" for index in (1, 2))
    await session.market(first, MARKET_SECONDS)
    await session.market(second, MARKET_SECONDS * 2)
    assert not session.stop.is_set()
    assert session.replay_queue.empty()
    assert session.closed_markets == {first, second}
    assert list(session.reports) == [second]
    assert [(row["market"], row["stage"]) for row in session.errors] == [(first, "LEDGER_SEAL")]
    log = [json.loads(row) for row in (tmp_path / "runtime.jsonl").read_text().splitlines()]
    assert [row["event"] for row in log] == [
        "MARKET_LEDGER_CLOSED", "MARKET_LEDGER_SEAL_FAILED", "MARKET_LEDGER_CLOSED", "MARKET_SEALED",
    ]


def test_v2_cli_keeps_strategy_instances_independent(tmp_path, monkeypatch, capsys):
    root = tmp_path / "m122124_baseline"
    root.mkdir()
    monkeypatch.setattr("baseline_v2.storage_status", lambda *_: {"reason": None})
    monkeypatch.setattr("baseline_v2.shutil.which", lambda _: "/usr/bin/zstd")
    launched = []

    async def supervise(session):
        launched.append((session.output.name, tuple(session.parameters)))
        await session.api.close()
        return {"status": "PASS"}

    monkeypatch.setattr("baseline_v2.supervise", supervise)

    def invoke(*options):
        monkeypatch.setattr(sys, "argv", ["baseline-v2", "run", "--storage-root", str(root), *options])
        baseline_v2_main()

    with (root / "pair-cutover.lock").open("a") as m12_lock:
        fcntl.flock(m12_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        invoke("--strategy", "M21", "--run-name", "m21-standalone")
        with pytest.raises(SystemExit, match="2"):
            invoke("--strategy", "M12", "--run-name", "duplicate-m12")
        assert "already has a running baseline" in capsys.readouterr().err
    invoke("--strategy", "M12", "--run-name", "m12-standalone")
    with pytest.raises(SystemExit, match="2"):
        invoke("--strategy", "M12", "--strategy", "M21")
    assert "exactly one --strategy" in capsys.readouterr().err
    assert launched == [("m21-standalone", ("M21",)), ("m12-standalone", ("M12",))]
    assert not (root / "duplicate-m12").exists()


def test_v2_resume_checks_strategy_and_keeps_original_market_window(tmp_path):
    settings, _ = configuration(("M21",))
    settings["max_markets"] = 4
    epochs = [300, 600, 900, 1200]
    plan = {"path": str(tmp_path), "mode": "SIMULATION_ONLY_PUBLIC_DATA", "schema": SCHEMA,
            "baseline_architecture": BASELINE_V2, "strategy": "M21",
            "market_epochs": epochs, "settings": settings}
    (tmp_path / "plan.json").write_text(json.dumps(plan))
    previous, end = resume_session(tmp_path, "M21", settings, now=574)
    assert previous["market_epochs"] == [300, 600] and end == 1500
    with pytest.raises(ValueError, match="RUN_IDENTITY_MISMATCH"):
        resume_session(tmp_path, "M12", settings, now=574)
    with pytest.raises(ValueError, match="RESUME_SETTINGS_CHANGED"):
        resume_session(tmp_path, "M21", {**settings, "seed": -1}, now=574)


def test_disk_admission_and_runtime_stop(tmp_path, monkeypatch):
    free = FREE_RESERVE + STOP_HEADROOM + 1
    monkeypatch.setattr(
        "m1223_shared_storage.shutil.disk_usage", lambda _: SimpleNamespace(free=free)
    )
    status = storage_status(tmp_path, tmp_path, used_bytes=30 * FREE_RESERVE)
    assert status["reason"] is None and status["project_reserve_bytes"] == FREE_RESERVE
    assert status["output_budget_bytes"] is None
    assert status["available_run_headroom_bytes"] == 1
    free = FREE_RESERVE + STOP_HEADROOM
    assert storage_status(tmp_path, tmp_path)["reason"] == "PROJECT_DISK_RESERVE"


def test_tape_retention_requires_age_and_all_three_sealed_ledgers(tmp_path):
    def market(epoch, *, status="SEALED", skip_report=False, missing_ledger=False):
        directory = tmp_path / f"btc-updown-5m-{epoch}"
        directory.mkdir()
        tape = directory / "input.jsonl.zst"
        tape.write_text("fixture tape")
        report = {
            "market": directory.name, "input": tape.name, "complete_window": True,
            "input_sha256": "fixture", "strategies": {}, "ledgers": {},
        }
        for strategy in ("M12", "M21", "M24"):
            state = status if strategy == "M24" else "SEALED"
            relative = Path(strategy) / "simulation.sqlite3.zst"
            ledger = directory / relative
            ledger.parent.mkdir()
            if strategy != "M21" or not missing_ledger:
                ledger.write_bytes(b"fixture ledger")
            report["strategies"][strategy] = [{"status": state, "complete": True}, []]
            report["ledgers"][strategy] = {
                "file": str(relative), "bytes": len(b"fixture ledger"), "sqlite_sha256": "fixture",
            }
        if not skip_report:
            (directory / "market-report.json").write_text(json.dumps(report))
        return tape

    older = market(1000)
    unsettled = market(700, status="OPEN")
    unreported = market(400, skip_report=True)
    missing_ledger = market(100, missing_ledger=True)
    newer = market(1300)
    now = 1000 + MARKET_SECONDS + TAPE_RETENTION_SECONDS
    allocated = older.stat().st_blocks * 512
    assert prune_expired_tapes(tmp_path, now) == {
        "removed_tapes": 1, "removed_allocated_bytes": allocated,
    }
    assert not older.exists()
    assert (older.parent / "market-report.json").is_file()
    assert len(list(older.parent.glob("*/simulation.sqlite3.zst"))) == 3
    assert all(tape.is_file() for tape in (unsettled, unreported, missing_ledger, newer))
    assert prune_expired_tapes(tmp_path, now)["removed_tapes"] == 0


@pytest.mark.asyncio
async def test_continuation_captures_only_tail_and_reports_both_segments(tmp_path, monkeypatch):
    predecessor = {"market_epochs": [300, 600], "settings": {"max_markets": 2}}
    prior_reports = [{"market": f"btc-updown-5m-{epoch}", "complete_window": True,
                      "official_winner": "YES", "strategies": {}}
                     for epoch in predecessor["market_epochs"]]
    for report in prior_reports:
        directory = tmp_path / report["market"]
        directory.mkdir()
        (directory / "market-report.json").write_text(json.dumps(report))
    legacy_terminal = {"planned_markets": 2, "errors": [], "peak_bytes": 100}
    (tmp_path / "completion.json").write_text(json.dumps(legacy_terminal))
    session = SharedRun(
        tmp_path, tmp_path, {"max_markets": 4, "max_pending_markets": 12}, {},
        api=SimpleNamespace(clock=lambda: asyncio.sleep(0, result=[0, 0, 0]),
                            close=lambda: asyncio.sleep(0)), predecessor=predecessor,
    )
    captured = []

    async def market(slug, epoch):
        captured.append(epoch)
        session.reports[slug] = {
            "market": slug, "complete_window": True, "official_winner": "YES", "strategies": {},
        }
        session.active.pop(slug)

    monkeypatch.setattr(session, "market", market)
    monkeypatch.setattr(session, "server_now", lambda: 700)
    monkeypatch.setattr(session, "wait_until", lambda _: asyncio.sleep(0, result=True))
    monkeypatch.setattr(session, "watch_capacity", lambda: asyncio.sleep(100))
    terminal = await asyncio.wait_for(session.run(), timeout=3)
    assert captured == [900, 1200]
    assert terminal["status"] == "PASS" and terminal["common_complete_settled_markets"] == 4
    assert json.loads((tmp_path / "completion.json").read_text()) == legacy_terminal
    assert json.loads((tmp_path / "continuation-completion.json").read_text()) == terminal
    assert json.loads((tmp_path / "plan.json").read_text())["market_epochs"] == [300, 600, 900, 1200]


def test_pair_resume_retains_handoff_archive_and_original_schedule(tmp_path):
    settings, _ = configuration(PAIR)
    phase = tmp_path / PHASE_DIRECTORY
    phase.mkdir()
    handoff = {"state": "ACTIVE", "cutoff_epoch": 300, "strategies": list(PAIR)}
    (phase / HANDOFF_FILE).write_text(json.dumps(handoff))
    plan = {"path": str(phase), "mode": "SIMULATION_ONLY_PUBLIC_DATA", "schema": SCHEMA,
            "settings": {**settings, "implementation": {}}, "market_epochs": [300, 600, 900, 1200]}
    (phase / "plan.json").write_text(json.dumps(plan))
    terminal = {"errors": [], "peak_bytes": 1}
    (phase / "completion.json").write_text(json.dumps(terminal))
    runtime = tmp_path / "frozen"
    (runtime / "ops").mkdir(parents=True)
    (runtime / "ops/m1223_shared_worker.py").touch()
    occupied = phase / "btc-updown-5m-900"
    occupied.mkdir()
    with pytest.raises(ValueError, match="OVERLAPS_EXISTING_MARKET"):
        prepare_resume(tmp_path, settings, 900, runtime)

    saved, resume = prepare_resume(tmp_path, settings, 1200, runtime)
    resume = archive_resume(phase, saved, resume)
    assert resume["cutoff"] == 300 and resume["predecessor"]["market_epochs"] == [300, 600, 900]
    assert json.loads((phase / HANDOFF_FILE).read_text()) == handoff
    assert json.loads((phase / "plan.json").read_text()) == plan
    assert json.loads((resume["replay_plan"].parent / "completion.json").read_text()) == terminal
    assert not (phase / "completion.json").exists()
    assert occupied.is_dir()


def test_resume_only_m12_after_pair_and_keep_historical_reporting(tmp_path):
    settings, _ = configuration(M12_ONLY)
    before, _ = configuration(PAIR)
    phase = tmp_path / PHASE_DIRECTORY
    phase.mkdir()
    (phase / HANDOFF_FILE).write_text('{"state":"ACTIVE","cutoff_epoch":300,"strategies":["M12","M21"]}')
    epochs = [300, 600, 900]
    (tmp_path / "plan.json").write_text(json.dumps({"market_epochs": epochs}))
    plan = {"path": str(phase), "mode": "SIMULATION_ONLY_PUBLIC_DATA", "schema": SCHEMA,
            "settings": before, "market_epochs": epochs, "continuation_start_epoch": 600,
            "continuation_settings": {**before, "implementation": {}}}
    (phase / "plan.json").write_text(json.dumps(plan))
    runtime = tmp_path / "frozen"
    (runtime / "ops").mkdir(parents=True)
    (runtime / "ops/m1223_shared_worker.py").touch()
    prior, resume = prepare_resume(tmp_path, settings, 900, runtime, M12_ONLY)
    resume = archive_resume(phase, prior, resume)
    assert resume["m21_stop_epoch"] == 900 and resume["previous_strategies"] == PAIR
    assert resume["baseline_v2_start_epoch"] == 900
    assert json.loads(resume["replay_plan"].read_text())["continuation_start_epoch"] == 600
    (phase / "plan.json").write_text(json.dumps({**plan, "m21_stop_epoch": 900}))
    for epoch, labels in ((300, PAIR), (600, PAIR), (900, M12_ONLY)):
        slug = f"btc-updown-5m-{epoch}"
        directory = phase / slug
        directory.mkdir()
        report = {"market": slug, "complete_window": True, "official_winner": "YES",
                  "active_strategies": list(labels), "strategies": {
                      name: [{"complete": True, "status": "SEALED", "signal_count": 1,
                              "signal_ids": [name], "assumed_orders": 0}, []] for name in labels}}
        (directory / "market-report.json").write_text(json.dumps(report))
    result = collect(tmp_path, {}, {}, datetime.fromtimestamp(1200, timezone.utc))
    assert result["sealed_markets"] == 3 and result["m21_stop_epoch"] == 900
    assert result["rows"]["M12"]["signals"] == 3 and result["rows"]["M21"]["signals"] == 2


def test_hourly_report_waits_for_continuation_completion(tmp_path):
    (tmp_path / "plan.json").write_text(json.dumps({
        "market_epochs": [300, 600, 900, 1200], "continuation_start_epoch": 900,
    }))
    (tmp_path / "completion.json").write_text('{"planned_markets": 2}')
    now = datetime.fromtimestamp(1500, timezone.utc)
    assert not collect(tmp_path, {}, {}, now)["run_finished"]
    (tmp_path / "continuation-completion.json").write_text('{"planned_markets": 4}')
    assert collect(tmp_path, {}, {}, now)["run_finished"]
