import hashlib
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m12 import Parameters
from sop_signals.parameters import load_revision

from m1234_fixtures import fixture_events


@pytest.mark.asyncio
async def test_raw_path_to_two_ledgers_cancellation_race_and_official_settlement(tmp_path, monkeypatch):
    # Frozen candidate validation runs from that runtime's root. Exercise its
    # selected parameters through the raw path and both economic ledgers.
    parameter_file = Path.cwd() / "config/m1223-parameters.json"
    parameters = (
        load_revision(json.loads(parameter_file.read_text()))[0]
        if parameter_file.exists() and json.loads(parameter_file.read_text()).get("strategy") == "M12"
        else Parameters()
    )
    source = fixture_events()
    initial = deepcopy(source[0])
    settings = initial["definition"]["settings"]
    settings.update(
        profiles={},
        sop=definition(parameters),
        sop_execution=execution_configuration(),
        scenarios={
            "trade-through": {
                "post_ms": 100,
                "ack_ms": 200,
                "cancel_ms": 200,
                "ahead_cancel_fraction": "0",
            }
        },
    )
    e = SopEngine(tmp_path, parameters=parameters)
    await e.ingest(initial)
    start_wall = initial["available_wall"]
    seq = 1

    async def event(t, kind, **payload):
        nonlocal seq
        seq += 1
        raw = payload.get("raw")
        if raw is not None and kind == "FRAME":
            for r in raw if isinstance(raw, list) else [raw]:
                r.update(market=e.meta.condition_id, timestamp=str(round((start_wall + t) * 1000)))
            payload.update(received_mono=t, received_wall=start_wall + t)
        await e.ingest(
            {
                "schema": initial["schema"],
                "seq": seq,
                "kind": kind,
                "available_mono": t,
                "available_wall": start_wall + t,
                "generation": 1,
                **payload,
            }
        )

    def bbo(token="1"):
        return {
            "event_type": "best_bid_ask",
            "asset_id": token,
            "best_bid": ".50",
            "best_ask": ".52",
        }

    def delta(side, q):
        return {
            "event_type": "price_change",
            "price_changes": [
                {
                    "asset_id": "1",
                    "side": side,
                    "price": ".50" if side == "BUY" else ".52",
                    "size": q,
                }
            ],
        }

    def trade(side, p, qty, identity):
        return {
            "event_type": "last_trade_price",
            "asset_id": "1",
            "side": side,
            "price": p,
            "size": qty,
            "trade_id": identity,
            "transaction_hash": "0x" + hashlib.sha256(identity.encode()).hexdigest(),
        }

    await event(0, "CONNECT")
    await event(
        0,
        "FRAME",
        raw=[
            {
                "event_type": "book",
                "asset_id": t,
                "bids": [{"price": ".50", "size": "100"}, {"price": ".49", "size": "100"}],
                "asks": [{"price": ".52", "size": "100"}],
            }
            for t in ("1", "2")
        ],
    )
    await event(
        0.01, "FRAME", raw=[trade("SELL", ".50", "10", "bid"), trade("BUY", ".52", "10", "ask")]
    )
    await event(0.02, "FRAME", raw=[delta("BUY", "80"), delta("SELL", "80")])
    await event(0.10, "FRAME", raw=[bbo("1"), bbo("2")])
    await event(0.20, "FRAME", raw=[delta("BUY", "95"), delta("SELL", "82")])
    for t in (0.3, 0.4, 0.5, 0.511):
        await event(t, "FRAME", raw=[bbo("1"), bbo("2")])
    await event(0.512, "POLL")
    b = e.brokers["sop/trade-through"]
    assert len(e.signal_history) == len(b.orders) == len(e.assumed_orders) == 1
    o = next(iter(b.orders.values()))
    assert D(o.intent.price) == D(".50") and D(o.intent.quantity) == 10
    await event(0.6, "FRAME", raw=[bbo("1"), bbo("2")])
    await event(0.7, "FRAME", raw=trade("SELL", ".49", "3", "fill-valid"))
    assert o.filled == 3
    await event(0.8, "FRAME", raw=delta("BUY", "87"))
    assert o.cancel_requested == pytest.approx(0.8)
    await event(0.9, "FRAME", raw=trade("SELL", ".49", "4", "fill-inflight"))
    assert o.filled == 7
    await event(1.01, "POLL")
    assert o.state == "CANCELLED"
    raw = {
        "slug": e.meta.slug,
        "conditionId": e.meta.condition_id,
        "clobTokenIds": ["1", "2"],
        "outcomePrices": ["1", "0"],
        "closed": True,
        "resolutionStatus": "resolved",
    }
    await event(1.02, "SETTLEMENT", raw=raw)
    assert b.account("M12").payout == 7 and b.account("M12").spent == D("3.50")
    assert D(next(iter(e.assumed_orders.values()))["gross_pnl"]) == 5
    e.store.commit()
    rows = list(
        e.store.db.execute("SELECT kind,payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION'")
    )
    assert [json.loads(v)["classification"] for _, v in rows] == [
        "WITHIN_SIGNAL_VALIDITY",
        "AFTER_SIGNAL_END",
    ]
    await event(300, "END", reason="T300_OBSERVED")
    await e.close()
    from m1223_report import install_report_compatibility, market_result

    from polymaker.m1234_sim.report import summary

    install_report_compatibility()
    report = summary(e.store.path)
    assert report["accounts"][0]["strategy"] == "M12"
    # Final economics must not depend on whether another task has subsequently
    # removed its original public/live-trade files. END already froze this gate.
    def external_evidence_no_longer_available(*_args):
        raise AssertionError("SEALED_MARKET_MUST_USE_COMMITTED_ELIGIBILITY")

    monkeypatch.setattr("m1223_report.own_market_gate", external_evidence_no_longer_available)
    result, orders = market_result(e.store.path, tmp_path)
    assert result["signal_count"] == 1 and result["positive_modelled_fill_orders"] == 1
    assert result["gate_source"] == "COMMITTED_END_OF_MARKET"
    assert D(orders[0]["assumed_gross_pnl"]) == 5
    assert D(orders[0]["known_simulated_gross_pnl"]) == D("3.50")
    assert D(orders[0]["within_signal_validity_quantity"]) == 3
    assert D(orders[0]["after_signal_end_quantity"]) == 4
    assert D(orders[0]["cancel_in_flight_quantity"]) == 4
    from m1223_pair_diagnostics import diagnose

    pairs = diagnose(e.store.path)
    assert pairs["outcomes"]["RECORDED_SIGNAL_SELECTED"] == 1
    assert "ELIGIBLE_PAIR_WITHOUT_RECORDED_SIGNAL_REQUIRES_REVIEW" not in pairs["outcomes"]
    e.retire_runtime()
    assert e.sop_runtime is None and not e.signal_history
