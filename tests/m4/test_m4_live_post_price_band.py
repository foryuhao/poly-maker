"""Signed-limit risk band: real SDK with synthetic HTTP and isolated ledgers."""

import hashlib
import json
from dataclasses import asdict, replace
from decimal import Decimal as D

import pytest
import test_m4_live_budget as budget
from m4_helpers import close, op
from test_m4_live_best_ask import dynamic_settings, move
from test_m4_live_startup import EPOCH, FUNDER, harness
from test_m4_live_task import evidence, serve

from polymaker.m4_config import STRATEGY
from polymaker.m4_frequency_signal import PARAMETERS
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_live_price_band import (
    ANCHOR_PRICE_FLOOR,
    POST_PRICE_OUTSIDE_BAND,
    POST_SIGNED_LIMIT_BAND,
    price_in_band,
)
from polymaker.m4_live_pricing import DYNAMIC, FIXED, origin
from polymaker.m4_live_taker import M4LiveLedger


def band_settings(mode="taker", **bounds):
    return M4LiveSettings(minimum_price=".50", execution_revision="m4-taker-budget-v1",
                          execution={"mode": mode, "taker": {
                              "pricing_policy": DYNAMIC,
                              "price_band_policy": POST_SIGNED_LIMIT_BAND, **bounds}})


async def fixture(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(budget, "settings", band_settings)
    return await budget.fixture(tmp_path, monkeypatch, **kwargs)


@pytest.mark.parametrize("price,allowed", [
    (".499", False), (".50", True), (".949", True), (".95", False),
    (".99", False), ("NaN", False), ("Infinity", False), ("-1", False),
])
def test_exact_boundaries(price, allowed):
    band = {"policy": POST_SIGNED_LIMIT_BAND, "minimum": ".50", "maximum": ".95"}
    assert price_in_band(price, band) is allowed


@pytest.mark.parametrize("bounds", [
    {"post_minimum_price": ".95"}, {"post_maximum_price": ".50"},
    {"post_minimum_price": "NaN"}, {"post_maximum_price": "Infinity"},
    {"post_minimum_price": "-.01"}, {"post_maximum_price": "1.01"},
])
def test_invalid_config_fails_closed(bounds):
    with pytest.raises(ValueError):
        band_settings(**bounds)


def test_configuration_and_old_serialization_are_versioned(tmp_path):
    (tmp_path / "config/m4-live").mkdir(parents=True)
    (tmp_path / "config/m4.toml").write_text('minimum_price="0.50"\n')
    (tmp_path / "config/m4-live/execution.toml").write_text(
        'mode="taker"\n[taker]\nprice_band_policy="POST_SIGNED_LIMIT_BAND_V1"\n'
        'post_minimum_price="0.45"\npost_maximum_price="0.90"\n')
    cfg = M4LiveSettings.load(tmp_path)
    assert cfg.execution.taker.post_minimum_price == D(".45")
    assert cfg.execution.taker.post_maximum_price == D(".90")
    assert M4LiveSettings.model_validate(cfg.model_dump()).fingerprint == cfg.fingerprint
    for old in (budget.settings(), dynamic_settings()):
        raw = old.model_dump(mode="json")
        assert not {"price_band_policy", "post_minimum_price", "post_maximum_price"} & raw["execution"]["taker"].keys()
        expected = hashlib.sha256(json.dumps({"settings": raw, "signal": PARAMETERS}, sort_keys=True).encode()).hexdigest()
        restored = M4LiveSettings.model_validate(raw)
        assert restored.fingerprint == old.fingerprint == expected
        assert restored.execution.taker.price_band_policy == ANCHOR_PRICE_FLOOR
    for revision in ("m4-taker-single-fak-v1", "m4-taker-task-v1"):
        with pytest.raises(ValueError, match="M4_POST_PRICE_BAND_REQUIRES_BUDGET_TAKER"):
            M4LiveSettings(execution_revision=revision, execution={"mode": "taker", "taker": {
                "price_band_policy": POST_SIGNED_LIMIT_BAND}})


@pytest.mark.parametrize("anchor,first", [
    (".49", ".50"), (".48", None), (".94", None), (".96", None),
])
async def test_anchor_is_observed_and_each_selected_limit_is_filtered(tmp_path, monkeypatch, anchor, first):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=anchor, response="KILLED")
    try:
        assert len(m.pending) == 1  # No strategy anchor-price filter.
        ep = next(iter(m.pending.values()))
        root = m.ledger.root_plan(ep.episode_id)
        assert D(root["limit_price"]) == D(anchor) + D(".01")
        assert root["post_price_band"] == m.ledger.post_price_band
        f.m1_pending_received.append(c.now - 5)  # Admitted task ignores queue backlog.
        await m.submit(ep)
        if first:
            assert len(calls) == 1
            assert D(json.loads(m.ledger.attempts()[0]["plan"])["limit_price"]) == D(first)
        else:
            assert not calls and not m.ledger.attempts()
            assert not m.ledger.facts("taker_pricing_quote")
            assert m.ledger.committed() == m.ledger.instruction_slots() == 0
            assert m.ledger.row(ep.episode_id)["state"] == "OBSERVED"
            assert m.guard_failure(ep.episode_id) == POST_PRICE_OUTSIDE_BAND
            assert m.retry_after[ep.episode_id] >= c.now + .5
        move(m, f, c, age=10, ask=".94")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == (2 if first else 1)
        assert D(json.loads(m.ledger.attempts()[-1]["plan"])["limit_price"]) == D(".94")
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_unsigned_anchor_plus_tick_one_can_wait_for_a_legal_ask(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        # An anchor captured earlier may now be one tick below1. No valid
        # executable quote at1 is manufactured to keep its durable reference.
        ep = op("anchor99", price=".99")
        m.ledger.observe(ep, minimum=5, elapsed=m.elapsed())
        m.ledger.plan_signal(ep.episode_id, D(".01"), 5,
                             pricing_origin=origin(ep.signal_start_mono, c.now, m.elapsed()))
        m.pending = {ep.episode_id: ep}
        assert D(m.ledger.root_plan(ep.episode_id)["limit_price"]) == 1
        await m.submit(ep)
        assert not calls and not m.ledger.attempts()
        move(m, f, c, age=10, ask=".94")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1
        assert D(json.loads(m.ledger.attempts()[-1]["plan"])["limit_price"]) == D(".94")
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_every_retry_filters_fresh_ask_and_does_not_churn_evidence(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        for age, ask in ((10, ".95"), (11, ".49"), (12, ".99")):
            move(m, f, c, age=age, ask=ask)
            await m.refresh_account()
            for _ in range(5):
                await m.submit(ep)
            assert len(calls) == len(m.ledger.attempts()) == 1
            assert len(m.ledger.facts("taker_pricing_quote")) == 1
            assert m.guard_failure(ep.episode_id) == POST_PRICE_OUTSIDE_BAND
        for age, ask in ((13, ".50"), (14, ".94")):
            move(m, f, c, age=age, ask=ask)
            await m.refresh_account()
            await m.submit(ep)
            assert D(json.loads(m.ledger.attempts()[-1]["plan"])["limit_price"]) == D(ask)
        assert len(calls) == len({a["order_id"] for a in m.ledger.attempts()}) == 3
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_custom_bounds_and_legal_price_with_no_cent_compatible_quantity(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "settings", lambda mode="taker": band_settings(
        mode, post_minimum_price=".60", post_maximum_price=".95"))
    m, e, f, c, calls, t = await budget.fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c, ask=".50")
        await m.refresh_account()
        await m.submit(ep)
        assert not calls and m.guard_failure(ep.episode_id) == POST_PRICE_OUTSIDE_BAND
        move(m, f, c, age=11, ask=".949", tick=".001")
        await m.submit(ep)
        assert not calls and m.guard_failure(ep.episode_id) == "M4_REMAINDER_WAIT_LEGAL_REPRICE"
        assert not m.ledger.facts("taker_pricing_quote")
        move(m, f, c, age=12, ask=".94", tick=".001")
        await m.submit(ep)
        assert len(calls) == 1
        assert D(json.loads(m.ledger.attempts()[0]["plan"])["limit_price"]) == D(".94")
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("ask", [".49", ".95", ".99"])
async def test_historical_dynamic_policy_does_not_acquire_new_post_bounds(tmp_path, monkeypatch, ask):
    old = M4LiveSettings(minimum_price=".50", execution_revision="m4-taker-budget-v1",
                         execution={"mode": "taker", "taker": {"pricing_policy": DYNAMIC}})
    monkeypatch.setattr(budget, "settings", lambda mode: old)
    m, e, f, c, calls, t = await budget.fixture(tmp_path, monkeypatch, response="KILLED")
    try:
        ep = move(m, f, c, ask=ask)
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.post_price_band is None
        assert D(json.loads(m.ledger.attempts()[0]["plan"])["limit_price"]) == D(ask)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("status", ["CONFIRMED", "MINED"])
async def test_topups_obey_band_keep_pending_reserve_and_price_improvement(tmp_path, monkeypatch, status):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".50")
    q = None
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        oid = m.ledger.attempts()[0]["order_id"]
        order, trade = evidence(m, e, oid, "3", price=".40", status=status)
        q, _ = serve(monkeypatch, t, order, trade)
        await m.reconcile(oid)
        used = m.ledger.budget_used(ep.episode_id)
        actual_cost = budget.all_in_cost(".40", 3, budget.legacy.FEES)
        # Unconfirmed matches keep the existing rounding/tolerance reserve.
        assert actual_cost <= used <= actual_cost + D(".000011")
        row = m.ledger.row(ep.episode_id)
        assert D(row["filled"]) == (3 if status == "CONFIRMED" else 0)
        assert m.ledger.pending_cost(ep.episode_id) == (used if status == "MINED" else 0)
        move(m, f, c, age=10, ask=".95")
        await m.refresh_account()
        await m.submit(ep)
        assert len(calls) == 1 and m.ledger.budget_used(ep.episode_id) == used
        move(m, f, c, age=11, ask=".80")
        await m.submit(ep)
        assert len(calls) == 2
        plan = json.loads(m.ledger.attempts()[-1]["plan"])
        assert D(plan["prior_spent_usdc"]) == used
        assert D(plan["planned_cost_usdc"]) + used <= 5
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()
        if q:
            q.close()


async def test_unknown_restart_keeps_clock_cash_slot_and_does_not_repost(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, response="TIMEOUT")
    ep = next(iter(m.pending.values()))
    await m.submit(ep)
    root, commitment = m.ledger.root_plan(ep.episode_id), m.ledger.committed()
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, recover=True)
    try:
        move(m, f, c, age=12, ask=".94")
        await m.refresh_account()
        await m.submit(ep)
        assert not calls and len(m.ledger.attempts()) == 1
        assert m.ledger.root_plan(ep.episode_id) == root
        assert m.ledger.committed() == commitment and m.ledger.instruction_slots() == 1
        assert m.ledger.row(ep.episode_id)["state"] == "UNKNOWN"
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_band_wait_survives_restart_and_finishes_as_normal_filter(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".96")
    ep = next(iter(m.pending.values()))
    await m.submit(ep)
    root = m.ledger.root_plan(ep.episode_id)
    await close(m, e)
    t.close()
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".96", recover=True)
    try:
        assert m.ledger.root_plan(ep.episode_id) == root
        move(m, f, c, age=12, ask=".94")
        e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
        e.time_anchor = c.now
        await m.submit(ep)
        row = m.ledger.row(ep.episode_id)
        assert not calls and not m.ledger.attempts()
        assert not m.ledger.facts("taker_pricing_quote")
        assert row["state"] == "SKIPPED" and row["reason"] == POST_PRICE_OUTSIDE_BAND
        assert m.ledger.committed() == m.ledger.instruction_slots() == 0
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


async def test_eligible_then_unsigned_failure_is_not_relabelled_as_price_only(tmp_path, monkeypatch):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".96")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        move(m, f, c, age=10, ask=".94")
        await m.refresh_account()

        async def failed(*args, **kwargs):
            raise ValueError("synthetic signing failure")

        monkeypatch.setattr(e.gateway, "prepare", failed)
        await m.submit(ep)
        assert not calls and not m.ledger.attempts()
        e.official_time = int(e.slug.rsplit("-", 1)[1]) + 240
        e.time_anchor = c.now
        m.finish_pending()
        assert m.ledger.row(ep.episode_id)["reason"] == "M4_T240_RESIDUAL"
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("boundary", ["submit", "native"])
async def test_exact_signed_quote_is_rechecked_after_signing(tmp_path, monkeypatch, boundary):
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch)
    ep = move(m, f, c, ask=".94")
    await m.refresh_account()
    original = m.ledger.post_price_allowed
    gate = e.gateway.prepare if boundary == "submit" else m.ledger.post_start

    async def signed(*args, **kwargs):
        result = await gate(*args, **kwargs)
        monkeypatch.setattr(m.ledger, "post_price_allowed", lambda price: False)
        return result

    def native(*args, **kwargs):
        result = gate(*args, **kwargs)
        monkeypatch.setattr(m.ledger, "post_price_allowed", lambda price: False)
        return result

    monkeypatch.setattr(e.gateway if boundary == "submit" else m.ledger,
                        "prepare" if boundary == "submit" else "post_start",
                        signed if boundary == "submit" else native)
    try:
        await m.submit(ep)
        assert not calls
        if boundary == "native":
            assert m.ledger.attempts()[0]["state"] == "UNSENT"
        # At the submit boundary, preparation rejects before the durable attempt.
        assert not m.ledger.unresolved(ep.episode_id)
        monkeypatch.setattr(m.ledger, "post_price_allowed", original)
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("change", ["cutoff", "stop", "cash", "tick"])
async def test_band_keeps_other_native_protections(tmp_path, monkeypatch, change):
    from test_m4_live_best_ask import test_repriced_native_post_still_rechecks_all_protections

    monkeypatch.setattr(budget, "settings", band_settings)
    import test_m4_live_best_ask as best_ask
    monkeypatch.setattr(best_ask, "dynamic_settings", band_settings)
    await test_repriced_native_post_still_rechecks_all_protections(tmp_path, monkeypatch, change)


async def test_signal_quality_still_rejects_a_stale_new_signal(tmp_path, monkeypatch):
    from polymaker.m4_live_budget_market import BudgetMarket

    collect = BudgetMarket.collect
    monkeypatch.setattr(BudgetMarket, "collect", lambda self: None)
    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".49")
    try:
        f.books = {token: replace(book, received_mono=c.now - 1) for token, book in f.books.items()}
        collect(m)
        assert not m.pending and not m.ledger.rows() and not calls
    finally:
        await close(m, e)
        t.close()


async def test_watch_uses_saved_post_band_and_allows_low_anchor(tmp_path, monkeypatch):
    import shutil
    import sqlite3
    from pathlib import Path

    from test_m4_taker_watch import watch

    m, e, f, c, calls, t = await fixture(tmp_path, monkeypatch, price=".49", response="KILLED")
    try:
        ep = next(iter(m.pending.values()))
        await m.submit(ep)
        await e.journal.flush()
        epoch = int(e.slug.rsplit("-", 1)[1])
        root = tmp_path / "research_outputs/m4_live"
        path = root / "ledgers" / (e.slug + ".sqlite3")
        path.parent.mkdir(parents=True)
        (root / "journal").mkdir()
        with sqlite3.connect(path) as target:
            m.ledger.db.backup(target)
        source = Path(e.journal.journal.path)
        shutil.copyfile(source, root / "journal" / source.name)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = watch.inspect_market(tmp_path, epoch, epoch + 40)
        assert not result["alerts"]
        assert result["native_posts"] == 1 and result["confirmed_filled_signal_count"] == 0
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        # Even a consistent but out-of-band frozen quote must be flagged.
        with sqlite3.connect(path) as target:
            target.execute("UPDATE taker_attempts SET quote=json_set(quote,'$.price','.95')")
        assert "POST_PRICE_SIZE_RULE_VIOLATION" in watch.inspect_market(tmp_path, epoch, epoch + 40)["alerts"]
    finally:
        await close(m, e)
        t.close()


@pytest.mark.parametrize("policy", [FIXED, DYNAMIC])
async def test_same_market_uses_old_settings_next_market_saves_new_band(tmp_path, monkeypatch, policy):
    h = harness(tmp_path, monkeypatch)
    saved = M4LiveSettings(minimum_price=".50", execution_revision="m4-taker-budget-v1",
                          execution={"mode": "taker", "taker": {"pricing_policy": policy}})
    configured = band_settings()
    identity = {"strategy": STRATEGY, "mode": "LIVE", "funder": FUNDER,
                "market_id": h.meta.condition_id, "settings_hash": saved.fingerprint}
    path = h.startup.root / "ledgers" / f"{h.meta.slug}.sqlite3"
    ledger = M4LiveLedger(path, identity, settings=saved, fees=budget.legacy.FEES)
    ledger.initialize_session("offline_price_band_recovery", saved.model_dump(mode="json"), asdict(h.meta))
    ledger.close()
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.settings == saved
        assert ready.identity["settings_hash"] == saved.fingerprint
    finally:
        ready.reads.close()
    h.clock_values[:] = [EPOCH + 301, EPOCH + 302]
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.settings.execution.taker.price_band_policy == POST_SIGNED_LIMIT_BAND
        assert ready.identity["settings_hash"] == configured.fingerprint
    finally:
        ready.reads.close()
