"""Release only proven cancelled zero-fill commitments; safely restart no-POST runs."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RiskJournal
from test_rdp_sm_entry import OLD_CID, boundary
from test_rdp_sm_historical_expiry import traded_records
from test_rdp_sm_historical_risk import safe_records, write_records
from test_rdp_sm_m1 import ADDRESS, CID, SLUG, START
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def closed_records():
    rows = traded_records()
    row = {"id": "old-order", "market": OLD_CID, "asset_id": "3", "side": "BUY",
           "maker_address": ADDRESS, "price": "0.4", "original_size": "5",
           "size_matched": "0", "status": "CANCELED", "associate_trades": []}
    close = rows[-2]["data"]["payload"]
    close.clear()
    close.update(result="SAFE_TO_CONTINUE", confirmed_fills=[], proofs={"old-order": {
        "order": deepcopy(row), "trades": [], "outcome": "COMPLETE"}})
    rows[-1]["data"]["payload"].update(
        final_close_barrier="SAFE_TO_CONTINUE", execution_status="COMPLETED", stop_reason="MARKET_CUTOFF")
    rows.insert(-2, {"ts": rows[-2]["ts"], "kind": "sm_official_reconciliation",
                     "data": {"session_id": "old-trading", "payload": {
                         "order_id": "old-order", "order": row, "trades": [], "filltruth": "COMPLETE"}}})
    return rows


def read_history(tmp_path, rows):
    path = write_records(tmp_path, rows)
    before = path.read_bytes()
    journal = RiskJournal(tmp_path, ADDRESS, current_slug=SLUG, official_time=START + 60,
                          current_market_id=CID)
    try:
        return deepcopy(journal.historical_orders)
    finally:
        journal.close()
        assert path.read_bytes() == before


def journal_bytes(path):
    return Path(path).read_bytes()


def test_cancelled_zero_fill_complete_evidence_releases_only_order_cost(tmp_path):
    assert read_history(tmp_path, closed_records()) == {}


@pytest.mark.parametrize("fault", [
    "missing_proof", "missing_rest", "unresolved_rest", "inconsistent_rest",
    "blocked_final", "blocked_close", "live", "matched", "missing_associates",
    "associate_trade", "wrong_market", "wrong_token", "wrong_price", "wrong_size",
    "wrong_funder", "malformed_trade", "earlier_fill", "ws_fill", "late_live", "revived",
])
def test_incomplete_or_conflicting_close_keeps_full_reserve(tmp_path, fault):
    rows = closed_records()
    close = rows[-2]["data"]["payload"]
    proof = close["proofs"]["old-order"]
    rest = rows[-3]["data"]["payload"]
    if fault == "missing_proof":
        close["proofs"] = {}
    elif fault == "missing_rest":
        rows.pop(-3)
    elif fault == "unresolved_rest":
        rest["filltruth"] = "UNRESOLVED"
    elif fault == "inconsistent_rest":
        rest["order"]["status"] = "LIVE"
    elif fault == "blocked_final":
        rows[-1]["data"]["payload"]["final_close_barrier"] = "BLOCKED"
    elif fault == "blocked_close":
        close["result"] = "BLOCKED"
    elif fault in {"live", "matched", "missing_associates", "associate_trade", "wrong_market",
                   "wrong_token", "wrong_price", "wrong_size", "wrong_funder"}:
        row = proof["order"]
        if fault == "missing_associates":
            row.pop("associate_trades")
        else:
            key, value = {
                "live": ("status", "LIVE"), "matched": ("size_matched", "1"),
                "associate_trade": ("associate_trades", ["trade"]),
                "wrong_market": ("market", CID), "wrong_token": ("asset_id", "4"),
                "wrong_price": ("price", "0.5"), "wrong_size": ("original_size", "6"),
                "wrong_funder": ("maker_address", "0x" + "02" * 20),
            }[fault]
            row[key] = value
        rest["order"] = deepcopy(row)
    elif fault == "malformed_trade":
        proof["trades"] = rest["trades"] = [{"status": "MATCHED"}]
    elif fault == "earlier_fill":
        earlier = deepcopy(rows[-3])
        earlier["data"]["payload"]["order"]["size_matched"] = "1"
        rows.insert(-3, earlier)
    elif fault == "ws_fill":
        rows.insert(-2, {"ts": rows[-2]["ts"], "kind": "sm_trade", "data": {
            "session_id": "old-trading", "payload": {"maker_order_id": "old-order", "status": "MATCHED"}}})
    elif fault in {"late_live", "revived"}:
        rows.insert(-1 if fault == "late_live" else -2, {
            "ts": rows[-2]["ts"], "kind": "sm_order", "data": {
                "session_id": "old-trading", "payload": {
                    "order_id": "old-order", "size_matched": 0, "status": "LIVE"}}})
    orders = read_history(tmp_path, rows)
    assert Decimal(orders["old-order"]["price"]) * Decimal(orders["old-order"]["size"]) == 2


async def test_released_history_allows_real_m1_m2_m3_pair_at_six_usdc(env):
    v, cfg = env
    v.capital["balance"] = "6000000"
    path = write_records(cfg.paths.journal_dir, closed_records())
    before = path.read_bytes()
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.final_close_barrier == "SAFE_TO_CONTINUE"
    assert result.confirmed_fills == 0 and path.read_bytes().startswith(before)


async def test_real_m1_m2_m3_cancelled_no_fill_journal_does_not_recharge_next_market(env, monkeypatch):
    v, cfg = env

    def account_identity(request):
        if request.url.path.startswith("/data/order/"):
            oid = request.url.path.rsplit("/", 1)[1]
            v.rows[oid]["maker_address"] = v.funder
        return None

    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, account_identity):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.confirmed_fills == 0
    path = v.executor.journal.path
    before = journal_bytes(path)
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS,
                          current_slug=f"btc-updown-5m-{START + 300}",
                          official_time=START + 360, current_market_id=OLD_CID)
    assert journal.historical_orders == {} and journal_bytes(path) == before
    journal.close()


async def test_capital_failure_zero_post_safe_close_allows_same_market_restart(env, monkeypatch):
    v, cfg = env
    v.capital["balance"] = "6000000"

    def capital_drops(request):
        if request.url.path == "/balance-allowance" and v.executor is not None:
            v.capital["balance"] = "1000000"
        return None

    with monkeypatch.context() as scoped, boundary(scoped, v, capital_drops):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.stop_reason == "OFFICIAL_CAPITAL_INSUFFICIENT"
    assert result.total_windows == result.blocked_windows and result.total_windows > 1 and not v.posts
    assert result.stop_elapsed_s == 300
    assert result.final_close_barrier == "SAFE_TO_CONTINUE"
    path = v.executor.journal.path
    before = journal_bytes(path)
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS, current_slug=SLUG,
                          current_market_id=CID, official_time=START + 60)
    assert journal.historical_orders == {} and journal_bytes(path) == before
    journal.close()
    v.capital["balance"] = "6000000"
    # A separate offline invocation exercises same-market journal admission;
    # the first invocation has already waited through its complete market.
    v.time = START + 60
    on_windows(v, lambda *_: None, stop_after=1)
    again = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert again.execution_status == "COMPLETED", again.stop_reason
    assert len(v.posts) == 2 and again.final_close_barrier == "SAFE_TO_CONTINUE"
    assert journal_bytes(path).startswith(before)


def test_zero_submission_does_not_release_original_matched_reserve(tmp_path):
    rows = safe_records(retained=True)
    rows[-1]["data"]["payload"]["stop_reason"] = "OFFICIAL_CAPITAL_INSUFFICIENT"
    path = write_records(tmp_path, rows)
    before = path.read_bytes()
    journal = RiskJournal(tmp_path, ADDRESS)
    assert sum(journal.historical_reserve.values()) == Decimal("10.15")
    assert path.read_bytes() == before
    journal.close()
