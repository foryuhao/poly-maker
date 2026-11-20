"""M18 original raw input through actual paper orders, partial fills and reports."""
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json

import pytest

from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m18 import Parameters
from sop_signals.parameters import load_revision
from m1234_fixtures import fixture_events


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["settled", "unsettled", "parent_guard_break", "tick_change"])
async def test_original_m18_native_order_population_and_complete_cancel_race_ledger(tmp_path, mode):
    await native_order_trace(tmp_path, mode, short=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["settled", "unsettled", "parent_guard_break", "tick_change"])
async def test_explicit_quarter_m18_native_orders_partial_fill_and_cancel_race(tmp_path, mode):
    await native_order_trace(tmp_path, mode, short=True)


async def native_order_trace(tmp_path, mode, *, short):
    from test_sop_m18_parameters import short_document
    document = short_document() if short else {"strategy":"M18", "revision_round":0,
                                             "parameters":{}, "pnl_used_for_selection":False}
    parameters, revision = load_revision(document)
    initial = deepcopy(fixture_events()[0])
    initial["definition"]["settings"].update(
        profiles={}, sop=definition(parameters), sop_parameter_revision=revision,
        sop_execution=execution_configuration(),
        scenarios={"trade-through":{"post_ms":100,"ack_ms":200,"cancel_ms":200,"ahead_cancel_fraction":"0"}})
    assert parameters.round_seconds == (.25 if short else .5)
    if not short:
        assert parameters == Parameters()
    else:
        assert initial["definition"]["settings"]["sop"]["terminal_quantity_input_contract"]["window_ms"] == 250
        assert "explicit-bid250-stable100" in initial["definition"]["settings"]["sop"]["version"]
    assert not initial["definition"]["settings"]["sop"]["terminal_quantity_input_contract"]["m17_per_print_recheck_required"]
    e = SopEngine(tmp_path, parameters=parameters)
    await e.ingest(initial)
    start, seq = initial["available_wall"], 1

    def time_for(t):
        if not short or t < .99 or t == 300:
            return t
        # Scale only the formation path. Transport/ACK/cancel intervals after
        # publication keep their original absolute lengths.
        return round(1 + (t-1)/2, 6) if t <= 2.102 else round(t-.5505, 6)

    async def event(t, kind, raw=None, **payload):
        nonlocal seq
        t = time_for(t)
        seq += 1
        if raw is not None:
            if kind == "FRAME":
                for row in raw if isinstance(raw, list) else [raw]:
                    row.update(market=e.meta.condition_id, timestamp=str(round((start+t)*1000)))
            payload.update(raw=raw, received_mono=t, received_wall=start+t)
        await e.ingest({"schema":initial["schema"],"seq":seq,"kind":kind,
                        "available_mono":t,"available_wall":start+t,"generation":1,**payload})

    def depth(q):
        return {"event_type":"price_change","price_changes":[
            {"asset_id":"1","side":"BUY","price":".50","size":q}]}

    def trade(price, q, identity):
        return {"event_type":"last_trade_price","asset_id":"1","side":"SELL",
                "price":price,"size":q,"trade_id":identity,
                "transaction_hash":"0x"+hashlib.sha256(identity.encode()).hexdigest()}

    async def refresh(t):
        await event(t,"FRAME",[{"event_type":"best_bid_ask","asset_id":token,
                                "best_bid":".50","best_ask":".52"} for token in ("1","2")])

    await event(0,"CONNECT")
    await event(.99,"FRAME",[{"event_type":"book","asset_id":token,
        "bids":[{"price":".50","size":"100"},{"price":".49","size":"100"}],
        "asks":[{"price":".52","size":"100"}]} for token in ("1","2")])
    await event(1.,"FRAME",trade(".50","10","reference"))
    await event(1.02,"FRAME",depth("80"))
    await refresh(1.15)
    await event(1.30,"FRAME",depth("95"))
    await refresh(1.45)
    await event(1.501,"POLL")
    core = e.sop_runtime.core
    assert core.campaigns["1"].reference["hits"] == 10
    assert not e.signal_history and not e.assumed_orders
    await event(1.6,"FRAME",trade(".50","25","second"))
    await event(1.62,"FRAME",depth("77"))
    await refresh(1.75)
    await event(1.90,"FRAME",depth("95"))
    await refresh(2.05)
    parent = [{"event_type":"best_bid_ask","asset_id":token,
               "best_bid":".50","best_ask":".52"} for token in ("1","2")]
    if mode == "parent_guard_break":
        parent.extend([depth("83"), depth("95")])
    elif mode == "tick_change":
        parent.append({"event_type":"tick_size_change","asset_id":"1",
                       "old_tick_size":".01","new_tick_size":".02"})
    await event(2.101,"FRAME",parent)
    await event(2.102,"POLL")
    broker = e.brokers["sop/trade-through"]
    if mode in {"parent_guard_break", "tick_change"}:
        assert not core.signals and not e.signal_history and not broker.orders and not e.assumed_orders
        await event(300,"END",reason="T300_OBSERVED")
        await e.close()
        e.retire_runtime()
        assert e.sop_runtime is None
        return
    assert len(e.signal_history) == len(broker.orders) == len(e.assumed_orders) == 1
    signal = next(iter(e.signal_history.values()))
    assert signal["confirmed"] == time_for(2.101), signal["confirmed"]
    # The lifecycle calculates deadlines with Decimal before serializing the
    # clock value. Adding these fractional times as floats can produce
    # 4.5504999999999995 instead of the contract's exact 4.5505.
    assert D(str(signal["deadline"])) == D(str(time_for(2.101))) + 3, signal["deadline"]
    order = next(iter(broker.orders.values()))
    assert order.intent.strategy == order.intent.profile == "M18" and set(broker.accounts) == {"M18"}
    assert D(order.intent.price) == D(".50") and D(order.intent.quantity) == 10
    await refresh(2.20)
    await event(2.31,"POLL")
    await event(2.35,"FRAME",trade(".49","3","partial-valid"))
    assert order.filled == 3
    await event(2.35,"FRAME",trade(".49","3","partial-valid"))
    assert order.filled == 3
    await event(2.40,"FRAME",depth("83"))
    assert not core.signals and order.cancel_requested == pytest.approx(time_for(2.40))
    await event(2.50,"FRAME",trade(".49","4","cancel-race"))
    await event(2.61,"POLL")
    assert order.filled == 7 and order.state == "CANCELLED"
    if mode == "settled":
        await event(2.62,"SETTLEMENT",{"slug":e.meta.slug,"conditionId":e.meta.condition_id,
            "clobTokenIds":["1","2"],"outcomePrices":["1","0"],"closed":True,"resolutionStatus":"resolved"})
        assert broker.account("M18").payout == 7
    e.store.commit()
    attrs = [json.loads(p) for (p,) in e.store.db.execute(
        "SELECT payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION' ORDER BY seq")]
    assert [x["classification"] for x in attrs] == ["WITHIN_SIGNAL_VALIDITY", "AFTER_SIGNAL_END"]
    await event(300,"END",reason="T300_OBSERVED")
    await e.close()
    from m1223_report import install_report_compatibility, market_result
    from polymaker.m1234_sim.report import read_state
    install_report_compatibility()
    market, orders = market_result(e.store.path, tmp_path)
    # M18 publishes directly usable signals; the additional admission alias is
    # an M17 pending/READY field, not part of the other SOP report schemas.
    assert market["signal_count"] == market["positive_modelled_fill_orders"] == 1
    assert market["signal_ids"] == [signal["id"]]
    assert D(orders[0]["within_signal_validity_quantity"]) == 3
    assert D(orders[0]["after_signal_end_quantity"]) == D(orders[0]["cancel_in_flight_quantity"]) == 4
    if mode == "unsettled":
        assert market["pending_settlement_orders"] == 1
        assert orders[0]["assumed_gross_pnl"] is orders[0]["known_simulated_gross_pnl"] is None
    else:
        assert D(orders[0]["assumed_gross_pnl"]) == 5
        assert D(orders[0]["known_simulated_gross_pnl"]) == D("3.50")
    durable = read_state(e.store.path)
    e.retire_runtime()
    assert not core.rounds and not core.campaigns and not core.signals
    assert e.sop_runtime is None and e.signals is None and not e.extra_policies
    assert read_state(e.store.path) == durable
