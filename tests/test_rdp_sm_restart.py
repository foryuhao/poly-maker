"""Ctrl+C restart: actual Journal/M1/M2/SDK, official HTTP mocked, no real I/O."""

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from polymaker.rdp_sm_entry import run_current_market
from polymaker.rdp_sm_execution import RiskJournal, WindowResult
from polymaker.rdp_sm_restart import ARCHIVE_EVENT
from test_rdp_sm_entry import OLD_CID, boundary
from test_rdp_sm_historical_expiry import traded_records
from test_rdp_sm_historical_risk import write_records
from test_rdp_sm_m1 import ADDRESS, CID, SLUG, START
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_m3_loop import env as loop_env
from test_rdp_sm_m3_loop import on_windows


@pytest.fixture
def env(monkeypatch, tmp_path):
    yield from loop_env.__wrapped__(monkeypatch, tmp_path)


def interrupted(env, *, filled=False, status="CONFIRMED"):
    v, cfg = env
    rows = traded_records()[:-1]  # Actual interruption: no sm_run_result.
    path = write_records(cfg.paths.journal_dir, rows)
    v.rows["old-order"] = {"id": "old-order", "asset_id": "3", "market": OLD_CID,
        "side": "BUY", "price": ".4", "original_size": "5", "size_matched": "0",
        "status": "CANCELED", "associate_trades": []}
    if filled:
        v.fill("old-order", "old-fill", 5, status)
        v.trades[-1]["market"] = OLD_CID
        v.trades[-1]["trader_side"] = "MAKER"
        v.trades[-1]["maker_orders"][0]["maker_address"] = ADDRESS
        v.trades[-1]["match_time"] = v.trades[-1]["timestamp"] = str(START - 100)
        v.rows["old-order"]["status"] = "MATCHED"
    return v, cfg, path, path.read_bytes()


async def run(env):
    v, cfg = env
    return await run_current_market(cfg, sleep=v.sleep, monotonic=lambda: v.mono)


def historical_market(request):
    if request.url.host == "gamma-api.polymarket.com" and (
            "condition_ids" in request.url.params or request.url.params.get("slug") == f"btc-updown-5m-{START - 300}"):
        return httpx.Response(200, json=[{"conditionId": OLD_CID,
            "slug": f"btc-updown-5m-{START - 300}", "closed": False, "acceptingOrders": False,
            "umaResolutionStatus": "proposed", "endDate": datetime.fromtimestamp(START, UTC).isoformat(),
            "clobTokenIds": '["3","4"]'}], request=request)
    return None


@pytest.mark.parametrize("filled,status", [(False, "CONFIRMED"), (True, "CONFIRMED"),
    (True, "MATCHED"), (True, "MINED"), (True, "RETRYING")])
async def test_ctrl_c_expired_session_recovers_without_rewriting_history(env, monkeypatch, filled, status):
    v, cfg, path, before = interrupted(env, filled=filled, status=status)
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, historical_market):
        result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert len(v.posts) == 2 and result.confirmed_fills == 0
    assert path.read_bytes().startswith(before)
    rows = journal_records(path)
    archive = next(r for r in rows if r["kind"] == ARCHIVE_EVENT)
    assert archive["data"]["session_id"] == "old-trading"
    payload = archive["data"]["payload"]
    assert payload["summary"]["execution_status"] == "INCOMPLETE"
    assert payload["summary"]["final_close_barrier"] == "BLOCKED"
    assert payload["orders"]["old-order"]["status"] == ("MATCHED" if filled else "CANCELED")
    assert "old-order" not in v.executor.known
    assert "old-order" not in v.executor.gateway._client.known_ids
    assert not any("old-order" in ids for ids in v.cancels)
    assert len([r for r in rows if r["kind"] == "sm_run_result" and r["data"]["session_id"] == "old-trading"]) == 0
    # Marker replay remains valid; no repeated recovery GET is needed.
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS, current_slug=f"btc-updown-5m-{START + 300}",
        current_market_id="0x" + "ef" * 32, official_time=START + 360, recover_interrupted=True)
    assert journal.interrupted_session is None
    assert "old-trading" in journal.historical_session_ids
    journal.close()


@pytest.mark.parametrize("fault", ["live", "missing_trade", "wrong_token", "wrong_market", "wrong_price",
    "wrong_size", "bad_associates", "get_order_fails", "trade_pagination", "missing_previous_fill"])
async def test_official_uncertainty_blocks_without_appending_archive_or_new_orders(env, monkeypatch, fault):
    v, _, path, before = interrupted(env, filled=True)
    if fault == "live":
        v.rows["old-order"]["status"] = "LIVE"
    elif fault == "missing_trade":
        v.trades = []
    elif fault in {"wrong_token", "wrong_market", "wrong_price", "wrong_size", "bad_associates"}:
        key, value = {"wrong_token": ("asset_id", "99"), "wrong_market": ("market", CID),
            "wrong_price": ("price", ".5"), "wrong_size": ("original_size", "6"),
            "bad_associates": ("associate_trades", [])}[fault]
        v.rows["old-order"][key] = value
    elif fault == "missing_previous_fill":
        records = journal_records(path)
        records.insert(-1, {"ts": records[-1]["ts"], "kind": "sm_official_reconciliation",
            "data": {"session_id": "old-trading", "payload": {
                "order_id": "old-order", "order": deepcopy(v.rows["old-order"]),
                "trades": deepcopy(v.trades), "filltruth": "COMPLETE"}}})
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        before = path.read_bytes()
        v.trades = []
        v.rows["old-order"].update(status="CANCELED", size_matched="0", associate_trades=[])
    def fail(request):
        if fault == "get_order_fails" and request.url.path == "/data/order/old-order":
            return httpx.Response(404, json={"error": "not found"}, request=request)
        if fault == "trade_pagination" and request.url.path == "/data/trades":
            return httpx.Response(200, json={"data": [], "next_cursor": None}, request=request)
        return historical_market(request)
    with boundary(monkeypatch, v, fail):
        result = await run(env)
    assert result.execution_status != "COMPLETED"
    assert not v.posts and not v.cancels
    assert path.read_bytes() == before


@pytest.mark.parametrize('fault', ['ack', 'unknown', 'yes_inventory', 'no_inventory', 'delta'])
async def test_interrupted_window_cannot_hide_unrecorded_placement_or_inventory(env, fault):
    v, _, path, _ = interrupted(env)
    rows = journal_records(path)[:1]
    p = WindowResult('old-trading', 'unbound-window', OLD_CID, rows[0]['ts']).to_dict()
    if fault in {'ack', 'unknown'}:
        p['placement_results'] = [{'attempt_id': 'unbound', 'order_id': 'unbound' if fault == 'ack' else None,
            'outcome': fault.upper(), 'post_started': True}]
    else:
        p[fault] = 1
    rows.append({'ts': rows[-1]['ts'], 'kind': 'sm_window_result',
        'data': {'session_id': 'old-trading', 'payload': p}})
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before = path.read_bytes()
    result = await run(env)
    assert result.execution_status == 'BLOCKED' and not v.posts and not v.cancels
    assert path.read_bytes() == before


@pytest.mark.parametrize('outcome', ['ACK', 'UNKNOWN'])
async def test_interrupted_close_cannot_hide_unrecorded_post(env, outcome):
    v, _, path, _ = interrupted(env)
    rows = journal_records(path)[:1]
    rows.append({'ts': rows[0]['ts'], 'kind': 'sm_close_barrier', 'data': {
        'session_id': 'old-trading', 'payload': {'result': 'BLOCKED', 'capital_committed': '0',
            'attempts': [{'attempt_id': 'unbound', 'outcome': outcome, 'post_started': True,
                'order_id': None}]}}})
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before = path.read_bytes()
    result = await run(env)
    assert result.execution_status == 'BLOCKED' and not v.posts and not v.cancels
    assert path.read_bytes() == before
    assert ARCHIVE_EVENT not in [r["kind"] for r in journal_records(path)]


@pytest.mark.parametrize("fault", ["current", "future", "unknown", "no_ack", "bad_funder",
    "bad_signer", "unknown_merge", "torn_json", "duplicate_key"])
async def test_unrecoverable_history_still_fails_closed(env, fault):
    v, cfg, path, _ = interrupted(env)
    records = journal_records(path)
    if fault in {"current", "future"}:
        records[0]["data"]["payload"]["slug"] = SLUG if fault == "current" else f"btc-updown-5m-{START + 300}"
    elif fault == "unknown":
        records[2]["data"]["payload"].update(outcome="UNKNOWN", order_id=None)
    elif fault == "no_ack":
        records.pop(2)
    elif fault in {"bad_funder", "bad_signer"}:
        records[0]["data"]["payload"]["funder" if fault == "bad_funder" else "signer"] = "0x" + "02" * 20
    elif fault == "unknown_merge":
        records.append({"ts": records[-1]["ts"], "kind": "sm_merge_intent",
            "data": {"session_id": "old-trading", "payload": {}}})
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    if fault == "torn_json":
        with path.open('a') as stream:
            stream.write('{"kind":')
    if fault == "duplicate_key":
        path.write_text(path.read_text().replace('"side": "BUY"', '"side": "BUY", "side": "SELL"', 1))
    before = path.read_bytes()
    result = await run(env)
    assert result.execution_status == "BLOCKED", result.stop_reason
    assert not v.posts and not v.cancels and path.read_bytes() == before


async def test_pure_session_header_can_be_archived_without_inventing_orders(env, monkeypatch):
    v, cfg, path, _ = interrupted(env)
    header = journal_records(path)[0]
    path.write_text(json.dumps(header)+'\n')
    before = path.read_bytes()
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, historical_market):
        result = await run(env)
    assert result.execution_status == "COMPLETED", result.stop_reason
    assert path.read_bytes().startswith(before)
    event = next(r for r in journal_records(path) if r['kind'] == ARCHIVE_EVENT)
    assert event['data']['payload']['orders'] == {}
    assert event['data']['payload']['summary']['market_id'] == OLD_CID


async def test_existing_history_requires_recovery_before_new_session_write(env):
    _, cfg, path, before = interrupted(env)
    journal = RiskJournal(cfg.paths.journal_dir, ADDRESS, current_slug=SLUG,
        current_market_id=CID, official_time=START+60, recover_interrupted=True)
    try:
        with pytest.raises(ValueError, match="INTERRUPTED_SESSION_NOT_RECONCILED"):
            journal.write('sm_session', {}, START+60)
        assert path.read_bytes() == before
    finally:
        journal.close()


async def test_durable_ack_recovers_when_ctrl_c_prevents_placement_result(env):
    v, _, path, _ = interrupted(env)
    records = [r for r in journal_records(path) if r['kind'] != 'sm_placement_result']
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))
    before = path.read_bytes()
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run(env)
    assert result.execution_status == 'COMPLETED', result.stop_reason
    assert path.read_bytes().startswith(before)
    assert any(r['kind'] == ARCHIVE_EVENT for r in journal_records(path))


@pytest.mark.parametrize('fault', ['prefix', 'proof', 'status', 'unknown_field'])
async def test_archival_marker_tampering_does_not_bypass_history(env, fault):
    v, cfg, path, _ = interrupted(env)
    on_windows(v, lambda *_: None, stop_after=1)
    result = await run(env)
    assert result.execution_status == 'COMPLETED', result.stop_reason
    rows = journal_records(path)
    marker = next(r for r in rows if r['kind'] == ARCHIVE_EVENT)['data']['payload']
    if fault == 'prefix':
        marker['prefix_sha256'] = '0' * 64
    elif fault == 'proof':
        marker['orders']['old-order']['status'] = 'LIVE'
    elif fault == 'status':
        marker['summary']['execution_status'] = 'COMPLETED'
    else:
        marker['override'] = True
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match='HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW'):
        RiskJournal(cfg.paths.journal_dir, ADDRESS, current_slug=f'btc-updown-5m-{START+300}',
                    current_market_id='0x'+'ef'*32, official_time=START+360, recover_interrupted=True)
    assert path.read_bytes() == before


async def test_all_rejected_attempts_recover_without_inventing_accepted_orders(env, monkeypatch):
    v, _, path, _ = interrupted(env)
    rows = [r for r in journal_records(path) if r['kind'] != 'sm_placement_ack']
    result = next(r for r in rows if r['kind'] == 'sm_placement_result')['data']['payload']
    result.update(outcome='REJECTED', order_id=None, raw_response={'success': False, 'errorMsg': 'post only'})
    next(r for r in rows if r['kind'] == 'sm_close_barrier')['data']['payload']['capital_committed'] = '0'
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before = path.read_bytes()
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, historical_market):
        outcome = await run(env)
    assert outcome.execution_status == 'COMPLETED', outcome.stop_reason
    assert path.read_bytes().startswith(before)
    marker = next(r for r in journal_records(path) if r['kind'] == ARCHIVE_EVENT)
    assert marker['data']['payload']['orders'] == {}


@pytest.mark.parametrize('fault', ['missing_balance', 'missing_budget', 'extra', 'negative', 'nan',
    'inflated_budget', 'pending_shape', 'pending_cost', 'preflight_unknown'])
@pytest.mark.parametrize('has_intent', [False, True])
async def test_interrupted_headers_keep_original_strict_schema_and_budget_checks(env, fault, has_intent):
    v, _, path, _ = interrupted(env)
    rows = journal_records(path)
    if not has_intent:
        rows = rows[:1]
    h = rows[0]['data']['payload']
    if fault == 'missing_balance':
        del h['starting_official_balance_usdc']
    elif fault == 'missing_budget':
        del h['starting_spendable_usdc']
    elif fault == 'extra':
        h['corrupt_unknown_field'] = True
    elif fault in {'negative', 'nan', 'inflated_budget'}:
        h['starting_spendable_usdc'] = {'negative': -1, 'nan': float('nan'), 'inflated_budget': 1000}[fault]
    elif fault in {'pending_shape', 'pending_cost'}:
        h['historical_matched'] = {'pending': {'status': 'MATCHED', 'decision': 'HISTORICAL_MATCHED_NONBLOCKING',
            'pending_cost_reserved_usd': 'NaN' if fault == 'pending_cost' else '1', 'redeem': 'NOT_VERIFIED'}}
        if fault == 'pending_shape':
            h['historical_matched']['pending']['extra'] = True
    else:
        h['preflight_account_incomplete'] = 'OPEN_ORDERS_PAGINATION_INCOMPLETE'
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before = path.read_bytes()
    result = await run(env)
    assert result.execution_status == 'BLOCKED' and not v.posts and not v.cancels
    assert path.read_bytes() == before


@pytest.mark.parametrize('fault', ['time', 'owner', 'role', 'gamma_tokens', 'gamma_time'])
async def test_pending_seen_only_during_recovery_must_pass_account_policy(env, monkeypatch, fault):
    v, _, path, before = interrupted(env, filled=True, status='MATCHED')
    if fault == 'time':
        v.trades[0]['match_time'] = str(START + 1)
    elif fault == 'owner':
        v.trades[0]['maker_orders'][0]['maker_address'] = '0x' + '99' * 20
    elif fault == 'role':
        v.trades[0]['trader_side'] = 'TAKER'

    def response(request):
        if request.url.path == '/data/trades' and 'market' not in request.url.params:
            return httpx.Response(200, json={'data': [], 'next_cursor': 'LTE='}, request=request)
        raw = historical_market(request)
        if raw is not None and fault in {'gamma_tokens', 'gamma_time'}:
            data = raw.json()
            if fault == 'gamma_tokens':
                data[0]['clobTokenIds'] = '["99","100"]'
            else:
                data[0]['endDate'] = datetime.fromtimestamp(START + 300, UTC).isoformat()
            return httpx.Response(200, json=data, request=request)
        return raw
    with boundary(monkeypatch, v, response):
        result = await run(env)
    assert result.execution_status == 'BLOCKED', result.stop_reason
    assert not v.posts and not v.cancels and path.read_bytes() == before


async def test_recovery_only_pending_retained_across_empty_pages_and_process_restart(env, monkeypatch):
    v, _, path, before = interrupted(env, filled=True, status='MATCHED')

    def response(request):
        if request.url.path == '/data/trades' and 'market' not in request.url.params:
            return httpx.Response(200, json={'data': [], 'next_cursor': 'LTE='}, request=request)
        return historical_market(request)
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, response):
        result = await run(env)
    assert result.execution_status == 'COMPLETED', result.stop_reason
    assert path.read_bytes().startswith(before)
    policy = v.executor.reads.account_policy
    assert policy.pending['old-fill'] == v.trades[0]
    assert policy.historical['old-fill']['pending_cost_reserved_usd'] == '2.00'
    assert 'old-order' not in v.executor.gateway._client.known_ids
    # Simulate death immediately after the durable marker, before a new header.
    rows = journal_records(path)
    marker_index = next(i for i, r in enumerate(rows) if r['kind'] == ARCHIVE_EVENT)
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows[:marker_index+1]))
    preserved = path.read_bytes()
    v.time, v.mono, v.trades = START + 60, 0, []
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, response):
        result = await run(env)
    assert result.execution_status == 'COMPLETED', result.stop_reason
    assert path.read_bytes().startswith(preserved)
    assert v.executor.reads.account_policy.historical['old-fill']['pending_cost_reserved_usd'] == '2.00'
    assert v.executor.reads.account_policy.pending['old-fill']['status'] == 'MATCHED'
    assert not any('old-order' in ids for ids in v.cancels)


@pytest.mark.parametrize('closed', [True, False])
async def test_header_only_uses_historical_metadata_for_closed_and_unclosed_markets(env, monkeypatch, closed):
    v, _, path, _ = interrupted(env)
    path.write_text(json.dumps(journal_records(path)[0])+'\n')
    queries = []
    def response(request):
        raw = historical_market(request)
        if raw is not None:
            queries.append(dict(request.url.params))
            if request.url.params.get('closed') != str(closed).lower():
                return httpx.Response(200, json=[], request=request)
        return raw
    on_windows(v, lambda *_: None, stop_after=1)
    with boundary(monkeypatch, v, response):
        result = await run(env)
    assert result.execution_status == 'COMPLETED', result.stop_reason
    assert queries[0]['closed'] == 'true'
    assert queries[-1]['closed'] == str(closed).lower()


@pytest.mark.parametrize('fault', [None, 'incomplete', 'cash'])
async def test_interrupted_completed_merge_reuses_receipts_and_never_repeats_merge(env, fault):
    from polymaker.rdp_sm_execution import _ExecutionReads
    from polymaker.rdp_sm_restart import reconcile_interrupted
    from test_rdp_sm_m3_loop import current_orders

    v, cfg = env
    def fill(venue, index):
        if index == 1:
            ids = current_orders(venue)
            venue.fill(ids['YES'], 'merged-yes', 5)
            venue.fill(ids['NO'], 'merged-no', 5)
    on_windows(v, fill, stop_after=1)
    result = await run(env)
    assert result.execution_status == 'COMPLETED' and result.merged_pairs == 5
    path = Path(v.executor.journal.path)
    rows = [r for r in journal_records(path) if r['kind'] != 'sm_run_result']
    if fault == 'incomplete':
        rows = [r for r in rows if r['kind'] != 'sm_merge_result']
    elif fault == 'cash':
        next(r for r in rows if r['kind'] == 'sm_merge_result')['data']['payload']['collateral_after_raw'] += 1
    await asyncio.to_thread(path.write_text, ''.join(json.dumps(r)+'\n' for r in rows))
    before, submissions = await asyncio.to_thread(path.read_bytes), list(v.merge_submissions)
    args = dict(current_slug=f'btc-updown-5m-{START+300}',
        current_market_id=OLD_CID, official_time=START+360, recover_interrupted=True)
    if fault:
        with pytest.raises(RuntimeError, match='HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW'):
            RiskJournal(cfg.paths.journal_dir, ADDRESS, **args)
        assert await asyncio.to_thread(path.read_bytes) == before
    else:
        j = RiskJournal(cfg.paths.journal_dir, ADDRESS, **args)
        v.time = START + 360
        reads = _ExecutionReads(cfg)
        try:
            await reads.connect_account()
            await reconcile_interrupted(j, reads, {})
        finally:
            reads.close()
            j.close()
        assert (await asyncio.to_thread(path.read_bytes)).startswith(before)
        marker = journal_records(path)[-1]['data']['payload']
        assert marker['summary']['merged_pairs'] == 5
        j = RiskJournal(cfg.paths.journal_dir, ADDRESS, **args)
        assert j.interrupted_session is None
        j.close()
    assert v.merge_submissions == submissions


@pytest.mark.parametrize('kind,payload', [
    ('sm_trade', {'maker_order_id': 'unbound'}),
    ('sm_order', {'order_id': 'unbound'}),
    ('sm_official_reconciliation', {'order_id': 'unbound'}),
    ('sm_cancel_ack', {'ids': ['unbound']}),
    ('sm_close_barrier', {'proofs': {'unbound': {}}}),
    ('sm_close_barrier', {'confirmed_fills': [{'maker_order_id': 'unbound'}]}),
    ('sm_close_barrier', {'attempts': [{'order_id': 'unbound'}]}),
    ('sm_window_result', {'known_order_ids': ['unbound']}),
])
@pytest.mark.parametrize('has_ack', [False, True])
async def test_interrupted_orphan_execution_evidence_fails_closed(env, kind, payload, has_ack):
    v, _, path, _ = interrupted(env)
    rows = journal_records(path)
    if not has_ack:
        rows = rows[:1]
    rows.append({'ts': rows[-1]['ts'], 'kind': kind,
        'data': {'session_id': 'old-trading', 'payload': payload}})
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    before = path.read_bytes()
    result = await run(env)
    assert result.execution_status == 'BLOCKED' and not v.posts and not v.cancels
    assert path.read_bytes() == before
