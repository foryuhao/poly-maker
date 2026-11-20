"""M15 raw parent ordering through native SUBMIT, partial/race fills and PnL."""

import hashlib
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m15 import Parameters
from sop_signals.parameters import load_revision
from m1234_fixtures import fixture_events


def selected_parameters():
    """Exercise the actual M15 frozen config when validating a candidate bundle."""
    path = Path.cwd() / 'config/m1223-parameters.json'
    document = json.loads(path.read_text()) if path.exists() else {}
    return load_revision(document)[0] if document.get('strategy') == 'M15' else Parameters()


def test_m15_native_fixture_reads_selected_config_instead_of_silent_baseline(tmp_path, monkeypatch):
    # A synthetic loader contract, not a chosen/replayed frequency candidate.
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'm1223-parameters.json').write_text(json.dumps({
        'strategy': 'M15', 'revision_round': 1, 'pnl_used_for_selection': False,
        'hypothesis': 'SYNTHETIC_CONFIG_CONTRACT_ONLY_NOT_TUNING_SELECTION',
        'parameters': {'ask_recovery': '0.40'},
    }))
    monkeypatch.chdir(tmp_path)
    parameters = selected_parameters()
    assert parameters.ask_recovery == D('0.40') and parameters != Parameters()
    assert definition(parameters)['parameters']['ask_recovery'] == '0.40'


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["settled", "unsettled", "parent_guard_break", "tick_change", "same_timestamp_pre_hit"])
async def test_m15_raw_order_partial_duplicate_cancel_race_official_ledgers_and_release(tmp_path, mode):
    parameters = selected_parameters()
    # The same causal fixture, with continuous fresh BBO coverage while the
    # explicitly selected longer formation proof is still in progress.
    hold_delay = parameters.lw_hold_seconds - .100
    assert hold_delay == pytest.approx(0) or hold_delay == pytest.approx(.150)
    initial = deepcopy(fixture_events()[0])
    initial["definition"]["settings"].update(
        profiles={}, sop=definition(parameters), sop_execution=execution_configuration(),
        scenarios={"trade-through": {"post_ms": 100, "ack_ms": 200,
                                      "cancel_ms": 200, "ahead_cancel_fraction": "0"}},
    )
    engine = SopEngine(tmp_path, parameters=parameters)
    await engine.ingest(initial)
    start_wall, seq = initial["available_wall"], 1

    async def event(t, kind, raw=None, **payload):
        nonlocal seq
        seq += 1
        if raw is not None:
            if kind == "FRAME":
                for row in raw if isinstance(raw, list) else [raw]:
                    row.update(market=engine.meta.condition_id,
                               timestamp=str(round((start_wall + t) * 1000)))
                payload.update(received_mono=t, received_wall=start_wall + t)
            payload["raw"] = raw
        await engine.ingest({"schema": initial["schema"], "seq": seq, "kind": kind,
                             "available_mono": t, "available_wall": start_wall + t,
                             "generation": 1, **payload})

    def bbo(token="1"):
        return {"event_type": "best_bid_ask", "asset_id": token,
                "best_bid": ".50", "best_ask": ".52"}

    def delta(side, price, size):
        return {"event_type": "price_change", "price_changes": [
            {"asset_id": "1", "side": side, "price": price, "size": size}]}

    def trade(side, price, size, identity):
        return {"event_type": "last_trade_price", "asset_id": "1", "side": side,
                "price": price, "size": size, "trade_id": identity,
                "transaction_hash": "0x" + hashlib.sha256(identity.encode()).hexdigest()}

    async def refresh(t):
        await event(t, "FRAME", [bbo("1"), bbo("2")])

    await event(0, "CONNECT")
    await event(0, "FRAME", [
        {"event_type": "book", "asset_id": token,
         "bids": [{"price": ".50", "size": "100"}, {"price": ".49", "size": "100"}],
         "asks": [{"price": p, "size": "100"} for p in (".52", ".53", ".54")]}
        for token in ("1", "2")])
    await event(.01, "FRAME", trade("SELL", ".50", "10", "old-support"))
    await event(.02, "FRAME", delta("BUY", ".50", "80"))
    await refresh(.1)
    await event(.2, "FRAME", delta("BUY", ".50", "95"))
    for t in (.3, .4, .5, .511, .6):
        await refresh(t)
    await event(.61, "FRAME", [delta("SELL", ".53", "40"), delta("SELL", ".54", "40")])
    if hold_delay > 0:
        for t in (.7, .8):
            await refresh(t)
        assert engine.sop_runtime.core.counts["LW_CONFIRMED"] == 0
    await refresh(.711 + hold_delay)
    core = engine.sop_runtime.core
    root = core.withdrawals["1"]
    assert root.seed is not None and root.seed["support"]["guard"] == 88
    first_buy = trade("BUY", ".52", "10", "first-new-buy")
    if mode == "same_timestamp_pre_hit":
        await event(.72 + hold_delay, "FRAME", [delta("SELL", ".52", "99"), first_buy])
        assert engine.sop_runtime.reasons["1"] == "PRE_HIT_LEVEL_ORDER_UNPROVEN"
    else:
        await event(.72 + hold_delay, "FRAME", first_buy)
        await event(.73 + hold_delay, "FRAME", delta("SELL", ".52", "90"))
    for t in (.8, .9):
        await refresh(t + hold_delay)
    await event(.92 + hold_delay, "FRAME", delta("SELL", ".52", "92"))
    for t in (1.0, 1.1, 1.2):
        await refresh(t + hold_delay)
    final_parent = [bbo("1"), bbo("2")]
    if mode == "parent_guard_break":
        final_parent += [delta("SELL", ".52", "96"), delta("SELL", ".52", "92")]
    elif mode == "tick_change":
        final_parent.append({"event_type": "tick_size_change", "asset_id": "1",
                             "old_tick_size": ".01", "new_tick_size": ".02"})
    await event(1.231 + hold_delay, "FRAME", final_parent)
    await event(1.232 + hold_delay, "POLL")
    broker = engine.brokers["sop/trade-through"]
    if mode in {"parent_guard_break", "tick_change", "same_timestamp_pre_hit"}:
        assert not engine.signal_history and not broker.orders and not engine.assumed_orders
        assert core.counts["SIGNAL_CONFIRMED"] == 0
        await event(300, "END", reason="T300_OBSERVED")
        await engine.close()
        engine.retire_runtime()
        assert engine.sop_runtime is None
        return
    assert len(engine.signal_history) == len(broker.orders) == len(engine.assumed_orders) == 1
    signal = next(iter(engine.signal_history.values()))
    assert signal["bid"] == signal["prior_support"] and signal["deadline"] == pytest.approx(3.711 + hold_delay)
    order = next(iter(broker.orders.values()))
    assert order.intent.strategy == order.intent.profile == "M15"
    assert set(broker.accounts) == {"M15"}
    assert D(order.intent.price) == D(".50") and D(order.intent.quantity) == 10
    await refresh(1.3 + hold_delay)
    await event(1.4 + hold_delay, "FRAME", trade("SELL", ".49", "3", "valid-fill"))
    assert order.filled == 3
    await event(1.4 + hold_delay, "FRAME", trade("SELL", ".49", "3", "valid-fill"))
    assert order.filled == 3 and engine.sop_runtime.counts["DUPLICATE_DIRECT_TRADE"] == 1
    await event(1.5 + hold_delay, "FRAME", delta("SELL", ".52", "96"))
    assert order.cancel_requested == pytest.approx(1.5 + hold_delay) and not core.signals
    await event(1.6 + hold_delay, "FRAME", trade("SELL", ".49", "4", "cancel-race"))
    assert order.filled == 7
    await refresh(1.65 + hold_delay)
    await event(1.71 + hold_delay, "POLL")
    assert order.state == "CANCELLED"
    if mode == "settled":
        await event(1.72 + hold_delay, "SETTLEMENT", {"slug": engine.meta.slug,
                    "conditionId": engine.meta.condition_id, "clobTokenIds": ["1", "2"],
                    "outcomePrices": ["1", "0"], "closed": True, "resolutionStatus": "resolved"})
        assert broker.account("M15").payout == 7
        assert D(next(iter(engine.assumed_orders.values()))["gross_pnl"]) == 5
    else:
        await event(1.72 + hold_delay, "GAP", reason="SYNTHETIC_LATE_KNOWN_DATA_GAP")
        assert order.uncertain and order.filled == 7 and engine.resolution is None
        assert not next(iter(engine.assumed_orders.values()))["settled"]
    assert broker.account("M15").spent == D("3.50")
    engine.store.commit()
    attribution = [json.loads(row[0])["classification"] for row in engine.store.db.execute(
        "SELECT payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION' ORDER BY seq")]
    assert attribution == ["WITHIN_SIGNAL_VALIDITY", "AFTER_SIGNAL_END"]
    await event(300, "END", reason="T300_OBSERVED")
    await engine.close()
    from m1223_report import install_report_compatibility, market_result
    from polymaker.m1234_sim.report import read_state, summary

    install_report_compatibility()
    assert summary(engine.store.path)["accounts"][0]["strategy"] == "M15"
    market, orders = market_result(engine.store.path, tmp_path)
    assert market["signal_count"] == market["positive_modelled_fill_orders"] == 1
    if mode == "settled":
        assert D(orders[0]["known_simulated_gross_pnl"]) == D("3.50")
        assert D(orders[0]["assumed_gross_pnl"]) == 5
    else:
        assert market["pending_settlement_orders"] == market["unresolved_orders"] == 1
        assert orders[0]["known_simulated_gross_pnl"] is None and orders[0]["assumed_gross_pnl"] is None
        assert D(orders[0]["known_filled_quantity"]) == 7 and orders[0]["unresolved"]
    assert D(orders[0]["within_signal_validity_quantity"]) == 3
    assert D(orders[0]["cancel_in_flight_quantity"]) == 4
    durable = read_state(engine.store.path)
    engine.retire_runtime()
    assert engine.sop_runtime is None and engine.signals is None and not engine.extra_policies
    assert not any((core.withdrawals, core.buy_history, core.buy_intervals, core.closed_buy_intervals))
    assert read_state(engine.store.path) == durable
