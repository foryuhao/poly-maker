"""R2: real coordinator control flow, exact availability, bounded retries, offline replay."""

import asyncio
import gzip
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest

from m1234_fixtures import fixture_events
from polymaker.m1234_sim import INPUT_SCHEMA
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import Clock, Recorder, digest, encode
from polymaker.m1234_sim.store import read_audit
from polymaker.m1234_sim.ticks import TICK_POLICY


async def initial(tmp_path):
    engine = Engine(tmp_path)
    engine.test_tape = deepcopy(fixture_events()[:3])
    for row in engine.test_tape:
        await engine.ingest(row)
    engine.test_wall = engine.clock.wall
    return engine


async def send(engine, kind, at, **data):
    row = {"schema": INPUT_SCHEMA, "seq": engine.seq + 1, "generation": engine.coverage["generations"],
           "available_mono": at, "available_wall": engine.test_wall + at,
           "kind": kind, "pending": [], **data}
    if kind == "FRAME":
        row.setdefault("received_mono", at)
        row.setdefault("received_wall", engine.test_wall + row["received_mono"])
    await engine.ingest(row)
    engine.test_tape.append(deepcopy(row))


async def change(engine, at, old=".01", new=".001"):
    await send(engine, "FRAME", at, raw={
        "event_type": "tick_size_change", "market": engine.meta.condition_id,
        "asset_id": "1", "old_tick_size": old, "new_tick_size": new,
        "timestamp": str((engine.test_wall + at) * 1000),
    })


async def fresh(engine, at):
    rows = deepcopy(engine.test_tape[2]["raw"])
    for row in rows:
        row["timestamp"] = str((engine.test_wall + at) * 1000)
    await send(engine, "FRAME", at, raw=rows)


async def request(engine, at):
    proposed = engine.tick_verification.propose(engine.signals, at)
    assert proposed is not None
    await send(engine, "TICK_REQUEST", at, request=proposed)
    return deepcopy(engine.tick_verification.active)


async def result(engine, req, at, *, ticks=None, status="SUCCESS", received=None, **overrides):
    received = at if received is None else received
    payload = {"request_id": req["request_id"], "target_id": req["target_id"],
               "target": req["target"], "market": req["market"],
               "status": status, "ticks": ticks or req["target"]["M1"]["ticks"],
               "sent_mono": req["requested_mono"], "sent_wall": req["requested_wall"],
               "received_mono": received, "received_wall": engine.test_wall + received,
               **overrides}
    await send(engine, "TICK_RESULT", at, result=payload)


def legal(engine):
    return [feed.m1.legal(feed.books["1"], engine.clock.mono)
            for feed in engine.signals.views.values()]


async def test_fresh_views_recover_at_recorded_result_availability_not_request(tmp_path):
    e = await initial(tmp_path)
    try:
        assert all(legal(e))
        await change(e, .01)
        await fresh(e, .02)
        assert not any(legal(e))
        assert all(f.snapshot().reason == "WS_TICK_UNVERIFIED" for f in e.signals.views.values())
        req = await request(e, .025)
        await send(e, "TIMER", .03)
        assert not any(legal(e))
        await result(e, req, .04, received=.035)
        assert all(legal(e))
        assert e.tick_verification.constraints["1"]["verified_at"] == .04
        assert req["requested_mono"] == .025
    finally:
        await e.close()


async def test_duplicate_notifications_share_one_request_and_do_not_exhaust_budget(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        req = await request(e, .02)
        for at in [.03, .04, .05]:
            await change(e, at)
        assert e.tick_verification.propose(e.signals, .06) is None
        await result(e, req, .07)
        await change(e, .08)
        assert e.tick_verification.propose(e.signals, .09) is None
        assert e.tick_verification.stats["requests"] == 1
        assert e.tick_verification.stats["duplicate_notifications"] == 4
    finally:
        await e.close()


@pytest.mark.parametrize("return_to_same", [False, True])
async def test_query_cannot_override_a_newer_ws_revision_even_if_tick_returns_same(tmp_path, return_to_same):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        old = await request(e, .02)
        await change(e, .03, old=".001", new=".01")
        if return_to_same:
            await change(e, .04)
        await result(e, old, .05)
        assert e.tick_verification.state == "UNVERIFIED" and not any(legal(e))
        assert e.tick_verification.stats["stale_results"] == 1
        new = await request(e, .06)
        expected = ".001" if return_to_same else ".01"
        await fresh(e, .07)
        await result(e, new, .08, ticks={"1": expected, "2": e.definition["ticks"]["2"]})
        assert all(legal(e))
        assert new["target_id"] != old["target_id"]
    finally:
        await e.close()


@pytest.mark.parametrize("bad", ["conflict", "missing-token", "nonfinite", "future-response", "failed", "timeout"])
async def test_failed_or_conflicting_verification_stays_blocked_then_recovers(tmp_path, bad):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        req = await request(e, .02)
        kwargs = {
            "conflict": {"ticks": {"1": ".01", "2": e.definition["ticks"]["2"]}},
            "missing-token": {"ticks": {"1": ".001"}},
            "nonfinite": {"ticks": {"1": "NaN", "2": e.definition["ticks"]["2"]}},
            "future-response": {"received": .5}, "failed": {"status": "FAILED"},
            "timeout": {"status": "TIMEOUT"},
        }[bad]
        await result(e, req, .03, **kwargs)
        assert e.tick_verification.state == "UNVERIFIED"
        assert not any(feed.tick_verified for feed in e.signals.views.values())
        assert e.tick_verification.propose(e.signals, .5) is None
        req2 = await request(e, 1.04)
        await fresh(e, 1.05)
        await result(e, req2, 1.06)
        assert all(legal(e)) and e.tick_verification.stats["successes"] == 1
    finally:
        await e.close()


async def test_unmatched_response_does_not_consume_the_current_request(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        req = await request(e, .02)
        await result(e, req, .03, request_id="foreign-request")
        assert e.tick_verification.active["request_id"] == req["request_id"]
        assert not any(legal(e))
        await result(e, req, .04)
        assert all(legal(e))
    finally:
        await e.close()


async def test_reconnect_requires_current_generation_verification(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        old = await request(e, .02)
        await send(e, "GAP", .03, reason="ARTIFICIAL_DISCONNECT")
        await send(e, "CONNECT", .04)
        await fresh(e, .05)
        await result(e, old, .06)
        assert not any(legal(e)) and e.signals.m1.generation == 2
        new = await request(e, .07)
        await result(e, new, .08)
        assert all(legal(e)) and new["target"]["M1"]["generation"] == 2
    finally:
        await e.close()


async def test_retry_limits_are_explicit_and_new_constraint_can_recover(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        at = .02
        for _ in range(TICK_POLICY["attempts_per_target"]):
            req = await request(e, at)
            await result(e, req, at + .01, status="FAILED")
            at = e.tick_verification.next_due + .01
        assert e.tick_verification.propose(e.signals, 100) is None
        assert e.tick_verification.reason.startswith("RETRY_EXHAUSTED")
        assert e.tick_verification.stats["requests"] == 4
        await change(e, at, old=".001", new=".01")
        req = await request(e, at + .01)
        await fresh(e, at + .02)
        await result(e, req, at + .03, ticks={"1": ".01", "2": e.definition["ticks"]["2"]})
        assert all(legal(e))
    finally:
        await e.close()


async def test_verification_after_cutoff_observes_without_reopening_entries(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, 221.0)
        req = await request(e, 221.01)
        await fresh(e, 221.02)
        await result(e, req, 221.03)
        await send(e, "TIMER", 221.04)
        assert all(legal(e)) and e.bounds()[0] > 240
        assert all(not b.orders for b in e.brokers.values())
    finally:
        await e.close()


async def test_tick_tape_replays_twice_offline_without_inventing_a_query(tmp_path, monkeypatch):
    from polymaker.m1234_sim import runner

    e = await initial(tmp_path / "online")
    await change(e, .01)
    req = await request(e, .02)
    await result(e, req, .03)
    await send(e, "END", .1, reason="ARTIFICIAL_TICK_TEST")
    await e.close()
    path = tmp_path / "tick-input.gz"
    with gzip.open(path, "wt") as output:
        for row in e.test_tape:
            output.write(encode(row) + "\n")
    monkeypatch.setattr(runner, "output_path", lambda p: Path(p))
    monkeypatch.setattr(runner, "VirtualPublicAPI", lambda: pytest.fail("offline replay queried REST"))
    first = await runner.replay(path, tmp_path / "replay-a")
    second = await runner.replay(path, tmp_path / "replay-b")
    assert first["state_hash"] == second["state_hash"] == digest(e.snapshot())
    assert first["audit_hash"] == second["audit_hash"]


async def test_runner_query_events_restore_natural_policy_flow_and_replay(tmp_path, monkeypatch):
    from polymaker.m1234_sim import runner

    tape = fixture_events()
    e = Engine(tmp_path / "online")
    record = Recorder(tmp_path / "input.gz")
    clock = Clock(0, tape[0]["available_wall"])

    def payload(row):
        return {k: v for k, v in row.items()
                if k not in {"schema", "seq", "kind", "available_mono", "available_wall", "generation"}}

    for row in tape[:3]:
        copied = record.append(row["kind"], clock, row["generation"], **payload(row))
        await e.ingest(copied)
    gate = asyncio.Event()

    class API:
        calls = 0

        async def ticks(self, tokens):
            self.calls += 1
            await gate.wait()
            return {"1": D(".001"), "2": D(tape[0]["definition"]["ticks"]["2"])}

    api = API()
    run = runner.LiveRun.__new__(runner.LiveRun)
    run.api, run.tick_task, run.active_input = api, None, None
    run.recorders = {e.directory.name: record}
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=clock.monotonic, time=clock.time))
    clock.set(.001, tape[0]["available_wall"] + .001)
    await run.record(e, "FRAME", received_mono=clock.mono, received_wall=clock.wall, raw={
        "event_type": "tick_size_change", "market": e.meta.condition_id, "asset_id": "1",
        "old_tick_size": ".01", "new_tick_size": ".001", "timestamp": str(clock.wall * 1000),
    })
    clock.set(.002, tape[0]["available_wall"] + .002)
    await run.refresh_ticks(e)
    for _ in range(10):
        await asyncio.sleep(0)
        await run.refresh_ticks(e)
    assert api.calls == 1 and run.tick_task is not None
    assert not any(legal(e))
    clock.set(.003, tape[0]["available_wall"] + .003)
    gate.set()
    for _ in range(10):
        await asyncio.sleep(0)
    clock.set(.004, tape[0]["available_wall"] + .004)
    await run.refresh_ticks(e)
    assert run.tick_task is None and all(legal(e))
    for row in tape[3:]:
        clock.set(row["available_mono"], row["available_wall"])
        copied = record.append(row["kind"], clock, row["generation"], **payload(row))
        await e.ingest(copied)
    assert any(o.intent.strategy == "M1" for b in e.brokers.values() for o in b.orders.values())
    assert any(o.filled > 0 for b in e.brokers.values() for o in b.orders.values())
    state = digest(e.snapshot())
    await e.close()
    record.close()
    audit = list(read_audit(e.store.path))
    reply = next(row["payload"] for row in audit if row["kind"] == "TICK_REVERIFY_RESULT")
    assert reply["at"] == .004 and reply["result"]["received_mono"] == .003
    assert reply["applied"]
    monkeypatch.setattr(runner, "output_path", lambda p: Path(p))
    monkeypatch.setattr(runner, "VirtualPublicAPI", lambda: pytest.fail("replay performed network IO"))
    replayed = await runner.replay(tmp_path / "input.gz", tmp_path / "replayed")
    assert replayed["state_hash"] == state
    assert replayed["audit_hash"] == digest(audit)


async def test_real_async_query_timeout_is_bounded_and_does_not_create_second_task(monkeypatch):
    from polymaker.m1234_sim import runner

    cancelled = asyncio.Event()

    class API:
        async def ticks(self, tokens):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    run = runner.LiveRun.__new__(runner.LiveRun)
    run.api = API()
    monkeypatch.setitem(TICK_POLICY, "timeout_seconds", .01)
    req = {"request_id": "test", "target_id": "version", "target": {},
           "market": "m", "tokens": ["1", "2"]}
    result = await run.fetch_ticks(req)
    assert result["status"] == "TIMEOUT" and cancelled.is_set()
    assert result["received_mono"] >= result["sent_mono"]


async def test_verification_success_does_not_hide_stale_book_source(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        req = await request(e, .02)
        await result(e, req, .3)
        assert e.tick_verification.state == "VERIFIED"
        assert not any(legal(e))  # source age is a distinct reason, not tick failure
        await fresh(e, .31)
        assert all(legal(e))
    finally:
        await e.close()


async def test_per_market_query_cap_remains_blocked_after_new_notifications(tmp_path, monkeypatch):
    e = await initial(tmp_path)
    monkeypatch.setitem(TICK_POLICY, "requests_per_market", 2)
    try:
        await change(e, .01)
        one = await request(e, .02)
        await result(e, one, .03, status="FAILED")
        await change(e, .04, old=".001", new=".01")
        two = await request(e, .05)
        await result(e, two, .06, status="FAILED")
        await change(e, .07)
        assert e.tick_verification.propose(e.signals, 10) is None
        assert e.tick_verification.reason == "REQUEST_BUDGET_EXHAUSTED"
        assert e.tick_verification.stats["requests"] == 2 and not any(legal(e))
    finally:
        await e.close()


async def test_wrong_market_result_cannot_restore_verification(tmp_path):
    e = await initial(tmp_path)
    try:
        await change(e, .01)
        req = await request(e, .02)
        await result(e, req, .03, market="another-market")
        assert e.tick_verification.state == "UNVERIFIED" and not any(legal(e))
        assert e.tick_verification.stats["stale_results"] == 1
    finally:
        await e.close()


async def test_market_end_cancels_only_owned_tick_task_and_records_cancellation(tmp_path, monkeypatch):
    from polymaker.m1234_sim import runner

    e = await initial(tmp_path)
    try:
        await change(e, .01)
        req = await request(e, .02)
        owned = asyncio.create_task(asyncio.Event().wait())
        unrelated = asyncio.create_task(asyncio.sleep(0))
        run = runner.LiveRun.__new__(runner.LiveRun)
        run.tick_task = (e, req, owned)
        monkeypatch.setattr(runner, "time", SimpleNamespace(
            monotonic=lambda: .04, time=lambda: e.test_wall + .04))

        async def record(engine, kind, **data):
            await send(engine, kind, .04, **data)

        run.record = record
        await run.finish_tick_request(e)
        await unrelated
        assert owned.cancelled() and not unrelated.cancelled()
        assert run.tick_task is None and e.tick_verification.stats["cancelled"] == 1
        assert e.tick_verification.stats["failures"] == 0
        assert not any(legal(e))
    finally:
        await e.close()
