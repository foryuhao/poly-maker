"""Explicit continuous EX overlap and causal H/L handoff on frozen v8."""
from dataclasses import dataclass
from decimal import Decimal as D

from maker_signals.local_exhaustion import LocalSellExhaustion
from maker_signals.model import EPS, ZERO
from maker_signals.strategies import Strategies, M910_DENSITY, key
from .m12 import parameter_identity

VARIANT = 'M9_EX_VALIDITY_OVERLAP'
VERSION = 'm9-continuous-ex-overlap-handoff-v1/continuous-proxy-r1'
SOP_FILE = 'V_M9_EX_VALIDITY_OVERLAP.md'
SOP_SHA256 = '0af9c4415088218669a2f6bdf15827f66203d3123dde5703ba174c1f6c149122'
DOCUMENT_PARAMETERS_SHA256 = '8d28d14ca90520e21d08080494ba9f6b2ab173f88557ed5e781f082c82234609'


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = .5
    hold_seconds: float = .01
    stable_recovery: D = D('.20')
    support_ttl_seconds: float = 6.
    formation_seconds: float = 8.
    residual_fraction: D = D('.95')
    seed_ex_ttl_seconds: float = 3.
    ex_total_fraction: D = D('.49')
    diagnostic_ex_bin_fraction: D = D('.30')
    ex_resume_fraction: D = D('.50')
    variant_id: str = VARIANT


def signal_version(parameters):
    return VERSION+'/p-'+parameter_identity(parameters)[1][:12]


class TotalOnlyExhaustion(LocalSellExhaustion):
    """Recompute locally; never reuse EX facts rejected by the old bin cap."""
    def close(self):
        c=self.c
        for ident,watch in tuple(self.watches.items()):
            start,ap=watch['start'],watch['ap'];end=start+.75
            if c.watermark<end-EPS:
                if c.now>end+.05+EPS:
                    del self.watches[ident]
                    c.event('M9_LOCAL_EX_UNAVAILABLE',{'token':self.token,
                        'reason':'EX_INPUT_BACKLOG','ap_id':ident})
                continue
            del self.watches[ident]
            if (c.now>end+.05+EPS or not c.good[self.token] or not c.legal(self.token)
                    or not c.flows[self.token].covered(start,end)):
                c.event('M9_LOCAL_EX_UNAVAILABLE',{'token':self.token,
                    'reason':'EX_WINDOW_UNAVAILABLE','ap_id':ident});continue
            bins=[c.flows[self.token].volume(start+i*.25,start+(i+1)*.25,
                  'SELL',after_key=watch['gate']) for i in range(3)]
            reference=ap.values['volume']
            if sum(bins,ZERO)>reference*self.total_fraction:
                c.event('M9_LOCAL_EX_NOT_CONFIRMED',{'token':self.token,
                    'ap_id':ident,'subwindow_volumes':bins,'reason':'EX_TOTAL_CAP'});continue
            fact=c.fact('EX_SELL_M9_PRIVATE',self.token,c.now+.75,start,end,
                refs=[ap.id],values={'volume':reference,'side':'SELL',
                    'ap_confirmed':ap.confirmed,'strategy_scope':'M9',
                    'window_seconds':.75,'subwindow_volumes':bins,
                    'total_fraction':str(self.total_fraction),
                    'diagnostic_bin_cap_satisfied':max(bins)<=reference*self.bin_fraction,
                    'bin_cap_role':'DIAGNOSTIC_ONLY',
                    'pressure_resume_fraction':str(self.resume_fraction)})
            self.active.append(fact);self.m.on_fact(fact)
        for fact in tuple(self.active):
            if (c.registry.valid(fact,c.now) and
                c.flows[self.token].volume(c.now-.75,c.now,'SELL',frontier=True)
                    >=fact.values['volume']*self.resume_fraction):
                c.registry.end(fact,c.now,'EX_PRESSURE_RESUMED')
        self.active[:]=[f for f in self.active if c.registry.valid(f,c.now)]


class M9Strategies(Strategies):
    # These mean current-regime exits, not falsification of formation evidence.
    REGIME_EXITS=frozenset({'EX_PRESSURE_RESUMED','REVERSE_AP'})

    def __init__(self,context,parameters=None):
        self.selected_parameters=parameters or Parameters();p=self.selected_parameters
        self.ex_proxies={};self.current_proxies={};self.proxy_by_source={}
        super().__init__(context,signal_variant=M910_DENSITY,parent_filters=False,
            enabled_strategies={'M9'},frequency_parameters={'M9':{
                'round_seconds':p.round_seconds,'hold_seconds':p.hold_seconds,
                'stable_recovery':str(p.stable_recovery),
                'support_ttl_seconds':p.support_ttl_seconds,'formation_seconds':p.formation_seconds,
                'residual_fraction':str(p.residual_fraction),'seed_ex_ttl_seconds':p.seed_ex_ttl_seconds,
                'private_seed':True,'seed_round_seconds':p.round_seconds,
                'seed_hold_seconds':p.hold_seconds,'seed_stable_recovery':str(p.stable_recovery),
                'private_ex':True,'ex_total_fraction':str(p.ex_total_fraction),
                'ex_bin_fraction':str(p.diagnostic_ex_bin_fraction),
                'ex_resume_fraction':str(p.ex_resume_fraction)}})
        self.private_ex={t:TotalOnlyExhaustion(self,t,total_fraction=p.ex_total_fraction,
            bin_fraction=p.diagnostic_ex_bin_fraction,resume_fraction=p.ex_resume_fraction)
            for t in context.tokens}

    def end_proxy(self,record,reason,reason_class='HARD_INVALID'):
        if not record['ended']:
            record.update(ended=True,end_key=self.c.cursor,end_reason=reason,
                          end_reason_class=reason_class)
            self.current_proxies.pop(record['proxy'].id,None)
            self.c.registry.end(record['proxy'],self.c.now,reason,reason_class)
            self.c.event('M9_PROXY_ENDED',{'source_ex':record['source'].id,
                'proxy':record['proxy'].id,'token':record['proxy'].token,
                'reason':reason,'reason_class':reason_class,'key':self.c.cursor})

    def maintain_proxy(self,record):
        if record['ended']:
            return
        c=self.c;t=record['proxy'].token
        if not c.good[t] or not c.legal(t):
            self.end_proxy(record,'REQUIRED_INPUT_UNAVAILABLE','UNKNOWN');return
        source_end=c.registry.endings.get(record['source'].id)
        if source_end and source_end['reason_class']!='SOFT_EXPIRED':
            self.end_proxy(record,source_end['reason'],source_end['reason_class']);return
        volume=c.flows[t].volume(c.now-.75,c.now,'SELL',frontier=True)
        if volume>=record['reference']*self.selected_parameters.ex_resume_fraction:
            self.end_proxy(record,'EX_PRESSURE_RESUMED');return
        if c.now>=record['proxy'].deadline:
            self.end_proxy(record,'TTL_EXPIRED','SOFT_EXPIRED');return
        record.update(last_checked_key=c.cursor,last_rolling_sell=volume)

    def on_fact(self,fact):
        c=self.c;t=fact.token
        self.facts[t].setdefault(fact.kind,[]).append(fact)
        self.private_ex[t].on_fact(fact)
        if fact.kind in {'AP_BUY','AP_SELL'}:
            for record in tuple(self.current_proxies.values()):
                if record['proxy'].token!=t or fact.id==record['ap'].id:
                    continue
                if fact.kind=='AP_BUY' or fact.values['volume']>=record['reference']*D('.50'):
                    self.end_proxy(record,'REVERSE_AP' if fact.kind=='AP_BUY' else 'EX_PRESSURE_RESUMED')
        if fact.kind=='EX_SELL_M9_PRIVATE':
            if fact.id in self.proxy_by_source:
                raise ValueError('M9_SOURCE_EX_PROXY_RECREATED')
            ap=c.registry.facts[fact.refs[0]]
            proxy=c.fact('EX_SELL_M9_CONTINUOUS',t,c.now+self.selected_parameters.seed_ex_ttl_seconds,
                fact.start,fact.end,refs=[fact.id,ap.id],values={
                    'source_ex_id':fact.id,'source_original_deadline':fact.deadline,
                    'proxy_created_at':c.now,'created_key':c.cursor,
                    'volume':ap.values['volume'],'maintenance_from_ex_confirmation':True})
            record={'proxy':proxy,'source':fact,'ap':ap,'reference':ap.values['volume'],
                    'ended':False,'created_key':c.cursor,'last_checked_key':c.cursor}
            self.ex_proxies[proxy.id]=record;self.current_proxies[proxy.id]=record
            self.proxy_by_source[fact.id]=proxy.id
            self.maintain_proxy(record)
            support=self.m9_support.get(t)
            if c.registry.valid(support,c.now):self.combine_m9(t,support)
        for record in tuple(self.current_proxies.values()):
            if record['proxy'].token==t:self.maintain_proxy(record)
        campaign=self.active('M9',t)
        if campaign:self.retest_guard(campaign)

    def combine_m9(self,token,rf):
        c=self.c
        for record in tuple(self.current_proxies.values()):
            ex=record['proxy'];ap=record['ap']
            if ex.token!=token:continue
            self.maintain_proxy(record)
            if record['ended'] or not c.registry.valid(rf,c.now) or not c.registry.valid(ex,c.now):
                continue
            if c.now-min(rf.start,ap.start)>20+EPS:continue
            deadline=min(rf.deadline,ex.deadline)
            p=self.start('M9',token,rf.id+'|'+record['source'].id,deadline,rf.anchor)
            if p is None:continue
            p.data.update(seed_rf=rf,seed_ex=ex,seed_ex_source=record['source'],
                proxy_record=record,ap=ap,qualified=False,seed_deadline=deadline,
                formation_deadline=c.now+self.selected_parameters.formation_seconds,
                seed_key=c.cursor,seed_created=c.now,
                paired_after_native_ex_ttl=c.now>=record['source'].deadline)
            p.observe(lambda r,p=p:self.retest_round(p,r),
                qualified=lambda r,p=p:self.retest_qualified(p,r),
                progress=lambda r,p=p:self.retest_progress(p,r),local_window=True)
            self.retest_guard(p)

    def retest_guard(self,p):
        if p.closed:return
        c=self.c;d=p.data;t=p.token
        if not c.good[t] or not c.legal(t):
            p.end('REQUIRED_INPUT_UNAVAILABLE','UNKNOWN');return
        self.maintain_proxy(d['proxy_record'])
        if c.books[t].bids.get(p.anchor,ZERO)<=0:
            p.end('ANCHOR_LOST');return
        volume=c.flows[t].volume(c.now-.75,c.now,'SELL',frontier=True)
        if volume>=d['ap'].values['volume']*self.selected_parameters.residual_fraction:
            p.end('RETEST_RESIDUAL_PRESSURE_RESUMED');return
        source_end=c.registry.endings.get(d['seed_ex_source'].id)
        proxy=d['proxy_record']
        if not d['qualified']:
            if (proxy['ended'] or not c.registry.valid(d['seed_rf'],c.now)
                    or c.now>=d['seed_deadline']):
                reason=proxy.get('end_reason','SEED_ENDED_BEFORE_QUALIFICATION')
                classification=proxy.get('end_reason_class','HARD_INVALID')
                p.end('SEED_'+reason,classification);return
            if source_end and source_end['reason_class']!='SOFT_EXPIRED':
                p.end('SEED_'+source_end['reason'],source_end['reason_class']);return
        else:
            # A historical S still retains its recorded hard counterevidence.
            ending=c.registry.endings.get(d['seed_rf'].id)
            if ending and ending['reason_class']!='SOFT_EXPIRED':
                p.end('SEED_'+ending['reason'],ending['reason_class']);return
            for ending in (source_end,({'reason':proxy['end_reason'],
                         'reason_class':proxy['end_reason_class']} if proxy['ended'] else None)):
                if (ending and ending['reason_class']!='SOFT_EXPIRED'
                        and ending['reason'] not in self.REGIME_EXITS):
                    p.end('SEED_'+ending['reason'],ending['reason_class']);return
        if not p.candidate and c.now>=d['formation_deadline']:
            p.end('RETEST_FORMATION_TIMEOUT','SOFT_EXPIRED')

    def retest_qualified(self,p,r):
        self.retest_progress(p,r)
        if p.closed or p.data['qualified']:return
        d=p.data
        if self.c.now>=min(d['seed_deadline'],d['formation_deadline']):
            p.end('SEED_ENDED_BEFORE_QUALIFICATION');return
        d.update(qualified=True,first_qualified=self.c.now,
            first_qualified_key=self.c.cursor,ex_role='HISTORY',
            handoff_prechecks={'proxy_continuously_current':True,'support_current':True,
                'rolling_sell':self.c.flows[p.token].volume(self.c.now-.75,self.c.now,'SELL',frontier=True),
                'pre_handoff_fraction_strict_max':str(self.selected_parameters.ex_resume_fraction)})
        p.deadline=d['formation_deadline']
        self.c.event('M9_EX_HANDOFF',{'token':p.token,'root_id':p.root,'key':self.c.cursor,
            'prechecks':d['handoff_prechecks'],'source_ex':d['seed_ex_source'].id})

    def tick(self):
        for record in tuple(self.current_proxies.values()):self.maintain_proxy(record)
        super().tick()
        for token in self.c.tokens:
            support=self.m9_support.get(token)
            if self.c.registry.valid(support,self.c.now):self.combine_m9(token,support)

    def payload(self,p):
        value=super().payload(p);d=p.data
        value.update(strategy_version=signal_version(self.selected_parameters),
            selected_variant_id=VARIANT,selected_sop_sha256=SOP_SHA256)
        value['feature_values'].update(seed_key=d['seed_key'],
            ex_proxy_created_at=d['seed_ex'].confirmed,ex_proxy_deadline=d['seed_ex'].deadline,
            paired_after_native_ex_ttl=d['paired_after_native_ex_ttl'],
            first_qualified_key=d['first_qualified_key'],
            handoff_prechecks=d['handoff_prechecks'],ex_role='HISTORY',
            diagnostic_ex_bin_cap_satisfied=d['seed_ex_source'].values['diagnostic_bin_cap_satisfied'])
        return value

    def invalidate(self,token,reason):
        for record in tuple(self.current_proxies.values()):
            if record['proxy'].token==token:self.end_proxy(record,reason,'UNKNOWN')
        return super().invalidate(token,reason)

    def release_runtime(self):
        self.ex_proxies.clear();self.current_proxies.clear();self.proxy_by_source.clear()
        return super().release_runtime()
