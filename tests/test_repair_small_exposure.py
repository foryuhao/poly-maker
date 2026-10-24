"""Small exposures stay real inventory; new repair orders must reduce Delta."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm_execution import SMWindowExecutor
from polymaker.rdp_sm_logging import HumanRunLog
from polymaker.rdp_sm_path_repair import PathRepairController
from polymaker.repair_policy import PathPoint, RepairPolicy
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_path_repair import setup_policy
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes, reflect_positions


@pytest.mark.parametrize("quantity", [.676664, 2.499999, 2.5])
async def test_small_actual_opening_does_not_repair_or_erase_inventory(env, monkeypatch, quantity):
    setup = await setup_obi(env, monkeypatch)
    v, e, _, lifecycle = setup[:4]
    try:
        result, _ = await open_yes(setup, size=quantity)
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        controller = PathRepairController(e, lifecycle)

        def no_signal_needed(*_, **__):
            raise AssertionError("A small exposure must be filtered before signal evaluation")

        monkeypatch.setattr(controller, "observe", no_signal_needed)
        inventory, committed = e.economic_inventory(), e.committed()
        for wid in ("small-1", "small-2"):
            assert await controller.repair(*inventory, wid) is None
        assert len(v.post_requests) == 1
        assert e.repair_episode is None and e.trading_state == "NORMAL"
        assert e.cooldown.last_result["cooldown_sec"] == 0
        assert Decimal(e.cooldown.last_result["paired_shares"]) == 0
        assert e.economic_inventory() == inventory == (Decimal(str(quantity)), Decimal(0))
        assert e.committed() == committed
        records = journal_records(e.journal.path)
        exits = [r["data"]["payload"] for r in records if r["kind"] == "sm_repair_episode"
                 and r["data"]["payload"].get("event") == "COMPLETE_WITH_RESIDUAL_TOLERANCE"]
        assert len(exits) == 1 and exits[0]["inventory_flat"] is False
        assert exits[0]["residual_retained_in_ledger"] is True
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("delta", ["5", "-5"])
def test_minimum_notional_cannot_flip_equal_risk_exposure(delta):
    cfg = RDPSMConfig()
    d = Decimal(delta)
    e = SimpleNamespace(
        cfg=SimpleNamespace(rdp_sm=cfg), meta=SimpleNamespace(min_order_size=5),
        repair_episode={"side": "YES" if d > 0 else "NO"},
        economic_inventory=lambda: (max(d, Decimal(0)), max(-d, Decimal(0))),
        committed=lambda: Decimal(0),
    )
    e.delta_candidate_allowed = lambda x, side, q: SMWindowExecutor.delta_candidate_allowed(e, x, side, q)
    controller = PathRepairController(e, SimpleNamespace(account=None))
    controller.policy = RepairPolicy(cfg.repair.path, mode="SL")
    book = SimpleNamespace(best_bid=Decimal(".10"), best_ask=Decimal(".11"), tick=Decimal(".01"))
    with pytest.raises(ValueError, match="REPAIR_WOULD_NOT_REDUCE_DELTA"):
        controller._make_plan(SimpleNamespace(yes=book, no=book), {"mode": "SL"},
                              PathPoint(100, .9, .02, 1, float(d), .1))


@pytest.mark.parametrize("delta", ["2.500001", "-2.500001"])
def test_exposure_above_threshold_can_use_minimum_order_if_delta_decreases(delta):
    cfg = RDPSMConfig()
    d = Decimal(delta)
    e = SimpleNamespace(
        cfg=SimpleNamespace(rdp_sm=cfg), meta=SimpleNamespace(min_order_size=5),
        repair_episode={"side": "YES" if d > 0 else "NO"},
        economic_inventory=lambda: (max(d, Decimal(0)), max(-d, Decimal(0))),
        committed=lambda: Decimal(0),
    )
    e.delta_candidate_allowed = lambda x, side, q: SMWindowExecutor.delta_candidate_allowed(e, x, side, q)
    controller = PathRepairController(e, SimpleNamespace(account=None))
    controller.policy = RepairPolicy(cfg.repair.path, mode="SL")
    book = SimpleNamespace(best_bid=Decimal(".49"), best_ask=Decimal(".50"), tick=Decimal(".01"))
    plan = controller._make_plan(SimpleNamespace(yes=book, no=book), {"mode": "SL"},
                                 PathPoint(100, .5, .02, 1, float(d), -.1))
    assert plan["quantity"] == 5
    after = d+(Decimal(5) if plan["side"] == "YES" else -Decimal(5))
    assert abs(after) < abs(d)


async def test_partial_repair_cancels_remainder_when_actual_delta_becomes_small(env, monkeypatch):
    setup, controller = await setup_policy(env, monkeypatch)
    v, e, _, lifecycle = setup[:4]

    def partial(venue, oid):
        row = venue.rows[oid]
        if row["asset_id"] == "2" and not row["associate_trades"]:
            venue.fill(oid, "repair-to-small-exposure", 3)
            reflect_positions(venue)

    v.on_order_read = partial
    try:
        result, _ = await controller.repair(*e.economic_inventory(), "partial-small")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert e.economic_inventory() == (Decimal(5), Decimal(3))
        assert e.repair_episode is None and e.trading_state == "NORMAL"
        assert len(v.post_requests) == 2
        assert all(row["status"] == "CANCELED" for row in v.rows.values())
        assert Decimal(e.cooldown.last_result["paired_shares"]) == 3
        assert Decimal(e.cooldown.last_result["paired_yes_cost"]) == Decimal(".9")
        assert Decimal(e.cooldown.last_result["paired_no_cost"]) == Decimal("1.38")
        assert e.committed() == Decimal("2.88")  # also retains the unpaired 2*.3
        lifecycle.account = (await e._snapshot(include_books=False))[3]
        assert await controller.repair(*e.economic_inventory(), "no-small-replacement") is None
        assert len(v.post_requests) == 2 and e.economic_inventory() == (Decimal(5), Decimal(3))
    finally:
        await cleanup(setup)


def test_small_exposure_log_identifies_residual_without_claiming_pairing():
    observer = HumanRunLog(repair_mode=True)
    try:
        lines = observer.render("events", [("sm_repair_small_exposure", {
            "actual_delta": "-2.5", "threshold_shares": "2.5", "inventory_flat": False,
        })])
        assert "WAIT_WITH_RESIDUAL" in lines[0] and "Delta=-2.5 shares" in lines[0]
        assert "Actual inventory and cost retained" in lines[0]
    finally:
        observer.close()
