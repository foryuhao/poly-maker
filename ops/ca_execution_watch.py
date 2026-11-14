"""Read-only CA journal watch. No credentials, orders, or strategy imports.

Run: .venv/bin/python ops/ca_execution_watch.py --project-dir .
Writes research_outputs/ca_execution_watch/status.json and incidents.jsonl.
This process records incidents; it cannot wake a Codex chat or repair trading.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

ZERO = Decimal(0)
TOLERANCE = Decimal('.01')
ACTIVE = {'MATCHED', 'MINED', 'CONFIRMED'}
KINDS = {'sm_session', 'sm_prepared', 'sm_trade', 'sm_run_result', 'sm_ca_expiry_exit_result',
         'sm_placement_result', 'sm_order_rejection_proven', 'sm_close_barrier'}


@dataclass
class Cycle:
    window: str
    session: str
    fills: dict = field(default_factory=dict)
    opened_at: float | None = None
    covered_at: float | None = None
    exit_result: dict | None = None

    def quantities(self):
        opening = [f for f in self.fills.values() if f['role'] == 'OPEN' and f['status'] in ACTIVE]
        tokens = {f['token'] for f in opening}
        bought = sum((f['qty'] for f in opening), ZERO)
        exits = [f for f in self.fills.values() if f['role'] != 'OPEN' and f['status'] in ACTIVE
                 and f['token'] not in tokens]
        tp = sum((f['qty'] for f in exits if f['role'] == 'TP'), ZERO)
        sl = sum((f['qty'] for f in exits if f['role'] == 'SL'), ZERO)
        return bought, tp, sl


class Watch:
    def __init__(self, grace_sec=5):
        self.grace_sec = grace_sec
        self.sessions, self.orders, self.cycles = {}, {}, {}
        self.pending_openings = {}

    def apply(self, record, source=''):
        kind = record.get('kind')
        envelope = record.get('data', {})
        data, stamp = envelope.get('payload', {}), float(record['ts'])
        sid = envelope.get('session_id') or data.get('session_id')
        if not sid:
            return
        session = self.sessions.setdefault(sid, {'source': source, 'last_seen': stamp})
        session['last_seen'] = max(stamp, session['last_seen'])
        if kind not in KINDS:
            return
        if kind == 'sm_session':
            session['settings'] = data
        elif kind == 'sm_run_result':
            session['result'] = data
        elif kind == 'sm_prepared':
            for candidate in data.get('candidates', []):
                for oid, quote in zip(candidate.get('order_ids', []), candidate.get('quotes', []), strict=True):
                    window = quote.get('window_id', '')
                    match = re.fullmatch(r'(.+:w\d+)(:ca-tp|:ca-expiry-exit)?', window)
                    if match:
                        role = {'': 'OPEN', ':ca-tp': 'TP', ':ca-expiry-exit': 'SL'}[match[2] or '']
                        self.orders[oid] = (match[1], role, quote)
        elif kind == 'sm_trade':
            owner = self.orders.get(data.get('maker_order_id'))
            if owner is None:
                return
            window, role, quote = owner
            self.pending_openings.pop(data.get('maker_order_id'), None)
            cycle = self.cycles.setdefault(window, Cycle(window, sid))
            qty = Decimal(str(data['size']))
            if role == 'OPEN' and qty > 0 and data['status'] in ACTIVE:
                cycle.opened_at = stamp if cycle.opened_at is None else min(cycle.opened_at, stamp)
            cycle.fills[(data['trade_id'], data['maker_order_id'])] = {
                'role': role, 'status': data['status'], 'qty': qty,
                'token': data['token_id'], 'price': Decimal(str(data['price'])),
                'outcome': quote.get('outcome')}
            opening, tp, sl = cycle.quantities()
            if opening > 0 and opening-tp-sl <= TOLERANCE:
                cycle.covered_at = stamp if cycle.covered_at is None else cycle.covered_at
            else:
                cycle.covered_at = None
        elif kind == 'sm_placement_result':
            oid = data.get('order_id') or data.get('expected_order_id')
            owner = self.orders.get(oid)
            if owner and owner[1] == 'OPEN' and data.get('post_started'):
                if data.get('outcome') in {'REJECTED', 'ABORTED'}:
                    self.pending_openings.pop(oid, None)
                elif not any(key[1] == oid for c in self.cycles.values() for key in c.fills):
                    self.pending_openings[oid] = {'session': sid, 'window': owner[0], 'since': stamp}
        elif kind == 'sm_order_rejection_proven':
            self.pending_openings.pop(data.get('order_id'), None)
        elif kind == 'sm_close_barrier' and data.get('result') == 'SAFE_TO_CONTINUE':
            for oid, proof in data.get('proofs', {}).items():
                row = proof.get('order', {})
                if Decimal(str(row.get('size_matched', '-1'))) == 0:
                    self.pending_openings.pop(oid, None)
        elif kind == 'sm_ca_expiry_exit_result':
            window = data.get('opening_window')
            if window:
                cycle = self.cycles.setdefault(window, Cycle(window, sid))
                cycle.exit_result = data

    def snapshot(self, now):
        alerts, cycles = [], []
        for oid, pending in self.pending_openings.items():
            if now-pending['since'] > 30+self.grace_sec:
                alerts.append({'key': oid+':OPEN_ORDER_UNRESOLVED', 'code': 'OPEN_ORDER_UNRESOLVED',
                               'order_id': oid, **pending})
        for sid, session in self.sessions.items():
            result = session.get('result')
            if result and result.get('execution_status') != 'COMPLETED':
                alerts.append({'key': sid+':ABNORMAL_EXIT', 'code': 'ABNORMAL_EXIT', 'session': sid,
                               'reason': result.get('stop_reason'), 'source': session['source']})
            settings = session.get('settings', {})
            slug = settings.get('slug', '')
            if slug and not result:
                end = int(slug.rsplit('-', 1)[1])+300
                if now > end+20:
                    alerts.append({'key': sid+':MISSING_RUN_RESULT', 'code': 'MISSING_RUN_RESULT',
                                   'session': sid, 'source': session['source']})
                elif now-session['last_seen'] > 20:
                    alerts.append({'key': sid+':LOG_STALLED', 'code': 'LOG_STALLED',
                                   'session': sid, 'source': session['source']})
        for window, cycle in self.cycles.items():
            opening, tp, sl = cycle.quantities()
            if cycle.opened_at is None or opening <= 0:
                continue
            session = self.sessions[cycle.session]
            missing, excess = max(ZERO, opening-tp-sl), max(ZERO, tp+sl-opening)
            age = now-cycle.opened_at
            details = {'window': window, 'session': cycle.session, 'opened_at': cycle.opened_at,
                       'age_seconds': round(age, 3), 'opening_shares': str(opening),
                       'tp_shares': str(tp), 'sl_shares': str(sl),
                       'covered_at': cycle.covered_at,
                       'original_unhedged_shares': str(missing), 'extra_reverse_shares': str(excess),
                       'source': session['source']}
            cycles.append(details)
            ttl = float(session.get('settings', {}).get('ca_matched_tp', {}).get('ttl_sec', 30))
            if missing > TOLERANCE and (age > ttl+self.grace_sec or session.get('result')):
                code = 'UNCOVERED_AT_END' if session.get('result') else 'LATE_EXIT'
                alerts.append({'key': window+':'+code, 'code': code, **details})
            if cycle.covered_at is not None and cycle.covered_at-cycle.opened_at > ttl+self.grace_sec:
                alerts.append({'key': window+':LATE_CLOSE', 'code': 'LATE_CLOSE',
                               'close_delay_seconds': round(cycle.covered_at-cycle.opened_at, 3), **details})
            taker = session.get('settings', {}).get('ca_taker', {})
            if taker.get('entry_price_ceiling_enabled') and any(
                    f['role'] == 'OPEN' and f['status'] in ACTIVE
                    and f['price'] >= Decimal(str(taker['entry_price_ceiling']))
                    for f in cycle.fills.values()):
                alerts.append({'key': window+':OPEN_PRICE_CEILING', 'code': 'OPEN_PRICE_CEILING', **details})
            if cycle.exit_result and cycle.exit_result.get('reason') not in {
                    'PAIRED', 'HEDGED_WITH_EXCESS', 'WITHIN_RESIDUAL_TOLERANCE'}:
                alerts.append({'key': window+':FAILED_EXIT', 'code': 'FAILED_EXIT',
                               'reason': cycle.exit_result.get('reason'), **details})
        return {'updated_at': now, 'alerts': alerts, 'cycles': cycles,
                'coverage_basis': 'Observed MATCHED/MINED/CONFIRMED fills; not ACKs or order limits',
                'late_threshold': f'TP ttl + {self.grace_sec} seconds; accepted dust <=0.01 shares',
                'automatic_chat_wakeup': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', type=Path, default=Path('.'))
    parser.add_argument('--grace-sec', type=float, default=5)
    parser.add_argument('--interval-sec', type=float, default=2)
    parser.add_argument('--recent-sessions', type=int, default=8)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    root = args.project_dir.resolve()
    output = root/'research_outputs/ca_execution_watch'
    output.mkdir(parents=True, exist_ok=True)
    with (output/'watch.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        watch, offsets, previous, errors = Watch(args.grace_sec), {}, {}, {}
        if (output/'status.json').exists():
            previous = {a['key']: a for a in json.loads((output/'status.json').read_text())['alerts']}
        while True:
            paths = sorted((root/'journal').glob('rdp_sm_*.jsonl'),
                           key=lambda p: p.stat().st_mtime)[-args.recent_sessions:]
            for path in paths:
                with path.open('rb') as stream:
                    stream.seek(offsets.get(path, 0))
                    while True:
                        start = stream.tell()
                        line = stream.readline()
                        if not line or not line.endswith(b'\n'):
                            offsets[path] = start
                            break
                        try:
                            watch.apply(json.loads(line), str(path))
                        except (ValueError, KeyError, TypeError) as exc:
                            errors[str(path)] = {'key': str(path)+':MONITOR_READ_ERROR',
                                'code': 'MONITOR_READ_ERROR', 'source': str(path),
                                'offset': start, 'error': type(exc).__name__}
            status = watch.snapshot(time.time())
            status['alerts'].extend(errors.values())
            temporary = output/'status.tmp'
            temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2)+'\n')
            temporary.replace(output/'status.json')
            current = {a['key']: a for a in status['alerts']}
            for key in current.keys() | previous.keys():
                old, new = previous.get(key), current.get(key)
                def stable(a):
                    return {k: v for k, v in (a or {}).items() if k != 'age_seconds'}
                if stable(old) != stable(new):
                    event = {'time': status['updated_at'], 'event': 'ALERT' if new else 'RECOVERED',
                             'incident': new or old}
                    line = json.dumps(event, ensure_ascii=False)
                    with (output/'incidents.jsonl').open('a') as stream:
                        stream.write(line+'\n')
                    print(line, flush=True)
            previous = current
            if args.once:
                return
            time.sleep(args.interval_sec)


if __name__ == '__main__':
    main()
