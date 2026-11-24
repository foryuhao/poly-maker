"""Pinned complementary-view feed for explicitly selected M7/M9 variants.

The mapping/dedup rule remains the frozen v7/v8 rule. Necessary new-round
pre-hit quantities additionally retain explicit original level provenance.
"""
import hashlib
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from maker_signals.feed import ResearchFeed
from maker_signals.model import Trade, decimal, ZERO

PINNED = {
    'feed.py':'dc7eb44cdcdf2781c5d5d40cca7fa011e322072f1680b72d1d76db9da78795f0',
    'model.py':'41a0e8e3c976cd447f58dbe99de2d3407756e60b89e42f163c41e0b3353d7b1e',
    'strategies.py':'61b49f4584b2832ac6ab89360a7958566e2ee02e33bf6a82902645f046d30077',
    'primitives.py':'53a170deeb747b57ad1b29e2016bafe8105c9bf426cd5974d3ee45628e86f175',
    'local_pressure.py':'346fbf4ea4c43655b9c33c191f6e1b250fbd2a650351a27136ae5dec5d3e998b',
    'local_exhaustion.py':'29396b4c3e9676db27d680049615a603ca99a1346288971bed2d8d4a5a0ab804',
    'parameters.json':'3a8347790f905216b5b511f24892b0507a4777873a6dce374ab53d5e9c82ea2c',
}


def verify_sources():
    import maker_signals
    root=Path(maker_signals.__file__).parent
    for name,expected in PINNED.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('PINNED_LEGACY_COMPATIBILITY_SOURCE_CHANGED:'+name)


class ProvenanceFeed(ResearchFeed):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.level_refs={}

    def unavailable(self,token,reason):
        if hasattr(self,'level_refs'):
            self.level_refs={k:v for k,v in self.level_refs.items() if k[0]!=token}
        return super().unavailable(token,reason)

    def _depth(self,token,row,changes,received):
        super()._depth(token,row,changes,received)
        if not self.good[token]:
            return
        stamp=float(decimal(row['timestamp'])/1000)
        if row['event_type']=='book':
            self.level_refs={k:v for k,v in self.level_refs.items() if k[0]!=token}
            updates=[(side,decimal(x['price']),decimal(x['size']),i)
                     for side,name in (('BID','bids'),('ASK','asks')) for i,x in enumerate(row[name])]
        else:
            updates=[('BID' if x['side']=='BUY' else 'ASK',decimal(x['price']),decimal(x['size']),i)
                     for i,x in enumerate(changes)]
        for side,price,quantity,index in updates:
            key=token,side,price
            if quantity:
                self.level_refs[key]=(received,stamp,(self.seq,self.subindex,index))
            else:
                self.level_refs.pop(key,None)

    def initial_quantity_required(self,token,price):
        manager=self.strategies
        if manager is None:
            return False
        campaign=manager.active('M7',token) if manager.enabled('M7') else manager.active('M9',token)
        observers=[]
        if campaign is not None and campaign.observer is not None:
            observers.append(campaign.observer)
        if manager.enabled('M9'):
            observer=manager.m9_seed_observers.get(token)
            if observer is not None:
                observers.append(observer)
        return any(not o.closed and o.round is None and self.cursor>o.gate
                   and (o.anchor is None or o.anchor==price)
                   and self.books[token].bids.get(price,ZERO)>0 for o in observers)

    def _trade(self,row,received):
        # Keep the exact frozen top3 complement check and tuple identity.
        origin=row['asset_id'];book=self.books[origin]
        side,price,quantity=row['side'],decimal(row['price']),decimal(row['size'])
        if side not in {'BUY','SELL'} or not 0<price<1 or price%book.tick or quantity<=0:
            raise ValueError('INVALID_TRADE')
        tx=row.get('transaction_hash')
        if not isinstance(tx,str) or re.fullmatch(r'0x[0-9a-fA-F]{64}',tx) is None:
            for token in self.tokens:self.unavailable(token,'TRADE_IDENTITY_AMBIGUOUS')
            return
        target=next(t for t in self.tokens if t!=origin);other=self.books[target]
        aligned=self.legal(target)
        for left,right,reverse in ((book.bids,other.asks,True),(book.asks,other.bids,False)):
            aligned &= all(right.get(1-p)==left[p] for p in sorted(left,reverse=reverse)[:3])
            aligned &= all(left.get(1-p)==right[p] for p in sorted(right,reverse=not reverse)[:3])
        views=[(origin,side,price,False)]
        if aligned:views.append((target,'SELL' if side=='BUY' else 'BUY',1-price,True))
        else:self.unavailable(target,'COMPLEMENT_VIEW_UNAVAILABLE')
        for token,direction,p,mirrored in views:
            identity=(tx.lower(),str(row['timestamp']),token,direction,
                      str(p.normalize()),str(quantity.normalize()))
            if identity in self.seen_trades:
                self.counts['DUPLICATE_ECONOMIC_VIEW_IGNORED']+=1;continue
            if not self.source(token,row) or not self.good[token] or not self.legal(token):
                continue
            stamp=float(decimal(row['timestamp'])/1000)
            if direction=='SELL' and self.initial_quantity_required(token,p):
                ref=self.level_refs.get((token,'BID',p))
                if ref is None or ref[0]>received or ref[1]>=stamp or ref[2]>=(self.seq,self.subindex,0):
                    self.unavailable(token,'PRE_HIT_FIXED_LEVEL_SOURCE_UNPROVEN');continue
                self.event('INITIAL_QUANTITY_PROVEN',{'token':token,'price':p,
                    'quantity_ref':ref,'trade_input_ref':(self.seq,self.subindex,0),
                    'trade_source':stamp,'trade_origin':origin,'mirrored':mirrored})
            self.seen_trades[identity]=self.now
            trade=Trade(hashlib.sha256(repr(identity).encode()).hexdigest(),token,direction,p,quantity,
                        received,stamp,self.now,self.seq,self.subindex,origin,mirrored)
            self.flows[token].trades.append(trade)
            self.event('NORMALIZED_TRADE',asdict(trade))
            self.pressures[token].trade(trade)
            self.refills[token].observer.trade(trade)
            for observer in tuple(self.observers):
                if observer.token==token:observer.trade(trade)
            self.pressures[token].guard()
            if self.strategies:self.strategies.trade(trade)

    def release_runtime(self):
        self.level_refs.clear()
        return super().release_runtime()


class CoreView:
    def __init__(self,owner):
        self.owner=owner
        self.signals={}
        self.counts=Counter()

    @property
    def rounds(self):
        return {str(i):o.round for i,o in enumerate(self.owner.legacy.observers)
                if not o.closed and o.round is not None}

    @property
    def components(self):
        return self.owner.legacy.registry.facts


class LegacyVariantFeed:
    def __init__(self,market,tokens,ticks,emit,parameters,*,strategy):
        verify_sources()
        self.emit,self.strategy,self.parameters=emit,strategy,parameters
        self.counts=Counter()
        self.core=CoreView(self)
        self.canonical_ids={}
        self.legacy=ProvenanceFeed(market,tokens,ticks,self.event)
        if strategy=='M7':
            from . import m7 as contract
            self.manager=contract.M7Strategies(self.legacy,parameters)
        elif strategy=='M9':
            from . import m9 as contract
            self.manager=contract.M9Strategies(self.legacy,parameters)
        else:
            raise ValueError('UNIMPLEMENTED_SELECTED_LEGACY_VARIANT:'+strategy)
        self.contract=contract
        self.version=contract.signal_version(parameters)

    def __getattr__(self,name):
        return getattr(self.legacy,name)

    def evidence(self,event,payload):
        self.counts[event]+=1;self.core.counts[event]+=1
        self.emit('SOP_EVIDENCE',{'event':event,'market':self.legacy.market,
            'input_seq':self.legacy.seq,'subindex':self.legacy.subindex,
            'generation':self.legacy.generation,'available':self.legacy.now,
            'payload':{'strategy':self.strategy,'variant_id':self.contract.VARIANT,
                'version':self.version,'available':self.legacy.now,'key':self.legacy.cursor,**payload}})

    def event(self,kind,payload):
        if kind=='SIGNAL' and payload.get('event')=='CANDIDATE':
            value=dict(payload['payload']);original=value['id']
            identity=self.contract.VARIANT+':'+original
            value.update(id=identity,candidate_id=identity,version=self.version,
                         variant_id=self.contract.VARIANT,quality_status='PROVEN')
            if original in self.canonical_ids:
                raise ValueError('SELECTED_VARIANT_CANDIDATE_REPUBLISHED')
            self.canonical_ids[original]=identity
            self.core.signals[identity]=value
            self.evidence('SIGNAL_CONFIRMED',value)
            return
        if kind!='RESEARCH_EVIDENCE':
            return
        event,body=payload['event'],payload['payload']
        if event=='NORMALIZED_TRADE':
            self.counts[event]+=1;return
        if event=='FACT_ENDED' and body['id'] in self.canonical_ids:
            identity=self.canonical_ids[body['id']]
            if self.core.signals.pop(identity,None) is not None:
                self.evidence('SIGNAL_ENDED',{'id':identity,'reason':body['reason'],
                    'original_fact_id':body['id'],'source_fact_end_at':body['at']})
        if event=='QUALITY_UNAVAILABLE':
            self.evidence('QUALITY_UNKNOWN',body)
        elif event=='M9_LOCAL_EX_UNAVAILABLE':
            self.evidence('SEED_UNKNOWN',body)
        elif event=='CAMPAIGN_ENDED' and body.get('reason_class')=='UNKNOWN':
            self.evidence('ROOT_UNKNOWN',body)
        self.evidence('LEGACY_'+event,body)

    def connect(self,**kwargs):
        return self.legacy.connect(**kwargs)

    def clock(self,**kwargs):
        return self.legacy.set_clock(**kwargs)

    def apply(self,*args,**kwargs):
        return self.legacy.apply(*args,**kwargs)

    def timer(self,**kwargs):
        return self.legacy.timer(**kwargs)

    def release_runtime(self):
        self.core.signals.clear()
        self.canonical_ids.clear()
        self.legacy.release_runtime()
        self.emit=None
