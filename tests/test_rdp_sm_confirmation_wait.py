"""Post-cancel confirmation waiting through real M2/M3, fake venue/time only."""

import pytest

from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m3_loop import current_orders, on_windows, runner
from test_rdp_sm_m3_loop import env as loop_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


async def test_matched_mined_confirmed_after_cancel_reopens_barrier(env):
    v, executor = await open_executor(env)
    v.on_cancel = lambda venue: venue.fill("o-1", "delayed", 5, "MATCHED")
    observed = []

    async def settle(venue):
        if not venue.trades:
            return
        observed.append(
            (
                venue.mono,
                executor.safe_to_continue,
                len(executor.confirmed()),
                float(executor.committed()),
                len(venue.post_requests),
            )
        )
        venue.trades[0]["status"] = "MINED" if venue.mono < 7 else "CONFIRMED"

    v.on_sleep = settle
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert result.yes_inventory == 5 and result.no_inventory == 0
    assert result.capital_committed == 2.45
    assert observed == [(5, False, 0, 2.45, 1), (7, False, 0, 2.45, 1)]
    assert len(v.cancels) == len(v.post_requests) == 1
    assert result.fill_deadline_monotonic - result.t0_monotonic == 3


async def test_delayed_confirmations_allow_normal_rebalance_normal(env):
    v, _ = env
    born = {}

    def fills(venue, index):
        if index < 3:
            oid = current_orders(venue)["YES" if index == 1 else "NO"]
            tid = f"delayed-{index}"
            venue.fill(oid, tid, 5, "MATCHED")
            born[tid] = venue.mono

    on_windows(v, fills, stop_after=3)

    async def settle(venue):
        for trade in venue.trades:
            age = venue.mono - born[trade["id"]]
            if age >= 4:
                trade["status"] = "CONFIRMED"
            elif age >= 2:
                trade["status"] = "MINED"
        totals = {}
        for trade in venue.trades:
            if trade["status"] != "CONFIRMED":
                continue
            for leg in trade["maker_orders"]:
                token = leg["asset_id"]
                qty, cost = totals.get(token, (0, 0))
                size = float(leg["matched_amount"])
                totals[token] = (qty + size, cost + size * float(leg["price"]))
        venue.positions = [
            {"token_id": token, "current_size": qty, "avg_price": cost / qty}
            for token, (qty, cost) in totals.items()
        ]

    v.on_sleep = settle
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    assert [len(request) for request in v.post_requests] == [2, 1, 2]
    assert v.post_requests[1][0]["order"]["tokenId"] == "2"
    assert result.confirmed_yes == result.confirmed_no == 5
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
