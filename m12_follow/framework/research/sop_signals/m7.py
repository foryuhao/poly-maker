"""Explicit pause-release M7 on the pinned v7-compatible input primitives."""
from dataclasses import dataclass
from decimal import Decimal as D

from maker_signals.local_pressure import LocalPressure
from maker_signals.model import EPS, ZERO
from maker_signals.strategies import Strategies, M5_M10_LOCAL_AP, key
from .m12 import parameter_identity

VARIANT = 'M7_PAUSE_RELEASE'
VERSION = 'm7-total-pause-released-reattack-v1/parallel-private-slot-r1'
SOP_FILE = 'V_M7_PAUSE_RELEASE.md'
SOP_SHA256 = '21fffffb96001acac2264bd3b0cd6f042b87456448780ce61bf4ba645d5b2acc'
DOCUMENT_PARAMETERS_SHA256 = '904a7e3f896dfa75ad12cca7d2c4a3686f9c1b79c372bc8189940d297cb286d5'


@dataclass(frozen=True)
class Parameters:
    round_seconds: float = .1
    hold_seconds: float = .01
    stable_recovery: D = D('.40')
    pause_seconds: float = .5
    pause_total_fraction: D = D('.50')
    diagnostic_pause_bin_fraction: D = D('.35')
    ap_window_seconds: float = .75
    support_ttl: float = 8.
    root_ttl: float = 20.
    variant_id: str = VARIANT


def signal_version(parameters):
    return VERSION + '/p-' + parameter_identity(parameters)[1][:12]


class PauseReleasedPressure(LocalPressure):
    def trade(self, trade):
        campaign = self.m.active('M7',self.token)
        ap1 = campaign.data.get('ap1') if campaign else None
        pause = campaign.data.get('ex') if campaign else None
        active = self.active_ap.get('BUY',[])
        allowed = bool(trade.side=='BUY' and campaign and pause and ap1
                       and 'BUY' not in self.attacks and self.c.cursor>key(pause)
                       and any(f.id==ap1.id and self.c.registry.valid(f,self.c.now) for f in active)
                       and not any(f.id!=ap1.id and self.c.registry.valid(f,self.c.now) for f in active))
        if not allowed:
            return super().trade(trade)
        # Remove only AP1 from this one occupancy query. Keep its fact, expiry,
        # original watch and every other current fact unchanged.
        self.active_ap['BUY']=[f for f in active if f.id!=ap1.id]
        try:
            super().trade(trade)
            watch=self.attacks.get('BUY')
            if watch is not None:
                watch['ap1_occupancy_exception_used']=True
                self.c.event('M7_PAUSE_RELEASE_WATCH_STARTED',{
                    'strategy':'M7','token':self.token,'watch_id':watch['watch_id'],
                    'start_key':watch['start_key'],'pause_key':key(pause),
                    'ap1_id':ap1.id,'ap1_original_deadline':ap1.deadline})
        finally:
            self.active_ap['BUY']=active


class M7Strategies(Strategies):
    def __init__(self, context, parameters=None):
        self.selected_parameters=parameters or Parameters()
        p=self.selected_parameters
        super().__init__(context,signal_variant=M5_M10_LOCAL_AP,parent_filters=False,
            frequency_parameters={'M7':{'round_seconds':p.round_seconds,
                'hold_seconds':p.hold_seconds,'stable_recovery':str(p.stable_recovery),
                'pause_seconds':p.pause_seconds,'pause_total_fraction':str(p.pause_total_fraction),
                'pause_bin_fraction':str(p.diagnostic_pause_bin_fraction),
                'restart_after_failed_attack':False,'ap_window_seconds':p.ap_window_seconds}},
            enabled_strategies={'M7'})
        # Exactly750ms is still private in this SOP; the historical generic
        # selector's window<750ms shortcut must not fall back to shared AP.
        self.private_ap={('M7',t):PauseReleasedPressure(self,'M7',t,p.ap_window_seconds)
                         for t in context.tokens}

    def check_short_pause(self, campaign):
        c,p=self.c,self.selected_parameters
        ap=campaign.data['ap1'];start=ap.confirmed;end=start+p.pause_seconds
        if c.now>end+.05+EPS:
            campaign.end('PAUSE_WINDOW_UNAVAILABLE','UNKNOWN');return
        if c.watermark<end-EPS:
            return
        flow=c.flows[campaign.token]
        if not c.good[campaign.token] or not flow.covered(start,end):
            campaign.end('PAUSE_WINDOW_UNAVAILABLE','UNKNOWN');return
        volumes=[flow.volume(a,b,'BUY',after_key=key(ap)) for a,b in
                 ((start,start+p.pause_seconds/2),(start+p.pause_seconds/2,end))]
        reference=ap.values['volume'];total=sum(volumes,ZERO)
        if total>reference*p.pause_total_fraction:
            campaign.end('PAUSE_TOTAL_NOT_CONFIRMED');return
        pause=c.fact('M7_TOTAL_PAUSE_RELEASE',campaign.token,c.now+.75,start,end,
            refs=[ap.id],values={'volume':reference,'subwindow_volumes':volumes,
                'total_buy':total,'diagnostic_bin_cap_satisfied':
                    all(v<=reference*p.diagnostic_pause_bin_fraction for v in volumes),
                'single_bin_cap_role':'DIAGNOSTIC_ONLY','pause_seconds':p.pause_seconds})
        campaign.data['ex']=pause
        campaign.stage='WAIT_JOIN'

    def on_ap_verdict(self, token, watch, fact, *, local=False):
        if not local or watch['side']!='BUY' or fact is None:
            return
        campaign=self.active('M7',token)
        if campaign is None or fact.id==campaign.root:
            return
        self.roots.add(('M7',token,fact.id))
        if campaign.candidate or 'ap2' in campaign.data:
            return
        pause=campaign.data.get('ex')
        if pause is None or tuple(watch['start_key'])<=key(pause):
            campaign.end('REATTACK_STARTED_BEFORE_PAUSE_CONFIRMATION');return
        campaign.data.update(ap2=fact,ap2_start_key=tuple(watch['start_key']),
            ap1_occupancy_exception_used=watch.get('ap1_occupancy_exception_used',False))
        campaign.refs=[fact]
        campaign.stage='WAIT_JOIN'

    def reattack_round(self, campaign, fact):
        if campaign.closed or not fact.values['qualified']:
            return
        if not fact.values['stable']:
            campaign.end('QUALIFIED_REFILL_FAILED');return
        if 'rf' in campaign.data:
            return
        if self.c.now-fact.start>15+EPS:
            campaign.end('RF_FORMATION_TIMEOUT');return
        campaign.anchor=fact.anchor
        campaign.rounds.append(fact)
        support=self.new_rf(campaign,campaign.rounds)
        campaign.data.update(rf=support,rf_key=key(support))
        campaign.stage='WAIT_JOIN' if 'ex' in campaign.data else 'WAIT_EX'

    def tick(self):
        super().tick()
        for campaign in tuple(self.campaigns.values()):
            if campaign.closed or campaign.strategy!='M7':
                continue
            for name in ('rf','ap2'):
                fact=campaign.data.get(name)
                if fact is not None and not self.c.registry.valid(fact,self.c.now):
                    ending=self.c.registry.endings.get(fact.id,{'reason':'ORIGINAL_COMPONENT_EXPIRED',
                                                               'reason_class':'SOFT_EXPIRED'})
                    campaign.end(ending['reason'],ending['reason_class']);break

    def publish(self):
        self.tick()
        # This method is called only after the complete original parent frame.
        for campaign in tuple(self.campaigns.values()):
            if campaign.closed or campaign.candidate:
                continue
            support,attack=campaign.data.get('rf'),campaign.data.get('ap2')
            if support is None or attack is None or 'ex' not in campaign.data:
                continue
            if not all(self.c.registry.valid(f,self.c.now) for f in (support,attack)):
                continue
            deadline=min(support.deadline,attack.deadline,campaign.data['ap1'].confirmed+20)
            if self.c.now>=deadline:
                campaign.end('JOINT_DEADLINE_EXPIRED','SOFT_EXPIRED');continue
            campaign.confirm(deadline,[support,attack],[campaign.root,campaign.data['ex'].id],
                features={'pause_key':key(campaign.data['ex']),
                    'ap2_start_key':campaign.data['ap2_start_key'],
                    'ap1_occupancy_exception_used':campaign.data['ap1_occupancy_exception_used'],
                    'diagnostic_pause_bin_cap_satisfied':campaign.data['ex'].values['diagnostic_bin_cap_satisfied'],
                    'signal_variant':VARIANT})
        super().publish()

    def payload(self, campaign):
        value=super().payload(campaign)
        value.update(strategy_version=signal_version(self.selected_parameters),
                     selected_variant_id=VARIANT,selected_sop_sha256=SOP_SHA256)
        return value
