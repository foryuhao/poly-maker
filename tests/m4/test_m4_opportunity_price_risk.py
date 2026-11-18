"""Strategy price risk handoff through the live path with simulated HTTP."""

import json
from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest
import test_m4_live_budget as budget
from m4_helpers import close, op
from test_m4_live_band_abandonment import move
from test_m4_live_first_band_rejection import settings as legacy_settings
from test_m4_live_startup import EPOCH, FUNDER, harness
from test_m4_live_task import evidence, serve

from polymaker.m4_config import STRATEGY, M4Config
from polymaker.m4_live_config import M4LiveSettings
from polymaker.m4_live_price_band import POST_PRICE_OUTSIDE_BAND, SIGNED_LIMIT_BAND_ABANDONED
from polymaker.m4_live_taker import M4LiveLedger
from polymaker.m4_types import OPPORTUNITY_PRICE_RISK


PROJECT = Path(__file__).resolve().parents[2]


def strategy_settings():
    return M4LiveSettings.load(PROJECT)


def test_config_sources_and_historical_serialization(tmp_path):
    (tmp_path / "config/m4-live").mkdir(parents=True)
    strategy = tmp_path / "config/m4.toml"
    live = tmp_path / "config/m4-live/execution.toml"
    strategy.write_text((PROJECT / "config/m4.toml").read_text())
    live.write_text((PROJECT / "config/m4-live/execution.toml").read_text())
    configured = M4LiveSettings.load(tmp_path)
    saved = configured.model_dump(mode="json")
    assert saved["live_taker_price_risk"]["minimum"] == "0.50"
    assert saved["live_taker_price_risk"]["maximum"] == "0.95"
    assert "post_minimum_price" not in saved["execution"]["taker"]
    assert M4LiveSettings.model_validate(saved).fingerprint == configured.fingerprint
    baseline = M4Config.load(tmp_path)
    live.write_text(live.read_text() + '\nprice_band_policy = "POST_SIGNED_LIMIT_BAND_V1"\n')
    with pytest.raises(ValueError, match="M4_PRICE_RISK_CONFLICTING_SOURCES"):
        M4LiveSettings.load(tmp_path)
    strategy.write_text(strategy.read_text().split("[live_taker_price_risk]")[0])
    assert M4Config.load(tmp_path).fingerprint == baseline.fingerprint
    with pytest.raises(ValueError, match="M4_OPPORTUNITY_PRICE_RISK_REQUIRED"):
        M4LiveSettings.load(tmp_path)
    old = legacy_settings()
    historical = old.model_dump(mode="json")
    assert "live_taker_price_risk" not in historical
    assert M4LiveSettings.model_validate(historical).model_dump(mode="json") == historical


@pytest.mark.parametrize(("anchor", "expected_stage"), [(".94", "INITIAL"), (".49", "BEST_ASK")])
async def test_signal_snapshot_enforced_before_every_post(tmp_path, monkeypatch, anchor, expected_stage):
    cfg = strategy_settings()
    monkeypatch.setattr(budget, "settings", lambda *args: cfg)
    market, executor, feed, clock, calls, transport = await budget.fixture(
        tmp_path, monkeypatch, price=anchor, response="KILLED")
    try:
        identified = [e["opportunity"] for e in feed.m1.events if e["event"] == "OPPORTUNITY_IDENTIFIED"]
        row, = market.ledger.rows()
        ep = market.restored_opportunity(row)
        assert identified == [asdict(ep)]
        assert ep.price_risk == cfg.live_taker_price_risk
        with pytest.raises(FrozenInstanceError):
            ep.price_risk.maximum = Decimal("1")
        with pytest.raises(ValueError, match="M4_OPPORTUNITY_PRICE_RISK_CONFLICT"):
            market.ledger.observe(op(2, price=anchor), elapsed=20, minimum=5)
        root = market.ledger.root_plan(ep.episode_id)
        assert root["post_price_band_source"] == OPPORTUNITY_PRICE_RISK
        assert root["post_price_band"] == cfg.post_price_band
        if expected_stage == "BEST_ASK":
            await market.submit(ep)
            attempt, = market.ledger.attempts(ep.episode_id)
            assert len(calls) == 1 and Decimal(json.loads(attempt["quote"])["price"]) == Decimal(".50")
            move(market, feed, clock, ep, age=11, ask=".95")
            await market.refresh_account()
            await market.submit(ep)
        else:
            assert row["reason"] == POST_PRICE_OUTSIDE_BAND and not calls and not market.ledger.attempts()
        proof = market.ledger.price_band_abandonment(ep.episode_id)
        assert proof["pricing"]["stage"] == expected_stage
        assert proof["pricing_origin"] == root["pricing_origin"]
        assert Decimal(proof["pricing"]["limit_price"]) == Decimal(".95")
        market.ledger.validate_recovery()
    finally:
        await close(market, executor)
        transport.close()

    recovered, executor, feed, clock, calls, transport = await budget.fixture(
        tmp_path, monkeypatch, price=anchor, response="KILLED", recover=True)
    try:
        ep = recovered.restored_opportunity(recovered.ledger.row(ep.episode_id))
        move(recovered, feed, clock, ep, age=12, ask=".70")
        await recovered.refresh_account()
        await recovered.submit(ep)
        assert not calls
        assert recovered.ledger.price_band_abandonment(ep.episode_id) == proof
    finally:
        await close(recovered, executor)
        transport.close()


@pytest.mark.parametrize("legacy", [True, False])
async def test_same_market_reads_saved_risk_next_market_uses_config(tmp_path, monkeypatch, legacy):
    h = harness(tmp_path, monkeypatch)
    current = strategy_settings()
    old = (legacy_settings() if legacy else current.model_copy(update={
        "live_taker_price_risk": replace(current.live_taker_price_risk, maximum=Decimal(".93")),
    }))
    identity = {"strategy": STRATEGY, "mode": "LIVE", "funder": FUNDER,
                "market_id": h.meta.condition_id, "settings_hash": old.fingerprint}
    path = h.startup.root / "ledgers" / f"{h.meta.slug}.sqlite3"
    ledger = M4LiveLedger(path, identity, settings=old, fees=budget.legacy.FEES)
    ledger.initialize_session("offline_price_risk_recovery", old.model_dump(mode="json"), asdict(h.meta))
    ledger.close()
    ready = await h.startup.prepare(current, h.reads_type, None)
    assert ready.settings == old and ready.identity["settings_hash"] == old.fingerprint
    ready.reads.close()
    h.clock_values[:] = [EPOCH+301, EPOCH+302]
    ready = await h.startup.prepare(current, h.reads_type, None)
    assert ready.settings == current and ready.identity["settings_hash"] == current.fingerprint
    ready.reads.close()


async def test_settling_budget_survives_strategy_price_risk_drop(tmp_path, monkeypatch):
    configured = strategy_settings()
    monkeypatch.setattr(budget, "settings", lambda *args: configured)
    market, executor, feed, clock, calls, transport = await budget.fixture(
        tmp_path, monkeypatch, price=".49")
    query = None
    try:
        ep, = market.pending.values()
        await market.submit(ep)
        attempt, = market.ledger.attempts()
        order_id = attempt["order_id"]
        order, trade = evidence(market, executor, order_id, "3", price=".44", status="MINED")
        query, _ = serve(monkeypatch, transport, order, trade)
        await market.reconcile(order_id)
        reserve = market.ledger.pending_cost(ep.episode_id)
        assert reserve > 0
        move(market, feed, clock, ep, age=10, ask=".95")
        await market.refresh_account()
        await market.submit(ep)
        assert market.ledger.price_band_abandonment(ep.episode_id)["reason"] == SIGNED_LIMIT_BAND_ABANDONED
        assert Decimal(market.ledger.row(ep.episode_id)["reserved"]) == reserve
        trade["status"] = "CONFIRMED"
        await market.reconcile(order_id)
        move(market, feed, clock, ep, age=12, ask=".80")
        await market.submit(ep)
        row = market.ledger.row(ep.episode_id)
        assert len(calls) == 1 and row["state"] == "CUTOFF" and Decimal(row["filled"]) == 3
        assert Decimal(row["reserved"]) == 0
        market.ledger.validate_recovery()
    finally:
        await close(market, executor)
        transport.close()
        if query:
            query.close()
