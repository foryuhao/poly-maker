"""Exploratory M4 cohort over original qualified rounds; no extra raw feed.

One bid round is support evidence, not the baseline two-round RF definition.
No account, execution, price-return or settlement inputs are introduced.
"""
from decimal import Decimal

from polymaker.maker_queue import legal as queue_legal

STRATEGY = 'M4'
PARAMETERS = {
    'definition': 'm4-frequency-v2',
    'bid_support_rounds': 1, 'bid_support_ttl_sec': 8,
    'ask_reference_rounds': 1, 'ask_reference_min_recovery': '.50',
    'reference_ttl_sec': 15, 'weak_rounds': 1,
    'weak_min_loss_fraction': '.05',
    'weak_max_recovery': '.60', 'weak_max_reference_fraction': '.80',
    'normalized_shock_ratio_min': '.10', 'normalized_shock_ratio_max': '10',
    'df_ttl_sec': 5, 'recovered_stable_ask_min': '.50',
    'round_quality_and_resets': 'UNCHANGED_ORIGINAL_M4',
}


class M4FrequencySignals:
    def __init__(self, feed, emit):
        self.feed, self.emit = feed, emit
        self.states, self.candidates = {}, {}

    def state(self, token, side):
        return self.states.setdefault((token, side),
            {'namespace': None, 'support': None, 'reference': None, 'df': None})

    def expire(self, state, now):
        for key in ('support', 'reference', 'df'):
            value = state[key]
            if value and now >= value['deadline']:
                state[key] = None

    def end_candidate(self, token, reason):
        old = self.candidates.pop(token, None)
        if old:
            self.emit(STRATEGY, 'CANDIDATE_ENDED', {**old, 'reason': reason})

    def invalidate(self, reason, token=None):
        for key in list(self.states):
            if token is None or key[0] == token:
                self.states.pop(key)
        for t in list(self.candidates):
            if token is None or t == token:
                self.end_candidate(t, reason)

    def ingest(self, event, fact):
        if event == 'QUEUE_ENDED':
            self.states.pop((fact['token'], fact['side']), None)
            self.end_candidate(fact['token'], fact['reason'])
            return
        if event != 'ROUND_CLOSED':
            return
        now = float(fact['confirmed'])
        token, side = fact['token'], fact['side']
        state = self.state(token, side)
        namespace = fact['id'].split(':')[1]
        if state['namespace'] not in (None, namespace):
            self.states.pop((token, side))
            self.end_candidate(token, 'QUEUE_REBUILT')
            state = self.state(token, side)
        state['namespace'] = namespace
        self.expire(state, now)
        recovery, stable = Decimal(str(fact['recovery'])), bool(fact['stable'])
        if side == 'BID':
            state['support'] = None
            if stable:
                state['support'] = {'id': fact['id']+':SUPPORT',
                    'anchor': fact['anchor'], 'round': fact,
                    'deadline': now+PARAMETERS['bid_support_ttl_sec']}
                self.emit(STRATEGY, 'BID_SUPPORT_CONFIRMED', state['support'])
            return
        if state['df'] and stable and recovery >= Decimal(PARAMETERS['recovered_stable_ask_min']):
            state['df'] = None
        reference = state['reference']
        if reference:
            # Require a meaningful depletion before interpreting lack of refill.
            # loss is initial minus the round's lowest observed queue size.
            loss_fraction = Decimal(str(fact['loss']))/Decimal(str(fact['initial']))
            minimum_loss = Decimal(PARAMETERS['weak_min_loss_fraction'])
            ratios = (Decimal(str(fact['hit']))/Decimal(str(fact['initial']))/reference['hit'],
                      loss_fraction/reference['loss'])
            comparable = all(Decimal(PARAMETERS['normalized_shock_ratio_min']) <= x
                <= Decimal(PARAMETERS['normalized_shock_ratio_max']) for x in ratios)
            weak = recovery <= min(Decimal(PARAMETERS['weak_max_recovery']),
                reference['recovery']*Decimal(PARAMETERS['weak_max_reference_fraction']))
            if comparable and weak and loss_fraction >= minimum_loss:
                state['df'] = {'id': fact['id']+':DF', 'reference': reference,
                    'weak_round': fact, 'weak_loss_fraction': loss_fraction,
                    'deadline': now+PARAMETERS['df_ttl_sec']}
                self.emit(STRATEGY, 'DF_ASK_CONFIRMED', state['df'])
            elif comparable and weak:
                self.emit(STRATEGY, 'WEAK_ROUND_REJECTED', {
                    'token': token, 'side': side, 'round_id': fact['id'],
                    'reference_id': reference['id'], 'loss_fraction': loss_fraction,
                    'minimum_loss_fraction': minimum_loss,
                    'reason': 'ASK_LOSS_FRACTION_BELOW_MINIMUM'})
            state['reference'] = None
        if stable and recovery >= Decimal(PARAMETERS['ask_reference_min_recovery']):
            state['reference'] = {'id': fact['id']+':REFERENCE', 'round': fact,
                'recovery': recovery,
                'hit': Decimal(str(fact['hit']))/Decimal(str(fact['initial'])),
                'loss': Decimal(str(fact['loss']))/Decimal(str(fact['initial'])),
                'deadline': now+PARAMETERS['reference_ttl_sec']}
            self.emit(STRATEGY, 'ASK_REFERENCE_CONFIRMED', state['reference'])

    def tick(self):
        now = self.feed.monotonic()
        for state in self.states.values():
            self.expire(state, now)
        tokens = {token for token, _ in self.states} | set(self.candidates)
        for token in tokens:
            bid, ask = self.states.get((token, 'BID')), self.states.get((token, 'ASK'))
            support, df = (bid['support'] if bid else None), (ask['df'] if ask else None)
            book = self.feed.books[token]
            if (not support or not df or not queue_legal(self, book, now)
                    or not any(p == support['anchor'] and q > 0 for p, q in book.bids)):
                self.end_candidate(token, 'COMPONENT_UNAVAILABLE')
                continue
            key = support['id']+':'+df['id']
            if self.candidates.get(token, {}).get('id') == key:
                continue
            candidate = {'id': key, 'root': df['id'], 'token': token,
                'price': support['anchor'], 'confirmed': now,
                'deadline': min(support['deadline'], df['deadline']),
                'support_id': support['id'], 'df_id': df['id'],
                'definition': PARAMETERS['definition'], 'generation': self.feed.generation}
            self.candidates[token] = candidate
            self.emit(STRATEGY, 'CANDIDATE', candidate)
