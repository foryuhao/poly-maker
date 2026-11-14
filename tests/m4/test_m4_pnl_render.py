"""Offline reports for valid-touch-only fills and historical gap diagnostics."""
import csv
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLOT_PYTHON = ROOT / ".venv-m4-report/bin/python"
FONT = ROOT / "research_outputs/shared_assets/NotoSansCJKsc-Regular.otf"
pytestmark = pytest.mark.skipif(
    not PLOT_PYTHON.exists() or not FONT.exists(),
    reason="The isolated M4 report runtime and CJK font are required.",
)
FIRST = 1794279600


def market(epoch, states, *, winner="YES", complete=True):
    posts, observations, fills = [], [], []
    for index, state in enumerate(states):
        order_id = f"{epoch}-{index}"
        quote = {"outcome": "YES" if index == 0 else "NO", "price": 0.5, "size": 10}
        posts.append({"order_id": order_id, "quote": quote,
                      "anchor_price": "0.5", "tick_at_post": "0.01", "target_price": "0.48"})
        if state is not None:
            observations.append({"order_id": order_id, "status": state})
        if state == "FILLED":
            fills.append({"order_id": order_id, "quote": quote})
    return {"epoch": epoch, "metadata": {"complete": complete, "market": {"min_order_size": 5}},
            "facts": {"virtual_post": posts, "virtual_observation": observations, "virtual_fill": fills},
            "resolution": {"winner": winner} if winner else None}


def render(tmp_path, records, snapshot_at):
    snapshot = {"first_epoch": FIRST, "snapshot_at": snapshot_at,
                "snapshot_at_bj": "2026-11-10T12:00:00+08:00", "records": records}
    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(snapshot))
    original = source.read_bytes()
    subprocess.run([str(PLOT_PYTHON), "-B", str(ROOT / "ops/m4_pnl_render.py"),
                    "--project-dir", str(ROOT), "--directory", str(tmp_path)],
                   check=True, capture_output=True, text=True, timeout=60)
    assert source.read_bytes() == original
    assert (tmp_path / "m4-pnl-waterfall-dual-cumulative-drawdown.png").stat().st_size > 0
    rows = list(csv.DictReader((tmp_path / "market-pnl-coverage.csv").open(encoding="utf-8-sig")))
    return json.loads((tmp_path / "summary.json").read_text()), rows


def test_all_terminal_posts_are_compared_and_legacy_unknown_has_zero_new_pnl(tmp_path):
    records = [market(FIRST, ["FILLED", "UNKNOWN"]),
               market(FIRST + 300, ["NOT_TOUCHED", "UNKNOWN"], winner="NO"),
               market(FIRST + 600, ["UNKNOWN"]), market(FIRST + 900, []),
               market(FIRST + 1200, ["FILLED"], complete=False),
               market(FIRST + 1500, ["FILLED", "UNKNOWN"], winner=None),
               market(FIRST + 1800, ["FILLED", "UNKNOWN"], winner=None)]
    # Keep the old saved comparison immutable while applying the new report policy.
    records[0]["metadata"]["modeled_settlement"] = {
        "modeled_pnl_usdc": "5", "modeled_receipts": 1,
        "instant_fill_comparison": {"modeled_pnl_usdc": "0", "comparable_pnl_usdc": "5"}}
    summary, rows = render(tmp_path, records, FIRST + 1850)
    assert summary["comparison_grain"] == "POST"
    assert summary["partial_settled_markets"] == 0
    assert summary["observations"]["UNKNOWN"] == 0
    assert summary["observations"]["LEGACY_UNKNOWN"] == 5
    assert summary["metrics"]["new"]["pnl_usdc"] == "5.0"
    assert summary["metrics"]["old"]["pnl_usdc"] == "5.0"
    assert summary["metrics"]["new"]["settled_trades"] == 1
    assert summary["metrics"]["old"]["settled_trades"] == 5
    assert summary["metrics"]["old"]["maximum_drawdown_usdc"] == "0"
    assert summary["metrics"]["new"]["pending_settlement_trades"] == 2
    assert summary["metrics"]["old"]["pending_settlement_trades"] == 4
    assert summary["new_settled_zero_trade_markets"] == 3
    assert summary["old_settled_zero_trade_markets"] == 1
    assert [r["coverage"] for r in rows] == ["COMPLETE_SETTLED", "COMPLETE_SETTLED", "COMPLETE_SETTLED",
                                             "COMPLETE_SETTLED", "INCOMPLETE", "UNSETTLED", "OPEN"]
    assert rows[0]["comparable_posts"] == rows[1]["comparable_posts"] == "2"
    assert rows[0]["new_pnl_usdc"] == "5.0"
    assert rows[0]["old_pnl_usdc"] == "0.0"
    assert rows[1]["new_pnl_usdc"] == "0"
    assert rows[1]["old_pnl_usdc"] == "0.0"
    assert rows[2]["new_pnl_usdc"] == "0"
    assert rows[2]["old_pnl_usdc"] == "5.0"
    assert rows[4]["new_pnl_usdc"] == rows[4]["old_pnl_usdc"] == ""


def test_legacy_unknown_is_no_fill_but_missing_market_remains_missing(tmp_path):
    records = [market(FIRST, ["UNKNOWN"]),
               {"epoch": FIRST + 300, "metadata": {}, "facts": {}, "resolution": None}]
    summary, rows = render(tmp_path, records, FIRST + 900)
    for model in ["new", "old"]:
        assert summary["metrics"][model]["maximum_drawdown_usdc"] == "0"
    assert summary["metrics"]["new"]["pnl_usdc"] == "0"
    assert summary["metrics"]["new"]["settled_trades"] == 0
    assert summary["metrics"]["old"]["pnl_usdc"] == "5.0"
    assert summary["metrics"]["old"]["settled_trades"] == 1
    assert summary["partial_settled_markets"] == 0
    assert [r["coverage"] for r in rows] == ["COMPLETE_SETTLED", "MISSING"]


def test_pending_observation_waits_for_terminal_status(tmp_path):
    summary, rows = render(tmp_path, [market(FIRST, [None])], FIRST + 900)
    assert rows[0]["coverage"] == "PENDING_OBSERVATION"
    assert rows[0]["new_pnl_usdc"] == rows[0]["old_pnl_usdc"] == ""
    assert summary["observations"]["PENDING"] == 1


@pytest.mark.parametrize('touch_elapsed,new_pnl', [(60,'5.0'), (240,'5.0'), (240.001,'0')])
def test_historical_touch_replay_respects_t240_and_preserves_instant_curve(tmp_path,touch_elapsed,new_pnl):
    record=market(FIRST,["FILLED"])
    record['metadata']['settings']={'cutoff_sec':240}
    post=record['facts']['virtual_post'][0]
    post.update(model='BEST_ASK_MINUS_2TICK_WITHIN_10S_V1',wall=FIRST+20)
    record['facts']['virtual_fill'][0].update(
        model=post['model'],touch={'received_wall':FIRST+touch_elapsed,'best_ask':'0.48'})
    record['metadata']['modeled_settlement']={
        'modeled_pnl_usdc':'5.0','modeled_receipts':1,
        'instant_fill_comparison':{'modeled_pnl_usdc':'5.0','comparable_pnl_usdc':'5.0'}}
    summary,rows=render(tmp_path,[record],FIRST+900)
    assert summary['metrics']['new']['pnl_usdc']==new_pnl
    assert summary['metrics']['old']['pnl_usdc']=='5.0'
    assert summary['observations']['HISTORICAL_REPLAY_POSTS']==1
    assert rows[0]['coverage']=='COMPLETE_SETTLED'
