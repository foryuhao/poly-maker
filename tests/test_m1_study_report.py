"""Future study reporting must not manufacture profits from missing data."""

import importlib.util
from pathlib import Path


def reporter():
    path = Path(__file__).resolve().parents[1]/"ops/m1_study_report.py"
    spec = importlib.util.spec_from_file_location("study_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.report


def state(rows, status="COMPLETE"):
    return {"sessions": rows, "status": status, "phase": "EVALUATION", "evaluation_target": 2,
            "frozen_config_sha256": "fixed"}


def row(epoch, pnl="1", fills=1):
    return {"phase": "EVALUATION", "epoch": epoch, "signals": 1, "config_sha256": "fixed",
            "complete_window": True, "audit_valid": True,
            "paper": {"post_attempts": 1, "filled_orders": fills, "uncertain_orders": 0, "spent_usdc": "5"},
            "settlement": {"status": "RESOLVED", "pnl_lower_usdc": pnl,
                           "pnl_upper_usdc": pnl, "pnl_usdc": pnl}}


def test_complete_report_counts_only_holdout_and_computes_pnl():
    r1, r2 = row(1, "-2"), row(2, "3")
    calibration = {**row(0, "100"), "phase": "CALIBRATION"}
    text = reporter()(state([calibration, r1, r2]))
    assert "累计模型盈亏：1 USDC" in text and "最大回撤：2 USDC" in text
    assert "每场平均：0.5 USDC" in text


def test_pending_or_missing_market_cannot_be_counted_as_zero_profit():
    r = row(1)
    r["complete_window"] = False
    text = reporter()(state([r, row(2)]))
    assert "不能给出总体收益结论" in text and "收益为正" not in text
    text = reporter()(state([row(1)], "RUNNING"))
    assert "尚未完成固定参数评估" in text and "累计模型盈亏" not in text


def test_no_estimated_fills_is_not_positive_evidence():
    text = reporter()(state([row(1, "0", 0), row(2, "0", 0)]))
    assert "尚无明确的模型成交" in text and "收益为正" not in text
