"""Known closed dust may be absent from the Data API without altering its ledger."""

from decimal import Decimal

import pytest

from polymaker.rdp_sm_execution import InventoryMismatch
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes


@pytest.mark.parametrize("status", ["CONFIRMED", "MINED"])
async def test_proven_dust_omitted_by_data_api_keeps_exact_quantity_cost_and_delta(
        env, monkeypatch, status):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    try:
        opened, _ = await open_yes(setup, size=.01, status=status)
        assert opened.close_barrier == "SAFE_TO_CONTINUE", opened.block_reason
        if status == "MINED":
            assert e.position_reconciled and not e.confirmed()
        v.positions = []
        for _ in range(2):
            *_, account = await e._snapshot(include_books=False)
            assert account["yes_inventory"] == 0
            assert e.inventory_mismatch is None
            obs = e.inventory_observation["inventory"]["YES"]
            assert obs == {"expected": .01, "actual": 0., "difference": -.01,
                           "data_api_omitted_known_dust": True}
        assert e.economic_inventory() == (Decimal(".01"), Decimal(0))
        assert e.committed() == Decimal(".003")
        assert e.safe_to_continue and len(v.post_requests) == 1
        assert not e.delta_candidate_allowed(Decimal(".01"), "YES", Decimal(5))
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("quantity", [.1, .2, 5])
async def test_missing_position_at_or_above_existing_dust_limit_still_blocks(
        env, monkeypatch, quantity):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    try:
        await open_yes(setup, size=quantity)
        v.positions = []
        with pytest.raises(InventoryMismatch):
            await e._snapshot(include_books=False)
        assert e.inventory_mismatch["tolerance_shares"] == .01
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("positions", [
    [{"token_id": "1", "current_size": ".02", "avg_price": ".3"}],
    [{"token_id": "2", "current_size": ".02", "avg_price": ".3"}],
])
async def test_dust_exception_does_not_mask_out_of_tolerance_discrepancies(
        env, monkeypatch, positions):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    try:
        await open_yes(setup, size=.01)
        v.positions = positions
        with pytest.raises(InventoryMismatch):
            await e._snapshot(include_books=False)
        assert e.economic_inventory() == (Decimal(".01"), Decimal(0))
        assert e.committed() == Decimal(".003") and len(v.post_requests) == 1
    finally:
        await cleanup(setup)
