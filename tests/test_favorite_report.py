"""Synthetic run artifacts; never invokes the live wrapper."""
import json
from pathlib import Path

import pytest

from polymaker.domain import Side, TradeState
from polymaker.favorite_report import aggregate, build_summary, write_artifacts
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent, UserEventProcessor


def test_fill_status_update_preserves_accepted_timestamp():
    first = TradeEvent("yes", Side.BUY, .64, 5, "t1", TradeState.MATCHED, 1052.2)
    update = TradeEvent("yes", Side.BUY, .64, 5, "t1", TradeState.CONFIRMED, 0)
    confirmed = UserEventProcessor._advanced_status(first, update)
    assert confirmed.ts == pytest.approx(1052.2)
    assert confirmed.status is TradeState.CONFIRMED


def fixture_report(*, first=0.64, pair=0.36, qty=5, pair_qty=5,
                   divergence=False, stale=False, reconciled=True):
    start = 1000.0
    decisions = [
        (1030.2, "ENTRY_CONFIRM", None, None, None),
        (1047.6, "ENTRY_QUOTING", 0.62, None, None),
        (1052.3, "FIRST_LEG_RISK_WINDOW", None, -0.05, None),
        (1061.26, "PAIR_STOP", 0.44, -0.55, "EMERGENCY_STOP"),
        (1063.7, "PAIR_STOP", 0.37, -0.35, "EMERGENCY_STOP"),
        (1065.4, "PAIR_STOP", 0.36, -0.30, "EMERGENCY_STOP"),
        (1066.0, "COMPLETE", None, 0, "EMERGENCY_STOP"),
    ] if qty else [(1180.0, "NO_TRADE", None, None, None)]
    rows = [{"ts": ts, "data": {"strategy_state": state, "desired_price": quote,
                                 "total_lockable_pnl": pnl, "pair_trigger": trigger,
                                 "favorite_side": "YES", "favorite_probability": .625,
                                 "pair_quote_mode": "AGGRESSIVE" if trigger else None}}
            for ts, state, quote, pnl, trigger in decisions]
    return {
        "run_id": "19700101T001640000000Z_btc-updown-5m-1000",
        "market": "btc-updown-5m-1000", "condition_id": "0xcondition",
        "start": start, "selected_at": 990.0, "finalized_at": 1066.0,
        "selected_tick": 0.01, "min_order_size": 5,
        "effective_config": {"first_leg_qty": 5, "entry_start_s": 30, "probation_max_s": 20},
        "favorite_at_entry": "YES" if qty else None,
        "favorite_probability_at_entry": .625 if qty else None,
        "first_quote_time": 1047.6 if qty else None,
        "first_quote_price": .62 if qty else None,
        "first_quote_reprices": 1 if qty else 0,
        "first_fill_time": 1052.2 if qty else None,
        "first_leg_final_qty": qty if qty else None,
        "first_leg_vwap": first if qty else None,
        "risk_decision_time": 1061.26 if qty else None,
        "risk_decision_total_lockable_pnl": -.55 if qty else None,
        "decision": "PAIR_STOP" if qty else "NO_TRADE",
        "peak_total_lockable_pnl": None,
        "trailing_floor": None,
        "pair_trigger": "EMERGENCY_STOP" if qty else None,
        "yes_qty": qty if reconciled else 0,
        "no_qty": pair_qty if reconciled else 0,
        "residual_side": "YES" if qty > pair_qty else None,
        "residual_or_settlement_pnl": None, "maker_rebate": None,
        "total_settled_pnl": None, "owned_open_orders": 0,
        "fill_truth_complete": True, "position_reconciled": reconciled,
        "strategy_state": "COMPLETE" if qty == pair_qty else "UNPAIRABLE_RESIDUAL",
        "decisions": rows,
        "first_orders": [{"timestamp": 1048.0, "order_id": "first-order", "qty": qty,
                          "price": first, "side": "BUY"}] if qty else [],
        "pair_orders": [{"timestamp": 1063.7, "order_id": "pair-order", "qty": pair_qty,
                         "price": pair, "side": "BUY"}] if pair_qty else [],
        "first_fills": [{"timestamp": 1052.2, "order_id": "first-order", "trade_id": "t1",
                         "token": "yes", "outcome": "YES", "side": "BUY", "fill_qty": qty,
                         "fill_price": first}] if qty else [],
        "pair_fills": [{"timestamp": 1065.8, "order_id": "pair-order", "trade_id": "t2",
                        "token": "no", "outcome": "NO", "side": "BUY", "fill_qty": pair_qty,
                        "fill_price": pair}] if pair_qty else [],
    }


def raw_file(path: Path, *, stale=False, divergence=False):
    events = []
    if stale:
        events.append((1062.67, "order_place_aborted_stale_connectivity", "info"))
    if divergence:
        events.append((1065.9, "position_divergence", "error"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"timestamp": f"1970-01-01T00:{int(ts // 60):02d}:{ts % 60:06.3f}Z",
                             "event": event, "level": level}) + "\n"
                            for ts, event, level in events))


@pytest.mark.parametrize("first,pair,outcome,pnl", [
    (.64, .36, "BREAKEVEN", 0), (.64, .40, "LOSS", -.20),
    (.62, .34, "PROFIT", .20)])
def test_pair_economics_and_timestamps(tmp_path, first, pair, outcome, pnl):
    report = fixture_report(first=first, pair=pair)
    directory = tmp_path / report["run_id"]
    raw_file(directory / "raw.log")
    summary = write_artifacts(report, "COMPLETED", directory)
    assert summary["strategy_outcome"] == outcome
    assert summary["economics"]["gross_paired_locked_pnl"] == pytest.approx(pnl)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    first_fill = next(e for e in events if e["event"] == "FIRST_FILL")
    pair_fill = next(e for e in events if e["event"] == "PAIR_FILL")
    assert first_fill["market_elapsed_s"] == pytest.approx(52.2)
    assert pair_fill["market_elapsed_s"] == pytest.approx(65.8)
    assert pair_fill["seconds_since_first_fill"] == pytest.approx(13.6)
    assert summary["economics"]["final_net_pnl"] is None
    chat = (directory / "chat_report.md").read_text()
    assert "## Anomalies" in chat and "## Final Reconciliation" in chat
    assert len(chat.splitlines()) <= 150
    assert len(chat.encode()) < 8192


def test_recovered_and_unresolved_anomalies(tmp_path):
    for reconciled in (True, False):
        report = fixture_report(reconciled=reconciled)
        report["run_id"] += str(reconciled)
        directory = tmp_path / report["run_id"]
        raw_file(directory / "raw.log", stale=True, divergence=True)
        summary = write_artifacts(report, "COMPLETED", directory)
        status = {a["event"]: a["status"] for a in summary["anomalies"]}
        assert status["STALE_CONNECTIVITY"] == "RECOVERED"
        assert status["POSITION_DIVERGENCE"] == ("RECOVERED" if reconciled else "UNRESOLVED")
        assert summary["first_leg"]["qty"] == summary["pair"]["qty"] == 5
        assert summary["economics"]["gross_paired_locked_pnl"] == pytest.approx(0)
        assert summary["engineering_result"] == ("COMPLETED" if reconciled else "INCOMPLETE_RECONCILIATION")
        chat = (directory / "chat_report.md").read_text()
        assert "STALE_CONNECTIVITY" in chat and "PAIR_FILL" in chat


def test_no_trade_residual_and_aggregate(tmp_path):
    base = tmp_path / "runs"
    cases = [fixture_report(first=.62, pair=.34), fixture_report(first=.64, pair=.40),
             fixture_report(), fixture_report(qty=0, pair_qty=0),
             fixture_report(qty=2, pair_qty=0)]
    for index, report in enumerate(cases):
        report["run_id"] += str(index)
        directory = base / report["run_id"]
        raw_file(directory / "raw.log")
        summary = write_artifacts(report, "COMPLETED", directory)
        if index == 3:
            assert summary["strategy_outcome"] == "NO_TRADE"
        if index == 4:
            assert summary["strategy_outcome"] == "UNSETTLED_RESIDUAL"
            assert summary["economics"]["gross_paired_locked_pnl"] == 0
            assert summary["economics"]["final_net_pnl"] is None
    output = aggregate(base, 5)
    assert "Last 5 Markets" in output
    assert "No Trade: 1" in output
    assert "known 5/5" in output
    assert "Emergency: 4" in output
    assert "Residual markets: 1" in output
    assert "Last 2 Markets" in aggregate(base, 2)


def test_preflight_artifacts_are_not_counted_as_completed_market(tmp_path):
    report = fixture_report(qty=0, pair_qty=0)
    report["strategy_state"] = "NOT_ARMED"
    directory = tmp_path / "runs" / report["run_id"]
    raw_file(directory / "raw.log")
    write_artifacts(report, "PREFLIGHT_PASS", directory)
    assert (directory / "run.json").exists()
    assert (directory / "chat_report.md").exists()
    assert "Last 0 Markets" in aggregate(tmp_path / "runs", 10)


def test_runner_peak_capture_and_emergency_exit_delta(tmp_path):
    emergency = fixture_report(first=.62, pair=.34)
    emergency["run_id"] += "emergency"
    emergency_dir = tmp_path / emergency["run_id"]
    raw_file(emergency_dir / "raw.log")
    emergency_summary = write_artifacts(emergency, "COMPLETED", emergency_dir)
    assert emergency_summary["pair"]["trigger_pnl"] == pytest.approx(-.55)
    assert emergency_summary["pair"]["emergency_exit_delta"] == pytest.approx(.75)
    assert emergency_summary["pair"]["trigger_to_fill_s"] == pytest.approx(4.54)

    runner = fixture_report(first=.62, pair=.34)
    runner["run_id"] += "runner"
    runner["decision"] = "RUNNER"
    runner["pair_trigger"] = "TRAILING_STOP"
    runner["peak_total_lockable_pnl"] = .40
    for row in runner["decisions"]:
        if row["data"]["strategy_state"] == "PAIR_STOP":
            row["data"]["pair_trigger"] = "TRAILING_STOP"
    runner_dir = tmp_path / runner["run_id"]
    raw_file(runner_dir / "raw.log")
    runner_summary = write_artifacts(runner, "COMPLETED", runner_dir)
    assert runner_summary["pair"]["peak_capture_ratio"] == pytest.approx(.5)


def test_fill_truth_overrides_frozen_memory_in_every_artifact(tmp_path):
    report = fixture_report()
    report["first_leg_final_qty"] = 2
    report["first_leg_vwap"] = .99  # stale remembered value cannot price execution
    report["paired_qty"] = 2
    directory = tmp_path / "runs" / report["run_id"]
    direct_summary = build_summary(report, [], "COMPLETED", directory)
    assert direct_summary["economics"]["paired_qty"] == 5
    assert any(a["event"] == "ACCOUNTING_STATE_MISMATCH" for a in direct_summary["anomalies"])
    raw_file(directory / "raw.log")
    summary = write_artifacts(report, "COMPLETED", directory)
    assert summary["first_leg"]["qty"] == 5
    assert summary["pair"]["qty"] == 5
    assert summary["first_leg"]["vwap"] == pytest.approx(.64)
    assert summary["pair"]["vwap"] == pytest.approx(.36)
    assert summary["economics"]["paired_qty"] == 5
    assert summary["economics"]["residual_qty"] == 0
    assert summary["economics"]["gross_paired_locked_pnl"] == pytest.approx(0)
    mismatch = next(a for a in summary["anomalies"] if a["event"] == "ACCOUNTING_STATE_MISMATCH")
    assert mismatch["status"] == "RECOVERED_BY_FILL_TRUTH"
    assert mismatch["detail"]["authoritative_qty"] == 5
    assert mismatch["detail"]["memory_qty"] == 2
    assert json.loads((directory / "run.json").read_text())["economics"]["paired_qty"] == 5
    assert "Gross paired locked PnL: $+0.00; paired qty: 5" in (directory / "chat_report.md").read_text()
    assert "ACCOUNTING_STATE_MISMATCH" in (directory / "chat_report.md").read_text()
    assert "ACCOUNTING_STATE_MISMATCH" in (directory / "events.jsonl").read_text()
    assert "Gross locked PnL (known 1/1): $+0.00" in aggregate(directory.parent, 1)


def test_partial_pair_uses_accepted_quantities(tmp_path):
    report = fixture_report(pair=.34, pair_qty=3)
    report["first_leg_final_qty"] = 2
    report["paired_qty"] = 5
    directory = tmp_path / report["run_id"]
    raw_file(directory / "raw.log")
    summary = write_artifacts(report, "UNPAIRABLE_RESIDUAL", directory)
    assert summary["first_leg"]["qty"] == 5
    assert summary["pair"]["qty"] == 3
    assert summary["economics"]["paired_qty"] == 3
    assert summary["economics"]["residual_qty"] == 2
    assert summary["economics"]["residual_side"] == "YES"
    assert summary["economics"]["gross_paired_locked_pnl"] == pytest.approx(.06)
    assert summary["economics"]["residual_settlement_pnl"] is None
    assert "Gross locked PnL (known 1/1): $+0.06" in aggregate(tmp_path, 1)
    assert "final PnL: 0.060 (n=1)" in aggregate(tmp_path, 1)


def test_multifill_weighted_vwaps(tmp_path):
    report = fixture_report()
    report["first_fills"] = [
        {**report["first_fills"][0], "trade_id": "f1", "fill_qty": 2, "fill_price": .62},
        {**report["first_fills"][0], "trade_id": "f2", "fill_qty": 3, "fill_price": .64},
    ]
    report["pair_fills"] = [
        {**report["pair_fills"][0], "trade_id": "p1", "fill_qty": 1, "fill_price": .34},
        {**report["pair_fills"][0], "trade_id": "p2", "fill_qty": 4, "fill_price": .36},
    ]
    report["first_leg_vwap"] = .99
    directory = tmp_path / report["run_id"]
    raw_file(directory / "raw.log")
    summary = write_artifacts(report, "COMPLETED", directory)
    assert summary["first_leg"]["qty"] == summary["pair"]["qty"] == 5
    assert summary["first_leg"]["vwap"] == pytest.approx((2 * .62 + 3 * .64) / 5)
    assert summary["pair"]["vwap"] == pytest.approx((1 * .34 + 4 * .36) / 5)
    assert summary["economics"]["gross_paired_locked_pnl"] == pytest.approx(.06)


def test_no_memory_fallback_and_incomplete_fill_truth(tmp_path):
    report = fixture_report(qty=0, pair_qty=0)
    report["first_leg_final_qty"] = 5
    directory = tmp_path / "no-fills"
    raw_file(directory / "raw.log")
    summary = write_artifacts(report, "COMPLETED", directory)
    assert summary["first_leg"]["qty"] == 0
    assert summary["strategy_outcome"] == "NO_TRADE"
    assert summary["anomalies"][0]["status"] == "RECOVERED_BY_FILL_TRUTH"

    report = fixture_report()
    report["first_leg_final_qty"] = 2
    report["fill_truth_complete"] = False
    directory = tmp_path / "incomplete"
    raw_file(directory / "raw.log")
    summary = write_artifacts(report, "COMPLETED", directory)
    assert summary["first_leg"]["qty"] == 5
    assert summary["economics"]["gross_paired_locked_pnl"] is None
    assert summary["economics"]["status"] == "INCOMPLETE_FILL_TRUTH"
    assert summary["engineering_result"] == "INCOMPLETE_RECONCILIATION"
    assert next(a for a in summary["anomalies"] if a["event"] == "ACCOUNTING_STATE_MISMATCH")["status"] == "UNRESOLVED"


def test_overpair_is_explicit_accounting_anomaly(tmp_path):
    report = fixture_report(qty=3, pair_qty=5)
    directory = tmp_path / report["run_id"]
    raw_file(directory / "raw.log")
    summary = write_artifacts(report, "COMPLETED", directory)
    assert summary["economics"]["paired_qty"] == 3
    assert summary["economics"]["residual_qty"] == 2
    assert summary["economics"]["residual_side"] == "NO"
    assert summary["economics"]["status"] == "OVERPAIRED"
    assert summary["engineering_result"] == "INCOMPLETE_RECONCILIATION"
    assert any(a["event"] == "ACCOUNTING_OVERPAIR" for a in summary["anomalies"])


def test_lifecycle_statuses_remain_one_economic_fill(tmp_path):
    store = StateStore(":memory:")
    try:
        processor = UserEventProcessor(store)
        processor.register_order("first", "cid", "yes", Side.BUY, 5, .64)
        for status in (TradeState.MATCHED, TradeState.MINED, TradeState.CONFIRMED):
            processor.on_trade(TradeEvent("yes", Side.BUY, .64, 5, "trade", status,
                                          1052.2 if status is TradeState.MATCHED else 0,
                                          "first"), "cid")
        processor.on_trade(TradeEvent("yes", Side.BUY, .64, 5, "trade",
                                      TradeState.CONFIRMED, 0, "first"), "cid", source="REST")
        accepted = processor.economic_fills_for_orders({"first"})
        assert len(accepted) == 1
        assert accepted[0].size == 5
        assert accepted[0].ts == pytest.approx(1052.2)
        assert store.position("yes").size == 5
        report = fixture_report()
        report["first_fills"] = [{"timestamp": fill.ts, "order_id": fill.maker_order_id,
                                  "trade_id": fill.trade_id, "token": fill.token_id,
                                  "outcome": "YES", "side": fill.our_side.value,
                                  "fill_qty": fill.size, "fill_price": fill.price}
                                 for fill in accepted]
        directory = tmp_path / report["run_id"]
        raw_file(directory / "raw.log")
        summary = write_artifacts(report, "COMPLETED", directory)
        assert summary["first_leg"]["qty"] == 5
        assert summary["economics"]["paired_qty"] == 5
    finally:
        store.close()
