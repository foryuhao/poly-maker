"""Ten-second signal-clock fallback: real SDK, synthetic HTTP, isolated ledgers."""

import hashlib
import json
import shutil
import sqlite3
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest
import test_m4_live_budget as budget
from m4_helpers import close
from test_m4_live_task import evidence, serve

from polymaker.m4_frequency_signal import PARAMETERS
from polymaker.m4_live_config import M4LiveSettings, all_in_cost
from polymaker.m4_live_numeric import fak_amounts_compatible
from polymaker.m4_live_pricing import DYNAMIC, WAIT_US, quote_valid, signal_age


def dynamic_settings(mode="taker"):
    return M4LiveSettings(execution_revision="m4-taker-budget-v1",
                          execution={"mode": mode, "taker": {"pricing_policy": DYNAMIC}})


async def fixture(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(budget, "settings", dynamic_settings)
    return await budget.fixture(tmp_path, monkeypatch, **kwargs)


def move(m, f, c, *, age=10, ask=".80", tick=".01"):
    ep = next(iter(m.pending.values()))
    root = m.ledger.root_plan(ep.episode_id)
    c.now = float(D(str(root["pricing_origin"]["signal_monotonic"])) + D(str(age)))
    ask, tick = D(ask), D(tick)
    f.books[ep.token_id] = replace(f.books[ep.token_id], bids=((ask-tick, D(100)),),
                                  asks=((ask, D(100)),), tick=tick, source_time=f.wall(),
                                  received_mono=c.now, reported_bid=None, reported_ask=None)
    f.verify_ticks({ep.token_id: tick})
    return ep


def test_saved_settings_fingerprint_is_unchanged_and_new_policy_is_distinct():
    cfg = budget.settings()
    saved = cfg.model_dump(mode="json")
    assert "pricing_policy" not in saved["execution"]["taker"]
    expected = hashlib.sha256(json.dumps({"settings": saved, "signal": PARAMETERS}, sort_keys=True).encode()).hexdigest()
    assert cfg.fingerprint == expected == M4LiveSettings.model_validate(saved).fingerprint
    assert dynamic_settings().fingerprint != expected
    assert dynamic_settings().model_dump(mode="json")["execution"]["taker"]["anchor_wait_sec"] == 10


@pytest.mark.parametrize("age,ask,expected", [
    (0, ".80", ".71"), (9.999999, ".80", ".71"), (10, ".80", ".80"),
    (10.000001, ".81", ".81"), (20, ".60", ".60"), (10, ".92", ".92"),
])
async def test_exact_signal_boundary_and_budget_sizing(tmp_path, monkeypatch, age, ask, expected):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c, age=age, ask=ask)
        await m.refresh_account()
        # Intent transport deliberately has no queue-age gate, in either phase.
        f.m1_pending_received.append(c.now-5)
        await m.submit(ep)
        assert len(calls) == 1
        a = m.ledger.attempts()[0]
        plan = json.loads(a["plan"])
        assert D(plan["limit_price"]) == D(expected)
        qty = D(plan["quantity"])
        assert fak_amounts_compatible(D(expected), qty)
        assert all_in_cost(expected, qty, budget.legacy.FEES) <= 5
        assert plan["pricing"]["stage"] == ("ANCHOR" if age < 10 else "BEST_ASK")
        if expected == ".92":
            assert qty == 5  # Retain .1 shares and cents precision; no .4-share rounding up.
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_retries_reprice_each_new_attempt_at_current_ask_not_count(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c, age=0)
        # Six affirmative zeros in <10 seconds do not enable fallback.
        for _ in range(6):
            await m.submit(ep)
        assert all(D(json.loads(a["plan"])["limit_price"]) == D(".71") for a in m.ledger.attempts())
        for age, ask in [(10, ".80"), (11, ".82"), (12, ".79")]:
            move(m, f, c, age=age, ask=ask)
            await m.refresh_account()
            await m.submit(ep)
            assert D(json.loads(m.ledger.attempts()[-1]["plan"])["limit_price"]) == D(ask)
        ids = [a["order_id"] for a in m.ledger.attempts()]
        assert len(calls) == len(set(ids)) == 9
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("fault", ["stale", "source", "disconnect", "empty", "tick", "generation", "token", "off_grid"])
async def test_fallback_waits_for_valid_fresh_same_token_quote(tmp_path, monkeypatch, fault):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c)
        await m.refresh_account()
        book = f.books[ep.token_id]
        if fault == "stale":
            book = replace(book, received_mono=c.now-.200001)
        elif fault == "source":
            book = replace(book, source_time=f.wall()-.500001)
        elif fault == "disconnect":
            f.connected = False
        elif fault == "empty":
            book = replace(book, asks=())
        elif fault == "tick":
            f.tick_verified.pop(ep.token_id)
        elif fault == "generation":
            book = replace(book, snapshot_generation=f.generation-1)
        elif fault == "token":
            book = replace(book, token_id="other")
        else:
            book = replace(book, asks=((D(".805"), D(100)),))
        f.books[ep.token_id] = book
        await m.submit(ep)
        assert not calls and not m.ledger.attempts()
        assert ep.episode_id in m.pending
        assert m.guard_failure(ep.episode_id) == "M4_BEST_ASK_REPRICE_WAIT_FRESH_BOOK"
        f.connected = True
        move(m, f, c)
        f.books[ep.token_id] = replace(f.books[ep.token_id], token_id=ep.token_id,
                                     snapshot_generation=f.generation)
        await m.submit(ep)
        assert len(calls) == 1
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("boundary", ["sign", "native"])
async def test_sign_or_fsync_crossing_ten_seconds_is_proven_unsent_then_repriced(tmp_path, monkeypatch, boundary):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    ep = move(m, f, c, age=9.999)
    await m.refresh_account()
    if boundary == "sign":
        original = e.gateway.prepare

        async def cross(*args, **kwargs):
            result = await original(*args, **kwargs)
            move(m, f, c, age=10.01)
            return result

        monkeypatch.setattr(e.gateway, "prepare", cross)
    else:
        original = m.ledger.post_start

        def cross(*args, **kwargs):
            result = original(*args, **kwargs)
            move(m, f, c, age=10.01)
            return result

        monkeypatch.setattr(m.ledger, "post_start", cross)
    try:
        await m.submit(ep)
        assert not calls
        first = m.ledger.attempts()[0]
        assert first["state"] == "UNSENT"
        assert not m.ledger.unresolved(ep.episode_id)
        if boundary == "sign":
            monkeypatch.setattr(e.gateway, "prepare", original)
        else:
            monkeypatch.setattr(m.ledger, "post_start", original)
        await m.submit(ep)
        assert len(calls) == 1 and len(m.ledger.attempts()) == 2
        assert D(json.loads(m.ledger.attempts()[-1]["plan"])["limit_price"]) == D(".80")
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_restart_does_not_reset_signal_clock_or_resend_unknown(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = next(iter(m.pending.values()))
    await m.submit(ep)
    root = m.ledger.root_plan(ep.episode_id)
    oid = m.ledger.attempts()[0]["order_id"]
    assert len(calls) == 1
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True)
    q = None
    try:
        assert m.ledger.root_plan(ep.episode_id) == root
        move(m, f, c, age=12)
        await m.refresh_account()
        assert not m.guard(ep) and not calls
        assert m.ledger.unresolved(ep.episode_id)
        # Only exact owned terminal-zero evidence releases the old reservation.
        order, trade = evidence(m, e, oid, "0")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        assert not m.ledger.unresolved(ep.episode_id)
        await m.submit(ep)
        assert len(calls) == 1
        assert m.ledger.attempts()[-1]["order_id"] != oid
        assert json.loads(m.ledger.attempts()[-1]["plan"])["pricing"]["stage"] == "BEST_ASK"
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


@pytest.mark.parametrize("status", ["CONFIRMED", "MINED"])
async def test_partial_fill_and_pending_matches_reduce_repriced_remaining_cash(tmp_path, monkeypatch, status):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = next(iter(m.pending.values()))
    q = None
    try:
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "3", status=status)
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        used = m.ledger.budget_used(ep.episode_id)
        assert used > 2
        move(m, f, c, age=12, ask=".80")
        await m.refresh_account()
        await m.submit(ep)
        plan = json.loads(m.ledger.attempts()[-1]["plan"])
        assert len(calls) == 2 and D(plan["limit_price"]) == D(".80")
        assert D(plan["prior_spent_usdc"]) == used
        assert D(plan["planned_cost_usdc"]) + used <= 5
        assert m.ledger.committed() <= 10
        before = len(calls)
        assert not m.guard(ep)  # New ACK cannot be duplicated.
        assert len(calls) == before
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_fine_tick_no_legal_quantity_retains_intent_for_next_ask(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c, age=10, ask=".971", tick=".001")
        await m.refresh_account()
        await m.submit(ep)
        assert not calls and m.ledger.row(ep.episode_id)["state"] in {"OBSERVED", "RESERVED"}
        assert ep.episode_id in m.pending
        move(m, f, c, age=11, ask=".97", tick=".001")
        await m.submit(ep)
        assert len(calls) == 1
        assert D(json.loads(m.ledger.attempts()[0]["plan"])["quantity"]) == 5
    finally:
        await close(m, e)
        t.close()


async def test_new_valid_tick_does_not_keep_obsolete_anchor_cap_gate(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c, age=10, ask=".80", tick=".1")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1
        assert D(json.loads(m.ledger.attempts()[0]["plan"])["limit_price"]) == D(".80")
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("change", ["cutoff", "stop", "cash", "tick"])
async def test_repriced_native_post_still_rechecks_all_protections(tmp_path, monkeypatch, change):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    ep = move(m, f, c)
    await m.refresh_account()
    original = m.ledger.post_start

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "cutoff":
            e.official_time = int(e.slug.rsplit("-", 1)[1])+240
            e.time_anchor = c.now
        elif change == "stop":
            m.stop.set()
        elif change == "cash":
            m.account["official_balance_usd"] = "0"
        else:
            f.tick_verified.pop(ep.token_id)
        return result

    monkeypatch.setattr(m.ledger, "post_start", changed)
    try:
        await m.submit(ep)
        assert not calls and m.ledger.attempts()[0]["state"] == "UNSENT"
        assert not m.ledger.unresolved(ep.episode_id)
        assert m.ledger.committed() <= 10
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_read_only_watcher_validates_higher_ask_against_immutable_evidence(tmp_path, monkeypatch):
    from test_m4_taker_watch import watch

    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c)
        await m.refresh_account()
        await m.submit(ep)
        await e.journal.flush()
        epoch = int(e.slug.rsplit("-", 1)[1])
        root = tmp_path / "research_outputs/m4_live"
        path = root / "ledgers" / (e.slug+".sqlite3")
        path.parent.mkdir(parents=True)
        (root/"journal").mkdir()
        with sqlite3.connect(path) as target:
            m.ledger.db.backup(target)
        source = Path(e.journal.journal.path)
        shutil.copyfile(source, root/"journal"/source.name)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = watch.inspect_market(tmp_path, epoch, epoch+40)
        assert not result["alerts"]
        assert result["native_posts"] == 1 and result["confirmed_filled_signal_count"] == 0
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        with sqlite3.connect(path) as target:
            target.execute("DELETE FROM facts WHERE kind='taker_pricing_quote'")
        result = watch.inspect_market(tmp_path, epoch, epoch+40)
        assert "POST_PRICE_SIZE_RULE_VIOLATION" in result["alerts"]
    finally:
        await close(m, e)
        t.close()


async def test_best_ask_unknown_survives_restart_and_confirms_original_attempt(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = move(m, f, c, age=12, ask=".80")
    await m.refresh_account()
    await m.submit(ep)
    old = m.ledger.attempts()[0]
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True)
    q = None
    try:
        # The new process's books/clock are unrelated to capture-time evidence.
        assert m.ledger.attempts()[0]["plan"] == old["plan"]
        assert m.ledger.unresolved(ep.episode_id) and not calls
        order, trade = evidence(m, e, old["order_id"], "6", price=".80")
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(old["order_id"])
        row = m.ledger.row(ep.episode_id)
        assert D(row["filled"]) == 6 and D(row["spent"]) == D("4.867200")
        assert not m.ledger.unresolved(ep.episode_id) and not calls
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_boundary_wakes_backoff_but_t240_still_forbids_any_post(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    ep = next(iter(m.pending.values()))
    try:
        await m.submit(ep)
        move(m, f, c, age=10)
        m.retry_after[ep.episode_id] = c.now+2
        assert m.entry_retry_ready(m.ledger.row(ep.episode_id), c.now)
        e.official_time = int(e.slug.rsplit("-", 1)[1])+240
        e.time_anchor = c.now
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1
        assert m.ledger.row(ep.episode_id)["state"] == "CUTOFF"
    finally:
        await close(m, e)
        t.close()


def test_reboot_signal_clock_falls_back_to_saved_official_market_time():
    root = {"pricing_origin": {"policy": DYNAMIC, "wait_us": WAIT_US,
            "boot_id": "old", "signal_monotonic": 50000, "signal_elapsed_us": 20_000_000}}
    assert signal_age(root, 1, 30, "new") == WAIT_US
    assert signal_age(root, 50009.999999, 999, "old") == WAIT_US-1


@pytest.mark.parametrize("fault", ["ask", "age", "token", "freshness", "missing_fact"])
async def test_tampered_repricing_evidence_is_rejected_during_recovery(tmp_path, monkeypatch, fault):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c)
        await m.refresh_account()
        await m.submit(ep)
        a = m.ledger.attempts()[0]
        plan = json.loads(a["plan"])
        q = dict(plan["pricing"])
        if fault == "ask":
            q["best_ask"] = ".90"
        elif fault == "age":
            q["signal_age_us"] = 0
        elif fault == "token":
            q["token_id"] = "foreign"
        elif fault == "freshness":
            q["received_monotonic"] = q["captured_monotonic"]-1
        else:
            q = None
        assert not quote_valid(m.ledger.root_plan(ep.episode_id), plan, ep.token_id, q)
        if q is None:
            with m.ledger.db:
                m.ledger.db.execute("DELETE FROM facts WHERE kind='taker_pricing_quote'")
            with pytest.raises(ValueError, match="PRICING_CONFLICT"):
                m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
