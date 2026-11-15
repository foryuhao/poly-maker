"""After10s band rejection is durable; all HTTP here is synthetic."""

import json
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
import test_m4_live_budget as budget
from m4_helpers import close
from test_m4_live_post_price_band import band_settings
from test_m4_live_task import evidence, serve

from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_live_price_band import (
    ABANDON_BEST_ASK_BAND, BEST_ASK_BAND_ABANDONED, POST_PRICE_OUTSIDE_BAND,
    WAIT_FOR_PRICE_BAND,
)


def settings(mode="taker"):
    return band_settings(mode, post_price_band_retry_policy=ABANDON_BEST_ASK_BAND)


async def fixture(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(budget, "settings", settings)
    return await budget.fixture(tmp_path, monkeypatch, **kwargs)


def move(m, f, c, ep, *, age, ask, stale=False):
    root = m.ledger.root_plan(ep.episode_id)
    c.now = float(D(str(root["pricing_origin"]["signal_monotonic"])) + D(str(age)))
    price = D(ask)
    book = f.books[ep.token_id]
    f.books[ep.token_id] = replace(book, bids=((price-book.tick, D(100)),),
        asks=((price, D(100)),), source_time=f.wall(), received_mono=c.now-(1 if stale else 0),
        reported_bid=None, reported_ask=None)


def test_new_policy_is_explicit_and_historical_band_settings_keep_identity():
    old, new = band_settings(), settings()
    saved = old.model_dump(mode="json")
    assert "post_price_band_retry_policy" not in saved["execution"]["taker"]
    assert M4LiveSettings.model_validate(saved).fingerprint == old.fingerprint
    assert old.execution.taker.post_price_band_retry_policy == WAIT_FOR_PRICE_BAND
    assert new.fingerprint != old.fingerprint
    assert new.model_dump(mode="json")["execution"]["taker"]["post_price_band_retry_policy"] == ABANDON_BEST_ASK_BAND
    for taker in ({"post_price_band_retry_policy": ABANDON_BEST_ASK_BAND},
                  {"post_price_band_retry_policy": ABANDON_BEST_ASK_BAND,
                   "price_band_policy": "POST_SIGNED_LIMIT_BAND_V1"}):
        with pytest.raises(ValueError, match="M4_BAND_ABANDON_REQUIRES_DYNAMIC_POST_BAND"):
            M4LiveSettings(execution_revision="m4-taker-budget-v1", execution={"mode": "taker", "taker": taker})


@pytest.mark.parametrize("anchor,ask,rejection_age", [
    (".16", ".17", 10), (".96", ".95", 10), (".96", ".99", 10),
    (".16", ".23", 10.508576),  # Saved14:35 fresh-ask rejection, before the130s POST.
])
async def test_first_fresh_outside_ask_at10s_permanently_stops_unsent_signal(tmp_path, monkeypatch, anchor, ask, rejection_age):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=anchor, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        assert not calls and not m.ledger.price_band_abandonment(ep.episode_id)
        move(m, f, c, ep, age=9.999999, ask=ask)
        await m.refresh_account()
        await m.submit(ep)
        assert not m.ledger.price_band_abandonment(ep.episode_id)
        move(m, f, c, ep, age=rejection_age, ask=ask)
        await m.refresh_account()
        await m.submit(ep)
        proof = m.ledger.price_band_abandonment(ep.episode_id)
        assert proof["pricing"]["signal_age_us"] == int(D(str(rejection_age))*1_000_000)
        assert proof["pricing"]["best_ask"] == str(D(ask))
        assert m.ledger.row(ep.episode_id)["state"] == "SKIPPED"
        assert m.ledger.row(ep.episode_id)["reason"] == POST_PRICE_OUTSIDE_BAND
        assert ep.episode_id not in m.pending
        for age, price in ((11, ".50"), (130.846, ".50"), (131.834, ".60")):
            move(m, f, c, ep, age=age, ask=price)
            await m.refresh_account()
            await m.submit(ep)
        assert not calls and not m.ledger.attempts()
        assert m.ledger.committed() == m.ledger.instruction_slots() == 0
        assert m.ledger.price_band_abandonment(ep.episode_id) == proof
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_legal_ask_zero_match_continues_until_t240(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        for age, ask in ((10, ".50"), (30, ".94"), (100, ".80"), (200, ".60")):
            move(m, f, c, ep, age=age, ask=ask)
            await m.refresh_account()
            await m.submit(ep)
            assert not m.ledger.price_band_abandonment(ep.episode_id)
        assert len(calls) == 4
        e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
        e.time_anchor = c.now
        await m.submit(ep)
        assert len(calls) == 4 and m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
    finally:
        await close(m, e)
        t.close()


async def test_no_match_then_outside_band_stops_all_further_retries(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        move(m, f, c, ep, age=10, ask=".95")
        await m.refresh_account()
        await m.submit(ep)
        assert m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
        assert m.ledger.row(ep.episode_id)["reason"] == BEST_ASK_BAND_ABANDONED
        move(m, f, c, ep, age=11, ask=".80")
        await m.submit(ep)
        assert len(calls) == 1
        assert m.ledger.instruction_slots() == 1
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("cause", ["network", "stale_book", "signing"])
async def test_other_failure_retries_without_abandonment(tmp_path, monkeypatch, cause):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        move(m, f, c, ep, age=10, ask=".80", stale=cause == "stale_book")
        await m.refresh_account()
        snapshot, prepare = e.reads.network.snapshot, e.gateway.prepare
        if cause == "network":
            monkeypatch.setattr(e.reads.network, "snapshot", lambda: SimpleNamespace(allowed=False, reason="NETWORK_PAUSED"))
        if cause == "signing":
            async def failed(*args, **kwargs):
                raise ValueError("offline signing failure")
            monkeypatch.setattr(e.gateway, "prepare", failed)
        await m.submit(ep)
        assert not calls and not m.ledger.price_band_abandonment(ep.episode_id)
        monkeypatch.setattr(e.reads.network, "snapshot", snapshot)
        monkeypatch.setattr(e.gateway, "prepare", prepare)
        move(m, f, c, ep, age=11, ask=".80")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1 and not m.ledger.price_band_abandonment(ep.episode_id)
    finally:
        await close(m, e)
        t.close()


async def test_stale_outside_book_does_not_count_as_fresh_band_rejection(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        move(m, f, c, ep, age=10, ask=".95", stale=True)
        await m.refresh_account()
        await m.submit(ep)
        assert not m.ledger.price_band_abandonment(ep.episode_id)
        move(m, f, c, ep, age=11, ask=".80")
        await m.submit(ep)
        assert len(calls) == 1
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("first_status,final_status", [("CONFIRMED", "CONFIRMED"), ("MINED", "CONFIRMED"), ("MINED", "FAILED")])
async def test_stop_topups_keeps_confirmed_and_pending_matches(tmp_path, monkeypatch, first_status, final_status):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".50")
    query = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "3", price=".40", status=first_status)
        query, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        used = m.ledger.budget_used(ep.episode_id)
        move(m, f, c, ep, age=10, ask=".95")
        await m.refresh_account()
        await m.submit(ep)
        assert m.ledger.budget_used(ep.episode_id) == used
        assert m.ledger.price_band_abandonment(ep.episode_id)
        assert D(m.ledger.row(ep.episode_id)["reserved"]) == m.ledger.pending_cost(ep.episode_id)
        trade["status"] = final_status
        await m.reconcile(oid)
        move(m, f, c, ep, age=12, ask=".80")
        await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert len(calls) == 1 and row["state"] == "CUTOFF"
        assert row["reason"] == BEST_ASK_BAND_ABANDONED and D(row["reserved"]) == 0
        assert D(row["filled"]) == (3 if final_status == "CONFIRMED" else 0)
        assert m.ledger.pending_cost(ep.episode_id) == 0
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


async def test_abandoned_signal_stays_stopped_after_restart(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".16", response="KILLED")
    ep = next(iter(m.pending.values()))
    move(m, f, c, ep, age=10, ask=".17")
    await m.refresh_account()
    await m.submit(ep)
    proof = m.ledger.price_band_abandonment(ep.episode_id)
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".16", response="KILLED", recover=True)
    try:
        move(m, f, c, ep, age=131, ask=".60")
        await m.refresh_account()
        await m.submit(ep)
        assert not calls and not m.pending and not m.ledger.attempts()
        assert m.ledger.price_band_abandonment(ep.episode_id) == proof
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_unknown_is_reconciled_before_outside_price_can_stop_entry(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        reserve = m.ledger.committed()
        move(m, f, c, ep, age=10, ask=".95")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
        assert m.ledger.committed() == reserve
        assert not m.ledger.price_band_abandonment(ep.episode_id)
    finally:
        await close(m, e)
        t.close()


async def test_signing_crosses10s_records_unsent_before_abandonment(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        prepare = e.gateway.prepare

        async def cross_boundary(*args, **kwargs):
            result = await prepare(*args, **kwargs)
            move(m, f, c, ep, age=10, ask=".95")
            return result

        monkeypatch.setattr(e.gateway, "prepare", cross_boundary)
        await m.submit(ep)
        assert not calls and m.ledger.attempts()[0]["state"] == "UNSENT"
        assert not m.ledger.price_band_abandonment(ep.episode_id)
        await m.refresh_account()
        await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert row["state"] == "CUTOFF" and row["reason"] == BEST_ASK_BAND_ABANDONED
        assert not calls and len(m.ledger.attempts()) == 1
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_unverified_tick_outside_band_waits_then_can_retry(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        move(m, f, c, ep, age=10, ask=".95")
        await m.refresh_account()
        tick = f.tick_verified.pop(ep.token_id)
        await m.submit(ep)
        assert not calls and not m.ledger.price_band_abandonment(ep.episode_id)
        f.tick_verified[ep.token_id] = tick
        move(m, f, c, ep, age=11, ask=".80")
        await m.submit(ep)
        assert len(calls) == 1
    finally:
        await close(m, e)
        t.close()


async def test_same_market_restores_wait_policy_next_market_uses_abandonment(tmp_path, monkeypatch):
    from dataclasses import asdict
    from test_m4_live_startup import EPOCH, FUNDER, harness
    from polymaker.m4_config import STRATEGY
    from polymaker.m4_live_taker import M4LiveLedger

    h = harness(tmp_path, monkeypatch)
    saved, configured = band_settings(), settings()
    identity = {"strategy": STRATEGY, "mode": "LIVE", "funder": FUNDER,
                "market_id": h.meta.condition_id, "settings_hash": saved.fingerprint}
    path = h.startup.root / "ledgers" / f"{h.meta.slug}.sqlite3"
    ledger = M4LiveLedger(path, identity, settings=saved, fees=budget.legacy.FEES)
    ledger.initialize_session("offline_band_abandonment_recovery", saved.model_dump(mode="json"), asdict(h.meta))
    ledger.close()
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.settings == saved
        assert ready.settings.execution.taker.post_price_band_retry_policy == WAIT_FOR_PRICE_BAND
    finally:
        ready.reads.close()
    h.clock_values[:] = [EPOCH+301, EPOCH+302]
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.settings.execution.taker.post_price_band_retry_policy == ABANDON_BEST_ASK_BAND
        assert ready.identity["settings_hash"] == configured.fingerprint
    finally:
        ready.reads.close()
