"""Production quote/HTTP admission failures must not end a recoverable session."""

from copy import deepcopy
from decimal import Decimal

import httpx
import pytest

from test_rdp_sm_entry import boundary
from test_rdp_sm_m1 import START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import current_orders, on_windows
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_recovery import run


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def set_probability(v, yes_bid):
    bid = Decimal(yes_bid)
    for token, b, a in (("1", bid, bid + Decimal(".01")),
                        ("2", Decimal(".99") - bid, Decimal(1) - bid)):
        v.books[token]["bids"][0]["price"] = str(b)
        v.books[token]["asks"][0]["price"] = str(a)


@pytest.mark.parametrize("target", [0, 1])
async def test_low_price_repair_stays_at_gap_when_final_snapshot_price_falls(
    env, monkeypatch, target
):
    v, cfg = env
    cfg.rdp_sm.base_size = Decimal(7)
    cfg.rdp_sm.target_delta = Decimal(target)
    cfg.rdp_sm.max_order_size = Decimal(15)
    v.time = START + 60
    set_probability(v, ".26")
    low_reads = []

    def fills(venue, index):
        orders = current_orders(venue)
        if index == 1:
            venue.fill(orders["YES"], "initial-yes", 6)
        elif index == 2:
            qty = float(venue.rows[orders["NO"]]["original_size"])
            venue.fill(orders["NO"], "first-repair", qty)
        elif index == 3:
            venue.fill(orders["NO"], "one-sided-no", 7)
            venue.time = START + 164
            set_probability(venue, ".07")
        elif index == 4:
            venue.fill(orders["YES"], "exact-repair", 7)

    on_windows(v, fills, stop_after=4)

    def move(request):
        if (request.url.path == "/book" and request.url.params["token_id"] == "1"
                and v.executor is not None and len(v.executor.attempts) == 5):
            low_reads.append(v.time)
            # Both prices accept the same seven shares; a final price drop
            # must no longer inflate quantity past max15 to reach a dollar.
            if len(low_reads) >= 2:
                set_probability(v, ".06")
        return None

    with boundary(monkeypatch, v, move):
        result = await run(env)
    assert len(low_reads) >= 2
    repair = result.windows[3]["result"]
    assert repair["requested_quotes"][0]["price"] == .06
    assert repair["requested_quotes"][0]["size"] == 7
    assert len(repair["placement_results"]) == 1 and repair["t0_monotonic"] is not None
    assert repair["close_barrier"] == "SAFE_TO_CONTINUE"
    assert result.skipped_quotes == []
    assert result.stop_elapsed_s == 300 and result.stop_reason == "MARKET_CUTOFF"
    assert result.execution_status == "COMPLETED" and result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert all(row["status"] != "LIVE" for row in v.rows.values())
    assert len(v.post_requests) == 4
    assert result.windows[3]["sizing"]["confirmed_yes_fill"] == "7.0"
    assert result.delta == -target


async def test_unquotable_repair_never_requotes_without_fresh_close_proof(env, monkeypatch):
    v, cfg = env
    cfg.rdp_sm.base_size = Decimal(7)
    cfg.rdp_sm.max_order_size = Decimal(15)
    v.time = START + 60
    reads = []
    failed = []
    on_windows(v, lambda venue, _: venue.fill(current_orders(venue)["NO"], "no", 7))

    def fault(request):
        if (request.url.path == "/book" and request.url.params["token_id"] == "1"
                and v.trades):
            reads.append(v.time)
            # A genuine tick-grid violation still forces a close before POST.
            set_probability(v, ".07" if len(reads) == 1 else ".0605")
        if request.url.path == "/data/order/o-1" and len(reads) >= 2:
            failed.append(v.time)
            return httpx.Response(503, request=request, json={"error": "offline close outage"})
        return None

    with boundary(monkeypatch, v, fault):
        result = await run(env)
    assert failed and len(v.post_requests) == 1
    assert result.windows[1]["result"]["placement_results"] == []
    assert result.windows[1]["result"]["close_barrier"] == "BLOCKED"
    assert result.stop_elapsed_s == 300 and result.execution_status == "INCOMPLETE"
    assert result.final_close_barrier == "BLOCKED" and result.delta == -7


@pytest.mark.parametrize("existing_orders", [False, True])
@pytest.mark.parametrize("fault", ["timeout", 429, 503, "clock"])
async def test_final_admission_read_recovers_after_proven_close(
    env, monkeypatch, existing_orders, fault
):
    v, _ = env
    v.time = START + 60
    reads, failures = [], []
    on_windows(v, lambda *_: None, stop_after=2 if existing_orders else 1)

    def outage(request):
        ready = v.executor is not None and len(v.executor.attempts) == (2 if existing_orders else 0)
        if ready and request.url.path == "/book" and request.url.params["token_id"] == "1":
            reads.append(v.time)
        # A clock read occurs just before the second book read. Identify the
        # final admission from its first decision book already being observed.
        fail = ready and not failures and (
            fault == "clock" and request.url.path == "/time" and len(reads) == 1
            or fault != "clock" and request.url.path == "/book"
            and request.url.params["token_id"] == "1" and len(reads) == 2)
        if fail:
            failures.append(v.time)
            if fault == "clock":
                return httpx.Response(200, json=0, request=request)
            if fault == "timeout":
                raise httpx.ReadTimeout("offline final admission outage")
            return httpx.Response(fault, json={"error": "offline final admission outage"}, request=request)
        return None

    with boundary(monkeypatch, v, outage):
        result = await run(env)
    assert failures and result.execution_status == "COMPLETED", result.stop_reason
    assert result.stop_elapsed_s == 300 and result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert len(v.post_requests) == (2 if existing_orders else 1)
    first = result.windows[1 if existing_orders else 0]["result"]
    assert first["placement_results"] == []
    assert any(w["result"]["placement_results"] for w in result.windows[(2 if existing_orders else 1):])
    assert all(row["status"] != "LIVE" for row in v.rows.values())
    assert not any(r["kind"] == "sm_placement_unknown" for r in journal_records(v.executor.journal.path))


@pytest.mark.parametrize("fault", ["auth", "identity"])
async def test_final_admission_authentication_and_identity_faults_still_stop(env, monkeypatch, fault):
    v, _ = env
    reads = []
    on_windows(v, lambda *_: None, stop_after=1)

    def reject(request):
        if v.executor is not None and request.url.path == "/book" and request.url.params["token_id"] == "1":
            reads.append(v.time)
            if len(reads) == 2:
                if fault == "auth":
                    return httpx.Response(401, json={"error": "offline unauthorized"}, request=request)
                raw = deepcopy(v.books["1"])
                raw["asset_id"] = "2"
                raw["timestamp"] = str(v.time * 1000)
                return httpx.Response(200, json=raw, request=request)
        return None

    with boundary(monkeypatch, v, reject):
        result = await run(env)
    assert len(reads) == 2 and result.execution_status == "BLOCKED"
    assert not v.post_requests and not result.skipped_quotes


@pytest.mark.parametrize("recover", [True, False])
async def test_invalid_official_clock_waits_without_buy_until_valid_or_deadline(env, monkeypatch, recover):
    v, _ = env
    v.time = START + 60
    failures = []

    def fills(venue, index):
        orders = current_orders(venue)
        venue.fill(orders["NO" if index == 1 else "YES"], f"fill-{index}", 5)

    on_windows(v, fills, stop_after=2)

    def bad_clock(request):
        if request.url.path == "/time" and v.trades and (not recover or v.time < START + 90):
            failures.append(v.time)
            return httpx.Response(200, json=0, request=request)
        return None

    with boundary(monkeypatch, v, bad_clock):
        result = await run(env)
    assert failures and result.stop_elapsed_s == 300
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert len(v.post_requests) == (2 if recover else 1)
    if recover:
        assert result.execution_status == "COMPLETED" and result.delta == 0
        assert result.windows[1]["elapsed_s"] >= 90
    else:
        assert result.execution_status == "BLOCKED"
        assert result.stop_reason == "OFFICIAL_TIME_INVALID" and result.delta == -5
