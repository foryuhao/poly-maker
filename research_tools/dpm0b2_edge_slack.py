"""Read-only pair edge at authenticated FIRST fills in persisted M4A sessions.

Run from the repository root: python3 research_tools/dpm0b2_edge_slack.py
Requires the locally generated M0B fills.csv; makes no API calls.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median


D = lambda value: Decimal(str(value))
ONE = Decimal('1')
FIVE = Decimal('5')
CENT = Decimal('0.01')


def micros(iso):
    delta = datetime.fromisoformat(iso) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def iso(us):
    return datetime.fromtimestamp(us / 1_000_000, timezone.utc).isoformat(timespec='microseconds') if us is not None else ''


def number(value):
    return str(value) if value is not None else ''


def quantile(values, q):
    if not values:
        return None
    x = sorted(values)
    pos = (len(x) - 1) * q
    i = int(pos)
    return x[i] + (x[min(i + 1, len(x) - 1)] - x[i]) * D(pos - i)


def vwap(asks, qty):
    remaining = qty
    cost = Decimal(0)
    for price, size in sorted(asks.items()):
        take = min(remaining, size)
        cost += take * price
        remaining -= take
        if remaining <= 0:
            return cost / qty
    return None


def edge(p1, p2):
    return ONE - p1 - p2 if p2 is not None else None


def max_ticks(bid, ask, tick, p1, minimum):
    if tick is None or tick <= 0 or bid is None or ask is None:
        return None
    n = 0
    answer = None
    while bid + n * tick < ask and 0 < bid + n * tick < 1:
        e = edge(p1, bid + n * tick)
        if (e >= minimum) if minimum else (e > 0):
            answer = n
        n += 1
    return answer


def candidate(price, ask, tick):
    if price is None or tick is None or not (0 < price < ask < 1):
        return None
    return price if price / tick == (price / tick).to_integral_value() else None


def load_book_events(session, token, last_fill_exchange, last_fill_receipt):
    events = []
    excluded = Counter()
    for line in (session / 'events.jsonl').open():
        row = json.loads(line)
        kind = row['kind']
        payload = row.get('payload') or {}
        if payload.get('token_id') != token or kind not in ('BOOK', 'DELTA', 'REST_BOOK', 'TICK'):
            continue
        exchange = row.get('exchange_at_us')
        available = row.get('available_at_us')
        if available is None or available > last_fill_receipt:
            continue
        if kind in ('REST_BOOK', 'TICK'):
            events.append((None, available, row.get('event_seq', 0), kind, payload))
            excluded[kind] += 1
        elif exchange is not None and exchange <= last_fill_exchange:
            events.append((exchange, available, row.get('event_seq', 0), kind, payload))
        else:
            excluded['future_or_untimed_ws'] += 1
    return events, excluded


def reconstruct(events, fill_exchange, fill_receipt):
    # Require both exchange causality and local availability by authenticated receipt.
    selected = [r for r in events if r[0] is not None and r[0] <= fill_exchange and r[1] <= fill_receipt]
    selected.sort(key=lambda r: (r[0], r[2]))
    bids = {}
    asks = {}
    last_us = None
    last_available = None
    last_book_exchange = None
    tick = None
    snapshot_count = 0
    delta_count = 0
    orphan_deltas = 0
    for exchange, available, _seq, kind, payload in selected:
        if kind == 'BOOK':
            bids = {D(p): D(s) for p, s in payload.get('bids', []) if D(s) > 0}
            asks = {D(p): D(s) for p, s in payload.get('asks', []) if D(s) > 0}
            tick = D(payload['tick_size']) if payload.get('tick_size') is not None else None
            snapshot_count += 1
            last_book_exchange = exchange
        elif snapshot_count:
            side = bids if payload['side'] == 'BUY' else asks
            price = D(payload['price'])
            size = D(payload['size'])
            if size > 0:
                side[price] = size
            else:
                side.pop(price, None)
            delta_count += 1
        else:
            orphan_deltas += 1
            continue
        last_us, last_available = exchange, available
    # A REST snapshot has no exchange timestamp and cannot be placed in replay.
    last_snapshot_available = max((r[1] for r in selected if r[3] == 'BOOK'), default=None)
    intervening_rest = [r for r in events if r[3] == 'REST_BOOK' and last_snapshot_available is not None
                        and r[1] > last_snapshot_available and r[1] <= fill_receipt]
    untimed_ticks = [r for r in events if r[3] == 'TICK' and last_snapshot_available is not None
                     and r[1] > last_snapshot_available and r[1] <= fill_receipt]
    return dict(bids=bids, asks=asks, tick=tick, book_exchange_us=last_us,
                book_available_us=last_available, last_snapshot_exchange_us=last_book_exchange,
                snapshot_count=snapshot_count, delta_count=delta_count,
                orphan_deltas=orphan_deltas, intervening_rest=len(intervening_rest),
                untimed_ticks=len(untimed_ticks))


def row_for_fill(fill, book, opposite_token):
    p1 = D(fill['actual_fill_price'])
    qty = D(fill['actual_fill_size'])
    paired = min(qty, FIVE)
    bids, asks, tick = book['bids'], book['asks'], book['tick']
    bid = max(bids) if bids else None
    ask = min(asks) if asks else None
    valid = bid is not None and ask is not None and 0 < bid < ask < 1 and tick is not None and tick > 0
    if not valid:
        bid = ask = None
    p_bid_1 = candidate(bid + tick, ask, tick) if valid else None
    p_bid_2 = candidate(bid + 2 * tick, ask, tick) if valid else None
    p_ask_2 = candidate(ask - 2 * tick, ask, tick) if valid else None
    p_ask_1 = candidate(ask - tick, ask, tick) if valid else None
    taker5 = vwap(asks, FIVE) if valid else None
    takerpaired = vwap(asks, paired) if valid else None
    values = dict(edge_bid=edge(p1, bid), edge_bid_plus_1=edge(p1, p_bid_1),
                  edge_bid_plus_2=edge(p1, p_bid_2), edge_ask_minus_2=edge(p1, p_ask_2),
                  edge_ask_minus_1=edge(p1, p_ask_1), edge_taker_5=edge(p1, taker5),
                  edge_taker_paired=edge(p1, takerpaired))
    if not valid:
        category = 'UNUSABLE'
    elif values['edge_taker_paired'] is not None and values['edge_taker_paired'] > 0:
        category = 'A'
    elif any(price is not None and price > bid and edge(p1, price) > 0
             for price in (p_bid_1, p_bid_2, p_ask_1, p_ask_2)):
        category = 'B'
    elif any(price is not None and price <= bid and edge(p1, price) > 0
             for price in (bid, p_ask_1, p_ask_2)):
        category = 'C'
    else:
        category = 'D'
    ex = micros(fill['actual_fill_exchange_time'])
    issues = []
    if book['intervening_rest']:
        issues.append('untimed REST replacement after last replayed event')
    if book['untimed_ticks']:
        issues.append('untimed tick message after last replayed event')
    if not valid:
        issues.append('no valid exchange-bounded opposite L2 book/tick')
    if qty < FIVE:
        issues.append('partial first fill; category uses paired quantity, not five shares')
    gap_ms = (ex - book['book_exchange_us']) / 1000 if book['book_exchange_us'] is not None else None
    if gap_ms is not None and gap_ms > 1000:
        issues.append('last exchange-stamped book event more than one second before fill')
    if gap_ms == 0:
        issues.append('book event and fill share millisecond exchange timestamp; ordering unresolved')
    quality = ('missing' if not valid else
               'untimed_intervention' if book['intervening_rest'] or book['untimed_ticks'] else
               'stale_over_1s' if gap_ms is not None and gap_ms > 1000 else
               'same_ms_order_ambiguous' if gap_ms == 0 else 'causal_ws_l2_proxy')
    output = dict(market=fill['market'], fill_id=fill['trade_id'], order_id=fill['order_id'],
                  first_leg_outcome=fill['outcome'], first_leg_price=number(p1), first_leg_size=number(qty),
                  first_leg_exchange_time=fill['actual_fill_exchange_time'],
                  first_leg_user_ws_receive_time=fill['actual_fill_receive_time'],
                  order_accepted_time=fill['accepted_time'],
                  opposite_outcome='NO' if fill['outcome'] == 'YES' else 'YES',
                  opposite_token=opposite_token,
                  opposite_book_time=iso(book['book_exchange_us']),
                  opposite_book_available_time=iso(book['book_available_us']),
                  causal_time_gap_ms=gap_ms if gap_ms is not None else '',
                  opposite_best_bid=number(bid), opposite_bid_size=number(bids.get(bid) if bid is not None else None),
                  opposite_best_ask=number(ask), opposite_ask_size=number(asks.get(ask) if ask is not None else None),
                  opposite_spread=number(ask - bid if valid else None), tick_size=number(tick),
                  price_bid_plus_1=number(p_bid_1), price_bid_plus_2=number(p_bid_2),
                  price_ask_minus_2=number(p_ask_2), price_ask_minus_1=number(p_ask_1),
                  paired_quantity=number(paired), p_taker_5=number(taker5),
                  p_taker_paired=number(takerpaired),
                  pair_cost_bid=number(p1 + bid if bid is not None else None),
                  pair_cost_bid_plus_1=number(p1 + p_bid_1 if p_bid_1 is not None else None),
                  pair_cost_bid_plus_2=number(p1 + p_bid_2 if p_bid_2 is not None else None),
                  pair_cost_ask_minus_2=number(p1 + p_ask_2 if p_ask_2 is not None else None),
                  pair_cost_ask_minus_1=number(p1 + p_ask_1 if p_ask_1 is not None else None),
                  pair_cost_taker_5=number(p1 + taker5 if taker5 is not None else None),
                  pair_cost_taker_paired=number(p1 + takerpaired if takerpaired is not None else None),
                  max_positive_edge_ticks=max_ticks(bid, ask, tick, p1, Decimal(0)) if valid else None,
                  max_1c_edge_ticks=max_ticks(bid, ask, tick, p1, CENT) if valid else None,
                  max_2c_edge_ticks=max_ticks(bid, ask, tick, p1, 2 * CENT) if valid else None,
                  classification_A_B_C_D=category, reconstruction_quality=quality,
                  opposite_bids_json=json.dumps([[number(p), number(s)] for p, s in sorted(bids.items(), reverse=True)]),
                  opposite_asks_json=json.dumps([[number(p), number(s)] for p, s in sorted(asks.items())]),
                  ws_snapshot_count=book['snapshot_count'], ws_delta_count=book['delta_count'],
                  orphan_delta_count=book['orphan_deltas'],
                  untimed_rest_count=book['intervening_rest'], untimed_tick_count=book['untimed_ticks'],
                  notes='; '.join(issues))
    output.update({k: number(v) for k, v in values.items()})
    return output


def summarize(rows):
    usable = [r for r in rows if r['classification_A_B_C_D'] != 'UNUSABLE']
    categories = Counter(r['classification_A_B_C_D'] for r in usable)
    keys = ('edge_bid', 'edge_bid_plus_1', 'edge_bid_plus_2', 'edge_ask_minus_2',
            'edge_ask_minus_1', 'edge_taker_5')
    thresholds = {'edge_taker_5': ('0', '0.01'), 'edge_ask_minus_1': ('0', '0.01', '0.02'),
                  'edge_ask_minus_2': ('0',), 'edge_bid_plus_1': ('0',),
                  'edge_bid_plus_2': ('0',), 'edge_bid': ('0',)}
    def group(rs):
        result = dict(count=len(rs), categories=dict(Counter(r['classification_A_B_C_D'] for r in rs)))
        for key in keys:
            values = [D(r[key]) for r in rs if r[key] != '']
            result[key] = dict(available=len(values), p25=number(quantile(values, .25)),
                               median=number(median(values)) if values else '',
                               p75=number(quantile(values, .75)),
                               positive=sum(v > 0 for v in values))
            for threshold in thresholds.get(key, ()):
                v = D(threshold)
                name = 'gt_0' if v == 0 else 'ge_' + threshold
                result[key][name] = sum(x > 0 if v == 0 else x >= v for x in values)
        for key in ('max_positive_edge_ticks', 'max_1c_edge_ticks', 'max_2c_edge_ticks'):
            values = [D(r[key]) for r in rs if r[key] is not None]
            result[key] = dict(available=len(values), p25=number(quantile(values, .25)),
                               median=number(median(values)) if values else '',
                               p75=number(quantile(values, .75)), max=number(max(values)) if values else '')
        return result
    high_confidence = [r for r in usable if r['reconstruction_quality'] == 'causal_ws_l2_proxy']
    return dict(actual_first_fill_events=len(rows), unique_orders=len({r['order_id'] for r in rows}),
                unique_markets=len({r['market'] for r in rows}), yes_fills=sum(r['first_leg_outcome'] == 'YES' for r in rows),
                no_fills=sum(r['first_leg_outcome'] == 'NO' for r in rows),
                usable_causal_books=len(usable), high_confidence_books=len(high_confidence),
                partial_first_fill_events=sum(D(r['first_leg_size']) < FIVE for r in rows),
                reconstruction_quality=dict(Counter(r['reconstruction_quality'] for r in rows)),
                categories=dict(categories), all_usable=group(usable), high_confidence=group(high_confidence),
                yes=group([r for r in usable if r['first_leg_outcome'] == 'YES']),
                no=group([r for r in usable if r['first_leg_outcome'] == 'NO']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fills', type=Path, default=Path('research_outputs/dpm0b/fills.csv'))
    parser.add_argument('--sessions', type=Path, default=Path('research_data/m4a'))
    parser.add_argument('--output', type=Path, default=Path('research_outputs/dpm0b2'))
    args = parser.parse_args()
    with args.fills.open(newline='') as stream:
        first_fills = [r for r in csv.DictReader(stream) if r['role'] == 'FIRST' and r['fill_final_status'] == 'CONFIRMED']
    by_market = defaultdict(list)
    for row in first_fills:
        by_market[row['market']].append(row)
    sessions = {}
    for path in args.sessions.iterdir():
        manifest = path / 'manifest.json'
        if manifest.exists():
            m = json.loads(manifest.read_text())
            if m['market_slug'] in by_market:
                if m['market_slug'] in sessions:
                    raise RuntimeError(f'duplicate session for {m["market_slug"]}')
                sessions[m['market_slug']] = (path, m)
    if set(by_market) != set(sessions):
        raise RuntimeError(f'missing captured sessions: {set(by_market) - set(sessions)}')
    outputs = []
    audits = []
    for market, fills in sorted(by_market.items()):
        path, manifest = sessions[market]
        if manifest['close_status'] != 'COMPLETE' or manifest['gap_count'] or manifest['lost_event_count']:
            raise RuntimeError(f'incomplete session: {market}')
        target_tokens = {manifest['no_token_id'] if r['outcome'] == 'YES' else manifest['yes_token_id'] for r in fills}
        for token in target_tokens:
            selected = [r for r in fills if (manifest['no_token_id'] if r['outcome'] == 'YES' else manifest['yes_token_id']) == token]
            latest_exchange = max(micros(r['actual_fill_exchange_time']) for r in selected)
            latest_receipt = max(micros(r['actual_fill_receive_time']) for r in selected)
            events, excluded = load_book_events(path, token, latest_exchange, latest_receipt)
            for fill in selected:
                book = reconstruct(events, micros(fill['actual_fill_exchange_time']), micros(fill['actual_fill_receive_time']))
                outputs.append(row_for_fill(fill, book, token))
            audits.append(dict(market=market, opposite_token=token, event_count=len(events),
                               excluded=dict(excluded), manifest_gap_count=manifest['gap_count'],
                               manifest_lost_event_count=manifest['lost_event_count']))
    outputs.sort(key=lambda r: (r['first_leg_exchange_time'], r['fill_id']))
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'edge_slack.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(outputs[0]))
        writer.writeheader()
        writer.writerows(outputs)
    summary = summarize(outputs)
    summary['session_audits'] = audits
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'session_audits'}, indent=2))


if __name__ == '__main__':
    main()
