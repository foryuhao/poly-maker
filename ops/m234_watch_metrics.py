"""Read-only operational metrics for the shared M2/M3/M4 public study."""

import ast
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


def summarize_signals(rows):
    valid = [r for r in rows if Decimal(r['quantity']) > 0]
    fills = [r for r in rows if r['status'] == 'ASSUMED_FILL']
    settled = [r for r in fills if r['trade_pnl'] is not None]
    complete = [r for r in settled if r['full_window'] and r['market_state'] == 'CLOSED']
    return {
        'valid_signals': len(valid), 'signal_markets': len({r['slug'] for r in valid}),
        'research_rows': len(rows), 'assumed_fills': len(fills),
        'filled_markets': len({r['slug'] for r in fills}),
        'settled_fills': len(settled), 'pending_fills': len(fills)-len(settled),
        'complete_settled_fill_markets': len({r['slug'] for r in complete}),
        'direction_skipped': sum(r['reason'] == 'DIRECTION_ENTRY_LIMIT_REACHED' for r in rows),
        'last_signal_at': max((r['wall'] for r in valid), default=None),
        'last_assumed_fill_at': max((r['wall'] for r in fills), default=None),
        'oldest_pending_fill_at': min((r['wall'] for r in fills if r['trade_pnl'] is None), default=None),
        'settled_cost': str(sum((Decimal(r['cost']) for r in settled), Decimal(0))),
        'settled_pnl': str(sum((Decimal(r['trade_pnl']) for r in settled), Decimal(0))),
    }


def inventory(rows):
    result = summarize_signals(rows)
    # Counts describe accumulated records, not a portfolio across definitions.
    result.pop('settled_cost')
    result.pop('settled_pnl')
    return result


def strategy_dependency_drift(source_root, sources, baselines):
    """Ignore only dashboard observer changes proven outside the public API."""
    relevant, excluded = [], []
    for name, expected in sources.items():
        path = source_root/name
        if not path.is_file():
            relevant.append(name)
            continue
        actual = path.read_bytes()
        if hashlib.sha256(actual).hexdigest() == expected:
            continue
        baseline = baselines/(expected+'.py')
        if name == 'dashboard/settlement.py' and baseline.is_file():
            frozen = baseline.read_bytes()
            if hashlib.sha256(frozen).hexdigest() == expected:
                def projection(raw):
                    tree = ast.parse(raw)
                    tree.body = [node for node in tree.body
                                 if not isinstance(node, ast.ClassDef) or node.name != 'SettlementObserver']
                    return ast.dump(tree, include_attributes=False)
                try:
                    if projection(frozen) == projection(actual):
                        excluded.append(name)
                        continue
                except (SyntaxError, ValueError, UnicodeError):
                    pass
        relevant.append(name)
    return relevant, excluded


def operational_metrics(db, directory, latest, markets, now):
    version = latest['active_version']
    settings = latest.get('settings', {})
    runtime = latest.get('runtime', {})
    runs = {r['id']: dict(r) for r in db.execute('SELECT id,version,settings FROM runs')}
    m2 = [dict(r) for r in db.execute('''SELECT s.*,m.winner,m.full_window,
        m.state AS market_state FROM samples s JOIN markets m ON m.slug=s.slug
        WHERE s.rule='all_candidates' ORDER BY s.id''')]
    extra = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='strategy_samples'").fetchone():
        extra = [dict(r) for r in db.execute('''SELECT s.*,m.winner,m.full_window,
            m.state AS market_state FROM strategy_samples s JOIN markets m ON m.slug=s.slug ORDER BY s.id''')]
    all_groups = {'M2': m2, 'M3': [r for r in extra if r['strategy'] == 'M3'],
                  'M4': [r for r in extra if r['strategy'] == 'M4']}
    current_m2_all_rules = [dict(r) for r in db.execute('''SELECT s.*,m.winner,m.full_window,
        m.state AS market_state FROM samples s JOIN markets m ON m.slug=s.slug WHERE s.version=?''', (version,))]
    policy_rows = [('M2:'+r['rule'], r) for r in current_m2_all_rules]
    policy_rows += [(r['strategy'], r) for r in extra if r['version'] == version]
    errors = Counter()
    directions, spent = Counter(), defaultdict(lambda: Decimal(0))
    for group, row in policy_rows:
        try:
            p, q, c = (Decimal(row[k]) for k in ('price', 'quantity', 'cost'))
            if not all(v.is_finite() for v in (p, q, c)) or not 0 < p < 1 or q < 0 or not 0 <= c <= Decimal(settings.get('risk_usdc', 5)) or p*q != c:
                errors['quote_or_risk'] += 1
            if row['winner'] is None:
                errors['unsettled_pnl'] += row['sample_pnl'] is not None or row['trade_pnl'] is not None
            elif q > 0:
                expected = (q if row['outcome'] == row['winner'] else Decimal(0))-c
                errors['sample_payout'] += row['sample_pnl'] is None or Decimal(row['sample_pnl']) != expected
                if row['status'] == 'ASSUMED_FILL':
                    errors['trade_payout'] += row['trade_pnl'] is None or Decimal(row['trade_pnl']) != expected
            if row['status'] == 'SKIPPED':
                errors['skipped_trade_pnl'] += row['trade_pnl'] is not None
            elif row['status'] == 'ASSUMED_FILL':
                errors['fill_size'] += q <= 0
                directions[(group, row['slug'], row['outcome'])] += 1
                spent[(group, row['slug'])] += c
            else:
                errors['unknown_status'] += 1
            errors['run_version_link'] += row['run_id'] not in runs or runs[row['run_id']]['version'] != row['version']
        except (ValueError, TypeError, InvalidOperation):
            errors['invalid_numeric'] += 1
    errors['direction_cap'] = sum(n > settings.get('max_entries_per_direction', 1) for n in directions.values())
    errors['market_budget'] = sum(v > Decimal(settings.get('market_budget_usdc', 20)) for v in spent.values())
    for market in markets:
        if market['winner'] is None:
            continue
        resolution = json.loads(market['resolution'])
        errors['resolution_identity'] += resolution.get('slug') != market['slug'] or resolution.get('winner') != market['winner']
        if settings or 'condition_id' in resolution:
            meta = json.loads(market['metadata'])
            tokens = {'YES': meta['tokens'][0]['token_id'], 'NO': meta['tokens'][1]['token_id']}
            try:
                payouts = [Decimal(str(v)) for v in resolution.get('outcome_prices', [])]
                ids = resolution.get('clob_token_ids', [])
                final = (len(payouts) == 2 and all(v.is_finite() for v in payouts)
                         and sorted(payouts) == [Decimal(0), Decimal(1)] and len(ids) == 2
                         and ids[payouts.index(Decimal(1))] == tokens.get(market['winner']))
            except (ValueError, TypeError, InvalidOperation):
                final = False
            errors['official_resolution_evidence'] += (
                resolution.get('condition_id') != meta['condition_id'] or resolution.get('token_ids') != tokens
                or set(resolution.get('clob_token_ids', [])) != set(tokens.values())
                or resolution.get('closed') is not True or resolution.get('resolution_status') != 'resolved'
                or not final
                or resolution.get('source') != 'https://gamma-api.polymarket.com/markets/slug/'+market['slug'])

    definition_path = Path(directory)/f'definition-{version[:16]}.json'
    drift, excluded_drift, definition_status = [], [], 'NOT_PROVIDED'
    if latest.get('settings'):
        if definition_path.exists():
            definition = json.loads(definition_path.read_text())
            source_root = Path(__file__).resolve().parents[1]/'src/polymaker'
            baselines = Path(directory).parent/'m2_watch/source_baselines'
            drift, excluded_drift = strategy_dependency_drift(source_root, definition['sources'], baselines)
            errors['definition_settings'] += definition['settings'] != settings
            errors['definition_version'] += definition['version'] != version
            definition_status = 'MATCH' if not drift and not errors['definition_settings'] and not errors['definition_version'] else 'MISMATCH'
            if definition_status == 'MATCH' and excluded_drift:
                definition_status = 'MATCH_FOR_STRATEGY_SCOPE'
        else:
            definition_status = 'MISSING'
            errors['definition_missing'] += 1

    recent = [m for m in markets if m['epoch']+300 > now-3600]
    recent_events = Counter()
    component = {'M3': Counter(), 'M4': Counter()}
    candidate_counts = Counter()
    last_candidate = {}
    for strategy, kind, count, last_wall in db.execute('''SELECT
        substr(e.kind,1,2),e.kind,count(*),max(e.wall) FROM events e
        JOIN runs r ON r.id=e.run_id WHERE r.version=? AND
        (e.kind IN ('M3_CANDIDATE','M4_CANDIDATE') OR (e.wall>=? AND
         (e.kind LIKE 'M3_%' OR e.kind LIKE 'M4_%'))) GROUP BY e.kind''', (version, now-3600)):
        if kind.endswith('_CANDIDATE'):
            candidate_counts[strategy] = count
            last_candidate[strategy] = last_wall
        component[strategy][kind] = count
    candidate_counts['M2'] = db.execute('''SELECT count(*) FROM candidates c
        JOIN runs r ON r.id=c.run_id WHERE r.version=?''', (version,)).fetchone()[0]
    last_candidate['M2'] = db.execute('''SELECT max(c.wall) FROM candidates c
        JOIN runs r ON r.id=c.run_id WHERE r.version=?''', (version,)).fetchone()[0]
    for run in recent:
        for key, count in json.loads(run['counts']).get('invalid_reasons', {}).items():
            recent_events[key] += count
    observed = {}
    for strategy, table, clause in [('M2', 'observations', ''), ('M3', 'strategy_observations', "AND o.strategy='M3'"), ('M4', 'strategy_observations', "AND o.strategy='M4'")]:
        if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
            observed[strategy] = {'observations': 0, 'valid_observations': 0, 'last_observation_at': None}
            continue
        r = db.execute(f'''SELECT count(*) n,coalesce(sum(o.valid),0) valid,max(o.wall) last
            FROM {table} o JOIN runs r ON r.id=o.run_id WHERE r.version=? {clause}''', (version,)).fetchone()
        observed[strategy] = {'observations': r['n'], 'valid_observations': r['valid'], 'last_observation_at': r['last']}

    strategies = {}
    failures = runtime.get('observer_failures', {})
    for strategy, history in all_groups.items():
        current = [r for r in history if r['version'] == version]
        hour = [r for r in current if r['wall'] >= now-3600]
        enabled = strategy == 'M2' or settings.get('include_m3_m4', bool(current))
        failure = {k: v for k, v in failures.items() if k == strategy or (strategy == 'M4' and k == 'M4_V2')}
        versions = {v: summarize_signals([r for r in history if r['version'] == v]) for v in sorted({r['version'] for r in history})}
        regimes = defaultdict(list)
        if strategy == 'M4':
            for row in history:
                regime = 'm4-frequency-v2' if json.loads(runs[row['run_id']]['settings']).get('m4_frequency_study') else 'm4-v1'
                regimes[regime].append(row)
        strategies[strategy] = {
            'definition': ('m4-frequency-v2' if settings.get('m4_frequency_study') else 'm4-v1') if strategy == 'M4' else 'M2_PRIMARY_ALL_CANDIDATES' if strategy == 'M2' else 'M3_CA_THEN_BA',
            'enabled': enabled, 'observer_failure': failure,
            'strategy_health': 'DISABLED' if not enabled else 'OBSERVER_FAILED' if failure else 'RUNNING',
            'signal_health': 'DISABLED' if not enabled else 'PRODUCING' if any(Decimal(r['quantity']) > 0 for r in hour) else 'NO_NEW_VALID_SIGNAL_LAST_HOUR',
            'simulation_health': 'DISABLED' if not enabled else 'LEDGER_ERROR' if any(errors.values()) else 'ASSUMED_FILLS_RECORDED' if any(r['status'] == 'ASSUMED_FILL' for r in current) else 'NO_ELIGIBLE_FILL_YET',
            'cumulative_all_versions': inventory(history),
            'current_version': {**summarize_signals(current), **observed[strategy],
                                'candidates': candidate_counts[strategy], 'last_candidate_at': last_candidate.get(strategy)},
            'last_hour': summarize_signals(hour),
            'by_version': versions,
            'by_definition': {key: inventory(rows) for key, rows in regimes.items()},
            'component_event_counts': dict(component.get(strategy, {})),
        }
    gaps = [(a['epoch'], b['epoch']) for a,b in zip(markets, markets[1:], strict=False) if b['epoch']-a['epoch'] != 300]
    problems = []
    if any(errors.values()) or drift:
        problems.append('SIMULATION_OR_DEFINITION_INVARIANT_FAILED')
    if runtime.get('state') in {'WAIT_RESOURCES', 'RESOURCE_STOP'}:
        problems.append('COLLECTOR_RESOURCE_WAIT_OR_STOP')
    if settings.get('include_m3_m4') is False:
        problems.append('M3_M4_NOT_ENABLED')
    if settings.get('include_m3_m4') and runtime.get('enabled_strategies') and set(runtime['enabled_strategies']) != {'M2', 'M3', 'M4'}:
        problems.append('STRATEGY_SET_INCOMPLETE')
    return {
        'watch_contract': 'M234_HOURLY_HEALTH_V1', 'strategies': strategies,
        'checks': {k:v for k,v in errors.items() if v}, 'source_hash_drift': drift,
        'excluded_dashboard_only_drift': excluded_drift,
        'definition_status': definition_status, 'problems': problems,
        'recent_market_input_reset_calls': dict(recent_events), 'market_epoch_gaps': gaps,
        'scope': 'Current-version health; cumulative signal inventory split by version/definition. No pooled cross-version PnL claim.',
        'signal_definition': 'Distinct first-qualified research root with positive quantity; includes direction-cap skips, not repeated observations. M2 primary all_candidates only; M3/M4 own roots. Independent markets reported separately.',
        'no_signal_policy': 'Zero new signals alone is not a fault and never authorizes changing gates. Pending official outcomes remain null; full_window is not gap-free input.',
    }
