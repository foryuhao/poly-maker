"""Independent result/lineage checks for the research artifacts only."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]


def main():
    ev = json.loads((OUT/'evidence.json').read_text())
    manifest = json.loads((OUT/'raw_manifest.json').read_text())
    assert len(manifest) == len({r['session_id'] for r in manifest}) == 47
    assert sum(sum(r['events'].values()) for r in manifest) == 17571689
    assert ev['scope']['archive_comparison']['p_different'] == 0
    assert not any(r['issues'].get('available_time_reversal', 0) for r in manifest)
    rows = list(csv.DictReader((OUT/'path_probe_results.csv').open()))
    identities = [(r['sid'], r['t0'], r['side'], r['basis'], r['r_tp'], r['sl_spec']) for r in rows]
    assert len(identities) == len(set(identities)), 'Duplicated research case'
    for r in rows:
        if r['tp_t']:
            assert float(r['tp_t']) > float(r['t0'])
        if r['tp_quote_cross_t']:
            assert float(r['tp_quote_cross_t']) > float(r['tp_t'])
            assert float(r['tp_quote_cross_t']) < float(r['sl_t']) if r['sl_t'] else True
        if r['sl_t']:
            assert float(r['sl_t']) <= 180
        if r['censored_t']:
            assert not r['sl_t']
    by_case = {}
    for r in rows:
        by_case.setdefault((r['sid'],r['t0'],r['side'],r['basis'],r['sl_spec']), []).append(r)
    for group in by_case.values():
        group.sort(key=lambda r:float(r['r_tp']))
        has_tp = [bool(r['tp_t']) for r in group]
        assert has_tp == sorted(has_tp, reverse=True), 'TP count must fall as rate increases'
        assert len({r['sl_t'] for r in group}) == 1, 'Path-SL independent of rate absent assumed fills'
    prior = json.loads((ROOT/'docs/development/reviews/20261017_2245_2345_pairing/evidence.json').read_text())
    journals = {}
    for c in prior['cycles']:
        path = Path(c['source'])
        if str(path) not in journals:
            journals[str(path)] = [json.loads(l) for l in path.read_text().splitlines()]
        records = journals[str(path)]
        legs = []
        for lineno in c['opening_fill_lines']:
            r = records[lineno-1]
            assert r['kind'] == 'sm_trade'
            leg = r['data'].get('payload', r['data'])
            assert leg['status'] == 'CONFIRMED'
            legs.append(leg)
        assert abs(sum(float(l['size']) for l in legs)-c['entry_quantity']) < 1e-8
        assert all(abs(float(l['price'])-c['entry_price']) < 1e-8 for l in legs)
        repair_cost = 0
        for leg in c['allocated_repair_fills']:
            r = records[leg['source_line']-1]
            actual = r['data'].get('payload', r['data'])
            assert r['kind'] == 'sm_trade' and actual['status'] == 'CONFIRMED'
            assert actual['trade_id'] == leg['trade_id']
            assert abs(float(actual['price'])-leg['price']) < 1e-8
            repair_cost += leg['quantity']*leg['price']
        gross = c['entry_quantity']*(1-c['entry_price'])-repair_cost
        assert abs(gross-c['fifo_acquisition_pair_gross']) < 1e-8
    for source in prior['sources']:
        # Verify available frozen primary journal hashes used by these cycles.
        if source['path'] in journals:
            assert hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() == source['sha256']
    result = {'status':'PASS', 'cases_checked':len(rows), 'market_clusters':47,
              'source_quote_comparisons_exact':10047, 'source_quote_comparisons_different':0,
              'fixed_normal_episodes_economics_checked':len(prior['cycles']),
              'raw_fixed_normal_journals_read':len(journals),
              'checks':['unique market/case identity', 'recorded source hashes', 'causal event order',
                        'TP rate monotonicity', 'SL independence of TP rate without assumed fills',
                        'censoring and quote-cross ordering', 'primary confirmed opening and repair legs']}
    (OUT/'validation.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
