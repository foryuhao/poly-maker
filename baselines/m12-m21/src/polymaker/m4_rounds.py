"""M4 original qualified-round extraction; shared by frequency study and execution."""
from collections import deque
from decimal import Decimal
from statistics import median
from types import SimpleNamespace
from uuid import uuid4

from polymaker.maker_queue import Round
from polymaker.maker_queue import hold as queue_hold
from polymaker.maker_queue import legal as queue_legal
from polymaker.maker_queue import recovery as queue_recovery
from polymaker.rdp_sm_signal import number

ZERO = Decimal(0)

class FixedRounds:
    """Direction-symmetric 500ms queue tests, using shared hold arithmetic."""

    recovery = staticmethod(queue_recovery)
    _hold = queue_hold

    def __init__(self, feed, token, side, emit, *, features_only=False):
        self.feed, self.token, self.side, self.emit = feed, token, side, emit
        self.features_only = features_only
        self.namespace = uuid4().hex
        self.cfg = SimpleNamespace(min_round_shares=Decimal(".5"), min_round_depth_fraction=Decimal(".0001"))
        self.anchor = self.round = self.rf = self.df = self.reference = None
        self.successes, self.weak = deque(maxlen=2), []
        self.counter = 0

    def identity(self, kind):
        self.counter += 1
        return f'm4:{self.namespace}:{self.feed.generation}:{self.token}:{self.side}:{kind}:{self.counter}'

    def qualified(self, r):
        minimum = max(self.cfg.min_round_shares, r.initial*self.cfg.min_round_depth_fraction)
        return r.hit >= minimum and r.initial-r.low >= minimum

    def levels(self, book):
        return book.bids if self.side == 'BID' else book.asks

    def quantity(self, book):
        return next((q for p, q in self.levels(book) if p == self.anchor), ZERO)

    def clear(self, reason):
        if self.anchor is not None:
            self.emit('M4', 'QUEUE_ENDED', {'token': self.token, 'side': self.side,
                'anchor': self.anchor, 'reason': reason, 'censored': True})
        self.anchor = self.round = self.rf = self.df = self.reference = None
        self.successes.clear()
        self.weak.clear()

    def hit(self, price, size, book, received):
        quantity = next((q for p, q in self.levels(book) if p == price), ZERO)
        if quantity <= 0:
            return
        if self.anchor is None:
            self.anchor = price
        if self.anchor != price:
            return
        if self.round is None:
            self.round = Round(self.identity('ROUND'), received, price, quantity, quantity)
        if received < self.round.start+.5:
            self.round.hit += size

    def depth(self, book):
        if self.anchor is None:
            return
        quantity = self.quantity(book)
        if quantity <= 0:
            self.clear('ANCHOR_DISAPPEARED')
            return
        r = self.round
        if r:
            if quantity < r.low:
                r.low, r.hold_start, r.held = quantity, None, False
            self._hold(r, quantity, self.feed.monotonic())

    def tick(self, book):
        now = self.feed.monotonic()
        if self.anchor is not None and self.quantity(book) <= 0:
            self.clear('ANCHOR_DISAPPEARED')
            return
        if self.rf and now >= self.rf['deadline']:
            self.rf = None
        if self.df and now >= self.df['deadline']:
            self.df = None
        if self.reference and now > self.reference['confirmed']+5:
            self.reference = None
            self.weak.clear()
        r = self.round
        if not r:
            return
        end = r.start+.5
        self._hold(r, self.quantity(book), min(now, end))
        if now < end or self.feed.m1_watermark() < end:
            if now > end+.05:
                self.clear('ROUND_INPUT_BACKLOG')
            return
        self.round = None
        if now > end+.05+1e-9:
            self.clear('ROUND_PROCESSING_LATE')
            return
        if not self.qualified(r):
            self.emit('M4', 'ROUND_UNQUALIFIED', {'token': self.token, 'side': self.side,
                'anchor': self.anchor, 'round_id': r.id})
            return
        recovery = self.recovery(r, self.quantity(book))
        stable = r.held and recovery >= Decimal('.5')
        fact = {'id': r.id, 'start': r.start, 'end': end, 'confirmed': now,
            'hit': r.hit, 'initial': r.initial, 'loss': r.initial-r.low,
            'recovery': recovery, 'stable': stable, 'anchor': r.anchor,
            'token': self.token, 'side': self.side}
        self.emit('M4', 'ROUND_CLOSED', fact)
        if self.features_only:
            return
        if self.side == 'ASK':
            self.ask_result(fact)
        if stable:
            self.successes.append(fact)
            span = 15 if self.side == 'BID' else 5
            if len(self.successes) == 2 and now-self.successes[0]['start'] <= span:
                if self.side == 'BID':
                    self.rf = {'id': self.identity('RF'), 'confirmed': now,
                        'deadline': now+8, 'anchor': self.anchor,
                        'rounds': list(self.successes)}
                    self.emit('M4', 'RF_BID_CONFIRMED', self.rf)
                    self.successes.clear()
                elif median(x['recovery'] for x in self.successes) >= Decimal('.7'):
                    self.reference = {'id': self.identity('REFERENCE'), 'confirmed': now,
                        'recovery': median(x['recovery'] for x in self.successes),
                        'hit': median(x['hit']/x['initial'] for x in self.successes),
                        'loss': median(x['loss']/x['initial'] for x in self.successes),
                        'rounds': list(self.successes)}
                    self.weak.clear()
        else:
            self.successes.clear()
            self.rf = None

    def ask_result(self, fact):
        ref = self.reference
        if self.df and fact['stable'] and fact['recovery'] >= Decimal('.7'):
            self.df = None
        if ref is None:
            return
        comparable = all(Decimal('.5') <= x <= 2 for x in (
            fact['hit']/fact['initial']/ref['hit'], fact['loss']/fact['initial']/ref['loss']))
        weak = fact['recovery'] <= min(Decimal('.3'), ref['recovery']/2)
        if not comparable or not weak:
            self.reference = None
            self.weak.clear()
            return
        self.weak.append(fact)
        if len(self.weak) == 2:
            self.df = {'id': self.identity('DF'), 'confirmed': self.feed.monotonic(),
                'deadline': self.feed.monotonic()+1, 'anchor': self.anchor,
                'reference': ref, 'weak_rounds': list(self.weak)}
            self.emit('M4', 'DF_ASK_CONFIRMED', self.df)
            self.reference = None
            self.weak.clear()


class M4Signals:
    def __init__(self, feed, emit, *, features_only=False):
        self.feed, self.emit = feed, emit
        self.features_only = features_only
        self.queues, self.sources, self.candidates = {}, {}, {}
        self.keys, self.key_set = deque(), set()

    def pair(self, token):
        if token not in self.queues:
            self.queues[token] = {s: FixedRounds(self.feed, token, s, self.emit, features_only=self.features_only)
                                  for s in ('BID', 'ASK')}
        return self.queues[token]

    def invalidate(self, reason, token=None):
        for t in list(self.queues):
            if token is None or token == t:
                for q in self.queues.pop(t).values():
                    q.clear(reason)
                self.candidates.pop(t, None)

    def before(self, kind, row, book, received, wall):
        token, now = book.token_id, self.feed.monotonic()
        try:
            if kind == 'tick_size_change':
                raise ValueError('TICK_CHANGED')
            stamp = number(row['timestamp'])/1000
            if not -.002-1e-9 <= wall-float(stamp) <= .500+1e-9:
                raise ValueError('SOURCE_AGE')
            floor = max(self.sources.get(token, ZERO), number(book.source_time))
            if stamp < floor:
                raise ValueError('SOURCE_REGRESSION')
            self.sources[token] = stamp
            if now-received > .2 or not queue_legal(self, book, now):
                raise ValueError('BOOK_UNAVAILABLE')
            if kind == 'last_trade_price':
                side, p, size = row['side'], number(row['price']), number(row['size'])
                if side not in {'BUY', 'SELL'} or size <= 0 or not 0 < p < 1 or p % book.tick:
                    raise ValueError('TRADE_INVALID')
                key = (token, stamp, side, p, size, row.get('transaction_hash'))
                if key in self.key_set:
                    raise ValueError('DUPLICATE_AMBIGUITY')
                self.keys.append(key)
                self.key_set.add(key)
                if len(self.keys) > 8192:
                    self.key_set.remove(self.keys.popleft())
                self.pair(token)['BID' if side == 'SELL' else 'ASK'].hit(p, size, book, received)
            return True
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            self.invalidate(str(exc), token)
            self.emit('M4', 'INPUT_UNAVAILABLE', {'token': token, 'reason': str(exc)})
            return False

    def depth(self, book):
        for q in self.pair(book.token_id).values():
            q.depth(book)

    def tick(self):
        now = self.feed.monotonic()
        for token, pair in list(self.queues.items()):
            book = self.feed.books[token]
            if not queue_legal(self, book, now):
                self.invalidate('BOOK_UNAVAILABLE', token)
                continue
            for q in pair.values():
                q.tick(book)
            if self.features_only:
                continue
            rf, df = pair['BID'].rf, pair['ASK'].df
            if not rf or not df:
                self.candidates.pop(token, None)
                continue
            key = rf['id']+':'+df['id']
            if self.candidates.get(token, {}).get('id') != key:
                candidate = {'id': key, 'root': df['id'], 'token': token,
                    'price': rf['anchor'], 'confirmed': now,
                    'deadline': min(rf['deadline'], df['deadline']),
                    'rf_id': rf['id'], 'df_id': df['id'], 'generation': self.feed.generation}
                self.candidates[token] = candidate
                self.emit('M4', 'CANDIDATE', candidate)
