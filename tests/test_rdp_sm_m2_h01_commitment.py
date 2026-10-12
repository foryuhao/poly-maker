"""Cross-snapshot collateral regressions using real SM/Gateway and fake HTTP."""

import asyncio

import httpx
import pytest

from polymaker.domain import Side, TradeState
from polymaker.state.tracker import TradeEvent
from polymaker.userstream.parse import normalize_order
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m2_remediation import env as execution_env
from test_rdp_sm_m2_remediation import external_order


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from execution_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("visible_read", [1, 2])
async def test_reviewer_initial_observation_survives_presubmit_omission(
    env, monkeypatch, visible_read
):
    v, executor = await open_executor(env)
    v.capital["balance"] = "6000000"
    v.rows["external"] = external_order()
    original, reads, place_calls = v.response, [], []
    real_place = executor.gateway.place

    async def observe_place(*args, **kwargs):
        place_calls.append(args)
        return await real_place(*args, **kwargs)  # instrumentation, never a fake Gateway

    monkeypatch.setattr(executor.gateway, "place", observe_place)

    def response(request):
        if request.url.path == "/data/orders":
            reads.append(len(reads) + 1)
            v.open_rows_override = [v.rows["external"]] if len(reads) == visible_read else []
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", [quotes()[0]])
        again = await executor.execute_window("w2", [quotes("w2")[0]])
    assert reads == list(range(1, 9))
    assert not place_calls and not v.posts and not v.cancels
    assert result.block_reason == again.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert "external" in executor.gateway._client.observed_open_rows
    assert v.rows["external"]["status"] == "LIVE"


async def test_startup_preflight_observation_is_retained_too(env, monkeypatch):
    v, _ = env
    v.capital["balance"] = "6000000"
    v.rows["external"] = external_order()
    original, reads = v.response, []

    def response(request):
        if request.url.path == "/data/orders":
            reads.append(len(reads) + 1)
            v.open_rows_override = [v.rows["external"]] if len(reads) == 1 else []
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        _, executor = await open_executor(env)
        result = await executor.execute_window("w1", [quotes()[0]])
    assert reads == [1, 2, 3, 4, 5]
    assert not v.posts and result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"


async def observe_then_omit(v, executor):
    v.rows["external"] = external_order()
    v.capital["balance"] = "6000000"
    *_, account = await executor._snapshot()
    assert account["execution_reserved_usd"] == 4.5
    assert account["basic_spendable_budget_usd"] == 1.5
    v.open_rows_override = []


async def test_repeated_omissions_and_live_or_unknown_state_retain_maximum(env, monkeypatch):
    v, executor = await open_executor(env)
    await observe_then_omit(v, executor)
    for _ in range(3):
        *_, account = await executor._snapshot()
        assert account["execution_reserved_usd"] == 4.5
    original = v.response

    def response(request):
        if request.url.path == "/data/order/external":
            raise httpx.ReadTimeout("offline unknown external state")
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        *_, account = await executor._snapshot()
        result = await executor.execute_window("w1", [quotes()[0]])
    assert account["execution_reserved_usd"] == 4.5
    assert not v.posts and result.close_barrier == "BLOCKED"


async def test_only_fresh_no_fill_terminal_proof_releases_budget(env):
    v, executor = await open_executor(env)
    await observe_then_omit(v, executor)
    v.rows["external"]["status"] = "CANCELED"
    *_, account = await executor._snapshot()
    assert account["execution_reserved_usd"] == 0
    assert account["basic_spendable_budget_usd"] == 6
    # Evidence is retained and checked again. A later contradiction cannot
    # reuse the old release proof, even without an open-list reappearance.
    v.rows["external"]["status"] = "LIVE"
    *_, account = await executor._snapshot()
    assert account["execution_reserved_usd"] == 4.5
    v.rows["external"]["status"] = "CANCELED"
    result = await executor.execute_window("w1", [quotes()[0]])
    assert len(v.posts) == 1 and result.close_barrier == "SAFE_TO_CONTINUE"
    assert v.cancels == [["o-1"]] and result.known_order_ids == ["o-1"]
    assert result.yes_inventory == result.no_inventory == 0
    assert executor.processor.owned_record("external") is None
    assert any(call[1] == "/data/order/external" for call in v.calls)
    assert any(call[1] == "/data/trades" for call in v.calls)


@pytest.mark.parametrize(
    "cause",
    ["matched", "mined", "confirmed", "late_cumulative", "missing_associates", "market_conflict"],
)
async def test_cancellation_with_unresolved_or_new_fill_evidence_cannot_release(env, cause):
    v, executor = await open_executor(env)
    await observe_then_omit(v, executor)
    v.rows["external"]["status"] = "CANCELED"
    if cause in {"matched", "mined", "confirmed"}:
        v.fill("external", "external-trade", 1, cause.upper())
        # Simulate an order replica lagging the authoritative trade collection.
        v.rows["external"]["size_matched"] = "0"
        v.rows["external"]["associate_trades"] = []
    elif cause == "missing_associates":
        del v.rows["external"]["associate_trades"]
    elif cause == "market_conflict":
        v.rows["external"]["market"] = "wrong-market"
    else:
        read_count = 0

        def late_fill(venue, oid):
            nonlocal read_count
            if oid == "external":
                read_count += 1
                if read_count == 2:
                    venue.fill("external", "late", 1)

        v.on_order_read = late_fill
    result = await executor.execute_window("w1", [quotes()[0]])
    assert not v.posts and result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert "external" in executor.gateway._client.observed_open_rows


async def test_duplicate_observations_and_own_proofs_do_not_double_count(env):
    v, executor = await open_executor(env)
    v.rows["external"] = external_order(".49")
    v.capital["balance"] = "7400000"
    for _ in range(3):
        *_, account = await executor._snapshot()
        assert account["execution_reserved_usd"] == 2.45
        assert account["basic_spendable_budget_usd"] == 4.95
    for window in ("w1", "w2"):
        result = await executor.execute_window(window, quotes(window))
        assert result.close_barrier == "SAFE_TO_CONTINUE" and result.capital_committed == 0
    assert len(v.posts) == 4 and v.rows["external"]["status"] == "LIVE"


@pytest.mark.parametrize("source", ["listing", "order", "trade"])
async def test_seen_external_fill_cannot_be_erased_by_later_zero_fill_replicas(env, source):
    v, executor = await open_executor(env)
    await observe_then_omit(v, executor)
    if source == "listing":
        v.open_rows_override = None
        v.rows["external"]["size_matched"] = "1"
    else:
        v.rows["external"]["status"] = "CANCELED"
        if source == "order":
            v.rows["external"]["size_matched"] = "1"
        else:
            v.fill("external", "observed-fill", 1, "MATCHED")
            v.rows["external"]["size_matched"] = "0"
            v.rows["external"]["associate_trades"] = []
    *_, account = await executor._snapshot()
    assert account["execution_reserved_usd"] == 4.5
    v.open_rows_override = []
    v.rows["external"].update(status="CANCELED", size_matched="0", associate_trades=[])
    v.trades = []
    result = await executor.execute_window("w1", [quotes()[0]])
    assert not v.posts and result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert executor.gateway._client.observed_open_rows["external"]["_sm_fill_observed"]


async def test_sequential_submit_reserves_omitted_external_and_local_ack(env, monkeypatch):
    v, executor = await open_executor(env)
    v.rows["external"] = external_order()
    v.capital["balance"] = "7000000"
    original, reads = v.response, []

    def response(request):
        if request.url.path == "/data/orders":
            reads.append(len(reads) + 1)
            v.open_rows_override = [v.rows["external"]] if len(reads) == 1 else []
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", quotes())
    assert len(v.posts) == 1 and v.cancels == [["o-1"]]
    assert result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert result.known_order_ids == ["o-1"] and result.capital_committed == 0
    assert v.rows["external"]["status"] == "LIVE"


async def test_ambiguous_balance_reservation_semantics_never_infer_release(env):
    v, executor = await open_executor(env)
    await observe_then_omit(v, executor)
    # A smaller raw balance is not proof that a retained order is terminal or
    # that its funds are available. The budget deliberately remains conservative.
    v.capital["balance"] = "3000000"
    *_, account = await executor._snapshot()
    assert account["execution_reserved_usd"] == 4.5
    assert account["basic_spendable_budget_usd"] == 0
    result = await executor.execute_window("w1", [quotes()[0]])
    assert not v.posts and result.close_barrier == "BLOCKED"


@pytest.mark.parametrize("event", ["LIVE", "CONFIRMED"])
async def test_late_external_ws_fact_invalidates_zero_fill_budget_before_submit(
    env, monkeypatch, event
):
    v, executor = await open_executor(env)
    await observe_then_omit(v, executor)
    v.rows["external"]["status"] = "CANCELED"
    original, loop, delivered = v.response, asyncio.get_running_loop(), []

    def response(request):
        if request.url.path == "/time" and not delivered:
            delivered.append(True)

            def ws_event():
                if event == "LIVE":
                    executor.processor.on_order(
                        normalize_order({**v.rows["external"], "status": "LIVE"}), "other"
                    )
                else:
                    executor.processor.on_trade(
                        TradeEvent(
                            "3",
                            Side.BUY,
                            0.9,
                            1,
                            "external-late",
                            TradeState.CONFIRMED,
                            v.time,
                            "external",
                            "other",
                        ),
                        "other",
                    )

            loop.call_soon_threadsafe(ws_event)
        return original(request)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        monkeypatch.setattr("py_clob_client_v2.http_helpers.helpers._http_client", client)
        result = await executor.execute_window("w1", [quotes()[0]])
        again = await executor.execute_window("w2", [quotes("w2")[0]])
    assert delivered and not v.posts
    assert result.block_reason == "CAPITAL_EVIDENCE_CHANGED_DURING_READ"
    assert again.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert result.yes_inventory == result.no_inventory == 0


async def test_external_fact_during_final_sdk_spender_check_cannot_reuse_release(env, monkeypatch):
    v, executor = await open_executor(env)
    await observe_then_omit(v, executor)
    v.rows["external"]["status"] = "CANCELED"
    client, loop, resolutions = executor.gateway._client, asyncio.get_running_loop(), []
    real_resolve = client._ClobClient__resolve_version

    def resolve():
        resolutions.append(True)
        if len(resolutions) == 3:  # initial snapshot, pre-submit snapshot, final spender check
            loop.call_soon_threadsafe(
                executor.processor.on_order,
                normalize_order({**v.rows["external"], "status": "LIVE"}),
                "other",
            )
        return real_resolve()  # preserve actual SDK resolution; fake only event timing

    monkeypatch.setattr(client, "_ClobClient__resolve_version", resolve)
    result = await executor.execute_window("w1", [quotes()[0]])
    assert len(resolutions) == 4  # the final synchronous version check also remains real
    assert not v.posts and not executor.attempts
    assert result.block_reason == "CAPITAL_EVIDENCE_CHANGED_BEFORE_SUBMIT"
