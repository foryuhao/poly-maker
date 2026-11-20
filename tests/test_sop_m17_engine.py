"""Selected M17 parameters through native raw input, paper orders and ledgers."""

from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.parameters import load_revision
from m1234_fixtures import fixture_events


def selected_parameters():
    path = Path.cwd() / "config/m1223-parameters.json"
    document = json.loads(path.read_text()) if path.exists() else {}
    if document.get("strategy") != "M17":
        document = {"strategy":"M17", "revision_round":0, "parameters":{}, "pnl_used_for_selection":False}
    return load_revision(document)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["settled", "unsettled", "pending_at_confirmation", "parent_guard_break", "tick_change"])
async def test_m17_native_raw_lifecycle_uses_actual_config_and_keeps_recheck_exposure(tmp_path, mode):
    parameters, revision = selected_parameters()
    initial = deepcopy(fixture_events()[0])
    initial["definition"]["settings"].update(
        profiles={}, sop=definition(parameters), sop_parameter_revision=revision,
        sop_execution=execution_configuration(),
        scenarios={"trade-through":{"post_ms":100,"ack_ms":200,"cancel_ms":200,"ahead_cancel_fraction":"0"}})
    e = SopEngine(tmp_path, parameters=parameters)
    await e.ingest(initial)
    start, seq = initial["available_wall"], 1

    async def event(t, kind, raw=None, **payload):
        nonlocal seq
        seq += 1
        if raw is not None:
            if kind == "FRAME":
                for row in raw if isinstance(raw,list) else [raw]:
                    row.update(market=e.meta.condition_id,timestamp=str(round((start+t)*1000)))
            payload.update(raw=raw,received_mono=t,received_wall=start+t)
        await e.ingest({"schema":initial["schema"],"seq":seq,"kind":kind,
                        "available_mono":t,"available_wall":start+t,"generation":1,**payload})

    def bbo(token):
        return {"event_type":"best_bid_ask","asset_id":token,"best_bid":".50","best_ask":".52"}

    def depth(quantity):
        return {"event_type":"price_change","price_changes":[
            {"asset_id":"1","side":"BUY","price":".50","size":quantity}]}

    def trade(price,quantity,identity):
        return {"event_type":"last_trade_price","asset_id":"1","side":"SELL",
                "price":price,"size":quantity,"trade_id":identity,
                "transaction_hash":"0x"+hashlib.sha256(identity.encode()).hexdigest()}

    async def refresh(t):
        await event(t,"FRAME",[bbo("1"),bbo("2")])

    await event(0,"CONNECT")
    await event(.99,"FRAME",[{"event_type":"book","asset_id":token,
        "bids":[{"price":".50","size":"100"},{"price":".49","size":"100"}],
        "asks":[{"price":".52","size":"100"}]} for token in ("1","2")])
    for t, following in ((1.,1.14),(1.26,1.39),(1.51,1.65)):
        await event(t,"FRAME",trade(".50","20","forming-"+str(t)))
        await event(round(t+.01,3),"FRAME",depth("95"))
        await refresh(following)
    core = e.sop_runtime.core
    assert core.roots["1"].hits == 60 and core.roots["1"].rechecked_records == 3
    parent = [bbo("1"),bbo("2")]
    if mode == "parent_guard_break":
        # Break the actual selected absolute floor. With the original70%
        # parameters this is still69; numerical candidates must exercise the
        # same invalidation, rather than silently testing the old threshold.
        broken_floor = str(D("100") * parameters.minimum_visible_to_initial_depth - 1)
        parent += [depth(broken_floor),depth("95")]
    elif mode == "tick_change":
        parent.append({"event_type":"tick_size_change","asset_id":"1","old_tick_size":".01","new_tick_size":".02"})
    elif mode == "pending_at_confirmation":
        parent.append(trade(".50","1","handoff-pending"))
    await event(1.75,"FRAME",parent)
    await event(1.751,"POLL")
    broker = e.brokers["sop/trade-through"]
    if mode in {"parent_guard_break","tick_change"}:
        assert not core.signals and not e.signal_history and not broker.orders and not e.assumed_orders
        await event(300,"END",reason="T300_OBSERVED")
        await e.close()
        e.retire_runtime()
        assert e.sop_runtime is None
        return
    if mode == "pending_at_confirmation":
        assert len(e.signal_history)==1 and not broker.orders and not e.assumed_orders
        assert not next(iter(core.signals.values()))["can_reference_for_new_action"]
        await event(1.78,"FRAME",depth("95"))
        await event(1.781,"POLL")
    assert len(e.signal_history)==len(broker.orders)==len(e.assumed_orders)==1
    signal = next(iter(e.signal_history.values()))
    assert signal["confirmed"]==1.75 and signal["deadline"]==4.75
    assert signal["ever_reference_ready"] and signal["first_reference_ready"]==(1.78 if mode=="pending_at_confirmation" else 1.75)
    order=next(iter(broker.orders.values()))
    assert order.intent.strategy==order.intent.profile=="M17" and set(broker.accounts)=={"M17"}
    assert D(order.intent.price)==D(".50") and D(order.intent.quantity)==10
    await refresh(1.9)
    await event(1.95,"POLL")
    await event(1.96,"FRAME",trade(".50","1","existing-order-pending"))
    assert order.cancel_requested is None and next(iter(core.signals.values()))["pending_rechecks"]==1
    await event(2.,"FRAME",trade(".49","3","partial-valid"))
    assert order.filled==3
    await event(2.,"FRAME",trade(".49","3","partial-valid"))
    assert order.filled==3
    await event(2.06,"POLL")
    assert not core.signals and order.cancel_requested==pytest.approx(2.06)
    await event(2.15,"FRAME",trade(".49","4","after-timeout-cancel-race"))
    await event(2.27,"POLL")
    assert order.filled==7 and order.state=="CANCELLED"
    if mode != "unsettled":
        await event(2.28,"SETTLEMENT",{"slug":e.meta.slug,"conditionId":e.meta.condition_id,
            "clobTokenIds":["1","2"],"outcomePrices":["1","0"],"closed":True,"resolutionStatus":"resolved"})
        assert broker.account("M17").payout==7
    e.store.commit()
    attrs=[json.loads(p) for (p,) in e.store.db.execute(
        "SELECT payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION' ORDER BY seq")]
    assert [x["classification"] for x in attrs]==["WITHIN_SIGNAL_VALIDITY","AFTER_SIGNAL_END"]
    assert attrs[0]["pending_rechecks_at_evidence_availability"]==1
    assert attrs[1]["pending_rechecks_at_evidence_availability"] is None
    assert all(x["pending_rechecks_at_source_time"] is None for x in attrs)
    await event(300,"END",reason="T300_OBSERVED")
    await e.close()
    from m1223_report import install_report_compatibility,market_result
    from polymaker.m1234_sim.report import read_state
    install_report_compatibility()
    market,orders=market_result(e.store.path,tmp_path)
    assert market["signal_count"]==market["admission_signal_count"]==market["positive_modelled_fill_orders"]==1
    assert market["signal_ids"]==market["admission_signal_ids"]
    assert D(orders[0]["within_signal_validity_quantity"])==3 and D(orders[0]["after_signal_end_quantity"])==4
    assert D(orders[0]["cancel_in_flight_quantity"])==4
    if mode=="unsettled":
        assert market["unresolved_orders"]==market["pending_settlement_orders"]==1
        assert orders[0]["assumed_gross_pnl"] is None and orders[0]["known_simulated_gross_pnl"] is None
    else:
        assert D(orders[0]["known_simulated_gross_pnl"])==D("3.50") and D(orders[0]["assumed_gross_pnl"])==5
    durable=read_state(e.store.path)
    e.retire_runtime()
    assert e.sop_runtime is None and e.signals is None and not e.extra_policies
    assert not any((core.roots,core.signals,core.last_end)) and read_state(e.store.path)==durable
