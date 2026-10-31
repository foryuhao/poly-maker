"""Resolved result identity, deferred reads and residual accounting."""

from concurrent.futures import Future
from decimal import Decimal

import pytest

from polymaker.dashboard.collector import Collector
from polymaker.dashboard.model import summarize
from polymaker.dashboard.settlement import SettlementObserver, validate_resolution
from test_dashboard import NOW, SID, event, fill, header, placement, source, write_risk
from test_dashboard import files as files

CONDITION = "0x" + "12" * 32


def residual_events():
    p = placement("open", "YES")
    p["quote"]["market_id"] = CONDITION
    return [event("sm_session", header()), event("sm_placement_result", p, 1),
            event("sm_trade", fill("open", "YES", ".64"), 2),
            event("sm_run_result", {"market_id": CONDITION, "execution_status": "COMPLETED",
                                   "final_close_barrier": "SAFE_TO_CONTINUE"}, 3)]


def market():
    return {"slug": header()["slug"], "conditionId": CONDITION, "closed": True,
            "umaResolutionStatus": "resolved", "clobTokenIds": '["no","yes"]',
            "outcomePrices": '["1","0"]'}


@pytest.mark.parametrize("prices,pnl", [('["1","0"]', "-3.20"), ('["0","1"]', "1.80")])
def test_resolved_token_identity_computes_large_residual_once_as_other(prices, pnl):
    events = residual_events()
    initial = summarize(SID, events, [source()])
    assert initial["residual_pnl"] is None
    m = {**market(), "outcomePrices": prices}
    result = validate_resolution(m, initial)
    resolution = {"kind": "dashboard_market_resolution", "source_kind": "resolution",
                  "payload": {"result": result, "error": None}}
    s = summarize(SID, events + [resolution], [source()])
    assert Decimal(s["residual_pnl"]) == Decimal(pnl)
    assert Decimal(s["pnl_by_role"]["OTHER"]) == Decimal(pnl)
    assert Decimal(s["trading_pnl"]) == Decimal(pnl)
    assert Decimal(s["pair_pnl"]) == 0
    assert len(s["pnl_events"]) == 1
    assert s["pnl_events"][0]["kind"] == "RESIDUAL_SETTLEMENT"
    assert not s["other_pending"]


@pytest.mark.parametrize("change,pending", [
    ({"closed": False}, True), ({"umaResolutionStatus": "proposed"}, True),
    ({"umaResolutionStatus": "disputed"}, True), ({"umaResolutionStatus": None}, True),
    ({"resolutionStatus": "active"}, True),
    ({"slug": "wrong-market"}, False), ({"conditionId": "0x" + "34" * 32}, False),
    ({"clobTokenIds": '["other","no"]'}, False), ({"clobTokenIds": '["yes","yes"]'}, False),
    ({"outcomePrices": '["0.99","0.01"]'}, False), ({"outcomePrices": '["NaN","1"]'}, False),
])
def test_unresolved_or_ambiguous_result_never_produces_settlement_pnl(change, pending):
    s = summarize(SID, residual_events(), [source()])
    if pending:
        assert validate_resolution({**market(), **change}, s) is None
    else:
        with pytest.raises(ValueError):
            validate_resolution({**market(), **change}, s)


def test_observer_persists_resolution_rebuilds_summary_and_survives_restart(files, monkeypatch):
    root, path, output = files
    write_risk(path, residual_events())
    c = Collector(root, output)
    observer = SettlementObserver(c)
    future = Future()
    submitted = []
    monkeypatch.setattr(observer.pool, "submit", lambda fn, session: submitted.append(session) or future)
    try:
        snapshot = c.poll(NOW + 301)
        observer.tick(snapshot)
        assert len(submitted) == 1 and snapshot["sessions"][0]["residual_pnl"] is None
        future.set_result(validate_resolution(market(), snapshot["sessions"][0]))
        observer.tick(snapshot)
        assert Decimal(snapshot["sessions"][0]["pnl_by_role"]["OTHER"]) == Decimal("-3.2")
        assert len(submitted) == 1
        assert any(e["source_kind"] == "resolution" for e in c.events(SID))
    finally:
        observer.close()
        c.close()
    c = Collector(root, output)
    try:
        assert Decimal(c.poll(NOW + 302)["sessions"][0]["residual_pnl"]) == Decimal("-3.2")
    finally:
        c.close()


def test_lookup_failure_keeps_pending_and_retries_without_zeroing_residual(files, monkeypatch):
    root, path, output = files
    write_risk(path, residual_events())
    c = Collector(root, output)
    observer = SettlementObserver(c)
    future = Future()
    monkeypatch.setattr(observer.pool, "submit", lambda *args: future)
    try:
        snapshot = c.poll(NOW + 301)
        observer.tick(snapshot)
        future.set_exception(OSError("network unavailable"))
        observer.tick(snapshot)
        s = snapshot["sessions"][0]
        assert s["residual_pnl"] is None and s["trading_pnl"] is None
        assert s["other_pending"] and "SETTLEMENT_LOOKUP_FAILED" in s["issues"]
        assert observer.future is None  # Retry interval prevents a hot loop.
    finally:
        observer.close()
        c.close()


@pytest.mark.parametrize("fault", ["active", "catching_up", "pending_fill", "wrong_identity"])
def test_resolution_cannot_override_unfinished_or_conflicting_local_evidence(fault):
    events = residual_events()
    sources = [source()]
    initial = summarize(SID, events, sources)
    result = validate_resolution(market(), initial)
    if fault == "active":
        events = events[:-1]
    elif fault == "catching_up":
        sources[0]["caught_up"] = False
    elif fault == "pending_fill":
        events[2]["payload"]["status"] = "MATCHED"
    else:
        result["condition_id"] = "other-market"
    events.append({"kind": "dashboard_market_resolution", "source_kind": "resolution",
                   "payload": {"result": result, "error": None}})
    s = summarize(SID, events, sources)
    assert s["trading_pnl"] is None
    if s["residual"]:
        assert s["residual_pnl"] is None
