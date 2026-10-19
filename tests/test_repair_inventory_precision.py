"""Data API quantity tolerance must preserve exact economic fill accounting."""

from decimal import Decimal

import pytest

from polymaker.rdp_sm_execution import InventoryMismatch
from test_rdp_sm_obi_execution import cleanup, setup_obi
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair import open_yes


@pytest.mark.parametrize("difference,accepted", [
    ("-.000045", True),  # Live market: 4.994445 ledger versus 4.9944 Data API.
    (".000045", True),
    ("-.009999", True),
    (".009999", True),
    ("-.01", False),
    (".01", False),
    ("-.010001", False),
    (".010001", False),
])
async def test_data_api_quantity_tolerance_is_exclusive_and_preserves_ledger(
        env, monkeypatch, difference, accepted):
    setup = await setup_obi(env, monkeypatch)
    v, e = setup[:2]
    quantity = Decimal("4.994445")
    try:
        opened, _ = await open_yes(setup, size=float(quantity))
        assert opened.close_barrier == "SAFE_TO_CONTINUE", opened.block_reason
        cost = e.committed()
        actual = quantity+Decimal(difference)
        v.positions = [{"token_id": "1", "current_size": str(actual), "avg_price": ".3"}]
        if accepted:
            *_, account = await e._snapshot(include_books=False)
            assert Decimal(str(account["yes_inventory"])) == actual
            assert e.inventory_mismatch is None and e.safe_to_continue
        else:
            with pytest.raises(InventoryMismatch):
                await e._snapshot(include_books=False)
        observation = e.inventory_observation
        assert observation["tolerance_shares"] == .01
        assert observation["tolerance_exclusive"] is True
        assert observation["inventory"]["YES"]["difference"] == float(difference)
        assert e.economic_inventory() == (quantity, Decimal(0))
        assert e.committed() == cost == quantity*Decimal(".3")
        assert len(v.post_requests) == 1
    finally:
        await cleanup(setup)
