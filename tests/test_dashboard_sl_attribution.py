"""Only a proven TP-expiry exit belongs to SL; fees and residual stay exact."""

import json
from decimal import Decimal as D

import pytest

from polymaker.dashboard.archive import Archiver, session_report
from polymaker.dashboard.collector import Collector, public_snapshot
from polymaker.dashboard.model import summarize
from polymaker.dashboard.view import read_snapshot
from test_dashboard import NOW, SID, event, fill, header, placement, source, write_risk


def expiry_events():
    return [event("sm_session", header()),
        event("sm_placement_result", placement("opening", "YES"), 1),
        event("sm_trade", fill("opening", "YES", ".4", liquidity_role="TAKER",
                              principal_usdc="2", fee_usdc=".084"), 2),
        event("sm_placement_result", placement("exit", "NO", "w1:ca-expiry-exit"), 3),
        event("sm_trade", fill("exit", "NO", ".65", liquidity_role="TAKER",
                              principal_usdc="3.25", fee_usdc=".07"), 4),
        event("sm_ca_expiry_exit_result", {"orders": [{"order_id": "exit", "complete": True}]}, 5)]


def test_tp_expiry_taker_pnl_includes_opening_and_exit_fees_and_deduplicates():
    events = expiry_events()
    events.append(event("sm_trade", events[4]["payload"], 6))
    s = summarize(SID, events, [source()])
    assert {k: D(v) for k, v in s["pnl_by_role"].items()} == {
        "TP": D(0), "SL": D("-.404"), "OTHER": D(0)}
    assert D(s["fees"]) == D(".154") and D(s["known_pnl"]) == D("-.404")
    assert s["pairs"][0]["category"] == "SL" and not s["pending_orders"]
    report = session_report(s)
    assert "SL: -0.404" in report and "TP 到期后的 Taker 退出归 SL" in report
    assert "；BA:" not in report


def test_public_snapshot_keeps_open_legacy_view_alive_without_counting_sl_twice():
    s = summarize(SID, expiry_events(), [source()])
    published = public_snapshot({"sessions": [s], "current_session_id": SID})["sessions"][0]
    assert published["pnl_by_role"]["BA"] == "0"
    assert set(s["pnl_by_role"]) == {"TP", "SL", "OTHER"}
    assert sum(map(D, published["pnl_by_role"].values())) == D(s["known_pnl"])


def test_new_view_can_read_legacy_snapshot_during_migration_without_renaming_ba_to_sl(tmp_path):
    s = summarize(SID, expiry_events(), [source()])
    s["pnl_by_role"] = {"TP": "0", "BA": "-.404", "OTHER": "0"}
    s["pairs"][0]["category"] = "BA"
    s["pnl_events"][0]["category"] = "BA"
    snapshot = {"sessions": [s], "current_session_id": SID}
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(snapshot))
    original = path.read_bytes()
    loaded = read_snapshot(tmp_path)["sessions"][0]
    assert loaded["pnl_by_role"] == {"TP": "0", "SL": "0", "OTHER": "-0.404"}
    assert loaded["pairs"][0]["category"] == "OTHER"
    assert loaded["pnl_events"][0]["category"] == "OTHER"
    assert path.read_bytes() == original


@pytest.mark.parametrize("window,post_kind,result_kind", [
    ("w2", None, None),
    ("w2:ba:1", None, None),
    ("w2:ba-handoff:1", "sm_ba_taker_post_returned", "sm_ba_taker_result"),
    ("w2:system-exit", "sm_network_exit_post_returned", "sm_network_exit_result"),
])
def test_other_taker_ba_and_system_exit_do_not_leak_into_sl(window, post_kind, result_kind):
    events = expiry_events()
    events[3] = event("sm_placement_result", placement("exit", "NO", window), 3)
    events = events[:5]
    if post_kind:
        events.insert(4, event(post_kind, {"order_id": "exit", "post_started": True}, 3.5))
        events.append(event(result_kind, {"orders": [{"order_id": "exit", "complete": True}]}, 5))
    s = summarize(SID, events, [source()])
    assert D(s["pnl_by_role"]["SL"]) == 0
    assert D(s["pnl_by_role"]["OTHER"]) == D("-.404")
    assert D(s["pair_pnl"]) == D("-.404")


def test_partial_tp_then_expiry_sl_then_residual_settlement_use_three_categories():
    events = expiry_events()[:3] + [
        event("sm_placement_result", placement("tp", "NO", "w1:ca-tp"), 3),
        event("sm_trade", fill("tp", "NO", ".5", quantity=2), 4),
        event("sm_placement_result", placement("exit", "NO", "w1:ca-expiry-exit"), 5),
        event("sm_trade", fill("exit", "NO", ".65", quantity=2, liquidity_role="TAKER",
                              principal_usdc="1.30", fee_usdc=".028"), 6),
        event("sm_ca_expiry_exit_result", {"orders": [{"order_id": "exit", "complete": True}]}, 7),
        event("sm_run_result", {"execution_status": "COMPLETED", "final_close_barrier": "SAFE",
            "confirmed_yes": 5, "confirmed_no": 4, "merged_pairs": 0,
            "sizing_report": {"final_winning_outcome": "YES",
                "intentional_residual_pnl": ".5832", "unintentional_residual_pnl": "0"}}, 8)]
    s = summarize(SID, events, [source()])
    assert {k: D(v) for k, v in s["pnl_by_role"].items()} == {
        "TP": D(".1664"), "SL": D("-.1616"), "OTHER": D(".5832")}
    assert D(s["known_pnl"]) == D(s["trading_pnl"]) == D(".588")
    assert s["pnl_events"][-1]["category"] == "OTHER"


@pytest.mark.parametrize("status,missing_fee", [("MATCHED", False), ("CONFIRMED", True)])
def test_sl_requires_confirmed_fill_and_exact_taker_cost(status, missing_fee):
    events = expiry_events()
    events[4]["payload"]["status"] = status
    if missing_fee:
        events[4]["payload"].pop("fee_usdc")
    s = summarize(SID, events, [source()])
    assert D(s["pnl_by_role"]["SL"]) == 0
    assert not s["pairs"]
    if missing_fee:
        assert "TAKER_EXACT_COST_PENDING" in s["issues"]


def test_restart_recomputes_v2_cache_and_keeps_old_archive_immutable(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    write_risk(journal / f"rdp_sm_wallet_{SID}.jsonl", expiry_events())
    output = tmp_path / "derived"
    c = Collector(tmp_path, output)
    try:
        s = c.poll(now=NOW + 10)["sessions"][0]
        old = {**s, "pnl_attribution_version": 2,
               "pnl_by_role": {"TP": "0", "BA": "0", "OTHER": "-.404"}}
        old["pairs"] = [{**p, "category": "OTHER"} for p in s["pairs"]]
        old["pnl_events"] = [{**p, "category": "OTHER"} for p in s["pnl_events"]]
        c.db.execute("UPDATE summaries SET snapshot=? WHERE sid=?", (json.dumps(old), SID))
        c.db.commit()
        assert any(e["kind"] == "sm_ca_expiry_exit_result" for e in c.events(SID))
    finally:
        c.close()
    c = Collector(tmp_path, output)
    try:
        s = c.snapshots()[0]
        assert s["pnl_attribution_version"] == 3 and D(s["pnl_by_role"]["SL"]) == D("-.404")
        assert s["known_pnl"] == old["known_pnl"] and not s["pending_orders"]
        archiver = Archiver(c)
        first = archiver.market(s, NOW + 301)
        original = (first / "snapshot.json").read_bytes()
        changed = {**s, "reason": "late evidence"}
        second = archiver.market(changed, NOW + 302)
        assert first != second and (first / "snapshot.json").read_bytes() == original
        assert "SL:" in (second / "summary.md").read_text()
    finally:
        c.close()
