"""M1/M2/M3 + real SDK/Gateway/Tracker/FillTruth/Journal, Fake HTTP/time only."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from py_clob_client_v2.http_helpers import helpers

from polymaker.rdp_sm import _market
from polymaker.rdp_sm_execution import SMWindowExecutor
from polymaker.rdp_sm_loop import QuoteUnavailable, SMTradingLoop, make_quotes
from test_rdp_sm_m1 import SLUG, START, Venue
from test_rdp_sm_m2_execution import env as execution_env
from test_rdp_sm_type2_account import type2


@pytest.fixture
def env(monkeypatch, tmp_path):
    async_client = httpx.AsyncClient
    gen = execution_env.__wrapped__(monkeypatch, tmp_path)
    venue, cfg = next(gen)
    venue.post_times = []
    original_response = venue.response

    def response(request):
        if request.method == "POST":
            venue.post_times.extend([venue.time - START] * len(json.loads(request.content)))
        return original_response(request)

    transport = httpx.MockTransport(response)
    client = httpx.Client(transport=transport)
    monkeypatch.setattr(helpers, "_http_client", client)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=transport, **kw))
    real_open = SMWindowExecutor.open

    async def observe_open(cls, *args, **kwargs):
        executor = await real_open(*args, **kwargs)
        venue.executor = executor  # only instrumentation; actual M2 factory executes
        return executor

    monkeypatch.setattr(SMWindowExecutor, "open", classmethod(observe_open))
    yield venue, cfg
    client.close()
    if venue.executor and venue.executor.closed:
        venue.executor = None
    with pytest.raises(StopIteration):
        next(gen)


def runner(env):
    venue, cfg = env
    return SMTradingLoop(cfg, SLUG, "m3-session", sleep=venue.sleep, monotonic=lambda: venue.mono)


def on_windows(venue, action, *, stop_after=None):
    processed = set()

    def callback(v):
        wid = v.executor.attempts[-1].quote.window_id
        if wid in processed:
            return
        processed.add(wid)
        index = len(processed)
        action(v, index)
        # Official account replica reports the same confirmed quantities/costs.
        totals = {}
        for trade in v.trades:
            if trade["status"] != "CONFIRMED":
                continue
            for leg in trade["maker_orders"]:
                qty, cost = totals.get(leg["asset_id"], (0, 0))
                size = float(leg["matched_amount"])
                totals[leg["asset_id"]] = (qty + size, cost + size * float(leg["price"]))
        v.positions = [
            {"token_id": token, "current_size": qty, "avg_price": cost / qty}
            for token, (qty, cost) in totals.items()
        ]
        if index == stop_after:
            # Force test shutdown at market end, regardless of inventory state.
            v.time = START + 300

    venue.on_cancel = callback


def current_orders(v):
    wid = v.executor.attempts[-1].quote.window_id
    return {
        a.quote.outcome: a.order_id
        for a in v.executor.attempts
        if a.order_id and a.quote.window_id == wid
    }


@pytest.mark.parametrize(
    "delta,sizes,state",
    [
        (0, {"YES": 5, "NO": 5}, "NORMAL"),
        (2, {"YES": 5, "NO": 7}, "NORMAL"),
        (-3, {"YES": 8, "NO": 5}, "NORMAL"),
        (5, {"NO": 5}, "REBALANCE"),
        (-5, {"YES": 5}, "REBALANCE"),
    ],
)
def test_m3_01_02_quote_state_prices_and_size_skew(delta, sizes, state):
    venue = Venue()
    meta, _ = _market(venue.raw, SLUG)
    books = {
        t: {
            "best_bid": float(b["bids"][0]["price"]),
            "best_ask": float(b["asks"][0]["price"]),
            "tick": float(venue.ticks[t]),
        }
        for t, b in venue.books.items()
    }
    observed, qs = make_quotes(meta, books, Decimal(max(delta, 0)), Decimal(max(-delta, 0)), "w")
    assert observed == state and {q.outcome: q.size for q in qs} == sizes
    for q in qs:
        expected = Decimal(str(books[q.token_id]["best_bid"]))
        if state == "NORMAL":
            expected -= Decimal(str(books[q.token_id]["tick"]))
        assert Decimal(str(q.price)) == expected and q.side.value == "BUY"


async def test_m3_15_full_market_type2_real_execution_timeline(env):
    v, cfg, safe = type2(env)
    v.time = START

    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["YES"], "yes-initial", 5)
        elif index == 2:
            venue.fill(ids["NO"], "no-rebalance", 3)
        elif index == 3:
            venue.fill(ids["NO"], "no-skew", 2)

    on_windows(v, fills)
    result = await runner(env).run(offline=True)
    states = [w["state"] for w in result.windows]
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert states[:4] == ["NORMAL", "REBALANCE", "NORMAL", "NORMAL"]
    assert result.windows[0]["elapsed_s"] == 15 and result.stop_elapsed_s == 300
    assert (
        result.total_windows == 55 and result.normal_windows == 54 and result.rebalance_windows == 1
    )
    assert result.active_cycles == 3 and result.confirmed_fills == 3
    assert result.confirmed_yes == result.confirmed_no == result.completed_pairs == 5
    assert result.delta == result.residual_shares == 0
    assert result.average_yes_cost == 0.48 and result.average_no_cost == pytest.approx(0.4796)
    assert result.theoretical_pair_pnl == pytest.approx(0.202)
    assert min(v.post_times) == 15 and max(v.post_times) == 177
    assert all(15 <= stamp < 180 for stamp in v.post_times)
    assert len(v.posts) == 109 and all(row["status"] == "CANCELED" for row in v.rows.values())
    assert result.live_authority == "NOT_VERIFIED_FOR_LIVE"
    for post in v.posts:
        assert post[0]["order"]["maker"] == safe
        assert post[0]["order"]["signatureType"] == 2
    assert all(params.get("slug", SLUG) == SLUG for _, _, params in v.calls)
    print(
        "M3 full market observed:",
        json.dumps(
            {
                "first_elapsed": result.windows[0]["elapsed_s"],
                "states": states[:4],
                "inventories": [
                    (w["result"]["yes_inventory"], w["result"]["no_inventory"])
                    for w in result.windows[:3]
                ],
                "windows": result.total_windows,
                "active_cycles": result.active_cycles,
                "last_post_elapsed": max(v.post_times),
                "stop_elapsed": result.stop_elapsed_s,
                "status": result.execution_status,
            }
        ),
    )


async def test_m3_05_rebalance_safe_requote_without_intra_window_chasing(env):
    v, _ = env

    def change(venue, index):
        if index == 1:
            venue.fill(current_orders(venue)["YES"], "imbalanced", 5)
        elif index == 2:
            venue.books["2"]["bids"][0]["price"] = ".50"

    on_windows(v, change, stop_after=3)
    result = await runner(env).run(offline=True)
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "REBALANCE"]
    second, third = [w["result"]["requested_quotes"] for w in result.windows[1:]]
    assert len(second) == len(third) == 1 and second[0]["outcome"] == third[0]["outcome"] == "NO"
    assert second[0]["price"] == 0.48 and third[0]["price"] == 0.50
    assert len(v.posts) == 4 and result.residual_shares == 5 and result.active_cycles == 1
    assert all(
        w["result"]["fill_deadline_monotonic"] - w["result"]["t0_monotonic"] == 3
        for w in result.windows
    )


@pytest.mark.parametrize("stage", ["MATCHED", "MINED"])
async def test_m3_06_07_pending_fill_blocks_loop_and_does_not_become_inventory(env, stage):
    v, _ = env
    on_windows(v, lambda venue, _: venue.fill(current_orders(venue)["YES"], "pending", 5, stage))
    result = await runner(env).run(offline=True)
    assert result.total_windows == result.blocked_windows == 1 and len(v.posts) == 2
    assert result.confirmed_yes == result.confirmed_no == result.active_cycles == 0
    assert result.execution_status == "INCOMPLETE" and result.final_close_barrier == "BLOCKED"


@pytest.mark.parametrize("elapsed", [14, 240])
async def test_m3_08_no_post_outside_window(env, elapsed):
    v, _ = env
    v.time = START + elapsed
    on_windows(v, lambda *_: None, stop_after=1)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED"
    if elapsed == 14:
        assert v.post_times == [15, 15]
    else:
        assert not v.posts and result.total_windows == 0


async def test_m3_09_capital_cannot_be_bypassed(env):
    v, _ = env
    v.capital["balance"] = "3000000"
    result = await runner(env).run(offline=True)
    assert result.total_windows == result.blocked_windows == 1 and not v.posts
    assert result.windows[0]["result"]["close_barrier"] == "BLOCKED"
    assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert result.execution_status == "BLOCKED" and not v.cancels


async def test_m3_10_active_cycle_counts_windows_not_fills(env):
    v, _ = env

    def fills(venue, index):
        ids = current_orders(venue)
        if index == 2:
            venue.fill(ids["YES"], "single", 1)
        elif index == 3:
            venue.fill(ids["YES"], "multi-yes", 1)
            venue.fill(ids["NO"], "multi-no", 2)

    on_windows(v, fills, stop_after=3)
    result = await runner(env).run(offline=True)
    assert result.total_windows == 3 and result.active_cycles == 2 and result.confirmed_fills == 3
    assert result.confirmed_yes == result.confirmed_no == 2 and result.delta == 0


async def test_m3_11_cancel_ack_without_terminal_is_incomplete(env):
    v, _ = env
    v.cancel_terminal = False
    result = await runner(env).run(offline=True)
    assert result.total_windows == 1 and len(v.posts) == 2 and v.cancels
    assert result.execution_status == "INCOMPLETE" and result.final_close_barrier == "BLOCKED"


async def test_m3_12_single_market_and_one_run_boundary(env):
    v, _ = env
    on_windows(v, lambda *_: None, stop_after=1)
    loop = runner(env)
    task = asyncio.create_task(loop.run(offline=True))
    await asyncio.sleep(0)
    other = await loop.run(offline=True)
    first = await task
    assert other.stop_reason == "M3_RUN_ALREADY_USED" and other.execution_status == "BLOCKED"
    assert first.execution_status == "COMPLETED" and len(v.posts) == 2
    assert all(params.get("slug", SLUG) == SLUG for _, _, params in v.calls)


async def test_m3_12_market_identity_change_stops_submissions(env):
    v, _ = env
    on_windows(v, lambda venue, _: venue.raw.update(conditionId="0x" + "cd" * 32))
    result = await runner(env).run(offline=True)
    assert result.total_windows == 1 and len(v.posts) == 2
    assert result.stop_reason == "SESSION_MARKET_CHANGED" and result.execution_status == "BLOCKED"


async def test_m3_13_tick_change_uses_new_official_grid(env):
    v, _ = env

    def change(venue, index):
        if index == 1:
            venue.ticks["1"] = ".001"
            venue.books["1"]["tick_size"] = ".001"

    on_windows(v, change, stop_after=2)
    result = await runner(env).run(offline=True)
    prices = [w["result"]["requested_quotes"][0]["price"] for w in result.windows]
    assert prices == [0.48, 0.489] and len(v.posts) == 4


async def test_normal_pair_cost_over_one_is_allowed(env):
    v, _ = env
    for book in v.books.values():
        book["bids"][0]["price"], book["asks"][0]["price"] = ".7", ".8"
    on_windows(v, lambda *_: None, stop_after=1)
    result = await runner(env).run(offline=True)
    qs = result.windows[0]["result"]["requested_quotes"]
    assert sum(q["price"] for q in qs) > 1 and len(v.posts) == 2
    assert result.execution_status == "COMPLETED"


async def test_invalid_known_quote_is_skipped_not_rounded_or_submitted(env):
    v, _ = env
    v.time = START + 170
    v.books["1"]["bids"][0]["price"] = ".01"
    result = await runner(env).run(offline=True)
    assert result.skipped_quotes and not v.posts and result.total_windows == 0
    assert result.execution_status == "COMPLETED" and result.stop_elapsed_s == 300
    meta, _ = _market(v.raw, SLUG)
    books = {"1": {"best_bid": 0.49, "best_ask": 0.51, "tick": 0.01}}
    with pytest.raises(QuoteUnavailable):
        make_quotes(meta, books, Decimal(0), Decimal("5.001"), "w")


async def test_default_entry_and_nonfake_transport_cannot_trade(env, monkeypatch):
    v, _ = env
    result = await runner(env).run()
    assert result.stop_reason == "M3_LIVE_EXECUTION_NOT_AUTHORIZED"
    assert not v.calls and not v.posts and v.executor is None
    with httpx.Client() as real_transport_client:
        monkeypatch.setattr(helpers, "_http_client", real_transport_client)
        result = await runner(env).run(offline=True)
    assert result.stop_reason == "M3_LIVE_EXECUTION_NOT_AUTHORIZED"
    assert not v.calls and not v.posts


async def test_unknown_placement_stops_without_retry_and_retains_commitment(env):
    v, _ = env
    v.replies = [httpx.ReadTimeout("offline unknown placement")]
    result = await runner(env).run(offline=True)
    assert len(v.posts) == 2 and len(v.post_requests) == result.total_windows == result.blocked_windows == 1
    assert result.execution_status == "INCOMPLETE" and result.final_close_barrier == "BLOCKED"
    assert result.windows[0]["result"]["capital_committed"] == 4.795
    assert result.windows[0]["result"]["placement_results"][0]["outcome"] == "UNKNOWN"


async def test_new_official_inventory_between_quote_and_submit_blocks_stale_state(env):
    v, _ = env
    on_windows(v, lambda *_: None)
    real_execute = SMWindowExecutor.execute_window

    async def observed(executor, window_id, quotes, **kw):
        if window_id.endswith("w2"):
            # New official confirmed economics become visible after M3 quotes,
            # but before real M2's account trade evidence check.
            v.fill("o-1", "late-official", 5)
            v.positions = [{"token_id": "1", "current_size": 5, "avg_price": 0.48}]
        return await real_execute(executor, window_id, quotes, **kw)

    # Instrument actual M2 entry; no fake execution or fabricated outcome.
    env_monkeypatch = pytest.MonkeyPatch()
    try:
        env_monkeypatch.setattr(SMWindowExecutor, "execute_window", observed)
        result = await runner(env).run(offline=True)
    finally:
        env_monkeypatch.undo()
    assert len(v.posts) == 2 and result.total_windows == 2 and result.blocked_windows == 1
    assert result.stop_reason == "PREVIOUS_WINDOW_TRADE_EVIDENCE_CHANGED"
    assert result.confirmed_yes == 5 and result.confirmed_no == 0 and result.active_cycles == 1
    assert result.execution_status == "BLOCKED"
