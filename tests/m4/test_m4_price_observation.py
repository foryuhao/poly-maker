"""Prospective cohort accounting and persistent milestone delivery safeguards."""
import hashlib
import json
import sqlite3
from copy import deepcopy

import pytest

from polymaker import m4_price_observation as observation
from polymaker.m4_control import atomic_json

EPOCH = 1800000000


def fixture():
    plan = {"id": "test-forward", "enabled": True, "started_at": EPOCH + 20,
            "started_at_beijing": "2027-01-15T16:00:20+08:00", "minimum_inclusive": ".90",
            "maximum_exclusive": ".95", "reminder_samples": 100, "evaluation_samples": 200,
            "sample_definition": "prospective signed-limit signals",
            "milestones": {"100": {"status": "WAITING"}, "200": {"status": "WAITING"}}}
    expected = {"slug": f"btc-updown-5m-{EPOCH}", "condition_id": "0xcondition",
                "token_ids": {"YES": "up", "NO": "down"}}
    trade = {"status": "CONFIRMED", "our_side": "BUY", "maker_order_id": "order",
             "token_id": "up", "market_id": "0xcondition", "trade_id": "trade",
             "size": "5", "price": ".88", "principal_usdc": "4.40", "fee_usdc": ".02"}
    order = {"task_id": "signal", "order_id": "order", "token_id": "up", "quantity": "5",
             "principal_usdc": "4.40", "fee_usdc": ".02", "all_in_usdc": "4.42",
             "proof": {"kind": "OWNED_TERMINAL_ORDER_AND_TRADES",
                       "order": {"id": "order", "asset_id": "up", "price": ".90", "side": "BUY"},
                       "trades": [trade]}}
    market = {"epoch": EPOCH, "expected": expected, "confirmed_orders": [order]}
    snapshot = {"read_mode": "mode=ro,query_only,BEGIN", "production_ledger_writes": False,
                "account_trade_or_position_queries": [], "service_actions": [], "order_actions": [],
                "read_completed_at": EPOCH + 600, "markets": [market]}
    raw = {"slug": expected["slug"], "conditionId": expected["condition_id"],
           "clobTokenIds": ["up", "down"], "closed": True,
           "resolutionStatus": "resolved", "outcomePrices": ["1", "0"]}
    official = {"markets": [{"epoch": EPOCH, "raw": raw, "error": None, "observed_at": EPOCH + 610}]}
    timing = {(EPOCH, "signal"): {"wall": EPOCH + 25}}
    return snapshot, official, plan, timing


def copy_order(order, suffix, *, task=None, price=None):
    result = deepcopy(order)
    result["order_id"] += suffix
    result["proof"]["order"]["id"] = result["order_id"]
    result["proof"]["trades"][0]["maker_order_id"] = result["order_id"]
    result["proof"]["trades"][0]["trade_id"] += suffix
    if task:
        result["task_id"] = task
    if price:
        result["proof"]["order"]["price"] = price
    return result


def test_same_signal_topups_merge_and_price_improvement_is_included():
    snapshot, official, plan, timing = fixture()
    order = snapshot["markets"][0]["confirmed_orders"][0]
    snapshot["markets"][0]["confirmed_orders"] += [copy_order(order, "topup", price=".94"),
                                                  copy_order(order, "below", price=".89"),
                                                  copy_order(order, "ceiling", price=".95")]
    result = observation.build_observation(snapshot, official, plan, timing)
    assert result["summary"]["effective_samples"] == 1
    assert result["summary"]["resolved_all_in_usdc"] == "8.84"
    assert result["summary"]["resolved_pnl_usdc"] == "1.16"
    assert result["signals"][0]["order_ids"] == ["order", "ordertopup"]


def test_pre_registration_signal_with_late_fill_is_never_added():
    snapshot, official, plan, timing = fixture()
    timing[(EPOCH, "signal")]["wall"] = plan["started_at"] - .001
    assert observation.build_observation(snapshot, official, plan, timing)["summary"]["effective_samples"] == 0
    timing[(EPOCH, "signal")]["wall"] = plan["started_at"]
    assert observation.build_observation(snapshot, official, plan, timing)["summary"]["effective_samples"] == 1


@pytest.mark.parametrize("status", ["ACK", "UNKNOWN", "SETTLING", "MATCHED"])
def test_nonfinal_trades_cannot_count_as_samples(status):
    snapshot, official, plan, timing = fixture()
    snapshot["markets"][0]["confirmed_orders"][0]["proof"]["trades"][0]["status"] = status
    with pytest.raises(ValueError, match="FINAL_CONFIRMED"):
        observation.build_observation(snapshot, official, plan, timing)


def test_pending_result_keeps_cost_and_verified_cached_final_can_be_reused():
    snapshot, official, plan, timing = fixture()
    previous = deepcopy(official["markets"][0])
    official["markets"][0] = {"epoch": EPOCH, "raw": None, "error": "ReadTimeout"}
    pending = observation.build_observation(snapshot, official, plan, timing)
    assert pending["summary"]["effective_samples"] == 0
    assert pending["summary"]["pending_resolution_signals"] == 1
    assert pending["summary"]["pending_confirmed_cost_usdc"] == "4.42"
    assert pending["signals"][0]["pnl_usdc"] is None
    cached = observation.build_observation(snapshot, official, plan, timing,
                                          cached_official={str(EPOCH): previous})
    assert cached["summary"]["effective_samples"] == 1
    assert cached["cached_final_result_epochs"] == [EPOCH]


@pytest.mark.parametrize("field,value", [("closed", False), ("resolutionStatus", "disputed"),
                                        ("conditionId", "wrong"), ("outcomePrices", [".99", ".01"])])
def test_official_final_identity_and_binary_payout_required(field, value):
    snapshot, official, plan, timing = fixture()
    official["markets"][0]["raw"][field] = value
    result = observation.build_observation(snapshot, official, plan, timing)
    assert result["summary"]["effective_samples"] == 0
    assert result["summary"]["pending_confirmed_cost_usdc"] == "4.42"


def test_missing_signal_clock_is_visible_and_does_not_create_a_sample():
    snapshot, official, plan, _ = fixture()
    result = observation.build_observation(snapshot, official, plan, {},
                                          timing_gaps=[{"epoch": EPOCH, "reason": "SIGNAL_CLOCK_UNAVAILABLE"}])
    assert result["status"] == "PARTIAL_REVIEW_REQUIRED"
    assert result["summary"]["effective_samples"] == 0


def test_duplicate_orders_and_cost_mismatch_fail_closed():
    snapshot, official, plan, timing = fixture()
    snapshot["markets"][0]["confirmed_orders"] *= 2
    with pytest.raises(ValueError, match="ORDER_IDENTITY"):
        observation.build_observation(snapshot, official, plan, timing)
    snapshot, official, plan, timing = fixture()
    snapshot["markets"][0]["confirmed_orders"][0]["all_in_usdc"] = "4.41"
    with pytest.raises(ValueError, match="FILL_COST"):
        observation.build_observation(snapshot, official, plan, timing)


def test_100_and_200_due_do_not_mean_user_was_notified(tmp_path, monkeypatch):
    snapshot, official, plan, timing = fixture()
    order = snapshot["markets"][0]["confirmed_orders"][0]
    for i in range(1, 200):
        task = f"signal{i}"
        snapshot["markets"][0]["confirmed_orders"].append(copy_order(order, str(i), task=task))
        timing[(EPOCH, task)] = {"wall": EPOCH + 25 + i / 1000}
    root = tmp_path / "research_outputs"
    state_path = root / "m4_taker_watch/agent-state.json"
    pnl_dir = root / "review/pnl"
    atomic_json(state_path, {observation.STATE_KEY: plan, "reviewed_through_epoch": EPOCH - 300,
                             "pending_market_epochs": [123], "unrelated": "preserve"})
    atomic_json(pnl_dir / "snapshot.json", snapshot)
    atomic_json(pnl_dir / "official-resolutions.json", official)
    monkeypatch.setattr(observation, "read_signal_times", lambda *args: (timing, []))
    result = observation.run_observation(tmp_path, pnl_dir)
    assert result["milestones_due"] == [100, 200]
    saved = json.loads(state_path.read_text())
    assert saved["pending_market_epochs"] == [123] and saved["unrelated"] == "preserve"
    assert saved["reviewed_through_epoch"] == EPOCH - 300
    assert saved[observation.STATE_KEY]["milestones"]["100"]["status"] == "DUE"
    assert "notified_at" not in saved[observation.STATE_KEY]["milestones"]["100"]
    saved[observation.STATE_KEY]["milestones"]["100"].update(status="NOTIFIED", notified_at=EPOCH + 650)
    atomic_json(state_path, saved)
    assert observation.run_observation(tmp_path, pnl_dir)["milestones_due"] == [200]
    saved = json.loads(state_path.read_text())
    saved[observation.STATE_KEY]["milestones"]["200"].update(status="EVALUATED", evaluated_at=EPOCH + 660)
    atomic_json(state_path, saved)
    assert observation.run_observation(tmp_path, pnl_dir)["milestones_due"] == []
    assert observation.milestone_due(plan, 99) == []
    assert observation.milestone_due(plan, 100) == [100]


def test_signal_clock_and_signed_limit_read_is_local_and_read_only(tmp_path):
    snapshot, _, plan, _ = fixture()
    market = snapshot["markets"][0]
    root = tmp_path / "research_outputs/m4_live"
    ledger = root / "ledgers" / (market["expected"]["slug"] + ".sqlite3")
    ledger.parent.mkdir(parents=True)
    market["ledger_path"] = str(ledger)
    meta = {"identity": {"mode": "LIVE", "funder": "0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d"},
            "settings": {"execution_revision": "m4-taker-budget-v1"}, "session_id": "test",
            "market": {"slug": market["expected"]["slug"], "condition_id": "0xcondition"}}
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        db.executemany("INSERT INTO metadata VALUES (?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
        db.execute("CREATE TABLE taker_attempts(order_id TEXT,task_id TEXT,started INTEGER,state TEXT,quote TEXT,filled TEXT,spent TEXT)")
        db.execute("INSERT INTO taker_attempts VALUES (?,?,?,?,?,?,?)",
                   ("order", "signal", 1, "TERMINAL", json.dumps({"price": ".90"}), "5", "4.42"))
    journal = root / "journal/session_test.jsonl"
    journal.parent.mkdir()
    journal.write_text(json.dumps({"ts": EPOCH + 25, "kind": "sm_m4_signal", "data": {"payload": {
        "event": "OPPORTUNITY_IDENTIFIED", "opportunity": {"episode_id": "signal"}}}}) + "\n" + '{"torn":')
    before = hashlib.sha256(ledger.read_bytes()).hexdigest()
    timing, gaps = observation.read_signal_times(tmp_path, snapshot, plan)
    assert timing[(EPOCH, "signal")]["wall"] == EPOCH + 25 and gaps == []
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before
