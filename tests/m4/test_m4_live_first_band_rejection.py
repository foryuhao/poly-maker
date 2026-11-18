"""Immediate whole-band DROP; real SDK with synthetic HTTP and isolated ledgers."""

import json
from dataclasses import asdict
from decimal import Decimal as D

import pytest
import test_m4_live_band_abandonment as previous
import test_m4_live_budget as budget
from m4_helpers import close, op
from test_m4_live_post_price_band import band_settings
from test_m4_live_task import evidence, serve

from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_live_price_band import (
    ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND, POST_PRICE_OUTSIDE_BAND,
    SIGNED_LIMIT_BAND_ABANDONED, WAIT_FOR_PRICE_BAND,
)
from polymaker.m4_live_pricing import origin, quote_valid, rejected_price_band_valid


def settings(mode="taker"):
    return band_settings(mode, post_price_band_retry_policy=ABANDON_FIRST_SIGNED_BAND)


async def fixture(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(budget, "settings", settings)
    return await budget.fixture(tmp_path, monkeypatch, **kwargs)


def test_versioned_settings_preserve_both_historical_policies():
    for cfg in (band_settings(), previous.settings(), settings()):
        saved = cfg.model_dump(mode="json")
        assert M4LiveSettings.model_validate(saved).fingerprint == cfg.fingerprint
    assert len({cfg.fingerprint for cfg in (band_settings(), previous.settings(), settings())}) == 3
    assert settings().execution.taker.post_minimum_price == D(".50")
    assert settings().execution.taker.post_maximum_price == D(".95")
    with pytest.raises(ValueError, match="M4_BAND_ABANDON_REQUIRES_DYNAMIC_POST_BAND"):
        M4LiveSettings(execution_revision="m4-taker-budget-v1", execution={"mode": "taker", "taker": {
            "post_price_band_retry_policy": ABANDON_FIRST_SIGNED_BAND}})


@pytest.mark.parametrize("anchor", [".48", ".94", ".97", ".98"])
async def test_initial_cap_drop_has_no_preparation_post_or_later_revival(tmp_path, monkeypatch, anchor):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=anchor, response="KILLED")
    try:
        row = m.ledger.rows()[0]
        ep = m.restored_opportunity(row)
        root = m.ledger.root_plan(row["id"])
        proof = m.ledger.price_band_abandonment(row["id"])
        assert row["state"] == "SKIPPED" and row["reason"] == POST_PRICE_OUTSIDE_BAND
        assert proof["policy"] == ABANDON_FIRST_SIGNED_BAND
        assert proof["reason"] == SIGNED_LIMIT_BAND_ABANDONED
        assert proof["pricing"]["stage"] == "INITIAL"
        assert D(proof["pricing"]["limit_price"]) == D(anchor)+D(".01")
        assert proof["pricing_origin"] == root["pricing_origin"]
        assert proof["pricing"]["signal_age_us"] < 10_000_000
        assert rejected_price_band_valid(root, proof["pricing"], row["token"])
        assert not quote_valid(root, {**root, "pricing": proof["pricing"]}, row["token"], proof["pricing"])
        for age, ask in ((1, ".50"), (10, ".89"), (130, ".60"), (240, ".80")):
            previous.move(m, f, c, ep, age=age, ask=ask)
            await m.refresh_account()
            await m.submit(ep)
        assert not calls and not m.pending and not m.ledger.attempts()
        assert not m.ledger.facts("taker_pricing_quote")
        assert m.ledger.committed() == m.ledger.instruction_slots() == 0
        assert m.ledger.price_band_abandonment(row["id"]) == proof
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("delay", [0, 10, 130])
async def test_interrupted_initial_check_cannot_be_rescued_by_later_legal_ask(tmp_path, monkeypatch, delay):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        # Simulate the crash window between root-plan durability and the first
        # price check using a separate task; no production data is modified.
        ep = op("interrupted_initial", price=".97")
        m.ledger.observe(ep, minimum=5, elapsed=m.elapsed())
        m.ledger.plan_signal(ep.episode_id, D(".01"), 5,
                            pricing_origin=origin(ep.signal_start_mono, c.now, m.elapsed()))
        m.pending = {ep.episode_id: ep}
        previous.move(m, f, c, ep, age=delay, ask=".89", stale=True)
        # No account/network/book freshness is required to reject the known cap.
        m.account = None
        await m.submit(ep)
        proof = m.ledger.price_band_abandonment(ep.episode_id)
        assert proof["pricing"]["stage"] == "INITIAL"
        assert proof["pricing"]["signal_age_us"] == delay*1_000_000
        assert D(proof["pricing"]["limit_price"]) == D(".98")
        assert not calls and not m.ledger.attempts()
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_initial_cap_one_is_rejected_without_a_synthetic_executable_quote(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = op("initial_cap_one", price=".99")
        m.ledger.observe(ep, minimum=5, elapsed=m.elapsed())
        m.ledger.plan_signal(ep.episode_id, D(".01"), 5,
                            pricing_origin=origin(ep.signal_start_mono, c.now, m.elapsed()))
        m.pending = {ep.episode_id: ep}
        await m.submit(ep)
        proof = m.ledger.price_band_abandonment(ep.episode_id)
        assert D(proof["pricing"]["limit_price"]) == 1
        assert proof["pricing"]["stage"] == "INITIAL"
        assert not calls and not m.ledger.attempts()
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("anchor,cap", [(".49", ".50"), (".93", ".94")])
async def test_legal_initial_boundaries_and_improved_fills_are_retained(tmp_path, monkeypatch, anchor, cap):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=anchor)
    query = None
    try:
        ep = next(iter(m.pending.values()))
        f.m1_pending_received.append(c.now-5)  # admitted FAK ignores queue backlog
        await m.submit(ep)
        attempt = m.ledger.attempts()[0]
        assert D(json.loads(attempt["plan"])["limit_price"]) == D(cap)
        order, trade = evidence(m, e, attempt["order_id"], "5", price=".44")
        query, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(attempt["order_id"])
        row = m.ledger.row(ep.episode_id)
        assert len(calls) == 1 and D(row["filled"]) == 5 and D(row["spent"]) > D("2.20")
        assert not m.ledger.price_band_abandonment(ep.episode_id)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


@pytest.mark.parametrize("ask", [".49", ".95"])
async def test_later_fresh_ask_rejection_stops_zero_match_retries(tmp_path, monkeypatch, ask):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        previous.move(m, f, c, ep, age=10, ask=ask)
        await m.refresh_account()
        await m.submit(ep)
        proof = m.ledger.price_band_abandonment(ep.episode_id)
        assert proof["pricing"]["stage"] == "BEST_ASK"
        assert m.ledger.row(ep.episode_id)["reason"] == SIGNED_LIMIT_BAND_ABANDONED
        previous.move(m, f, c, ep, age=11, ask=".89")
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.committed() == 0
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("first_status,final_status", [("CONFIRMED", "CONFIRMED"), ("MINED", "CONFIRMED"), ("MINED", "FAILED")])
async def test_later_drop_preserves_confirmed_and_unsettled_budget(tmp_path, monkeypatch, first_status, final_status):
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
        previous.move(m, f, c, ep, age=10, ask=".95")
        await m.refresh_account()
        await m.submit(ep)
        assert m.ledger.budget_used(ep.episode_id) == used
        assert D(m.ledger.row(ep.episode_id)["reserved"]) == m.ledger.pending_cost(ep.episode_id)
        trade["status"] = final_status
        await m.reconcile(oid)
        previous.move(m, f, c, ep, age=12, ask=".80")
        await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert len(calls) == 1 and row["state"] == "CUTOFF"
        assert row["reason"] == SIGNED_LIMIT_BAND_ABANDONED and D(row["reserved"]) == 0
        assert D(row["filled"]) == (3 if final_status == "CONFIRMED" else 0)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if query:
            query.close()


async def test_initial_drop_survives_same_market_restart(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".97", response="KILLED")
    ep = m.restored_opportunity(m.ledger.rows()[0])
    proof = m.ledger.price_band_abandonment(ep.episode_id)
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".97", response="KILLED", recover=True)
    try:
        previous.move(m, f, c, ep, age=131, ask=".89")
        await m.refresh_account()
        await m.submit(ep)
        assert not m.pending and not calls and not m.ledger.attempts()
        assert m.ledger.price_band_abandonment(ep.episode_id) == proof
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("corrupt", ["token", "clock", "price", "policy", "stage"])
async def test_corrupt_initial_drop_evidence_fails_closed(tmp_path, monkeypatch, corrupt):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".97", response="KILLED")
    try:
        key = m.ledger.rows()[0]["id"]
        proof = m.ledger.price_band_abandonment(key)
        q = proof["pricing"]
        if corrupt == "token":
            q["token_id"] = "other"
        elif corrupt == "clock":
            q["signal_age_us"] += 1
        elif corrupt == "price":
            q["limit_price"] = ".89"
        elif corrupt == "policy":
            proof["policy"] = ABANDON_BEST_ASK_BAND
        else:
            q["stage"] = "ANCHOR"
        with m.ledger.db:
            m.ledger.db.execute("UPDATE facts SET payload=? WHERE kind='taker_price_band_abandonment' AND identity=?", (json.dumps(proof), key))
        with pytest.raises(ValueError, match="M4_BAND_ABANDONMENT_EVIDENCE_INVALID"):
            m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("saved", [band_settings(), previous.settings()])
async def test_historical_initial_cap_can_still_wait_for_legal_ask(tmp_path, monkeypatch, saved):
    monkeypatch.setattr(budget, "settings", lambda mode="taker": saved)
    m, e, f, c, calls, t = await budget.fixture(tmp_path, monkeypatch, price=".97", response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        assert not calls and not m.ledger.price_band_abandonment(ep.episode_id)
        previous.move(m, f, c, ep, age=10, ask=".89")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1 and D(json.loads(m.ledger.attempts()[0]["plan"])["limit_price"]) == D(".89")
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("old_settings", [band_settings(), previous.settings()])
async def test_startup_restores_old_same_market_next_market_uses_new_rule(tmp_path, monkeypatch, old_settings):
    from test_m4_live_startup import EPOCH, FUNDER, harness
    from polymaker.m4_config import STRATEGY
    from polymaker.m4_live_taker import M4LiveLedger

    h = harness(tmp_path, monkeypatch)
    configured = settings()
    identity = {"strategy": STRATEGY, "mode": "LIVE", "funder": FUNDER,
                "market_id": h.meta.condition_id, "settings_hash": old_settings.fingerprint}
    path = h.startup.root / "ledgers" / f"{h.meta.slug}.sqlite3"
    ledger = M4LiveLedger(path, identity, settings=old_settings, fees=budget.legacy.FEES)
    ledger.initialize_session("offline_first_band_recovery", old_settings.model_dump(mode="json"), asdict(h.meta))
    ledger.close()
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.settings == old_settings
    finally:
        ready.reads.close()
    h.clock_values[:] = [EPOCH+301, EPOCH+302]
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.settings.execution.taker.post_price_band_retry_policy == ABANDON_FIRST_SIGNED_BAND
        assert ready.identity["settings_hash"] == configured.fingerprint
    finally:
        ready.reads.close()


@pytest.mark.parametrize("check", [
    previous.test_unknown_is_reconciled_before_outside_price_can_stop_entry,
    previous.test_stale_outside_book_does_not_count_as_fresh_band_rejection,
    previous.test_unverified_tick_outside_band_waits_then_can_retry,
    previous.test_legal_ask_zero_match_continues_until_t240,
])
async def test_existing_execution_protections_with_new_policy(tmp_path, monkeypatch, check):
    monkeypatch.setattr(previous, "settings", settings)
    await check(tmp_path, monkeypatch)


@pytest.mark.parametrize("corrupt", [None, "clock", "token", "policy", "missing"])
def test_read_only_watch_validates_initial_drop_against_saved_policy(tmp_path, corrupt):
    import hashlib
    import sqlite3
    from test_m4_taker_watch import EPOCH, fixture as watch_fixture, watch

    path, journal = watch_fixture(tmp_path, state="TERMINAL")
    root = {"opportunity_id": "signal", "limit_price": ".98",
            "post_price_band_retry_policy": ABANDON_FIRST_SIGNED_BAND,
            "post_price_band": {"policy": "POST_SIGNED_LIMIT_BAND_V1", "minimum": "0.50", "maximum": "0.95"},
            "pricing_origin": origin(10, 10, 20)}
    q = {"opportunity_id": "signal", "token_id": "1", "stage": "INITIAL", "limit_price": ".98",
         "captured_monotonic": 10, "captured_elapsed_s": 20,
         "boot_id": root["pricing_origin"]["boot_id"], "signal_age_us": 0}
    proof = {"policy": ABANDON_FIRST_SIGNED_BAND, "reason": SIGNED_LIMIT_BAND_ABANDONED,
             "opportunity_id": "signal", "post_price_band": root["post_price_band"],
             "pricing_origin": root["pricing_origin"], "pricing": q}
    if corrupt == "clock":
        q["signal_age_us"] += 1
    if corrupt == "token":
        q["token_id"] = "other"
    if corrupt == "policy":
        proof["policy"] = ABANDON_BEST_ASK_BAND
    with sqlite3.connect(path) as db:
        saved = json.loads(db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()[0])
        saved["execution"]["taker"] = settings().model_dump(mode="json")["execution"]["taker"]
        db.execute("UPDATE metadata SET value=? WHERE key='settings'", (json.dumps(saved),))
        db.execute("ALTER TABLE opportunities ADD COLUMN token TEXT")
        db.execute("UPDATE opportunities SET token='1',price='.97',posts=0,reserved='0',state='SKIPPED',reason=?", (POST_PRICE_OUTSIDE_BAND,))
        db.execute("DELETE FROM taker_attempts")
        db.execute("UPDATE facts SET payload=? WHERE kind='taker_plan'", (json.dumps(root),))
        if corrupt != "missing":
            db.execute("INSERT INTO facts VALUES ('taker_price_band_abandonment','signal',?)", (json.dumps(proof),))
    journal.write_text("\n".join(line for line in journal.read_text().splitlines()
                                if json.loads(line)["kind"] == "sm_m4_signal")+"\n")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = watch.inspect_market(tmp_path, EPOCH, EPOCH+320)
    expected = ("INITIAL_PRICE_BAND_REJECTION_MISSING" if corrupt == "missing"
                else "PRICE_BAND_ABANDONMENT_EVIDENCE_INVALID")
    assert (expected in result["alerts"]) == bool(corrupt)
    if not corrupt:
        assert not result["alerts"]
    assert result["native_posts"] == result["posted_signal_count"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
