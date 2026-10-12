"""Close at loop end, then admit fresh books without repeated order REST reads."""

from decimal import Decimal

import httpx
import pytest

from polymaker.rdp_sm_entry import run_current_market
from test_rdp_sm_entry import boundary
from test_rdp_sm_m2_execution import open_executor, quotes
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("windows", [1, 4])
async def test_production_checks_at_tail_and_has_no_rest_reads_after_final_book(
    env, monkeypatch, windows
):
    v, cfg = env
    trace = []
    on_windows(v, lambda *_: None, stop_after=windows)

    def observe(request):
        path = request.url.path
        # Slow official account and order replies; never fake M1/M2/M3 internals.
        delay = (1 if path == "/balance-allowance" else
                 .2 if path.startswith("/data/order/") else
                 .05 if path in {"/book", "/tick-size"} else 0)
        v.mono += delay
        v.time += delay
        trace.append((request.method, path, v.mono))
        return None

    with boundary(monkeypatch, v, observe):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    posts = [i for i, (method, path, _) in enumerate(trace) if method == "POST" and path == "/orders"]
    assert len(posts) == windows
    # Each tail proves all known orders with two official order reads; final
    # shutdown explicitly verifies once more. No extra loop-head proofs.
    assert sum(path.startswith("/data/order/") for _, path, _ in trace) == (
        sum(4 * i for i in range(1, windows + 1)) + 4 * windows)
    for post in posts:
        before = trace[:post]
        final_book = max(i for i, (_, path, _) in enumerate(before) if path == "/book")
        assert trace[final_book + 1:post] == []
        balance = max(i for i, (_, path, _) in enumerate(before) if path == "/balance-allowance")
        assert [path for _, path, _ in trace[balance + 1:post]].count("/book") == 2
        assert not any(path.startswith("/data/order/") for _, path, _ in trace[balance + 1:post])
        # Even with accumulated known orders, both final books reach POST
        # immediately; account/order latency is paid before acquiring them.
        recent_books = [t for _, path, t in trace[balance + 1:post] if path == "/book"]
        assert trace[post][2] - min(recent_books) < .2


@pytest.mark.parametrize("status", ["MATCHED", "MINED", "CONFIRMED"])
async def test_new_rest_trade_invalidates_cached_safe_close_before_next_post(env, monkeypatch, status):
    v, executor = await open_executor(env)
    first = await executor.execute_window("w1", quotes())
    assert first.close_barrier == "SAFE_TO_CONTINUE"
    seen = []

    def late_trade(request):
        if request.url.path == "/data/trades" and not seen:
            seen.append(True)
            v.fill("o-1", "late-rest-only", 1, status)
        return None

    with boundary(monkeypatch, v, late_trade):
        second = await executor.execute_window("w2", quotes("w2"))
    assert seen and len(v.post_requests) == 1
    assert second.placement_results == []
    assert second.block_reason == "PREVIOUS_WINDOW_TRADE_EVIDENCE_CHANGED"
    if status != "CONFIRMED":
        assert second.close_barrier == "BLOCKED"
        assert executor.committed() >= Decimal("5") * Decimal(str(quotes()[0].price))
        assert second.yes_inventory == 0
    else:
        # Closeout may reconcile the late CONFIRMED fill, but cannot submit
        # quotes based on the previous inventory decision.
        assert second.yes_inventory == 1


async def test_corrupt_fresh_trade_page_cannot_reuse_prior_close_proof(env, monkeypatch):
    v, executor = await open_executor(env)
    first = await executor.execute_window("w1", quotes())
    assert first.close_barrier == "SAFE_TO_CONTINUE"

    def corrupt(request):
        if request.url.path == "/data/trades":
            return httpx.Response(200, json={"data": [{}], "next_cursor": "LTE="}, request=request)
        return None

    with boundary(monkeypatch, v, corrupt):
        second = await executor.execute_window("w2", quotes("w2"))
    assert len(v.post_requests) == 1 and second.placement_results == []
    assert second.close_barrier == "BLOCKED"
    assert not executor.safe_to_continue
