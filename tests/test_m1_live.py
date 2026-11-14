"""Offline acceptance for the independent resting-order M1 engine."""

# ruff: noqa: ASYNC240 -- All filesystem assertions use isolated pytest temp files.

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from polymaker.cli import app
from polymaker.m1_live import M1Executor, M1Market
from polymaker.m1_live_config import M1LiveConfig
from polymaker.m1_live_signal import M1LiveSignals
from polymaker.m1_live_state import M1Ledger
from polymaker.rdp_sm_m1_signal import Opportunity
from test_rdp_sm_m1_strategy import env as env
from test_rdp_sm_m1_strategy import m1_execution, m1_feed, seed_opportunity
from test_rdp_sm_obi_execution import cleanup


def opportunity(i, price=".1", token="1"):
    return Opportunity(f"op{i}", token, "YES" if token == "1" else "NO", D(price),
                       1., 5., f"rf{i}", f"ex{i}", f"ap{i}", 1)


def ledger(tmp_path):
    return M1Ledger(tmp_path/"risk.sqlite3", {"account": "test", "market": "test"})


def observe(book, i, price=".1"):
    return book.observe(opportunity(i, price), minimum=5, elapsed=15)


def prepared(book, i, price=".1"):
    row = observe(book, i, price)
    assert book.reserve(row["id"], spendable=20)
    quote = {"side": "BUY", "token_id": "1", "price": price, "size": row["quantity"]}
    oid = "0x"+f"{i:064x}"
    book.prepare(row["id"], oid, quote)
    book.post_start(row["id"])
    return row


def test_cli_help_is_inert():
    result = CliRunner().invoke(app, ["m1-run", "--help"])
    assert result.exit_code == 0
    assert "--preflight-only" in result.stdout and "--once" in result.stdout


def test_settings_frozen_parameters_and_ca_isolation(tmp_path):
    from polymaker.config import Config

    original = Config.load("config")
    before = original.model_dump(mode="json", exclude={"secrets"})
    settings = M1LiveConfig()
    cfg = settings.execution_config(original)
    assert original.model_dump(mode="json", exclude={"secrets"}) == before
    assert cfg.paths.journal_dir == original.paths.journal_dir
    assert cfg.rdp_sm.normal_entry_mode == "m1_v1"
    assert not cfg.rdp_sm.sync_observe.use_for_execution
    assert not cfg.rdp_sm.ca_observe.enabled
    frozen = json.loads(
        (Path(__file__).parent / "fixtures/m1_signal_cal16_hold_v2.json").read_text()
    )
    from polymaker.config import RDPM1SignalConfig

    assert settings.signal == RDPM1SignalConfig.model_validate(frozen["rdp_sm"]["m1"]["signal"])
    (tmp_path/"m1.toml").write_text("order_budget_usdc = 5\n")
    with pytest.raises(ValueError):
        M1LiveConfig.load(tmp_path)


@pytest.mark.parametrize("price,quantity", [(".01", "100.0"), (".19", "5.3")])
def test_one_dollar_legal_target(tmp_path, price, quantity):
    book = ledger(tmp_path)
    try:
        r = observe(book, 1, price)
        assert D(r["quantity"]) == D(quantity)
        assert 1 <= D(r["target"]) < 1+D(price)*D('.1')
        assert book.committed() == 0  # Eligible denominator is not a reservation.
        assert book.metric()["signals"] == 1
    finally:
        book.close()


def test_atomic_five_reservations_under_contention(tmp_path):
    book = ledger(tmp_path)
    try:
        for i in range(30):
            observe(book, i)
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda i: book.reserve(f"op{i}", spendable=100), range(30)))
        assert sum(results) == 5
        assert book.committed() == 5
        assert book.metric()["signals"] == 30
        assert sum(r["reason"] == "SESSION_INSTRUCTION_LIMIT" for r in book.rows()) == 25
    finally:
        book.close()


def test_one_reservation_per_direction_is_atomic_under_contention(tmp_path):
    book = ledger(tmp_path)
    try:
        for i in range(30):
            book.observe(opportunity(i, token='1' if i % 2 else '2'), minimum=5, elapsed=15)
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda i: book.reserve(
                f'op{i}', spendable=100, direction_limit=1), range(30)))
        assert sum(results) == 2
        assert {r['outcome'] for r in book.rows() if r['state'] == 'RESERVED'} == {'YES', 'NO'}
        assert book.committed() == 2
        assert sum(r['reason'] == 'M1_DIRECTION_OPPORTUNITY_LIMIT' for r in book.rows()) == 28
    finally:
        book.close()


@pytest.mark.parametrize('state', ['ACK', 'UNKNOWN', 'REJECTED'])
def test_sent_direction_claim_survives_restart_even_after_rejection(tmp_path, state):
    book = ledger(tmp_path)
    prepared(book, 1)
    book.outcome('op1', state)
    book.close()
    book = ledger(tmp_path)
    try:
        observe(book, 2)
        assert not book.reserve('op2', spendable=20, direction_limit=1)
        assert book.row('op2')['reason'] == 'M1_DIRECTION_OPPORTUNITY_LIMIT'
        book.observe(opportunity(3, token='2'), minimum=5, elapsed=15)
        assert book.reserve('op3', spendable=20, direction_limit=1)
        if state == 'REJECTED':
            assert book.retry('op1', proof={'kind': 'REJECTED_RESPONSE'}, direction_limit=1)
        elif state == 'UNKNOWN':
            assert book.retry('op1', proof={'kind': 'UNCERTAIN_TIMEOUT'}, spendable=20, direction_limit=1)
            assert book.row('op1')['uncertain_retries'] == 1
            assert len(book.uncertain_posts()) == 1
    finally:
        book.close()


def test_unsent_aborted_direction_releases_claim_but_cannot_retry_over_new_root(tmp_path):
    book = ledger(tmp_path)
    try:
        observe(book, 1)
        assert book.reserve('op1', spendable=20, direction_limit=1)
        book.outcome('op1', 'ABORTED', 'M1_EVIDENCE_ENDED')
        observe(book, 2)
        assert book.reserve('op2', spendable=20, direction_limit=1)
        assert not book.retry('op1', proof={'kind': 'NOT_SENT'}, direction_limit=1)
    finally:
        book.close()


def test_historical_virtual_receipts_claim_only_their_direction(tmp_path):
    book = ledger(tmp_path)
    try:
        book.set('historical_orders', [{'id': 'old', 'outcome': 'YES', 'cost': '1'}])
        observe(book, 1)
        assert not book.reserve('op1', spendable=20, direction_limit=1)
        book.observe(opportunity(2, token='2'), minimum=5, elapsed=15)
        assert book.reserve('op2', spendable=20, direction_limit=1)
    finally:
        book.close()


def test_partial_unknown_closed_and_merge_never_credit_gross_spend(tmp_path):
    book = ledger(tmp_path)
    try:
        prepared(book, 1)
        book.reconcile("op1", quantity=2, cost=".2")
        assert book.committed() == 1
        assert D(book.metric()["attainment"]) == D(".2")
        assert book.metric()["pending"] == 1
        book.reconcile("op1", quantity=2, cost=".2", closed=True)
        assert book.committed() == D(".2")
        book.set("merged_collateral", 100)
        assert book.committed() == D(".2")
        with pytest.raises(ValueError, match="CONFLICT"):
            book.reconcile("op1", quantity=1, cost=".1", closed=True)
        book.close()
        book = ledger(tmp_path)
        assert book.committed() == D(".2")
        with pytest.raises(ValueError, match="ALREADY_STARTED"):
            book.outcome("op1", "ABORTED")
        with pytest.raises(ValueError, match="DUPLICATE_POST"):
            book.post_start("op1")
    finally:
        book.close()


def test_dedup_and_all_eligible_denominator(tmp_path):
    book = ledger(tmp_path)
    try:
        prepared(book, 1)
        assert observe(book, 1) is None
        assert book.observe(replace(opportunity(2), rf_id="rf1"), minimum=5, elapsed=15) is None
        observe(book, 3)
        book.outcome("op3", "SKIPPED", "NO_FUNDS")
        book.reconcile("op1", quantity=10, cost=1, closed=True)
        assert D(book.metric()["attainment"]) == D(".5")
        assert book.metric()["signals"] == 2
        assert book.metric(winner="NO")["attainment"] is None
    finally:
        book.close()


def test_live_signal_keeps_real_depth_and_allows_new_evidence_at_same_queue():
    f, _ = m1_feed()
    f.m1 = M1LiveSignals(f)
    seed_opportunity(f)
    f.m1.evaluate(f.snapshot())
    ep = f.m1.selected
    assert f.m1.take(ep)
    assert not f.m1.own_active
    assert f.m1.states["1"]["ex"] is not None
    assert f.m1.states["1"]["rf"] is not None
    seed_opportunity(f)
    f.m1.evaluate(f.snapshot())
    second = f.m1.selected
    assert second.anchor_bid_price == ep.anchor_bid_price
    assert second.rf_id != ep.rf_id and second.ex_id != ep.ex_id
    assert f.m1.take(second)


async def live_setup(env, monkeypatch, tmp_path, price=".05"):
    setup = await m1_execution(env, monkeypatch)
    v, e, f = setup[:3]
    e.__class__ = M1Executor
    e.ledger = ledger(tmp_path)
    e.processor.price_tolerance = D(".000001")
    with f.lock:
        f.books["1"] = replace(f.books["1"], bids=((D(price), D(100)),), asks=((D(price)+D('.01'), D(100)),),
                               reported_bid=None, reported_ask=None)
        f.books["2"] = replace(f.books["2"], bids=((D(".94"), D(100)),), asks=((D(".95"), D(100)),),
                               reported_bid=None, reported_ask=None)
    market = M1Market(e, M1LiveConfig(post_budget_floor_ms=10), asyncio.Event(), display=lambda x: None)
    await market.refresh_account()
    assert market.account_reason is None, market.account_reason
    seed_opportunity(f)
    market.collect()
    assert len(market.pending) == 1
    return setup, market


async def close_setup(setup, market):
    await market.drain()
    await cleanup(setup)
    market.ledger.close()


async def test_signal_recording_failure_stays_latched_and_visible_in_heartbeat(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    venue, _, feed = setup[:3]
    heartbeats = []
    market.display = heartbeats.append
    try:
        market.heartbeat()
        assert heartbeats[-1].get("signal_failure") is None
        assert heartbeats[-1].get("reason") is None
        # Reproduce a recorder refusing an event while its writer is under pressure.
        feed.m1.record = lambda row: False
        feed.m1.emit("UNAVAILABLE", token_id="1", reason="M1_PROCESSING_GAP")
        assert feed.m1.failure == "M1_EVENT_RECORDING_FAILED"
        feed.m1.record = lambda row: True
        market.heartbeat()
        assert heartbeats[-1]["signal_failure"] == "M1_EVENT_RECORDING_FAILED"
        assert heartbeats[-1]["reason"] == "M1_EVENT_RECORDING_FAILED"
        # Account recovery and diagnostic output must never clear the signal guard.
        assert market.account_reason is None
        assert feed.m1.active_reason((), None) == "M1_EVENT_RECORDING_FAILED"
        assert not venue.post_requests and not venue.cancels
    finally:
        await close_setup(setup, market)


async def test_batched_journal_still_blocks_native_post_until_fsynced(env, monkeypatch, tmp_path):
    import os
    from threading import Event

    from polymaker.m1_live_journal import M1RiskJournal
    from polymaker.rdp_sm_writer import AsyncRiskWriter

    setup, market = await live_setup(env, monkeypatch, tmp_path)
    venue, executor = setup[:2]
    entered, release = Event(), Event()
    task = None
    try:
        await executor.journal.flush()
        executor.journal.close()
        executor.session_id = 'm1_live_' + 'b' * 32
        backing = M1RiskJournal(tmp_path/'batched', '0x'+'1'*40,
                                session_id=executor.session_id)
        executor.journal = AsyncRiskWriter(backing, batch_size=128)
        sync = os.fsync

        def delayed_sync(fd):
            entered.set()
            assert release.wait(3)
            sync(fd)

        monkeypatch.setattr(os, 'fsync', delayed_sync)
        ep = next(iter(market.pending.values()))
        task = asyncio.create_task(market.submit(ep))
        assert await asyncio.to_thread(entered.wait, 3)
        await asyncio.sleep(.01)
        assert not task.done() and not venue.post_requests
        assert market.ledger.row(ep.episode_id)['posts'] == 0
        release.set()
        await asyncio.wait_for(task, 3)
        assert market.ledger.row(ep.episode_id)['state'] == 'ACK'
        assert len(venue.post_requests) == 1 and not venue.cancels
    finally:
        release.set()
        if task is not None:
            await task
        await close_setup(setup, market)


async def test_real_sdk_resting_orders_do_not_cancel_on_signal_end(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e, f = setup[:3]
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        assert len(v.post_requests) == 1
        row = market.ledger.row(ep.episode_id)
        assert row["state"] == "ACK", row
        assert not v.cancels
        assert D(row["quantity"]) == 20 and market.ledger.committed() == 1
        body = v.post_requests[0][0]
        assert body["postOnly"] is True and body["orderType"] == "GTC"
        assert body["order"]["makerAmount"] == "1000000"
        assert body["order"]["takerAmount"] == "20000000"
        # Ordinary expiry never invokes cancellation or closes the ledger.
        f.m1.invalidate("M1_OPPORTUNITY_EXPIRED")
        market.stop.set()
        await market.drain()
        assert not v.cancels and not market.ledger.row(ep.episode_id)["closed"]
    finally:
        await close_setup(setup, market)


async def test_native_guard_expiry_does_not_post_or_reserve_forever(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e, f = setup[:3]
    original = e.gateway.prepare

    async def delayed(*args, **kwargs):
        signed = await original(*args, **kwargs)
        f.m1.invalidate("M1_OPPORTUNITY_EXPIRED")
        return signed

    monkeypatch.setattr(e.gateway, "prepare", delayed)
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        assert not v.post_requests and not v.cancels
        assert market.ledger.committed() == 0
        assert market.ledger.row(ep.episode_id)["state"] == "ABORTED"
    finally:
        await close_setup(setup, market)


async def test_run_can_post_from_fresh_cache_while_account_refresh_is_in_flight(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    entered, release, posted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = e.gateway.balance_allowance
    loop = asyncio.get_running_loop()

    async def delayed_positions():
        entered.set()
        await release.wait()
        return await original()

    def on_post():
        loop.call_soon_threadsafe(posted.set)
        loop.call_soon_threadsafe(market.stop.set)

    monkeypatch.setattr(e.gateway, "balance_allowance", delayed_positions)
    monkeypatch.setattr(market, "restore", lambda: None)
    setup[5].delay = on_post
    market.account_task = asyncio.create_task(market.refresh_account())
    runner = None
    try:
        await entered.wait()
        assert market.account_current()
        runner = asyncio.create_task(market.run())
        await asyncio.wait_for(posted.wait(), timeout=1)
        assert not release.is_set() and len(v.post_requests) == 1
        release.set()
        assert await runner is False  # Manual stop retains the real order.
        assert not v.cancels and market.ledger.committed() == 1
    finally:
        market.stop.set()
        release.set()
        if runner is not None:
            await runner
        await close_setup(setup, market)


async def test_background_refresh_does_not_extend_a_stale_account_snapshot(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    entered, release = asyncio.Event(), asyncio.Event()
    original = e.gateway.balance_allowance

    async def delayed_positions():
        entered.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(e.gateway, "balance_allowance", delayed_positions)
    market.account_at -= market.settings.account_max_age_sec+1
    market.account_task = asyncio.create_task(market.refresh_account())
    try:
        await entered.wait()
        ep = next(iter(market.pending.values()))
        assert not market.account_current()
        await market.submit(ep)
        assert not v.post_requests and market.ledger.committed() == 0
        release.set()
        await market.account_task
        assert market.account_current()
        await market.submit(ep)
        assert len(v.post_requests) == 1
    finally:
        release.set()
        await close_setup(setup, market)


async def test_account_refresh_reads_only_cash_and_allowance(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    e = setup[1]
    calls = []
    async def forbidden(*args, **kwargs):
        pytest.fail("M1 tried to read order/trade/position evidence")
    for name in ("positions", "open_orders", "get_trades", "get_order"):
        monkeypatch.setattr(e.gateway, name, forbidden)
    original = e.gateway.balance_allowance
    async def counted():
        calls.append("cash")
        return await original()
    monkeypatch.setattr(e.gateway, "balance_allowance", counted)
    # Obsolete pagination errors do not gate the cash-only M1 lifecycle.
    e.gateway._client._pagination_failures[("trades", "old")] = {}
    try:
        await market.refresh_account()
        assert market.account_reason is None
        assert calls == ["cash"]
        await market.submit(next(iter(market.pending.values())))
        await market.refresh_account()
        assert len(setup[0].post_requests) == 1
    finally:
        await close_setup(setup, market)


@pytest.mark.parametrize("age,allowed", [(.005, True), (.019, True), (.021, False), (.1, False)])
async def test_native_guard_distinguishes_millisecond_queueing_from_input_backlog(env, monkeypatch, tmp_path, age, allowed):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    e, feed = setup[1:3]
    try:
        ep = next(iter(market.pending.values()))
        with feed.lock:
            feed.m1_pending_received.append(e.monotonic()-age)
            assert market.guard(ep) is allowed
            if not allowed:
                assert market.guard_reason == "M1_SUBMIT_INPUT_BACKLOG"
            feed.m1_pending_received.clear()
    finally:
        await close_setup(setup, market)


async def test_real_sdk_can_post_with_a_fresh_frame_waiting_to_apply(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    venue, e, feed = setup[:3]
    original = e.gateway.prepare

    async def with_pending_frame(*args, **kwargs):
        signed = await original(*args, **kwargs)
        with feed.lock:
            feed.m1_pending_received.append(e.monotonic()-.001)
        return signed

    monkeypatch.setattr(e.gateway, "prepare", with_pending_frame)
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        assert len(venue.post_requests) == 1
        assert market.ledger.row(ep.episode_id)["state"] == "ACK"
        assert market.ledger.committed() == 1
    finally:
        feed.m1_pending_received.clear()
        await close_setup(setup, market)


async def test_second_same_direction_signal_is_recorded_but_not_posted(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e, f = setup[:3]
    try:
        first = next(iter(market.pending.values()))
        market.pending.clear()
        await market.submit(first)
        assert len(v.post_requests) == 1
        await market.refresh_account()
        assert market.account_reason is None, market.account_reason
        await asyncio.sleep(.02)
        seed_opportunity(f)
        market.collect()
        assert len(market.pending) == 1
        second = next(iter(market.pending.values()))
        assert second.rf_id != first.rf_id and second.ex_id != first.ex_id
        await market.submit(second)
        assert len(v.post_requests) == 1
        assert not v.cancels
        assert market.ledger.committed() == 1
        assert len(e.known) == 1
        assert market.ledger.row(second.episode_id)['reason'] == 'M1_DIRECTION_OPPORTUNITY_LIMIT'
        assert market.ledger.metric()['signals'] == 2
    finally:
        await close_setup(setup, market)


async def test_lost_ack_retries_once_without_reconciliation(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    setup[5].fail_ack = True
    async def forbidden(*args, **kwargs):
        pytest.fail("M1 attempted post-send reconciliation")
    for name in ("positions", "open_orders", "get_trades", "get_order"):
        monkeypatch.setattr(e.gateway, name, forbidden)
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        await market.refresh_account()
        monkeypatch.setattr(e.reads.network, "snapshot", lambda *args: SimpleNamespace(allowed=True, reason="HEALTHY"))
        await market.submit(ep)
        row = market.ledger.row(ep.episode_id)
        assert row["state"] == "UNKNOWN" and row["started"]
        assert row["order_id"] in e.known and len(e.known) == 2
        assert row["uncertain_retries"] == 1
        assert market.ledger.committed() == 2
        assert market.account_reason is None
        await market.submit(ep)
        assert len(v.post_requests) == 2 and not v.cancels
        assert market.ledger.metric()["attainment"] is None
    finally:
        await close_setup(setup, market)


async def test_ctrl_c_drains_native_post_and_keeps_the_order(env, monkeypatch, tmp_path):
    import threading

    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    wire = setup[5]
    entered, release = threading.Event(), threading.Event()

    def hold_native():
        entered.set()
        assert release.wait(5)

    wire.delay = hold_native
    try:
        ep = next(iter(market.pending.values()))
        market.post_task = asyncio.create_task(market.submit(ep))
        assert await asyncio.to_thread(entered.wait, 2)
        market.stop.set()
        draining = asyncio.create_task(market.drain())
        await asyncio.sleep(.01)
        assert not draining.done()
        release.set()
        await draining
        assert len(v.post_requests) == 1 and not v.cancels
        assert market.ledger.row(ep.episode_id)["state"] == "ACK"
        assert market.ledger.committed() == 1
    finally:
        release.set()
        await close_setup(setup, market)


async def test_restart_registers_exact_id_without_reposting(env, monkeypatch, tmp_path):
    from polymaker.rdp_sm_execution import _SMProcessor
    from polymaker.state.store import StateStore

    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e, f = setup[:3]
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        original_id = market.ledger.row(ep.episode_id)["order_id"]
        e.store.close()
        e.store = StateStore(":memory:")
        e.processor = _SMProcessor(e, e.store)
        e.known.clear()
        e.attempts.clear()
        e.gateway._client.known_ids.clear()
        market.ledger.close()
        market.ledger = e.ledger = ledger(tmp_path)
        resumed = M1Market(e, market.settings, asyncio.Event(), display=lambda x: None)
        resumed.restore()
        assert e.known == {original_id}
        assert len(e.attempts) == 1 and e.attempts[0].post_started
        assert resumed.ledger.committed() == 1
        assert not resumed.pending
        assert len(v.post_requests) == 1 and not v.cancels
        await resumed.refresh_account()
        assert resumed.account_reason is None
        assert resumed.ledger.committed() == 1
        assert resumed.ledger.metric()["attainment"] is None
    finally:
        await close_setup(setup, market)


async def test_end_market_preserves_orders_for_platform_without_merge(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        # Simulate a new boot at T300: restore must not repeat a submission.
        e.known.clear()
        e.attempts.clear()
        e.gateway._client.known_ids.clear()
        monkeypatch.setattr(market, "elapsed", lambda: 300.)
        assert await market.run()
        assert not v.cancels
        assert market.ledger.get("complete") is True
        assert len(v.post_requests) == 1
        assert market.ledger.metric()["attainment"] is None
        assert market.ledger.get("settlement_policy") == "PLATFORM_AUTO_SETTLEMENT"
        assert market.ledger.get("settlement_status") == "AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED"
    finally:
        await close_setup(setup, market)


@pytest.mark.parametrize("lost_ack", [False, True])
async def test_t300_only_records_completion_without_order_actions(env, monkeypatch, tmp_path, lost_ack):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    venue, executor = setup[:2]

    async def forbidden(*args, **kwargs):
        pytest.fail("M1 performed a market-end order action or reconciliation")

    try:
        setup[5].fail_ack = lost_ack
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        if lost_ack:
            monkeypatch.setattr(executor.reads.network, "snapshot", lambda *args: SimpleNamespace(allowed=True, reason="HEALTHY"))
            await market.submit(ep)
        before = market.ledger.rows()
        assert before[0]["state"] == ("UNKNOWN" if lost_ack else "ACK")
        for name in ("positions", "open_orders", "get_trades", "get_order", "cancel",
                     "cancel_detailed", "cancel_all", "cancel_asset", "cancel_market_asset", "send_prepared"):
            monkeypatch.setattr(executor.gateway, name, forbidden)
        monkeypatch.setattr(market, "elapsed", lambda: 300.)
        assert await market.run()
        assert market.ledger.get("complete") is True
        assert market.ledger.get("remaining_positions") is None
        assert market.ledger.get("settlement_status") == "AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED"
        assert market.ledger.get("market_end_actions") == "PLATFORM_MANAGED"
        assert market.ledger.get("cancel_request_status") is None
        assert market.ledger.rows() == before
        assert market.ledger.committed() == (2 if lost_ack else 1)
        assert not market.ledger.events("sm_merge_")
        assert len(venue.post_requests) == (2 if lost_ack else 1) and not venue.cancels
        market.complete_window()  # Repeated local completion has no order side effects.
    finally:
        await close_setup(setup, market)


async def test_m1_keeps_historical_merge_records_without_reconciling_them(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    try:
        with pytest.raises(RuntimeError, match="M1_MERGE_DISABLED"):
            setup[1].record("sm_merge_intent", {"merge_id": "new"})
        market.ledger.event("sm_merge_intent", {"merge_id": "historical"}, 1)
        market.ledger.event("sm_merge_result", {"merge_id": "historical", "status": "UNKNOWN"}, 2)
        market.restore()
        assert len(market.ledger.events("sm_merge_")) == 2
        assert not setup[0].post_requests
    finally:
        await close_setup(setup, market)


async def test_fill_tracking_is_absent_from_live_executor(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    old = setup[1]
    fresh = M1Executor(old.cfg, old.slug, old.session_id, old.reads, old.journal, ledger=market.ledger)
    try:
        for name in ("processor", "verify_close", "_verify_order", "confirmed", "economic_inventory", "proofs"):
            assert not hasattr(fresh, name)
        assert not issubclass(M1Executor, __import__("polymaker.rdp_sm_execution", fromlist=["SMWindowExecutor"]).SMWindowExecutor)
    finally:
        await close_setup(setup, market)


async def test_external_fill_does_not_enter_m1_ledger_or_change_admission(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v = setup[0]
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        oid = market.ledger.row(ep.episode_id)["order_id"]
        v.fill(oid, qty=2)
        await market.refresh_account()
        assert market.account_reason is None
        assert market.ledger.committed() == 1
        assert market.ledger.metric()["attainment"] is None
        assert market.ledger.metric()["by_signal"][0]["filled"] is None
        assert not market.ledger.facts("confirmed_fill")
        with pytest.raises(ValueError, match="M1_FILL_TRACKING_DISABLED"):
            market.ledger.reconcile(ep.episode_id, quantity=2, cost=".1")
        assert not v.cancels
    finally:
        await close_setup(setup, market)


@pytest.mark.parametrize("bid,ask,expected_price,quantity", [
    (".06", ".07", None, None), (".04", ".05", ".04", "25"),
])
async def test_definite_failure_retries_one_signal_after_expiry_at_original_or_cheaper_price(
    env, monkeypatch, tmp_path, bid, ask, expected_price, quantity,
):
    from test_rdp_sm_repair import reflect_positions

    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e, f = setup[:3]
    wire = setup[5]
    try:
        ep = next(iter(market.pending.values()))
        wire.reject_index = 0
        await market.submit(ep)
        first = market.ledger.row(ep.episode_id)
        assert first["state"] == "RESERVED" and first["posts"] == 1
        assert market.ledger.committed() == 1
        assert not e.known
        wire.reject_index = None
        f.m1.invalidate("M1_OPPORTUNITY_EXPIRED")
        with f.lock:
            f.books["1"] = replace(f.books["1"], bids=((D(bid), D(100)),),
                                   asks=((D(ask), D(100)),), reported_bid=None, reported_ask=None)
        await market.submit(ep)
        row = market.ledger.row(ep.episode_id)
        if expected_price is None:
            assert row["state"] == "RESERVED" and row["posts"] == 1
            assert len(v.post_requests) == 1
            assert market.quote_price(ep) is None
            return
        assert row["state"] == "ACK" and row["posts"] == 2
        assert len(v.post_requests) == 2
        assert len(e.known) == 1 and market.ledger.committed() == 1
        q = json.loads(row["quote"])
        assert D(str(q["price"])) == D(expected_price) and D(str(q["size"])) == D(quantity)
        assert D(row["price"]) == D(".05")  # Original evidence is immutable.
        assert market.ledger.metric()["signals"] == 1
        assert market.ledger.metric()["native_posts"] == 2
        assert all(r[0]["postOnly"] and r[0]["orderType"] == "GTC" for r in v.post_requests)
        v.fill(row["order_id"], qty=float(quantity))
        v.rows[row["order_id"]]["status"] = "MATCHED"
        reflect_positions(v)
        await market.refresh_account()
        assert market.ledger.metric()["attainment"] is None
        assert market.ledger.metric()["by_signal"][0]["attainment"] is None
        # Re-reading the same signal after success cannot create a third order.
        await market.submit(ep)
        assert len(v.post_requests) == 2
    finally:
        await close_setup(setup, market)


async def test_uncertain_retry_cannot_pass_cutoff_and_keeps_first_order_risk(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    try:
        ep = next(iter(market.pending.values()))
        setup[5].fail_ack = True
        await market.submit(ep)
        assert market.ledger.row(ep.episode_id)["state"] == "RESERVED"
        assert market.ledger.committed() == 2
        monkeypatch.setattr(e.reads.network, "snapshot", lambda *args: SimpleNamespace(allowed=True, reason="HEALTHY"))
        monkeypatch.setattr(market, "elapsed", lambda: 240.)
        await market.submit(ep)
        assert len(v.post_requests) == 1
        assert market.ledger.row(ep.episode_id)["state"] == "ABORTED"
        assert market.ledger.committed() == 1  # The original uncertain POST survives.
        assert len(market.ledger.uncertain_posts()) == 1
        assert market.ledger.metric()["signals"] == 1
    finally:
        await close_setup(setup, market)


async def test_known_rejection_waits_without_holding_up_other_signals_and_aborts_at_t240(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    wire = setup[5]
    try:
        ep = next(iter(market.pending.values()))
        wire.reject_index = 0
        await market.submit(ep)
        assert ep.episode_id in market.pending and market.retry_after[ep.episode_id] > e.monotonic()
        assert market.ledger.committed() == 1
        monkeypatch.setattr(market, "elapsed", lambda: 240.)
        await market.submit(ep)
        assert len(v.post_requests) == 1
        assert market.ledger.row(ep.episode_id)["state"] == "ABORTED"
        assert market.ledger.committed() == 0
        assert market.ledger.metric()["signals"] == 1
        assert market.ledger.metric()["attainment"] is None
    finally:
        await close_setup(setup, market)


async def test_restart_resumes_a_proven_failed_task_without_new_signal_or_new_budget(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e, f = setup[:3]
    wire = setup[5]
    try:
        ep = next(iter(market.pending.values()))
        wire.reject_index = 0
        await market.submit(ep)
        market.stop.set()
        market.finish_pending()
        assert market.ledger.row(ep.episode_id)["state"] == "RESERVED"
        market.ledger.close()
        market.ledger = e.ledger = ledger(tmp_path)
        resumed = M1Market(e, market.settings, asyncio.Event(), display=lambda x: None)
        resumed.restore()
        assert ep.episode_id in resumed.pending
        assert resumed.ledger.committed() == 1
        assert not f.m1.states  # Fresh evidence is not manufactured for this retry.
        wire.reject_index = None
        await resumed.refresh_account()
        retry_book(resumed, ".04", ".06")  # Restart retains strictly-downward repricing.
        await resumed.submit(resumed.pending[ep.episode_id])
        assert len(v.post_requests) == 2
        assert resumed.ledger.metric()["signals"] == 1
        assert resumed.ledger.row(ep.episode_id)["state"] == "ACK"
    finally:
        await close_setup(setup, market)


@pytest.mark.parametrize("status,state", [(425, "RESERVED"), (429, "RESERVED"), (500, "RESERVED")])
async def test_real_sdk_http_failures_only_rearm_definite_rejections(env, monkeypatch, tmp_path, status, state):
    import httpx

    from test_rdp_sm_entry import boundary

    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v = setup[0]
    wire = setup[5]
    requests = []

    def failed(request):
        if request.method == "POST":
            requests.append(json.loads(request.content))
            return httpx.Response(status, json={"error": "temporary server error"}, request=request)
        return wire(request)

    client = boundary(monkeypatch, v, failed)
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        assert market.ledger.row(ep.episode_id)["state"] == state
        assert market.ledger.row(ep.episode_id)["posts"] == 1
        assert market.ledger.committed() == (2 if status == 500 else 1)
        assert market.ledger.row(ep.episode_id)["uncertain_retries"] == (1 if status == 500 else 0)
        assert len(requests) == 1
    finally:
        await close_setup(setup, market)
        client.close()


async def test_full_runner_ignores_account_mutex_and_other_strategy_fences(env, monkeypatch):
    from pathlib import Path

    from polymaker.m1_live import run_m1
    from polymaker.rdp_sm import _market
    from polymaker.rdp_sm_fence import AccountLease, fence_path
    from test_rdp_sm_m1 import SLUG, START
    v, cfg = env
    meta, _ = _market(v.raw, SLUG)
    v.time = START+300
    fence = fence_path(cfg, v.funder, meta.condition_id)
    fence.parent.mkdir(parents=True, exist_ok=True)
    fence.write_text(json.dumps({"strategy": "ca_v1", "status": "UNRESOLVED"}))
    # Hold the real legacy mutex while M1 completes its own local window.
    lease = AccountLease(cfg, v.funder)
    async def ended_market(_):
        return meta, START, START+300, {}
    monkeypatch.setattr("polymaker.m1_live.discover_current_market", ended_market)
    try:
        result = await asyncio.wait_for(run_m1(cfg, once=True, display=lambda x: None), 5)
        assert result["status"] == "COMPLETE"
        assert len(list(Path(cfg.paths.journal_dir).glob("m1_live_*.sqlite3"))) == 1
        assert json.loads(fence.read_text()) == {"strategy": "ca_v1", "status": "UNRESOLVED"}
        again = await run_m1(cfg, once=True, display=lambda x: None)
        assert again["status"] == "MARKET_ALREADY_COMPLETE"
        assert not v.post_requests and not v.cancels
    finally:
        lease.close()


async def test_readonly_preflight_creates_no_trade_ledger_or_fence(env, monkeypatch):
    from pathlib import Path

    from polymaker.m1_live import run_m1
    from polymaker.rdp_sm import _market
    from test_rdp_sm_m1 import SLUG, START

    v, cfg = env
    meta, _ = _market(v.raw, SLUG)

    async def selected(_):
        return meta, START, START+60, {}

    monkeypatch.setattr("polymaker.m1_live.discover_current_market", selected)
    result = await run_m1(cfg, preflight_only=True, display=lambda x: None)
    assert result["status"] == "READ_ONLY"
    assert not v.post_requests and not v.cancels
    assert not list(Path(cfg.paths.journal_dir).glob("m1_live_*.sqlite3"))
    assert not list(Path(cfg.paths.journal_dir).glob("rdp_obi_market_*.json"))


def test_recovery_rejects_changed_identity_or_parameters(tmp_path):
    book = ledger(tmp_path)
    prepared(book, 1)
    book.close()
    with pytest.raises(ValueError, match="IDENTITY_OR_SETTINGS_CHANGED"):
        M1Ledger(tmp_path/"risk.sqlite3", {"account": "another", "market": "test"})
    book = ledger(tmp_path)
    assert book.committed() == 1
    book.close()


def test_sent_order_cannot_be_skipped_to_free_its_reservation(tmp_path):
    book = ledger(tmp_path)
    try:
        prepared(book, 1)
        book.outcome("op1", "ACK")
        with pytest.raises(ValueError, match="CANNOT_SKIP"):
            book.outcome("op1", "SKIPPED")
        with pytest.raises(ValueError, match="RESULT_CONFLICT"):
            book.outcome("op1", "REJECTED")
        assert book.committed() == 1
    finally:
        book.close()


def test_risk_attainment_uses_legal_rounded_target(tmp_path):
    book = ledger(tmp_path)
    try:
        prepared(book, 1, ".19")
        book.reconcile("op1", quantity="5.3", cost="1.007", closed=True)
        metric = book.metric()
        assert D(metric["attainment"]) == 1
        assert D(metric["quantity_attainment"]) == 1
        assert D(metric["by_signal"][0]["attainment"]) == 1
        assert D(metric["by_signal"][0]["target_usdc"]) == D('1.007')
    finally:
        book.close()


def test_retry_does_not_charge_or_release_an_extra_dollar_at_full_budget(tmp_path):
    book = ledger(tmp_path)
    try:
        for i in range(5):
            prepared(book, i)
        assert book.committed() == 5
        book.outcome("op1", "REJECTED")
        assert book.retry("op1", proof={"kind": "REJECTED_RESPONSE", "message": "post only"})
        assert book.committed() == 5
        observe(book, 21)
        assert not book.reserve("op21", spendable=100)
        assert book.metric()["signals"] == 6
        assert book.row("op1")["posts"] == 1
    finally:
        book.close()


@pytest.mark.parametrize("raw,can_retry", [
    ({"success": False, "errorMsg": "post only"}, True),
    ({"success": False, "errorMsg": "not enough balance / allowance"}, True),
    ({"success": False, "errorMsg": "invalid signature"}, False),
    ({"success": False, "errorMsg": "order is invalid. Duplicated."}, None),
    ({"success": False, "errorMsg": "post only", "tradeIDs": ["a"]}, None),
    ({"success": False, "errorMsg": "context canceled"}, None),
])
def test_retry_classifies_only_definitive_rejections(raw, can_retry):
    from polymaker.m1_live_retry import rejection

    proof = rejection(raw, D(1))
    assert (proof["retryable"] if proof else None) == can_retry


def test_market_race_minimum_notional_rejection_can_retry_as_maker():
    from polymaker.m1_live_retry import rejection

    raw = {"success": True, "orderID": "", "status": "", "makingAmount": "", "takingAmount": "",
           "errorMsg": "invalid amount for a marketable BUY order ($0.9994), min size: $1"}
    proof = rejection(raw, D(".9994"))
    assert proof and proof["retryable"]


def test_durable_journal_resumes_same_market_and_keeps_all_complete_facts(tmp_path):
    from pathlib import Path

    from polymaker.m1_live_journal import M1RiskJournal

    sid, funder = "m1_live_"+"1"*32, "0x"+"1"*40
    journal = M1RiskJournal(tmp_path, funder, session_id=sid)
    journal.write("sm_session", {"session_id": sid, "payload": {"strategy": "m1_live_v1"}}, 1.)
    path = Path(journal.path)
    journal.close()
    with path.open("ab") as out:
        out.write(b'{"ts":')
    resumed = M1RiskJournal(tmp_path, funder, session_id=sid, resume=True)
    resumed.write("sm_m1_state", {"session_id": sid, "payload": {"state": "OPEN"}}, 2.)
    resumed.close()
    facts = [json.loads(line) for line in path.read_text().splitlines()]
    assert [fact["kind"] for fact in facts] == ["sm_session", "sm_m1_state"]
    assert all(fact["data"]["session_id"] == sid for fact in facts)


async def test_async_journal_sequence_survives_restart_without_dashboard_duplicates(tmp_path):
    from pathlib import Path

    from polymaker.m1_live_journal import M1RiskJournal
    from polymaker.rdp_sm_writer import AsyncRiskWriter

    sid, funder = "m1_live_"+"1"*32, "0x"+"1"*40
    backing = M1RiskJournal(tmp_path, funder, session_id=sid)
    writer = AsyncRiskWriter(backing)
    writer.write("sm_m1_state", {"session_id": sid, "payload": {"state": "OPEN"}}, 1.)
    await writer.flush()
    writer.close()
    backing = M1RiskJournal(tmp_path, funder, session_id=sid, resume=True)
    assert backing.last_writer_sequence == 1
    writer = AsyncRiskWriter(backing)
    writer.accepted = writer.written = backing.last_writer_sequence
    writer.write("sm_m1_state", {"session_id": sid, "payload": {"state": "RETAINED"}}, 2.)
    await writer.flush()
    writer.close()
    rows = [json.loads(line) for line in Path(backing.path).read_text().splitlines()]
    assert [row["data"]["writer_sequence"] for row in rows] == [1, 2]


async def test_continuous_runner_waits_only_for_own_completed_market(env, monkeypatch):
    from polymaker.m1_live import run_m1
    from polymaker.rdp_sm import _market
    from test_rdp_sm_m1 import SLUG, START
    v, cfg = env
    meta, _ = _market(v.raw, SLUG)
    v.time = START+300
    async def selected(_):
        return meta, START, START+300, {}
    monkeypatch.setattr("polymaker.m1_live.discover_current_market", selected)
    assert (await run_m1(cfg, once=True, display=lambda x: None))["status"] == "COMPLETE"
    stop, messages = asyncio.Event(), []
    def display(payload):
        messages.append(payload)
        stop.set()
    result = await run_m1(cfg, stop=stop, display=display)
    assert result["status"] == "STOPPED"
    assert messages[0]["reason"] == "M1_SUBMISSION_WINDOW_ALREADY_COMPLETE"
    assert not v.post_requests and not v.cancels


def test_dashboard_shows_durable_m1_progress():
    from io import StringIO

    from rich.console import Console

    from polymaker.dashboard.m1_view import render_m1
    from polymaker.dashboard.model import summarize
    from test_dashboard import SID, event, source

    progress = {"signals": {"signals": 4, "attainment": ".25", "pending": 1,
                            "committed_usdc": "2"}}
    result = summarize(SID, [event("sm_session", {"slug": "btc-updown-5m-1800000000",
                       "normal_entry_mode": "m1_v1"}), event("sm_m1_heartbeat", progress)], [source()])
    assert result["m1_progress"] == progress
    output = StringIO()
    Console(file=output, width=100).print(render_m1(
        {"observed_at": result["market_start"]}, result, [result],
        now=result["market_start"]+100, width=100, height=32, hours=1,
        archive_view=False, archive_page=0))
    assert "25.00%" in output.getvalue() and "目标100%" in output.getvalue()


async def test_ca_recovery_cannot_cancel_retained_m1_orders(tmp_path):
    from polymaker.config import Config
    from polymaker.rdp_sm_fence import AccountLease, fence_path, recover_fence

    cfg = Config.load("config")
    cfg.paths.journal_dir = str(tmp_path)
    funder = "0x"+"1"*40
    path = fence_path(cfg, funder, "old-market")
    path.write_text(json.dumps({"strategy": "m1_live_v1"}))
    lease = AccountLease(cfg, funder)
    lease.activate(path, "test")
    lease.write({**lease.state, "strategy": "m1_live_v1"})
    reads = SimpleNamespace(lease=lease, cfg=cfg, gateway=SimpleNamespace(funder=funder))
    try:
        with pytest.raises(Exception, match="M1_OWNER_REQUIRES_M1_RUN_RESUME"):
            await recover_fence(reads, SimpleNamespace(condition_id="new-market"))
    finally:
        lease.close()


@pytest.mark.parametrize("raw,spenders", [
    ({"balance": "NaN", "allowances": {"spender": "1000000"}}, ("spender",)),
    ({"balance": "-1", "allowances": {"spender": "1000000"}}, ("spender",)),
    ({"balance": "0.1", "allowances": {"spender": "1000000"}}, ("spender",)),
    ({"balance": "1000000", "allowances": {}}, ("spender",)),
    ({"balance": "1000000", "allowances": {"spender": "Infinity"}}, ("spender",)),
    ({"balance": "1000000", "allowances": {"spender": "1000000", "SPENDER": "2000000"}}, ("spender",)),
    ({"balance": "1000000", "allowances": {"spender": "1000000"}}, ()),
])
async def test_cash_only_snapshot_still_rejects_invalid_money(raw, spenders):
    from decimal import InvalidOperation

    from polymaker.m1_live_account import account_snapshot

    async def cash():
        return raw

    async def applicable(meta):
        return spenders

    reads = SimpleNamespace(gateway=SimpleNamespace(balance_allowance=cash,
        required_collateral_spenders=applicable))
    with pytest.raises((ValueError, InvalidOperation)):
        await account_snapshot(reads, SimpleNamespace())


def test_untracked_real_fills_are_not_reported_as_zero_pnl():
    from polymaker.dashboard.m1 import real_session

    record = {"m1_progress": {"signals": {"fill_tracking": "NOT_TRACKED"}},
              "signal_counts": {}, "signals": {}, "pending_orders": 0, "pending_fills": 0,
              "trading_pnl": "0", "orders": [], "issues": []}
    assert real_session(record)["trading_pnl"] is None


def post_results(monkeypatch, market, results):
    """Keep real signing/final SDK guards; control only native POST results."""
    client, sent = market.e.gateway._client, []
    replies = iter(results)

    def native(*args, **kwargs):
        attempt = client._operation_value("batch_attempts")[0]
        sent.append((D(str(attempt.quote.price)), attempt.expected_order_id))
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        if result == "ACK":
            return [{"success": True, "orderID": attempt.expected_order_id}]
        return result

    monkeypatch.setattr(client, "_native_order_post", native)
    monkeypatch.setattr(market.e.reads.network, "snapshot", lambda *args: SimpleNamespace(allowed=True, reason="HEALTHY"))
    return sent


def retry_book(market, bid=".04", ask=".06"):
    feed = market.feed
    with feed.lock:
        feed.books["1"] = replace(feed.books["1"], bids=((D(bid), D(100)),),
            asks=((D(ask), D(100)),), reported_bid=None, reported_ask=None)


@pytest.mark.parametrize("fault", [None, [], {"success": True}, {"success": True, "orderID": "0x"+"f"*64}, "timeout"])
@pytest.mark.parametrize("second_result", ["ACK", None])
async def test_abnormal_or_timeout_retries_once_at_signal_price(env, monkeypatch, tmp_path, fault, second_result):
    import httpx

    setup, market = await live_setup(env, monkeypatch, tmp_path)
    fault = httpx.ReadTimeout("lost response") if fault == "timeout" else fault
    sent = post_results(monkeypatch, market, [fault, second_result])
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        assert market.ledger.row(ep.episode_id)["uncertain_retries"] == 1
        assert market.ledger.committed() == 2
        assert market.ledger.uncertain_posts()[0]["order_id"] == sent[0][1]
        retry_book(market)  # Best bid falls, but there was no price rejection.
        await market.submit(ep)
        await market.submit(ep)
        assert [p for p, _ in sent] == [D(".05"), D(".05")]
        assert len({oid for _, oid in sent}) == 2
        assert len(market.e.known) == 2
        assert market.ledger.instruction_slots() == 1
        assert market.ledger.metric()["signals"] == 1
        assert market.ledger.row(ep.episode_id)["state"] == ("ACK" if second_result == "ACK" else "UNKNOWN")
        assert not setup[0].cancels
    finally:
        await close_setup(setup, market)


async def test_uncertain_resend_can_reprice_only_after_its_own_crossing_rejection(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [None,
        {"success": False, "errorMsg": "invalid post-only order: order crosses book"}, "ACK"])
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        retry_book(market)
        await market.submit(ep)
        await market.submit(ep)
        assert [p for p, _ in sent] == [D(".05"), D(".05"), D(".04")]
        row = market.ledger.row(ep.episode_id)
        assert row["state"] == "ACK" and row["uncertain_retries"] == 1
        assert D(row["price"]) == D(".05") and market.ledger.committed() == 2
        assert market.ledger.instruction_slots() == 1
    finally:
        await close_setup(setup, market)


async def test_timeout_after_repricing_resends_original_signal_price(env, monkeypatch, tmp_path):
    import httpx

    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [
        {"success": False, "errorMsg": "invalid post-only order: order crosses book"},
        httpx.ReadTimeout("repriced POST response lost"), "ACK"])
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        retry_book(market)
        await market.submit(ep)
        await market.submit(ep)
        assert [p for p, _ in sent] == [D(".05"), D(".04"), D(".05")]
        assert market.ledger.committed() == 2
        assert market.ledger.events("m1_retry_authorized")[-1][1]["proof"]["kind"] == "UNCERTAIN_TIMEOUT"
    finally:
        await close_setup(setup, market)


@pytest.mark.parametrize("error,bid,expected", [
    ("not enough balance", ".04", ".05"),
    ("not enough allowance", ".04", ".05"),
    ("invalid post-only order: order crosses book", ".05", None),
    ("invalid post-only order: order crosses book", ".06", None),
    ("invalid post-only order: order crosses book", ".04", ".04"),
])
async def test_only_crossing_reject_plus_lower_bid_can_change_price(env, monkeypatch, tmp_path, error, bid, expected):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [{"success": False, "errorMsg": error}, "ACK"])
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        retry_book(market, bid, ".08")
        await market.submit(ep)
        assert [p for p, _ in sent] == ([D(".05"), D(expected)] if expected else [D(".05")])
        assert market.ledger.row(ep.episode_id)["uncertain_retries"] == 0
    finally:
        await close_setup(setup, market)


async def test_http_400_crossing_rejection_allows_best_bid_repost(env, monkeypatch, tmp_path):
    import httpx
    from py_clob_client_v2.exceptions import PolyApiException

    error = PolyApiException(httpx.Response(400, json={"error": "invalid post-only order: order crosses book"}))
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [error, "ACK"])
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        retry_book(market)
        await market.submit(ep)
        assert [p for p, _ in sent] == [D(".05"), D(".04")]
        assert market.ledger.committed() == 1
        assert not market.ledger.uncertain_posts()
    finally:
        await close_setup(setup, market)


async def test_restart_keeps_uncertain_post_risk_and_single_retry_allowance(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [None, None])
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        market.e.known.clear()
        market.e.attempts.clear()
        market.e.gateway._client.known_ids.clear()
        market.ledger.close()
        market.ledger = market.e.ledger = ledger(tmp_path)
        resumed = M1Market(market.e, market.settings, asyncio.Event(), display=lambda _: None)
        resumed.restore()
        await resumed.refresh_account()
        assert resumed.ledger.committed() == 2
        assert resumed.e.known == {sent[0][1]}
        await resumed.submit(resumed.pending[ep.episode_id])
        assert resumed.ledger.row(ep.episode_id)["uncertain_retries"] == 1
        assert resumed.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
        after = M1Market(market.e, market.settings, asyncio.Event(), display=lambda _: None)
        after.restore()
        assert not after.pending
        await after.submit(ep)
        assert len(sent) == 2 and len(after.e.known) == 2
        assert after.ledger.committed() == 2
    finally:
        await close_setup(setup, market)


def test_uncertain_resend_retains_risk_and_checks_additional_cash(tmp_path):
    book = ledger(tmp_path)
    try:
        prepared(book, 1)
        assert not book.retry("op1", proof={"kind": "UNCERTAIN_TIMEOUT"}, spendable=".9")
        assert book.row("op1")["state"] == "UNKNOWN" and book.row("op1")["uncertain_retries"] == 0
        assert book.committed() == 1
        assert book.retry("op1", proof={"kind": "UNCERTAIN_TIMEOUT"}, spendable=1)
        assert book.committed() == 2 and book.instruction_slots() == 1
        with pytest.raises(ValueError, match="UNAUTHORIZED_REPRICE"):
            book.reserve_quote("op1", price=".09", quantity="11.2", spendable=20)
        book.prepare("op1", "0x"+"2"*64, {"side": "BUY", "token_id": "1", "price": ".1", "size": "10"})
        book.post_start("op1")
        assert not book.retry("op1", proof={"kind": "UNCERTAIN_RESPONSE"}, spendable=20)
        assert book.committed() == 2
    finally:
        book.close()


def test_uncertain_resend_respects_gross_budget_and_existing_five_signals(tmp_path):
    book = ledger(tmp_path)
    try:
        for i in range(1, 6):
            prepared(book, i)
        assert book.instruction_slots() == 5
        assert book.retry("op1", proof={"kind": "UNCERTAIN_RESPONSE"}, spendable=20)
        assert book.committed() == 6 and book.instruction_slots() == 5
    finally:
        book.close()
    limited = M1Ledger(tmp_path/"limited.sqlite3", {"market": "test"}, limit=D("1.99"))
    try:
        prepared(limited, 1)
        assert not limited.retry("op1", proof={"kind": "UNCERTAIN_RESPONSE"}, spendable=20)
        assert limited.committed() == 1 and not limited.uncertain_posts()
    finally:
        limited.close()


async def test_restart_after_unknown_before_retry_authorization_still_allows_only_one_extra(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [None, "ACK"])
    try:
        ep = next(iter(market.pending.values()))
        monkeypatch.setattr(market, "queue_retry", lambda *args, **kwargs: False)
        await market.submit(ep)
        assert market.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
        resumed = M1Market(market.e, market.settings, asyncio.Event(), display=lambda _: None)
        resumed.restore()
        await resumed.refresh_account()
        await resumed.submit(resumed.pending[ep.episode_id])  # Reserve its one extra attempt.
        assert len(sent) == 1 and resumed.ledger.committed() == 2
        await resumed.submit(ep)
        assert len(sent) == 2 and resumed.ledger.row(ep.episode_id)["state"] == "ACK"
        assert resumed.ledger.row(ep.episode_id)["uncertain_retries"] == 1
    finally:
        await close_setup(setup, market)


def test_existing_ledger_migration_preserves_unknown_signed_identity(tmp_path):
    import sqlite3

    book = ledger(tmp_path)
    prepared(book, 1)
    original = book.row("op1")["order_id"]
    book.close()
    with sqlite3.connect(tmp_path/"risk.sqlite3") as db:
        for name in ("uncertain_retries", "retry_pricing", "repriced_from"):
            db.execute(f"ALTER TABLE opportunities DROP COLUMN {name}")
        db.execute("DROP TABLE uncertain_posts")
    restored = ledger(tmp_path)
    try:
        assert restored.row("op1")["order_id"] == original
        assert restored.row("op1")["state"] == "UNKNOWN"
        assert restored.row("op1")["uncertain_retries"] == 0
        assert restored.committed() == 1
        assert restored.retry("op1", proof={"kind": "UNCERTAIN_RESTART"}, spendable=1)
        assert restored.committed() == 2
    finally:
        restored.close()


def test_restart_refuses_to_forget_first_uncertain_order(tmp_path):
    import sqlite3

    book = ledger(tmp_path)
    prepared(book, 1)
    assert book.retry("op1", proof={"kind": "UNCERTAIN_TIMEOUT"}, spendable=1)
    book.close()
    with sqlite3.connect(tmp_path/"risk.sqlite3") as db:
        db.execute("DELETE FROM uncertain_posts")
    with pytest.raises(ValueError, match="M1_UNCERTAIN_POST_INVALID"):
        ledger(tmp_path)


async def test_timeout_keeps_resend_pending_until_funds_are_refreshed(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [None, "ACK"])
    client = market.e.gateway._client
    native = client._native_order_post

    def stale_after_send(*args, **kwargs):
        result = native(*args, **kwargs)
        market.account_at -= market.settings.account_max_age_sec+1
        return result

    monkeypatch.setattr(client, "_native_order_post", stale_after_send)
    try:
        ep = next(iter(market.pending.values()))
        market.pending.clear()
        await market.submit(ep)
        assert market.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
        assert market.ledger.row(ep.episode_id)["uncertain_retries"] == 0
        assert ep.episode_id in market.pending
        assert market.ledger.committed() == 1
        await market.submit(market.pending.pop(ep.episode_id))
        assert ep.episode_id in market.pending and len(sent) == 1
        await market.refresh_account()
        await market.submit(market.pending.pop(ep.episode_id))
        assert market.ledger.row(ep.episode_id)["state"] == "RESERVED"
        assert market.ledger.committed() == 2
        await market.submit(market.pending.pop(ep.episode_id))
        assert len(sent) == 2 and market.ledger.row(ep.episode_id)["state"] == "ACK"
    finally:
        await close_setup(setup, market)


async def test_public_time_probes_restore_network_guard_before_uncertain_resend(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    v, e = setup[:2]
    try:
        ep = next(iter(market.pending.values()))
        setup[5].fail_ack = True
        await market.submit(ep)
        assert not e.reads.network.snapshot().allowed
        assert market.ledger.row(ep.episode_id)["state"] == "RESERVED"
        await market.submit(ep)
        assert len(v.post_requests) == 1  # No bypass of the existing health guard.
        e.reads.network.pause_until = 0  # Recovery pause has elapsed.
        for _ in range(e.cfg.rdp_sm.obi.network_recovery_samples):
            await market.refresh_account()
        assert e.reads.network.snapshot().allowed
        setup[5].fail_ack = False
        await market.submit(ep)
        assert len(v.post_requests) == 2
        assert market.ledger.row(ep.episode_id)["state"] == "ACK"
        assert market.ledger.committed() == 2
    finally:
        await close_setup(setup, market)


async def test_unsent_repriced_preparation_keeps_original_crossing_authorization(env, monkeypatch, tmp_path):
    setup, market = await live_setup(env, monkeypatch, tmp_path)
    sent = post_results(monkeypatch, market, [
        {"success": False, "errorMsg": "invalid post-only order: order crosses book"}, "ACK"])
    try:
        ep = next(iter(market.pending.values()))
        await market.submit(ep)
        retry_book(market)
        native_prepare = market.e.gateway.prepare
        moved = False

        async def move_after_signing(*args, **kwargs):
            nonlocal moved
            result = await native_prepare(*args, **kwargs)
            if not moved:
                moved = True
                retry_book(market, ".03", ".04")
            return result

        monkeypatch.setattr(market.e.gateway, "prepare", move_after_signing)
        await market.submit(ep)
        assert len(sent) == 1
        assert market.ledger.row(ep.episode_id)["retry_pricing"] == "BEST_BID_ON_REJECTION"
        await market.submit(ep)
        assert [p for p, _ in sent] == [D(".05"), D(".03")]
        assert market.ledger.row(ep.episode_id)["uncertain_retries"] == 0
    finally:
        await close_setup(setup, market)


async def test_live_launcher_requires_explicit_opt_in_with_real_transport(monkeypatch):
    import httpx
    from py_clob_client_v2.http_helpers import helpers

    from polymaker.config import Config
    from polymaker.m1_live import run_m1

    # No real request is made: admission must fail before account connection.
    with httpx.Client() as client:
        monkeypatch.setattr(helpers, "_http_client", client)
        with pytest.raises(ValueError, match="REQUIRES_EXPLICIT_LIVE"):
            await run_m1(Config.load("config"))


def test_live_cli_requires_flag_and_passes_explicit_authorization(monkeypatch):
    monkeypatch.setattr("polymaker.m4_control.legacy_entry_guard", lambda: None)
    calls = []

    def run(config_dir, once, preflight_only, *, live):
        calls.append((config_dir, once, preflight_only, live))

    monkeypatch.setattr("polymaker.cli._legacy_m1_live_run", run)
    assert CliRunner().invoke(app, ["m1-live-run"]).exit_code != 0
    assert not calls
    result = CliRunner().invoke(app, ["m1-live-run", "--live", "--once"])
    assert result.exit_code == 0, result.output
    assert calls == [("config", True, False, True)]
