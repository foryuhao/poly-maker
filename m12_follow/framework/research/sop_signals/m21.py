"""M21_ENDPOINT_COFLOW: same-window recovered Bid and zero-BUY Ask loss.

Endpoint recovery is mandatory; historical strength fractions and100ms holds
are diagnostics. Fixed F/U guards and original3s expiry never renew.
"""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal as D

from .m12 import parameter_identity
from .m19_path import AnchorRoundPath, Observation, number

VARIANT = 'M21_ENDPOINT_COFLOW'
VERSION = 'm21-endpoint-recovery-withdrawal-v1/raw-joint-endpoint-r1'
SOP_FILE = 'V_M21_ENDPOINT_COFLOW.md'
SOP_SHA256 = 'a7e36318bb3d0098c0a7c6a0425925c51d7dbdd9ba21eba7c79f54cd76b7ee0c'
DOCUMENT_PARAMETERS_SHA256 = 'f94e8d3b8c7c0e171294f2eec1d8ed664eae2da345b7fd32ff03ab997525f7f2'


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = .5
    first_hit_min: D = D('5')
    first_loss_min: D = D('.5')
    recovery_min: D = D('.50')
    ask_loss_min: D = D('.5')
    ask_loss_fraction: D = D('.10')
    bid_guard_fraction: D = D('.40')
    ask_retained_loss: D = D('.50')
    diagnostic_hold_seconds: float = .1
    diagnostic_hit_fraction: D = D('.10')
    diagnostic_loss_fraction: D = D('.05')
    publication_lateness: float = .05
    ttl: float = 3.
    variant_id: str = VARIANT


def signal_version(parameters):
    return VERSION + '/p-' + parameter_identity(parameters)[1][:12]


@dataclass
class Root:
    id: str
    token: str
    price: D
    ask: D
    generation: int
    path: AnchorRoundPath
    a0: D
    g: D
    initial_bid_ref: tuple
    initial_ask_ref: tuple
    started_key: tuple
    ask_end: D
    bid_now: D
    ask_terminal: dict | None = None
    ask_hold_since: float | None = None
    post_bid_min: D | None = None
    post_ask_max: D | None = None
    fact: dict | None = None
    ready: bool = False
    confirmed: float | None = None
    confirmation_key: tuple | None = None
    deadline: float | None = None
    floor: D | None = None
    ceiling: D | None = None
    followup: AnchorRoundPath | None = None
    closed_end: D | None = None


class M21:
    strategy = 'M21'

    def __init__(self, emit, parameters=None):
        self.emit, self.p = emit, parameters or Parameters()
        self.version = signal_version(self.p)
        self.parameter_sha256 = parameter_identity(self.p)[1]
        self.now, self.key, self.serial = 0., (0., 0, -1), 0
        self.roots, self.signals, self.rounds = {}, {}, {}
        self.components, self.quality, self.last_end = {}, {}, {}
        self.buy_presence, self.buy_overflow = {}, {}
        self.counts = Counter()

    def event(self, event, **payload):
        self.counts[event] += 1
        self.emit(event, {'strategy':self.strategy, 'variant_id':VARIANT,
            'version':self.version, 'parameter_sha256':self.parameter_sha256,
            'available':self.now, 'key':self.key, **payload})

    def clock(self, now, seq, subindex=-1):
        if now < self.now:
            raise ValueError('SOP_AVAILABLE_CLOCK_REGRESSION')
        self.now, self.key = now, (now, seq, subindex)
        for token, root in tuple(self.roots.items()):
            if root.deadline is not None and now >= root.deadline:
                self.end(token, 'TTL_EXPIRED')
        self.buy_presence = {k:v for k,v in self.buy_presence.items() if v[0] >= now-1.}
        self.buy_overflow = {k:v for k,v in self.buy_overflow.items() if v >= now-1.}

    def end(self, token, reason, *, unknown=False):
        root = self.roots.pop(token, None)
        if root is None:
            return
        self.last_end[token] = self.now, self.key
        self.rounds.pop((token,'BID',root.price), None)
        if unknown:
            self.event('ROOT_UNKNOWN', id=root.id, token=token, reason=reason)
        self.event('ROOT_ENDED', id=root.id, token=token, reason=reason)
        sid = 'M21:signal:' + root.id
        if self.signals.pop(sid,None) is not None:
            self.event('SIGNAL_ENDED', id=sid, token=token, reason=reason)
        root.path.release()
        if root.followup:
            root.followup.release()

    def invalidate(self, token, reason):
        self.quality[token] = reason
        self.end(token, reason, unknown=True)
        self.event('QUALITY_UNKNOWN', token=token, reason=reason)

    def new_generation(self, token, generation):
        self.end(token, 'BOOK_RECONSTRUCTED', unknown=True)
        self.quality.pop(token,None)
        self.event('BOOK_GENERATION_STARTED', token=token, generation=generation)

    def remember_buy(self, token, price, received, *, proven):
        key = token, price
        if key not in self.buy_presence and len(self.buy_presence) >= 4096:
            self.buy_overflow[token] = max(received,self.buy_overflow.get(token,float('-inf')))
            self.invalidate(token,'DIRECT_BUY_PRESENCE_CAPACITY')
            return
        old = self.buy_presence.get(key)
        if old is None or received > old[0]:
            self.buy_presence[key] = received, proven
        elif received == old[0]:
            self.buy_presence[key] = received, old[1] or proven

    def observe_direct_trade(self, token, side, price, received):
        if side != 'BUY':
            return
        self.remember_buy(token,price,received,proven=True)
        root = self.roots.get(token)
        if root and price == root.ask and received >= root.path.start:
            if root.confirmed is None or received <= root.confirmed:
                self.end(token,'BUY_IN_FORMATION_THROUGH_PUBLICATION')

    def observe_unproven_buy(self, token, price, received):
        self.remember_buy(token,price,received,proven=False)
        root = self.roots.get(token)
        if root and (price is None or price == root.ask) and received >= root.path.start:
            if root.confirmed is None or received <= root.confirmed:
                self.end(token,'DIRECT_BUY_ABSENCE_UNPROVEN',unknown=True)

    def point(self, root, kind, quantity, received, source, input_ref):
        if source is None or input_ref is None or len(input_ref)!=3:
            self.invalidate(root.token,'EXPLICIT_QUANTITY_OR_TRADE_SOURCE_UNPROVEN')
            return None
        return Observation(kind,root.token,root.price,root.generation,received,self.now,
                           *input_ref,source,quantity)

    def level_missing(self, token, side, price, received, **_):
        root = self.roots.get(token)
        if root and (side,price) in {('BID',root.price),('ASK',root.ask)}:
            self.invalidate(token,'ORIGINAL_LEVEL_ABSENT_IN_FULL_BOOK')

    def depth(self, token, side, price, quantity, received, *, generation=None,
              input_ref=None, source=None, kind=None):
        root = self.roots.get(token)
        if root is None or (side,price) not in {('BID',root.price),('ASK',root.ask)}:
            return
        if generation != root.generation or quantity <= 0:
            self.invalidate(token,'ORIGINAL_LEVEL_OR_GENERATION_LOST')
            return
        if received < root.path.start or (root.closed_end is not None and number(received)<root.closed_end):
            self.invalidate(token,'LATE_QUANTITY_TO_CLOSED_OR_PRE_ROOT_WINDOW')
            return
        if side == 'BID':
            root.bid_now = quantity
            path = root.path if not root.path.closed else root.followup
            if path is not None:
                point = self.point(root,kind,quantity,received,source,input_ref)
                if point is None:
                    return
                path.observe(point)
                if path.failure:
                    self.invalidate(token,path.failure)
                    return
        else:
            if source is None or input_ref is None or kind not in {'ASK_BOOK','ASK_PRICE_CHANGE'}:
                self.invalidate(token,'EXPLICIT_ASK_TERMINAL_SOURCE_UNPROVEN')
                return
            if number(received) < root.path.end:
                root.ask_end = quantity
                root.ask_terminal = {'quantity':quantity, 'received':received,
                    'source':source, 'input_ref':input_ref, 'kind':kind}
                if quantity <= root.a0-root.g:
                    if root.ask_hold_since is None:
                        root.ask_hold_since = received
                else:
                    root.ask_hold_since = None
        if number(received) >= root.path.end:
            if side == 'BID':
                root.post_bid_min = quantity if root.post_bid_min is None else min(root.post_bid_min,quantity)
            else:
                root.post_ask_max = quantity if root.post_ask_max is None else max(root.post_ask_max,quantity)
        if root.confirmed is not None and (
                (side=='BID' and quantity<root.floor) or (side=='ASK' and quantity>root.ceiling)):
            self.end(token,'CURRENT_FIXED_F_OR_U_BROKEN')

    def requires_pre_hit_for_trade(self, *, token, side, price, received,
                                   pre_event_best_bid=None, **_):
        root = self.roots.get(token)
        return side=='SELL' and price==(root.price if root else pre_event_best_bid)

    def trade(self, *, token, side, price, quantity, received, q0, generation,
              input_ref=None, source=None, initial_quantity_ref=None,
              pre_event_best_bid=None, pre_event_best_ask=None,
              opposite_quantity=None, opposite_quantity_ref=None):
        if side != 'SELL':
            return
        root = self.roots.get(token)
        if price != (root.price if root else pre_event_best_bid):
            return
        if root is None:
            ended = self.last_end.get(token)
            if ended and (received<=ended[0] or self.key<=ended[1]):
                return
            a = pre_event_best_ask
            if a is None or not price<a or q0<=0 or opposite_quantity is None or opposite_quantity<=0:
                return
            refs = (initial_quantity_ref,opposite_quantity_ref)
            if (source is None or input_ref is None or any(ref is None or ref[2]>=input_ref
                    or ref[0]>received or ref[1]>=source for ref in refs)):
                self.invalidate(token,'JOINT_PRE_SELL_BID_ASK_QUANTITIES_UNPROVEN')
                return
            previous = [self.buy_presence[k] for k in ((token,a),(token,None))
                        if k in self.buy_presence and self.buy_presence[k][0]>=received]
            if previous or self.buy_overflow.get(token,float('-inf'))>=received:
                self.event('ROOT_NOT_STARTED',token=token,reason='BUY_OR_UNKNOWN_BUY_AT_FIRST_RECEIPT')
                return
            g = max(self.p.ask_loss_min,self.p.ask_loss_fraction*opposite_quantity)
            if g >= opposite_quantity:
                self.event('ROOT_NOT_STARTED',token=token,reason='NO_POSITIVE_ASK_QUANTITY_SOLUTION')
                return
            self.serial += 1
            path = AnchorRoundPath(token=token,side='BID',price=price,generation=generation,
                                   start=received,q0=q0,round_seconds=self.p.round_seconds)
            root = Root(f'M21:{generation}:{token}:root:{self.serial}',token,price,a,generation,
                        path,opposite_quantity,g,initial_quantity_ref,opposite_quantity_ref,
                        self.key,opposite_quantity,q0)
            self.roots[token]=root
            self.rounds[token,'BID',price]=path
            self.event('JOINT_ROUND_STARTED',id=root.id,token=token,price=price,ask=a,
                       q0=q0,a0=root.a0,g=g,received=received,input_ref=input_ref,
                       initial_bid_ref=initial_quantity_ref,initial_ask_ref=opposite_quantity_ref)
        if root.closed_end is not None and number(received)<root.closed_end:
            self.invalidate(token,'LATE_SELL_TO_CLOSED_WINDOW')
            return
        path = root.path if not root.path.closed else root.followup
        if root.confirmed is not None and path is None:
            if received<=root.confirmed or self.key<=root.confirmation_key:
                return
            if initial_quantity_ref is None or initial_quantity_ref[1]>=source or initial_quantity_ref[2]>=input_ref:
                self.invalidate(token,'FOLLOWUP_PRE_HIT_QUANTITY_UNPROVEN')
                return
            path=AnchorRoundPath(token=token,side='BID',price=price,generation=generation,
                                 start=received,q0=q0,round_seconds=self.p.round_seconds)
            root.followup=path
            self.rounds[token,'BID',price]=path
            self.event('FOLLOWUP_ROUND_STARTED',id=root.id,token=token,received=received)
        if path is not None:
            point=self.point(root,'DIRECT_SELL',quantity,received,source,input_ref)
            if point is not None:
                path.observe(point)
                if path.failure:
                    self.invalidate(token,path.failure)

    def close(self, watermark, covered):
        for token,root in tuple(self.roots.items()):
            path=root.path if not root.path.closed else root.followup
            if path is None or number(watermark)<=path.end:
                continue
            fact=path.finish(available=self.now,watermark=watermark,
                             coverage_proven=covered(token,float(path.start),float(path.end)))
            self.rounds.pop((token,'BID',root.price),None)
            root.closed_end=path.end
            self.event('BID_ENDPOINT_ROUND_CLOSED',id=root.id,followup=root.confirmed is not None,**fact)
            if not fact['data_proven']:
                self.invalidate(token,fact['reason'])
                continue
            if root.confirmed is not None:
                root.followup=None
                if fact['qualified'] and fact['recovery']<self.p.recovery_min:
                    self.end(token,'FOLLOWUP_QUALIFIED_ENDPOINT_RECOVERY_FAILED')
                path.release()
                continue
            terminal=root.ask_terminal
            if (terminal is None or tuple(terminal['input_ref'])<=tuple(fact['last_trade'][k] for k in ('frame','row','change'))
                    or number(terminal['source'])<=fact['maximum_trade_source']
                    or not path.start<=number(terminal['received'])<path.end):
                self.invalidate(token,'ASK_TERMINAL_AFTER_ALL_COUNTED_SELLS_UNPROVEN')
                continue
            if (fact['hits']<self.p.first_hit_min or fact['loss']<self.p.first_loss_min
                    or fact['recovery']<self.p.recovery_min or root.a0-root.ask_end<root.g):
                self.end(token,'FIRST_JOINT_ENDPOINT_OR_MATERIALITY_NOT_QUALIFIED')
                continue
            root.fact=fact
            root.floor=fact['qmin']+self.p.bid_guard_fraction*fact['loss']
            root.ceiling=root.a0-self.p.ask_retained_loss*(root.a0-root.ask_end)
            root.ready=True

    def publish(self):
        for token,root in tuple(self.roots.items()):
            if not root.ready:
                continue
            root.ready=False
            if number(self.now)>root.path.end+number(self.p.publication_lateness):
                self.invalidate(token,'JOINT_PUBLICATION_TOO_LATE')
                continue
            if ((root.post_bid_min is not None and root.post_bid_min<root.floor)
                    or (root.post_ask_max is not None and root.post_ask_max>root.ceiling)):
                self.end(token,'POST_WINDOW_GUARD_BROKEN_BEFORE_PUBLICATION')
                continue
            root.confirmed,root.confirmation_key,root.deadline=self.now,self.key,self.now+self.p.ttl
            fact=root.fact
            signal={'id':'M21:signal:'+root.id,'root_id':root.id,'token':token,
                    'variant_id':VARIANT,'version':self.version,'anchor_price':root.price,
                    'ask_anchor':root.ask,'confirmed':self.now,'deadline':root.deadline,
                    'guard_floor':root.floor,'ask_ceiling':root.ceiling,'generation':root.generation,
                    'bid':fact,'ask':{'a0':root.a0,'g':root.g,'aend':root.ask_end,
                    'net_loss':root.a0-root.ask_end,'terminal':root.ask_terminal,
                    'initial_quantity_ref':root.initial_ask_ref},
                    'initial_bid_ref':root.initial_bid_ref,'quality_status':'PROVEN',
                    'diagnostics':{'bid_hit_fraction_at_least_10pct':fact['hits']/fact['q0']>=self.p.diagnostic_hit_fraction,
                    'bid_loss_fraction_at_least_5pct':fact['loss']/fact['q0']>=self.p.diagnostic_loss_fraction,
                    'bid_final_100ms_recovered':fact['stable_since'] is not None and root.path.end-fact['stable_since']>=number(self.p.diagnostic_hold_seconds),
                    'ask_final_100ms_net_loss':root.ask_hold_since is not None and root.path.end-number(root.ask_hold_since)>=number(self.p.diagnostic_hold_seconds),
                    'ask_prequiet_250ms':None,'ask_prequiet_availability':'NOT_COLLECTED_OPTIONAL_DIAGNOSTIC'}}
            self.signals[signal['id']]=signal
            self.event('SIGNAL_CONFIRMED',**signal)

    def release(self):
        for root in self.roots.values():
            root.path.release()
            if root.followup:
                root.followup.release()
        for values in (self.roots,self.signals,self.rounds,self.components,self.quality,
                       self.last_end,self.buy_presence,self.buy_overflow):
            values.clear()
