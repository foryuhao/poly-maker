"""M9-only exhaustion thresholds; shared AP/EX and quality remain unchanged."""
from .model import D, EPS, ZERO


class LocalSellExhaustion:
    def __init__(self, manager, token, *, total_fraction, bin_fraction, resume_fraction='.50'):
        self.m, self.c, self.token = manager, manager.c, token
        self.total_fraction, self.bin_fraction = D(str(total_fraction)), D(str(bin_fraction))
        self.resume_fraction = D(str(resume_fraction))
        self.watches, self.active = {}, []

    def end_active(self, reason, reason_class='HARD_INVALID'):
        for fact in self.active:
            self.c.registry.end(fact, self.c.now, reason, reason_class)
        self.active.clear()

    def on_fact(self, fact):
        if fact.token != self.token:
            return
        if fact.kind == 'AP_BUY':
            self.watches.clear()
            self.end_active('REVERSE_AP')
        elif fact.kind == 'AP_SELL':
            self.watches[fact.id] = {'ap': fact, 'start': fact.confirmed,
                'gate': (fact.confirmed, fact.input_seq, fact.values.get('available_subindex', -1))}

    def reset(self, reason, reason_class='UNKNOWN'):
        self.end_active(reason, reason_class)
        self.watches.clear()

    def close(self):
        c = self.c
        for ident, watch in tuple(self.watches.items()):
            start, ap = watch['start'], watch['ap']
            end = start + .75
            if c.watermark < end-EPS:
                if c.now > end+.050+EPS:
                    del self.watches[ident]
                    c.event('M9_LOCAL_EX_UNAVAILABLE', {'token': self.token,
                            'reason': 'EX_INPUT_BACKLOG', 'ap_id': ident})
                continue
            del self.watches[ident]
            if (c.now > end+.050+EPS or not c.good[self.token] or not c.legal(self.token)
                    or not c.flows[self.token].covered(start, end)):
                c.event('M9_LOCAL_EX_UNAVAILABLE', {'token': self.token,
                        'reason': 'EX_WINDOW_UNAVAILABLE', 'ap_id': ident})
                continue
            volumes = [c.flows[self.token].volume(start+i*.25, start+(i+1)*.25,
                        'SELL', after_key=watch['gate']) for i in range(3)]
            reference = ap.values['volume']
            if (sum(volumes, ZERO) <= reference*self.total_fraction
                    and max(volumes) <= reference*self.bin_fraction):
                fact = c.fact('EX_SELL_M9_PRIVATE', self.token, c.now+.75, start, end,
                    refs=[ap.id], values={'volume': reference, 'side': 'SELL',
                        'ap_confirmed': ap.confirmed, 'strategy_scope': 'M9',
                        'window_seconds': .75, 'subwindow_volumes': volumes,
                        'total_fraction': str(self.total_fraction),
                        'bin_fraction': str(self.bin_fraction),
                        'pressure_resume_fraction': str(self.resume_fraction)})
                self.active.append(fact)
                self.m.on_fact(fact)
            else:
                c.event('M9_LOCAL_EX_NOT_CONFIRMED', {'token': self.token,
                        'ap_id': ident, 'subwindow_volumes': volumes})
        for fact in tuple(self.active):
            if (c.registry.valid(fact, c.now) and
                    c.flows[self.token].volume(c.now-.75, c.now, 'SELL', frontier=True)
                    >= fact.values['volume']*self.resume_fraction):
                c.registry.end(fact, c.now, 'EX_PRESSURE_RESUMED')
        self.active[:] = [f for f in self.active if c.registry.valid(f, c.now)]

    def release_runtime(self):
        self.watches.clear()
        self.active.clear()
        self.m = self.c = None
