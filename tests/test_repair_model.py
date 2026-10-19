"""Financial decisions, joint fill adverse selection, and session constraints."""
from dataclasses import replace

import pytest

from polymaker.config import RDPRepairConfig
from polymaker.repair_model import (
    OrderValuer,
    RepairOrder,
    RepairPlanner,
    RepairState,
    quantity_candidates,
)


def state(p=.31, **kw):
    s = RepairState(p, .02, 100, 5, 0, 1.5, 13.5, round(1-p-.01, 3),
                    round(1-p+.01, 3), .01, original_side="YES")
    return replace(s, **kw)


def test_reference_version_new_quote_and_joint_pnl():
    cfg = RDPRepairConfig(model_version="bounded_martingale_v1", solve_timeout_sec=30)
    result = RepairPlanner(cfg).decide(state())
    assert (result["action"], result["side"], result["price"], result["quantity"], result["cancel_at"]) == (
        "BUY", "NO", .68, 5, 240)
    assert result["expected_pnl"] == pytest.approx(.06870925786906, abs=1e-10)
    assert 0 < result["incremental_ev"] < .05  # adverse selection consumes most displayed edge
    expensive = RepairPlanner(cfg).decide(state(cost=3.75))
    assert (expensive["action"], expensive["quantity"], expensive["cancel_at"]) == (
        result["action"], result["quantity"], result["cancel_at"])
    assert expensive["expected_pnl"] == pytest.approx(result["expected_pnl"]-2.25)


@pytest.mark.parametrize("p,action,pnl", [(.25, "KEEP", -.12625358914915),
    (.30, "KEEP", .05747329760423), (.35, "CANCEL", .1)])
def test_live_order_scenarios_with_common_next_review(p, action, pnl):
    result = RepairPlanner(RDPRepairConfig(solve_timeout_sec=30)).decide(
        state(p, elapsed=102, cash=10.1), RepairOrder("NO", .68, 5, 1.8, 240))
    assert result["action"] == action
    assert result["expected_pnl"] == pytest.approx(pnl, abs=1e-10)
    assert result["parameter_status"] == "UNCALIBRATED"
    cancel = next(r for r in result["candidates"] if r["action"] == "CANCEL")
    assert cancel["continuation_gain"] >= 0
    assert result["baseline_pnl"] == cancel["expected_pnl"]
    assert cancel["settlement_sd"] >= 0


def test_hold_has_observable_future_decision_not_permanent_settlement():
    s = state()
    cfg = RDPRepairConfig(solve_timeout_sec=30)
    r = RepairPlanner(cfg).decide(s)
    hold = next(c for c in r["candidates"] if c["action"] == "HOLD")
    assert hold["continuation_gain"] > 0
    assert hold["expected_pnl"] > s.mark
    assert r["valuation_scope"] == "NEXT_REVIEW_HONOUR_LEASE_ELSE_OPTIMIZE_ONCE"
    assert r["advantage"] == pytest.approx(r["expected_pnl"]-hold["expected_pnl"])


def test_minimum_notional_reverse_residue_and_small_original_fill():
    cfg = RDPRepairConfig()
    assert quantity_candidates(state(.85, bid=.14, ask=.16), cfg) == (7.15,)
    assert quantity_candidates(state(.85, bid=.15, ask=.17), cfg) == (6.67,)
    assert quantity_candidates(state(yes=2), cfg) == (5.,)
    assert not quantity_candidates(state(.95, bid=.05, ask=.06), cfg)  # would exceed |Delta|=5
    assert not quantity_candidates(state(cash=3.39), cfg)
    r = RepairPlanner(cfg).decide(state(yes=5, no=7.15, bid=.84, ask=.86))
    assert r["action"] == "HOLD" and r["reason"] == "ACCEPT_REVERSE_RESIDUE"


def test_recovery_uses_negative_ev_only_for_actual_limit_violation():
    cfg = RDPRepairConfig(model_version="bounded_martingale_v1", liquidity_rate=.5,
                           solve_timeout_sec=30)
    normal = RepairPlanner(cfg).decide(state(yes=5))
    assert normal["action"] == "HOLD"
    over = RepairPlanner(cfg).decide(state(yes=5.02))
    assert over["action"] == "BUY" and over["mode"] == "LIMIT_RECOVERY"
    assert over["incremental_ev"] < 0
    assert abs(over["delta"]-over["quantity"]) <= 5
    no_cash = RepairPlanner(cfg).decide(state(yes=5.02, cash=0))
    assert no_cash["reason"] == "LIMIT_RECOVERY_UNRESOLVED"
    partial = RepairPlanner(cfg).decide(state(yes=12, cash=3.4))
    assert partial["action"] == "BUY" and partial["quantity"] == 5
    assert partial["delta"]-partial["quantity"] > 5


def test_cutoff_and_zero_ev_do_not_create_new_submissions():
    cfg = RDPRepairConfig()
    planner = RepairPlanner(cfg)
    assert planner.decide(state(elapsed=240))["action"] == "HOLD"
    assert planner.decide(state(elapsed=240), RepairOrder("NO", .68, 5, 2, 240))["action"] == "CANCEL"
    assert planner.decide(state(yes=5, no=5))["reason"] == "PAIRED"


def test_joint_probability_payoff_identity_half_tick_and_fractional_fill():
    cfg = RDPRepairConfig(solve_timeout_sec=30)
    s = state(.315, yes=5.1, bid=.68, ask=.69, tick=.001)
    row = OrderValuer(cfg).profiles(s, "NO", .68, (5.1,), (3,), export=True)[0]
    assert sum(w for w, _, _ in row["states"])+row["reject_probability"] == pytest.approx(1)
    # For existing orders (no post-only rejection), terminal conditional payoff
    # independently agrees with accumulated fill-time fair-value improvement.
    row = OrderValuer(cfg).profiles(s, "NO", .68, (5.1,), (3,), existing=True, export=True)[0]
    pnl = sum(w*(p*s.yes+(1-p)*(s.no+x)-s.cost-.68*x) for w, p, x in row["states"])
    assert pnl == pytest.approx(row["expected_pnl"], abs=1e-10)
    assert row["expected_filled"] <= 5.1


def test_cpu_budget_returns_no_partial_or_expired_solution():
    planner = RepairPlanner(RDPRepairConfig(solve_timeout_sec=.000001))
    with pytest.raises(TimeoutError, match="REPAIR_SOLVE_TIMEOUT"):
        planner.decide(state())
