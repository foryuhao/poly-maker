"""Bounded, public-book-only baseline fill observation; no account operations."""
from collections import Counter
from dataclasses import asdict

from polymaker.m4_state import decimal
from polymaker.maker_queue import legal

MODEL = 'BEST_ASK_MINUS_2TICK_WITHIN_10S_V1'
LEGACY_MODEL = MODEL
INSTANT_MODEL = 'FULL_FILL_AT_INSTRUCTION_PRICE'
EVIDENCE_POLICY = 'VALID_TOUCH_REQUIRED_NO_EVIDENCE_NO_FILL_V1'
LEGACY_POLICY = {'model': LEGACY_MODEL, 'window_sec': 10, 'offset_ticks': 2,
          'price_and_quantity': 'ORIGINAL_POST', 'touch': 'BEST_ASK_LE_THRESHOLD',
          'window_end_inclusive': True}
POLICY = {'model': MODEL, 'cutoff_sec': 240, 'offset_ticks': 1,
          'price_and_quantity': 'ORIGINAL_POST', 'touch': 'BEST_ASK_LE_THRESHOLD',
          'window_end_inclusive': True, 'cancel_unfilled_at_cutoff': True}


def finish_observation(ledger, post, state, *, wall, reason=None, touch=None, gaps=()):
    """Fill receipt and terminal observation are one durable transaction."""
    result = {'order_id': post['order_id'], 'opportunity_id': post['opportunity_id'],
              'model': post['model'], 'status': state, 'wall': wall,
              'post_wall': post['wall'], 'deadline_wall': post['deadline_wall'],
              'target_price': post['target_price'], 'tick_at_post': post['tick_at_post'],
              'quote': post['quote'], 'reason': reason, 'gaps': list(gaps), 'touch': touch}
    facts = [('virtual_observation', post['order_id'], result)]
    if state == 'FILLED':
        facts.append(('virtual_fill', post['order_id'], {
            'order_id': post['order_id'], 'opportunity_id': post['opportunity_id'],
            'quote': post['quote'], 'wall': touch['received_wall'], 'model': post['model'],
            'post_wall': post['wall'], 'touch': touch}))
    if post.get('cancel_unfilled_at_cutoff') and state == 'NOT_TOUCHED' and wall >= post['deadline_wall']:
        facts.append(('virtual_cancel', post['order_id'], {
            'order_id': post['order_id'], 'opportunity_id': post['opportunity_id'],
            'model': MODEL, 'effective_wall': post['deadline_wall'],
            'recorded_wall': wall, 'reason': 'T240_UNFILLED_SIMULATED_CANCEL',
            'real_cancel_sent': False}))
    ledger.fact_batch(facts)
    return result


def recover_observations(ledger, *, wall):
    """A lost interval has no confirmed fill and never opens a new window."""
    ended = {r['order_id'] for r in ledger.facts('virtual_observation')}
    for post in ledger.facts('virtual_post'):
        if post['order_id'] not in ended:
            finish_observation(ledger, post, 'NOT_TOUCHED', wall=wall,
                               reason='PROCESS_RESTART_OR_INCOMPLETE_WINDOW',
                               gaps=('PROCESS_RESTART_OR_INCOMPLETE_WINDOW',))


class TouchFillModel:
    def __init__(self, feed, ledger):
        self.policy = ledger.get('baseline_fill_model')
        if ledger.get('identity')['mode'] != 'VIRTUAL' or self.policy not in (POLICY, LEGACY_POLICY):
            raise ValueError('M4_BASELINE_FILL_MODEL_IDENTITY')
        self.feed, self.ledger = feed, ledger
        self.pending = {}
        recover_observations(ledger, wall=feed.wall())

    def post_start(self, attempt, *, cutoff_monotonic=None):
        # Same lock order as book callbacks. No received pre-POST quote can
        # sneak into a pending observer between the durable POST and arming it.
        with self.feed.lock:
            quote = asdict(attempt.quote)
            row = self.ledger.row(quote['window_id'])
            book = self.feed.books[quote['token_id']]
            now, wall = self.feed.monotonic(), self.feed.wall()
            anchor, tick = decimal(row['price']), book.tick
            deadline = now+self.policy.get('window_sec', 0)
            if self.policy == POLICY:
                cutoff = self.ledger.get('settings', {}).get('cutoff_sec', self.policy['cutoff_sec'])
                deadline = (cutoff_monotonic if cutoff_monotonic is not None else
                            now+int(self.feed.meta.slug.rsplit('-', 1)[1])+cutoff-wall)
            post = {'opportunity_id': row['id'], 'order_id': attempt.expected_order_id,
                    'quote': quote, 'wall': wall, 'post_monotonic': now,
                    'deadline_monotonic': deadline,
                    'deadline_wall': wall+deadline-now, 'model': self.policy['model'],
                    'anchor_price': str(anchor), 'tick_at_post': str(tick),
                    'target_price': str(anchor-self.policy['offset_ticks']*tick),
                    'cancel_unfilled_at_cutoff': self.policy == POLICY,
                    'generation': self.feed.generation}
            self.ledger.post_start(row['id'], baseline_post=post)
            self.pending[post['order_id']] = {'post': post, 'gaps': []}

    def gap(self, reason, token=None):
        for item in self.pending.values():
            if ((token is None or item['post']['quote']['token_id'] == token)
                    and reason not in item['gaps'] and len(item['gaps']) < 8):
                item['gaps'].append(reason)

    def on_book(self, book):
        """Called for coherent row-level books only after the frame validates."""
        if self.feed.m1.failure:
            self.gap('PUBLIC_FEED_VALIDATION_FAILED')
            return
        for oid, item in list(self.pending.items()):
            post = item['post']
            if book.token_id != post['quote']['token_id']:
                continue
            # Receipt time fixes the inclusive observation window. Source time
            # also excludes a pre-POST update that happened to arrive later.
            if not post['post_monotonic'] < book.received_mono <= post['deadline_monotonic']:
                continue
            if book.source_time < post['wall']:
                continue
            if not legal(self, book, self.feed.monotonic()):
                self.gap('INVALID_OR_STALE_BOOK', book.token_id)
                continue
            if book.best_ask <= decimal(post['target_price']):
                touch = {'best_ask': str(book.best_ask), 'best_ask_size': str(book.best_ask_size),
                         'received_wall': book.received_wall, 'received_monotonic': book.received_mono,
                         'source_time': book.source_time, 'generation': book.snapshot_generation,
                         'after_post_sec': book.received_mono-post['post_monotonic']}
                finish_observation(self.ledger, post, 'FILLED', wall=self.feed.wall(),
                                   touch=touch, gaps=item['gaps'])
                del self.pending[oid]

    def poll(self):
        with self.feed.lock:
            if not self.feed.connected:
                self.gap('PUBLIC_FEED_DISCONNECTED')
            if self.feed.m1.failure:
                self.gap('PUBLIC_FEED_VALIDATION_FAILED')
            now = self.feed.monotonic()
            # Finish only after pre-deadline inputs already queued for this
            # feed have drained. Retain any lost coverage as diagnostic evidence.
            queued = list(self.feed.m1_pending_received)
            if self.feed.m1_processing_received is not None:
                queued.append(self.feed.m1_processing_received)
            for oid, item in list(self.pending.items()):
                post = item['post']
                if self.feed.generation != post['generation']:
                    self.gap('PUBLIC_FEED_RECONNECTED', post['quote']['token_id'])
                if now <= post['deadline_monotonic']:
                    continue
                early = [t for t in queued if t <= post['deadline_monotonic']]
                if early and now-min(early) <= .2:
                    continue
                if early:
                    self.gap('PUBLIC_INPUT_BACKLOG', post['quote']['token_id'])
                finish_observation(self.ledger, post, 'NOT_TOUCHED', wall=self.feed.wall(),
                                   reason=('T240_NO_VALID_TOUCH_EVIDENCE' if post.get('cancel_unfilled_at_cutoff') else
                                           'NO_VALID_TOUCH_EVIDENCE' if item['gaps'] else 'TEN_SECOND_WINDOW_ENDED'),
                                   gaps=item['gaps'])
                del self.pending[oid]

    def stop(self, reason):
        with self.feed.lock:
            self.poll()
            for oid, item in list(self.pending.items()):
                finish_observation(self.ledger, item['post'], 'NOT_TOUCHED', wall=self.feed.wall(),
                                   reason=reason, gaps=item['gaps'])
                del self.pending[oid]

    def summary(self):
        with self.feed.lock:
            observations = self.ledger.facts('virtual_observation')
            counts = Counter(r['status'] for r in observations)
            return {'model': self.policy['model'], 'posted': len(self.ledger.facts('virtual_post')),
                    'offset_ticks': self.policy['offset_ticks'],
                    'observation_end': 'T240' if self.policy == POLICY else 'POST_PLUS_10_SECONDS',
                    'simulated_cancels': len(self.ledger.facts('virtual_cancel')),
                    'fill_evidence_policy': EVIDENCE_POLICY,
                    'pending': len(self.pending), 'confirmed_fills': counts['FILLED'],
                    'not_touched': counts['NOT_TOUCHED']+counts['UNKNOWN'], 'unknown': 0,
                    'legacy_unknown': counts['UNKNOWN'],
                    'gap_no_fill': sum(r['status'] == 'UNKNOWN' or
                        (r['status'] == 'NOT_TOUCHED' and bool(r.get('gaps'))) for r in observations),
                    'counterfactual_model': INSTANT_MODEL}
