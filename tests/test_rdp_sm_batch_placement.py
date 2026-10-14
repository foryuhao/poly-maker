"""One real signed HTTP batch for NORMAL; one order for REBALANCE. Offline only."""

from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import START
from test_rdp_sm_m2_execution import journal_records, open_executor, quotes
from test_rdp_sm_m3_loop import current_orders, on_windows, runner
from test_rdp_sm_m3_loop import env as loop_env


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


async def test_normal_two_sides_share_one_http_request_and_deadline(env):
    v, _ = env
    on_windows(v, lambda *_: None, stop_after=1)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 2
    assert {r["order"]["tokenId"] for r in v.post_requests[0]} == {"1", "2"}
    assert all(r["postOnly"] is True and r["orderType"] == "GTC" for r in v.post_requests[0])
    window = result.windows[0]["result"]
    assert result.windows[0]["state"] == "NORMAL"
    assert [a["outcome"] for a in window["placement_results"]] == ["ACK", "ACK"]
    assert len({a["request_hash"] for a in window["placement_results"]}) == 1
    assert window["fill_deadline_monotonic"] - window["t0_monotonic"] == 3
    assert v.cancels and all(ids == ["o-1", "o-2"] for ids in v.cancels)


async def test_normal_rebalance_normal_request_sizes_are_two_one_two(env):
    v, _ = env

    def fills(venue, index):
        ids = current_orders(venue)
        if index == 1:
            venue.fill(ids["YES"], "first-up", 5)
        if index == 2:
            assert set(ids) == {"NO"}
            venue.fill(ids["NO"], "repair-down", 5)

    on_windows(v, fills, stop_after=3)
    result = await runner(env).run(offline=True)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert [w["state"] for w in result.windows] == ["NORMAL", "REBALANCE", "NORMAL"]
    assert [len(request) for request in v.post_requests] == [2, 1, 2]
    assert v.post_requests[1][0]["order"]["tokenId"] == "2"
    assert result.confirmed_yes == result.confirmed_no == 5 and result.merged_pairs == 0


async def test_slow_presubmit_reads_cannot_consume_gap_between_sides(env, monkeypatch):
    v, cfg = env
    _, executor = await open_executor(env)
    cfg.risk.ws_stale_halt_s = 100
    timeline = []
    original = v.response

    def slow_reads(request):
        timeline.append((request.method, request.url.path, v.mono))
        if request.method == "GET":
            v.mono += 0.4
            v.time += 0.4
        return original(request)

    with boundary(monkeypatch, v, lambda request: slow_reads(request)):
        result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert result.t0_monotonic > 3  # many mandatory reads, all before the shared window
    assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 2
    start = next(i for i, row in enumerate(timeline) if row[0] == "POST")
    stop = next(i for i, row in enumerate(timeline[start + 1:], start + 1) if row[0] == "DELETE")
    assert all(row[0] != "GET" for row in timeline[start + 1:stop])
    assert result.fill_deadline_monotonic - result.t0_monotonic == 3


@pytest.mark.parametrize("key", ["balance", "allowance"])
async def test_entire_batch_budget_required_before_any_submission(env, key):
    v, executor = await open_executor(env)
    if key == "balance":
        v.capital["balance"] = "3000000"
    else:
        v.capital["allowances"] = {k: "3000000" for k in v.capital["allowances"]}
    result = await executor.execute_window("w1", quotes())
    assert result.block_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert not v.post_requests and not v.posts and result.capital_committed == 0
    assert not any(r["kind"] == "sm_placement_intent" for r in journal_records(executor.journal.path))


@pytest.mark.parametrize("bad_index", [0, 1])
async def test_partial_rejection_cancels_accepted_leg_without_retry(env, bad_index):
    v, executor = await open_executor(env)
    v.replies = [{"success": True, "orderID": f"o-{i + 1}"} if i != bad_index
                 else {"success": False, "orderID": "", "errorMsg": "post only"} for i in range(2)]
    result = await executor.execute_window("w1", quotes())
    accepted = f"o-{2 if bad_index == 0 else 1}"
    assert len(v.post_requests) == 1 and len(v.post_requests[0]) == 2
    assert result.placement_results[bad_index]["outcome"] == "REJECTED"
    assert result.known_order_ids == [accepted] and v.cancels == [[accepted]]
    assert result.close_barrier == "SAFE_TO_CONTINUE" and result.capital_committed == 0
    assert v.mono == 0  # stop waiting immediately on a rejected leg


@pytest.mark.parametrize("bad_index", [0, 1])
async def test_partial_unknown_preserves_known_ack_and_unknown_cost(env, bad_index):
    v, executor = await open_executor(env)
    v.replies = [{"success": True, "orderID": f"o-{i + 1}"} if i != bad_index
                 else {"success": True} for i in range(2)]
    result = await executor.execute_window("w1", quotes())
    accepted = f"o-{2 if bad_index == 0 else 1}"
    assert result.close_barrier == "BLOCKED"
    assert result.placement_results[bad_index]["outcome"] == "UNKNOWN"
    assert result.known_order_ids == [accepted] and v.cancels == [[accepted]]
    assert result.capital_committed == (2.45 if bad_index == 0 else 2.4)
    assert len(v.post_requests) == 1
    await executor.execute_window("w2", quotes("w2"))
    assert len(v.post_requests) == 1


@pytest.mark.parametrize("fault", ["timeout", "short", "long", "dict", "duplicate", "nonobject"])
async def test_ambiguous_batch_response_retains_full_commitment(env, monkeypatch, fault):
    v, executor = await open_executor(env)
    if fault == "timeout":
        v.replies = [httpx.ReadTimeout("offline batch timeout")]
    original = v.response

    def alter(request):
        response = original(request)
        if request.method != "POST":
            return response
        rows = response.json()
        raw = {"short": rows[:1], "long": rows + [rows[0]], "dict": rows[0],
               "duplicate": [rows[0], rows[0]], "nonobject": [None, None]}.get(fault, rows)
        return httpx.Response(200, json=raw, request=request)

    with boundary(monkeypatch, v, lambda request: alter(request)):
        result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.capital_committed == 4.85
    assert all(a["outcome"] == "UNKNOWN" and a["post_started"] for a in result.placement_results)
    assert len(v.post_requests) == 1 and result.confirmed_fills == []


async def test_each_token_uses_its_own_tick_in_batch_signing(env):
    v, executor = await open_executor(env)
    qs = [quotes()[0], replace(quotes()[1], price=0.479)]
    result = await executor.execute_window("w1", qs)
    assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
    assert len(v.post_requests) == 1
    signed = {r["order"]["tokenId"]: r["order"] for r in v.post_requests[0]}
    assert signed["1"]["makerAmount"] == "2450000"
    assert signed["2"]["makerAmount"] == "2395000"


async def test_both_intents_and_full_pair_reservation_exist_before_post(env):
    v, executor = await open_executor(env)
    at_post = []
    v.after_post = lambda _: at_post.append(executor.committed())
    result = await executor.execute_window("w1", quotes())
    records = journal_records(executor.journal.path)
    intents = [r["data"]["payload"] for r in records if r["kind"] == "sm_placement_intent"]
    assert at_post == [Decimal("4.85")]
    assert len(intents) == 2 and all(p["capital_committed_usd"] == "4.85" for p in intents)
    assert len({a["request_hash"] for a in result.placement_results}) == 1


async def test_pending_match_still_blocks_and_preserves_unconfirmed_cost(env):
    v, executor = await open_executor(env)
    v.on_cancel = lambda venue: venue.fill("o-1", "pending-up", 5, "MATCHED")
    result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.confirmed_fills == []
    assert result.yes_inventory == result.no_inventory == 0 and result.capital_committed == 2.45
    assert len(v.post_requests) == 1


async def test_swapped_response_ids_fail_final_order_identity_verification(env, monkeypatch):
    v, executor = await open_executor(env)
    original = v.response

    def reversed_response(request):
        response = original(request)
        if request.method == "POST":
            return httpx.Response(200, json=list(reversed(response.json())), request=request)
        return response

    with boundary(monkeypatch, v, lambda request: reversed_response(request)):
        result = await executor.execute_window("w1", quotes())
    assert result.close_barrier == "BLOCKED" and result.confirmed_fills == []
    assert result.capital_committed == 4.85 and len(v.post_requests) == 1


@pytest.mark.parametrize("elapsed", [14, 222, 239, 240])
async def test_cutoff_blocks_entire_request(env, elapsed):
    v, executor = await open_executor(env)
    v.time = START + elapsed
    result = await executor.execute_window("w1", quotes())
    assert result.block_reason == "MARKET_SUBMISSION_CUTOFF" and not v.post_requests
