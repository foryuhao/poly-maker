"""Per-signal PnL uses one strategy's captured evidence and exact final result."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "ops/m4_taker_pnl.py"
EPOCH = 1800000000


def test_replay_keeps_strategy_and_market_accounting_separate(tmp_path):
    for strategy, token in (("M4", "up"), ("M6", "down")):
        source = tmp_path / f"saved-{strategy}"
        source.mkdir()
        expected = {"token_ids": {"YES": "up", "NO": "down"}}
        orders = [{"task_id": "shared-task-id", "order_id": f"order-{i}", "token_id": token,
                   "quantity": "1.5", "principal_usdc": ".50", "fee_usdc": ".01",
                   "all_in_usdc": ".51"} for i in (1, 2)]
        markets = [
            {"epoch": EPOCH, "label": "first", "expected": expected, "confirmed_orders": orders},
            {"epoch": EPOCH+300, "label": "second", "expected": expected, "confirmed_orders": orders[:1]},
        ]
        results = [
            {"epoch": EPOCH, "url": "saved", "observed_at": EPOCH+300, "error": None,
             "resolution": {"token_ids": expected["token_ids"], "winner": "YES"}},
            {"epoch": EPOCH+300, "url": "saved", "observed_at": EPOCH+310, "error": None,
             "resolution": None},
        ]
        snapshot = {"strategy": strategy, "markets": markets, "owned_ledgers_scanned": 2,
                    "read_completed_at": EPOCH+310, "beijing_time": "2027-01-15T00:05:10+08:00"}
        (source/"snapshot.json").write_text(json.dumps(snapshot))
        (source/"official-resolutions.json").write_text(json.dumps({"strategy": strategy, "markets": results}))
        output = tmp_path/"research_outputs"/strategy
        subprocess.run([sys.executable, str(SCRIPT), "--project-dir", str(tmp_path), "--strategy", strategy,
                        "--replay-dir", str(source), "--output-dir", str(output)],
                       check=True, capture_output=True, text=True)
        report = json.loads((output/"analysis.json").read_text())
        summary = report["summary"]
        net = "1.98" if strategy == "M4" else "-1.02"
        assert summary["strategy"] == strategy and summary["resolved_net_pnl_usdc"] == net
        assert summary["confirmed_filled_signals"] == 2 and summary["pending_cost_usdc"] == "0.51"
        [resolved] = report["resolved_signals"]
        [pending] = report["pending_resolution_signals"]
        assert resolved["pnl_usdc"] == net and resolved["order_ids"] == ["order-1", "order-2"]
        assert pending["pnl_usdc"] is None and pending["task_id"] == resolved["task_id"]

        subprocess.run([sys.executable, str(SCRIPT), "--project-dir", str(tmp_path), "--strategy", strategy,
                        "--replay-dir", str(source), "--output-dir", str(output),
                        "--market-window-start", str(EPOCH+300), "--market-window-end", str(EPOCH+3900)],
                       check=True, capture_output=True, text=True)
        scoped = json.loads((output/"analysis.json").read_text())
        assert scoped["summary"]["market_window"] == {"window_start": EPOCH+300, "window_end": EPOCH+3900}
        assert scoped["summary"]["pnl_status"] == "AWAITING_RESULTS"
        assert scoped["summary"]["pending_cost_usdc"] == "0.51"
        assert not scoped["resolved_signals"]
        assert [row["epoch"] for row in scoped["pending_resolution_signals"]] == [EPOCH+300]
