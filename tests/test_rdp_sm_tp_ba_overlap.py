"""BA-before-TP-cancel through real SDK I/O, delayed commands and fill proofs."""

import asyncio
import threading
from decimal import Decimal as D

import pytest

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm_sl_ledger import exposure
from polymaker.state.tracker import TradeEvent
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_restored_tp import filled, restored_setup, until


async def setup_handoff(env, monkeypatch):
    setup, c = await restored_setup(env, monkeypatch)
    for cfg in (c.ba_config, setup[2].opposite_ba.cfg):
        cfg.signal_lifetime_sec = 3
        cfg.max_entry_age_ms = 1000
        cfg.max_sell_age_ms = 1000
    return setup, c


async def start_tp(setup, c):
    v, e = setup[:2]
    task = asyncio.create_task(c.repair(*e.economic_inventory(), "overlap"))
    await until(lambda: len(v.post_requests) == 2 and e.attempts[-1].outcome == "ACK")
    return task, e.attempts[-1].order_id


async def stop_task(task):
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cached", [False, True])
async def test_ba_posts_fresh_before_slow_tp_cancel_and_keeps_original_deadline(
    env, monkeypatch, tmp_path, cached
):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed = setup[:3]
    companion = (await attach(setup, tmp_path))[0] if cached else None
    task = None
    events = []
    try:
        task, tp = await start_tp(setup, c)
        native_send, native_cancel = e.gateway.send_prepared, e.gateway.cancel_detailed

        async def send(signed, **kw):
            assert v.rows[tp]["status"] == "LIVE"
            assert not any(tp in ids for ids in v.cancels)
            events.append(("BA_POST", e.monotonic()))
            return await native_send(signed, **kw)

        async def cancel(ids):
            if tp in ids:
                assert e.attempts[-1].outcome == "ACK"
                events.append(("TP_DELETE", e.monotonic()))
                await asyncio.sleep(1.15)  # Reproduce the live >1s close latency.
            return await native_cancel(ids)

        monkeypatch.setattr(e.gateway, "send_prepared", send)
        monkeypatch.setattr(e.gateway, "cancel_detailed", cancel)
        absorption(feed, "2")
        episode = next(reversed(feed.opposite_ba.opportunities.values()))
        original_t0, original_end = episode.signal_start_mono, episode.order_deadline
        result, sizing = await asyncio.wait_for(task, 6)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 3 and sizing["ba_orders"][0]["post_started"]
        assert [kind for kind, _ in events[:2]] == ["BA_POST", "TP_DELETE"]
        assert 0 <= events[0][1] - original_t0 < 1
        assert episode.consumed and episode.signal_start_mono == original_t0
        assert episode.order_deadline == original_end
        assert all(r["status"] != "LIVE" for r in v.rows.values())
        assert e.economic_inventory() == (D(5), D(0))
        assert all(
            row["postOnly"] and row["orderType"] == "GTC"
            for body in v.post_requests
            for row in body
        )
    finally:
        await stop_task(task)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("failure", ["rejected", "unknown", "signing"])
@pytest.mark.parametrize("cached", [False, True])
async def test_failed_or_unknown_ba_preserves_original_tp_without_duplicate_signal(
    env, monkeypatch, tmp_path, failure, cached
):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed, _, _, wire = setup[:6]
    companion = (await attach(setup, tmp_path))[0] if cached else None
    task = None
    try:
        task, tp = await start_tp(setup, c)
        if failure == "rejected":
            wire.reject_index = 0
        elif failure == "unknown":
            native = e.gateway.send_prepared

            async def ambiguous_acceptance(*args, **kwargs):
                await native(*args, **kwargs)
                return [{"success": True, "orderID": "", "status": ""}]

            monkeypatch.setattr(e.gateway, "send_prepared", ambiguous_acceptance)
        else:

            async def signing_failure(*args, **kwargs):
                raise ValueError("test signer failure")

            monkeypatch.setattr(e.gateway, "prepare", signing_failure)
        absorption(feed, "2")
        await until(
            lambda: (
                len(e.attempts) == 3
                and e.attempts[-1].outcome in {"REJECTED", "VERIFIED", "ABORTED"}
            )
        )
        await asyncio.sleep(0.08)
        assert not task.done()
        assert v.rows[tp]["status"] == "LIVE" and not any(tp in ids for ids in v.cancels)
        assert len(e.attempts) == 3
        if failure == "unknown":
            ba = e.attempts[-1].order_id
            assert ba in e.sl_terminal_quantities and v.rows[ba]["status"] == "CANCELED"
            assert any(ba in ids for ids in v.cancels)
        filled(v, tp, "tp-after-ba-failure")
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (D(5), D(5))
        assert len(v.post_requests) == (2 if failure == "signing" else 3)
    finally:
        await stop_task(task)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


async def test_wait_for_positive_ba_ack_before_any_tp_delete(env, monkeypatch):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed, _, _, wire = setup[:6]
    entered, release = threading.Event(), threading.Event()
    task = None
    try:
        task, tp = await start_tp(setup, c)
        wire.delay = lambda: (entered.set(), release.wait(2))
        absorption(feed, "2")
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.05)
        assert v.rows[tp]["status"] == "LIVE" and not any(tp in ids for ids in v.cancels)
        assert e.attempts[-1].post_started and e.attempts[-1].outcome != "ACK"
        ledger = exposure(e, retain_unknown_reservations=True)
        assert ledger.no_pending == 10 and ledger.interval == (D(-5), D(5))
        assert ledger.reserved == D("3.25") + D(str(e.attempts[-1].quote.price)) * 5
        release.set()
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert any(tp in ids for ids in v.cancels)
    finally:
        release.set()
        await stop_task(task)
        await cleanup(setup)


@pytest.mark.parametrize("tp_qty,ba_qty,expected", [(5, 0, 0), (2, 0, 3), (5, 5, -5), (2, 1, 2)])
@pytest.mark.parametrize("cached", [False, True])
async def test_tp_cancel_races_stop_excess_ba_and_preserve_fifo_reverse_inventory(
    env, monkeypatch, tmp_path, tp_qty, ba_qty, expected, cached
):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed = setup[:3]
    companion = (await attach(setup, tmp_path))[0] if cached else None
    task = None
    try:
        task, tp = await start_tp(setup, c)
        native = e.gateway.send_prepared

        async def send(signed, **kw):
            raw = await native(signed, **kw)
            if ba_qty:
                oid = signed.order_ids[0]
                filled(v, oid, "ba-first", ba_qty)
                # Deliver the first BA fill before TP's DELETE race; REST read
                # order alone cannot establish this venue event ordering.
                e.processor.on_trade(
                    TradeEvent(
                        "2",
                        Side.BUY,
                        float(v.rows[oid]["price"]),
                        ba_qty,
                        "ba-first",
                        TradeState.CONFIRMED,
                        v.time,
                        oid,
                        e.meta.condition_id,
                    ),
                    e.meta.condition_id,
                )
            return raw

        def on_cancel(venue):
            if venue.rows[tp]["status"] == "CANCELED" and not venue.rows[tp]["associate_trades"]:
                filled(venue, tp, "tp-during-delete", tp_qty)
                # Partial fills during DELETE leave the unfilled remainder canceled.
                venue.rows[tp]["status"] = "CANCELED"

        monkeypatch.setattr(e.gateway, "send_prepared", send)
        v.on_cancel = on_cancel
        absorption(feed, "2")
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        ba = e.attempts[-1].order_id
        assert len(v.post_requests) == 3 and any(ba in ids for ids in v.cancels)
        assert e.economic_inventory()[0] - e.economic_inventory()[1] == D(expected)
        assert not any(row["status"] == "LIVE" for row in v.rows.values())
        if expected < -2.5:
            assert e.repair_episode["side"] == "NO"
            assert e.repair_episode["original_qty"] == "5.0"
            assert e.repair_episode["direction_leg"] == 1
            assert e.repair_episode["chain_id"] == "opening"
            assert sum(D(str(f.size)) for f in e.repair_episode["source_lots"]) == 5
            assert {f.maker_order_id for f in e.repair_episode["source_lots"]} == {tp}
        logs = journal_records(e.journal.path)
        tp_result = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_restored_tp_result")
        ba_result = next(
            r["data"]["payload"] for r in logs if r["kind"] == "sm_tp_ba_handoff_result"
        )
        assert tp_result["pnl_verified"] and ba_result["pnl_verified"]
        assert D(tp_result["paired_shares"]) + D(ba_result["paired_shares"]) == min(
            5, tp_qty + ba_qty
        )
        assert D(tp_result["unpaired_shares"]) + D(ba_result["unpaired_shares"]) == max(
            0, tp_qty + ba_qty - 5
        )
        assert tp_result["submitted_orders"] == ba_result["submitted_orders"] == 1
        if ba_qty == 5:
            assert D(tp_result["pair_pnl_usdc"]) == 0  # TP is the reverse residual, not a profit.
            assert D(ba_result["pair_pnl_usdc"]) == 5 * (
                D(".7") - D(str(e.attempts[-1].quote.price))
            )
            v.on_cancel = None
            monkeypatch.setattr(e.gateway, "send_prepared", native)
            reverse = asyncio.create_task(c.repair(*e.economic_inventory(), "reverse-restore"))
            await until(lambda: len(v.post_requests) == 4)
            assert e.attempts[-1].quote.outcome == "YES" and e.attempts[-1].quote.size == 5
            assert e.attempts[-1].quote.price == 0.3  # Actual residual cost .65, profit .05.
            filled(v, e.attempts[-1].order_id, "reverse-paired")
            reverse_result, _ = await asyncio.wait_for(reverse, 5)
            assert reverse_result.close_barrier == "SAFE_TO_CONTINUE", reverse_result.block_reason
            assert e.economic_inventory() == (D(10), D(10)) and e.repair_episode is None
    finally:
        await stop_task(task)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("constraint", ["cash", "delta"])
async def test_overlap_never_releases_tp_reservation_to_admit_ba(env, monkeypatch, constraint):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed, lifecycle = setup[:4]
    task = None
    try:
        task, tp = await start_tp(setup, c)
        if constraint == "cash":
            lifecycle.account["basic_spendable_budget_usd"] = 0
        else:
            c.cfg.delta_limit = D(4)
        absorption(feed, "2")
        await asyncio.sleep(0.1)
        assert len(v.post_requests) == 2 and len(e.attempts) == 2
        assert not any(tp in ids for ids in v.cancels)
        filled(v, tp, "kept-tp")
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    finally:
        await stop_task(task)
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["stale_signal", "cutoff", "tp_fill", "disconnect"])
async def test_ba_final_native_guard_rechecks_after_signing_and_keeps_tp_when_healthy(
    env, monkeypatch, fault
):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed = setup[:3]
    task = None
    try:
        task, tp = await start_tp(setup, c)
        native = e.gateway.prepare

        async def prepare(*args, **kw):
            signed = await native(*args, **kw)
            if fault == "stale_signal":
                # Shorten admission without renewing the original signal t0.
                c.ba_config.max_entry_age_ms = 0.001
            elif fault == "cutoff":
                target = int(e.slug.rsplit("-", 1)[1]) + 240
                anchor = e.monotonic()
                e.official_time, e.time_anchor = target, anchor

                async def cutoff_server_time():
                    return target + e.monotonic() - anchor

                monkeypatch.setattr(e.reads, "server_time", cutoff_server_time)
            elif fault == "tp_fill":
                filled(v, tp, "filled-before-ba-post")
                e.processor.on_trade(
                    TradeEvent(
                        "2",
                        Side.BUY,
                        0.65,
                        5,
                        "filled-before-ba-post",
                        TradeState.CONFIRMED,
                        v.time,
                        tp,
                        e.meta.condition_id,
                    ),
                    e.meta.condition_id,
                )
            else:
                feed.invalidate("WS_DISCONNECTED")
                setup[-2]["running"] = False
            return signed

        monkeypatch.setattr(e.gateway, "prepare", prepare)
        absorption(feed, "2")
        await until(lambda: len(e.attempts) == 3 and e.attempts[-1].outcome == "ABORTED")
        assert len(v.post_requests) == 2 and not e.attempts[-1].post_started
        if fault == "stale_signal":
            assert v.rows[tp]["status"] == "LIVE" and not any(tp in ids for ids in v.cancels)
            filled(v, tp, "kept-after-stale-ba")
        result, _ = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    finally:
        await stop_task(task)
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_initial_ca_tp_also_posts_ba_before_cancel_and_reports_ba_fills(
    env, monkeypatch, tmp_path, cached
):
    from test_rdp_sm_ca_matched_tp import fill, tp_setup

    setup = await tp_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.ca_matched_tp.restore_after_ba = True
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native = e.gateway.send_prepared
    task = None
    try:

        async def send(signed, **kw):
            if len(v.post_requests) == 2:
                tp = e.attempts[1].order_id
                assert v.rows[tp]["status"] == "LIVE" and not any(tp in ids for ids in v.cancels)
            raw = await native(signed, **kw)
            if len(v.post_requests) in {1, 3}:
                fill(v, signed.order_ids[0], "initial-or-ba-" + str(len(v.post_requests)))
                v.rows[signed.order_ids[0]]["status"] = "MATCHED"
                from test_rdp_sm_repair import reflect_positions

                reflect_positions(v)
            return raw

        monkeypatch.setattr(e.gateway, "send_prepared", send)
        await confirm_ca(feed)
        task = asyncio.create_task(normal.enter(D(0), D(0), "initial-overlap"))
        await until(lambda: len(v.post_requests) == 2 and e.attempts[-1].outcome == "ACK")
        absorption(feed, "2")
        result, sizing = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == len(result.requested_quotes) == 3
        assert len(sizing["ca_matched_tp"]["ba_handoff_orders"]) == 1
        assert e.economic_inventory() == (D(5), D(5))
    finally:
        await stop_task(task)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("status", [TradeState.MATCHED, TradeState.CONFIRMED])
async def test_tp_fill_while_ba_ack_pending_cancels_ba_without_waiting_for_settlement(
    env, monkeypatch, status
):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed, _, _, wire = setup[:6]
    entered, release = threading.Event(), threading.Event()
    task = None
    try:
        task, tp = await start_tp(setup, c)
        wire.delay = lambda: (entered.set(), release.wait(2))
        absorption(feed, "2")
        assert await asyncio.to_thread(entered.wait, 1)
        v.fill(tp, "tp-with-ba-inflight", 5, status=status.value)
        v.rows[tp]["status"] = "MATCHED"
        from test_rdp_sm_repair import reflect_positions

        reflect_positions(v)
        if status is TradeState.CONFIRMED:
            e.processor.on_trade(
                TradeEvent(
                    "2",
                    Side.BUY,
                    0.65,
                    5,
                    "tp-with-ba-inflight",
                    TradeState.MATCHED,
                    v.time,
                    tp,
                    e.meta.condition_id,
                ),
                e.meta.condition_id,
            )
        e.processor.on_trade(
            TradeEvent(
                "2",
                Side.BUY,
                0.65,
                5,
                "tp-with-ba-inflight",
                status,
                v.time,
                tp,
                e.meta.condition_id,
            ),
            e.meta.condition_id,
        )
        assert exposure(e).execution == 0
        release.set()
        result, _ = await asyncio.wait_for(task, 5)
        ba = e.attempts[-1].order_id
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert any(ba in ids for ids in v.cancels) and v.rows[ba]["status"] == "CANCELED"
        assert e.economic_inventory() == (D(5), D(5)) and len(v.post_requests) == 3
    finally:
        release.set()
        await stop_task(task)
        await cleanup(setup)


@pytest.mark.parametrize("fault", ["transport_timeout", "cutoff_during_post"])
async def test_global_network_halt_and_cutoff_still_close_every_owned_order(
    env, monkeypatch, fault
):
    setup, c = await setup_handoff(env, monkeypatch)
    v, e, feed, _, _, wire = setup[:6]
    task = None
    try:
        task, tp = await start_tp(setup, c)
        if fault == "transport_timeout":
            wire.fail_ack = True
        else:

            def cutoff():
                e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
                e.time_anchor = e.monotonic()

            wire.delay = cutoff
        absorption(feed, "2")
        result, sizing = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 3 and any(tp in ids for ids in v.cancels)
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        if fault == "transport_timeout":
            assert sizing["reason"] == "NETWORK_EXECUTION_FAILURE"
        assert not any(a.outcome in {"UNKNOWN", "INTENT"} for a in e.attempts)
    finally:
        await stop_task(task)
        await cleanup(setup)
