"""Fresh raw/control fixtures with natural policy submissions and hand economics."""
from copy import deepcopy
from decimal import Decimal

from m1234_fixtures import fixture_events
from polymaker.m1234_sim.events import digest


def reprice_early(value, token=None):
    if isinstance(value, list):
        return [reprice_early(item, token) for item in value]
    if not isinstance(value, dict):
        return value
    token = value.get('asset_id', token)
    result = {}
    for key, item in value.items():
        if key in {'price', 'best_bid', 'best_ask'} and token in {'1', '2'}:
            offset = Decimal('-.005') if token == '1' else Decimal('.005')
            result[key] = str(Decimal(item) + offset)
        else:
            result[key] = reprice_early(item, token)
    return result


def rr1_events(mode='normal'):
    """The two negative inputs contradict actual native M3 fill history."""
    if mode not in {'normal', 'filled-conflict', 'cancelled-partial-conflict'}:
        raise ValueError(mode)
    original = fixture_events()
    anchor_wall = original[0]['available_wall'] - 25
    definition = deepcopy(original[0]['definition'])
    definition['ticks']['1'] = '.001'
    definition['official_clock'] = [anchor_wall, 0, 0]
    definition['fixture_case'] = mode
    initial = reprice_early(deepcopy(original[2]['raw']))
    warm = [{**row, 'timestamp': str(anchor_wall * 1000)} for row in initial]
    scheduled = [
        (0., 'INIT', {'definition': definition}),
        (0., 'CONNECT', {}),
        (0., 'FRAME', {'raw': warm, 'received_mono': 0., 'received_wall': anchor_wall}),
        (.01, 'TIMER', {}),
        (25., 'FRAME', {'raw': initial, 'received_mono': 25.,
                         'received_wall': anchor_wall + 25}),
    ]
    for event in original[3:]:
        payload = {key: deepcopy(value) for key, value in event.items()
                   if key not in {'schema', 'seq', 'generation', 'available_mono', 'available_wall', 'kind'}}
        if event['kind'] == 'FRAME':
            payload['received_mono'] += 25
            if event['available_mono'] < 12:
                payload['raw'] = reprice_early(payload['raw'])
            raw = payload['raw']
            if (mode == 'filled-conflict' and event['available_mono'] == .25
                    and isinstance(raw, dict) and raw.get('event_type') == 'last_trade_price'):
                raw['size'] = '200'
        if event['kind'] == 'END':
            payload['reason'] = 'T300_OBSERVED'
        scheduled.append((event['available_mono'] + 25, event['kind'], payload))
    # Explicit artificial official response after the base tape's reconnect.
    ticks = {token: str(Decimal(tick)) for token, tick in definition['ticks'].items()}
    target = {view: {'generation': 2, 'revision': 0, 'ticks': ticks}
              for view in ['M1', 'M234']}
    request = {'request_id': 'tick:' + definition['market']['slug'] + ':1',
               'market': definition['market']['slug'], 'tokens': sorted(ticks),
               'target': target, 'target_id': digest(target), 'attempt': 1}
    scheduled.append((41.001, 'TICK_REQUEST', {'request': request}))
    scheduled.append((41.011, 'TICK_RESULT', {'result': {
        'request_id': request['request_id'], 'market': request['market'],
        'target': target, 'target_id': request['target_id'], 'status': 'SUCCESS',
        'ticks': ticks, 'source': 'ARTIFICIAL_DECLARED_RESPONSE',
        'sent_mono': 41.001, 'sent_wall': anchor_wall + 41.001,
        'received_mono': 41.010, 'received_wall': anchor_wall + 41.010,
    }}))
    if mode != 'normal':
        available = 25.4 if mode == 'filled-conflict' else 25.6
        if mode == 'cancelled-partial-conflict':
            scheduled.append((25.301, 'FRAME', {
                'received_mono': 25.301, 'received_wall': anchor_wall + 25.301,
                'raw': {'event_type': 'book', 'market': definition['market']['condition_id'],
                        'asset_id': '1', 'timestamp': str((anchor_wall + 25.301) * 1000),
                        'bids': [{'price': '.485', 'size': '80'}],
                        'asks': [{'price': '.515', 'size': '80'}]},
            }))
        scheduled.append((available, 'FRAME', {
            'received_mono': available, 'received_wall': anchor_wall + available,
            'raw': {'event_type': 'tick_size_change', 'market': definition['market']['condition_id'],
                    'asset_id': '1', 'old_tick_size': '.001', 'new_tick_size': '.01',
                    'timestamp': str((anchor_wall + 25.15) * 1000)},
        }))
    scheduled.sort(key=lambda row: row[0])
    tape = []
    generation = 0
    for index, (at, kind, payload) in enumerate(scheduled):
        pending = sorted(data['received_mono'] for _, event_kind, data in scheduled[index + 1:]
                         if event_kind == 'FRAME' and data['received_mono'] <= at)
        tape.append({'schema': original[0]['schema'], 'seq': index + 1,
                     'generation': generation, 'kind': kind, 'available_mono': at,
                     'available_wall': anchor_wall + at, 'pending': pending, **payload})
        if kind == 'CONNECT':
            generation += 1
    return tape
