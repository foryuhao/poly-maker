"""Native submission records are observable without inventing fills or balances."""

import io
import json

import pytest
from rich.console import Console

from polymaker.dashboard.collector import Collector
from polymaker.dashboard.m1_native import M1_NATIVE_VERSION
from polymaker.dashboard.m1_trades import trade_records
from polymaker.dashboard.model import summarize
from polymaker.dashboard.view import render
from test_dashboard import NOW, event, header, placement, source, write_risk


def live_events(*, finish=True, balance=None):
    h = header()
    h.update(normal_entry_mode="m1_v1", execution_mode="LIVE",
             m1_execution_policy={"fill_tracking": "NOT_TRACKED"})
    opportunity = {"episode_id": "signal-1", "outcome": "YES", "token_id": "yes", "anchor_bid_price": ".12"}
    quote = {**placement("native", "YES")["quote"], "price": .12, "size": 8.4}
    rows = [event("sm_session", h),
        event("sm_m1_signal", {"event": "RF_CONFIRMED", "token_id": "yes", "rf_id": "rf1"}, 1),
        event("sm_m1_signal", {"event": "EX_CONFIRMED", "token_id": "yes", "ex_id": "ex1"}, 2),
        event("sm_m1_signal", {"event": "OPPORTUNITY_IDENTIFIED", "opportunity": opportunity}, 3),
        event("sm_m1_live_signal", {"id": "signal-1", "token": "yes", "outcome": "YES",
            "price": ".12", "quantity": "8.4", "state": "OBSERVED"}, 4),
        event("sm_m1_native_post", {"opportunity_id": "signal-1", "order_id": "native",
            "post_number": 1, "quote": quote}, 5),
        event("sm_placement_result", {**placement("native", "YES"), "quote": quote}, 6),
        event("sm_m1_live_post", {"opportunity_id": "signal-1", "order_id": "native",
            "outcome": "ACK", "quote": quote}, 7),
        event("sm_m1_account_snapshot", {"source": "CLOB_BALANCE_ALLOWANCE_ONLY",
            **({"official_balance_usd": balance} if balance is not None else {})}, 8),
        event("sm_m1_candidate_skipped", {"opportunity": {"episode_id": "signal-2", "outcome": "NO",
            "token_id": "no", "anchor_bid_price": ".88"}, "reason": "PRICE_OUTSIDE_TEST_BAND"}, 9),
        event("sm_m1_heartbeat", {"signal_counts": {"RF_CONFIRMED": 1, "EX_CONFIRMED": 1},
            "signals": {"signals": 1, "fill_tracking": "NOT_TRACKED", "by_signal": [
                {"id": "signal-1", "outcome": "YES", "state": "ACK", "posts": 1}]}}, 10)]
    if finish:
        rows.append(event("sm_run_result", {"execution_status": "SUBMISSION_WINDOW_COMPLETE",
            "fill_tracking": "NOT_TRACKED", "positions_verified": False}, 11))
    return rows


def test_native_ack_and_skipped_signals_have_prices_times_and_unknown_fills():
    summary = summarize("live", live_events(), [source()])
    assert summary["m1_native_version"] == M1_NATIVE_VERSION
    assert summary["real_submitted_orders"] == 1
    assert summary["signal_counts"]["M1"] == 2
    first, skipped = summary["m1_signals"]
    assert first["ts"] == NOW+3 and first["price"] == ".12" and first["state"] == "ACK"
    assert skipped["price"] == ".88" and skipped["reason"] == "PRICE_OUTSIDE_TEST_BAND"
    assert summary["m1_component_latest"]["RF"] == {"ts": NOW+1, "outcome": "YES"}
    assert summary["m1_components"] == {"RF": 1, "EX": 1}
    assert summary["trading_pnl"] is None and summary["residual_pnl"] is None
    assert "M1_FILL_UNVERIFIED" in summary["issues"]
    assert "TERMINAL_EXECUTION_UNRESOLVED" not in summary["issues"]
    records = trade_records([summary])
    assert len(records) == 1 and records[0]["quantity"] == 8.4
    assert records[0]["filled"] is records[0]["cost"] is records[0]["pnl"] is None
    assert records[0]["status"] == "已接单，成交未核实"


@pytest.mark.parametrize("height", [24, 50])
def test_untracked_live_render_does_not_claim_zero_inventory_pnl_or_fake_balance(height):
    summary = summarize("live", live_events(), [source()])
    console = Console(width=140, file=io.StringIO(), record=True)
    console.print(render({"observed_at": NOW+20, "sessions": [summary], "current_session_id": "live"},
        now=NOW+20, width=140, height=height))
    text = console.export_text()
    assert "M1 · 真实" in text and "真实持仓：未核实" in text
    assert "账户余额：日志未记录" in text
    assert "本场已确认 PnL：未核实" in text and "YES 0 / NO 0" not in text
    assert "累计真实已确认 PnL" not in text
    if height >= 34:
        assert "已接单，成交未核实" in text and "暂不绘制为零收益" in text


@pytest.mark.parametrize("value", ["0", "31.123456"])
def test_recorded_official_balance_is_shown_with_age_but_modeled_balance_is_not(value):
    rows = live_events(balance=value)
    summary = summarize("live", rows, [source()])
    assert summary["account_balance_usdc"] == value and summary["account_balance_at"] == NOW+8
    console = Console(width=140, file=io.StringIO(), record=True)
    console.print(render({"observed_at": NOW+20, "sessions": [summary], "current_session_id": "live"},
        now=NOW+20, width=140, height=50))
    text = console.export_text()
    assert f"账户余额：{value} USDC" in text and "已过期" in text
    rows[0]["payload"]["execution_mode"] = "OFFLINE_TEST"
    assert summarize("offline", rows, [source()])["account_balance_usdc"] is None


def test_native_post_without_heartbeat_or_placement_is_still_an_unknown_order():
    rows = [e for e in live_events(finish=False) if e["kind"] not in {"sm_m1_heartbeat", "sm_placement_result"}]
    summary = summarize("live", rows, [source()])
    record = trade_records([summary])[0]
    assert summary["real_submitted_orders"] == 1
    assert record["status"] == "已接单，成交未核实" and record["filled"] is None


@pytest.mark.parametrize("policy", ["MAKER_ONLY", None])
def test_legacy_policy_header_shape_does_not_break_native_observer(policy):
    rows = live_events()
    rows[0]["payload"]["m1_execution_policy"] = policy
    result = summarize("old-native", rows, [source()])
    assert result["m1_fill_tracking"] == "NOT_TRACKED"
    assert result["real_submitted_orders"] == 1 and result["trading_pnl"] is None


def test_existing_native_source_replays_new_event_types_without_changing_journal(tmp_path):
    journal = tmp_path / "journal"
    journal.mkdir()
    sid = "m1_live_"+"a"*32
    path = journal / f"rdp_sm_wallet_{sid}.jsonl"
    rows = live_events()
    rows[0]["payload"]["session_id"] = sid
    write_risk(path, rows)
    path.write_text(path.read_text().replace('"session-test"', json.dumps(sid)))
    original = path.read_bytes()
    collector = Collector(tmp_path, tmp_path / "derived")
    collector.poll(now=NOW+20)
    # Simulate the old observer's cursor after it skipped native RF/EX/detail events.
    collector.db.execute("DELETE FROM events WHERE sid=? AND json_extract(event,'$.kind') LIKE 'sm_m1_%'", (sid,))
    collector.db.execute("DELETE FROM metadata WHERE key='m1_native_version'")
    collector.db.commit()
    collector.close()
    collector = Collector(tmp_path, tmp_path / "derived")
    try:
        for _ in range(4):
            snapshot = collector.poll(now=NOW+21)
        summary = next(s for s in snapshot["sessions"] if s["session_id"] == sid)
        assert summary["real_submitted_orders"] == 1 and summary["signal_counts"]["M1"] == 2
        assert summary["m1_component_latest"]["EX"]["ts"] == NOW+2
        assert trade_records([summary])[0]["filled"] is None
        assert path.read_bytes() == original
    finally:
        collector.close()
