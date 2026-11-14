"""Independent contract tests: late pre-arrival constraints must also inspect terminal fills."""
from decimal import Decimal

import pytest

from test_m1234_sim_core import intent
from test_m1234_sim_ticks import change, initial, send


@pytest.mark.parametrize("quantity,cancelled,price,source,expected_error", [
    ("5", False, ".095", .055, True),
    ("10", False, ".095", .055, True),
    ("5", True, ".095", .055, True),
    ("10", False, ".09", .055, False),
    ("10", False, ".095", .11, False),
], ids=["partial-conflict", "filled-conflict", "cancelled-partial-conflict", "aligned-control", "grandfather-control"])
async def test_constraint_conflict_survives_order_completion(tmp_path, quantity, cancelled, price, source, expected_error):
    e = await initial(tmp_path)
    try:
        await change(e, .005)
        orders = {name: b.submit(intent(price=price)) for name, b in e.brokers.items()}
        await send(e, "FRAME", .12, raw={
            "event_type": "last_trade_price", "market": e.meta.condition_id,
            "asset_id": "1", "timestamp": str((e.test_wall + .12) * 1000),
            "price": ".09", "size": quantity, "side": "SELL", "trade_id": "pre-tick",
        })
        assert all(o.filled == Decimal(quantity) for o in orders.values())
        at = .16
        if cancelled:
            await send(e, "RESOURCE", .13)
            for name, b in e.brokers.items():
                b.cancel(orders[name].id)
            await send(e, "RESOURCE", .34)
            assert all(o.state == "CANCELLED" for o in orders.values())
            at = .4
        raw = {
            "event_type": "tick_size_change", "market": e.meta.condition_id,
            "asset_id": "1", "timestamp": str((e.test_wall + source) * 1000),
            "old_tick_size": ".001", "new_tick_size": ".01",
        }
        if expected_error:
            # The published arrival-v2 contract explicitly chooses visible failure
            # for a contradictory known fill; merely marking uncertainty is insufficient.
            with pytest.raises(ValueError, match="SIM_CONSTRAINT_ORDERING_CONTRADICTS_KNOWN_FILL"):
                await send(e, "FRAME", at, raw=raw)
        else:
            await send(e, "FRAME", at, raw=raw)
            assert all(o.state == "FILLED" and not o.uncertain for o in orders.values())
    finally:
        await e.close()
