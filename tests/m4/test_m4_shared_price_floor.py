"""Shared anchor threshold, durable recovery and synthetic FAK submission."""
from dataclasses import asdict
from decimal import Decimal as D

import m4_helpers
import pytest
import test_m4_live_taker as legacy
from m4_helpers import close
from test_m4_live_startup import EPOCH, FUNDER, harness
from test_m4_live_task import evidence, serve

from polymaker.m4_config import STRATEGY, M4Config
from polymaker.m4_live_config import M4LiveSettings, all_in_cost
from polymaker.m4_live_taker import M4LiveLedger


def live_settings(floor=".50"):
    return M4LiveSettings(minimum_price=floor, execution_revision="m4-taker-budget-v1",
                          execution={"mode": "taker"})


def config_files(tmp_path):
    (tmp_path / "config/m4-live").mkdir(parents=True)
    (tmp_path / "config/m4.toml").write_text('minimum_price="0.20"\n')
    return tmp_path / "config/m4-live/execution.toml"


def test_shared_file_changes_both_modes_and_preserves_old_settings_identity(tmp_path):
    path = config_files(tmp_path)
    path.write_text('mode="taker"\n')
    old = M4LiveSettings.load(tmp_path)
    baseline = M4Config.load(tmp_path)
    (tmp_path / "config/m4.toml").write_text('minimum_price="0.50"\n')
    current = M4LiveSettings.load(tmp_path)
    assert current.minimum_price == D(".50")
    assert baseline.minimum_price == D(".20")
    assert M4Config.load(tmp_path).minimum_price == D(".50")
    assert baseline.fingerprint != M4Config.load(tmp_path).fingerprint
    assert M4Config.model_validate(baseline.model_dump()).fingerprint == baseline.fingerprint
    assert current.execution == old.execution and current.fingerprint != old.fingerprint
    assert M4LiveSettings.model_validate(old.model_dump()).fingerprint == old.fingerprint
    changed = {k for k, v in current.model_dump().items() if old.model_dump()[k] != v}
    assert changed == {"minimum_price"}
    # There is deliberately no independent live price override.
    path.write_text('mode="taker"\nminimum_price="0.20"\n')
    with pytest.raises(ValueError):
        M4LiveSettings.load(tmp_path)


@pytest.mark.parametrize("floor", ["0.19", "0.49", "0.51", "1", "NaN", "Infinity"])
def test_unapproved_shared_floor_fails_closed(tmp_path, floor):
    path = config_files(tmp_path)
    path.write_text('mode="taker"\n')
    (tmp_path / "config/m4.toml").write_text(f'minimum_price="{floor}"\n')
    with pytest.raises(ValueError):
        M4Config.load(tmp_path)
    with pytest.raises(ValueError):
        M4LiveSettings.load(tmp_path)


@pytest.mark.parametrize("price,admitted", [(".49", False), (".50", True), (".70", True)])
async def test_threshold_filters_anchor_before_plus_tick_and_mock_post(tmp_path, monkeypatch, price, admitted):
    monkeypatch.setattr(legacy, "settings", lambda mode: live_settings())
    m, e, f, c, calls, transport = await legacy.market(tmp_path, monkeypatch, price=price)
    try:
        assert m.settings.minimum_price == m.ledger.minimum_price == D(".50")
        assert bool(m.pending) is admitted
        if admitted:
            opportunity = next(iter(m.pending.values()))
            await m.submit(opportunity)
            plan = m.ledger.plan(opportunity.episode_id)
            assert len(calls) == 1 and calls[0]["orderType"] == "FAK"
            assert D(plan["limit_price"]) == D(price) + D(".01")
            assert D(plan["planned_cost_usdc"]) <= 5 and m.ledger.committed() <= 10
        else:
            await m.advance()
            assert not calls and not m.ledger.rows() and m.ledger.committed() == 0
    finally:
        await close(m, e)
        transport.close()


@pytest.mark.parametrize("price,admitted", [(".49", False), (".50", True)])
async def test_baseline_uses_the_same_shared_threshold(tmp_path, monkeypatch, price, admitted):
    cfg = M4Config(minimum_price=".50")
    original_ledger = m4_helpers.ledger

    def configured_ledger(path, mode="VIRTUAL"):
        book = original_ledger(path, mode)
        book.set("settings", cfg.model_dump(mode="json"))
        return book

    monkeypatch.setattr(m4_helpers, "M4Config", lambda: cfg)
    monkeypatch.setattr(m4_helpers, "ledger", configured_ledger)
    m, e, f, c = await m4_helpers.setup(tmp_path, price=price, touch_model=True)
    try:
        assert m.settings.minimum_price == m.ledger.minimum_price == D(".50")
        assert bool(m.pending) is admitted
        if admitted:
            await m.submit(next(iter(m.pending.values())))
            posts = m.ledger.facts("virtual_post")
            assert len(posts) == 1 and D(str(posts[0]["quote"]["price"])) == D(".50")
        else:
            assert not m.ledger.rows() and not m.ledger.facts("virtual_post")
    finally:
        await close(m, e)


async def test_price_improvement_below_signal_floor_can_fill_and_top_up(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, "settings", lambda mode: live_settings())
    m, e, f, c, calls, transport = await legacy.market(tmp_path, monkeypatch, price=".50")
    e.gateway._address = e.gateway._client.get_address()
    query = None
    try:
        opportunity = next(iter(m.pending.values()))
        await m.submit(opportunity)
        oid = m.ledger.row(opportunity.episode_id)["order_id"]
        order, trade = evidence(m, e, oid, "3", price=".40")
        query, _ = serve(monkeypatch, transport, order, trade)
        await m.submit(opportunity)
        row = m.ledger.row(opportunity.episode_id)
        assert row["state"] == "RESERVED" and D(row["filled"]) == 3
        assert D(row["spent"]) == all_in_cost(".40", 3, legacy.FEES)
        await m.submit(opportunity)
        plan = m.ledger.plan(opportunity.episode_id)
        assert len(calls) == 2 and D(plan["limit_price"]) == D(".51")
        assert D(row["spent"]) + D(plan["planned_cost_usdc"]) <= 5
    finally:
        await close(m, e)
        transport.close()
        if query:
            query.close()


async def test_new_floor_unknown_survives_restart_without_another_post(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, "settings", lambda mode: live_settings())
    m, e, f, c, calls, transport = await legacy.market(
        tmp_path, monkeypatch, price=".50", response="TIMEOUT")
    try:
        opportunity = next(iter(m.pending.values()))
        await m.submit(opportunity)
        row = m.ledger.row(opportunity.episode_id)
        assert row["state"] == "UNKNOWN" and len(calls) == 1
        commitment, oid = m.ledger.committed(), row["order_id"]
    finally:
        await close(m, e)
        transport.close()
    m, e, f, c, calls, transport = await legacy.market(
        tmp_path, monkeypatch, price=".50", recover=True)
    try:
        assert m.ledger.minimum_price == D(".50")
        assert m.ledger.row(opportunity.episode_id)["order_id"] == oid
        assert m.ledger.row(opportunity.episode_id)["state"] == "UNKNOWN"
        assert m.ledger.committed() == commitment and not calls
        m.ledger.validate_recovery()
    finally:
        await close(m, e)
        transport.close()


@pytest.mark.parametrize("saved_floor", [".20", ".50", ".60"])
async def test_same_market_restores_saved_floor_next_market_uses_shared_setting(tmp_path, monkeypatch, saved_floor):
    h = harness(tmp_path, monkeypatch)
    saved, configured = live_settings(saved_floor), live_settings()
    identity = {"strategy": STRATEGY, "mode": "LIVE", "funder": FUNDER,
                "market_id": h.meta.condition_id, "settings_hash": saved.fingerprint}
    path = h.startup.root / "ledgers" / f"{h.meta.slug}.sqlite3"
    ledger = M4LiveLedger(path, identity, settings=saved, fees=legacy.FEES)
    ledger.initialize_session("offline_floor_recovery", saved.model_dump(mode="json"), asdict(h.meta))
    ledger.close()
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.settings.minimum_price == D(saved_floor)
        assert ready.identity["settings_hash"] == saved.fingerprint
    finally:
        ready.reads.close()
    h.clock_values[:] = [EPOCH + 301, EPOCH + 302]
    ready = await h.startup.prepare(configured, h.reads_type, None)
    try:
        assert ready.slug.endswith(str(EPOCH + 300))
        assert ready.settings.minimum_price == D(".50")
        assert ready.identity["settings_hash"] == configured.fingerprint
    finally:
        ready.reads.close()
