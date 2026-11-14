"""Price sensitivity must replay admission on a common settled cohort."""

import importlib.util
from decimal import Decimal as D
from pathlib import Path


def analysis():
    path = Path(__file__).resolve().parents[1]/"ops/m1_signal_hold_analysis.py"
    spec = importlib.util.spec_from_file_location("m1_price_analysis", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analyze


def row(signals):
    return {"phase": "EVALUATION", "epoch": 300, "complete_window": True, "audit_valid": True,
            "paper": {"signals": signals}, "settlement": {"resolution": {
                "resolution_status": "resolved", "closed": True, "winner": "YES"}}}


def test_filter_runs_before_budget_and_can_admit_a_previously_skipped_signal():
    signals = [{"episode_id": f"low-{i}", "price": ".02", "size": "250", "outcome": "YES"} for i in range(4)]
    signals.append({"episode_id": "eligible", "price": ".5", "size": "10", "outcome": "YES"})
    state = {"sessions": [row(signals)], "evaluation_target": 300}
    calculate = analysis()
    original = calculate(state, {"orders": []})
    filtered = calculate(state, {"orders": []}, min_price=D(".1"), max_price=D(".9"))
    assert original["summary"]["budget_skipped"] == 1 and original["summary"]["signals"] == 4
    assert filtered["summary"]["signals"] == 1 and filtered["summary"]["pnl"] == "5.0"
    assert filtered["trades"][0]["episode_id"] == "eligible"
    assert filtered["price_filter"]["excluded_settled_signals"] == 4


def test_both_price_boundaries_are_inclusive():
    signals = [{"episode_id": str(i), "price": p, "size": q, "outcome": "YES"} for i, (p, q) in enumerate([
        (".099", "50"), (".10", "50"), (".90", "5.55"), (".901", "5")])]
    result = analysis()({"sessions": [row(signals)], "evaluation_target": 300}, {"orders": []},
                        min_price=D(".10"), max_price=D(".90"))
    assert [t["price"] for t in result["trades"]] == ["0.10", "0.90"]
    assert D(result["summary"]["cost"]) == D("9.995")


def test_pending_and_invalid_markets_remain_missing_in_both_scenarios():
    pending = row([])
    pending["settlement"] = {}
    incomplete = {**row([]), "epoch": 600, "complete_window": False}
    state = {"sessions": [pending, incomplete], "evaluation_target": 300}
    calculate = analysis()
    for result in (calculate(state, {"orders": []}), calculate(state, {"orders": []}, min_price=D(".1"), max_price=D(".9"))):
        assert result["summary"]["markets"] == 0
        assert result["summary"]["pending"] == result["summary"]["incomplete"] == 1
        assert all(r["pnl"] is None for r in result["markets"])
