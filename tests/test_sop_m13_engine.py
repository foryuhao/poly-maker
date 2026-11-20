"""Original M13 raw frames through submission, partial fills and settlement."""

import hashlib
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest
from m1223_sim import SopEngine, definition
from m1223_sim_forward import execution_configuration
from sop_signals.m13 import Parameters
from sop_signals.parameters import load_revision

from m1234_fixtures import fixture_events


@pytest.mark.asyncio
@pytest.mark.parametrize("official_settlement", [True, False])
async def test_m13_raw_to_separate_account_partial_cancel_race_and_official_ledgers(tmp_path, official_settlement):
    selected = Path.cwd() / "config/m1223-parameters.json"
    document = json.loads(selected.read_text()) if selected.exists() else {}
    parameters = load_revision(document)[0] if document.get("strategy") == "M13" else Parameters()
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
    support_close = .01 + parameters.round_seconds + .001
    support_refill = min(.2, support_close - .125)
    # The selected numerical candidate may shorten the fixed hit window, but
    # must still prove the unchanged continuous100ms hold and500ms LW history.
    # A250ms round beginning at10ms crosses the250ms coverage bucket just
    # before its end. Supply evidence inside that bucket, before closing;
    # the closing frame itself cannot manufacture missing window coverage.
    support_coverage = support_close - .006
    for t in sorted({support_refill, support_coverage, support_close, *(.1 * i for i in range(1, 7))}):
        if abs(t - support_refill) < 1e-9:
            await event(t, "FRAME", delta("BUY", ".50", "94"))
        else:
            await refresh(t)
    await event(.61, "FRAME", [delta("SELL", ".53", "40"), delta("SELL", ".54", "40")])
    await refresh(.711)
    core = engine.sop_runtime.core
    assert core.withdrawals["1"].seed is not None and not engine.signal_history
    await event(.73, "FRAME", trade("SELL", ".50", "5", "new-retest"))
    await event(.74, "FRAME", delta("BUY", ".50", "89"))
    retest_close = .73 + parameters.round_seconds + .001
    retest_refill = min(.94, retest_close - .125)
    refresh_times = {i / 10 for i in range(8, 13) if i / 10 < retest_close}
    for t in sorted({retest_refill, retest_close, *refresh_times}):
        if abs(t - retest_refill) < 1e-9:
            await event(t, "FRAME", delta("BUY", ".50", "93"))
        else:
            await refresh(t)
    await event(retest_close + .001, "POLL")
    broker = engine.brokers["sop/trade-through"]
    assert len(engine.signal_history) == len(broker.orders) == len(engine.assumed_orders) == 1
    order = next(iter(broker.orders.values()))
    assert order.intent.strategy == order.intent.profile == "M13"
    assert D(order.intent.price) == D(".50") and D(order.intent.quantity) == 10
    assert set(broker.accounts) == {"M13"}
    await refresh(retest_close + .069)
    await event(retest_close + .189, "FRAME", trade("SELL", ".49", "3", "valid-fill"))
    assert order.filled == 3
    cancel_at = retest_close + .289
    await event(cancel_at, "FRAME", delta("BUY", ".50", "90"))
    assert order.cancel_requested == pytest.approx(cancel_at) and not core.signals
    await event(retest_close + .389, "FRAME", trade("SELL", ".49", "4", "cancel-race"))
    assert order.filled == 7
    await event(retest_close + .499, "POLL")
    assert order.state == "CANCELLED"
    if official_settlement:
        await event(retest_close + .509, "SETTLEMENT", {"slug": engine.meta.slug,
                    "conditionId": engine.meta.condition_id, "clobTokenIds": ["1", "2"],
                    "outcomePrices": ["1", "0"], "closed": True, "resolutionStatus": "resolved"})
        assert broker.account("M13").payout == 7
        assert D(next(iter(engine.assumed_orders.values()))["gross_pnl"]) == 5
    else:
        # A gap discovered after cancellation can overlap the original live
        # interval. Keep seven known fills AND the remaining uncertainty.
        await event(retest_close + .509, "GAP", reason="SYNTHETIC_LATE_KNOWN_DATA_GAP")
        assert order.uncertain and order.filled == 7 and engine.resolution is None
        assert not next(iter(engine.assumed_orders.values()))["settled"]
    assert broker.account("M13").spent == D("3.50")
    engine.store.commit()
    attributions = [json.loads(row[0])["classification"] for row in engine.store.db.execute(
        "SELECT payload FROM audit WHERE kind='SOP_FILL_ATTRIBUTION' ORDER BY seq")]
    assert attributions == ["WITHIN_SIGNAL_VALIDITY", "AFTER_SIGNAL_END"]
    await event(300, "END", reason="T300_OBSERVED")
    await engine.close()
    from m1223_report import install_report_compatibility, market_result

    from polymaker.m1234_sim.report import read_state, summary

    install_report_compatibility()
    assert summary(engine.store.path)["accounts"][0]["strategy"] == "M13"
    market, orders = market_result(engine.store.path, tmp_path)
    assert market["signal_count"] == market["positive_modelled_fill_orders"] == 1
    if official_settlement:
        assert D(orders[0]["known_simulated_gross_pnl"]) == D("3.50")
        assert D(orders[0]["assumed_gross_pnl"]) == 5
    else:
        assert market["pending_settlement_orders"] == market["unresolved_orders"] == 1
        assert orders[0]["known_simulated_gross_pnl"] is None
        assert orders[0]["assumed_gross_pnl"] is None
        assert D(orders[0]["known_filled_quantity"]) == 7 and orders[0]["unresolved"]
    assert D(orders[0]["within_signal_validity_quantity"]) == 3
    assert D(orders[0]["cancel_in_flight_quantity"]) == 4
    durable_before_retirement = read_state(engine.store.path)
    engine.retire_runtime()
    assert engine.sop_runtime is None and engine.signals is None and not engine.extra_policies
    assert read_state(engine.store.path) == durable_before_retirement
