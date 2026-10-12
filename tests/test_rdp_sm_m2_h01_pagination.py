"""Real SM/Gateway/Tracker/FillTruth/Journal; fail only external HTTP pages."""

from copy import deepcopy
from decimal import Decimal

import httpx
import pytest

from polymaker.domain import Side, TradeState
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m2_h01_commitment import env as execution_env
from test_rdp_sm_m2_remediation import external_order


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from execution_env.__wrapped__(monkeypatch, tmp_path)


def page(request, rows, cursor="LTE="):
    return httpx.Response(
        200, json={"data": deepcopy(rows), "next_cursor": cursor}, request=request
    )


def count_real_placements(monkeypatch, executor):
    calls, real_place = [], executor.gateway.place

    async def observed(*args, **kwargs):
        calls.append(args)
        return await real_place(*args, **kwargs)

    monkeypatch.setattr(executor.gateway, "place", observed)
    return calls


@pytest.mark.parametrize("failure", ["timeout", "cursor_cycle", "missing_cursor"])
async def test_p01_order_page_survives_failure_and_later_complete_empty(env, monkeypatch, failure):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    v.rows["external"] = external_order()
    original, completed = v.response, []
    calls = count_real_placements(monkeypatch, executor)

    def response(request):
        if request.url.path == "/data/orders":
            cursor = request.url.params.get("next_cursor")
            if not completed and cursor == "MA==":
                return page(request, [v.rows["external"]], "next")
            if not completed:
                completed.append(True)
                if failure == "timeout":
                    raise httpx.ReadTimeout("offline page2 timeout")
                if failure == "cursor_cycle":
                    return page(request, [], "next")
                return httpx.Response(200, json={"data": []}, request=request)
            return page(request, [])
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        first = await executor.execute_window("w1", [quotes()[0]])
        sdk = executor.gateway._client
        assert sdk.observed_open_rows["external"]["original_size"] == "5"
        assert sdk.observed_open_rows["external"]["price"] == ".9"
        assert sdk.pagination_failure == "OPEN_ORDERS_PAGINATION_INCOMPLETE"
        # Successful later reads are permitted for diagnosis; no empty result
        # or zero-fill cancelled row can reset the incomplete-account latch.
        assert await executor.gateway.open_orders() == []
        second = await executor.execute_window("w2", [quotes("w2")[0]])
        v.rows["external"]["status"] = "CANCELED"
        assert not await executor._external_no_fill_terminal(
            "external", ("3", Side.BUY, Decimal(".9"), Decimal("5"))
        )
        assert not await executor.verify_close()
    assert completed and not calls and not v.posts and not v.cancels
    assert first.close_barrier == second.close_barrier == "BLOCKED"
    assert second.block_reason == "OPEN_ORDERS_PAGINATION_INCOMPLETE"


@pytest.mark.parametrize("status", ["MATCHED", "MINED", "CONFIRMED"])
async def test_p02_trade_page_retains_risk_without_inventory_or_automatic_release(
    env, monkeypatch, status
):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    v.rows["external"] = external_order()
    await executor._snapshot()
    trade = v.fill("external", "external-pending", 5, status)
    trade["market"] = "other"
    v.rows["external"]["status"] = "CANCELED"
    v.open_rows_override = []
    original, completed = v.response, []
    calls = count_real_placements(monkeypatch, executor)

    def response(request):
        if request.url.path == "/data/order/external":
            row = {**v.rows["external"], "size_matched": "0", "associate_trades": []}
            return httpx.Response(200, json=row, request=request)
        if request.url.path == "/data/trades":
            if not completed and request.url.params.get("next_cursor") == "MA==":
                return page(request, [trade], "next")
            if not completed:
                completed.append(True)
                raise httpx.ReadTimeout("offline page2 after positive fill evidence")
            return page(request, [])
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        first = await executor.execute_window("w1", [quotes()[0]])
        sdk = executor.gateway._client
        assert sdk.observed_open_rows["external"]["_sm_fill_observed"]
        stage, leg = sdk.observed_trade_legs[("external-pending", "external")]
        assert stage is TradeState(status) and leg.matched_amount == 5 and leg.price == 0.9
        assert sdk.pagination_failure == "TRADES_PAGINATION_INCOMPLETE"
        assert await executor.gateway.get_trades(market="other") == []
        second = await executor.execute_window("w2", [quotes("w2")[0]])
        assert not await executor.verify_close()
    assert completed and not calls and not v.posts
    assert first.close_barrier == second.close_barrier == "BLOCKED"
    assert first.block_reason == second.block_reason == "TRADES_PAGINATION_INCOMPLETE"
    assert first.yes_inventory == first.no_inventory == 0
    assert not executor.confirmed()  # external/partial API evidence is not SM inventory
    assert ("external-pending", "external") in sdk.observed_trade_legs


async def test_complete_multi_page_orders_deduplicate_and_allow_sufficient_funds(env, monkeypatch):
    v, executor = await open_executor(env)
    v.capital["balance"] = "7400000"
    v.rows["external"] = external_order(".49")
    original = v.response

    def response(request):
        if request.url.path == "/data/orders":
            if request.url.params.get("next_cursor") == "MA==":
                return page(request, [v.rows["external"]], "next")
            return page(
                request,
                [
                    row
                    for oid, row in v.rows.items()
                    if oid != "external" and row["status"] == "LIVE"
                ],
            )
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", quotes())
    assert len(v.posts) == 2 and result.close_barrier == "SAFE_TO_CONTINUE"
    assert executor.gateway._client.pagination_failure is None
    assert v.rows["external"]["status"] == "LIVE"


async def test_complete_multi_page_trade_proof_allows_verified_zero_fill_release(env, monkeypatch):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    v.rows["external"] = external_order()
    await executor._snapshot()
    v.rows["external"]["status"] = "CANCELED"
    v.open_rows_override = []
    original = v.response

    def response(request):
        if request.url.path == "/data/trades":
            return page(
                request, [], "next" if request.url.params.get("next_cursor") == "MA==" else "LTE="
            )
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", [quotes()[0]])
    assert len(v.posts) == 1 and result.close_barrier == "SAFE_TO_CONTINUE"
    assert result.yes_inventory == result.no_inventory == result.capital_committed == 0
    assert executor.gateway._client.pagination_failure is None


async def test_duplicate_completed_trade_pages_do_not_sum_commitment_or_inventory(env, monkeypatch):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    v.rows["external"] = external_order()
    await executor._snapshot()
    trade = v.fill("external", "duplicate", 5, "MATCHED")
    trade["market"] = "other"
    original = v.response

    def response(request):
        if request.url.path == "/data/trades":
            return page(
                request,
                [trade],
                "next" if request.url.params.get("next_cursor") == "MA==" else "LTE=",
            )
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        for _ in range(2):
            assert len(await executor.gateway.get_trades(market="other")) == 2
        *_, account = await executor._snapshot()
        result = await executor.execute_window("w1", [quotes()[0]])
    assert len(executor.gateway._client.observed_trade_legs) == 1
    assert account["execution_reserved_usd"] == 4.5
    assert account["basic_spendable_budget_usd"] == 1.5
    assert not v.posts and result.close_barrier == "BLOCKED" and not executor.confirmed()


async def test_incomplete_owned_trade_read_cannot_release_seen_fill_or_repeat_order(
    env, monkeypatch
):
    v, executor = await open_executor(env)
    v.on_cancel = lambda venue: venue.fill("o-1", "owned-prefix", 5, "MATCHED")
    original, completed = v.response, []
    calls = count_real_placements(monkeypatch, executor)

    def response(request):
        if request.url.path == "/data/order/o-1":
            row = {**v.rows["o-1"], "size_matched": "0", "associate_trades": []}
            return httpx.Response(200, json=row, request=request)
        if request.url.path == "/data/trades":
            if not completed and request.url.params.get("next_cursor") == "MA==":
                return page(request, v.trades, "next")
            if not completed:
                completed.append(True)
                raise httpx.ReadTimeout("offline owned fill page2")
            return page(request, [])
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        first = await executor.execute_window("w1", [quotes()[0]])
        v.on_cancel = None
        assert not await executor.verify_close()
        second = await executor.execute_window("w2", [quotes("w2")[0]])
    assert len(calls) == len(v.posts) == 1
    assert v.cancels and all(ids == ["o-1"] for ids in v.cancels)
    assert first.close_barrier == second.close_barrier == "BLOCKED"
    assert executor.committed() == Decimal("2.45")
    assert not executor.confirmed()
