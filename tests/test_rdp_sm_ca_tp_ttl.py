"""CA TP lease expiry preserves fills/proofs and cannot be renewed by REPAIR."""

import asyncio
import time
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import Config, RDPCAMatchedTPConfig
from polymaker.rdp_sm_ca_matched_tp import CAMatchedTP
from polymaker.rdp_sm_opposite_ba import OppositeBAController
from polymaker.rdp_sm_restored_tp import RestoredTP
from test_rdp_sm_ca_entry import confirm_ca
from test_rdp_sm_ca_matched_tp import fill, tp_setup
from test_rdp_sm_ca_tp_direction_handoff import delayed_tp, observe
from test_rdp_sm_cached_reads import attach, finish
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_opposite_ba import absorption
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import reflect_positions
from test_rdp_sm_restored_tp import restored_setup, until


def test_default_and_deployed_ca_tp_ttl():
    assert RDPCAMatchedTPConfig().ttl_sec == 15
    assert Config.load("config", load_env=False).rdp_sm.ca_matched_tp.ttl_sec == 30
    assert RDPCAMatchedTPConfig(ttl_sec=7.5).ttl_sec == 7.5


@pytest.mark.parametrize("ttl", [0, -1, "NaN", "Infinity", "-Infinity"])
def test_ca_tp_ttl_rejects_invalid_values(ttl):
    with pytest.raises(ValidationError):
        RDPCAMatchedTPConfig(ttl_sec=ttl)


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("token", ["1", "2"])
@pytest.mark.parametrize("fill_timing", ["none", "partial", "cancel"])
async def test_ca_tp_expires_preserves_fills_and_cannot_restore(
    env, monkeypatch, tmp_path, cached, token, fill_timing
):
    async with delayed_tp(
        env, monkeypatch, tmp_path, cached, token, ba_enabled=False, ttl_sec=.3
    ) as c:
        await c.confirm()
        deadline = c.owner.expires_at
        assert c.e.ca_tp_deadlines[c.opening] == deadline
        started = c.owner.post_started[c.owner.created[0].attempt_id]
        assert deadline == pytest.approx(started + .3, abs=.001)
        assert c.v.rows[c.tp]["status"] == "LIVE" and not c.owner.tp_expired()
        if fill_timing == "partial":
            observe(c.v, c.e, c.tp, "partial-tp", qty=2)
        elif fill_timing == "cancel":
            def cancel(venue):
                if (venue.rows[c.tp]["status"] == "CANCELED"
                        and not venue.rows[c.tp]["associate_trades"]):
                    fill(venue, c.tp, "expiry-cancel-race", 2)
                    reflect_positions(venue)

            c.v.on_cancel = cancel
        result, sizing = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["ca_matched_tp"]["reason"] == "CA_MATCHED_TP_EXPIRED"
        assert c.owner.expires_at == deadline  # Partial fills never extend the lease.
        assert c.owner.cancel_started[c.owner.created[0].attempt_id] >= deadline
        assert c.v.rows[c.tp]["status"] == "CANCELED"
        assert c.tp in c.e.terminal and c.tp in c.e.proofs
        qty = D(0) if fill_timing == "none" else D(2)
        assert c.e.economic_inventory() == ((D(5), qty) if token == "1" else (qty, D(5)))
        controller = OppositeBAController(c.e, c.lifecycle)
        restored = RestoredTP(controller, "expired-ca-tp")
        assert restored.expires_at == deadline
        assert restored.goal() == (None, "CA_MATCHED_TP_EXPIRED")
        assert await controller.repair(*c.e.economic_inventory(), "expired-retry") is None
        assert len(c.v.post_requests) == 2
        await c.e.journal.flush()
        logs = journal_records(c.e.journal.path)
        lease = next(r["data"]["payload"] for r in logs if r["kind"] == "sm_ca_matched_tp_lease_started")
        assert lease["ttl_sec"] == .3 and lease["expires_at_monotonic"] == deadline
        deletes = [r["data"]["payload"]["delete_requested"] for r in logs
                   if r["kind"] == "sm_cancel_ack" and c.tp in r["data"]["payload"]["ids"]
                   and "delete_requested" in r["data"]["payload"]]
        assert deletes and min(deletes) >= deadline


async def test_expiry_cancels_before_opening_confirmation_and_keeps_close_barrier(
    env, monkeypatch, tmp_path
):
    async with delayed_tp(env, monkeypatch, tmp_path, ba_enabled=False, ttl_sec=.15) as c:
        await until(lambda: c.v.rows[c.tp]["status"] == "CANCELED")
        assert c.opening_trade["status"] == "MATCHED"
        assert not c.e.safe_to_continue and not c.task.done()
        c.opening_trade["status"] = "CONFIRMED"
        reflect_positions(c.v)
        result, sizing = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["ca_matched_tp"]["reason"] == "CA_MATCHED_TP_EXPIRED"
        assert c.e.economic_inventory() == (D(5), D(0))
        controller = OppositeBAController(c.e, c.lifecycle)
        assert RestoredTP(controller, "late-confirm").goal() == (None, "CA_MATCHED_TP_EXPIRED")


@pytest.mark.parametrize("cached", [False, True])
async def test_signing_does_not_start_ttl_and_delayed_ack_does_not_extend_it(
    env, monkeypatch, tmp_path, cached
):
    setup = await tp_setup(env, monkeypatch)
    v, e, feed, _, normal = setup[:5]
    e.cfg.rdp_sm.repair.ba_enabled = False
    e.cfg.rdp_sm.ca_matched_tp.ttl_sec = .15
    companion = (await attach(setup, tmp_path))[0] if cached else None
    native_send, native_prepare, native_init = (
        e.gateway.send_prepared, e.gateway.prepare, CAMatchedTP.__init__
    )
    owner = []
    tp = []
    task = None

    def capture(actor, *args, **kwargs):
        native_init(actor, *args, **kwargs)
        owner.append(actor)

    async def prepare(*args, **kwargs):
        if v.post_requests:
            await asyncio.sleep(.2)
            assert owner[0].expires_at is None
        return await native_prepare(*args, **kwargs)

    async def send(signed, **kwargs):
        raw = await native_send(signed, **kwargs)
        oid = signed.order_ids[0]
        if len(v.post_requests) == 1:
            fill(v, oid, "opening")
            v.rows[oid]["status"] = "MATCHED"
            reflect_positions(v)
        else:
            tp.append(oid)
            await until(lambda: v.rows[oid]["status"] == "CANCELED")
            # The first DELETE cannot release a POST still in flight.
            assert not e.safe_to_continue and not task.done()
            v.rows[oid]["status"] = "LIVE"  # Simulate a late acceptance after DELETE.
        return raw

    monkeypatch.setattr(CAMatchedTP, "__init__", capture)
    monkeypatch.setattr(e.gateway, "prepare", prepare)
    monkeypatch.setattr(e.gateway, "send_prepared", send)
    try:
        await confirm_ca(feed)
        task = asyncio.create_task(normal.enter(D(0), D(0), "slow-post"))
        result, sizing = await asyncio.wait_for(task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["ca_matched_tp"]["reason"] == "CA_MATCHED_TP_EXPIRED"
        actor = owner[0]
        post_start = actor.post_started[actor.created[0].attempt_id]
        assert post_start - actor.started_monotonic >= .2
        await e.journal.flush()
        deletes = [r["data"]["payload"]["delete_requested"] for r in journal_records(e.journal.path)
                   if r["kind"] == "sm_cancel_ack" and tp[0] in r["data"]["payload"]["ids"]
                   and "delete_requested" in r["data"]["payload"]]
        assert min(deletes) >= actor.expires_at
        assert len(deletes) >= 2 and v.rows[tp[0]]["status"] == "CANCELED"
        assert e.economic_inventory() == (D(5), D(0))
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_restored_ca_tp_uses_original_deadline(env, monkeypatch, tmp_path, cached):
    setup, controller = await restored_setup(env, monkeypatch)
    v, e = setup[:2]
    e.cfg.rdp_sm.repair.ba_enabled = False
    opening = e.attempts[0].order_id
    # Simulate a CA TP canceled early (e.g. for BA); only .2s of its lease remains.
    e.ca_tp_deadlines[opening] = time.monotonic() + .2
    companion = (await attach(setup, tmp_path))[0] if cached else None
    try:
        result, sizing = await asyncio.wait_for(controller.repair(*e.economic_inventory(), "restore-lease"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["reason"] == "CA_MATCHED_TP_EXPIRED"
        assert v.rows[e.attempts[-1].order_id]["status"] == "CANCELED"
        assert await controller.repair(*e.economic_inventory(), "restore-again") is None
        assert len(v.post_requests) == 2
    finally:
        if companion:
            await finish(setup, companion)
        else:
            await cleanup(setup)


async def test_restored_tp_expiring_during_signing_never_posts(env, monkeypatch):
    setup, controller = await restored_setup(env, monkeypatch)
    v, e = setup[:2]
    e.cfg.rdp_sm.repair.ba_enabled = False
    e.ca_tp_deadlines[e.attempts[0].order_id] = time.monotonic() + .1
    native = e.gateway.prepare

    async def prepare(*args, **kwargs):
        signed = await native(*args, **kwargs)
        await asyncio.sleep(.2)
        return signed

    monkeypatch.setattr(e.gateway, "prepare", prepare)
    try:
        result, sizing = await asyncio.wait_for(controller.repair(*e.economic_inventory(), "late-restore"), 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["reason"] == "CA_MATCHED_TP_EXPIRED"
        assert len(v.post_requests) == 1 and not e.attempts[-1].post_started
    finally:
        await cleanup(setup)


async def test_expired_other_lots_do_not_block_new_or_reverse_residual_tp(env, monkeypatch):
    setup, controller = await restored_setup(env, monkeypatch)
    _, e = setup[:2]
    e.ca_tp_deadlines["previous-ca-opening"] = time.monotonic() - 10
    try:
        actor = RestoredTP(controller, "new-lots")
        assert actor.expires_at is None
        goal, reason = actor.goal()
        assert goal is not None and reason is None
    finally:
        await cleanup(setup)


async def test_tp_expires_during_ba_signing_and_prevents_late_ba_post(env, monkeypatch, tmp_path):
    async with delayed_tp(env, monkeypatch, tmp_path, ttl_sec=.3) as c:
        await c.confirm()
        native = c.e.gateway.prepare

        async def prepare(*args, **kwargs):
            signed = await native(*args, **kwargs)
            if ":ba-handoff:" in c.e.attempts[-1].quote.window_id:
                await asyncio.sleep(.4)
            return signed

        monkeypatch.setattr(c.e.gateway, "prepare", prepare)
        absorption(c.feed, c.opposite)
        await until(lambda: len(c.e.attempts) == 3)
        result, sizing = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["ca_matched_tp"]["reason"] == "CA_MATCHED_TP_EXPIRED"
        assert c.v.rows[c.tp]["status"] == "CANCELED"
        assert not c.e.attempts[-1].post_started
        assert len(c.v.post_requests) == 2


async def test_accepted_ba_keeps_its_own_lease_after_ca_tp_deadline(env, monkeypatch, tmp_path):
    async with delayed_tp(env, monkeypatch, tmp_path, ttl_sec=.3) as c:
        await c.confirm()
        absorption(c.feed, c.opposite)
        await until(lambda: c.ba is not None and c.e.attempts[-1].outcome == "ACK")
        await until(lambda: c.owner.tp_expired())
        assert c.v.rows[c.tp]["status"] == "CANCELED"
        assert c.v.rows[c.ba]["status"] == "LIVE"
        assert not any(c.ba in ids for ids in c.v.cancels)
        observe(c.v, c.e, c.ba, "ba-after-tp-deadline")
        result, sizing = await asyncio.wait_for(c.task, 5)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert sizing["ca_matched_tp"]["reason"] == "OPPOSITE_BA_HANDOFF"
        assert c.e.economic_inventory() == (D(5), D(5))
