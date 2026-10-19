"""Static notebook companion charts from reviewed pairing evidence."""
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT=Path(__file__).resolve().parent


def build_plot(result):
    cycles=result['cycles']
    fig,(left,right)=plt.subplots(1,2,figsize=(13.8,7),gridspec_kw={'width_ratios':[1.25,1]})
    fig.subplots_adjust(left=.095,right=.97,top=.8,bottom=.23,wspace=.31)
    fig.suptitle('NORMAL entry to REBALANCE pairing',x=.095,y=.965,ha='left',fontsize=19,fontweight='bold')
    fig.text(.095,.914,'2026-10-17 22:45–23:45 UTC+8 | 13 paired entries across 10 of 12 markets',fontsize=11,color='#475569')
    stages=[('entry_confirmation_wait_s','Entry confirmation','#94a3b8'),
            ('confirmed_to_first_repair_post_s','Wait to first repair POST','#e69f00'),
            ('first_repair_post_to_pair_match_s','Repair attempts to fill','#0072b2')]
    position=list(range(len(cycles)))
    offset=[0.0]*len(cycles)
    for key,label,color in stages:
        vals=[c[key] for c in cycles]
        left.barh(position,vals,left=offset,color=color,label=label,height=.66)
        offset=[a+b for a,b in zip(offset,vals)]
    left.set_yticks(position,[c['market']+' / '+c['window'] for c in cycles],fontsize=9)
    left.invert_yaxis()
    left.set_xlim(0,210)
    left.set_xlabel('Pairing time (seconds)',fontsize=11)
    left.set_title('Where the pairing time was spent',loc='left',fontsize=12,pad=33)
    for y,c in enumerate(cycles):
        left.text(c['matched_to_matched_s']+3,y,f"{c['matched_to_matched_s']:.1f}",va='center',fontsize=9)
    mean=result['all']['metrics']['matched_to_matched_s']['mean']
    left.axvline(mean,color='#475569',lw=1,ls='--',zorder=0)
    left.text(mean+2,-.7,f'Mean {mean:.1f}s',fontsize=9,color='#475569')
    handles,legend_labels=left.get_legend_handles_labels()
    fig.legend(handles,legend_labels,loc='lower left',bbox_to_anchor=(.09,.105),
               fontsize=9,frameon=False,ncol=3)
    labels={'YES':('o','#0072b2'),'NO':('s','#009e73')}
    for side,(marker,color) in labels.items():
        group=[c for c in cycles if c['side']==side]
        right.scatter([c['signal_strength'] for c in group],[c['matched_to_matched_s'] for c in group],
                      marker=marker,s=68,c=color,label='BUY '+side,edgecolors='white',linewidths=.8,zorder=3)
    for c in cycles:
        if c['capital_constrained']:
            right.annotate('23:25 w7\nCapital constraint',xy=(c['signal_strength'],c['matched_to_matched_s']),
                           xytext=(.39,177),fontsize=10,color='#9a6700',
                           arrowprops={'arrowstyle':'->','color':'#9a6700','lw':1})
        elif c['market']=='23:40':
            right.annotate('23:40 w2\n25 repair POSTs',xy=(c['signal_strength'],c['matched_to_matched_s']),
                           xytext=(.72,126),fontsize=10,color='#475569',
                           arrowprops={'arrowstyle':'->','color':'#475569','lw':1})
    right.set_xlim(.28,1.01)
    right.set_ylim(0,205)
    right.set_xlabel('Opening signal strength |OBI|',fontsize=11)
    right.set_ylabel('Pairing time (seconds)',fontsize=11)
    right.set_title('Signal strength has little association',loc='left',fontsize=12,pad=33)
    corr=result['all']['signal_vs_duration']
    right.text(.02,1.02,f"Pearson r = {corr['pearson']:.2f} | Spearman = {corr['spearman']:.2f}",
               transform=right.transAxes,fontsize=10,color='#475569')
    right.legend(loc='upper right',frameon=False,fontsize=9)
    for ax in (left,right):
        ax.spines[['top','right']].set_visible(False)
        ax.spines[['left','bottom']].set_color('#cbd5e1')
        ax.set_axisbelow(True)
        ax.grid(axis='x' if ax==left else 'y',color='#e2e8f0',lw=.7)
    fig.text(.095,.035,'Timing: first local MATCHED of entry to last local MATCHED needed to pair all entry shares.\n'
             'Official match timestamps are unavailable. The sample precedes the new NORMAL capital rule.',
             fontsize=9,color='#475569',linespacing=1.6)
    fig.savefig(OUT/'pairing.png',dpi=160,facecolor='white')
    fig.savefig(OUT/'pairing.svg',facecolor='white')
    return fig


if __name__=='__main__':
    build_plot(json.loads((OUT/'evidence.json').read_text()))
