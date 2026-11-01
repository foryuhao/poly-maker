"""Calibration and evaluation populations stay chronological and separate."""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from polymaker.dashboard.settlement import validate_resolution
from polymaker.rdp_sm_m1_study import (
    GRID,
    choose_profile,
    frequency_streak,
    load_verifier,
    run_study,
    summarize_study,
)

PROJECT = Path(__file__).resolve().parents[1]


def complete(signals, index=0):
    return {"signals": signals, "profile_index": index, "complete_window": True, "audit_valid": True}


def test_calibration_cannot_hide_zero_markets_or_mix_profiles_for_streak():
    rows = [complete(1), complete(2), complete(0), complete(1), complete(2)]
    assert frequency_streak(rows, 0) == 2
    assert frequency_streak([complete(1, 1), *[complete(1)]*4], 0) == 4
    assert choose_profile([complete(0)]*5, 0) == 1
    # PnL is never an input into profile selection.
    assert choose_profile([{**complete(0), "settlement": {"pnl_usdc": "100000"}}]*5, 0) == 1


def test_unavailable_market_is_not_reported_as_zero_signal_or_zero_pnl():
    state = {"status": "FAILED", "phase": "EVALUATION", "study_dir": "example", "evaluation_target": 50,
             "sessions": [{"phase": "EVALUATION", "error": "NO_DATA"}]}
    group = summarize_study(state)["groups"]["EVALUATION"]
    assert group["markets"] == group["unavailable_markets"] == 1
    assert group["signal_distribution"]["0"] == 0
    assert group["pnl_lower_usdc"] is None and group["pnl_upper_usdc"] is None


async def test_frozen_evaluation_cannot_be_upgraded_even_with_explicit_calibration_flag(tmp_path):
    directory = tmp_path/"frozen"
    directory.mkdir()
    original = {"source_sha256": {}, "phase": "EVALUATION", "evaluation_target": 50, "sessions": []}
    (directory/"study.json").write_text(json.dumps(original))
    with pytest.raises(RuntimeError, match="RESUME_IDENTITY_MISMATCH"):
        await run_study(PROJECT/"config-m1", directory, resume=True, upgrade_calibration=True)
    assert json.loads((directory/"study.json").read_text()) == original


def test_raw_marker_hold_checker_ignores_transport_heartbeat(tmp_path):
    verifier = load_verifier()
    # Retrieve the independent helper from the loaded verifier's module globals.
    hold = verifier.__globals__["raw_hold_checks"]
    events = []
    for t, quantity in ((.01, 80), (.03, 100)):
        raw = {"event_type": "price_change", "price_changes": [
            {"asset_id": "token", "side": "BUY", "price": ".5", "size": str(quantity)}]}
        events.append({"event": "RAW_FRAME", "frame": len(events)+1,
                       "processed_monotonic": t, "received_monotonic": t, "raw": raw})
    events.append({"event": "RAW_FRAME", "frame": 3, "processed_monotonic": .2,
                   "received_monotonic": .2, "raw": "PONG"})
    path = tmp_path/"events.jsonl"
    path.write_text("".join(json.dumps(e)+"\n" for e in events))
    rounds = [{"round_id": "round", "initial": "100", "trough": "80", "evidence_start": 0, "evidence_end": .5}]
    assert hold(path, rounds, "token", verifier.__globals__["D"](".5"))[0]["passed"]


async def test_five_calibration_markets_precede_distinct_frozen_evaluation_and_official_payout(tmp_path, monkeypatch):
    import polymaker.rdp_sm_m1_study as module

    epoch = int(time.time()//300)*300-30000
    first = epoch
    counter = 0
    async def wait(last):
        nonlocal epoch
        epoch += 300
        return epoch
    async def collect(config, **kwargs):
        nonlocal counter
        counter += 1
        assert kwargs["paper_mode"] and kwargs["post_latency_ms"] == 400
        session = tmp_path/f"market-{counter}"
        session.mkdir()
        meta = {"condition_id": "0xabc", "tokens": [{"token_id": "yes"}, {"token_id": "no"}]}
        (session/"metadata.json").write_text(json.dumps({"market": meta}))
        return {"session_dir": str(session), "market_epoch": epoch, "slug": f"btc-updown-5m-{epoch}",
            "feed_start_elapsed": 2, "stop_reason": "M1_OBSERVATION_CUTOFF",
            "m1_observation": {"counts": {"WOULD_POST": 1}}, "writer": {"failure": None, "dropped": 0},
            "paper": {"positions": {"YES": "10", "NO": "0"}, "spent_usdc": "5",
                      "post_attempts": 1, "filled_orders": 1, "uncertain_orders": 0, "orders": []}}
    def resolution(session):
        raw = {"slug": session["slug"], "conditionId": "0xabc", "clobTokenIds": '["no","yes"]',
               "closed": True, "umaResolutionStatus": "resolved", "outcomePrices": '["0","1"]'}
        return validate_resolution(raw, session)
    monkeypatch.setattr(module, "wait_open_after", wait)
    monkeypatch.setattr(module, "observe", collect)
    monkeypatch.setattr(module, "fetch_resolution", resolution)
    monkeypatch.setattr(module, "load_verifier", lambda: lambda path: {"proofs": [{"passed": True}]})
    monkeypatch.setattr(module.shutil, "disk_usage", lambda path: SimpleNamespace(free=10*1024**3))
    state = await run_study(PROJECT/"config-m1", tmp_path/"study", evaluation_sessions=2)
    assert state["status"] == "COMPLETE"
    assert [r["phase"] for r in state["sessions"]] == ["CALIBRATION"]*5+["EVALUATION"]*2
    assert [r["epoch"] for r in state["sessions"]] == [first+300*i for i in range(1, 8)]
    assert len({r["config_sha256"] for r in state["sessions"]}) == 1
    summary = summarize_study(state)
    assert summary["groups"]["CALIBRATION"]["pnl_lower_usdc"] == "25"
    assert summary["groups"]["EVALUATION"]["pnl_lower_usdc"] == "10"
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "m1-paper-resolution"]
    assert state["frozen_profile"] == GRID[0]
