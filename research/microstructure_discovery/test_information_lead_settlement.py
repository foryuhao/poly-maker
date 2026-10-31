import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from information_lead_settlement import CTF, terminal_value


def fixture():
    decision = dict(
        id="case",
        event="game",
        market="0x123",
        ticker="k",
        asset="n",
        pair=dict(poly_yes="y", poly_no="n"),
        plan=dict(poly_budget=7.13, kalshi_cost_bound=2.32, reserve=0.02, kalshi_side="yes"),
    )
    result = dict(status="PAIRED_PENDING_SETTLEMENT", token_floor=10.002)
    evidence = dict(
        chain=dict(chain_id=137, contract=CTF, condition="0x123", denominator=1, numerators=[0, 1]),
        resolution=dict(data=[dict(condition_id="0x123", status="resolved", payouts=[0, 1000000])]),
        kalshi=dict(market=dict(ticker="k", status="finalized", result="no")),
    )
    return decision, result, evidence


def test_correct_orientation_and_terminal_cap_cash_costs():
    d, r, e = fixture()
    out = terminal_value(d, r, e)
    assert out["terminal"] and out["complementary_payouts"]
    assert abs(out["net_lower_bound"] - 0.532) < 1e-10
    d["asset"] = "y"
    out = terminal_value(d, r, e)
    assert out["terminal"] and not out["complementary_payouts"]
    assert abs(out["net_lower_bound"] + 9.47) < 1e-10


def test_pending_or_bad_source_not_excluded_or_credited_as_profit():
    d, r, e = fixture()
    for bad in [{}, dict(e, chain=dict(e["chain"], chain_id=1))]:
        out = terminal_value(d, r, bad)
        assert not out["terminal"] and out["net_lower_bound"] == -9.47
    assert terminal_value(d, None, {})["net_lower_bound"] == -9.47
    e["kalshi"]["market"]["result"] = "scalar"
    assert not terminal_value(d, r, e)["terminal"]


def test_existing_leg_losses_and_zero_fills_retained():
    d, _, _ = fixture()
    for status, value in [("P_ONLY_ZERO_RECOVERY_BOUND", -7.15), ("NO_FILLS", 0)]:
        out = terminal_value(d, dict(status=status, net_lower_bound=value), {})
        assert out["terminal"] and out["net_lower_bound"] == value
