"""Read-only watch separates CA openings from high-price TP/SL and dedupes fills."""
from decimal import Decimal

from ops.ca_execution_watch import Watch


def record(watch, kind, data, stamp=100):
    watch.apply({'kind': kind, 'ts': stamp, 'data': {'session_id': 'sid', 'payload': data}}, 'journal.jsonl')


def setup_watch():
    watch = Watch()
    record(watch, 'sm_session', {'slug': 'btc-updown-5m-0', 'ca_matched_tp': {'ttl_sec': 30},
                               'ca_taker': {'entry_price_ceiling_enabled': True, 'entry_price_ceiling': '.90'}})
    record(watch, 'sm_prepared', {'candidates': [{'order_ids': ['open', 'tp', 'sl', 'sl2'], 'quotes': [
        {'window_id': 'sid:w1', 'token_id': 'no', 'outcome': 'NO'},
        {'window_id': 'sid:w1:ca-tp', 'token_id': 'yes', 'outcome': 'YES'},
        {'window_id': 'sid:w1:ca-expiry-exit', 'token_id': 'yes', 'outcome': 'YES'},
        {'window_id': 'sid:w1:ca-expiry-exit', 'token_id': 'yes', 'outcome': 'YES'}]}]})
    trade(watch, 'open', '5', 100)
    return watch


def trade(watch, oid, qty, stamp, *, status='MATCHED', price=None, tid=None):
    record(watch, 'sm_trade', {'maker_order_id': oid, 'trade_id': tid or oid,
           'token_id': 'no' if oid == 'open' else 'yes', 'status': status,
           'size': qty, 'price': price or ('.22' if oid == 'open' else '.96')}, stamp)


def codes(watch, now, *, heartbeat=True):
    if heartbeat:
        record(watch, 'sm_sync_cache_read', {}, now)
    return {a['code'] for a in watch.snapshot(now)['alerts']}


def test_deadline_partial_and_late_recovery_without_misclassifying_96_sl():
    w = setup_watch()
    assert not codes(w, 135)
    trade(w, 'sl', '3.95', 133)
    assert codes(w, 136) == {'LATE_EXIT'}
    details = w.snapshot(136)['cycles'][0]
    assert details['original_unhedged_shares'] == '1.05' and details['sl_shares'] == '3.95'
    trade(w, 'sl2', '1.052083', 140)
    assert codes(w, 141) == {'LATE_CLOSE'}
    details = w.snapshot(141)['cycles'][0]
    assert details['original_unhedged_shares'] == '0'
    assert details['extra_reverse_shares'] == '0.002083'


def test_accepted_dust_and_reverse_excess_do_not_create_another_opening():
    for size in ['4.994263', '100']:
        w = setup_watch()
        trade(w, 'tp', size, 125)
        assert not codes(w, 150)


def test_confirmation_updates_are_not_double_counted_and_failed_exit_reopens_risk():
    w = setup_watch()
    trade(w, 'tp', '5', 125)
    trade(w, 'tp', '5', 130, status='MINED')
    trade(w, 'tp', '5', 134, status='CONFIRMED')
    assert not codes(w, 150)
    assert w.snapshot(150)['cycles'][0]['covered_at'] == 125
    trade(w, 'tp', '5', 151, status='FAILED')
    assert codes(w, 152) == {'LATE_EXIT'}
    assert w.snapshot(152)['cycles'][0]['covered_at'] is None


def test_misleading_completed_and_empty_abnormal_sessions_both_alert():
    w = setup_watch()
    record(w, 'sm_run_result', {'execution_status': 'COMPLETED', 'stop_reason': 'MARKET_CUTOFF'}, 132)
    assert codes(w, 132) == {'UNCOVERED_AT_END'}
    w = Watch()
    record(w, 'sm_run_result', {'execution_status': 'INCOMPLETE', 'stop_reason': 'BROKEN'}, 132)
    assert codes(w, 132) == {'ABNORMAL_EXIT'}


def test_open_price_ceiling_is_checked_only_on_filled_openings():
    w = setup_watch()
    trade(w, 'open', '5', 101, price='.96')
    assert codes(w, 102) == {'OPEN_PRICE_CEILING'}
    assert Decimal(w.snapshot(102)['cycles'][0]['opening_shares']) == 5


def test_process_without_final_record_after_market_end_is_detected():
    w = Watch()
    record(w, 'sm_session', {'slug': 'btc-updown-5m-0'})
    assert not codes(w, 320)
    assert codes(w, 321) == {'MISSING_RUN_RESULT'}


def test_stalled_journal_during_market_is_not_hidden_by_absence_of_trades():
    w = Watch()
    record(w, 'sm_session', {'slug': 'btc-updown-5m-0'})
    assert not codes(w, 120, heartbeat=False)
    assert codes(w, 121, heartbeat=False) == {'LOG_STALLED'}


def test_unknown_open_is_not_silently_treated_as_zero_fill():
    w = setup_watch()
    # A second signed opening has been sent but has neither trade nor no-fill proof.
    record(w, 'sm_prepared', {'candidates': [{'order_ids': ['unknown'], 'quotes': [
        {'window_id': 'sid:w2', 'token_id': 'no', 'outcome': 'NO'}]}]})
    record(w, 'sm_placement_result', {'order_id': 'unknown', 'post_started': True, 'outcome': 'UNKNOWN'})
    trade(w, 'tp', '5', 120)
    assert codes(w, 136) == {'OPEN_ORDER_UNRESOLVED'}
    record(w, 'sm_order_rejection_proven', {'order_id': 'unknown'}, 137)
    assert not codes(w, 138)


def test_only_positive_zero_fill_proof_clears_pending_open():
    w = setup_watch()
    record(w, 'sm_prepared', {'candidates': [{'order_ids': ['pending'], 'quotes': [
        {'window_id': 'sid:w2', 'token_id': 'no', 'outcome': 'NO'}]}]})
    record(w, 'sm_placement_result', {'order_id': 'pending', 'post_started': True, 'outcome': 'ACK'})
    trade(w, 'tp', '5', 120)
    record(w, 'sm_close_barrier', {'result': 'SAFE_TO_CONTINUE', 'proofs': {}}, 130)
    assert codes(w, 136) == {'OPEN_ORDER_UNRESOLVED'}
    record(w, 'sm_close_barrier', {'result': 'SAFE_TO_CONTINUE', 'proofs': {
        'pending': {'order': {'size_matched': '0'}}}}, 137)
    assert not codes(w, 138)
