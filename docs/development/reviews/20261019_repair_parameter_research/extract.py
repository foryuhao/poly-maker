"""Read-only causal BBO extraction from the 47 archived Shadow event streams.

Run with the isolated research Python, never the trading entry point.
No production imports, no network, no changes to config or runtime code.
"""
import bisect
import csv
import hashlib
import math
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import orjson

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]


def numeric(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def extract(path):
    started = time.monotonic()
    summary = orjson.loads(path.with_name('market_summary.json').read_bytes())
    snapshots = [orjson.loads(line) for line in path.with_name('market_snapshots.jsonl').read_bytes().splitlines()]
    epoch = float(summary['market_slug'].rsplit('-', 1)[1])
    targets = sorted([(epoch+i, 'grid', i) for i in range(301)] + [
        (r['actual_snapshot_time'], 'archive', j) for j, r in enumerate(snapshots)
        if r.get('slot_validity') == 'OBSERVED'])
    qi = 0
    quotes = {}
    points = deque(maxlen=128)
    fields = ['slug', 'session_id', 'epoch', 't', 'generation', 'p', 'sigma',
              'yes_bid', 'yes_ask', 'no_bid', 'no_ask', 'tick', 'reason']
    rows, comparison = [], Counter()
    meta = None
    last_available = -math.inf
    issues, types = Counter(), Counter()
    digest = hashlib.sha256()

    def sample(at, kind, index):
        row = dict(zip(fields, [summary['market_slug'], summary['session_id'], epoch,
                              at-epoch, None, None, None, None, None, None, None, None, 'NO_BOOK']))
        if len(quotes) == 2:
            y, n = quotes['YES'], quotes['NO']
            row.update(generation=y[4], yes_bid=y[0], yes_ask=y[1],
                       no_bid=n[0], no_ask=n[1], tick=n[5])
            valid = (all(numeric(v) for v in [y[0], y[1], n[0], n[1]])
                     and 0 < y[0] < y[1] < 1 and 0 < n[0] < n[1] < 1)
            fresh = all(0 <= at-q[2] <= 5 for q in [y, n])
            generation = y[4] == n[4]
            coherent = valid and abs((y[0]+y[1]+n[0]+n[1])/2-1) <= .02000000001
            row['reason'] = ('VALID' if valid and fresh and generation and coherent else
                             'STALE' if not fresh else 'GENERATION' if not generation else
                             'INVALID_BOOK' if not valid else 'CROSSCHECK')
            if row['reason'] == 'VALID':
                row['p'] = (y[0]+y[1])/2
                # Same 30s / completed-second buckets and 10-pair / 20s gate
                # as the new adapter. Monotonic intervals, no current bucket.
                clock = y[3]+(at-y[2])
                eligible = [p for p in points if p[0] < math.floor(at)
                            and p[3] == y[4] and clock-30 <= p[1] <= clock]
                pairs = [(a, b) for a, b in zip(eligible, eligible[1:]) if 0 < b[1]-a[1] <= 5]
                duration = sum(b[1]-a[1] for a, b in pairs)
                if len(pairs) >= 10 and duration >= 20:
                    row['sigma'] = math.sqrt(sum((b[2]-a[2])**2 for a, b in pairs)/duration)
        if kind == 'grid':
            rows.append(row)
        else:
            archived = snapshots[index]
            if archived.get('probability_validity') == 'VALID' and numeric(archived.get('p_yes')):
                comparison['archive_valid'] += 1
                comparison['raw_valid'] += row['p'] is not None
                comparison['p_exact_match'] += row['p'] is not None and abs(row['p']-archived['p_yes']) < 1e-9
                comparison['p_different'] += row['p'] is not None and abs(row['p']-archived['p_yes']) >= 1e-9

    with path.open('rb') as handle:
        for line in handle:
            digest.update(line)
            r = orjson.loads(line)
            et = r.get('event_type')
            types[et] += 1
            at = r.get('available_timestamp', r.get('timestamp'))
            if not numeric(at):
                issues['missing_available_time'] += 1
                continue
            if at < last_available:
                issues['available_time_reversal'] += 1
                # Preserve observer order; an earlier wall timestamp cannot
                # retroactively update an already sampled research observation.
                at = last_available
            last_available = at
            while qi < len(targets) and targets[qi][0] < at:
                sample(*targets[qi])
                qi += 1
            if et == 'OFFICIAL_METADATA':
                meta = r
                assert r['session_id'] == summary['session_id']
            elif et == 'CONNECTION':
                payload = r.get('event_payload', {})
                if payload.get('state') in ['UP', 'DOWN']:
                    quotes.clear()
                    points.clear()
            elif et in ['BOOK', 'DELTA'] and meta:
                token = r.get('token_id')
                side = ('YES' if token == meta['yes_token_id'] else
                        'NO' if token == meta['no_token_id'] else None)
                if side:
                    q = (r.get('best_bid'), r.get('best_ask'), r.get('local_receive_timestamp'),
                         r.get('local_receive_monotonic'), r.get('connection_generation'),
                         r.get('official_tick_size'))
                    if all(numeric(q[i]) for i in [2, 3]) and q[4] is not None:
                        quotes[side] = q
                        if side == 'YES' and numeric(q[0]) and numeric(q[1]) and q[0] < q[1]:
                            point = (math.floor(q[2]), q[3], (q[0]+q[1])/2, q[4])
                            if points and points[-1][0] == point[0]:
                                points[-1] = point
                            else:
                                points.append(point)
            issues['dropped_flag_rows'] += bool(r.get('shadow_dropped_event_count_so_far', 0))
    while qi < len(targets):
        sample(*targets[qi])
        qi += 1
    cache = OUT/'cache'
    cache.mkdir(exist_ok=True)
    dest = cache/(summary['session_id'].replace('/', '_')+'.csv')
    with dest.open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    result = {'path': str(path.resolve()), 'sha256': digest.hexdigest(), 'bytes': path.stat().st_size,
              'slug': summary['market_slug'], 'session_id': summary['session_id'],
              'events': dict(types), 'issues': dict(issues), 'comparison': dict(comparison),
              'grid_valid': sum(r['p'] is not None for r in rows),
              'grid_sigma_valid': sum(r['sigma'] is not None for r in rows),
              'seconds': time.monotonic()-started}
    print(orjson.dumps({k: result[k] for k in ['slug', 'grid_valid', 'seconds']}).decode(), flush=True)
    return result


if __name__ == '__main__':
    files = sorted((ROOT/'research_outputs/rdp_risk_shadow').glob('*/*/market_events.jsonl'))
    assert len(files) == 47
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(extract, files))
    (OUT/'raw_manifest.json').write_bytes(orjson.dumps(results, option=orjson.OPT_INDENT_2))
    print('DONE', len(results), sum(r['bytes'] for r in results), flush=True)
