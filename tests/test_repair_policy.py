"""Causal path decisions, persistent intent and legal mode-specific pricing."""
from dataclasses import replace
from decimal import Decimal

import pytest

from polymaker.config import RDPRepairConfig, RDPRepairPathConfig, RDPSMConfig
from polymaker.repair_policy import PathPoint, RepairPolicy, repair_readiness


def config(**kw):
    return RDPRepairPathConfig(tp_profit_rate=".01", shock_window_sec=2, shock_z=1.6,
                              drawdown_per_share=".20", **kw)


def decide(policy, point, start=90):
    return policy.decide(point, original_side="YES", original_qty=5, first_fill_elapsed=start)


def test_confirmed_parameters_are_defaults_but_not_live_validation():
    cfg = RDPRepairConfig(policy="hd_tp_sl_v1")
    report = repair_readiness(cfg)
    assert not report["ready_for_live"] and report["parameters_complete"]
    assert cfg.path.tp_profit_rate == Decimal(".002")
    assert (cfg.path.shock_window_sec, cfg.path.shock_max_age_sec, cfg.path.shock_z) == (2, 3, 1.5)
    assert cfg.path.shock_min_ticks == 2 and cfg.path.drawdown_per_share == Decimal(".06")
    assert cfg.path.calibration_status == "PARAMETERS_CONFIRMED"
    assert "EXECUTION_REPLAY_NOT_VALIDATED" in report["reasons"]
    cfg.path.tp_profit_rate = None
    assert "MISSING_TP_PROFIT_RATE" in repair_readiness(cfg)["reasons"]
    cfg.path = config()
    assert repair_readiness(cfg)["parameters_complete"]
    assert not repair_readiness(cfg)["ready_for_live"]
    cfg.path.calibration_status = "REPLAY_VALIDATED"
    assert not repair_readiness(cfg)["ready_for_live"]
    cfg.path.validation_reference = "offline/replay-results"
    assert repair_readiness(cfg)["ready_for_live"]


def test_selecting_new_policy_cannot_silently_run_legacy_normal_controller():
    with pytest.raises(ValueError, match="requires normal_entry_mode=obi_v1"):
        RDPSMConfig(repair=RDPRepairConfig(policy="hd_tp_sl_v1"))
    cfg = RDPSMConfig(normal_entry_mode="obi_v1", repair=RDPRepairConfig(policy="hd_tp_sl_v1"))
    assert cfg.repair.policy == "hd_tp_sl_v1"


def test_default_hold_and_strict_profit_rate_then_no_return_from_tp():
    policy = RepairPolicy(config())
    p = PathPoint(100, .5, .02, 1, 5, .1)
    assert decide(policy, p)["mode"] == "HD"  # equality does not trigger
    assert decide(policy, replace(p, elapsed=101, pnl_per_share=.12))["mode"] == "TP"
    assert decide(policy, replace(p, elapsed=102, pnl_per_share=-.1))["mode"] == "TP"


def test_profitable_adverse_shock_uses_pre_event_sigma_not_inflated_sigma():
    policy = RepairPolicy(config())
    decide(policy, PathPoint(100, .675, .0187544767, 1, 5, .145))
    result = decide(policy, PathPoint(102.636608839, .625, .08, 1, 5, .095))
    assert result["mode"] == "SL" and result["reason"] == "ADVERSE_SHOCK"
    assert result["observations"]["short_z"] == pytest.approx(1.64188298)
    assert result["observations"]["pnl_per_share"] > 0
    restored = RepairPolicy.restore(policy.cfg, policy.payload())
    result = decide(restored, PathPoint(103, .9, .01, 1, 5, .37))
    assert result["mode"] == "SL"
    assert result["last_transition"]["reason"] == "ADVERSE_SHOCK"


def test_normal_fluctuation_does_not_trigger_sl_but_slow_drawdown_can():
    cfg = config()
    cfg.drawdown_per_share = Decimal(".04")
    policy = RepairPolicy(cfg)
    for i in range(5):
        result = decide(policy, PathPoint(100+2*i, .5-.011*i, .1, 1, 5, -.011*i))
        assert result["mode"] == ("SL" if i == 4 else "HD")
    assert result["reason"] == "CUMULATIVE_DRAWDOWN"


def test_price_features_do_not_use_sunk_cost_or_fill_created_pnl_jump():
    policy = RepairPolicy(config())
    result = decide(policy, PathPoint(100, .15, .02, 1, 5, -.6))
    assert result["mode"] == "HD" and result["observations"]["drawdown_per_share"] == 0
    result = decide(policy, PathPoint(101, .15, .02, 1, 3, -.1))
    assert result["observations"]["price_pnl_per_share"] == 0
    result = decide(policy, PathPoint(102, .14, .02, 1, 3, -.11))
    assert result["observations"]["drawdown_per_share"] == pytest.approx(.006)


@pytest.mark.parametrize("elapsed", [180, 180.01, 239.999])
def test_force_sl_needs_neither_sigma_nor_path_history(elapsed):
    policy = RepairPolicy(RDPRepairPathConfig())
    result = decide(policy, PathPoint(elapsed, .15, None, 9, 5, -.6))
    assert result["mode"] == "SL" and result["reason"] == "TIME_FORCE_SL"


def test_reconnect_never_compares_a_shock_across_generations():
    policy = RepairPolicy(config())
    decide(policy, PathPoint(100, .7, .001, 1, 5, .05))
    result = decide(policy, PathPoint(102, .2, None, 2, 5, -.33))
    assert result["mode"] == "HD"
    assert result["observations"]["short_z"] is None


@pytest.mark.parametrize("sigma,n", [(.01, 1), (.02, 2), (.021, 3), (0, 1)])
def test_tp_tick_distance_rounds_up_and_keeps_accepted_price(sigma, n):
    policy = RepairPolicy(config(), mode="TP")
    price = policy.price(bid=Decimal(".5"), ask=Decimal(".52"),
                         tick=Decimal(".01"), sigma=sigma)
    assert price == Decimal(".5")-n*Decimal(".01")
    policy.accepted_tp(price, Decimal(".01"))
    assert policy.price(bid=Decimal(".7"), ask=Decimal(".72"),
                        tick=Decimal(".01"), sigma=.08) == price
    assert policy.price(bid=Decimal(".7"), ask=Decimal(".72"),
                        tick=Decimal(".01"), sigma=None) == price


def test_tp_illegal_price_is_blocked_not_clamped_and_sl_enters_spread():
    policy = RepairPolicy(config(), mode="TP")
    with pytest.raises(ValueError, match="OUT_OF_RANGE"):
        policy.price(bid=Decimal(".01"), ask=Decimal(".02"), tick=Decimal(".01"), sigma=.03)
    policy.mode = "SL"
    assert policy.price(bid=Decimal(".60"), ask=Decimal(".65"),
                        tick=Decimal(".01"), sigma=None) == Decimal(".64")


def test_cutoff_does_not_request_new_trade_and_paired_dust_is_not_deleted():
    policy = RepairPolicy(config(), mode="SL")
    result = decide(policy, PathPoint(240, .5, .02, 1, 5, -.3))
    assert result["reason"] == "CUTOFF"
    result = decide(policy, PathPoint(241, .5, .02, 1, .02, -.3))
    assert result["observations"]["delta"] == .02


@pytest.mark.parametrize("sigma,adverse,expected", [
    (.00001, .01, "HD"), (0, .01, "HD"), (0, .02, "SL"),
    (None, .05, "HD"), (.02, .04, "HD"), (.02, .05, "SL"),
])
def test_short_shock_requires_tick_floor_and_pre_sigma(sigma, adverse, expected):
    policy = RepairPolicy(config())
    decide(policy, PathPoint(100, .5, sigma, 1, 5, -.1))
    result = decide(policy, PathPoint(102, round(.5-adverse, 3), .1, 1, 5, -.2))
    assert result["mode"] == expected
    if expected == "SL":
        assert result["reason"] == "ADVERSE_SHOCK"


@pytest.mark.parametrize("age,expected", [(1.99, "HD"), (2, "SL"), (3, "SL"), (3.001, "HD")])
def test_short_baseline_has_both_minimum_and_maximum_age(age, expected):
    policy = RepairPolicy(config())
    decide(policy, PathPoint(100, .5, .001, 1, 5, -.1))
    result = decide(policy, PathPoint(100+age, .45, .001, 1, 5, -.1))
    assert result["mode"] == expected


def test_no_exposure_uses_opposite_probability_direction_and_held_token_tick():
    policy = RepairPolicy(config())
    for elapsed, p in [(100, .5), (102, .502)]:
        result = policy.decide(PathPoint(elapsed, p, 0., 1, -5, -.1, tick=.001),
            original_side="NO", original_qty=5, first_fill_elapsed=90)
    assert result["mode"] == "SL" and result["reason"] == "ADVERSE_SHOCK"
    assert result["observations"]["short_threshold"] == .002


def test_confirmed_drawdown_is_exact_at_six_cents_and_does_not_require_sigma():
    policy = RepairPolicy(RDPRepairPathConfig())
    for i in range(7):
        # Still profitable, already in TP; no sigma for the shock branch.
        result = decide(policy, PathPoint(100+i, round(.5-i*.01, 2), None, 1, 5, .3-i*.01))
        assert result["mode"] == ("TP" if i < 6 else "SL")
    assert result["reason"] == "CUMULATIVE_DRAWDOWN"
    assert result["observations"]["drawdown_per_share"] == .06


def test_episode_delta_and_partial_fill_separate_price_pnl_from_session_dust():
    policy = RepairPolicy(config())
    decide(policy, PathPoint(100, .5, None, 1, 5, -.1, episode_delta=4))
    decide(policy, PathPoint(101, .5, None, 1, 3, -.05, episode_delta=2))
    result = decide(policy, PathPoint(102, .45, None, 1, 3, -.06, episode_delta=2))
    assert result["observations"]["drawdown_per_share"] == .02  # 2*.05/original 5


def test_gap_restarts_local_drawdown_without_splicing_old_peak_and_new_price():
    policy = RepairPolicy(RDPRepairPathConfig())
    decide(policy, PathPoint(100, .5, None, 1, 5, -.1))
    decide(policy, PathPoint(101, .6, None, 1, 5, -.1))
    result = decide(policy, PathPoint(102, .2, None, 2, 5, -.1))
    assert result["mode"] == "HD" and result["observations"]["drawdown_per_share"] == 0
    assert result["observations"]["path_status"] == "PARTIAL_PATH"
    restored = RepairPolicy.restore(policy.cfg, policy.payload())
    result = decide(restored, PathPoint(103, .14, None, 2, 5, -.1))
    assert result["mode"] == "SL"  # a reliable local DD is sufficient evidence
    assert result["observations"]["path_breaks"][0]["peak"] == "0.1"


def test_same_generation_segment_gap_is_not_a_continuous_price_path():
    policy = RepairPolicy(RDPRepairPathConfig())
    decide(policy, PathPoint(100, .5, .001, 1, 5, -.1, path_segment=1))
    result = decide(policy, PathPoint(102, .2, .001, 1, 5, -.1, path_segment=2))
    assert result["mode"] == "HD" and result["observations"]["short_z"] is None
    assert result["observations"]["drawdown_per_share"] == 0


def test_unknown_inventory_suspends_dd_but_does_not_hide_a_price_shock():
    policy = RepairPolicy(RDPRepairPathConfig())
    decide(policy, PathPoint(100, .5, .02, 1, 5, -.1))
    result = decide(policy, PathPoint(102, .45, .1, 1, 5, -.1, inventory_path_valid=False))
    assert result["mode"] == "SL" and result["reason"] == "ADVERSE_SHOCK"
    assert not result["observations"]["drawdown_valid"]


def test_no_early_tp_cooldown_and_strict_confirmed_rate_units():
    policy = RepairPolicy(RDPRepairPathConfig())
    point = PathPoint(100, .5, .02, 1, 5, .002)
    assert decide(policy, point, start=99)["mode"] == "HD"
    point = replace(point, elapsed=100.1, pnl_per_share=.0023)
    assert decide(policy, point, start=99)["mode"] == "TP"
    policy = RepairPolicy(RDPRepairPathConfig())
    assert decide(policy, replace(point, elapsed=100, pnl_per_share=.005), start=100)["mode"] == "HD"
    assert decide(policy, replace(point, elapsed=100.01, pnl_per_share=.005), start=100)["mode"] == "TP"


def test_bad_maximum_shock_age_is_rejected():
    with pytest.raises(ValueError, match="shock_max_age_sec"):
        RDPRepairPathConfig(shock_window_sec=4)
