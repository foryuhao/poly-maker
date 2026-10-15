"""Prior Session time and order evidence are independent from new admission."""

from copy import deepcopy
from datetime import UTC, datetime

import httpx
import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RiskJournal
from test_rdp_sm_entry import OLD_CID, boundary, old_trade
from test_rdp_sm_historical_risk import env as history_env
from test_rdp_sm_historical_risk import safe_records, write_records
from test_rdp_sm_m1 import ADDRESS, START
from test_rdp_sm_m3_loop import on_windows


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from history_env.__wrapped__(monkeypatch, tmp_path)


def traded_records(*, start=START - 300, outcome="ACK"):
    rows = safe_records("old-trading")
    rows[0]["data"]["payload"]["slug"] = f"btc-updown-5m-{start}"
    result = rows[-1]["data"]["payload"]
    result.update(
        market_id=OLD_CID,
        total_windows=1,
        normal_windows=1,
        execution_status="INCOMPLETE",
        final_close_barrier="BLOCKED",
        stop_reason="TRADE_CONFIRMATION_UNRESOLVED",
    )
    rows[1]["data"]["payload"] = {
        "result": "BLOCKED",
        "reason": "TRADE_CONFIRMATION_UNRESOLVED",
        "capital_committed": "2.00",
    }
    quote = {
        "market_id": OLD_CID,
        "token_id": "3",
        "outcome": "YES",
        "side": "BUY",
        "price": 0.4,
        "size": 5.0,
        "window_id": "old-w1",
    }
    intent = {
        "attempt_id": "a1",
        "quote": quote,
        "outcome": "INTENT",
        "order_id": None,
        "post_started": False,
        "request_hash": None,
        "raw_response": None,
    }
    ack = {
        **intent,
        "outcome": outcome,
        "order_id": "old-order" if outcome == "ACK" else None,
        "post_started": True,
        "request_hash": "a" * 64,
        "raw_response": {"success": True, "orderID": "old-order"},
    }
    for kind, payload in reversed(
        [("sm_placement_intent", intent), ("sm_placement_ack", ack), ("sm_placement_result", ack)]
    ):
        rows.insert(
            1,
            {
                "ts": rows[0]["ts"],
                "kind": kind,
                "data": {"session_id": "old-trading", "payload": deepcopy(payload)},
            },
        )
    return rows


def test_previous_market_known_order_nonblocking_and_bytes_unchanged(tmp_path):
    path = write_records(tmp_path, traded_records())
    before = path.read_bytes()
    j = RiskJournal(tmp_path, ADDRESS)
    assert j.sequence == 0 and path.read_bytes() == before
    j.close()


@pytest.mark.parametrize(
    "fault", ["current", "future", "unknown", "incomplete", "missing_intent", "wrong_funder"]
)
def test_old_session_time_and_orders_do_not_gate_new_journal(tmp_path, fault):
    rows = traded_records(start=START if fault == "current" else START+300,
                          outcome="UNKNOWN" if fault == "unknown" else "ACK")
    if fault == "incomplete":
        rows.pop()
    elif fault == "missing_intent":
        rows.pop(1)
    elif fault == "wrong_funder":
        rows[0]["data"]["payload"]["funder"] = "0x"+"02"*20
    path = write_records(tmp_path, rows)
    before = path.read_bytes()
    j = RiskJournal(tmp_path, ADDRESS)
    assert j.sequence == 0 and path.read_bytes() == before
    j.close()


@pytest.mark.parametrize("balance,expected", [("100000000", True), ("6000000", True), ("3000000", False)])
async def test_prior_journal_order_is_not_imported_into_starting_balance(env, balance, expected):
    v, cfg = env
    v.capital["balance"] = balance
    path = write_records(cfg.paths.journal_dir, traded_records())
    before = path.read_bytes()
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert bool(v.posts) == expected, result.stop_reason
    if expected:
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert result.confirmed_fills == 0 and len(v.posts) == 2
    else:
        assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert path.read_bytes().startswith(before)


async def test_previous_market_matched_needs_no_time_or_resolution_lookup(env, monkeypatch):
    v, cfg = env
    trade = old_trade(v)
    trade["match_time"] = trade["timestamp"] = str(START - 100)
    trade["maker_orders"][0].update(price=".4", matched_amount="5")
    v.trades = [trade]
    write_records(cfg.paths.journal_dir, traded_records())
    v.capital["balance"] = "7000000"  # Starting balance covers the current pair.

    def market(request):
        if request.url.host == "gamma-api.polymarket.com" and "condition_ids" in request.url.params:
            if request.url.params.get("closed", "false") == "true":
                return httpx.Response(200, json=[], request=request)
            return httpx.Response(
                200,
                json=[
                    {
                        "conditionId": OLD_CID,
                        "slug": f"btc-updown-5m-{START - 300}",
                        "closed": False,
                        "acceptingOrders": False,
                        "umaResolutionStatus": "proposed",
                        "endDate": datetime.fromtimestamp(START, UTC).isoformat(),
                        "clobTokenIds": '["3","4"]',
                    }
                ],
                request=request,
            )

    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, market):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.confirmed_fills == 0
    assert v.trades[0]["status"] == "MATCHED"


def test_previous_journal_market_id_is_not_an_admission_input(tmp_path):
    path = write_records(tmp_path, traded_records())
    before = path.read_bytes()
    j = RiskJournal(tmp_path, ADDRESS)
    j.close()
    assert path.read_bytes() == before


def test_journal_creation_does_not_require_prior_market_expiry(tmp_path):
    path = write_records(tmp_path, traded_records())
    j = RiskJournal(tmp_path, ADDRESS)
    j.close()
    assert path.exists()
