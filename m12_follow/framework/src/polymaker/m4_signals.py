"""M4-only public feed adapter; no M1/M2/M3 strategy is instantiated."""
from collections import Counter, deque
from dataclasses import asdict, replace
from decimal import Decimal
from types import SimpleNamespace

from polymaker.m4_frequency_signal import M4FrequencySignals
from polymaker.m4_rounds import M4Signals
from polymaker.m4_types import Opportunity
from polymaker.maker_queue import legal
from polymaker.rdp_sm_marketdata import SessionFeed
from polymaker.rdp_sm_signal import number


class M4LiveSignals:
    m4_only = True
    legal = legal

    def __init__(self, feed, record=None, *, price_risk=None, taker_strategy=None):
        self.feed, self.record = feed, record
        self.price_risk = price_risk
        self.taker_strategy = taker_strategy
        self.counts, self.events, self.source = Counter(), deque(maxlen=4096), {}
        self.failure = self.decision_reason = self.selected = None
        self.cfg = SimpleNamespace(mirror_complement_flow=False)
        self.used_roots, self.opportunities, self.context, self.last_diagnostic = set(), {}, {}, {}
        self.rounds = M4Signals(feed, self.round_event, features_only=True)
        self.frequency = M4FrequencySignals(feed, self.component_event)

    def emit(self, event, **payload):
        self.counts[event] += 1
        if event in {'INPUT_UNAVAILABLE', 'UNAVAILABLE', 'EVENT_DECISION'}:
            # Depth batches repeat the same acceptance decision for every level.
            # Count them all for heartbeats, but journal at most one diagnostic
            # per token/kind/reason/acceptance/generation each second. Strategy,
            # round and execution facts below remain unsampled.
            key = (event, payload.get('token'), payload.get('token_id'), payload.get('reason'),
                   payload.get('kind'), payload.get('accepted'), self.feed.generation)
            now = self.feed.monotonic()
            if now-self.last_diagnostic.get(key, -1e9) < 1:
                return
            self.last_diagnostic[key] = now
        row = {'event': event, 'available_monotonic': self.feed.monotonic(),
               'generation': self.feed.generation, 'strategy': 'm4-frequency-v2', **payload}
        self.events.append(row)
        if not self.failure and self.record is not None and self.record(row) is False:
            self.failure = 'M4_EVENT_RECORDING_FAILED'
            self.invalidate(self.failure)

    def component_event(self, strategy, event, payload):
        self.emit(event, **payload)

    def round_event(self, strategy, event, payload):
        self.component_event(strategy, event, payload)
        if event in {'ROUND_CLOSED', 'QUEUE_ENDED'}:
            self.frequency.ingest(event, payload)

    def before_event(self, kind, row, book, received, wall, *, books=None):
        """Preserve shared-book rejection of stale depth independently of signals."""
        self.decision_reason = None
        token, now = book.token_id, self.feed.monotonic()
        if self.failure:
            return False
        try:
            stamp = number(row['timestamp'])/1000
            if not -.002-1e-9 <= self.feed.wall()-float(stamp) <= .500+1e-9:
                raise ValueError('M4_SOURCE_AGE')
            previous = max(self.source.get(token, Decimal(0)), number(book.source_time))
            if stamp < previous:
                if (previous-stamp)*1000 <= 20:
                    self.decision_reason = 'M4_EVENT_OUT_OF_ORDER_IGNORED'
                    return False
                raise ValueError('M4_SOURCE_REGRESSION')
            self.source[token] = stamp
            if now-received > .2 or received > now:
                raise ValueError('M4_PROCESSING_GAP')
            return True
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            self.decision_reason = str(exc)
            observer = getattr(self.feed, 'fill_model', None)
            if observer is not None and kind in {'book', 'price_change', 'tick_size_change'}:
                observer.gap(self.decision_reason, token)
            self.invalidate(self.decision_reason, token)
            return False

    def observe_before(self, kind, row, book, received, wall):
        key = (self.feed.m1_frame, id(row), book.token_id)
        old = self.context.get(book.token_id)
        if old is None or old[0] != key:
            coherent = replace(book, reported_bid=None, reported_ask=None)
            accepted = self.rounds.before(kind, row, coherent, received, wall)
            self.context[book.token_id] = (key, accepted)
        return True

    def observe_depth(self, book):
        if self.context.get(book.token_id, (None, False))[1]:
            self.rounds.depth(replace(book, reported_bid=None, reported_ask=None))

    def depth_quantity(self, book):
        pass  # M4's before/depth hook owns the fixed-queue evidence.

    def depth(self, book, received):
        batch = getattr(self.feed, 'fill_observation_batch', None)
        if batch is not None:
            batch.append(book)

    def tick(self, **kwargs):
        if self.failure:
            return
        try:
            self.rounds.tick()
            self.frequency.tick()
        except Exception as exc:
            self.failure = 'M4_OBSERVER_FAILED:'+type(exc).__name__
            self.invalidate(self.failure)
            self.emit('OBSERVER_FAILED', reason=self.failure)

    def invalidate(self, reason, token=None):
        self.rounds.invalidate(reason, token)
        self.frequency.invalidate(reason, token)
        self.selected = None

    def reset(self):
        self.invalidate('GENERATION_CHANGED')
        self.context.clear()
        self.source.clear()
        self.rounds.sources.clear()
        self.rounds.keys.clear()
        self.rounds.key_set.clear()

    def evaluate(self, snapshot):
        self.tick()
        candidates = []
        if not self.failure:
            # The journal callback may invalidate and clear candidates in emit().
            for candidate in tuple(self.frequency.candidates.values()):
                root = candidate['root']
                if root in self.used_roots or candidate['deadline'] <= self.feed.monotonic():
                    continue
                token = candidate['token']
                opportunity = Opportunity(root, token,
                    'YES' if token == self.feed.meta.yes.token_id else 'NO',
                    candidate['price'], candidate['confirmed'], candidate['deadline'],
                    candidate['support_id'], root, candidate['generation'], price_risk=self.price_risk,
                    taker_strategy=self.taker_strategy,
                    market_id=self.feed.meta.condition_id if self.taker_strategy else None)
                if self.opportunities.get(root) != opportunity:
                    self.opportunities[root] = opportunity
                    self.emit('OPPORTUNITY_IDENTIFIED', opportunity=asdict(opportunity))
                    if self.failure:
                        self.selected = None
                        return
                candidates.append(opportunity)
        parity = sum(self.feed.meta.condition_id.encode()) % 2
        self.selected = min(candidates, key=lambda o: (o.signal_start_mono,
            (o.outcome == 'YES') == bool(parity))) if candidates else None

    def active_reason(self, claim, snapshot):
        self.tick()
        if self.failure:
            return self.failure
        opportunity = self.opportunities.get(claim[0])
        if opportunity is None or opportunity.generation != self.feed.generation:
            return 'M4_EPISODE_CHANGED'
        if self.feed.monotonic() >= opportunity.order_deadline:
            return 'M4_OPPORTUNITY_EXPIRED'
        candidate = self.frequency.candidates.get(opportunity.token_id)
        if (candidate is None or candidate['root'] != opportunity.root_id
                or candidate['support_id'] != opportunity.support_id):
            return 'M4_EVIDENCE_ENDED'
        return None

    def take(self, opportunity):
        with self.feed.lock:
            if (opportunity.root_id in self.used_roots
                    or self.active_reason((opportunity.episode_id,), None) is not None):
                return False
            self.used_roots.add(opportunity.root_id)
            return True

    def diagnostics(self):
        return {'strategy': 'm4-frequency-v2', 'failure': self.failure, 'counts': dict(self.counts)}


class M4Feed(SessionFeed):
    def __init__(self, *args, record=None, price_risk=None, taker_strategy=None, **kwargs):
        kwargs['normal_entry_mode'] = 'm4_v2'
        super().__init__(*args, **kwargs)
        # SessionFeed's historic name denotes its event-validation port only.
        self.m1 = M4LiveSignals(self, record, price_risk=price_risk, taker_strategy=taker_strategy)
        self.fill_model = None
        self.fill_observation_batch = None

    def _apply(self, raw, **kwargs):
        observer = self.fill_model
        if observer is None:
            return super()._apply(raw, **kwargs)
        # Keep every coherent row in a multi-row frame, including a transient
        # touch followed by a rebound. Never use partially validated frames.
        self.fill_observation_batch = []
        try:
            result = super()._apply(raw, **kwargs)
            for book in self.fill_observation_batch:
                observer.on_book(book)
            return result
        finally:
            self.fill_observation_batch = None

    def release_signal_memory(self):
        """Discard persisted signal/depth buffers after close and worker drain.

        Keep counters, failures, consumed roots and book identity/tick metadata.
        Order identities, direction claims and risk belong to the intact ledger.
        """
        with self.lock:
            if not self.stop_event.is_set() or (self.task is not None and not self.task.done()):
                raise RuntimeError('M4_MEMORY_FEED_NOT_STOPPED')
            detector = self.m1
            buffers = [detector.events, detector.source, detector.context,
                       detector.opportunities, detector.last_diagnostic,
                       detector.rounds.queues, detector.rounds.sources, detector.rounds.candidates,
                       detector.rounds.keys, detector.rounds.key_set,
                       detector.frequency.states, detector.frequency.candidates,
                       self.probability_points, self.repair_price_points,
                       self.signal_changes, self.normal_signals, self.m1_pending_received]
            released = {'buffer_items': sum(len(buffer) for buffer in buffers),
                        'depth_levels': sum(len(b.bids)+len(b.asks) for b in self.books.values())}
            for buffer in buffers:
                buffer.clear()
            detector.selected = None
            self.books = {t: replace(b, bids=(), asks=(), reported_bid=None, reported_ask=None)
                          for t, b in self.books.items()}
            self.m1_processing_received = None
            self.task = None
            return released

    def observe_ca(self, method, *args):
        detector = getattr(self, 'm1', None)
        if not isinstance(detector, M4LiveSignals):
            return None
        observer = getattr(self, 'fill_model', None)
        if observer is not None:
            if method in {'invalidate', 'reset', 'stop'}:
                observer.gap(str(args[0]) if args else method.upper())
            elif (method == 'before_event' and args[0] in {'book', 'price_change', 'tick_size_change'}
                  and detector.decision_reason not in {None, 'M4_EVENT_OUT_OF_ORDER_IGNORED'}):
                observer.gap(detector.decision_reason, args[2].token_id)
        if method == 'before_event':
            return detector.observe_before(*args)
        if method == 'depth':
            detector.observe_depth(args[0])
        elif method in {'tick', 'book_updated'}:
            detector.tick()
        elif method in {'invalidate', 'disable', 'stop'}:
            detector.invalidate(str(args[0]) if args else method.upper())
        return None
