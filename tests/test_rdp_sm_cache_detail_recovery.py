"""A missing detail blocks its order, not independent close reconciliation."""

import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymaker.execution.gateway import GatewayReadError
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_preparation_reads import env as env
from test_rdp_sm_preparation_reads import setup_preparation
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_sync import sync_env as sync_env


async def two_closed_orders(env, monkeypatch, tmp_path):
    setup = await setup_preparation(env, monkeypatch)
    companion, _ = await attach(setup, tmp_path)
    v, e, _, _, normal = setup[:5]
    try:
        first = await normal.enter(Decimal(0), Decimal(0), "first")
        assert first and first[0].close_barrier == "SAFE_TO_CONTINUE"
        setup[7]["direction"] = "NONE"
        await asyncio.sleep(.025)
        setup[7]["direction"] = "YES"
        await asyncio.sleep(.025)
        for _ in range(10):
            e.reads.network.observe("GET", .001)
        setup[3].account = (await e._snapshot(include_books=False))[3]
        second = await normal.enter(Decimal(0), Decimal(0), "second")
        assert second and second[0].close_barrier == "SAFE_TO_CONTINUE"
        cache = e.shared_reads
        snap = await cache.acquire(account=True)
        cache.timeout_s = .1
        assert len(e.known) == len(v.post_requests) == 2
        return setup, companion, snap
    except BaseException:
        await finish(setup, companion)
        raise


def fresh_collections(snap):
    """Simulate continued complete heartbeat publication, as in this market."""
    updates = {r.name: replace(r, started_monotonic=time.monotonic()-.005,
                              completed_monotonic=time.monotonic())
               for r in snap.attempts if r.complete and not r.name.startswith("order:")}
    return replace(snap, attempts=tuple(updates.get(r.name, r) for r in snap.attempts),
                   last_good=tuple(updates.get(r.name, r) for r in snap.last_good))


@pytest.mark.parametrize("error", ["SYNC_ORDER_NOT_FOUND", "SYNC_ORDER_INVALID", "ReadTimeout"])
@pytest.mark.parametrize("keep_old_good", [False, True])
async def test_missing_order_does_not_block_other_proofs_and_recovers_without_resubmit(
    env, monkeypatch, tmp_path, error, keep_old_good
):
    setup, companion, snap = await two_closed_orders(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    cache = e.shared_reads
    valid, missing = sorted(e.known)
    name = "order:"+missing
    native = next(r for r in snap.last_good if r.name == name)
    failed = replace(native, complete=False, data_json=None, partial_json="[]",
                     error_type="ValueError" if error.startswith("SYNC_") else error,
                     error_code=error if error.startswith("SYNC_") else None, read_id="failed")
    broken = replace(snap, attempts=tuple(failed if r.name == name else r for r in snap.attempts),
                     last_good=tuple(r for r in snap.last_good if keep_old_good or r.name != name))
    latest = [broken]
    monkeypatch.setattr(companion.service, "latest", lambda: fresh_collections(latest[0]))
    unknown = next(a for a in e.attempts if a.order_id == missing)
    unknown.outcome, unknown.raw_response = "UNKNOWN", None
    e.invalidate("CLOSE_EVIDENCE_INVALIDATED", missing)
    try:
        assert not cache._ready(fresh_collections(broken), True, True)
        assert cache._ready(fresh_collections(broken), True, False)
        before = len(v.post_requests)
        for _ in range(2):
            assert not await e.verify_close(cancel=False)
            assert valid in e.proofs and valid in e.terminal
            assert missing not in e.proofs and missing in e.known
            assert missing in e.reason and error in e.reason
            assert unknown.outcome == "UNKNOWN" and e.committed() >= unknown.maximum
            assert not e.safe_to_continue and not e.processor.quarantined_orders
        assert len(v.post_requests) == before
        latest[0] = snap
        assert await e.verify_close(cancel=False), e.reason
        assert unknown.outcome == "VERIFIED" and e.safe_to_continue
        assert set(e.proofs) == e.known and e.committed() == 0
        assert len(v.post_requests) == before
        await e.journal.flush()
        events = [r for r in journal_records(e.journal.path)
                  if r["kind"] in {"sm_sync_detail_wait", "sm_sync_detail_recovered"}]
        assert [r["kind"] for r in events] == ["sm_sync_detail_wait", "sm_sync_detail_recovered"]
    finally:
        await finish(setup, companion)


@pytest.mark.parametrize("conflict", ["quantity", "references", "post_boundary"])
async def test_per_order_progress_keeps_original_detail_consistency_checks(
    env, monkeypatch, tmp_path, conflict
):
    setup, companion, snap = await two_closed_orders(env, monkeypatch, tmp_path)
    _, e = setup[:2]
    cache = e.shared_reads
    oid = sorted(e.known)[0]
    read = next(r for r in snap.last_good if r.name == "order:"+oid)
    data = read.data
    if conflict == "quantity":
        data["size_matched"] = 1
    elif conflict == "references":
        data["associate_trades"] = ["not-on-complete-trade-page"]
    else:
        cache.order_writes[oid] = read.started_monotonic+1
    bad = replace(read, data_json=json.dumps(data))
    broken = replace(snap, last_good=tuple(bad if r.name == read.name else r for r in snap.last_good),
                     attempts=tuple(bad if r.name == read.name else r for r in snap.attempts))
    monkeypatch.setattr(companion.service, "latest", lambda: fresh_collections(broken))
    try:
        assert not cache._ready(fresh_collections(broken), True, True)
        async with cache.scope("close", account=True, details=False):
            with pytest.raises(GatewayReadError, match="SYNC_DETAIL_NOT_READY"):
                await e.gateway.get_order(oid)
        assert not e.processor.quarantined_orders
        assert len(setup[0].post_requests) == 2
    finally:
        await finish(setup, companion)


async def test_missing_core_collection_and_inflight_write_still_block_close_scope(
    env, monkeypatch, tmp_path
):
    setup, companion, snap = await two_closed_orders(env, monkeypatch, tmp_path)
    _, e = setup[:2]
    cache = e.shared_reads
    trades = next(r for r in snap.attempts if r.name == "trades")
    failed = replace(trades, complete=False, data_json=None, partial_json="[]", read_id="failed-core")
    broken = replace(snap, attempts=tuple(failed if r.name == "trades" else r for r in snap.attempts))
    try:
        assert not cache._ready(broken, True, False)
        token = cache.begin_write("POST", ["inflight"])
        try:
            assert not cache._ready(snap, True, False)
        finally:
            cache.end_write(token)
        assert not cache._ready(snap, True, False)
    finally:
        await finish(setup, companion)


async def test_confirmed_inventory_survives_other_unknown_order_and_its_recovery(
    env, monkeypatch, tmp_path
):
    setup, companion, _ = await two_closed_orders(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    cache = e.shared_reads
    valid, missing = sorted(e.known)
    v.fill(valid, "late-confirmed", 5)
    reflect_positions(v)
    deadline = time.monotonic()+2
    while True:
        snap = await cache.acquire(account=True)
        trades = next(r.data for r in snap.last_good if r.name == "trades")
        if any(t["trade_id"] == "late-confirmed" for t in trades):
            break
        assert time.monotonic() < deadline
        await asyncio.sleep(.02)
    name = "order:"+missing
    native = next(r for r in snap.attempts if r.name == name)
    failed = replace(native, complete=False, data_json=None, partial_json="[]",
                     error_type="ValueError", error_code="SYNC_ORDER_NOT_FOUND", read_id="missing")
    broken = replace(snap, attempts=tuple(failed if r.name == name else r for r in snap.attempts))
    latest = [broken]
    monkeypatch.setattr(companion.service, "latest", lambda: fresh_collections(latest[0]))
    unknown = next(a for a in e.attempts if a.order_id == missing)
    unknown.outcome, unknown.raw_response = "UNKNOWN", None
    e.invalidate("CLOSE_EVIDENCE_INVALIDATED", missing)
    try:
        assert not await e.verify_close(cancel=False)
        assert e.economic_inventory() == (Decimal(5), Decimal(0))
        assert valid in e.proofs and missing not in e.proofs
        assert Decimal(str(e.proofs[valid]["order"]["size_matched"])) == 5
        cost = sum(Decimal(str(f.size))*Decimal(str(f.price)) for f in e.confirmed())
        assert e.committed() >= cost+unknown.maximum
        latest[0] = snap
        assert await e.verify_close(cancel=False), e.reason
        assert unknown.outcome == "VERIFIED"
        assert e.economic_inventory() == (Decimal(5), Decimal(0))
        assert e.committed() == cost and len(v.post_requests) == 2
    finally:
        await finish(setup, companion)


def test_null_detail_is_explicitly_unresolved_and_later_real_detail_recovers(sync_env):
    venue, _, reader, _ = sync_env
    reader.observed_ids = frozenset({"order-1"})
    venue.hook = lambda request: (httpx.Response(200, content=b"null",
                                                headers={"Content-Type": "application/json"})
                                 if request.url.path == "/data/order/order-1" else None)
    missing = reader.read("order:order-1", "market", lambda: reader.order("order-1"))
    assert not missing.complete and missing.error_code == "SYNC_ORDER_NOT_FOUND"
    assert missing.data is None and missing.error_type == "ValueError"
    venue.hook = None
    restored = reader.read("order:order-1", "market", lambda: reader.order("order-1"))
    assert restored.complete and restored.data["order_id"] == "order-1"
