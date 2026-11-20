"""M16 actual raw parser -> paper SUBMIT, partial/race fills and settlement."""

from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m16 import Parameters
from sop_signals.parameters import load_revision
from m1234_fixtures import fixture_events
from test_sop_m16_feed import synthetic_clock_evidence


def selected_parameters():
    path = Path.cwd() / "config/m1223-parameters.json"
    document = json.loads(path.read_text()) if path.exists() else {}
    return load_revision(document)[0] if document.get("strategy") == "M16" else Parameters()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["settled", "unsettled", "parent_guard_break", "tick_change", "same_timestamp_pre_hit"])
async def test_m16_native_raw_lifecycle_uses_actual_config_and_keeps_two_ledgers(tmp_path, mode):
    parameters = selected_parameters()
    initial = deepcopy(fixture_events()[0])
    initial["definition"]["settings"].update(
        profiles={}, sop=definition(parameters), sop_execution=execution_configuration(),
        sop_receiver_clock=synthetic_clock_evidence(),
        scenarios={"trade-through": {"post_ms":100,"ack_ms":200,"cancel_ms":200,"ahead_cancel_fraction":"0"}})
    engine = SopEngine(tmp_path, parameters=parameters)
    await engine.ingest(initial)
    start_wall, seq = initial["available_wall"], 1

    async def event(t, kind, raw=None, **payload):
        nonlocal seq
        seq += 1
        if raw is not None:
            if kind == "FRAME":
                for row in raw if isinstance(raw,list) else [raw]:
                    row.update(market=engine.meta.condition_id, timestamp=str(round((start_wall+t)*1000)))
                payload.update(received_mono=t, received_wall=start_wall+t)
            payload["raw"] = raw
        await engine.ingest({"schema":initial["schema"],"seq":seq,"kind":kind,
                             "available_mono":t,"available_wall":start_wall+t,"generation":1,**payload})

    def bbo(token="1"):
        return {"event_type":"best_bid_ask","asset_id":token,"best_bid":".50","best_ask":".52"}

    def delta(q):
        return {"event_type":"price_change","price_changes":[
            {"asset_id":"1","side":"BUY","price":".50","size":q}]}

    def trade(price, quantity, identity):
        return {"event_type":"last_trade_price","asset_id":"1","side":"SELL", "price":price,
                "size":quantity,"trade_id":identity,
                "transaction_hash":"0x"+hashlib.sha256(identity.encode()).hexdigest()}

    async def refresh(t):
        await event(t,"FRAME",[bbo("1"),bbo("2")])

    await event(0,"CONNECT")
    await event(0,"FRAME",[
        {"event_type":"book","asset_id":token,
         "bids":[{"price":".50","size":"100"},{"price":".49","size":"100"}],
         "asks":[{"price":".52","size":"100"}]} for token in ("1","2")])
    await event(.01,"FRAME",trade(".50","10","slow-reference"))
    await event(.02,"FRAME",delta("80"))
    await refresh(.1)
    await refresh(.2)
    await event(.22,"FRAME",delta("85"))
    await event(.24,"FRAME",delta("95"))
    for t in (.3,.4,.5,.511):
        await refresh(t)
    core = engine.sop_runtime.core
    root = core.campaigns["1"]
    assert root.reference["guard"] == 88 and not core.signals
    if mode == "same_timestamp_pre_hit":
        await event(.6,"FRAME",[delta("95"),trade(".50","12","first-new-second")])
        assert engine.sop_runtime.reasons["1"] == "PRE_HIT_LEVEL_ORDER_UNPROVEN"
    else:
        await event(.6,"FRAME",trade(".50","12","first-new-second"))
        await event(.61,"FRAME",delta("75"))  # Legal below historical old88 floor.
    await event(.65,"FRAME",delta("80"))
    await event(.67,"FRAME",delta("95"))
    for t in (.8,.9,1.0,1.1):
        await refresh(t)
    parent = [bbo("1"),bbo("2")]
    if mode == "parent_guard_break":
        parent += [delta("82"),delta("95")]
    elif mode == "tick_change":
        parent.append({"event_type":"tick_size_change","asset_id":"1", "old_tick_size":".01","new_tick_size":".02"})
    await event(1.101,"FRAME",parent)
    await event(1.102,"POLL")
    broker = engine.brokers["sop/trade-through"]
    if mode in {"parent_guard_break","tick_change","same_timestamp_pre_hit"}:
        assert not engine.signal_history and not broker.orders and not engine.assumed_orders
        await event(300,"END",reason="T300_OBSERVED")
        await engine.close()
        engine.retire_runtime()
        assert engine.sop_runtime is None
        return
    assert len(engine.signal_history) == len(broker.orders) == len(engine.assumed_orders) == 1
    signal = next(iter(engine.signal_history.values()))
    assert signal["root_id"] == root.id and signal["bid"]["guard"] == 83
    assert signal["deadline"] == pytest.approx(4.101)
    order = next(iter(broker.orders.values()))
    assert order.intent.strategy == order.intent.profile == "M16" and set(broker.accounts) == {"M16"}
    assert D(order.intent.price) == D(".50") and D(order.intent.quantity) == 10
    await refresh(1.2)
    await refresh(1.3)
    await event(1.4,"FRAME",trade(".49","3","valid-fill"))
    assert order.filled == 3
    await event(1.4,"FRAME",trade(".49","3","valid-fill"))
    assert order.filled == 3 and engine.sop_runtime.counts["DUPLICATE_DIRECT_TRADE"] == 1
    await event(1.5,"FRAME",delta("82"))
    assert order.cancel_requested == pytest.approx(1.5) and not core.signals
    await event(1.6,"FRAME",trade(".49","4","cancel-race"))
    await refresh(1.65)
    await event(1.71,"POLL")
    assert order.filled == 7 and order.state == "CANCELLED"
    if mode == "settled":
        await event(1.72,"SETTLEMENT",{"slug":engine.meta.slug,"conditionId":engine.meta.condition_id,
                                     "clobTokenIds":["1","2"],"outcomePrices":["1","0"],
                                     "closed":True,"resolutionStatus":"resolved"})
        assert broker.account("M16").payout == 7
        assert D(next(iter(engine.assumed_orders.values()))["gross_pnl"]) == 5
    else:
        await event(1.72,"GAP",reason="SYNTHETIC_LATE_KNOWN_DATA_GAP")
        assert order.uncertain and engine.resolution is None
    engine.store.commit()
    attribution=[json.loads(p)["classification"] for (p,) in engine.store.db.execute(
        "SELECT payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION' ORDER BY seq")]
    assert attribution == ["WITHIN_SIGNAL_VALIDITY","AFTER_SIGNAL_END"]
    await event(300,"END",reason="T300_OBSERVED")
    await engine.close()
    from m1223_report import install_report_compatibility, market_result
    from polymaker.m1234_sim.report import read_state
    install_report_compatibility()
    market, orders = market_result(engine.store.path,tmp_path)
    assert market["signal_count"] == market["positive_modelled_fill_orders"] == 1
    if mode == "settled":
        assert D(orders[0]["known_simulated_gross_pnl"]) == D("3.50")
        assert D(orders[0]["assumed_gross_pnl"]) == 5
    else:
        assert market["unresolved_orders"] == market["pending_settlement_orders"] == 1
        assert orders[0]["known_simulated_gross_pnl"] is None and orders[0]["assumed_gross_pnl"] is None
    assert D(orders[0]["within_signal_validity_quantity"]) == 3
    assert D(orders[0]["cancel_in_flight_quantity"]) == 4
    durable=read_state(engine.store.path)
    engine.retire_runtime()
    assert engine.sop_runtime is None and engine.signals is None and not engine.extra_policies
    assert not any((core.rounds,core.campaigns,core.components,core.signals,core.closed_windows,core.blocked_rounds))
    assert read_state(engine.store.path) == durable
