"""Independent M3/M4 observers over the unchanged M2 public-book authority.

No account, signing or execution imports. A candidate is evidence, never a fill.
"""

from collections import Counter, deque
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from polymaker.config import RDPBAConfig, RDPCAObserveConfig
from polymaker.m2_config import M2Config
from polymaker.m2_signal import M2Signals
from polymaker.m4_rounds import M4Signals
from polymaker.rdp_sm_ba import BidAbsorption
from polymaker.rdp_sm_ca import CounterAttack, plain
from polymaker.rdp_sm_m1_signal import M1Signals
from polymaker.rdp_sm_marketdata import SessionFeed
from polymaker.rdp_sm_signal import number

ZERO = Decimal(0)


class M3Signals:
    def __init__(self, feed, emit):
        self.feed, self.emit = feed, emit
        self.campaigns, self.candidates = {}, {}
        self.ba = BidAbsorption(feed, RDPBAConfig(signal_lifetime_sec=3,
            max_book_age_ms=200, out_of_order_tolerance_ms=20))
        self.failure = None
        self.closed = False
        self.ca = CounterAttack(feed, self.ba.cfg,
            RDPCAObserveConfig(enabled=True, min_buy_shares=Decimal(10)), writer=self)

    def submit(self, record):
        """CounterAttack writer port; bounded persistence is owned by the hub."""
        event, payload = record['event'], record['payload']
        if event == 'CA_CONFIRMED':
            ep = payload['episode']
            token = ep['token_id']
            self.candidates.pop(token, None)
            self.ba.invalidate('M3_NEW_CA', token)
            self.ba.states.pop(token, None)
            self.campaigns[token] = ep
        accepted = self.emit('M3', event, payload)
        if accepted is False:
            self.failure = 'M3_EVENT_QUEUE_OVERFLOW'
        return accepted is not False

    def status(self):
        return {'failure': self.failure, 'mode': 'M234_BOUNDED_EVENTS'}

    def before(self, kind, row, book, received, wall):
        accepted = self.ca.safe('before_event', kind, row, book, received, wall)
        token = book.token_id
        campaign = self.campaigns.get(token)
        if (kind == 'last_trade_price' and campaign
                and received > campaign['tC']):
            self.ba.trade(row, book, received, wall)
        elif kind == 'best_bid_ask':
            self.ba.top(row, book, received, wall)
        elif kind == 'tick_size_change':
            self.invalidate('TICK_CHANGED', token)
        self.drain()
        return accepted

    def depth(self, row, old, new, received, wall, accepted):
        if accepted:
            self.ca.safe('depth', new)
        self.ba.depth(row, old, new, received, wall)
        self.drain()

    def drain(self):
        for row in self.ba.drain():
            self.emit('M3', 'BA_'+row['event'], row)

    def tick(self):
        self.ca.safe('tick')
        now = self.feed.monotonic()
        for token, campaign in list(self.campaigns.items()):
            ep = self.ca.states.get(token)
            ba = self.ba.states.get(token)
            if ba:
                self.ba.expire(ba, now)
            if (ep is None or ep.episode_id != campaign['episode_id'] or ep.end_reason
                    or now >= campaign['deadline']):
                self.invalidate('CA_ENDED', token)
                continue
            if (ba is None or ba.invalid_reason or ba.ba_confirmed_mono is None
                    or ba.signal_start_mono <= campaign['tC']):
                self.candidates.pop(token, None)
                continue
            book = self.feed.books[token]
            if (book.best_bid != ba.anchor_bid_price
                    or ba.anchor_bid_price != number(campaign['anchor_bid_price'])
                    or not self.ba.legal(book, now)):
                self.candidates.pop(token, None)
                continue
            key = campaign['episode_id']+':'+ba.episode_id
            if self.candidates.get(token, {}).get('id') != key:
                candidate = {'id': key, 'root': campaign['episode_id'], 'token': token,
                    'price': ba.anchor_bid_price, 'confirmed': now,
                    'deadline': min(campaign['deadline'], ba.order_deadline),
                    'ca_id': campaign['episode_id'], 'ba_id': ba.episode_id,
                    'generation': self.feed.generation}
                self.candidates[token] = candidate
                self.emit('M3', 'CANDIDATE', candidate)
        self.drain()

    def invalidate(self, reason, token=None):
        self.ca.safe('invalidate', reason, token)
        self.ba.invalidate(reason, token)
        for key in list(self.campaigns):
            if token is None or token == key:
                self.campaigns.pop(key, None)
                old = self.candidates.pop(key, None)
                if old:
                    self.emit('M3', 'CANDIDATE_ENDED', {**old, 'reason': reason})




class M234Hub:
    def __init__(self, feed, settings=None):
        self.feed, self.namespace = feed, uuid4().hex
        settings = settings or M2Config(host_cpu_guard_enabled=False, m1_cpu_guard_enabled=False)
        self.events = {s: deque() for s in ('M2', 'M3', 'M4')}
        self.failures, self.counts, self.context = {}, Counter(), {}
        self.m2_candidates = {}
        self.legacy_record = None
        self.last_observations, self.last_diagnostics = {}, {}
        self.m4_v2 = None
        self.m3 = M3Signals(feed, self.emit)
        self.m4 = M4Signals(feed, self.record_m4_evidence, features_only=settings.m4_frequency_study)
        if settings.m4_frequency_study:
            from polymaker.m4_frequency_signal import M4FrequencySignals
            self.m4_v2 = M4FrequencySignals(feed, self.emit)
        feed.m1 = M2Signals(feed, settings=settings, record=self.record_m2)

    def record_m4_evidence(self, strategy, event, payload):
        accepted = self.emit(strategy, event, payload)
        if accepted and self.m4_v2 and event in {'ROUND_CLOSED', 'QUEUE_ENDED'}:
            self.invoke('M4_V2', 'ingest', event, payload)
        return accepted

    def emit(self, strategy, event, payload):
        self.counts[strategy+':'+event] += 1
        if event == 'INPUT_UNAVAILABLE':
            key = (strategy, payload.get('token'), payload.get('reason'))
            now = self.feed.monotonic()
            if now-self.last_diagnostics.get(key, -1e9) < 1:
                return True
            self.last_diagnostics[key] = now
        if strategy in self.failures:
            return False
        queue = self.events[strategy]
        if len(queue) >= 4096:
            self.failures[strategy] = 'EVENT_QUEUE_OVERFLOW'
            return False
        queue.append({'strategy': strategy, 'event': event,
            'available_monotonic': self.feed.monotonic(), 'wall': self.feed.wall(),
            'frame': self.feed.m1_frame, 'generation': self.feed.generation,
            'payload': plain(payload)})
        return True

    def record_m2(self, row):
        accepted = self.legacy_record(row) if self.legacy_record else self.emit('M2', row['event'], row)
        if row['event'] == 'M2_OBSERVATION':
            token = row['token_id']
            self.m2_candidates[token] = {'id': row['candidate_id'], 'root': row['ap_id'],
                'token': token, 'price': row['anchor_bid_price'], 'confirmed': row['available_monotonic'],
                'deadline': row['deadline'], 'generation': row['generation'],
                'observation_valid': row['observation_valid'], 'buy_ratio_upper': row['buy_ratio_upper']}
        elif row['event'] == 'M2_CANDIDATE_ENDED':
            for token, c in list(self.m2_candidates.items()):
                if c['id'] == row['candidate_id']:
                    self.m2_candidates.pop(token)
        return accepted

    def invoke(self, strategy, method, *args):
        if strategy in self.failures:
            return None
        try:
            observer = {'M3': self.m3, 'M4': self.m4, 'M4_V2': self.m4_v2}[strategy]
            result = getattr(observer, method)(*args)
            failure = (observer.failure or observer.ca.failure) if strategy == 'M3' else None
            if failure:
                self.emit(strategy, 'OBSERVER_FAILED', {'reason': failure})
                self.failures[strategy] = failure
                return None
            return result
        except Exception as exc:
            target = 'M4' if strategy == 'M4_V2' else strategy
            self.emit(target, 'OBSERVER_FAILED', {'error_type': type(exc).__name__, 'observer': strategy})
            self.failures[strategy] = type(exc).__name__
            return None

    def before(self, kind, row, book, received, wall):
        key = (self.feed.m1_frame, id(row), book.token_id)
        old = self.context.get(book.token_id)
        if old is None or old['key'] != key:
            coherent = replace(book, reported_bid=None, reported_ask=None)
            accepted3 = self.invoke('M3', 'before', kind, row, coherent, received, wall)
            accepted4 = self.invoke('M4', 'before', kind, row, coherent, received, wall)
            old = {'key': key, 'a3': accepted3, 'a4': accepted4}
        self.context[book.token_id] = {**old, 'row': row, 'old': book,
            'received': received, 'wall': wall}
        return True

    def depth(self, book):
        c = self.context[book.token_id]
        coherent = replace(book, reported_bid=None, reported_ask=None)
        self.invoke('M3', 'depth', c['row'], c['old'], coherent,
                    c['received'], c['wall'], c['a3'])
        if c['a4']:
            self.invoke('M4', 'depth', coherent)

    def tick(self):
        self.invoke('M3', 'tick')
        self.invoke('M4', 'tick')
        if self.m4_v2:
            self.invoke('M4_V2', 'tick')

    def invalidate(self, reason):
        self.context.clear()
        self.m2_candidates.clear()
        self.invoke('M3', 'invalidate', reason)
        self.invoke('M4', 'invalidate', reason)
        if self.m4_v2:
            self.invoke('M4_V2', 'invalidate', reason)

    def reset(self):
        self.invalidate('GENERATION_CHANGED')
        self.m3.ca.safe('reset')
        self.m3.ba.reset()
        self.m4.sources.clear()
        self.m4.keys.clear()
        self.m4.key_set.clear()

    def candidates(self):
        self.tick()
        now = self.feed.monotonic()
        result = []
        groups = [('M2', self.m2_candidates), ('M3', self.m3.candidates),
                  ('M4', self.m4_v2.candidates if self.m4_v2 else self.m4.candidates)]
        for strategy, values in groups:
            if strategy in self.failures or (strategy == 'M4' and 'M4_V2' in self.failures):
                continue
            for c in values.values():
                if c['generation'] == self.feed.generation and c['deadline'] > now:
                    result.append({**c, 'strategy': strategy,
                        'outcome': 'YES' if c['token'] == self.feed.meta.yes.token_id else 'NO'})
        return result

    def active(self, candidate):
        return (candidate['generation'] == self.feed.generation
                and candidate['deadline'] > self.feed.monotonic()
                and any(c['strategy'] == candidate['strategy'] and c['id'] == candidate['id']
                        for c in self.candidates()))

    def observe_new(self, context):
        now = self.feed.monotonic()
        active = set()
        for c in self.candidates():
            if c['strategy'] == 'M2':
                continue
            key = (c['strategy'], c['id'])
            active.add(key)
            if now-self.last_observations.get(key, -1e9) < .25-1e-9:
                continue
            self.last_observations[key] = now
            book = self.feed.books[c['token']]
            visible = next((q for p, q in book.bids if p == c['price']), ZERO)
            pending_age = max(0., now-self.feed.m1_watermark())
            valid = (M1Signals.legal(self.feed.m1, book, now) and pending_age <= .020+1e-9
                     and visible > 0 and c['price'] < book.best_ask)
            self.emit(c['strategy'], 'OBSERVATION', {**c, **context(now),
                'observation_valid': valid, 'visible_bid_quantity': visible,
                'book_best_bid': book.best_bid, 'book_best_ask': book.best_ask,
                'tick': book.tick, 'pending_input_age_ms': pending_age*1000})
        self.last_observations = {k: v for k, v in self.last_observations.items() if k in active}


class M234Feed(SessionFeed):
    """Use existing CA observation hooks without adding BA to book filtering."""

    def __init__(self, *args, **kwargs):
        self.hub = None
        settings = kwargs.pop('research_settings', None)
        super().__init__(*args, **kwargs)
        self.hub = M234Hub(self, settings)

    def observe_ca(self, method, *args):
        if self.hub is None:
            return None
        if method == 'before_event':
            return self.hub.before(*args)
        if method == 'depth':
            self.hub.depth(args[0])
        elif method in {'tick', 'book_updated'}:
            self.hub.tick()
        elif method == 'reset':
            self.hub.reset()
        elif method in {'invalidate', 'disable', 'stop'}:
            self.hub.invalidate(str(args[0]) if args else method.upper())
        return None
