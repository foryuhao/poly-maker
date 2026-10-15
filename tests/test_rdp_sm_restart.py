"""Independent Session startup: no history reads or cross-market recovery calls."""

import asyncio
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RiskJournal
from test_rdp_sm_entry import OLD_CID, boundary, old_trade
from test_rdp_sm_historical_expiry import traded_records
from test_rdp_sm_historical_risk import write_records
from test_rdp_sm_m1 import ADDRESS, SLUG, START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("target", [0, 1])
@pytest.mark.parametrize("fault", ["complete", "no_result", "header_only", "unknown_post", "unknown_merge",
    "bad_header", "foreign_account", "torn_json", "duplicate_key", "bad_utf8", "inflated_budget",
    "live_order_event", "foreign_session", "current_market", "bad_pnl", "pending_cost"])
async def test_other_session_history_is_never_opened_and_cannot_gate_startup(env, monkeypatch, target, fault):
    v, cfg = env
    cfg.rdp_sm.target_delta = Decimal(target)
    rows = traded_records()
    if fault == "no_result":
        rows.pop()
    elif fault == "header_only":
        rows = rows[:1]
    elif fault == "unknown_post":
        rows[2]["data"]["payload"].update(outcome="UNKNOWN", order_id=None)
    elif fault == "unknown_merge":
        rows.append({"ts": rows[-1]["ts"], "kind": "sm_merge_intent", "data": {}})
    elif fault == "bad_header":
        rows[0]["data"]["payload"] = {"invalid": True}
    elif fault == "foreign_account":
        rows[0]["data"]["payload"]["funder"] = "0x" + "99" * 20
    elif fault == "inflated_budget":
        rows[0]["data"]["payload"]["starting_spendable_usdc"] = 999999
    elif fault == "live_order_event":
        rows.insert(1, {"kind": "sm_order", "data": {"status": "LIVE"}})
    elif fault == "foreign_session":
        rows[1]["data"]["session_id"] = "someone-else"
    elif fault == "current_market":
        rows[0]["data"]["payload"]["slug"] = SLUG
    elif fault == "bad_pnl":
        rows[-1]["data"]["payload"]["sizing_report"]["intentional_residual_shares"] = 999
    elif fault == "pending_cost":
        rows[0]["data"]["payload"]["historical_matched"] = {"unknown": {"pending_cost_reserved_usd": "NaN"}}
    old = write_records(cfg.paths.journal_dir, rows)
    if fault == "torn_json":
        old.write_bytes(old.read_bytes() + b'{"kind":')
    elif fault == "duplicate_key":
        old.write_bytes(b'{"data": {}, "data": null}\n')
    elif fault == "bad_utf8":
        old.write_bytes(b"\xff\n")
    prior_session = old.with_name(f"rdp_sm_{ADDRESS.lower()}_previous.jsonl")
    prior_session.write_bytes(old.read_bytes())
    before = {path: path.read_bytes() for path in (old, prior_session)}
    actual_open = Path.open
    def guarded_open(path, *args, **kwargs):
        assert path not in before, "another Session Journal was opened"
        return actual_open(path, *args, **kwargs)
    def no_history_http(request):
        assert "condition_ids" not in request.url.params, "historical market lookup"
        assert request.url.path != "/data/order/old-order", "historical order lookup"
        if request.url.path == "/data/trades":
            assert request.url.params.get("market") != OLD_CID
        return None
    v.trades = [old_trade(v)]
    on_windows(v, lambda *_: None, stop_after=1)
    with monkeypatch.context() as patch, boundary(patch, v, no_history_http):
        patch.setattr(Path, "open", guarded_open)
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.confirmed_fills == 0
    assert all(path.read_bytes() == data for path, data in before.items())
    assert Path(v.executor.journal.path) not in before
    records = journal_records(v.executor.journal.path)
    assert {r["data"]["session_id"] for r in records} == {result.session_id}
    assert not any(r["kind"] == "sm_session_interrupted_reconciled" for r in records)
    header = records[0]["data"]["payload"]
    assert header["historical_matched"] == {} and header["historical_pending_cost_reserved_usd"] == 0
    assert v.executor.reads.account_policy.pending == {}


@pytest.mark.parametrize("failure", [404, 429, 500, "timeout"])
async def test_previous_order_read_availability_is_irrelevant(env, monkeypatch, failure):
    v, cfg = env
    old = write_records(cfg.paths.journal_dir, traded_records()[:-1])
    before = old.read_bytes()
    queried = []
    def response(request):
        if request.url.path == "/data/order/old-order":
            queried.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("old order unavailable", request=request)
            return httpx.Response(failure, request=request)
        return None
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, response):
        result = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert result.execution_status == "COMPLETED" and len(v.posts) == 2
    assert queried == [] and old.read_bytes() == before


def test_journals_are_independent_and_same_session_id_cannot_reopen(tmp_path):
    first = RiskJournal(tmp_path, ADDRESS, session_id="first")
    second = RiskJournal(tmp_path, ADDRESS, session_id="second")
    try:
        first.write("sm_session", {"session_id": "first", "payload": {}}, 1)
        second.write("sm_session", {"session_id": "second", "payload": {}}, 1)
        assert first.path != second.path and first.sequence == second.sequence == 1
        with pytest.raises(FileExistsError):
            RiskJournal(tmp_path, ADDRESS, session_id="first")
        with pytest.raises(ValueError, match="SESSION_JOURNAL_IDENTITY_CONFLICT"):
            first.write("sm_trade", {"session_id": "second", "payload": {}}, 2)
        assert first.sequence == 1
    finally:
        first.close()
        second.close()
    with pytest.raises(FileExistsError):
        RiskJournal(tmp_path, ADDRESS, session_id="first")


@pytest.mark.parametrize("sid", ["../escape", "/absolute", "", "has space", "x\ny", "x"*129])
def test_session_journal_rejects_unsafe_identity_without_opening_history(tmp_path, sid):
    with pytest.raises(ValueError, match="SESSION_ID_INVALID"):
        RiskJournal(tmp_path, ADDRESS, session_id=sid)
    assert list(tmp_path.iterdir()) == []


async def test_new_invocation_does_not_inherit_previous_unknown_placement(env):
    v, cfg = env
    v.replies = [{"success": True, "orderID": "o-1"}, {"success": True}]
    first = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert first.final_close_barrier == "BLOCKED" and len(v.post_requests) == 1
    old_path = v.executor.journal.path
    old_bytes = await asyncio.to_thread(Path(old_path).read_bytes)
    old_ids = set(v.executor.known)
    # The fresh official account has no resting orders. The old UNKNOWN
    # Journal event itself must not become a new Session's placement state.
    for row in v.rows.values():
        row["status"] = "CANCELED"
    v.replies, v.time = [], START + 60
    on_windows(v, lambda *_: None, stop_after=1)
    second = await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)
    assert second.execution_status == "COMPLETED", second.stop_reason
    assert first.session_id != second.session_id and old_path != v.executor.journal.path
    assert len(v.post_requests) == 2 and second.confirmed_fills == 0
    assert not old_ids & v.executor.known
    assert await asyncio.to_thread(Path(old_path).read_bytes) == old_bytes
    assert all(a.outcome == "ACK" for a in v.executor.attempts)
