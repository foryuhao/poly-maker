"""M23_FIRST_SELL_WHILE_VALID: explicit preload -> first SELL -> buffer proof.

This is not an RF/QR round. Loss may be zero or exceed observed SELL volume.
Every deadline belongs to the original confirmed seed; 750ms is diagnostic only.
"""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal as D

from .m12 import parameter_identity

VARIANT = "M23_FIRST_SELL_WHILE_VALID"
VERSION = "m23-buffer-first-sell-while-valid-v1/raw-path-v2"
SOP_FILE = "V_M23_FIRST_SELL_WHILE_VALID.md"
SOP_SHA256 = "498e3d1af5e710e6c79e82a7836282ac61cb7e320f67de081d22cc70b80f1b09"
DOCUMENT_PARAMETERS_SHA256 = "72a82c851bbb00008f7e6b0bfb97209c87bbe850e04b82aaddb59cc0fbf6ea55"


@dataclass(frozen=True)
class Parameters:
    preload_min_shares: D = D('5')
    preload_min_fraction: D = D('.20')
    seed_seconds: float = .1
    preload_retention: D = D('.80')
    test_seconds: float = .5
    test_min_shares: D = D('5')
    test_min_preload_fraction: D = D('.50')
    buffer_retention: D = D('.20')
    publication_lateness: float = .05
    seed_ttl: float = 3.
    first_sell_diagnostic_seconds: float = .75
    first_sell_limit: None = None
    variant_id: str = VARIANT


def signal_version(parameters):
    return VERSION + '/p-' + parameter_identity(parameters)[1][:12]


@dataclass
class Root:
    id: str
    token: str
    price: D
    generation: int
    b: D
    delta: D
    started: float
    seed_input_ref: tuple
    before_quantity_ref: tuple
    fpre: D
    floor: D
    phase: str = 'PRELOAD'
    seed_confirmed: float | None = None
    seed_key: tuple | None = None
    deadline: float | None = None
    first_received: float | None = None
    first_available: float | None = None
    first_ref: tuple | None = None
    q0: D | None = None
    hits: D = D(0)
    qmin: D | None = None
    last_trade_ref: tuple | None = None
    max_trade_source: float | None = None
    terminal: dict | None = None
    ready: bool = False


class M23:
    strategy = 'M23'

    def __init__(self, emit, parameters=None):
        self.emit, self.p = emit, parameters or Parameters()
        self.version = signal_version(self.p)
        self.parameter_sha256 = parameter_identity(self.p)[1]
        self.now, self.key, self.serial = 0., (0., 0, -1), 0
        self.roots, self.signals, self.last_sell, self.last_end = {}, {}, {}, {}
        self.sell_presence_overflow = {}
        self.rounds, self.components, self.quality = {}, {}, {}
        self.counts = Counter()

    def event(self, kind, **payload):
        self.counts[kind] += 1
        self.emit(kind, {'strategy':self.strategy,'variant_id':VARIANT,
            'version':self.version,'parameter_sha256':self.parameter_sha256,
            'available':self.now,'key':self.key,**payload})

    def clock(self, now, seq, subindex=-1):
        if now < self.now:
            raise ValueError('SOP_AVAILABLE_CLOCK_REGRESSION')
        self.now, self.key = now, (now, seq, subindex)
        for token, root in tuple(self.roots.items()):
            if root.deadline is not None and now >= root.deadline:
                self.end(token, 'SEED_TTL_EXPIRED')
        self.last_sell = {k:v for k,v in self.last_sell.items() if v[0] >= now-1.}
        self.sell_presence_overflow = {k:v for k,v in self.sell_presence_overflow.items() if v >= now-1.}

    def end(self, token, reason, *, unknown=False):
        root = self.roots.pop(token, None)
        if root is None:
            return
        if unknown:
            self.event('ROOT_UNKNOWN',id=root.id,token=token,phase=root.phase,reason=reason)
        self.event('ROOT_ENDED',id=root.id,token=token,phase=root.phase,reason=reason)
        self.last_end[token] = self.now, self.key
        sid = 'M23:signal:' + root.id
        if self.signals.pop(sid,None) is not None:
            self.event('SIGNAL_ENDED',id=sid,token=token,reason=reason)

    def invalidate(self, token, reason):
        self.quality[token] = reason
        self.end(token,reason,unknown=True)
        self.event('QUALITY_UNKNOWN',token=token,reason=reason)

    def new_generation(self, token, generation):
        self.end(token,'BOOK_RECONSTRUCTED',unknown=True)
        # A rebuild cannot erase an earlier original SELL sharing the new
        # seed's received timestamp in this parent frame.
        self.quality.pop(token,None)
        self.event('BOOK_GENERATION_STARTED',token=token,generation=generation)

    def level_missing(self, token, side, price, received, **_):
        self.depth(token,side,price,D(0),received)

    def observe_direct_trade(self, token, side, price, received):
        """Called on original deduplicated rows, before hit/window qualification."""
        if side != 'SELL':
            return
        self.remember_sell_presence(token,price,received,proven=True)
        root = self.roots.get(token)
        if root is not None and root.price == price and received >= root.started:
            if root.phase == 'PRELOAD' or received <= root.seed_confirmed:
                self.end(token,'SELL_DURING_SEED_OR_BEFORE_SEED_PUBLICATION')
            elif root.first_received is not None and received < root.first_received:
                self.end(token,'LATE_SELL_PRECEDES_FROZEN_FIRST_SELL',unknown=True)
            elif root.phase=='VALID' and received < root.first_received+self.p.test_seconds:
                self.end(token,'LATE_SELL_TO_CLOSED_TEST_WINDOW',unknown=True)

    def remember_sell_presence(self, token, price, received, *, proven):
        k = token, price
        if k not in self.last_sell and len(self.last_sell) >= 4096:
            self.sell_presence_overflow[token] = max(received,self.sell_presence_overflow.get(token,float('-inf')))
            self.invalidate(token,'DIRECT_SELL_PRESENCE_CAPACITY')
            return
        old = self.last_sell.get(k)
        if old is None or received > old[0]:
            self.last_sell[k] = received, proven
        elif received == old[0]:
            self.last_sell[k] = received, proven or old[1]

    def observe_unproven_sell(self, token, price, received):
        """Identity/source/parser loss cannot later become a proof of zero SELL.

        Keep the small original-receipt marker across a same-parent snapshot
        rebuild. A fresh seed strictly after this receipt can still form.
        """
        self.remember_sell_presence(token,price,received,proven=False)
        root = self.roots.get(token)
        if root and (price is None or price==root.price) and received>=root.started:
            self.end(token,'DIRECT_SELL_PRESENCE_UNPROVEN',unknown=True)

    def depth(self, token, side, price, quantity, received, *, generation=None,
              input_ref=None, source=None, kind=None, previous_quantity=None,
              previous_quantity_ref=None, pre_event_best_bid=None,
              pre_event_valid=False):
        if side != 'BID':
            return
        root = self.roots.get(token)
        if root is not None and root.price == price:
            floor = root.fpre if root.phase in {'PRELOAD','WAIT_FIRST_SELL'} else root.floor
            if quantity <= 0 or quantity < floor:
                self.end(token,'PRELOAD_FLOOR_BROKEN' if root.phase in {'PRELOAD','WAIT_FIRST_SELL'}
                         else 'BUFFER_FLOOR_BROKEN')
                return
            if received < root.started:
                self.end(token,'LATE_PRE_ROOT_QUANTITY',unknown=True)
                return
            if root.phase=='VALID' and received<root.first_received+self.p.test_seconds:
                self.end(token,'LATE_P_QUANTITY_TO_CLOSED_TEST_WINDOW',unknown=True)
                return
            if root.phase == 'TESTING' and root.first_received <= received < root.first_received+self.p.test_seconds:
                root.qmin = quantity if root.qmin is None else min(root.qmin,quantity)
                root.terminal = {'input_ref':input_ref,'source':source,'received':received,
                                 'quantity':quantity,'kind':kind}
                root.ready = False
            return
        if root is not None or not pre_event_valid or previous_quantity is None:
            return
        if pre_event_best_bid != price or previous_quantity <= 0:
            return
        delta = quantity-previous_quantity
        if delta < max(self.p.preload_min_shares,self.p.preload_min_fraction*previous_quantity):
            return
        if (input_ref is None or previous_quantity_ref is None
                or previous_quantity_ref[2] >= input_ref or generation is None or source is None
                or previous_quantity_ref[0]>received or previous_quantity_ref[1]>source):
            self.event('SEED_UNKNOWN',token=token,reason='PRELOAD_QUANTITY_CHAIN_UNPROVEN')
            return
        prior = [self.last_sell[k] for k in ((token,price),(token,None))
                 if k in self.last_sell and self.last_sell[k][0]>=received]
        if prior or self.sell_presence_overflow.get(token,float('-inf'))>=received:
            proven = any(evidence[1] for evidence in prior)
            self.event('SEED_REJECTED' if proven else 'SEED_UNKNOWN',token=token,
                       reason='SAME_RECEIPT_SELL_BEFORE_PRELOAD' if proven
                       else 'DIRECT_SELL_PRESENCE_UNPROVEN_BEFORE_REBUILD')
            return
        ended = self.last_end.get(token)
        if ended is not None and (received <= ended[0] or self.key <= ended[1]):
            return
        if self.now > received+self.p.seed_seconds+self.p.publication_lateness:
            self.event('SEED_UNKNOWN',token=token,reason='PRELOAD_ALREADY_TOO_LATE')
            return
        self.serial += 1
        root = Root(f'M23:{generation}:{token}:seed:{self.serial}',token,price,generation,
            previous_quantity,delta,received,input_ref,previous_quantity_ref,
            previous_quantity+self.p.preload_retention*delta,
            previous_quantity+self.p.buffer_retention*delta)
        self.roots[token] = root
        self.event('PRELOAD_STARTED',id=root.id,token=token,price=price,b=root.b,delta=delta,
                   fpre=root.fpre,floor=root.floor,received=received,input_ref=input_ref,
                   previous_quantity_ref=previous_quantity_ref,generation=generation)

    def requires_pre_hit_for_trade(self, *, token, side, price, **_):
        root = self.roots.get(token)
        return bool(root and root.phase=='WAIT_FIRST_SELL' and side=='SELL' and price==root.price)

    def trade(self, *, token, side, price, quantity, received, q0, generation,
              input_ref=None, source=None, initial_quantity_ref=None, **_):
        root = self.roots.get(token)
        if root is None or side!='SELL' or price!=root.price:
            return
        if root.phase=='WAIT_FIRST_SELL':
            if not (received>root.seed_confirmed and self.key>root.seed_key
                    and received<root.deadline and self.now<root.deadline):
                self.end(token,'FIRST_SELL_NOT_LAWFULLY_AFTER_SEED')
                return
            if q0<root.fpre:
                self.end(token,'PRELOAD_FLOOR_BROKEN_BEFORE_FIRST_SELL')
                return
            if (q0<=0 or initial_quantity_ref is None or source is None or input_ref is None
                    or initial_quantity_ref[0]>received or initial_quantity_ref[1]>=source
                    or initial_quantity_ref[2]>=input_ref):
                self.end(token,'FIRST_SELL_PRE_HIT_QUANTITY_UNPROVEN',unknown=True)
                return
            root.phase, root.first_received, root.first_available = 'TESTING', received, self.now
            root.first_ref, root.q0 = input_ref, q0
            self.event('FIRST_SELL_HANDOFF',id=root.id,token=token,received=received,
                       first_available=self.now,q0=q0,fpre=root.fpre,floor=root.floor,
                       deadline=root.deadline,input_ref=input_ref)
        if root.phase=='TESTING' and root.first_received <= received < root.first_received+self.p.test_seconds:
            if source is None or input_ref is None:
                self.end(token,'TEST_SELL_SOURCE_UNPROVEN',unknown=True)
                return
            root.hits += quantity
            root.max_trade_source = source if root.max_trade_source is None else max(root.max_trade_source,source)
            root.last_trade_ref = input_ref
            root.ready = False

    def close(self, watermark, covered):
        for token, root in tuple(self.roots.items()):
            if root.phase not in {'PRELOAD','TESTING'}:
                continue
            start = root.started if root.phase=='PRELOAD' else root.first_received
            end = start+(self.p.seed_seconds if root.phase=='PRELOAD' else self.p.test_seconds)
            if watermark <= end:
                continue
            if self.now>end+self.p.publication_lateness or not covered(token,start,end):
                self.end(token,'SEED_OR_TEST_CLOSE_LATE_OR_UNCOVERED',unknown=True)
                continue
            if root.phase=='TESTING':
                terminal = root.terminal
                if (terminal is None or terminal['input_ref'] is None or terminal['source'] is None
                        or terminal['input_ref']<=root.last_trade_ref
                        or terminal['source']<=root.max_trade_source
                        or not start<=terminal['received']<end):
                    self.end(token,'TERMINAL_P_QUANTITY_AFTER_ALL_SELLS_UNPROVEN',unknown=True)
                    continue
                if root.hits<max(self.p.test_min_shares,self.p.test_min_preload_fraction*root.delta):
                    self.end(token,'FIRST_FIXED_WINDOW_INSUFFICIENT_REAL_SELL')
                    continue
            root.ready = True

    def publish(self):
        for token, root in tuple(self.roots.items()):
            if not root.ready:
                continue
            root.ready = False
            if root.phase=='PRELOAD':
                if self.now>root.started+self.p.seed_seconds+self.p.publication_lateness:
                    self.end(token,'SEED_PUBLICATION_TOO_LATE',unknown=True)
                    continue
                root.seed_confirmed, root.seed_key = self.now, self.key
                root.deadline, root.phase = self.now+self.p.seed_ttl, 'WAIT_FIRST_SELL'
                self.event('PRELOAD_CONFIRMED',id=root.id,token=token,confirmed=self.now,
                           deadline=root.deadline,fpre=root.fpre,floor=root.floor)
                continue
            if root.phase!='TESTING' or self.now>=root.deadline:
                continue
            if self.now>root.first_received+self.p.test_seconds+self.p.publication_lateness:
                self.end(token,'TEST_PUBLICATION_TOO_LATE',unknown=True)
                continue
            loss = max(D(0),root.q0-root.qmin)
            sid = 'M23:signal:'+root.id
            signal = {'id':sid,'root_id':root.id,'token':token,'variant_id':VARIANT,
                'version':self.version,'anchor_price':root.price,'confirmed':self.now,
                'deadline':root.deadline,'guard_floor':root.floor,'generation':root.generation,
                'preload':{'b':root.b,'delta':root.delta,'started':root.started,
                    'seed_confirmed':root.seed_confirmed,'seed_key':root.seed_key,
                    'seed_input_ref':root.seed_input_ref,'before_quantity_ref':root.before_quantity_ref,
                    'fpre':root.fpre,'floor':root.floor},
                'test':{'q0':root.q0,'qmin':root.qmin,'hits':root.hits,'loss':loss,
                    'first_received':root.first_received,'first_available':root.first_available,
                    'first_input_ref':root.first_ref,'last_trade_ref':root.last_trade_ref,
                    'max_trade_source':root.max_trade_source,'terminal':root.terminal},
                'diagnostics':{'first_sell_wait_received_ms':1000*(root.first_received-root.seed_confirmed),
                    'first_sell_wait_available_ms':1000*(root.first_available-root.seed_confirmed),
                    'first_sell_within_750ms':(
                        root.first_received<root.seed_confirmed+self.p.first_sell_diagnostic_seconds
                        and root.first_available<root.seed_confirmed+self.p.first_sell_diagnostic_seconds),
                    'loss_to_hit':loss/root.hits,'observed_sell_minus_loss':root.hits-loss,
                    'remaining_lifetime_seconds':root.deadline-self.now},'quality_status':'PROVEN'}
            root.phase = 'VALID'
            self.signals[sid] = signal
            self.event('SIGNAL_CONFIRMED',**signal)

    def release(self):
        for values in (self.roots,self.signals,self.last_sell,self.sell_presence_overflow,
                       self.last_end,self.rounds,self.components,self.quality):
            values.clear()
