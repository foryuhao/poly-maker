"""Read-only DP-M0 exploratory proxy test on local M4A captures.

Outputs are historical public trade opportunities, never own-order fill probabilities.
The corresponding definitions, exclusions, and limitations are in
docs/research/DP_M0_MAKER_FILL_RESEARCH_DESIGN.md.
"""

import json,pathlib,collections,bisect,statistics
ROOT=pathlib.Path('research_data/m4a')
H=[1,3,5,10,20]
rows=[]; audit=collections.Counter(); market_stats=[]
for d in sorted(ROOT.iterdir()):
 if not d.is_dir():continue
 m=json.loads((d/'manifest.json').read_text()); market=m['market_slug']; start=int(market.rsplit('-',1)[1])*1_000_000
 if m['close_status']!='COMPLETE' or m['gap_count'] or m['lost_event_count'] or m['persisted_count']<50000:continue
 toks={m['yes_token_id']:'YES',m['no_token_id']:'NO'}
 books={tok:{'BUY':{},'SELL':{}} for tok in toks}; last={tok:None for tok in toks}; sell=collections.defaultdict(list); changes=collections.defaultdict(list); samples=[]
 next_t=start+30_000_000; end_sample=start+260_000_000
 for line in (d/'events.jsonl').open():
  r=json.loads(line);t=r.get('available_at_us');k=r['kind'];p=r.get('payload') or {};tok=p.get('token_id')
  if t is None:continue
  while next_t<=end_sample and next_t<t:
   for token,side in toks.items():
    b=books[token]['BUY'];a=books[token]['SELL']
    if not b or not a or last[token] is None or next_t-last[token]>2_000_000:
     audit['skipped_stale']+=1;continue
    bp=max(b);ap=min(a);q=b[bp];opp=a[ap]
    if not (0<bp<ap<1 and q>0 and opp>0):audit['skipped_crossed']+=1;continue
    samples.append((market,side,token,next_t,bp,q*bp,opp*ap))
   next_t+=10_000_000
  old_bid=max(books[tok]['BUY']) if tok in toks and books[tok]['BUY'] else None
  if tok in toks and k in ('BOOK','REST_BOOK'):
   bids=p.get('bids') or []; asks=p.get('asks') or []
   books[tok]={'BUY':{float(x[0] if isinstance(x,list) else x['price']):float(x[1] if isinstance(x,list) else x['size']) for x in bids},'SELL':{float(x[0] if isinstance(x,list) else x['price']):float(x[1] if isinstance(x,list) else x['size']) for x in asks}};last[tok]=t
  elif tok in toks and k=='DELTA':
   side=p['side'];price=float(p['price']);size=float(p['size']);book=books[tok][side]
   if size>0:book[price]=size
   else:book.pop(price,None)
   last[tok]=t
  if tok in toks and k in ('BOOK','REST_BOOK','DELTA'):
   new_bid=max(books[tok]['BUY']) if books[tok]['BUY'] else None
   if new_bid!=old_bid:changes[tok].append((t,new_bid))
  if tok in toks and k=='TRADE':
   if p.get('side')=='SELL':sell[tok].append((t,float(p['price']),float(p['size'])))
   elif p.get('side')=='BUY':
    other=m['no_token_id'] if tok==m['yes_token_id'] else m['yes_token_id']
    sell[other].append((t,1-float(p['price']),float(p['size'])))
 for token in toks:sell[token].sort()
 for market,side,tok,t,bp,q,opp in samples:
  tr=sell[tok];times=[x[0] for x in tr];idx=bisect.bisect_right(times,t)
  change_t=[x[0] for x in changes[tok]];cidx=bisect.bisect_right(change_t,t);stop=change_t[cidx] if cidx<len(change_t) else start+300_000_000
  labels=[];strict=[]
  for h in H:
   j=bisect.bisect_right(times,t+h*1_000_000)
   labels.append(int(any(price<=bp+1e-8 for _,price,_ in tr[idx:j])))
   j2=bisect.bisect_right(times,min(t+h*1_000_000,stop))
   strict.append(int(any(price<=bp+1e-8 for _,price,_ in tr[idx:j2])))
  rows.append((market,side,t,bp,q,opp,*labels,*strict))
 market_stats.append((market,len(samples),len(sell[m['yes_token_id']]),len(sell[m['no_token_id']])))
print('MARKETS',len(market_stats),'SAMPLES',len(rows),'AUDIT',dict(audit))
print('PER_MARKET',market_stats)
for side in ('YES','NO','ALL'):
 rr=[r for r in rows if side=='ALL' or r[1]==side]
 print('OVERALL',side,len(rr),[(h,round(sum(r[6+i] for r in rr)/len(rr),4)) for i,h in enumerate(H)])
# market-level split chronological; use development quantile boundaries only
markets=sorted({r[0] for r in rows});cut=int(len(markets)*.67);dev=markets[:cut];val=markets[cut:]
def quantile(v,p):
 v=sorted(v);return v[int((len(v)-1)*p)]
qmed=quantile([r[4] for r in rows if r[0] in dev],.5);omed=quantile([r[5] for r in rows if r[0] in dev],.5)
print('SPLIT',len(dev),len(val),'Q_MED',qmed,'OPP_MED',omed)
for split,ms in [('DEV',dev),('VAL',val)]:
 for side in ('YES','NO','ALL'):
  rr=[r for r in rows if r[0] in ms and (side=='ALL' or r[1]==side)]
  print('GROUP',split,side,len(rr))
  for qname,condition in [('favorable',lambda r:r[4]<=qmed and r[5]>omed),('unfavorable',lambda r:r[4]>qmed and r[5]<=omed),('low_near',lambda r:r[4]<=qmed),('high_near',lambda r:r[4]>qmed),('low_opp',lambda r:r[5]<=omed),('high_opp',lambda r:r[5]>omed)]:
   a=[r for r in rr if condition(r)]
   print(qname,len(a),[(h,round(sum(r[6+i] for r in a)/len(a),3) if a else None) for i,h in enumerate(H)])
# paper-like 20 equal-width bins from min to 99th percentile dev; report occupancy
for col,label in [(4,'near'),(5,'opp')]:
 v=[r[col] for r in rows if r[0] in dev];lo=min(v);hi=quantile(v,.99);width=(hi-lo)/20
 counts=collections.Counter(min(19,int((r[col]-lo)/width)) if width else 0 for r in rows if r[0] in val)
 print('BINS',label,'min',lo,'p99',hi,'validation_nonempty',len(counts),'counts',sorted(counts.items()))
print('MARKET_CONSISTENCY_10S')
for market in markets:
 rr=[r for r in rows if r[0]==market]
 f=[r[9] for r in rr if r[4]<=qmed and r[5]>omed]
 u=[r[9] for r in rr if r[4]>qmed and r[5]<=omed]
 print(market.rsplit('-',1)[1],len(f),len(u),round(sum(f)/len(f),3) if f else None,round(sum(u)/len(u),3) if u else None)
# exact paper-style joint bin occupancy in validation
limits={col:(min(r[col] for r in rows if r[0] in dev),quantile([r[col] for r in rows if r[0] in dev],.99)) for col in (4,5)}
def binidx(value,col):
 lo,hi=limits[col];return min(19,max(0,int((value-lo)/((hi-lo)/20))))
for split,ms in [('DEV',dev),('VAL',val)]:
 cells=collections.Counter((binidx(r[4],4),binidx(r[5],5)) for r in rows if r[0] in ms)
 print('JOINT_CELLS',split,len(cells),'median_occupied_count',statistics.median(cells.values()),'cells_ge_5',sum(n>=5 for n in cells.values()))

for split,ms in [('DEV',dev),('VAL',val)]:
 for side in ('YES','NO','ALL'):
  rr=[r for r in rows if r[0] in ms and (side=='ALL' or r[1]==side)]
  for qname,condition in [('favorable',lambda r:r[4]<=qmed and r[5]>omed),('unfavorable',lambda r:r[4]>qmed and r[5]<=omed)]:
   a=[r for r in rr if condition(r)]
   print('STRICT',split,side,qname,len(a),[(h,round(sum(r[11+i] for r in a)/len(a),3) if a else None) for i,h in enumerate(H)])
